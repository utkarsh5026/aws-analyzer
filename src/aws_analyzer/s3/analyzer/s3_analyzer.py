"""
S3Analyzer, made of its mixins: the session, the clients per region and the account.
"""

from __future__ import annotations

from typing import Any

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.analyzer import _BaseAnalyzer

from ..helpers import parse_s3_uri
from ..prices import S3_PRICES
from ..analysis.downloads import DOWNLOADS
from .buckets import _BucketReads
from .listing import _ListingReads
from .versions import _VersionReads
from .files import _FileReads
from .preview import _PreviewReads
from .downloads import _DownloadReads


class S3Analyzer(_BucketReads, _ListingReads, _VersionReads, _FileReads, _PreviewReads, _DownloadReads, _BaseAnalyzer):
    """
    Pure-logic S3 analysis: every method returns data, nothing is printed.

    Anywhere a `uri` is taken you can pass 's3://bucket/prefix' or 'bucket/prefix'.
    Scans accept `limit` (stop after N keys) and `progress` (called with the running count).
    `prices` overrides S3_PRICES (USD per GB-month by storage class) for cost estimates.
    `downloads` is the folder download(), download_folder() and download_zip() save into when they're given no
    path (DOWNLOADS, 's3-downloads', relative to the notebook's folder; '~/...' and absolute paths work too).
    """

    _LIST_PRICES = S3_PRICES

    def __init__(
        self,
        session: Any = None,
        *,
        region: str | None = None,
        profile: str | None = None,
        client: Any = None,
        prices: dict[str, float] | None = None,
        downloads: str | None = None,
    ):
        super().__init__(session, region=region, profile=profile, prices=prices)
        # STS and S3 Control may be unreachable from a VPC-only notebook: fail fast instead of hanging.
        self._quick_config = Config(
            connect_timeout=5, read_timeout=15, retries={"max_attempts": 2}
        )
        self.client = client or self.session.client("s3", config=self._config)
        self.downloads = downloads or DOWNLOADS  # where downloads go without a path (see downloads_folder())
        self._regional_clients: dict[str, Any] = {}
        self._cloudwatch_clients: dict[str, Any] = {}
        self._regions: dict[str, str] = {}
        self._account_id: str | None = None
        self._account_id_checked = False

    def bucket_region(self, bucket: str) -> str:
        """
        Region of a bucket (cached). Falls back to HeadBucket if GetBucketLocation is denied.
        """
        bucket, _ = parse_s3_uri(bucket)
        if bucket in self._regions:
            return self._regions[bucket]
        try:
            location = (
                self.client.get_bucket_location(Bucket=bucket).get("LocationConstraint")
                or "us-east-1"
            )
            region = "eu-west-1" if location == "EU" else location
        except ClientError as exc:
            region = self._region_header(exc.response)
            if not region:
                try:
                    region = self._region_header(self.client.head_bucket(Bucket=bucket))
                except ClientError as head_exc:
                    region = self._region_header(head_exc.response)
            if not region:
                raise exc
        self._regions[bucket] = region
        return region

    @staticmethod
    def _region_header(response: dict) -> str | None:
        return (
            response.get("ResponseMetadata", {})
            .get("HTTPHeaders", {})
            .get("x-amz-bucket-region")
        )

    def _safe_region(self, bucket: str) -> str | None:
        try:
            return self.bucket_region(bucket)
        except (ClientError, BotoCoreError):
            return None

    def _client_for(self, bucket: str) -> Any:
        """
        S3 client in the bucket's own region (bucket-config APIs and presigned URLs need it).
        """
        return self._s3_in(self._safe_region(bucket))

    def _s3_in(self, region: str | None) -> Any:
        if not region or region == self.client.meta.region_name:
            return self.client
        if region not in self._regional_clients:
            self._regional_clients[region] = self.session.client(
                "s3", region_name=region, config=self._config
            )
        return self._regional_clients[region]

    def cloudwatch_in(self, region: str) -> Any:
        if region not in self._cloudwatch_clients:
            self._cloudwatch_clients[region] = self.session.client(
                "cloudwatch", region_name=region
            )
        return self._cloudwatch_clients[region]

    def account_id(self) -> str | None:
        """
        Your AWS account id (cached), or None if STS can't be reached.
        """
        if not self._account_id_checked:
            self._account_id_checked = True
            try:
                self._account_id = self.session.client(
                    "sts", config=self._quick_config
                ).get_caller_identity()["Account"]
            except (ClientError, BotoCoreError):
                pass
        return self._account_id
