"""
The S3 explorer's old home, kept so `from aws_analyzer.s3_explorer import S3Explorer` still works. The explorer is
part of the s3 package now, in aws_analyzer.s3.explorer: `from aws_analyzer.s3 import S3Explorer`.
"""

from __future__ import annotations

from .s3.explorer import (
    Entry, Filter, Folder, FolderStats, S3Explorer, S3Navigator, breadcrumbs, count_types, entry_icon,
    explain_list_error, filter_entries, folder_stats, folder_uri, parent_uri, parse_filter, parse_location,
    sort_entries,
)

__all__ = [
    "Entry",
    "Filter",
    "Folder",
    "FolderStats",
    "S3Explorer",
    "S3Navigator",
    "breadcrumbs",
    "count_types",
    "entry_icon",
    "explain_list_error",
    "filter_entries",
    "folder_stats",
    "folder_uri",
    "parent_uri",
    "parse_filter",
    "parse_location",
    "sort_entries",
]
