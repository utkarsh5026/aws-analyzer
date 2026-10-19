"""
s3_explorer.py - browse S3 like a file explorer, inside a SageMaker / Jupyter notebook.

Folders and files are listed on the left. Click a folder to open it, or a file to see what's inside it on the
right: a table's first rows, a PDF's pages, a Word file with its pictures, an image, and so on. The toolbar has
back / forward / up buttons and a clickable path you can also type into, and the column headers sort by name, size
or date. Over the list, a search box finds files by name or type ('.csv', '.csv .json'), All / Folders / Files
show only one kind, a chip per file type filters with one click, and "Include subfolders" searches everything below
the folder. A big folder shows its first rows at once and lists the rest in the background (up to 10,000 entries),
so the search covers all of it; the list shows 100 rows a page, with « ‹ › » under it to move between pages.
"Read all" shows a whole PDF as it looks, 20 pages at a time (click a page to see it full size), "Text" shows its
words laid out to read (headings, paragraphs and lists, without the running headers and footers), and
"Download .zip" packs the folder you're in into one .zip, within limits that ⚙ (Settings) changes. Tick files (a
checkbox shows when you point at a row) and "Download selected" zips just those, under a name you can change.

It builds on s3.py, beside it in aws-analyzer (the previews, the formatting and the AWS calls all come from it):
``pip install aws-analyzer``, then ``from aws_analyzer import S3Explorer``. Clicking needs
ipywidgets, which SageMaker notebooks already have. Without it you get a plain listing and a note on what to
install. Like s3.py, it only reads from AWS; "Download" and "Download .zip" save a copy on the notebook's disk, in
s3.py's downloads folder (s3-downloads, next to the notebook), where x.ui.downloads() lists them and
x.ui.clean_downloads() deletes them.

Quick start
-----------
    from aws_analyzer import S3Explorer
    S3Explorer()                                    # start from your buckets
    S3Explorer("s3://my-bucket/data/")              # start in a folder (S3 console links work too)
    S3Explorer("s3://my-bucket/data/report.pdf")    # open the folder with that file shown
    S3Explorer("s3://my-bucket/", profile="dev")    # another AWS profile (or core=S3Analyzer(...))
    S3Explorer(zip_max_size="2GB")                  # zip folders up to 2 GB (100 MB unless ⚙ changes it)
    S3Explorer(downloads="~/s3")                    # save downloads and zips there, not in s3-downloads

    x = S3Explorer("s3://my-bucket/")
    x.open("s3://my-bucket/raw/")                   # drive it from code: open, back, forward, up, refresh
    x.filter(".parquet", subfolders=True)           # every Parquet file below this folder; x.filter() clears it
    x.ui.summary(x.location)                        # any S3View report about where you are, in its own cell

    nav = S3Navigator()                             # the same navigation as data, with no UI
    folder = nav.open("s3://my-bucket/data/")       # its first 1,000 entries; nav.list_rest() lists up to 10,000
    [entry.name for entry in folder.entries]
"""

from __future__ import annotations

from .navigator import (
    Entry, Filter, Folder, FolderStats, S3Navigator, breadcrumbs, count_types, entry_icon, explain_list_error,
    filter_entries, folder_stats, folder_uri, parent_uri, parse_filter, parse_location, sort_entries,
)
from .window import S3Explorer

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
