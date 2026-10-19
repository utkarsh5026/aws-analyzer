"""
The dataclasses S3Analyzer returns: objects and listings, buckets and their settings, summaries,
duplicates, versions, downloads, archives, documents and previews.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Iterable

from .._kit.deps import _require
from .._kit.fmt import Stat, file_extension, human_size

from .helpers import _count_words, parse_s3_uri, s3_uri

if TYPE_CHECKING:
    from .analysis.downloads import ZipPlan
    from .analysis.details import FileDetails


@dataclass
class ObjectInfo:
    """
    One object from a listing.
    """

    bucket: str
    key: str
    size: int
    last_modified: datetime
    storage_class: str = "STANDARD"
    etag: str = ""

    @property
    def uri(self) -> str:
        return s3_uri(self.bucket, self.key)

    @property
    def name(self) -> str:
        return self.key.rsplit("/", 1)[-1]

    @property
    def extension(self) -> str:
        return file_extension(self.key)

    @property
    def is_folder_marker(self) -> bool:
        return self.key.endswith("/") and self.size == 0

    @property
    def is_multipart(self) -> bool:
        return (
            "-" in self.etag
        )  # multipart ETags look like "<md5-of-md5s>-<part count>"


@dataclass
class BucketInfo:
    name: str
    created: datetime | None = None
    region: str | None = None


@dataclass
class Listing:
    """
    One level of a prefix, like `aws s3 ls`.
    """

    uri: str
    folders: list[str] = field(default_factory=list)  # full prefixes, ending in '/'
    objects: list[ObjectInfo] = field(default_factory=list)
    truncated: bool = False


@dataclass
class BucketConfig:
    """
    Bucket settings. Sections that couldn't be read are listed in `errors` (section -> error code).
    """

    name: str
    region: str | None = None
    versioning: str | None = None  # 'Enabled' | 'Suspended' | 'Disabled'
    mfa_delete: str | None = None
    encryption: str | None = None  # 'AES256' | 'aws:kms' | 'aws:kms:dsse' | None
    kms_key: str | None = None
    bucket_key_enabled: bool | None = None
    public_access_block: dict[str, bool] | None = (
        None  # None = not configured on the bucket
    )
    has_policy: bool | None = None
    policy_is_public: bool | None = None
    policy: dict | None = None  # the bucket policy document (see explain_policy)
    object_ownership: str | None = None
    object_lock: bool | None = None
    lifecycle_rules: list[dict] = field(default_factory=list)
    replication_rules: list[dict] = field(default_factory=list)
    logging_target: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
    inventory_configs: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


@dataclass
class BucketMetrics:
    """
    Daily CloudWatch storage metrics: bucket totals without listing a single key.
    """

    bucket: str
    object_count: int | None = None
    size_by_storage_type: dict[str, int] = field(default_factory=dict)
    as_of: datetime | None = None

    @property
    def total_size(self) -> int:
        return sum(self.size_by_storage_type.values())


@dataclass
class PrefixSummary:
    """
    Everything `summarize` learns from one pass over a prefix. Folder markers are excluded.
    """

    uri: str
    object_count: int = 0
    total_size: int = 0
    min_size: int | None = None
    max_size: int = 0
    empty_count: int = 0
    folder_markers: int = 0
    oldest: ObjectInfo | None = None
    newest: ObjectInfo | None = None
    by_extension: dict[str, Stat] = field(default_factory=dict)
    by_storage_class: dict[str, Stat] = field(default_factory=dict)
    by_folder: dict[str, Stat] = field(
        default_factory=dict
    )  # '' = files directly under the prefix
    size_histogram: dict[str, Stat] = field(default_factory=dict)
    age_histogram: dict[str, Stat] = field(default_factory=dict)
    cold_standard: Stat = field(
        default_factory=Stat
    )  # STANDARD objects unchanged for 90+ days
    # Estimated USD per month per storage class (see object_monthly_cost); None = no price for that class
    cost_by_storage_class: dict[str, float | None] = field(default_factory=dict)
    below_minimum: dict[str, Stat] = field(
        default_factory=dict
    )  # objects billed as 128 KB, per class
    largest: list[ObjectInfo] = field(default_factory=list)
    truncated: bool = False
    scan_seconds: float = 0.0

    @property
    def avg_size(self) -> float:
        return self.total_size / self.object_count if self.object_count else 0.0

    @property
    def monthly_cost(self) -> float:
        return sum(cost for cost in self.cost_by_storage_class.values() if cost)


@dataclass
class FolderTree:
    """
    Size per folder, every level down to `depth`. Keys are folder paths relative to the prefix.
    """

    uri: str
    depth: int
    folders: dict[str, Stat] = field(
        default_factory=dict
    )  # '' = files directly under the prefix
    total: Stat = field(default_factory=Stat)
    truncated: bool = False
    # files directly in each folder shown (first `max_files` by key), and all of them counted
    files: dict[str, list[ObjectInfo]] = field(default_factory=dict)
    direct: dict[str, Stat] = field(default_factory=dict)
    max_files: int = 0


@dataclass
class CompareResult:
    """
    Two prefixes compared by relative key, size and ETag.
    """

    uri_a: str
    uri_b: str
    only_in_a: list[ObjectInfo] = field(default_factory=list)
    only_in_b: list[ObjectInfo] = field(default_factory=list)
    different: list[tuple[ObjectInfo, ObjectInfo]] = field(default_factory=list)
    identical: int = 0
    unverifiable: int = 0  # same size, but multipart ETags can't prove equal content

    @property
    def in_sync(self) -> bool:
        return not (self.only_in_a or self.only_in_b or self.different)


@dataclass
class DuplicateGroup:
    """
    Files with identical content. The first is the one to keep (see group_duplicates); the rest are copies.
    """

    size: int
    objects: list[ObjectInfo]
    sha256: str | None = None  # hex SHA-256 of the content, when it was read
    # How the files are known to match: 'ETag' (from the listing), 'SHA-256' (every file's content hashed) or
    # 'SHA-256 + ETag' (some by each, e.g. one file per ETag hashed)
    matched_by: str = "ETag"
    monthly_cost: float = 0.0  # estimated USD per month to store the copies

    @property
    def keep(self) -> ObjectInfo:
        return self.objects[0]

    @property
    def copies(self) -> list[ObjectInfo]:
        return self.objects[1:]

    @property
    def reclaimable(self) -> int:
        return self.size * (len(self.objects) - 1)


@dataclass
class DuplicateReport:
    """
    Identical files under a prefix (see S3Analyzer.find_duplicates and group_duplicates).
    """

    uri: str
    method: str = "etag"  # 'etag' (listing only), 'hash' or 'strict' (see S3Analyzer.find_duplicates)
    groups: list[DuplicateGroup] = field(
        default_factory=list
    )  # biggest reclaimable size first
    scanned: Stat = field(default_factory=Stat)  # files listed, folder markers excluded
    candidates: Stat = field(
        default_factory=Stat
    )  # files of at least min_size sharing their size with another
    not_compared: Stat = field(
        default_factory=Stat
    )  # candidates whose ETag differs and whose content wasn't read
    files_by_folder: dict[str, int] = field(
        default_factory=dict
    )  # folder -> files of at least min_size in it
    unreadable: dict[str, str] = field(
        default_factory=dict
    )  # key -> why it couldn't be read
    files_read: int = 0  # files read to hash (the first 64 KB or all of it)
    bytes_read: int = 0
    requests: int = 0  # GET requests made to read them
    max_read: int | None = None  # the read budget, in bytes (None = no cap)
    read_capped: bool = False  # some files weren't read because of max_read
    truncated: bool = False  # the listing stopped at `limit`
    versioning: str | None = (
        None  # the bucket's versioning status, when it could be read
    )
    scan_seconds: float = 0.0

    @property
    def copies(self) -> int:
        return sum(len(g.objects) - 1 for g in self.groups)

    @property
    def reclaimable(self) -> int:
        return sum(g.reclaimable for g in self.groups)

    @property
    def monthly_cost(self) -> float:
        return sum(g.monthly_cost for g in self.groups)

    def to_df(self):
        """
        One row per file in a duplicate group: group number, 'keep' or 'copy', key, size, sha256, ...
        """
        pd = _require("pandas", "DuplicateReport.to_df")
        columns = [
            "group",
            "role",
            "key",
            "size",
            "last_modified",
            "storage_class",
            "matched_by",
            "sha256",
            "etag",
            "uri",
        ]
        return pd.DataFrame(
            [
                [
                    n,
                    "keep" if i == 0 else "copy",
                    o.key,
                    o.size,
                    o.last_modified,
                    o.storage_class,
                    g.matched_by,
                    g.sha256,
                    o.etag,
                    o.uri,
                ]
                for n, g in enumerate(self.groups, 1)
                for i, o in enumerate(g.objects)
            ],
            columns=columns,
        )


@dataclass
class DuplicateFolder:
    """
    A folder holding files that have an identical file somewhere (see duplicate_folders).
    """

    folder: str  # full prefix of the folder, '' = the bucket's top level
    files: int  # files of at least min_size in the folder
    duplicated: Stat = field(
        default_factory=Stat
    )  # its files that have an identical file anywhere
    outside: int = 0  # its files that have an identical file in another folder
    elsewhere: dict[str, int] = field(
        default_factory=dict
    )  # folder holding copies -> files; itself = same folder

    @property
    def all_copies(self) -> bool:
        """
        Every file here also exists in another folder, so the folder could go without losing data.
        """
        return 0 < self.files == self.outside


@dataclass
class VersionStats:
    uri: str
    current: Stat = field(default_factory=Stat)
    noncurrent: Stat = field(default_factory=Stat)
    delete_markers: int = 0
    deleted_keys: int = (
        0  # keys whose latest version is a delete marker (old versions still billed)
    )
    noncurrent_cost: float = 0.0  # estimated USD per month for the noncurrent versions
    top_noncurrent: list[tuple[str, Stat]] = field(default_factory=list)
    truncated: bool = False


@dataclass
class ObjectVersion:
    key: str
    version_id: str
    is_latest: bool
    last_modified: datetime
    size: int = 0
    is_delete_marker: bool = False
    storage_class: str | None = None


@dataclass
class MultipartUpload:
    bucket: str
    key: str
    upload_id: str
    initiated: datetime
    storage_class: str = "STANDARD"
    parts: int | None = None  # filled when with_sizes=True
    size: int | None = None


@dataclass
class DeletedObject:
    """
    A key whose latest version is a delete marker. Deleting that marker brings `last_version` back.
    """

    key: str
    deleted: datetime
    marker_version_id: str
    last_version: ObjectVersion | None = (
        None  # newest real version; None = nothing left to restore
    )
    old_versions: Stat = field(
        default_factory=Stat
    )  # every version still stored for this key
    monthly_cost: float = 0.0  # estimated USD per month for those versions

    @property
    def restorable(self) -> bool:
        return self.last_version is not None


@dataclass
class DeletedFiles:
    uri: str
    files: list[DeletedObject] = field(
        default_factory=list
    )  # most recently deleted first
    truncated: bool = False


@dataclass
class LifecycleImpact:
    """
    What a lifecycle rule would do to a prefix if it ran today (see simulate_lifecycle_objects).
    """

    uri: str
    transitions: list[tuple[int, str]] = field(
        default_factory=list
    )  # (days, storage class), ascending
    expire_days: int | None = None
    scanned: Stat = field(default_factory=Stat)
    moves: dict[str, Stat] = field(
        default_factory=dict
    )  # target storage class -> objects moved there
    expired: Stat = field(default_factory=Stat)
    too_small: Stat = field(
        default_factory=Stat
    )  # old enough to move, but under S3's 128 KB transition minimum
    early_removals: Stat = field(
        default_factory=Stat
    )  # deleted or moved before their class's minimum storage duration
    cost_before: float = 0.0  # USD per month for the scanned objects today
    cost_after: float = 0.0
    one_time_cost: float = 0.0  # transition requests + early-deletion charges
    truncated: bool = False

    @property
    def monthly_savings(self) -> float:
        return self.cost_before - self.cost_after

    def describe(self) -> str:
        steps = [f"move to {cls} after {days} days" for days, cls in self.transitions]
        if self.expire_days is not None:
            steps.append(f"delete after {self.expire_days} days")
        return ", then ".join(steps)

    def rule(self, rule_id: str | None = None) -> dict:
        """
        The rule in the format put_bucket_lifecycle_configuration takes.
        """
        prefix = parse_s3_uri(self.uri)[1] if self.uri else ""
        rule: dict[str, Any] = {
            "ID": rule_id or f"{prefix.strip('/') or 'whole-bucket'}-lifecycle"[:255],
            "Status": "Enabled",
            "Filter": {"Prefix": prefix},
        }
        if self.transitions:
            rule["Transitions"] = [
                {"Days": days, "StorageClass": cls} for days, cls in self.transitions
            ]
        if self.expire_days is not None:
            rule["Expiration"] = {"Days": self.expire_days}
        return rule


@dataclass
class PolicyStatement:
    """
    One bucket-policy statement in plain English (see explain_policy).
    """

    sid: str
    effect: str  # 'Allow' | 'Deny'
    who: list[str]
    actions: list[str]
    resources: list[str]
    conditions: list[str]
    anyone: bool = False  # the principal is '*' (or NotPrincipal): everyone, including anonymous users
    restricted: bool = False  # a condition limits callers to a VPC, IP range, organization, account or ARN
    writes: bool = False  # can change or delete data or settings (not just read / list)
    other_accounts: list[str] = field(
        default_factory=list
    )  # account ids other than yours

    @property
    def public(self) -> bool:
        return self.effect == "Allow" and self.anyone and not self.restricted


@dataclass
class BucketReport:
    """
    One bucket in the all-buckets overview.
    """

    bucket: BucketInfo
    config: BucketConfig
    metrics: BucketMetrics | None = None
    metrics_error: str | None = None


@dataclass
class FolderDownload:
    """
    What S3Analyzer.download_folder fetched.
    """

    uri: str
    path: str  # the local folder
    downloaded: Stat = field(default_factory=Stat)
    already_there: Stat = field(
        default_factory=Stat
    )  # same size and time as the local file: not fetched again
    skipped: dict[str, str] = field(
        default_factory=dict
    )  # key -> why it wasn't downloaded
    truncated: bool = False  # the listing stopped at `limit`
    seconds: float = 0.0


@dataclass
class ZipDownload:
    """
    What S3Analyzer.download_zip did: the checks it ran (plan) and the zip it wrote.
    """

    plan: ZipPlan
    written: bool = False  # False: a check failed, or dry_run=True
    zip_size: int = 0
    files: Stat = field(default_factory=Stat)  # files in the zip
    failed: dict[str, str] = field(
        default_factory=dict
    )  # key -> error, files that couldn't be read while zipping
    seconds: float = 0.0


@dataclass
class LocalDownload:
    """
    A file, folder or zip in the downloads folder (see S3Analyzer.list_downloads).
    """

    name: str  # its name in the downloads folder
    path: str  # where it is (absolute)
    kind: str  # 'file' | 'folder' | 'zip' | 'unfinished' (a .zip.part a stopped download_zip left)
    size: int  # bytes, with everything below a folder
    files: int  # 1 for a file
    downloaded: datetime  # when it was last written here (UTC); file times are S3's, so this is the change time

    @property
    def is_folder(self) -> bool:
        return self.kind == "folder"


@dataclass
class DownloadsFolder:
    """
    What download() and download_zip() saved: the downloads folder and what's in it (S3Analyzer.list_downloads).
    """

    path: str  # the folder (absolute)
    exists: bool = False
    managed: bool = False  # made for downloads (it has aws-analyzer's .gitignore): clean_downloads may empty it
    protected: str = ""  # why it must never be emptied ('the notebook's folder', ...), '' when nothing stops it
    entries: list[LocalDownload] = field(default_factory=list)  # newest first
    disk_free: int | None = None  # bytes free on its disk
    errors: dict[str, str] = field(default_factory=dict)  # name -> why it couldn't be read

    @property
    def size(self) -> int:
        return sum(entry.size for entry in self.entries)

    @property
    def files(self) -> int:
        return sum(entry.files for entry in self.entries)

    def to_df(self):
        """
        One row per download: name, kind, size, files, downloaded, path.
        """
        pd = _require("pandas", "DownloadsFolder.to_df")
        return pd.DataFrame(
            [vars(entry) for entry in self.entries],
            columns=["name", "kind", "size", "files", "downloaded", "path"],
        )


@dataclass
class DownloadsCleanup:
    """
    What S3Analyzer.clean_downloads deleted, or would delete with dry_run=True.
    """

    path: str  # the downloads folder
    removed: list[LocalDownload] = field(default_factory=list)  # deleted (or to delete), newest first
    kept: list[LocalDownload] = field(default_factory=list)  # left: not named, or newer than older_than
    failed: dict[str, str] = field(default_factory=dict)  # name -> why it couldn't be deleted (it stays)
    dry_run: bool = False
    older_than: datetime | None = None  # only what was downloaded before this went

    @property
    def freed(self) -> int:
        """
        Bytes deleted (with dry_run, that would be).
        """
        return sum(entry.size for entry in self.removed if entry.name not in self.failed)


@dataclass
class ArchiveEntry:
    name: str
    size: int
    modified: datetime | None = None
    is_dir: bool = False


@dataclass
class ArchiveListing:
    """
    Files inside a zip / tar archive (see S3Analyzer.list_archive).
    """

    uri: str
    kind: str  # 'zip' | 'tar'
    entries: list[ArchiveEntry] = field(default_factory=list)
    total_files: int | None = None  # None when the listing stopped early
    complete: bool = True
    bytes_read: int | None = (
        None  # compressed tar: how much of the archive was streamed
    )


@dataclass
class Picture:
    """
    A picture from a document: a PDF page as it prints, or an image inside a Word file. It shows itself
    when it's the last thing in a notebook cell; .data holds the image file's bytes.
    """

    data: bytes = field(repr=False)
    mime: str  # 'image/png', 'image/jpeg', ...; '' when browsers can't show the format (EMF, WMF, TIFF)
    label: str  # 'Page 3', or the picture's file name inside the .docx ('image2.png')
    page: int | None = None  # PDF: the page number
    part: int | None = None  # Word: how many of Document.parts come before it, i.e. where it sits in the text
    alt: str = ""  # Word: the picture's description (alt text), when it has one

    def _repr_mimebundle_(self, include: Any = None, exclude: Any = None) -> dict[str, str]:
        text = f"<{self.label}: {human_size(len(self.data))} {self.mime or 'picture'}>"
        return {self.mime: base64.b64encode(self.data).decode(), "text/plain": text} if self.mime else {"text/plain": text}


@dataclass
class PdfLine:
    """
    One line of a PDF page as it prints: its text, where it starts (points from the page's left and top
    edges; None when pypdf couldn't place it), its font size in points and whether its font is bold.
    """

    text: str
    x: float | None = None
    y: float | None = None
    size: float = 0.0
    bold: bool = False


@dataclass
class PdfPage:
    """
    A PDF page's lines in the order pypdf reads them, and the page's size in points (read_pdf(..., layout=True));
    pdf_flow lays them out to read.
    """

    number: int
    width: float = 0.0
    height: float = 0.0
    lines: list[PdfLine] = field(default_factory=list)


@dataclass
class Document:
    """
    Text of a PDF, Word (.docx) or PowerPoint (.pptx) file (see S3Analyzer.read_document).
    """

    uri: str
    kind: str  # 'pdf' | 'docx' | 'pptx'
    parts: list[str] = field(
        default_factory=list
    )  # pages / slides / Word paragraphs and tables, in order
    numbers: list[int] = field(
        default_factory=list
    )  # page or slide number of each part (PDF, PPTX)
    title: str | None = None
    author: str | None = None
    page_count: int | None = (
        None  # PDF pages, PPTX slides; for a DOCX, the count Word last saved (can be stale)
    )
    headings: list[tuple[int, str]] = field(
        default_factory=list
    )  # DOCX outline: (level, text), 0 = title
    slide_titles: list[str | None] = field(default_factory=list)  # PPTX, one per part
    notes: list[str] = field(default_factory=list)  # PPTX speaker notes, one per part
    tables: list[list[list[str]]] = field(
        default_factory=list
    )  # DOCX / PPTX tables: rows of cell texts
    roles: list[str] = field(default_factory=list)  # DOCX, per part: 'title', 'h1'.., 'li', 'table' or 'p'
    pictures: list[Picture] = field(default_factory=list)  # PDF pages drawn, or a DOCX's pictures in order
    errors: dict[str, str] = field(default_factory=dict)  # what couldn't be read, and why: {'pictures': ...}
    layout: list[PdfPage] = field(default_factory=list)  # PDF read with layout=True: each page's lines (pdf_flow)

    @property
    def text(self) -> str:
        return "\n\n".join(part for part in self.parts if part)

    @property
    def word_count(self) -> int:
        return _count_words(self.text)


@dataclass
class Preview:
    """
    First look at an object. kind: 'table' (DataFrame), 'json' (the parsed value; info['cut'] when it's
    only the start of a bigger file: how many objects and arrays were cut off), 'text' (list of lines),
    'listing' (list of dicts: archive members, notebook cells, tensors, arrays), 'image' (bytes),
    'document' (text; the parsed Document in .document, with a PDF's first pages drawn and a Word file's
    pictures), 'media' (presigned URL for audio / video / PDF), 'binary' (bytes) or 'unavailable'.
    """

    uri: str
    kind: str
    size: int
    format: str | None = None
    compression: str | None = None
    content_type: str | None = None
    data: Any = None
    truncated: bool = False
    info: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    document: Document | None = None


@dataclass
class FileDetailsReport:
    """
    What's inside each file under a prefix (see S3Analyzer.file_details), and what reading it took.
    """

    uri: str
    files: list[FileDetails] = field(default_factory=list)  # in key order
    filters: dict[str, Any] = field(default_factory=dict)  # pattern / extensions it was called with
    limit: int | None = None
    truncated: bool = False  # more files matched than `limit`
    max_read: int | None = None
    read_capped: bool = False  # reading stopped at max_read: some files are skipped='max_read'
    bytes_read: int = 0
    requests: int = 0
    seconds: float = 0.0

    def to_df(self):
        """
        One row per file: key, format, size, summary, status, then a column per fact (pages, rows, ...).
        """
        pd = _require("pandas", "FileDetailsReport.to_df")
        keys: dict[str, None] = {}
        for d in self.files:
            keys.update(dict.fromkeys(d.facts))
        rows = [
            {
                "key": d.key,
                "format": d.format,
                "size": d.size,
                "summary": d.summary,
                "status": "skipped" if d.skipped else "error" if d.error else "ok",
                "problem": d.skipped or d.error or None,
                **{k: d.facts.get(k) for k in keys},
                "uri": d.uri,
            }
            for d in self.files
        ]
        return pd.DataFrame(rows, columns=["key", "format", "size", "summary", "status", "problem", *keys, "uri"])


def objects_to_df(objects: Iterable[ObjectInfo]):
    """
    List of ObjectInfo -> pandas DataFrame (key, size, last_modified, storage_class, ...).
    """
    pd = _require("pandas", "objects_to_df")
    columns = [
        "key",
        "size",
        "last_modified",
        "storage_class",
        "extension",
        "etag",
        "uri",
    ]
    return pd.DataFrame(
        [
            [
                o.key,
                o.size,
                o.last_modified,
                o.storage_class,
                o.extension,
                o.etag,
                o.uri,
            ]
            for o in objects
        ],
        columns=columns,
    )
