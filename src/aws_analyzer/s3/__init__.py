"""
s3.py - S3 analysis toolkit for SageMaker / Jupyter notebooks.

Part of aws-analyzer: ``pip install aws-analyzer``, then ``from aws_analyzer import S3View``.

Requirements: boto3 (required). pandas + pyarrow only for DataFrame / parquet
previews, IPython only for rich HTML output. All are preinstalled on SageMaker.

The file has two layers:

    S3Analyzer   Pure logic. Talks to AWS and returns plain Python data
                 (dataclasses, dicts, lists, DataFrames). Never prints.
    S3View       Notebook UI. Calls S3Analyzer and renders readable cards and
                 tables (HTML in Jupyter, plain text in a terminal).

Quick start
-----------
    ui = S3View()                                   # or S3View(S3Analyzer(profile="dev"))
    ui.help()                                       # list every command
    ui.buckets()                                    # all buckets + regions
    ui.bucket_info("my-bucket")                     # versioning, encryption, lifecycle, size
    ui.ls("s3://my-bucket/data/")                   # one level, like `aws s3 ls`
    ui.summary("s3://my-bucket/data/")              # full dashboard for a prefix
    ui.tree("s3://my-bucket/data/", depth=2)        # folder sizes as a tree
    ui.find("s3://my-bucket/data/", pattern="*.parquet", min_size="100MB")
    ui.preview("s3://my-bucket/data/part-0.csv.gz")
    ui.overview()                                   # every bucket: size, cost, security warnings
    ui.policy("my-bucket")                          # bucket policy in plain English
    ui.what_if("s3://my-bucket/logs/", move_after=30, to="STANDARD_IA")   # preview a lifecycle rule
    ui.deleted("s3://my-bucket/data/")              # deleted files you can still restore
    ui.duplicates("s3://my-bucket/data/")           # identical files (size, ETag, SHA-256) and what they cost
    ui.download("s3://my-bucket/data/")             # a file or folder into s3-downloads/, with progress
    ui.download_zip("s3://my-bucket/data/")         # the same as one .zip, once size / disk / access checks pass
    ui.downloads()                                  # what's downloaded, the disk it takes; clean_downloads() deletes it

    For clicking through folders and files instead (s3_explorer.py, which builds on this module):
    from aws_analyzer import S3Explorer; S3Explorer("s3://my-bucket/data/")

    s3 = ui.core                                    # same analyzer, raw data
    summary = s3.summarize("s3://my-bucket/data/")
    df = s3.read_df("s3://my-bucket/data/part-0.parquet", nrows=1000)
    files = objects_to_df(s3.find("s3://my-bucket/data/", extensions=["csv"]))
"""

from __future__ import annotations

from .._kit.fmt import Stat, file_extension, human_age, human_money, human_size, parse_size, parse_time
from .helpers import GB, KB, MB, TB, base_prefix, parse_s3_uri, relative_key, s3_uri
from .prices import (
    ARCHIVE_CLASSES, ARCHIVE_INDEX_BYTES, MIN_BILLABLE_SIZE, MIN_STORAGE_DAYS, MIN_TRANSITION_SIZE, S3_PRICES,
    S3_TRANSITION_PRICES, TRANSITION_ORDER,
)
from .models import (
    ArchiveEntry, ArchiveListing, BucketConfig, BucketInfo, BucketMetrics, BucketReport, CompareResult, DeletedFiles,
    DeletedObject, Document, DownloadsCleanup, DownloadsFolder, DuplicateFolder, DuplicateGroup, DuplicateReport,
    FileDetailsReport, FolderDownload, FolderTree, LifecycleImpact, Listing, LocalDownload, MultipartUpload,
    ObjectInfo, ObjectVersion, PdfLine, PdfPage, Picture, PolicyStatement, PrefixSummary, Preview, VersionStats,
    ZipDownload, objects_to_df,
)
from .readers.formats import detect_format, sniff_format
from .readers.avro import parse_avro
from .readers.office import office_kind, parse_docx, parse_pptx
from .readers.pdf import parse_pdf, render_pdf_pages
from .analysis.cost import cloudwatch_cost, object_monthly_cost, storage_type_class
from .analysis.summary import (
    AGE_BANDS, SIZE_BANDS, build_folder_tree, compare_objects, folder_of, make_filter, summarize_objects,
    summary_findings,
)
from .analysis.bucket import block_label, bucket_findings, encryption_label, public_exposure, rule_actions, rule_scope
from .analysis.duplicates import (
    DUPLICATE_METHODS, HASH_HEAD_BYTES, duplicate_findings, duplicate_folders, files_to_hash, find_duplicate_groups,
    group_duplicates,
)
from .analysis.downloads import DOWNLOADS, ZipPlan, downloads_findings, zip_checks, zip_findings
from .analysis.lifecycle import simulate_lifecycle_objects
from .analysis.policy import explain_policy, policy_findings
from .analysis.notifications import EventNotification, parse_notifications
from .analysis.documents import pdf_flow, pdf_furniture
from .analysis.details import FileDetails, describe_file, file_details_findings
from .analyzer.streams import DETAILS_BLOCK, DETAILS_FILE_MAX
from .analyzer.s3_analyzer import S3Analyzer
from .view.s3_view import S3View
from .explorer import S3Explorer, S3Navigator

__all__ = [
    "AGE_BANDS",
    "ARCHIVE_CLASSES",
    "ARCHIVE_INDEX_BYTES",
    "DETAILS_BLOCK",
    "DETAILS_FILE_MAX",
    "DOWNLOADS",
    "DUPLICATE_METHODS",
    "GB",
    "HASH_HEAD_BYTES",
    "KB",
    "MB",
    "MIN_BILLABLE_SIZE",
    "MIN_STORAGE_DAYS",
    "MIN_TRANSITION_SIZE",
    "S3_PRICES",
    "S3_TRANSITION_PRICES",
    "SIZE_BANDS",
    "TB",
    "TRANSITION_ORDER",
    "ArchiveEntry",
    "ArchiveListing",
    "BucketConfig",
    "BucketInfo",
    "BucketMetrics",
    "BucketReport",
    "CompareResult",
    "DeletedFiles",
    "DeletedObject",
    "Document",
    "DownloadsCleanup",
    "DownloadsFolder",
    "DuplicateFolder",
    "DuplicateGroup",
    "DuplicateReport",
    "EventNotification",
    "FileDetails",
    "FileDetailsReport",
    "FolderDownload",
    "FolderTree",
    "LifecycleImpact",
    "Listing",
    "LocalDownload",
    "MultipartUpload",
    "ObjectInfo",
    "ObjectVersion",
    "PdfLine",
    "PdfPage",
    "Picture",
    "PolicyStatement",
    "PrefixSummary",
    "Preview",
    "S3Analyzer",
    "S3Explorer",
    "S3Navigator",
    "S3View",
    "Stat",
    "VersionStats",
    "ZipDownload",
    "ZipPlan",
    "base_prefix",
    "block_label",
    "bucket_findings",
    "build_folder_tree",
    "cloudwatch_cost",
    "compare_objects",
    "describe_file",
    "detect_format",
    "downloads_findings",
    "duplicate_findings",
    "duplicate_folders",
    "encryption_label",
    "explain_policy",
    "file_details_findings",
    "file_extension",
    "files_to_hash",
    "find_duplicate_groups",
    "folder_of",
    "group_duplicates",
    "human_age",
    "human_money",
    "human_size",
    "make_filter",
    "object_monthly_cost",
    "objects_to_df",
    "office_kind",
    "parse_avro",
    "parse_docx",
    "parse_notifications",
    "parse_pdf",
    "parse_pptx",
    "parse_s3_uri",
    "parse_size",
    "parse_time",
    "pdf_flow",
    "pdf_furniture",
    "policy_findings",
    "public_exposure",
    "relative_key",
    "render_pdf_pages",
    "rule_actions",
    "rule_scope",
    "s3_uri",
    "simulate_lifecycle_objects",
    "sniff_format",
    "storage_type_class",
    "summarize_objects",
    "summary_findings",
    "zip_checks",
    "zip_findings",
]
