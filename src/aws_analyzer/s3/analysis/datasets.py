"""
S3's reports as data: the tables behind buckets(), overview(), ls(), find() / largest() / newest() / oldest(),
summary(), duplicates(), versions(), history(), deleted(), compare() and preview(), each column said in plain words
with how to read it, and the charts the workbook draws from them. Pure: every function takes what a command already
read and returns a ReportData (_kit/datasets.py), so ui.data() and ui.to_excel() read nothing from S3 again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..._kit.datasets import DataChart, DataColumn, DataTable, Recipe, ReportData
from ..._kit.fmt import Stat, _utcnow, file_extension
from ..helpers import GB, MB, base_prefix, parse_s3_uri, relative_key, s3_uri
from ..models import (
    BucketChecks,
    BucketInfo,
    BucketReport,
    CompareResult,
    DeletedFiles,
    DuplicateReport,
    Listing,
    ObjectInfo,
    ObjectVersion,
    PrefixSummary,
    Preview,
    VersionStats,
)
from .cost import object_monthly_cost
from .summary import _by_size

SECONDS_PER_DAY = 86_400
_CHECK_TONES = (("bad", "bad"), ("warn", "warn"), ("unknown", "warn"), ("ok", "ok"))
_STATUS_TONES = (("different", "bad"), ("only in A", "warn"), ("only in B", "warn"))


def _fraction(part: float | None, whole: float | None) -> float | None:
    """
    part / whole as a share from 0 to 1, or None without a whole.
    """
    if part is None or not whole:
        return None
    return part / whole


def _megabytes(size: int | None) -> float | None:
    return None if size is None else size / MB


def _gigabytes(size: int | None) -> float | None:
    return None if size is None else size / GB


def _age_days(moment: datetime | None, now: datetime) -> float | None:
    """
    How many days ago a moment was, to a tenth of a day.
    """
    if moment is None:
        return None
    return round((now - moment).total_seconds() / SECONDS_PER_DAY, 1)


def _stem(command: str, uri: str) -> str:
    """
    The start of the workbook's file name: 's3-summary-demo-lake-raw'.
    """
    bucket, prefix = parse_s3_uri(uri) if uri else ("", "")
    return f"s3-{command}-{bucket}-{prefix}".rstrip("-/")


_SIZE_COLUMNS = [
    DataColumn("size_bytes", "its size", "bytes"),
    DataColumn("size_mb", "its size in megabytes (1 MB = 1,048,576 bytes, as the AWS console counts)", "number",
               bar=True),
]


def _file_columns(*, with_cost: bool = True) -> list[DataColumn]:
    """
    The columns every table of files shares, in order.
    """
    columns = [
        DataColumn("key", "its key below the folder asked about (the path inside the bucket)", "id"),
        DataColumn("file_name", "the last part of the key"),
        DataColumn("extension", "its file type, from its name ('csv.gz', 'parquet', '(none)')"),
        *_SIZE_COLUMNS,
        DataColumn("last_modified", "when it was last written", "time"),
        DataColumn("age_days", "days since it was last written", "number",
                   read="Files untouched for 30+ days are candidates for a cheaper storage class (see what_if)."),
        DataColumn("storage_class", "where S3 keeps it, which sets its price: STANDARD, STANDARD_IA, GLACIER, ...",
                   tones=(("GLACIER", "warn"), ("DEEP_ARCHIVE", "warn"))),
    ]
    if with_cost:
        columns.append(DataColumn("est_monthly_usd", "an estimate of what storing it costs a month, at list prices",
                                  "usd", read="Storage only: requests and data transfer aren't in it."))
    columns.append(DataColumn("uri", "its full s3:// path: what preview(), head() and read_df() take", "id"))
    return columns


def object_row(obj: ObjectInfo, *, base: str = "", now: datetime | None = None,
               prices: dict[str, float] | None = None) -> dict[str, Any]:
    """
    One file as a row: its key below `base`, name, type, size, when it changed and how old that is, storage class,
    its estimated monthly storage cost and its s3:// path.
    """
    now = now or _utcnow()
    return {
        "key": relative_key(obj.key, base), "file_name": obj.key.rsplit("/", 1)[-1],
        "extension": file_extension(obj.key), "size_bytes": obj.size, "size_mb": _megabytes(obj.size),
        "last_modified": obj.last_modified, "age_days": _age_days(obj.last_modified, now),
        "storage_class": obj.storage_class,
        "est_monthly_usd": object_monthly_cost(obj.size, obj.storage_class, prices),
        "uri": obj.uri,
    }


_FILE_RECIPES = [
    Recipe("df.groupby('extension').size_mb.agg(['count', 'sum']).sort_values('sum')", "space by file type"),
    Recipe("df[df.age_days > 90].size_mb.sum()", "megabytes nobody has written to for 90 days"),
    Recipe("df.set_index('last_modified').size_mb.resample('D').sum()", "megabytes written per day"),
    Recipe("ui.core.read_df(df.uri[0])", "the first file itself, as a DataFrame (csv, parquet, json, ...)"),
]


def _files_table(name: str, title: str, objects: list[ObjectInfo], *, base: str,
                 prices: dict[str, float] | None) -> DataTable:
    now = _utcnow()
    rows = [object_row(obj, base=base, now=now, prices=prices) for obj in objects]
    return DataTable(name, title, "a file (an S3 object)", _file_columns(), rows, recipes=list(_FILE_RECIPES))


def files_data(objects: list[ObjectInfo], *, title: str, uri: str, command: str,
               prices: dict[str, float] | None = None) -> ReportData:
    """
    find()'s, largest()'s, newest()'s or oldest()'s report as data: a row per file, in the report's order.
    """
    base = base_prefix(parse_s3_uri(uri)[1])
    table = _files_table("files", title, objects, base=base, prices=prices)
    table.notes.append("Rows are in the report's order.")
    charts = [DataChart("Size of each file", "files", "file_name", ("size_mb",), "bar",
                        "The files in the report's order, by size: the longest bars are where the space goes.", "MB")]
    return ReportData(title, [table], charts, stem=_stem(command, uri))


def buckets_data(buckets: list[BucketInfo]) -> ReportData:
    """
    buckets()' report as data: a row per bucket, with its region and age.
    """
    now = _utcnow()
    columns = [
        DataColumn("bucket", "the bucket's name", "id"),
        DataColumn("region", "the AWS region it lives in", "id"),
        DataColumn("created", "when it was created", "time"),
        DataColumn("age_days", "days since it was created", "number"),
        DataColumn("uri", "its s3:// path: what summary() and ls() take", "id"),
    ]
    rows = [{"bucket": b.name, "region": b.region, "created": b.created, "age_days": _age_days(b.created, now),
             "uri": s3_uri(b.name)} for b in buckets]
    table = DataTable("buckets", "Every bucket in the account", "a bucket", columns, rows,
                      notes=["S3 lists every bucket of the account, whatever region the view is in."],
                      recipes=[Recipe("df.groupby('region').size()", "buckets per region")])
    return ReportData("S3 buckets", [table], stem="s3-buckets")


@dataclass
class BucketLine:
    """
    One bucket of overview(): its report, its checks and what CloudWatch says it holds (None without numbers).
    """

    report: BucketReport
    checks: BucketChecks
    size: int | None = None
    objects: int | None = None
    cost: float | None = None


_CHECK_NAMES = {
    "Bucket policy": "policy", "Block Public Access": "block_public_access", "Encryption": "encryption",
    "Old versions": "old_versions",
}


def _check_columns() -> list[DataColumn]:
    return [DataColumn(name, f"the {column} check: bad, warn, unknown (couldn't be read) or ok", tones=_CHECK_TONES)
            for column, name in _CHECK_NAMES.items()]


def _bucket_row(line: BucketLine, total_size: int) -> dict[str, Any]:
    config = line.report.config
    row: dict[str, Any] = {
        "bucket": config.name, "region": config.region, "objects": line.objects, "size_bytes": line.size,
        "size_gb": _gigabytes(line.size), "share_of_size": _fraction(line.size, total_size),
        "est_monthly_usd": line.cost, "worst": line.checks.worst,
    }
    for check in line.checks.checks:
        row[_CHECK_NAMES.get(check.column, check.column)] = check.state
    problems = [check.reason for check in line.checks.checks if check.state != "ok" and check.reason]
    enabled_rules = sum(rule.get("Status") == "Enabled" for rule in config.lifecycle_rules)
    row.update({"problems": "; ".join(problems), "versioning": config.versioning,
                "lifecycle_rules": enabled_rules, "created": line.report.bucket.created})
    return row


def overview_data(lines: list[BucketLine]) -> ReportData:
    """
    overview()'s report as data: a row per bucket, the biggest first, with each check's result and why.
    """
    total = sum(line.size or 0 for line in lines)
    rows = sorted((_bucket_row(line, total) for line in lines), key=lambda row: -(row["size_bytes"] or 0))
    columns = [
        DataColumn("bucket", "the bucket's name", "id"),
        DataColumn("region", "the AWS region it lives in", "id"),
        DataColumn("objects", "objects stored, every version counted (CloudWatch, about a day old)", "count"),
        DataColumn("size_bytes", "bytes stored, every version (CloudWatch)", "bytes"),
        DataColumn("size_gb", "the same in gigabytes", "number", bar=True),
        DataColumn("share_of_size", "its part of all the buckets' size", "share"),
        DataColumn("est_monthly_usd", "an estimate of its storage bill a month, at list prices", "usd", bar=True),
        DataColumn("worst", "the worst of its checks: bad (open to the internet), warn, unknown or ok",
                   tones=_CHECK_TONES),
        *_check_columns(),
        DataColumn("problems", "what each check that didn't pass found, in a sentence"),
        DataColumn("versioning", "Enabled, Suspended or Disabled: whether deleted files can be brought back"),
        DataColumn("lifecycle_rules", "lifecycle rules switched on: they move or delete files by age", "count",
                   read="0 on a big bucket means nothing ever moves to a cheaper class."),
        DataColumn("created", "when the bucket was created", "time"),
    ]
    table = DataTable(
        "buckets", "Every bucket, the biggest first", "a bucket", columns, rows,
        notes=["Sizes and counts come from CloudWatch's daily storage metrics, so they're about a day old.",
               "A bucket with no size had no CloudWatch numbers yet (new or empty)."],
        recipes=[Recipe("df[df.worst == 'bad'][['bucket', 'problems']]", "buckets open to the internet, and why"),
                 Recipe("df.nlargest(5, 'est_monthly_usd')[['bucket', 'size_gb', 'est_monthly_usd']]",
                        "where the storage bill goes"),
                 Recipe("df[(df.lifecycle_rules == 0) & (df.size_gb > 100)].bucket",
                        "big buckets with no lifecycle rule")],
    )
    charts = [DataChart("Storage by bucket", "buckets", "bucket", ("size_gb",), "bar",
                        "The biggest buckets first.", "GB"),
              DataChart("Estimated monthly cost by bucket", "buckets", "bucket", ("est_monthly_usd",), "bar",
                        "In the same order: storage only, at list prices.", "USD a month")]
    return ReportData("S3 overview", [table], charts, stem="s3-overview")


def listing_data(listing: Listing, *, inside: dict[str, str] | None = None,
                 prices: dict[str, float] | None = None) -> ReportData:
    """
    ls()'s report as data: a row per folder and file directly under the prefix, folders first.
    """
    base = base_prefix(parse_s3_uri(listing.uri)[1])
    bucket = parse_s3_uri(listing.uri)[0]
    now = _utcnow()
    rows: list[dict[str, Any]] = [{"kind": "folder", "key": relative_key(folder, base), "uri": s3_uri(bucket, folder)}
                                  for folder in listing.folders]
    for obj in listing.objects:
        row = {"kind": "file", **object_row(obj, base=base, now=now, prices=prices)}
        if inside is not None:
            row["inside"] = inside.get(obj.key, "")
        rows.append(row)
    columns = [DataColumn("kind", "folder or file"), *_file_columns()]
    if inside is not None:
        columns.append(DataColumn("inside", "what's in the file: a PDF's pages, a table's rows and columns, ..."))
    notes = ["Folders come first. A folder's size isn't listed: summary() adds up everything below it."]
    if listing.truncated:
        notes.append("The listing stopped at its limit: pass limit= for more.")
    table = DataTable("entries", f"What's directly under {listing.uri}", "a folder or a file", columns, rows,
                      notes=notes, recipes=[Recipe("df[df.kind == 'file'].nlargest(5, 'size_mb')", "the biggest files"),
                                            *_FILE_RECIPES[:1]])
    return ReportData(f"ls {listing.uri}", [table], stem=_stem("ls", listing.uri))


def _stat_rows(stats: dict[str, Stat], name: str, total: Stat,
               costs: dict[str, float | None] | None = None) -> list[dict[str, Any]]:
    rows = []
    for label, stat in stats.items():
        row = {name: label, "files": stat.count, "size_bytes": stat.size, "size_gb": _gigabytes(stat.size),
               "share_of_files": _fraction(stat.count, total.count), "share_of_size": _fraction(stat.size, total.size)}
        if costs is not None:
            row["est_monthly_usd"] = costs.get(label)
        rows.append(row)
    return rows


def _stat_columns(name: str, meaning: str, *, with_cost: bool = False) -> list[DataColumn]:
    columns = [
        DataColumn(name, meaning),
        DataColumn("files", "files in it", "count", bar=True),
        DataColumn("size_bytes", "their size", "bytes"),
        DataColumn("size_gb", "their size in gigabytes", "number", bar=True),
        DataColumn("share_of_files", "its part of all the files", "share"),
        DataColumn("share_of_size", "its part of all the space", "share",
                   read="Rows high here and low in share_of_files hold a few big files."),
    ]
    if with_cost:
        columns.append(DataColumn("est_monthly_usd", "what storing them costs a month, at list prices", "usd"))
    return columns


def _summary_tables(s: PrefixSummary) -> list[DataTable]:
    total = Stat(s.object_count, s.total_size)
    return [
        DataTable("folders", "Space by folder", "a folder below the prefix", _stat_columns(
            "folder", "the folder ('(here)' for files directly in the prefix)"),
            _stat_rows(_by_size(s.by_folder), "folder", total), notes=["The biggest folder first."],
            recipes=[Recipe("df.head(10)[['folder', 'size_gb', 'share_of_size']]", "the folders that hold most")]),
        DataTable("file_types", "Space by file type", "a file type", _stat_columns(
            "extension", "the file type, from the name ('csv.gz', 'parquet', '(none)')"),
            _stat_rows(_by_size(s.by_extension), "extension", total)),
        DataTable("storage_classes", "Space and cost by storage class", "a storage class", _stat_columns(
            "storage_class", "where S3 keeps the files, which sets their price", with_cost=True),
            _stat_rows(s.by_storage_class, "storage_class", total, s.cost_by_storage_class),
            notes=["Current versions only: versions() shows the older ones a versioned bucket keeps."]),
        DataTable("size_bands", "Files by size", "a size band", _stat_columns("band", "a range of file sizes"),
                  _stat_rows(s.size_histogram, "band", total),
                  notes=["Many tiny files make every listing and every Spark or Athena read slow, and IA classes "
                         "bill 128 KB for each smaller file."]),
        DataTable("ages", "Files by when they last changed", "an age band",
                  _stat_columns("age", "how long ago the files last changed"), _stat_rows(s.age_histogram, "age", total),
                  notes=["Old files still in STANDARD are what what_if() prices moving to a cheaper class."]),
    ]


def summary_data(s: PrefixSummary, *, prices: dict[str, float] | None = None) -> ReportData:
    """
    summary()'s report as data: space by folder, file type, storage class, size and age, and the largest files.
    """
    base = base_prefix(parse_s3_uri(s.uri)[1])
    largest = _files_table("largest", f"The {len(s.largest)} largest files", s.largest, base=base, prices=prices)
    tables = [*_summary_tables(s), largest]
    for table in tables:
        if s.truncated:
            table.notes.append("The scan stopped at limit=, so these numbers cover part of the prefix.")
    charts = [
        DataChart("Space by folder", "folders", "folder", ("share_of_size",), "bar",
                  "Each folder's part of the space, the biggest first.", "share of the space"),
        DataChart("Space by file type", "file_types", "extension", ("share_of_size",), "bar",
                  "Each file type's part of the space.", "share of the space"),
        DataChart("Monthly cost by storage class", "storage_classes", "storage_class", ("est_monthly_usd",), "bar",
                  "Storage only, at list prices.", "USD a month"),
        DataChart("Files by size", "size_bands", "band", ("files",), "column",
                  "A tall bar on the left is many small files.", "files"),
        DataChart("Files by age", "ages", "age", ("files",), "column", "How long ago the files last changed.",
                  "files"),
    ]
    return ReportData(f"Summary of {s.uri}", tables, charts, stem=_stem("summary", s.uri))


def duplicates_data(report: DuplicateReport) -> ReportData:
    """
    duplicates()' report as data: a row per file in a group of identical files, and a row per group.
    """
    base = base_prefix(parse_s3_uri(report.uri)[1])
    now = _utcnow()
    copies = []
    for number, group in enumerate(report.groups, 1):
        for index, obj in enumerate(group.objects):
            row = {"group": number, "role": "keep" if index == 0 else "copy",
                   **object_row(obj, base=base, now=now), "matched_by": group.matched_by, "sha256": group.sha256}
            row.pop("est_monthly_usd")
            copies.append(row)
    groups = [{"group": number, "size_mb": _megabytes(group.size), "files": len(group.objects),
               "copies": len(group.copies), "reclaimable_mb": _megabytes(group.reclaimable),
               "est_monthly_usd": group.monthly_cost, "matched_by": group.matched_by, "keep": group.keep.uri}
              for number, group in enumerate(report.groups, 1)]
    groups.sort(key=lambda row: -(row["reclaimable_mb"] or 0))
    copy_columns = [DataColumn("group", "which group of identical files it's in", "count"),
                    DataColumn("role", "keep (the oldest, the one to keep) or copy", tones=(("copy", "warn"),)),
                    *_file_columns(with_cost=False),
                    DataColumn("matched_by", "how it's known to match: ETag (the listing) or SHA-256 (its content)"),
                    DataColumn("sha256", "the SHA-256 of its content, when it was read", "id")]
    group_columns = [DataColumn("group", "the group's number", "count"),
                     DataColumn("size_mb", "the size of one file in it", "number"),
                     DataColumn("files", "identical files in it", "count"),
                     DataColumn("copies", "files beyond the one to keep", "count"),
                     DataColumn("reclaimable_mb", "what deleting the copies would free", "number", bar=True),
                     DataColumn("est_monthly_usd", "what storing the copies costs a month", "usd", bar=True),
                     DataColumn("matched_by", "how they're known to match"),
                     DataColumn("keep", "the file to keep", "id")]
    files = DataTable("copies", "Identical files", "a file in a group of identical files", copy_columns, copies,
                      notes=["Matched by ETag means S3's checksum agrees; a multipart upload's ETag can differ for "
                             "the same content, which duplicates(method='hash') catches."],
                      recipes=[Recipe("df[df.role == 'copy'].groupby('extension').size_mb.sum()",
                                      "megabytes of copies by file type")])
    by_group = DataTable("groups", "Groups of identical files", "a group", group_columns, groups,
                         notes=["The group that frees the most first."],
                         recipes=[Recipe("df.reclaimable_mb.sum()", "megabytes the copies take in all")])
    charts = [DataChart("What the copies take", "groups", "group", ("reclaimable_mb",), "bar",
                        "Megabytes each group's copies take: the longest bars free the most.", "MB")]
    return ReportData(f"Duplicates under {report.uri}", [by_group, files], charts, stem=_stem("duplicates", report.uri))


def versions_data(stats: VersionStats) -> ReportData:
    """
    versions()' report as data: the keys holding the most old versions.
    """
    bucket = parse_s3_uri(stats.uri)[0]
    rows = [{"key": key, "old_versions": stat.count, "size_bytes": stat.size, "size_mb": _megabytes(stat.size),
             "share_of_old_size": _fraction(stat.size, stats.noncurrent.size), "uri": s3_uri(bucket, key)}
            for key, stat in stats.top_noncurrent]
    columns = [DataColumn("key", "the file's key", "id"),
               DataColumn("old_versions", "versions of it that aren't the current one", "count"),
               DataColumn("size_bytes", "what those versions take", "bytes"),
               DataColumn("size_mb", "the same in megabytes", "number", bar=True),
               DataColumn("share_of_old_size", "its part of all the old versions' size", "share"),
               DataColumn("uri", "its s3:// path: what history() takes", "id")]
    table = DataTable("keys", "Keys with the most old versions", "a key", columns, rows,
                      notes=["Old versions are billed like the files they were: a NoncurrentVersionExpiration "
                             "lifecycle rule deletes them after some days."],
                      recipes=[Recipe("df.size_mb.sum()", "megabytes the listed keys' old versions take")])
    charts = [DataChart("Old versions by key", "keys", "key", ("size_mb",), "bar", "", "MB")]
    return ReportData(f"Versions under {stats.uri}", [table], charts, stem=_stem("versions", stats.uri))


def _version_row(version: ObjectVersion, now: datetime) -> dict[str, Any]:
    return {"last_modified": version.last_modified, "age_days": _age_days(version.last_modified, now),
            "size_bytes": version.size, "size_mb": _megabytes(version.size), "is_latest": version.is_latest,
            "is_delete_marker": version.is_delete_marker, "storage_class": version.storage_class,
            "version_id": version.version_id}


def history_data(uri: str, versions: list[ObjectVersion]) -> ReportData:
    """
    history()'s report as data: a row per version of one file, newest first.
    """
    now = _utcnow()
    columns = [DataColumn("last_modified", "when this version was written", "time"),
               DataColumn("age_days", "days since", "number"),
               DataColumn("size_bytes", "its size", "bytes"),
               DataColumn("size_mb", "its size in megabytes", "number", bar=True),
               DataColumn("is_latest", "whether it's the version you get when you read the file", "flag"),
               DataColumn("is_delete_marker", "a delete marker: the file was deleted then", "flag"),
               DataColumn("storage_class", "where S3 keeps this version"),
               DataColumn("version_id", "the ID to read or restore this version with", "id")]
    table = DataTable("versions", f"Every version of {uri}", "a version", columns,
                      [_version_row(version, now) for version in versions],
                      recipes=[Recipe("df[~df.is_latest].size_mb.sum()", "megabytes the old versions take")])
    return ReportData(f"History of {uri}", [table], stem=_stem("history", uri))


def deleted_data(found: DeletedFiles) -> ReportData:
    """
    deleted()'s report as data: a row per deleted file that versioning still holds.
    """
    bucket = parse_s3_uri(found.uri)[0]
    rows = []
    for item in found.files:
        last = item.last_version
        rows.append({"key": item.key, "deleted": item.deleted, "restorable": item.restorable,
                     "size_mb": _megabytes(last.size) if last else None,
                     "old_versions": item.old_versions.count, "old_versions_mb": _megabytes(item.old_versions.size),
                     "est_monthly_usd": item.monthly_cost, "marker_version_id": item.marker_version_id,
                     "uri": s3_uri(bucket, item.key)})
    columns = [DataColumn("key", "the deleted file's key", "id"),
               DataColumn("deleted", "when it was deleted", "time"),
               DataColumn("restorable", "whether a version is left to bring back", "flag"),
               DataColumn("size_mb", "the size of the version that would come back", "number"),
               DataColumn("old_versions", "versions still stored for it", "count"),
               DataColumn("old_versions_mb", "what they take", "number", bar=True),
               DataColumn("est_monthly_usd", "what keeping them costs a month", "usd",
                          read="A deleted file still costs money until its versions expire."),
               DataColumn("marker_version_id", "the delete marker to remove to restore it (deleted() shows the call)",
                          "id"),
               DataColumn("uri", "its s3:// path", "id")]
    table = DataTable("deleted", f"Deleted files under {found.uri}", "a deleted file", columns, rows,
                      notes=["Newest deletion first. Nothing here is changed: deleted() shows the call that "
                             "restores a file."],
                      recipes=[Recipe("df[df.restorable].set_index('deleted').size_mb.resample('D').count()",
                                      "files deleted per day")])
    return ReportData(f"Deleted files under {found.uri}", [table], stem=_stem("deleted", found.uri))


def _compare_row(status: str, key: str, a: ObjectInfo | None, b: ObjectInfo | None) -> dict[str, Any]:
    return {"status": status, "key": key, "size_a_bytes": a.size if a else None, "size_b_bytes": b.size if b else None,
            "modified_a": a.last_modified if a else None, "modified_b": b.last_modified if b else None}


def compare_data(result: CompareResult) -> ReportData:
    """
    compare()'s report as data: a row per key that differs between the two prefixes.
    """
    base_a = parse_s3_uri(result.uri_a)[1]
    base_b = parse_s3_uri(result.uri_b)[1]
    rows = [_compare_row("different", relative_key(a.key, base_a), a, b) for a, b in result.different]
    rows += [_compare_row("only in A", relative_key(a.key, base_a), a, None) for a in result.only_in_a]
    rows += [_compare_row("only in B", relative_key(b.key, base_b), None, b) for b in result.only_in_b]
    columns = [DataColumn("status", "different (on both sides, not the same), only in A or only in B",
                          tones=_STATUS_TONES),
               DataColumn("key", "the key below each prefix", "id"),
               DataColumn("size_a_bytes", f"its size under A ({result.uri_a})", "bytes"),
               DataColumn("size_b_bytes", f"its size under B ({result.uri_b})", "bytes"),
               DataColumn("modified_a", "when it last changed under A", "time"),
               DataColumn("modified_b", "when it last changed under B", "time")]
    table = DataTable("differences", "Keys that differ", "a key that differs", columns, rows,
                      notes=[f"{result.identical:,} keys are identical and aren't listed."],
                      recipes=[Recipe("df.groupby('status').size()", "how many of each")])
    return ReportData("Compare prefixes", [table], stem=_stem("compare", result.uri_a))


def _cell(value: Any) -> Any:
    """
    A file's value as a row keeps it: numbers, text, booleans and times as they are, anything else as text.
    """
    if value is None or isinstance(value, (bool, int, float, str, datetime)):
        return value
    return str(value)


_DTYPE_KINDS = {"i": ("number", "whole numbers"), "u": ("number", "whole numbers"), "f": ("number", "numbers"),
                "b": ("flag", "True or False"), "M": ("text", "dates and times, as the file has them")}


def _file_column(name: str, dtype_kind: str, declared: str | None) -> DataColumn:
    """
    A column of a previewed file: its kind from the type pandas read it as, and the type the file declares when it
    has a schema (Parquet, ORC, Avro...).
    """
    kind, words = _DTYPE_KINDS.get(dtype_kind, ("text", "text"))
    said = f"{declared} in the file" if declared else f"read as {words}"
    return DataColumn(name, f"a column of the file ({said})", kind)


def preview_data(preview: Preview) -> ReportData | None:
    """
    preview()'s report as data, for a table (csv, parquet, json lines, excel, ...): its first rows and its schema.
    None for any other kind of file.
    """
    if preview.kind != "table" or preview.data is None:
        return None
    frame = preview.data
    types = dict(preview.info.get("columns") or [])
    columns = [_file_column(str(name), frame[name].dtype.kind, types.get(str(name))) for name in frame.columns]
    rows = [{str(name): _cell(value) for name, value in record.items()}
            for record in frame.to_dict(orient="records")]
    reading = f"ui.core.read_df({preview.uri!r})"
    rows_table = DataTable(
        "rows", f"The first {len(rows):,} rows of {preview.uri}", "a row of the file", columns, rows,
        notes=[f"These are only the rows preview() read: {reading} reads the whole file as a DataFrame."],
        recipes=[Recipe(f"full = {reading}", "every row, not only the first ones"),
                 Recipe("df.describe(include='all')", "each column summed up")])
    schema = DataTable(
        "schema", "Its columns", "a column of the file",
        [DataColumn("column", "the column's name"), DataColumn("type", "its type, as the file says it")],
        [{"column": name, "type": kind} for name, kind in (preview.info.get("columns") or [])])
    tables = [rows_table] + ([schema] if schema.rows else [])
    return ReportData(f"Preview of {preview.uri}", tables, stem=_stem("preview", preview.uri))
