"""
S3Analyzer's reads about buckets.
"""

from __future__ import annotations

import fnmatch
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Callable

from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.errors import _error_code
from ..._kit.fmt import _utcnow

from ..helpers import parse_s3_uri, s3_uri
from ..models import BucketConfig, BucketInfo, BucketMetrics, BucketReport
from ..analysis.notifications import EventNotification, parse_notifications


class _BucketReads:
    """
    S3Analyzer's reads about buckets: the list of them, each one's settings, policy and event notifications,
    its size from CloudWatch, and the account's Block Public Access.
    """

    # ------------------------------------------------------------------ buckets

    def list_buckets(self, *, with_region: bool = True) -> list[BucketInfo]:
        """
        All buckets you can see. Regions are looked up in parallel when S3 doesn't return them.
        """
        if self.client.can_paginate("list_buckets"):
            raw = [
                b
                for page in self.client.get_paginator("list_buckets").paginate()
                for b in page.get("Buckets", [])
            ]
        else:
            raw = self.client.list_buckets().get("Buckets", [])
        buckets = [
            BucketInfo(b["Name"], b.get("CreationDate"), b.get("BucketRegion"))
            for b in raw
        ]
        self._regions.update({b.name: b.region for b in buckets if b.region})
        missing = [b for b in buckets if not b.region]
        if with_region and missing:
            with ThreadPoolExecutor(max_workers=16) as pool:
                for bucket, region in zip(
                    missing, pool.map(self._safe_region, [b.name for b in missing])
                ):
                    bucket.region = region
        return buckets

    def account_public_access_block(self) -> dict[str, bool]:
        """
        Account-level Block Public Access settings ({} if never set). Needs s3:GetAccountPublicAccessBlock.
        """
        account = self.account_id()
        if account is None:
            raise ValueError("Couldn't look up the account id (sts:GetCallerIdentity)")
        control = self.session.client(
            "s3control",
            region_name=self.client.meta.region_name or "us-east-1",
            config=self._quick_config,
        )
        try:
            return control.get_public_access_block(AccountId=account)[
                "PublicAccessBlockConfiguration"
            ]
        except ClientError as exc:
            if _error_code(exc) == "NoSuchPublicAccessBlockConfiguration":
                return {}
            raise

    def bucket_policy(self, bucket: str) -> dict | None:
        """
        The bucket policy document, or None if the bucket has none. See explain_policy.
        """
        bucket, _ = parse_s3_uri(bucket)
        try:
            return json.loads(
                self._client_for(bucket).get_bucket_policy(Bucket=bucket)["Policy"]
            )
        except ClientError as exc:
            if _error_code(exc) == "NoSuchBucketPolicy":
                return None
            raise

    def notifications(self, bucket: str) -> list[EventNotification]:
        """
        The bucket's event notifications: the Lambda functions, SQS queues and SNS topics it sends which events
        to, for which keys, and whether EventBridge gets every event (see parse_notifications). Needs
        s3:GetBucketNotification.
        """
        bucket, _ = parse_s3_uri(bucket)
        return parse_notifications(
            self._client_for(bucket).get_bucket_notification_configuration(Bucket=bucket)
        )

    def versioning_status(self, bucket: str) -> str:
        """
        'Enabled', 'Suspended' or 'Disabled'.
        """
        bucket, _ = parse_s3_uri(bucket)
        return (
            self._client_for(bucket)
            .get_bucket_versioning(Bucket=bucket)
            .get("Status", "Disabled")
        )

    def bucket_config(self, bucket: str) -> BucketConfig:
        """
        Versioning, encryption, public access, ownership, lock, lifecycle, replication, logging, tags.
        """
        bucket, _ = parse_s3_uri(bucket)
        cfg = BucketConfig(name=bucket, region=self._safe_region(bucket))
        client = self._client_for(bucket)

        def get(
            section: str, method: str, not_found: tuple[str, ...] = ()
        ) -> dict | None:
            try:
                return getattr(client, method)(Bucket=bucket)
            except ClientError as exc:
                if _error_code(exc) not in not_found:
                    cfg.errors[section] = _error_code(exc)
            except BotoCoreError as exc:
                cfg.errors[section] = type(exc).__name__
            return None

        if (resp := get("versioning", "get_bucket_versioning")) is not None:
            cfg.versioning = resp.get("Status", "Disabled")
            cfg.mfa_delete = resp.get("MFADelete", "Disabled")
        if resp := get(
            "encryption",
            "get_bucket_encryption",
            ("ServerSideEncryptionConfigurationNotFoundError",),
        ):
            rules = resp.get("ServerSideEncryptionConfiguration", {}).get("Rules", [])
            if rules:
                default = rules[0].get("ApplyServerSideEncryptionByDefault", {})
                cfg.encryption, cfg.kms_key = (
                    default.get("SSEAlgorithm"),
                    default.get("KMSMasterKeyID"),
                )
                cfg.bucket_key_enabled = rules[0].get("BucketKeyEnabled")
        if resp := get(
            "public_access_block",
            "get_public_access_block",
            ("NoSuchPublicAccessBlockConfiguration",),
        ):
            cfg.public_access_block = resp.get("PublicAccessBlockConfiguration")
        resp = get("policy", "get_bucket_policy_status", ("NoSuchBucketPolicy",))
        if "policy" not in cfg.errors:
            cfg.has_policy = resp is not None
            cfg.policy_is_public = (
                resp["PolicyStatus"].get("IsPublic", False) if resp else None
            )
        if cfg.has_policy is not False:
            resp = get("policy_document", "get_bucket_policy", ("NoSuchBucketPolicy",))
            if resp:
                cfg.policy, cfg.has_policy = json.loads(resp["Policy"]), True
            elif "policy_document" not in cfg.errors:
                cfg.has_policy, cfg.policy_is_public = False, None
        if resp := get(
            "ownership",
            "get_bucket_ownership_controls",
            ("OwnershipControlsNotFoundError",),
        ):
            cfg.object_ownership = resp["OwnershipControls"]["Rules"][0][
                "ObjectOwnership"
            ]
        resp = get(
            "object_lock",
            "get_object_lock_configuration",
            ("ObjectLockConfigurationNotFoundError",),
        )
        if "object_lock" not in cfg.errors:
            cfg.object_lock = bool(
                resp
                and resp["ObjectLockConfiguration"].get("ObjectLockEnabled")
                == "Enabled"
            )
        if resp := get(
            "lifecycle",
            "get_bucket_lifecycle_configuration",
            ("NoSuchLifecycleConfiguration",),
        ):
            cfg.lifecycle_rules = resp.get("Rules", [])
        if resp := get(
            "replication",
            "get_bucket_replication",
            ("ReplicationConfigurationNotFoundError",),
        ):
            cfg.replication_rules = resp["ReplicationConfiguration"].get("Rules", [])
        if (resp := get("logging", "get_bucket_logging")) and "LoggingEnabled" in resp:
            target = resp["LoggingEnabled"]
            cfg.logging_target = s3_uri(
                target["TargetBucket"], target.get("TargetPrefix", "")
            )
        if resp := get("tags", "get_bucket_tagging", ("NoSuchTagSet",)):
            cfg.tags = {tag["Key"]: tag["Value"] for tag in resp.get("TagSet", [])}
        if resp := get("inventory", "list_bucket_inventory_configurations"):
            cfg.inventory_configs = [
                c["Id"] for c in resp.get("InventoryConfigurationList", [])
            ]
        return cfg

    def bucket_metrics(self, bucket: str, *, days: int = 3) -> BucketMetrics:
        """
        Object count and size per storage type from CloudWatch (published daily, free, instant -
        the fastest way to size a bucket with millions of objects). Needs cloudwatch:ListMetrics/GetMetricData.
        """
        bucket, _ = parse_s3_uri(bucket)
        cloudwatch = self.cloudwatch_in(self.bucket_region(bucket))
        result = BucketMetrics(bucket=bucket)
        metrics = [
            metric
            for page in cloudwatch.get_paginator("list_metrics").paginate(
                Namespace="AWS/S3", Dimensions=[{"Name": "BucketName", "Value": bucket}]
            )
            for metric in page.get("Metrics", [])
            if metric["MetricName"] in ("BucketSizeBytes", "NumberOfObjects")
        ]
        if not metrics:
            return result
        now = _utcnow()
        queries = [
            {
                "Id": f"m{i}",
                "MetricStat": {"Metric": m, "Period": 86400, "Stat": "Average"},
            }
            for i, m in enumerate(metrics)
        ]
        for start in range(0, len(queries), 500):
            response = cloudwatch.get_metric_data(
                MetricDataQueries=queries[start : start + 500],
                StartTime=now - timedelta(days=days),
                EndTime=now,
                ScanBy="TimestampDescending",
            )
            for series in response["MetricDataResults"]:
                if not series["Values"]:
                    continue
                metric = metrics[int(series["Id"][1:])]
                storage_type = next(
                    (
                        d["Value"]
                        for d in metric["Dimensions"]
                        if d["Name"] == "StorageType"
                    ),
                    "?",
                )
                if metric["MetricName"] == "NumberOfObjects":
                    result.object_count = int(series["Values"][0])
                else:
                    result.size_by_storage_type[storage_type] = int(series["Values"][0])
                result.as_of = max(
                    filter(None, [result.as_of, series["Timestamps"][0]])
                )
        result.size_by_storage_type = dict(
            sorted(result.size_by_storage_type.items(), key=lambda kv: -kv[1])
        )
        return result

    def bucket_reports(
        self,
        *,
        match: str | None = None,
        metrics: bool = True,
        max_workers: int = 8,
        progress: Callable[[int], None] | None = None,
    ) -> list[BucketReport]:
        """
        Settings (and CloudWatch size unless metrics=False) for every bucket, checked in parallel.
        match: only buckets whose name matches this glob, e.g. 'sagemaker-*'.
        """
        buckets = [
            b
            for b in self.list_buckets()
            if match is None or fnmatch.fnmatchcase(b.name, match)
        ]
        for region in {b.region for b in buckets if b.region}:
            self._s3_in(
                region
            )  # boto3 sessions aren't thread-safe: make every client before the threads start
            if metrics:
                self.cloudwatch_in(region)

        def check(bucket: BucketInfo) -> BucketReport:
            report = BucketReport(bucket, self.bucket_config(bucket.name))
            if metrics:
                try:
                    report.metrics = self.bucket_metrics(bucket.name)
                except (ClientError, BotoCoreError) as exc:
                    report.metrics_error = (
                        _error_code(exc)
                        if isinstance(exc, ClientError)
                        else type(exc).__name__
                    )
            return report

        reports: list[BucketReport] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for report in pool.map(check, buckets):
                reports.append(report)
                if progress:
                    progress(len(reports))
        return reports
