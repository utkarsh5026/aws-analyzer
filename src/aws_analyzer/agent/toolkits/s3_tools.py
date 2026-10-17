"""
The S3 toolkit: tools over S3Analyzer for what a bucket holds and how it's set up, and the one that joins S3 and
Lambda. Which buckets there are (find_buckets), a bucket's settings, size, cost and event notifications
(bucket_details), a folder one level down (list_folder), the files under a location that match a filter
(find_files), what a location holds by type, storage class and folder (folder_summary), one file's headers
(file_info), and, when the agent has the Lambda toolkit too, s3_triggers: which bucket events invoke which functions,
and whether the files written in a window reached them. No tool reads what's inside a file.

The summarize_* functions, count_arrivals() and trigger_findings() are pure: they run on any BucketConfig,
BucketMetrics, Listing, PrefixSummary, list of ObjectInfo or TriggerLink.
"""

from __future__ import annotations

import difflib
import fnmatch
import heapq
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Iterable

from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.errors import _error_code
from ..._kit.fmt import _plural, _utcnow, human_money, human_size, parse_time
from ..._kit.text import _clip
from ..budget import Budget
from ..results import _iso, _window, _with_permissions, hide_secrets
from .base import Toolkit

if TYPE_CHECKING:  # for the type hints only: the S3 analyzer loads when the first tool runs
    from ...lambda_functions import Function, FunctionMetrics
    from ...s3 import (
        BucketConfig, BucketInfo, BucketMetrics, EventNotification, Listing, ObjectInfo, PrefixSummary, S3Analyzer,
    )

# What each tool reads (IAM permissions), for tools() and README. s3_triggers reads Lambda through the Lambda toolkit.
S3_TOOL_READS: dict[str, tuple[str, ...]] = {
    "find_buckets": ("s3:ListAllMyBuckets", "s3:GetBucketLocation", "cloudwatch:ListMetrics",
                     "cloudwatch:GetMetricData"),
    "bucket_details": ("s3:GetBucketLocation", "s3:GetBucketVersioning", "s3:GetEncryptionConfiguration",
                       "s3:GetBucketPublicAccessBlock", "s3:GetBucketPolicyStatus", "s3:GetBucketPolicy",
                       "s3:GetBucketOwnershipControls", "s3:GetBucketObjectLockConfiguration",
                       "s3:GetLifecycleConfiguration", "s3:GetReplicationConfiguration", "s3:GetBucketLogging",
                       "s3:GetBucketTagging", "s3:GetInventoryConfiguration", "s3:GetBucketNotification",
                       "s3:GetAccountPublicAccessBlock", "cloudwatch:ListMetrics", "cloudwatch:GetMetricData"),
    "list_folder": ("s3:ListBucket",),
    "find_files": ("s3:ListBucket",),
    "folder_summary": ("s3:ListBucket",),
    "file_info": ("s3:GetObject (HeadObject: its headers, never its content)", "s3:GetObjectTagging"),
    "s3_triggers": ("s3:GetBucketNotification", "s3:ListBucket", "s3:ListAllMyBuckets", "s3:GetBucketLocation",
                    "lambda:GetFunction", "lambda:GetPolicy", "cloudwatch:GetMetricData"),
}
# USD per 1,000 requests (S3's us-east-1 list prices, 2026-10): LIST requests, and GET, HEAD and the rest.
S3_REQUEST_PRICES = {"list": 0.005, "get": 0.0004}
_METRIC_PRICE = 0.01  # USD per 1,000 metrics read with CloudWatch GetMetricData
_SIZED_BUCKETS = 200  # find_buckets(sizes=True) reads CloudWatch for at most this many buckets
_SEARCHED_BUCKETS = 50  # s3_triggers looks through at most this many buckets for notifications that call a function
_CONFIG_PERMISSIONS = {  # BucketConfig.errors section (and this toolkit's own parts) -> the permission it needs
    "versioning": "s3:GetBucketVersioning", "encryption": "s3:GetEncryptionConfiguration",
    "public_access_block": "s3:GetBucketPublicAccessBlock", "policy": "s3:GetBucketPolicyStatus",
    "policy_document": "s3:GetBucketPolicy", "ownership": "s3:GetBucketOwnershipControls",
    "object_lock": "s3:GetBucketObjectLockConfiguration", "lifecycle": "s3:GetLifecycleConfiguration",
    "replication": "s3:GetReplicationConfiguration", "logging": "s3:GetBucketLogging", "tags": "s3:GetBucketTagging",
    "inventory": "s3:GetInventoryConfiguration", "notifications": "s3:GetBucketNotification",
    "metrics": "cloudwatch:GetMetricData", "list": "s3:ListBucket", "buckets": "s3:ListAllMyBuckets",
    "function": "lambda:GetFunction", "policy_of_function": "lambda:GetPolicy",
}


def _s3_module() -> Any:
    """
    aws_analyzer.s3, which the tools are built on. Loaded when a tool first needs it, so importing the agent loads
    no analyzer.
    """
    from ... import s3

    return s3


def _code(exc: BaseException) -> str:
    return _error_code(exc) if isinstance(exc, ClientError) else type(exc).__name__


def _between(since: str, until: str, now: datetime | None = None) -> tuple[datetime | None, datetime]:
    """
    (start, end) of a tool's since= and until=: ISO UTC times ('2026-10-15T18:00Z') or relative ones ('24h'). The
    start is None when since is empty, and the end is now when until is.
    """
    now = now or _utcnow()
    since, until = str(since or "").strip(), str(until or "").strip()
    try:
        start = parse_time(since, now=now) if since else None
        end = parse_time(until, now=now) if until else now
    except ValueError:
        raise ValueError(f"since= and until= take ISO UTC times ('2026-10-15T18:00Z') or relative ones ('24h', "
                         f"'7d'); got since={since!r}, until={until!r}") from None
    if start is not None and end is not None and start >= end:
        raise ValueError(f"since= ({since!r}) must be before until= ({until or 'now'!r})")
    return start, end or now


# =============================================================================
# What the tools return: S3Analyzer's results as compact JSON (pure)
# =============================================================================


def _shown(values: dict[str, Any]) -> dict[str, str]:
    """
    Tags or metadata as the model reads them: each value clipped, and hidden when it looks like a secret, by
    itself or by its name ({'api_key': 'sk-...'} -> {'api_key': '[hidden]'}).
    """
    out = {}
    for key, value in values.items():
        text = hide_secrets(f"{key}={_clip(str(value), 200)}")
        out[key] = text[len(key) + 1:] if text.startswith(f"{key}=") else hide_secrets(_clip(str(value), 200))
    return out


def file_row(obj: ObjectInfo) -> dict[str, Any]:
    """
    One file as the agent sees it: its key, size, when it was last written, its storage class, and whether it
    was uploaded in parts (which S3 events it sent depends on that).
    """
    row: dict[str, Any] = {"key": obj.key, "size": human_size(obj.size), "modified": obj.last_modified,
                           "storage_class": obj.storage_class}
    if obj.is_multipart:
        row["multipart"] = True
    return row


def summarize_buckets(buckets: list[BucketInfo], metrics: dict[str, BucketMetrics] | None = None,
                      errors: dict[str, str] | None = None, *, limit: int = 100) -> dict[str, Any]:
    """
    find_buckets()'s answer: every bucket (by name, or biggest first when `metrics` has their sizes), with its
    region and when it was made, and its object count and size from CloudWatch when they were read.
    """
    rows = []
    for b in buckets:
        row: dict[str, Any] = {"name": b.name, "region": b.region, "created": b.created}
        m = (metrics or {}).get(b.name)
        if metrics is not None and m is not None:
            row["objects"] = m.object_count
            row["size"] = human_size(m.total_size) if m.size_by_storage_type else None
            row["_bytes"] = m.total_size
        rows.append(row)
    rows.sort(key=lambda r: (-(r.get("_bytes") or 0), r["name"]) if metrics is not None else r["name"])
    for row in rows:
        row.pop("_bytes", None)
    out: dict[str, Any] = {"count": len(buckets), "regions": sorted({b.region for b in buckets if b.region}),
                           "buckets": rows[:limit]}
    if len(rows) > limit:
        out["more"] = f"{len(rows) - limit:,} more not listed: narrow with match='prefix-*'"
    if metrics is not None:
        out["sizes_note"] = ("From CloudWatch's daily storage metrics (up to a day old). A bucket without numbers is "
                             "new, empty, or has no metrics yet.")
    if errors:
        out["errors"] = _with_permissions(errors, _CONFIG_PERMISSIONS)
    return out


def summarize_bucket(cfg: BucketConfig, metrics: BucketMetrics | None, notifications: list[EventNotification] | None,
                     *, account_block: dict[str, bool] | None = None, prices: dict[str, float] | None = None,
                     errors: dict[str, str] | None = None) -> dict[str, Any]:
    """
    bucket_details()'s answer: a bucket's settings in plain words (public access, encryption, versioning,
    lifecycle rules, replication, logging, tags), its size and estimated monthly storage cost from CloudWatch, the
    events it sends where (Lambda functions, queues, topics, EventBridge), and the findings about it.
    """
    s3 = _s3_module()
    errors = {**cfg.errors, **(errors or {})}
    out: dict[str, Any] = {
        "bucket": cfg.name, "region": cfg.region,
        "public_access": s3._exposure(cfg, account_block),
        "block_public_access": s3._block_label(cfg.public_access_block),
        "encryption": s3._encryption_label(cfg), "kms_key": cfg.kms_key,
        "versioning": cfg.versioning, "object_ownership": cfg.object_ownership, "object_lock": cfg.object_lock,
        "lifecycle_rules": [{"id": rule.get("ID"), "status": rule.get("Status"), "applies_to": s3._rule_scope(rule),
                             "does": s3._rule_actions(rule).replace("\n", "; ")} for rule in cfg.lifecycle_rules],
        "replication_rules": len(cfg.replication_rules), "access_logs_to": cfg.logging_target,
        "tags": _shown(cfg.tags),
    }
    if metrics is not None and (metrics.size_by_storage_type or metrics.object_count is not None):
        costs = s3.cloudwatch_cost(metrics.size_by_storage_type, prices)
        known = [c for c in costs.values() if c is not None]
        out["size"] = {"objects": metrics.object_count, "total": human_size(metrics.total_size),
                       "by_storage_type": {k: human_size(v) for k, v in metrics.size_by_storage_type.items()},
                       "as_of": metrics.as_of}
        out["monthly_storage_cost_usd"] = {
            "total": round(sum(known), 2) if known else None,
            "basis": "estimate: CloudWatch's daily size at us-east-1 list prices; storage only (no requests or "
                     "data transfer)"}
    elif metrics is not None:
        out["size"] = {"note": "CloudWatch has no storage numbers for it yet (new or empty buckets have none)."}
    if notifications is not None:
        out["event_notifications"] = [{"sends": n.describe(), "to": n.target, "name": n.name if n.arn else None,
                                       "events": n.events, "prefix": n.prefix, "suffix": n.suffix}
                                      for n in notifications]
    found = s3.bucket_findings(cfg, account_block)
    if cfg.policy:
        found += s3.policy_findings(s3.explain_policy(cfg.policy))
    out["findings"] = [{"level": level, "message": text} for level, text in found
                       if not text.startswith("Couldn't read")]  # what couldn't be read is in errors
    if errors:
        out["errors"] = _with_permissions(errors, _CONFIG_PERMISSIONS)
    return out


def summarize_listing(listing: Listing, *, folders: int = 100, files: int = 50) -> dict[str, Any]:
    """
    list_folder()'s answer: the folders and files directly under a location (one level), the newest files first.
    """
    newest = sorted(listing.objects, key=lambda o: o.last_modified, reverse=True)
    out: dict[str, Any] = {
        "location": listing.uri, "folders_found": len(listing.folders), "files_found": len(listing.objects),
        "folders": listing.folders[:folders],
        "files_newest_first": [file_row(o) for o in newest[:files]],
        "cut_short": listing.truncated,
    }
    if len(listing.folders) > folders:
        out["more_folders"] = len(listing.folders) - folders
    if len(newest) > files:
        out["more_files"] = len(newest) - files
    if listing.truncated:
        out["note"] = "The folder holds more than was listed: find_files or folder_summary read all of it."
    return out


def summarize_found(location: str, found: list[ObjectInfo], *, scanned: int, cut_short: bool,
                    start: datetime | None = None, end: datetime | None = None,
                    filters: dict[str, Any] | None = None, limit: int = 30) -> dict[str, Any]:
    """
    find_files()'s answer: how many files under a location match, their total size, when the oldest and newest
    were written, and the newest of them; `scanned` keys were read, and `cut_short` when the listing stopped early.
    """
    newest = heapq.nlargest(limit, found, key=lambda o: o.last_modified)
    out: dict[str, Any] = {"location": location, "filters": {k: v for k, v in (filters or {}).items() if v}}
    if start is not None:
        out["window"] = _window(start, end or _utcnow())
    out.update({
        "matched": len(found), "total_size": human_size(sum(o.size for o in found)),
        "oldest_written": min((o.last_modified for o in found), default=None),
        "newest_written": max((o.last_modified for o in found), default=None),
        "newest_first": [file_row(o) for o in newest],
        "scanned": scanned, "cut_short": cut_short,
    })
    if len(found) > limit:
        out["more"] = len(found) - limit
    if cut_short:
        out["note"] = (f"Stopped after {scanned:,} keys (the s3_keys budget), in key order: the counts cover those "
                       "only. A narrower location reads all of it.")
    return out


def summarize_prefix(summary: PrefixSummary, prices: dict[str, float] | None = None, *, top: int = 8) -> dict[str, Any]:
    """
    folder_summary()'s answer: what a location holds (files, total size, by file type, storage class and
    folder), the oldest and newest file, the largest ones, the estimated monthly storage cost and the findings.
    """
    s3 = _s3_module()

    def stats(by: dict[str, Any], n: int = top) -> list[dict[str, Any]]:
        ordered = sorted(by.items(), key=lambda kv: -kv[1].size)
        return [{"name": name or "(directly here)", "files": st.count, "size": human_size(st.size)}
                for name, st in ordered[:n]]

    out: dict[str, Any] = {
        "location": summary.uri, "files": summary.object_count, "total_size": human_size(summary.total_size),
        "by_type": stats(summary.by_extension), "by_storage_class": stats(summary.by_storage_class),
        "by_folder": stats(summary.by_folder, 10),
        "oldest": file_row(summary.oldest) if summary.oldest else None,
        "newest": file_row(summary.newest) if summary.newest else None,
        "largest": [file_row(o) for o in summary.largest[:5]],
        "empty_files": summary.empty_count,
        "monthly_storage_cost_usd": round(summary.monthly_cost, 2),
        "cost_basis": "estimate: us-east-1 list prices, storage only",
        "findings": [{"level": level, "message": text} for level, text in s3.summary_findings(summary, prices)],
        "scanned": summary.object_count + summary.folder_markers, "cut_short": summary.truncated,
    }
    if len(summary.by_folder) > 10:
        out["more_folders"] = len(summary.by_folder) - 10
    return out


def summarize_file(info: dict[str, Any]) -> dict[str, Any]:
    """
    file_info()'s answer: one file's headers (size, when it was written, type, storage class, encryption, version,
    restore and lock status, its metadata and tags, with anything that looks like a secret hidden) and what they
    mean for reading it. Never its content.
    """
    s3 = _s3_module()
    out: dict[str, Any] = {k: v for k, v in info.items() if k not in ("metadata", "tags", "size", "etag")}
    out["size"] = human_size(info.get("size"))
    out["metadata"] = _shown(info.get("metadata") or {})
    tags = info.get("tags")
    out["tags"] = _shown(tags) if tags is not None else "not read: needs s3:GetObjectTagging"
    notes = []
    archived = info.get("storage_class") in s3.ARCHIVE_CLASSES or info.get("archive_status")
    if archived and 'ongoing-request="false"' not in str(info.get("restore")):
        tier = info.get("archive_status") or info.get("storage_class")
        notes.append(f"It's archived ({tier}): nothing can read it (a Lambda function included) until it's "
                     "restored. S3View().head() shows the restore call.")
    if info.get("encryption") in ("aws:kms", "aws:kms:dsse"):
        notes.append(f"It's encrypted with KMS key {info.get('kms_key') or '(the bucket default)'}: whatever reads it "
                     "(a Lambda function's role included) also needs kms:Decrypt on that key.")
    if notes:
        out["notes"] = notes
    return out


# ---- S3 and Lambda: the event notifications that invoke functions, checked from both ends


@dataclass
class Arrivals:
    """
    What a listing of a notification's prefix says about the files written in a window (count_arrivals).
    """

    scanned: int = 0  # keys read
    matched: int = 0  # files written in the window that its prefix and suffix cover
    sent: int = 0  # of those, the ones uploaded in a way its events fire on: they invoked the function
    multipart_missed: int = 0  # matched, but uploaded in parts, and it doesn't fire on CompleteMultipartUpload
    whole_missed: int = 0  # matched, but uploaded whole, and it fires only on multipart uploads
    missed_keys: list[str] = field(default_factory=list)  # a few of the files its events missed
    case_missed: list[str] = field(default_factory=list)  # files in the window whose ending matches only ignoring case
    newest: list[ObjectInfo] = field(default_factory=list)  # the newest files that invoked it (up to 5)
    last_match: ObjectInfo | None = None  # the newest file its filter covers, in the window or before
    cut_short: bool = False  # the listing stopped at its limit


@dataclass
class TriggerLink:
    """
    One S3 event notification that invokes a Lambda function, checked from both ends (s3_triggers).
    """

    bucket: str
    notification: EventNotification
    exists: bool | None = None  # the function is there; None when it couldn't be checked
    allowed: bool | None = None  # its resource policy lets this bucket invoke it; None when it couldn't be read
    arrivals: Arrivals | None = None  # None when it doesn't fire on new files, or the listing failed
    metrics: FunctionMetrics | None = None  # CloudWatch's counts for the function over the window
    account: str | None = None  # the function's account (for the add-permission command)

    @property
    def function(self) -> str:
        return self.notification.name

    @property
    def region(self) -> str | None:
        return self.notification.region


def count_arrivals(objects: Iterable[ObjectInfo], notification: EventNotification, start: datetime, end: datetime,
                   *, limit: int | None = None, show: int = 5) -> Arrivals:
    """
    What a listing of the notification's prefix says about the files written from `start` to `end`: how many its
    prefix and suffix cover, and how many of those it fired on, by how each was uploaded (an ETag with a part count
    means multipart, which sends s3:ObjectCreated:CompleteMultipartUpload; anything else sent a Put, Post or Copy
    event), and the files its suffix misses only by case ('.CSV' for '.csv': S3 compares case-sensitively). `limit`
    caps the keys read (cut_short when there were more).
    """
    out = Arrivals()
    suffix, prefix = notification.suffix, notification.prefix
    sent: list[ObjectInfo] = []
    for obj in objects:
        if limit is not None and out.scanned >= limit:
            out.cut_short = True
            break
        out.scanned += 1
        if obj.is_folder_marker or not obj.key.startswith(prefix):
            continue
        written = start <= obj.last_modified < end
        if obj.key.endswith(suffix):
            if out.last_match is None or obj.last_modified > out.last_match.last_modified:
                out.last_match = obj
            if not written:
                continue
            out.matched += 1
            if notification.on_created(obj.is_multipart):
                out.sent += 1
                sent.append(obj)
                continue
            if obj.is_multipart:
                out.multipart_missed += 1
            else:
                out.whole_missed += 1
            if len(out.missed_keys) < 3:
                out.missed_keys.append(obj.key)
        elif written and suffix and obj.key.lower().endswith(suffix.lower()):
            out.case_missed.append(obj.key)
    out.newest = heapq.nlargest(show, sent, key=lambda o: o.last_modified)
    return out


def _where(n: EventNotification) -> str:
    """
    "under 'incoming/' ending in '.csv'", or "anywhere in the bucket".
    """
    parts = [f"under '{n.prefix}'" if n.prefix else "", f"ending in '{n.suffix}'" if n.suffix else ""]
    return " ".join(filter(None, parts)) or "anywhere in the bucket"


def _events(n: EventNotification) -> str:
    return ", ".join(n.events) or "no events"


def trigger_findings(link: TriggerLink) -> list[tuple[str, str]]:
    """
    What's wrong with one S3 -> Lambda link, or what explains it, each with what to do: a notification that
    points at a function that isn't there, a function that doesn't let the bucket invoke it, files the filter or the
    event types miss, no file arriving at all, files that arrived without the function running, and runs that
    failed. (level, message) pairs.
    """
    n, fn, bucket = link.notification, link.function, link.bucket
    where = _where(n)
    found: list[tuple[str, str]] = []
    if link.exists is False:
        found.append(("warn", f"Bucket {bucket} sends {_events(n)} for files {where} to Lambda function {fn}, which "
                              f"isn't there in {link.region}: those events invoke nothing. Point the notification at "
                              "the function that should run, or remove it (aws s3api "
                              f"get-bucket-notification-configuration --bucket {bucket} shows it)."))
        return found
    if link.allowed is False:
        account = f" --source-account {link.account}" if link.account else ""
        found.append(("warn", f"{fn}'s resource policy doesn't let bucket {bucket} invoke it: S3 can't deliver the "
                              f"events for files {where}, so none of them run it. Allow it: aws lambda add-permission "
                              f"--function-name {fn} --statement-id s3-{bucket} --action lambda:InvokeFunction "
                              f"--principal s3.amazonaws.com --source-arn arn:aws:s3:::{bucket}{account}"
                              + (f" --region {link.region}" if link.region else "")))
    a, m = link.arrivals, link.metrics
    if a is None and not n.on_created():
        found.append(("info", f"Bucket {bucket} invokes {fn} on {_events(n)}, not on new files, so the listing can't "
                              f"show what it sent: CloudWatch's count of {fn}'s runs is the record."))
    if a is not None:
        if a.multipart_missed or a.whole_missed:
            missed = a.multipart_missed + a.whole_missed
            was, they = ("was", "it") if missed == 1 else ("were", "they")
            how = ("uploaded in parts (multipart), which sends s3:ObjectCreated:CompleteMultipartUpload" if
                   a.multipart_missed else "uploaded whole (a PUT), which sends s3:ObjectCreated:Put")
            found.append(("warn", f"{_plural(missed, 'file')} {where} written in the window {was} {how}, but the "
                                  f"notification fires only on {_events(n)}: {they} never invoked {fn} (e.g. "
                                  f"{a.missed_keys[0]}). Make it fire on s3:ObjectCreated:* instead."))
        if a.case_missed:
            endings = sorted({key[-len(n.suffix):] for key in a.case_missed})
            end, they = ("ends", "it") if len(a.case_missed) == 1 else ("end", "they")
            found.append(("warn", f"{_plural(len(a.case_missed), 'file')} under '{n.prefix}' written in the window "
                                  f"{end} in {', '.join(repr(e) for e in endings)}, which the suffix filter "
                                  f"'{n.suffix}' doesn't match (S3 compares case-sensitively): {they} never invoked "
                                  f"{fn} (e.g. {a.case_missed[0]}). Have the uploader write '{n.suffix}', or add a "
                                  "notification for the other ending."))
        if not a.matched and not a.case_missed:
            last = (f" The newest file it covers is {a.last_match.key}, written {_iso(a.last_match.last_modified)}."
                    if a.last_match else " No file there matches its filter at all.")
            found.append(("info", f"No file {where} in bucket {bucket} was written in the window"
                                  + (" (in the keys read)" if a.cut_short else "")
                                  + f", so S3 had nothing to send {fn}: if it didn't run, that's why.{last}"))
        if m is not None and a.sent:
            calls, throttles = int(m.invocations), int(m.throttles)
            throttled = f" ({_plural(throttles, 'call')} throttled)" if throttles else ""
            if not calls and link.allowed is not False:
                found.append(("warn", f"{_plural(a.sent, 'file')} {where} arrived in the window and should have "
                                      f"invoked {fn}, but CloudWatch counted no runs of it{throttled}: S3's events "
                                      f"didn't reach it. function_details('{fn}') shows whether S3 may invoke it "
                                      "and its reserved concurrency (0 stops every run)."))
            elif 0 < calls < a.sent:
                found.append(("info", f"{_plural(a.sent, 'file')} arrived that should have invoked {fn}, but it ran "
                                      f"{_plural(calls, 'time')} in the window{throttled}: some events may not have "
                                      "reached it yet, since Lambda queues S3's events and retries them for up to 6 "
                                      "hours."))
        if a.cut_short:
            found.append(("info", f"The listing of bucket {bucket} {where} stopped at {a.scanned:,} keys (the budget's "
                                  "s3_keys), in key order: the file counts cover those keys only. A notification "
                                  "with a narrower prefix, or AgentView(budget={'s3_keys': "
                                  f"{a.scanned * 5}}}), reads more."))
    if m is not None and m.errors:
        found.append(("warn", f"{fn} failed {_plural(int(m.errors), 'time')} in {_plural(int(m.invocations), 'run')} "
                              f"in the window: function_errors('{fn}') shows why. S3 invokes it asynchronously, so a "
                              "failed event is retried twice, then dropped unless the function has an on-failure "
                              "destination."))
    return found


def summarize_triggers(links: list[TriggerLink], start: datetime, end: datetime, *, bucket: str = "",
                       function: str = "", others: list[EventNotification] | None = None,
                       notes: list[tuple[str, str]] | None = None, errors: dict[str, str] | None = None,
                       searched: list[str] | None = None) -> dict[str, Any]:
    """
    s3_triggers()'s answer: each S3 -> Lambda link (the events and keys it fires on, whether the function is there
    and lets the bucket invoke it, the files written in the window that it covers and fired on, and the function's
    runs and failures in that window), the bucket's other notification targets, and every finding.
    """
    found: list[tuple[str, str]] = list(notes or [])
    rows = []
    for link in links:
        n, a, m = link.notification, link.arrivals, link.metrics
        row: dict[str, Any] = {
            "bucket": link.bucket, "function": link.function, "function_region": link.region,
            "notification_id": n.id or None, "fires_on": n.events, "prefix": n.prefix, "suffix": n.suffix,
            "function_exists": link.exists, "s3_may_invoke": link.allowed,
        }
        if a is not None:
            row["files_in_window"] = {
                "covered_by_filter": a.matched, "invoked_it": a.sent,
                "missed_by_upload_kind": a.multipart_missed + a.whole_missed, "missed_by_case": len(a.case_missed),
                "newest_that_invoked_it": [file_row(o) for o in a.newest],
                "keys_read": a.scanned, "cut_short": a.cut_short,
            }
            row["newest_file_it_covers"] = file_row(a.last_match) if a.last_match else None
        if m is not None:
            row["function_in_window"] = {"runs": int(m.invocations), "failed": int(m.errors),
                                         "throttled": int(m.throttles)}
        rows.append(row)
        found += trigger_findings(link)
    if not links:
        if bucket and function:
            found.append(("info", f"No event notification of bucket {bucket} invokes {function}."))
        elif bucket:
            found.append(("info", f"Bucket {bucket}'s event notifications invoke no Lambda function."
                                  + (" EventBridge gets its events, so an EventBridge rule may invoke one: "
                                     "function_details lists a function's EventBridge rules." if any(
                                         o.target == "EventBridge" for o in others or []) else "")))
        elif function:
            looked = (f" (checked {_plural(len(searched), 'bucket')}: {', '.join(searched[:10])}"
                      + (", ..." if len(searched) > 10 else "") + ")") if searched else ""
            found.append(("info", f"No bucket's event notification invokes {function}{looked}."))
    out: dict[str, Any] = {
        "window": _window(start, end), "bucket": bucket or None, "function": function or None,
        "links": rows,
        "other_targets": [o.describe() for o in others or []],
        "findings": [{"level": level, "message": text} for level, text in found],
        "scanned": sum(link.arrivals.scanned for link in links if link.arrivals),
        "cut_short": any(link.arrivals.cut_short for link in links if link.arrivals),
        "note": "File times are when each object was last written: an overwritten file shows its newest write, and a "
                "deleted one doesn't show. Runs are CloudWatch's count for the whole function, whatever invoked it.",
    }
    if searched:
        out["buckets_checked"] = searched
    if errors:
        out["errors"] = _with_permissions(errors, _CONFIG_PERMISSIONS)
    return out


def _points_at(n: EventNotification, fn: Function) -> bool:
    """
    Whether a notification invokes this function (any version or alias of it).
    """
    parts, own = (n.arn or "").split(":"), fn.arn.split(":")
    return (n.target == "Lambda function" and len(parts) > 6 and parts[6] == fn.name and parts[3] == fn.region
            and (len(own) < 5 or parts[4] == own[4]))


def _s3_may_invoke(triggers: list[Any], bucket: str) -> bool:
    """
    Whether a function's resource policy (as Lambda's parse_policy reads it) lets S3 invoke it for this bucket:
    a statement for S3 limited to this bucket's ARN (or a pattern it matches), to an account, or not limited.
    """
    arn = f"arn:aws:s3:::{bucket}"
    for t in triggers:
        if t.kind == "S3 bucket" or t.principal == "s3.amazonaws.com":
            if t.arn is None or fnmatch.fnmatchcase(arn, t.arn):
                return True
    return False


# =============================================================================
# S3Toolkit
# =============================================================================


class S3Toolkit(Toolkit):
    """
    The agent's tools for Amazon S3, reading through S3Analyzer: `analyzer` is one to use (else one is made on the
    agent's session). With the Lambda toolkit beside it, it also has s3_triggers, which reads Lambda through that
    toolkit's analyzer.
    """

    name = "s3"
    title = "S3"
    privacy = "File contents are never read: the S3 tools see names, sizes, dates, storage classes and settings."
    reads = S3_TOOL_READS
    service = "s3"
    services = {"GetMetricData": "cloudwatch", "ListMetrics": "cloudwatch", "GetFunction": "lambda",
                "GetPolicy": "lambda", "ListFunctions": "lambda"}
    permissions = {
        "ListObjectsV2": "s3:ListBucket", "ListBuckets": "s3:ListAllMyBuckets", "HeadObject": "s3:GetObject",
        "HeadBucket": "s3:ListBucket", "GetBucketNotificationConfiguration": "s3:GetBucketNotification",
        "GetBucketEncryption": "s3:GetEncryptionConfiguration",
        "GetBucketLifecycleConfiguration": "s3:GetLifecycleConfiguration",
        "GetBucketReplication": "s3:GetReplicationConfiguration",
        "GetObjectLockConfiguration": "s3:GetBucketObjectLockConfiguration",
        "ListBucketInventoryConfigurations": "s3:GetInventoryConfiguration",
    }
    view = "S3View"

    @property
    def covers(self) -> str:
        links = (", and which S3 events invoke which Lambda functions and whether the files that arrived ran them"
                 if self.peer("lambda") else "")
        return ("Amazon S3: which buckets exist and how they're set up (public access, encryption, versioning, "
                "lifecycle rules, event notifications), their size and estimated storage cost, what's in a folder and "
                f"which files were written when, and one file's headers (never what's inside a file){links}.")

    def make_analyzer(self, session: Any) -> S3Analyzer:
        return _s3_module().S3Analyzer(session)

    @property
    def analyzer(self) -> S3Analyzer:
        """
        The S3Analyzer the tools read through, made on first use and hooked to the read-only guard.
        """
        return super().analyzer

    def instructions(self, budget: Budget) -> str:
        text = f"""\
Amazon S3
- Locations are 's3://bucket/prefix' (or 'bucket/prefix'). Keys and prefixes are case-sensitive.
- list_folder reads one level and is fast. find_files and folder_summary read every key under a location, up to
  {budget.s3_keys:,} keys: give them the narrowest prefix you can (a date's folder rather than the whole bucket).
- The S3 tools never read what's inside a file: names, sizes, dates, storage classes and settings only. Say so when
  asked about a file's contents.
- A file's time is when it was last written: an overwritten file shows only its newest write, and a deleted file
  doesn't show at all."""
        if self.peer("lambda"):
            text += """

S3 and Lambda together
- For a function that S3 events invoke ("why didn't orders-etl run?", "were last night's files processed?", "which
  Lambda processes uploads to acme-raw?"), call s3_triggers first: it joins the bucket's event notifications, the
  files written in the window and the function's runs and failures in that window. Then function_errors or
  function_logs (search= a file's key) for the runs that failed.
- function_details lists the buckets a function's resource policy lets invoke it; s3_triggers checks what those
  buckets actually send it."""
        return text

    def request_cost(self, counts: dict[str, int]) -> float:
        """
        USD for the S3 requests in `counts` (the guard's 's3:ListObjectsV2' -> n), at S3_REQUEST_PRICES.
        """
        lists = sum(n for op, n in counts.items() if op.startswith("s3:List"))
        others = sum(n for op, n in counts.items() if op.startswith("s3:") and not op.startswith("s3:List"))
        return (lists * S3_REQUEST_PRICES["list"] + others * S3_REQUEST_PRICES["get"]) / 1000

    # ------------------------------------------------------------------ the tools

    def tools(self) -> list[Callable[..., dict[str, Any]]]:
        """
        find_buckets, bucket_details, list_folder, find_files, folder_summary and file_info, and s3_triggers when
        the agent has the Lambda toolkit too: plain functions (their type hints and docstrings tell the model how to
        call them), each reading through S3Analyzer and returning a compact, JSON-ready summary.
        """

        def find_buckets(match: str = "", sizes: bool = False) -> dict[str, Any]:
            """
            Lists the S3 buckets in the account: name, region and when each was made. match: a name glob like
            'acme-*'. sizes=True also reads each bucket's object count and size from CloudWatch's daily storage
            metrics and sorts the biggest first (two reads per bucket: slower when there are many).
            """
            core = self.analyzer
            buckets = [b for b in core.list_buckets() if not match or fnmatch.fnmatchcase(b.name, match)]
            metrics: dict[str, BucketMetrics] | None = None
            errors: dict[str, str] = {}
            if sizes:
                metrics, errors = self._sizes(buckets[:_SIZED_BUCKETS])
            out = summarize_buckets(buckets, metrics, errors)
            if sizes and len(buckets) > _SIZED_BUCKETS:
                out["sizes_note"] += f" Sizes were read for the first {_SIZED_BUCKETS} only: narrow with match=."
            out["_summary"] = f"{_plural(len(buckets), 'bucket')}" + (
                f" in {', '.join(out['regions'][:4])}" if out["regions"] else "")
            out["_next"] = [(self._view_call("overview", match or None),
                             "every bucket: size, cost, encryption, public access and warnings")]
            return out

        def bucket_details(bucket: str) -> dict[str, Any]:
            """
            Everything about one bucket's setup: public access, encryption, versioning, object lock, lifecycle
            rules in plain words, replication, access logging, tags, its event notifications (which Lambda
            functions, queues and topics it sends which events to, for which keys), its object count, size and
            estimated monthly storage cost from CloudWatch, and the findings about it with what to do.
            """
            s3 = _s3_module()
            core = self.analyzer
            name, _ = s3.parse_s3_uri(bucket)
            core.bucket_region(name)  # a bucket that isn't there fails here, with close names
            cfg = core.bucket_config(name)
            errors: dict[str, str] = {}
            notifications = metrics = account = None
            try:
                notifications = core.notifications(name)
            except (ClientError, BotoCoreError) as exc:
                errors["notifications"] = _code(exc)
            try:
                metrics = core.bucket_metrics(name)
                self._count_metrics(_metrics_read(metrics), _METRIC_PRICE)
            except (ClientError, BotoCoreError) as exc:
                errors["metrics"] = _code(exc)
            try:
                account = core.account_public_access_block()
            except (ClientError, BotoCoreError, ValueError):
                account = None  # unknown: the findings say account-level settings may still apply
            out = summarize_bucket(cfg, metrics, notifications, account_block=account, prices=core.prices,
                                   errors=errors)
            size = out.get("size", {}).get("total")
            out["_summary"] = ", ".join(filter(None, [
                size and f"{size}", out["public_access"], f"encryption {out['encryption']}",
                f"{_plural(len(notifications), 'event notification')}" if notifications is not None else "",
                _plural(len(out["findings"]), "finding")]))
            out["_next"] = [(self._view_call("bucket_info", name), "its settings, policy and size as a report")]
            if cfg.policy:
                out["_next"].append((self._view_call("policy", name), "its bucket policy in plain English"))
            return out

        def list_folder(location: str) -> dict[str, Any]:
            """
            What's directly in a folder (one level, like `aws s3 ls`): its subfolders, and its files newest first
            with size, when each was written and storage class. Fast even on huge buckets. location:
            's3://bucket/prefix/' or 'bucket/prefix/'; a bucket name alone lists its top level.
            """
            listing = self.analyzer.ls(location, limit=1000)
            out = summarize_listing(listing)
            out["_summary"] = (f"{_plural(len(listing.folders), 'folder')}, {_plural(len(listing.objects), 'file')}"
                               + (" (more not listed)" if listing.truncated else ""))
            out["_next"] = [(self._view_call("ls", listing.uri), "the same folder as a report")]
            return out

        def find_files(location: str, match: str = "", since: str = "", until: str = "", min_size: str = "",
                       max_size: str = "", limit: int = 30) -> dict[str, Any]:
            """
            The files under a location (every level below it) that match a filter: how many, their total size,
            when the oldest and newest were written, and the newest `limit` of them. match: a glob on the file name,
            or on the whole key when it has a '/' ('*.csv', 'orders_2026-10-1*'). since/until: only files last
            written in that window, as ISO UTC times ('2026-10-15T18:00Z') or relative ('24h', '7d'). min_size /
            max_size: '10MB', '1GB'. Reads the keys one listing page at a time, up to the budget's limit.
            """
            s3 = _s3_module()
            core = self.analyzer
            start, end = _between(since, until)
            keep = s3.make_filter(pattern=match or None, min_size=min_size or None, max_size=max_size or None,
                                  modified_after=start, modified_before=end if until else None)
            cap = self.budget.s3_keys
            scanned, found, more = 0, [], False
            for obj in core.iter_objects(location, limit=cap + 1):  # one past the cap says there's more
                if scanned >= cap:
                    more = True
                    break
                scanned += 1
                if keep(obj):
                    found.append(obj)
            bucket, prefix = s3.parse_s3_uri(location)
            out = summarize_found(s3.s3_uri(bucket, prefix), found, scanned=scanned, cut_short=more, start=start,
                                  end=end, limit=max(1, min(int(limit or 30), 200)),
                                  filters={"match": match, "min_size": min_size, "max_size": max_size})
            out["_summary"] = (f"{_plural(len(found), 'file')} ({out['total_size']})"
                               + (f" of {scanned:,} keys" if scanned != len(found) else "")
                               + (", cut short" if out["cut_short"] else ""))
            out["_next"] = [(self._view_call("find", s3.s3_uri(bucket, prefix), pattern=match or None,
                                             min_size=min_size or None, max_size=max_size or None,
                                             modified_after=_iso(start), modified_before=_iso(end) if until else None),
                             "the same search as a report")]
            return out

        def folder_summary(location: str) -> dict[str, Any]:
            """
            What a location holds, every level below it: how many files and how much, by file type, storage
            class and folder, the oldest and newest file, the largest ones, the estimated monthly storage cost and
            findings (many small files, files in Glacier, files untouched for 90+ days...). Reads every key, up to
            the budget's limit: use the narrowest location you can.
            """
            core = self.analyzer
            summary = core.summarize(location, top_n=5, folder_depth=1, limit=self.budget.s3_keys)
            out = summarize_prefix(summary, core.prices)
            out["_summary"] = (f"{_plural(summary.object_count, 'file')}, {out['total_size']}, about "
                               f"{human_money(summary.monthly_cost)}/month" + (", cut short" if summary.truncated
                                                                               else ""))
            out["_next"] = [(self._view_call("summary", summary.uri), "the same as a dashboard"),
                            (self._view_call("tree", summary.uri), "its folders' sizes as a tree")]
            return out

        def file_info(location: str) -> dict[str, Any]:
            """
            One file's headers, never its content: size, when it was written, content type, storage class (a
            file in Glacier must be restored before anything can read it), encryption (a KMS key means readers need
            kms:Decrypt on it), version, restore and lock status, its metadata and tags. location:
            's3://bucket/key'.
            """
            core = self.analyzer
            info = core.head(location)
            out = summarize_file(info)
            out["_summary"] = f"{out['size']}, {info.get('storage_class', 'STANDARD')}, written " + (
                _iso(info.get("last_modified")) or "?")
            out["_next"] = [(self._view_call("head", info["uri"]), "every header as a report"),
                            (self._view_call("preview", info["uri"]), "what's in it, in your notebook only")]
            return out

        tools = [find_buckets, bucket_details, list_folder, find_files, folder_summary, file_info]
        if self.peer("lambda") is not None:
            tools.append(self._triggers_tool())
        return tools

    def _triggers_tool(self) -> Callable[..., dict[str, Any]]:
        def s3_triggers(bucket: str = "", function: str = "", since: str = "24h", until: str = "",
                        region: str = "") -> dict[str, Any]:
            """
            How S3 and Lambda are wired: the bucket event notifications that invoke Lambda functions. Give a
            bucket (the functions its events invoke), a function (the buckets whose events invoke it, from its
            resource policy and those buckets' notifications) or both. For each link: the events and the key
            prefix and suffix it fires on, whether the function exists and lets that bucket invoke it, how many files
            the filter covers were written in the window and how many of them invoked it (and which it missed, by
            upload kind or by the case of their ending), and how often the function ran, failed and was throttled in
            that window (CloudWatch). Use it for "which Lambda processes files in X", "why didn't Y run after the
            upload" or "were last night's files processed". since/until: ISO UTC times or relative ('24h'). region:
            the function's region when it isn't the default.
            """
            return self._triggers(bucket, function, since, until, region)

        return s3_triggers

    # ------------------------------------------------------------------ what the tools share

    def _sizes(self, buckets: list[BucketInfo]) -> tuple[dict[str, BucketMetrics], dict[str, str]]:
        """
        Each bucket's CloudWatch size and object count, read side by side; errors by 'bucket:metrics'.
        """
        core = self.analyzer
        for region in {b.region for b in buckets if b.region}:
            core._cloudwatch_in(region)  # boto3 sessions aren't thread-safe: make the clients before the threads

        def size(bucket: BucketInfo) -> tuple[str, BucketMetrics | None, str | None]:
            try:
                return bucket.name, core.bucket_metrics(bucket.name), None
            except (ClientError, BotoCoreError) as exc:
                return bucket.name, None, _code(exc)

        metrics: dict[str, BucketMetrics] = {}
        errors: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            for name, found, error in pool.map(size, buckets):
                if found is not None:
                    metrics[name] = found
                    self._count_metrics(_metrics_read(found), _METRIC_PRICE)
                if error:
                    errors[f"{name}:metrics"] = error
        return metrics, errors

    def _triggers(self, bucket: str, function: str, since: str, until: str, region: str) -> dict[str, Any]:
        """
        s3_triggers: the links for a bucket, a function or both, each checked from both ends (see its docstring).
        """
        s3, lf = _s3_module(), _lambda_module()
        lam_kit = self.peer("lambda")
        if lam_kit is None:
            raise ValueError("s3_triggers needs the Lambda toolkit too: AgentView(toolkits=['lambda', 's3'])")
        lam, core = lam_kit.analyzer, self.analyzer
        bucket, function = str(bucket or "").strip(), str(function or "").strip()
        if not bucket and not function:
            raise ValueError("s3_triggers needs a bucket, a function, or both")
        start, end = _between(since or "24h", until)
        name = s3.parse_s3_uri(bucket)[0] if bucket else ""
        fn = lam.function(function, region=region or None) if function else None  # missing: its close names
        errors: dict[str, str] = {}
        notes: list[tuple[str, str]] = []
        others: list[EventNotification] = []
        pairs: list[tuple[str, EventNotification]] = []
        searched: list[str] = []
        if name:
            for n in core.notifications(name):  # a missing bucket fails here: its close names
                if n.target != "Lambda function":
                    others.append(n)
                elif fn is None or _points_at(n, fn):
                    pairs.append((name, n))
        elif fn is not None:
            searched, named, notes = self._buckets_for(fn, errors)
            for candidate in searched:
                try:
                    pairs += [(candidate, n) for n in core.notifications(candidate) if _points_at(n, fn)]
                except (ClientError, BotoCoreError) as exc:
                    errors[f"{candidate}:notifications"] = _code(exc)
            for leftover in sorted(set(named) - {b for b, _ in pairs}):
                notes.append(("info", f"{fn.name}'s resource policy lets bucket {leftover} invoke it, but "
                                      f"{leftover} has no event notification that does: a leftover permission, or a "
                                      "notification that was removed."))

        functions: dict[str, Function | None] = {}  # notification ARN -> the function, None when it isn't there
        unknown: set[str] = set()  # ARNs that couldn't be looked up
        policies: dict[tuple[str, str, str | None], list[Any] | None] = {}
        listings: dict[tuple[str, str], list[ObjectInfo] | None] = {}
        links: list[TriggerLink] = []
        cap = self.budget.s3_keys
        for where, n in pairs:
            arn = n.arn or ""
            link = TriggerLink(where, n, account=arn.split(":")[4] if arn.count(":") >= 5 else None)
            if arn not in functions:
                try:
                    functions[arn] = lam.function(arn)
                except (ClientError, BotoCoreError) as exc:
                    functions[arn] = None
                    if _code(exc) != "ResourceNotFoundException":
                        errors[f"{n.name}:function"] = _code(exc)
                        unknown.add(arn)
            target = functions[arn]
            link.exists = None if arn in unknown else target is not None
            if target is not None:
                qualifier = lf.parse_function_ref(arn)[1]
                key = (target.name, target.region, qualifier)
                if key not in policies:
                    try:
                        policies[key] = lf.parse_policy(lam.policy(target.name, target.region, qualifier=qualifier))
                    except (ClientError, BotoCoreError) as exc:
                        errors[f"{target.name}:policy_of_function"] = _code(exc)
                        policies[key] = None
                if policies[key] is not None:
                    link.allowed = _s3_may_invoke(policies[key], where)
            if link.exists is not False and n.on_created():
                listed = (where, n.prefix)
                if listed not in listings:
                    try:
                        listings[listed] = list(core.iter_objects(s3.s3_uri(where, n.prefix), limit=cap + 1))
                    except (ClientError, BotoCoreError) as exc:
                        errors[f"{where}:list"] = _code(exc)
                        listings[listed] = None
                if listings[listed] is not None:
                    link.arrivals = count_arrivals(listings[listed], n, start, end, limit=cap)
            links.append(link)

        live = list({f.arn: f for f in functions.values() if f is not None}.values())
        if live:
            try:
                numbers, read = lam.metrics(live, since=start, until=end, logs=False)
                self._count_metrics(read, lam.prices["metric_request"])
                for link in links:
                    target = functions.get(link.notification.arn or "")
                    link.metrics = numbers.get(target.arn) if target is not None else None
            except (ClientError, BotoCoreError) as exc:
                errors["metrics"] = _code(exc)

        out = summarize_triggers(links, start, end, bucket=name, function=fn.name if fn else "", others=others,
                                 notes=notes, errors=errors, searched=searched)
        warned = [link for link in links if any(level == "warn" for level, _ in trigger_findings(link))]
        pairs_text = sorted({f"{link.bucket} → {link.function}" for link in links})
        out["_summary"] = (_plural(len(links), "S3 → Lambda link") + (f": {_clip(', '.join(pairs_text), 120)}"
                                                                      if links else "")
                           + (f"; {_plural(len(warned), 'link')} with warnings" if warned else ""))
        nxt: list[tuple[str, str]] = []
        for link in links:
            target = functions.get(link.notification.arn or "")
            if target is not None and link.metrics is not None and link.metrics.errors:
                nxt.append((lam_kit._next_for(target, "errors", since=_iso(start)), "why its runs failed"))
            if link.arrivals is not None and link.arrivals.matched:
                n = link.notification
                nxt.append((self._view_call("find", s3.s3_uri(link.bucket, n.prefix),
                                            pattern=f"*{n.suffix}" if n.suffix else None,
                                            modified_after=_iso(start)), "the files written in the window"))
            if target is not None:
                nxt.append((lam_kit._next_for(target, "function_info"), "the function, its triggers and settings"))
        out["_next"] = nxt
        return out

    def _buckets_for(self, fn: Function, errors: dict[str, str]) -> tuple[list[str], list[str],
                                                                          list[tuple[str, str]]]:
        """
        (the buckets to look through, the buckets its policy names, notes) for a function: the buckets whose
        notifications may invoke it. Those its resource policy names for S3; or, when the policy lets S3 invoke it
        for any bucket (or for names matching a pattern, or doesn't let S3 at all), the buckets in its region, at
        most _SEARCHED_BUCKETS.
        """
        lf = _lambda_module()
        notes: list[tuple[str, str]] = []
        try:
            triggers = lf.parse_policy(self.peer("lambda").analyzer.policy(fn.name, fn.region))
        except (ClientError, BotoCoreError) as exc:
            errors[f"{fn.name}:policy_of_function"] = _code(exc)
            triggers = None
        for_s3 = [t for t in triggers or [] if t.kind == "S3 bucket" or t.principal == "s3.amazonaws.com"]
        wild = set("*?[")
        named = list(dict.fromkeys(t.arn.split(":::", 1)[1] for t in for_s3
                                   if t.arn and t.arn.startswith("arn:aws:s3:::") and not set(t.arn) & wild))
        patterns = [t.arn for t in for_s3 if t.arn and set(t.arn) & wild]
        anywhere = triggers is None or not for_s3 or any(t.arn is None for t in for_s3)
        if triggers is not None and not for_s3:
            notes.append(("warn", f"{fn.name}'s resource policy has no statement that lets S3 invoke it, so no "
                                  f"bucket's events can reach it. The buckets in {fn.region} were checked for "
                                  "notifications that point at it anyway."))
        if named and not patterns and not anywhere:
            return named, named, notes
        try:
            buckets = self.analyzer.list_buckets()
        except (ClientError, BotoCoreError) as exc:
            errors["buckets"] = _code(exc)
            return named, named, notes
        here = [b.name for b in buckets if b.region == fn.region and (
            anywhere or any(fnmatch.fnmatchcase(f"arn:aws:s3:::{b.name}", p) for p in patterns))]
        candidates = list(dict.fromkeys(named + here))
        if len(candidates) > _SEARCHED_BUCKETS:
            notes.append(("info", f"Only {_SEARCHED_BUCKETS} of the {len(candidates)} buckets in {fn.region} were "
                                  "checked for notifications that invoke it: name the bucket to check that one."))
            candidates = candidates[:_SEARCHED_BUCKETS]
        return candidates, named, notes

    def not_found(self, exc: ClientError, arguments: dict[str, Any]) -> dict[str, Any] | None:
        """
        A tool's answer when a bucket, a file or a function isn't there: the closest names.
        """
        s3 = _s3_module()
        code = _error_code(exc)
        if code == "ResourceNotFoundException" and arguments.get("function"):
            lam = self.peer("lambda")
            return lam.not_found(exc, arguments) if lam is not None else None
        location = str(arguments.get("bucket") or arguments.get("location") or "").strip()
        if not location:
            return None
        bucket, key = s3.parse_s3_uri(location)
        core = self.analyzer
        if code == "NoSuchBucket":
            try:
                names = [b.name for b in core.list_buckets(with_region=False)]
            except (ClientError, BotoCoreError):
                names = []
            return {"error": f"No bucket named {bucket!r} (that this account can see). Names are case-sensitive.",
                    "close_names": difflib.get_close_matches(bucket, names, n=3, cutoff=0.5),
                    "hint": "Use one of close_names, or find_buckets to list them."}
        if code in ("404", "NoSuchKey", "NotFound") and getattr(exc, "operation_name", "") == "HeadObject":
            try:
                listing = core.ls(s3.s3_uri(bucket, s3.base_prefix(key)), limit=1000)
            except (ClientError, BotoCoreError):
                listing = None
            if listing is not None and key.rstrip("/") + "/" in listing.folders:
                return {"error": f"s3://{bucket}/{key} is a folder, not a file.",
                        "hint": "list_folder or find_files show what's in it."}
            names = [o.key for o in listing.objects] if listing is not None else []
            return {"error": f"No file at s3://{bucket}/{key}. Keys are case-sensitive.",
                    "close_keys": difflib.get_close_matches(key, names, n=3, cutoff=0.6),
                    "hint": "Use one of close_keys, or list_folder / find_files to see what's there."}
        return None


def _lambda_module() -> Any:
    """
    aws_analyzer.lambda_functions, which s3_triggers reads Lambda's policies with (loaded when it first runs).
    """
    from ... import lambda_functions

    return lambda_functions


def _metrics_read(metrics: BucketMetrics) -> int:
    """
    How many CloudWatch metrics bucket_metrics() read: one per storage type it found, and the object count.
    """
    return len(metrics.size_by_storage_type) + (metrics.object_count is not None)

