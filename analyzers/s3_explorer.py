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

This file builds on s3.py (the previews, the formatting and the AWS calls all come from it): upload both files
next to your notebook, or paste s3.py into a cell and then this file into the next one. Clicking needs
ipywidgets, which SageMaker notebooks already have. Without it you get a plain listing and a note on what to
install. Like s3.py, it only reads from AWS; "Download" and "Download .zip" save a copy on the notebook's disk.

Quick start
-----------
    from s3_explorer import S3Explorer
    S3Explorer()                                    # start from your buckets
    S3Explorer("s3://my-bucket/data/")              # start in a folder (S3 console links work too)
    S3Explorer("s3://my-bucket/data/report.pdf")    # open the folder with that file shown
    S3Explorer("s3://my-bucket/", profile="dev")    # another AWS profile (or core=S3Analyzer(...))
    S3Explorer(zip_max_size="2GB")                  # zip folders up to 2 GB (100 MB unless ⚙ changes it)

    x = S3Explorer("s3://my-bucket/")
    x.open("s3://my-bucket/raw/")                   # drive it from code: open, back, forward, up, refresh
    x.filter(".parquet", subfolders=True)           # every Parquet file below this folder; x.filter() clears it
    x.ui.summary(x.location)                        # any S3View report about where you are, in its own cell

    nav = S3Navigator()                             # the same navigation as data, with no UI
    folder = nav.open("s3://my-bucket/data/")       # its first 1,000 entries; nav.list_rest() lists up to 10,000
    [entry.name for entry in folder.entries]
"""

from __future__ import annotations

import asyncio
import dataclasses
import fnmatch
import functools
import html
import importlib
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import parse_qs, quote, unquote, urlparse

from botocore.exceptions import BotoCoreError, ClientError

# =============================================================================
# 1. Helpers
# =============================================================================


def _s3_module(core: Any = None) -> Any:
    """The s3.py this explorer builds on: the module `core` came from, else the s3 next to this file when both
    were installed with pip (aws_analyzer.s3), else `import s3`, else the notebook itself when s3.py was pasted
    into a cell. Raises ImportError that says how to get it."""
    if core is not None:
        module = sys.modules.get(type(core).__module__)
        if module is not None and hasattr(module, "S3View"):
            return module
    if __package__:
        try:
            return importlib.import_module(f"{__package__}.s3")
        except ImportError:
            pass
    try:
        import s3

        if hasattr(s3, "S3View"):
            return s3
    except ImportError:
        pass
    main = sys.modules.get("__main__")
    if main is not None and all(hasattr(main, name) for name in ("S3Analyzer", "S3View", "_render_html")):
        return main
    raise ImportError(
        "s3_explorer.py builds on s3.py, which isn't here. Upload s3.py next to this notebook "
        "(or paste it into a cell above this one), then run this again."
    )


_SCHEMES = ("s3://", "s3a://", "s3n://")
_COMPRESSION = {"gz", "gzip", "bz2", "xz", "zst", "zstd", "snappy", "lz4", "z"}
_ARCHIVED = {"GLACIER", "DEEP_ARCHIVE"}  # storage classes that need a restore before a file can be read
_DIGITS = re.compile(r"(\d+)")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_SAME_TYPE = {"jpeg": "jpg", "yml": "yaml", "tif": "tiff", "htm": "html"}  # two spellings of one file type
_TYPE_WORD = re.compile(r"^(?:\*?\.|(?:ext|type):\.?)(\w[\w+-]*(?:\.[\w+-]+)*)$")  # '.csv', '*.csv', 'ext:csv'
_KINDS = {"all": "all", "folders": "folders", "folder": "folders", "files": "files", "file": "files"}
_CACHED_ENTRIES = 100_000  # S3Navigator forgets the folders it opened longest ago past this many entries (~50 MB)


def _natural(name: str) -> list[Any]:
    """Sort key that orders 'part-2' before 'part-10'."""
    return [int(piece) if piece.isdigit() else piece.lower() for piece in _DIGITS.split(name)]


def _extension(name: str) -> str:
    """'data.csv.gz' -> 'csv.gz', 'part-0.snappy.parquet' -> 'parquet', 'README' -> '' (like s3.file_extension)."""
    parts = name.lower().split(".")
    if len(parts) < 2 or not parts[-1] or (len(parts) == 2 and not parts[0]):
        return ""
    if parts[-1] in _COMPRESSION and len(parts) > 2 and parts[-2].isalpha() and len(parts[-2]) <= 8:
        return f"{parts[-2]}.{parts[-1]}"
    return parts[-1]


def _file_type(name: str) -> str:
    """The type a file is counted under: 'a.csv.gz' -> 'csv', 'x.JPEG' -> 'jpg', 'logs.tar.gz' -> 'tar', 'README' -> ''."""
    found = _extension(name).split(".")[0]
    return _SAME_TYPE.get(found, found)


def _types_of(name: str) -> set[str]:
    """Every type a file name answers to in a search: 'a.csv.gz' -> {'csv', 'gz', 'csv.gz'}, 'x.jpeg' -> {'jpg'}."""
    parts = name.lower().split(".")
    found = {_SAME_TYPE.get(t, t) for t in (".".join(parts[i:]) for i in range(1, len(parts)) if all(parts[i:]))}
    return found | {_file_type(name)} - {""}


def _type_word(word: str) -> str:
    """The file type a word of a filter asks for: '.csv', '*.csv' or 'ext:csv' -> 'csv', '.jpeg' -> 'jpg'; '' when the
    word isn't one."""
    match = _TYPE_WORD.match(word.lower())
    return _SAME_TYPE.get(match.group(1), match.group(1)) if match else ""


def _parent_prefix(key: str) -> str:
    """The folder part of a key: 'a/b/c.csv' -> 'a/b/', 'c.csv' -> '', '/c.csv' -> '/'."""
    head, sep, _ = key.rpartition("/")
    return head + sep


def _plural(count: int, word: str) -> str:
    return f"{count:,} {word}{'' if count == 1 else 's'}"


def _fmt_dt(moment: datetime | None) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if moment else ""


# =============================================================================
# 2. Data models
# =============================================================================


@dataclass
class Entry:
    """One row of the explorer: a bucket, a folder or a file."""

    kind: str  # 'bucket' | 'folder' | 'file'
    bucket: str
    key: str = ""  # the full key; a folder's ends in '/', a bucket's is ''
    size: int | None = None
    modified: datetime | None = None  # a bucket's creation date
    storage_class: str = ""
    etag: str = ""

    @property
    def uri(self) -> str:
        return f"s3://{self.bucket}/{self.key}"

    @property
    def is_folder(self) -> bool:
        """Buckets and folders open; files show what's inside them."""
        return self.kind != "file"

    @property
    def name(self) -> str:
        """What the explorer shows: 'events' for s3://b/raw/events/, 'a.json' for a file, the bucket's name."""
        if self.kind == "bucket":
            return self.bucket
        key = self.key[:-1] if self.kind == "folder" else self.key
        return key.rsplit("/", 1)[-1]

    @property
    def archived(self) -> bool:
        return self.storage_class in _ARCHIVED


@dataclass
class Folder:
    """One level of a bucket, or every bucket when `uri` is ''. Entries come in S3's order, a page at a time.
    A deep folder (S3Navigator.below) holds everything below `uri` instead: every file, and the folders between."""

    uri: str
    entries: list[Entry] = field(default_factory=list)
    more: bool = False  # S3 has more entries than these; S3Navigator.more() lists the next page
    token: str | None = None  # where the next page starts
    requests: int = 0  # list requests made for this folder so far
    error: str = ""  # why it couldn't be listed ('AccessDenied: ...'); entries is empty then
    error_code: str = ""
    deep: bool = False  # everything below uri, not only its first level
    listed: int = 0  # keys and folders S3 has returned for it so far, in its order (lookup() doesn't count)


@dataclass
class Filter:
    """What a filter box's text asks for (see parse_filter): words a name must contain, patterns it must match, and
    file types it must have one of."""

    words: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)  # with * ? or [, matched against the whole name
    types: list[str] = field(default_factory=list)  # 'csv', 'csv.gz', ...

    def __bool__(self) -> bool:
        return bool(self.words or self.patterns or self.types)

    def named(self, entry: Entry) -> bool:
        """The name has every word in it and matches every pattern (any case)."""
        name = entry.name.lower()
        return all(word in name for word in self.words) and all(fnmatch.fnmatchcase(name, p) for p in self.patterns)

    def typed(self, entry: Entry) -> bool:
        """It's a file of one of the types (anything is, when no type was asked for; a folder never is)."""
        return not self.types or (not entry.is_folder and not _types_of(entry.name).isdisjoint(self.types))

    def matches(self, entry: Entry) -> bool:
        return self.named(entry) and self.typed(entry)


@dataclass
class FolderStats:
    """What the loaded entries of a folder add up to. Sizes cover the files at this level, not sub-folders."""

    folders: int = 0
    files: int = 0
    size: int = 0
    newest: datetime | None = None
    oldest: datetime | None = None
    archived: int = 0  # files in GLACIER / DEEP_ARCHIVE
    types: list[tuple[str, int, int]] = field(default_factory=list)  # (extension, files, bytes), biggest first


# =============================================================================
# 3. Pure analysis (no AWS calls)
# =============================================================================


def parse_location(text: str | None) -> tuple[str, str]:
    """Where a pasted path points -> (bucket, key); ('', '') means every bucket.

    Takes 's3://bucket/key', 'bucket/key', S3 console links (…/s3/buckets/bucket?prefix=key,
    …/s3/object/bucket?prefix=key) and object URLs (https://bucket.s3.region.amazonaws.com/key,
    https://s3.region.amazonaws.com/bucket/key)."""
    text = (text or "").strip()
    lower = text.lower()
    if lower.startswith(("http://", "https://")):
        url = urlparse(text)
        host, path = url.netloc.lower(), unquote(url.path)
        if "console.aws.amazon.com" in host:
            match = re.search(r"/s3/(?:buckets|object)/([^/?#]+)", path)
            prefix = parse_qs(url.query).get("prefix", [""])[0]
            return (match.group(1), prefix) if match else ("", "")
        if ".s3." in host or host.startswith("s3.") or ".s3-" in host or host.startswith("s3-"):
            if host.startswith(("s3.", "s3-")):  # path style: s3.region.amazonaws.com/bucket/key
                bucket, _, key = path.lstrip("/").partition("/")
                return bucket, key
            return host.split(".s3", 1)[0], path.lstrip("/")  # virtual-hosted: bucket.s3.region.amazonaws.com
    for scheme in _SCHEMES:
        if lower.startswith(scheme):
            text = text[len(scheme):]
            break
    bucket, _, key = text.lstrip("/").partition("/")
    return bucket, key


def folder_uri(bucket: str, prefix: str = "") -> str:
    """'' for no bucket (every bucket), else 's3://bucket/prefix' with prefix ending in '/'."""
    if not bucket:
        return ""
    return f"s3://{bucket}/{prefix if not prefix or prefix.endswith('/') else prefix + '/'}"


def parent_uri(uri: str) -> str:
    """The folder above: 's3://b/x/y/' -> 's3://b/x/', 's3://b/' -> '' (every bucket), '' -> ''."""
    bucket, key = parse_location(uri)
    if not bucket or not key:
        return ""
    return folder_uri(bucket, _parent_prefix(key[:-1] if key.endswith("/") else key))


def breadcrumbs(uri: str) -> list[tuple[str, str]]:
    """[(label, uri)] from every bucket down to `uri`: [('All buckets', ''), ('b', 's3://b/'), ('x', 's3://b/x/')]."""
    crumbs = [("All buckets", "")]
    bucket, key = parse_location(uri)
    if bucket:
        crumbs.append((bucket, f"s3://{bucket}/"))
        path = ""
        for part in (key[:-1] if key.endswith("/") else key).split("/") if key else []:
            path += part + "/"  # an empty part is a real folder: keys like '/data/x' or 'a//b'
            crumbs.append((part or "(no name)", f"s3://{bucket}/{path}"))
    return crumbs


def entry_icon(entry: Entry, s3: Any = None) -> str:
    """🪣 bucket, 📁 folder, or a file's icon by its type (📊 tables, 📕 PDF, 🖼️ images, 🧠 models, ...), the same
    icons s3.py's reports put in front of keys (its _file_icon; pass the s3 module when you have it)."""
    if entry.kind == "bucket":
        return "🪣"
    if entry.kind == "folder":
        return "📁"
    return (s3 or _s3_module())._file_icon(entry.name)


def sort_entries(entries: list[Entry], by: str = "name", descending: bool = False) -> list[Entry]:
    """Folders (or buckets) first, then files, each sorted `by` 'name' (natural order: part-2 before part-10),
    'size' or 'modified'. Folders have no size, so a size sort keeps them by name. 'path' sorts by the whole key
    instead, folders and files together, so everything below a folder reads like a tree: each folder, then what's
    in it."""
    if by not in ("name", "size", "modified", "path"):
        raise ValueError("by must be 'name', 'size', 'modified' or 'path'")
    if by == "path":  # folder by folder, so 'a/' and what's in it come before 'a-2.csv'
        return sorted(entries, key=lambda e: [_natural(part) for part in e.key.rstrip("/").split("/")],
                      reverse=descending)

    def key(entry: Entry) -> tuple:
        if by == "size":
            return (entry.size or 0, _natural(entry.name))
        if by == "modified":
            return (entry.modified or _EPOCH, _natural(entry.name))
        return (_natural(entry.name),)

    folders = [e for e in entries if e.is_folder]
    files = [e for e in entries if not e.is_folder]
    dated = by == "modified" and any(e.modified for e in folders)  # buckets have a creation date, folders don't
    folders.sort(key=key if dated else (lambda e: (_natural(e.name),)), reverse=descending and (by == "name" or dated))
    files.sort(key=key, reverse=descending)
    return folders + files


def parse_filter(text: str | None) -> Filter:
    """What a filter box's text asks for. Spaces (or commas) separate words, and a name must contain every word, in
    any case. A word that starts with '.' or '*.' is a file type ('.csv', '*.parquet', or 'ext:csv'), and a file
    must be one of the types asked for: '.csv .json' finds both, '.csv' also finds .csv.gz files and '.jpg' .jpeg
    ones. Any other word with * ? or [ is a pattern for the whole name ('part-0*')."""
    found = Filter()
    for word in re.split(r"[\s,]+", (text or "").strip().lower()):
        kind = _type_word(word)
        if kind:
            if kind not in found.types:
                found.types.append(kind)
        elif any(ch in word for ch in "*?["):
            found.patterns.append(word)
        elif word:
            found.words.append(word)
    return found


def filter_entries(entries: list[Entry], text: str | None = None, kind: str = "all") -> list[Entry]:
    """The entries that match a filter: names with the words of `text` in them, file types such as '.csv' or
    '.csv .json', patterns such as 'part-0*' (see parse_filter), and only folders or files: kind is 'all',
    'folders' (buckets count as folders) or 'files'."""
    if kind not in _KINDS:
        raise ValueError("kind must be 'all', 'folders' or 'files'")
    kind, wanted = _KINDS[kind], parse_filter(text)
    return [e for e in entries if wanted.matches(e) and (kind == "all" or e.is_folder == (kind == "folders"))]


def count_types(entries: list[Entry]) -> list[tuple[str, int, int]]:
    """(type, files, bytes) for the files among `entries`, most files first: a filter's choices. Types are what
    '.csv' in a filter finds: .csv.gz files count as csv and .jpeg as jpg. Files without an extension aren't counted."""
    found: dict[str, list[int]] = {}
    for entry in entries:
        kind = "" if entry.is_folder else _file_type(entry.name)
        if kind:
            counts = found.setdefault(kind, [0, 0])
            counts[0] += 1
            counts[1] += entry.size or 0
    return sorted(((kind, n, size) for kind, (n, size) in found.items()), key=lambda t: (-t[1], -t[2], t[0]))


def folder_stats(entries: list[Entry], top: int = 8) -> FolderStats:
    """Counts, total size, newest / oldest file and the `top` file types by size, for one folder's entries."""
    stats = FolderStats()
    types: dict[str, list[int]] = {}
    for entry in entries:
        if entry.is_folder:
            stats.folders += 1
            continue
        stats.files += 1
        stats.size += entry.size or 0
        stats.archived += entry.archived
        if entry.modified:
            stats.newest = max(stats.newest or entry.modified, entry.modified)
            stats.oldest = min(stats.oldest or entry.modified, entry.modified)
        bucket = types.setdefault(_extension(entry.name) or "(no extension)", [0, 0])
        bucket[0] += 1
        bucket[1] += entry.size or 0
    stats.types = sorted(((ext, n, size) for ext, (n, size) in types.items()), key=lambda t: (-t[2], -t[1], t[0]))
    stats.types = stats.types[:top]
    return stats


def explain_list_error(code: str, bucket: str, prefix: str = "") -> str:
    """What a listing error means and what to do about it, in a sentence."""
    if code in ("AccessDenied", "AllAccessDisabled"):
        where = f"arn:aws:s3:::{bucket}" if bucket else "this account"
        need = "s3:ListBucket" if bucket else "s3:ListAllMyBuckets"
        hint = f" (on {where}{f', prefix {prefix}' if prefix else ''})" if bucket else ""
        return (f"Your AWS role isn't allowed to list this{hint}: it needs {need}. Ask for that permission, or open "
                "a folder you can read by typing its path above.")
    if code == "NoSuchBucket":
        return f"There's no bucket named '{bucket}'. Check the spelling (bucket names are lowercase)."
    if code in ("InvalidBucketName", "ParamValidation"):
        return f"'{bucket}' isn't a valid bucket name. Paths look like s3://bucket/folder/."
    if code in ("InvalidAccessKeyId", "ExpiredToken", "SignatureDoesNotMatch", "NoCredentials"):
        return ("AWS didn't accept your credentials (they may have expired). Refresh them, or pass "
                "profile= to S3Explorer, then press ↻.")
    return "S3 couldn't list this. Press ↻ to try again."


# =============================================================================
# 4. S3Navigator - where the explorer is, and what's there (no UI)
# =============================================================================


class S3Navigator:
    """The explorer's logic, without the UI: lists one level at a time (a page of `page_size` entries per S3
    request), remembers where you've been for back / forward / up, and caches each folder so going back is
    instant. Every method returns the Folder you're now in. Listing errors land in Folder.error instead of
    raising, so a folder you can't read is a note, not a crash. Never prints.

    core: an S3Analyzer (or an S3View, whose analyzer is used); without one, an S3Analyzer is made from
    session / region / profile / client. list_limit: how many entries of one folder list_rest() lists (the
    explorer lists that many, so its search covers them). deep_limit: how many files below() lists at a time."""

    def __init__(
        self,
        core: Any = None,
        *,
        session: Any = None,
        region: str | None = None,
        profile: str | None = None,
        client: Any = None,
        page_size: int = 1000,
        cache_size: int = 64,
        list_limit: int = 10_000,
        deep_limit: int = 10_000,
    ):
        self.s3 = _s3_module(core)
        core = getattr(core, "core", core)  # an S3View carries its analyzer as .core
        self.core = core or self.s3.S3Analyzer(session, region=region, profile=profile, client=client)
        self.page_size = max(1, min(int(page_size), 1000))
        self.cache_size = cache_size
        self.list_limit = max(1, int(list_limit))
        self.deep_limit = max(1, int(deep_limit))
        self.location = ""  # the folder you're in: 's3://bucket/prefix/', or '' for every bucket
        self.focus = ""  # after open(file uri): that file, for the UI to show
        self.notice = ""  # after open(): something worth saying about where you landed
        self._back: list[str] = []
        self._forward: list[str] = []
        self._cache: dict[str, Folder] = {}
        self._below: dict[str, Folder] = {}  # below()'s listings, by folder
        self._started = False

    # ------------------------------------------------------------------ moving around

    @property
    def can_back(self) -> bool:
        return bool(self._back)

    @property
    def can_forward(self) -> bool:
        return bool(self._forward)

    def open(self, uri: str | None = "") -> Folder:
        """Go to a folder. A file's path opens its folder with `focus` set to the file. A path that doesn't
        end in '/' is checked first: 's3://b/data' opens the folder data/ when there is one."""
        target, self.focus, self.notice = self._resolve(uri)
        self._go(target)
        return self.folder()

    def back(self) -> Folder:
        """The folder you were in before."""
        if self._back:
            self._forward.append(self.location)
            self.location = self._back.pop()
        self.focus = self.notice = ""
        return self.folder()

    def forward(self) -> Folder:
        """Undo the last back()."""
        if self._forward:
            self._back.append(self.location)
            self.location = self._forward.pop()
        self.focus = self.notice = ""
        return self.folder()

    def up(self) -> Folder:
        """The folder above (from a bucket's top level: every bucket)."""
        self.focus = self.notice = ""
        if self.location:
            self._go(parent_uri(self.location))
        return self.folder()

    def refresh(self) -> Folder:
        """List the current folder again (other folders stay cached)."""
        self._cache.pop(self.location, None)
        self._below.pop(self.location, None)
        self.notice = ""
        return self.folder()

    def _go(self, target: str) -> None:
        if self._started and target != self.location:
            self._back.append(self.location)
            self._forward.clear()
        self._started = True
        self.location = target

    # ------------------------------------------------------------------ listing

    def folder(self, uri: str | None = None) -> Folder:
        """The current folder (or `uri`), listed once (its first page) and then cached."""
        uri = self.location if uri is None else uri
        if uri not in self._cache:
            folder = Folder(uri)
            self._load(folder)
            while self._cache and (len(self._cache) >= self.cache_size or
                                   sum(len(f.entries) for f in self._cache.values()) > _CACHED_ENTRIES):
                self._cache.pop(next(iter(self._cache)))
            self._cache[uri] = folder
        return self._cache[uri]

    def more(self, below: bool = False, progress: Callable[[int], None] | None = None) -> Folder:
        """The next page of a folder with more entries than one listing returns; below=True: the next deep_limit
        files of below()'s listing."""
        if below and parse_location(self.location)[0]:
            fresh = self.location not in self._below
            folder = self.below(progress=progress)
            if folder.more and not fresh:
                self._list(folder, self.deep_limit, progress)
            return folder
        folder = self.folder()
        if folder.more:
            self._list(folder, self.page_size)
        return folder

    def list_rest(self, uri: str | None = None, limit: int | None = None,
                  progress: Callable[[int], None] | None = None) -> Folder:
        """The current folder (or `uri`) with the rest of its entries listed, one S3 request per 1,000, until S3 has
        no more or it has listed `limit` of them (list_limit, 10,000, unless given); more() goes on from there. This
        is what the explorer does, so its search, counts and sort cover the whole folder.
        progress(entries) is called after each request."""
        folder = self.folder(uri)
        if folder.more:
            self._list(folder, (self.list_limit if limit is None else limit) - folder.listed, progress)
        return folder

    def below(self, uri: str | None = None, progress: Callable[[int], None] | None = None) -> Folder:
        """Everything below the current folder (or `uri`), not only its first level: every file, and each folder
        between it and them, the first deep_limit (10,000) files; more(below=True) lists the next ones. It's one S3
        request per 1,000 files. Cached like folder(); on the list of buckets it's the buckets.
        progress(files) is called after each request."""
        return self._deep(uri, self.deep_limit, progress)

    def lookup(self, text: str) -> int:
        """Find entries whose names start with `text` in S3, for a folder too big to load: they're added to
        the current folder. Returns how many weren't loaded yet."""
        folder = self.folder()
        bucket, prefix = parse_location(folder.uri)
        if not bucket or not text or "/" in text:  # a '/' would find what's in a sub-folder, not here
            return 0
        try:
            page = self.core.client.list_objects_v2(Bucket=bucket, Prefix=prefix + text, Delimiter="/",
                                                    MaxKeys=self.page_size)
        except (ClientError, BotoCoreError):
            return 0
        folder.requests += 1
        new = self._entries(folder, page, {entry.key for entry in folder.entries})
        folder.entries += new
        return len(new)

    def _deep(self, uri: str | None, keys: int, progress: Callable[[int], None] | None = None) -> Folder:
        """below(), listing only the first `keys` keys when it isn't cached yet (the explorer lists one page, then
        the rest in the background)."""
        uri = self.location if uri is None else uri
        if not parse_location(uri)[0]:
            return self.folder(uri)
        if uri not in self._below:
            folder = Folder(uri, deep=True)
            self._list(folder, keys, progress)
            while len(self._below) >= max(1, self.cache_size // 8):  # these can be big: keep a few
                self._below.pop(next(iter(self._below)))
            self._below[uri] = folder
        return self._below[uri]

    def _load(self, folder: Folder) -> None:
        """A folder's first page, or every bucket."""
        if parse_location(folder.uri)[0]:
            self._list(folder, self.page_size)
            return
        try:
            folder.entries = [
                Entry("bucket", info.name, modified=info.created) for info in self.core.list_buckets(with_region=False)
            ]
            folder.requests += 1
        except (ClientError, BotoCoreError) as exc:
            self._failed(folder, exc)

    @staticmethod
    def _failed(folder: Folder, exc: ClientError | BotoCoreError) -> None:
        """Note why a folder couldn't be listed, in place of raising."""
        if isinstance(exc, ClientError):
            error = exc.response.get("Error", {})
            folder.error_code = str(error.get("Code", "Error"))
            folder.error = f"{folder.error_code}: {error.get('Message', exc)}"
        else:
            folder.error_code, folder.error = type(exc).__name__, f"{type(exc).__name__}: {exc}"

    def _list(self, folder: Folder, keys: int, progress: Callable[[int], None] | None = None) -> None:
        """List up to `keys` more of a folder (from its first page when it has none yet), a page per request; an
        error lands in folder.error. progress(entries, or files for a deep folder) after each request."""
        try:
            while keys > 0 and (folder.more or not folder.requests):
                keys -= self._add(folder, self._request(folder, folder.token, min(self.page_size, keys)))
                if progress is not None:
                    progress(sum(not e.is_folder for e in folder.entries) if folder.deep else len(folder.entries))
        except (ClientError, BotoCoreError) as exc:
            self._failed(folder, exc)

    def _request(self, folder: Folder, token: str | None, keys: int) -> dict[str, Any]:
        """One ListObjectsV2 page of a folder, from `token` on: its first level (S3's '/' delimiter), or every key
        below it for a deep folder. It changes nothing, so it can run on a worker thread; _add adds the page."""
        bucket, prefix = parse_location(folder.uri)
        request: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": max(1, min(keys, 1000))}
        if not folder.deep:
            request["Delimiter"] = "/"
        if token:
            request["ContinuationToken"] = token
        return self.core.client.list_objects_v2(**request)

    def _add(self, folder: Folder, page: dict[str, Any]) -> int:
        """Add a page from _request to its folder (nothing twice) and move on its token; returns how many keys and
        folders the page had."""
        folder.entries += self._entries(folder, page, {entry.key for entry in folder.entries})
        folder.token = page.get("NextContinuationToken") if page.get("IsTruncated") else None
        folder.more = folder.token is not None
        folder.requests += 1
        listed = len(page.get("Contents", [])) + len(page.get("CommonPrefixes", []))
        folder.listed += listed
        return listed

    @staticmethod
    def _entries(folder: Folder, page: dict[str, Any], known: set[str]) -> list[Entry]:
        """The entries a ListObjectsV2 page adds to a folder, leaving out the keys in `known` (and adding the new
        ones to it). A deep folder gets each file and each folder on the way to it (S3 has no folders of its own; a
        folder's marker object becomes the folder); a folder's own marker object isn't one of its files."""
        bucket, prefix = parse_location(folder.uri)
        new: list[Entry] = []

        def add(kind: str, key: str, o: dict[str, Any] | None = None) -> None:
            known.add(key)
            new.append(Entry(kind, bucket, key) if o is None else Entry(
                kind, bucket, key, o.get("Size", 0), o.get("LastModified"), o.get("StorageClass", "STANDARD"),
                o.get("ETag", "").strip('"')))

        if not folder.deep:
            for p in page.get("CommonPrefixes", []):
                if p["Prefix"] not in known:
                    add("folder", p["Prefix"])
        for o in page.get("Contents", []):
            key = o["Key"]
            if not folder.deep:
                if key != prefix and key not in known:
                    add("file", key, o)
                continue
            rest = key[len(prefix):]
            at = rest.find("/")
            while at >= 0:
                if prefix + rest[: at + 1] not in known:
                    add("folder", prefix + rest[: at + 1])
                at = rest.find("/", at + 1)
            if rest and not key.endswith("/") and key not in known:
                add("file", key, o)
        return new

    def _resolve(self, uri: str | None) -> tuple[str, str, str]:
        """(folder to open, file to focus, notice) for a path someone typed or pasted."""
        bucket, key = parse_location(uri)
        if not bucket:
            return "", "", ""
        if not key or key.endswith("/"):
            return folder_uri(bucket, key), "", ""
        client = self.core.client
        try:
            first = client.list_objects_v2(Bucket=bucket, Prefix=key, MaxKeys=1).get("Contents", [])
            if first and first[0]["Key"] == key:
                return folder_uri(bucket, _parent_prefix(key)), f"s3://{bucket}/{key}", ""
            if client.list_objects_v2(Bucket=bucket, Prefix=key + "/", MaxKeys=1).get("KeyCount", 0):
                return folder_uri(bucket, key), "", ""
        except (ClientError, BotoCoreError):
            return folder_uri(bucket, key), "", ""  # the listing will say what's wrong
        parent = folder_uri(bucket, _parent_prefix(key))
        name = key.rpartition("/")[2]
        return parent, "", f"There's no file or folder named '{name}' in {parent} (names are case-sensitive)."


# =============================================================================
# 5. S3Explorer - the clickable notebook UI
# =============================================================================


def _friendly_errors(method: Callable) -> Callable:
    """Show AWS / input errors as a note in the explorer instead of a traceback."""

    @functools.wraps(method)
    def wrapper(self: S3Explorer, *args: Any, **kwargs: Any) -> None:
        if self.s3 is None:
            self._say(self._broken)
            return None
        try:
            return method(self, *args, **kwargs)
        except ClientError as exc:
            error = exc.response.get("Error", {})
            self._fail(f"{error.get('Code', 'Error')}: {error.get('Message', exc)}  [{method.__name__}]")
        except ImportError as exc:  # a missing optional package: the message says what to pip install
            self._fail(f"{str(exc).rstrip('.')}.")
        except (BotoCoreError, *getattr(self.s3, "_DATA_ERRORS", (ValueError, ImportError, OSError))) as exc:
            self._fail(f"{type(exc).__name__}: {exc}  [{method.__name__}]")
        return None

    return wrapper


# The toolbar's icons (24 x 24, drawn with lines), shown as masks in the text's colour so they follow the theme.
_ICON_PATHS = {
    "back": "<path d='M19 12H5M11 18l-6-6 6-6'/>",
    "forward": "<path d='M5 12h14M13 6l6 6-6 6'/>",
    "up": "<path d='M12 19V5M6 11l6-6 6 6'/>",
    "refresh": "<path d='M20 12a8 8 0 1 1-2.34-5.66L20 8.5M20 4v4.5h-4.5'/>",
    "edit": "<path d='M15.5 4.5a2.12 2.12 0 0 1 3 3L8 18l-4 1 1-4zM13.5 6.5l3 3'/>",
    "settings": "<path d='M9.1 5.9L9 2.9h6l-.1 3 1 .5 2.5-1.5 3 5.1-2.6 1.4v1.2l2.6 1.4-3 5.1-2.5-1.5-1 .5.1 3H9l.1-3-1-.5"
                "-2.5 1.5-3-5.1 2.6-1.4v-1.2L2.6 10l3-5.1 2.5 1.5z'/><circle cx='12' cy='12' r='2.8'/>",
    "close": "<path d='M6 6l12 12M18 6L6 18'/>",
    "search": "<circle cx='11' cy='11' r='6.5'/><path d='M20 20l-4.2-4.2'/>",
    "chevron": "<path d='M9 6l6 6-6 6'/>",
    "previous": "<path d='M15 6l-6 6 6 6'/>",
    "next": "<path d='M9 6l6 6-6 6'/>",
    "first": "<path d='M17 6l-6 6 6 6M7 6v12'/>",
    "last": "<path d='M7 6l6 6-6 6M17 6v12'/>",
    "below": "<path d='M6 4v9a3 3 0 0 0 3 3h10M15 12l4 4-4 4'/>",
    "check": "<path stroke-width='3.2' d='M5 12.5l4.5 4.5L19 7.5'/>",
    "minus": "<path stroke-width='3.2' d='M6.5 12h11'/>",
}


def _icon_rules() -> str:
    """A CSS rule per icon: .s3x-i-<name> sets --s3x-icon, which .s3x-ic::before (and the search box) draw."""
    svg = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' "
           "stroke-linecap='round' stroke-linejoin='round'>{}</svg>")
    return "\n".join(f'.s3x .s3x-i-{name}{{--s3x-icon:url("data:image/svg+xml,{quote(svg.format(paths))}")}}'
                     for name, paths in _ICON_PATHS.items())


_CSS = """<style>
.s3x{--s3x-accent:#3b82f6;--s3x-accent-fg:#1d64d8;--s3x-tint:rgba(59,130,246,.12);--s3x-tint-line:rgba(59,130,246,.45);
 --s3x-line:rgba(127,127,127,.22);--s3x-fill:rgba(127,127,127,.08);--s3x-hover:rgba(127,127,127,.13);
 --s3x-bg:var(--jp-layout-color0,var(--vscode-editor-background,#fff));--s3x-raised:var(--s3x-bg);
 font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:13px;
 border:1px solid var(--s3x-line);border-radius:12px;overflow:hidden;margin:6px 0;background:var(--s3x-bg);
 box-shadow:0 1px 2px rgba(0,0,0,.04),0 12px 32px -18px rgba(0,0,0,.25)}
body[data-jp-theme-light=false] .s3x,body.vscode-dark .s3x{--s3x-accent-fg:#8ab4ff;--s3x-tint:rgba(96,165,250,.16);
 --s3x-tint-line:rgba(96,165,250,.5);--s3x-raised:rgba(255,255,255,.11)}
.s3x .widget-html-content{line-height:1.45}
.s3x .jupyter-button,.s3x .widget-label,.s3x input{font-size:13px}
.s3x button.jupyter-button{color:inherit;background:transparent;box-shadow:none;outline:none;margin:0;font-family:inherit;
 transition:background-color .12s ease,color .12s ease,opacity .12s ease,border-color .12s ease,box-shadow .12s ease}
.s3x button.jupyter-button:hover:enabled,.s3x button.jupyter-button:focus:enabled,.s3x button.jupyter-button:active,
.s3x button.jupyter-button.mod-active{box-shadow:none;outline:none}
.s3x button.jupyter-button:focus-visible:enabled{outline:2px solid var(--s3x-tint-line);outline-offset:-2px}
.s3x button.jupyter-button:active:enabled{background-color:var(--s3x-hover)}
.s3x .s3x-ic::before{content:"";display:block;flex:0 0 auto;width:16px;height:16px;background:currentColor;
 -webkit-mask:var(--s3x-icon) center/contain no-repeat;mask:var(--s3x-icon) center/contain no-repeat}
.s3x .s3x-bar{align-items:center;gap:8px;padding:8px 10px;border-bottom:1px solid var(--s3x-line)}
.s3x .s3x-bar>*,.s3x .s3x-navs>*{margin:0}
.s3x .s3x-navs{flex:0 0 auto;gap:2px;padding:2px;border-radius:10px;background:var(--s3x-fill)}
.s3x button.s3x-nav{display:inline-flex;align-items:center;justify-content:center;width:30px;min-width:30px;height:28px;
 padding:0;border-radius:8px;font-size:0;line-height:0;opacity:.8}
.s3x button.s3x-nav:hover:enabled{background:var(--s3x-hover);opacity:1}
.s3x button.s3x-nav:disabled{opacity:.25;cursor:default}
.s3x .s3x-crumbs{flex:1 1 auto;min-width:0;overflow:hidden;align-items:center;flex-wrap:nowrap}
.s3x .s3x-crumbs>*{margin:0;flex:0 1 auto;min-width:0}
.s3x button.s3x-crumb{width:auto;max-width:240px;height:28px;padding:0 8px;border-radius:7px;opacity:.62;font-weight:500}
.s3x button.s3x-crumb:hover{background:var(--s3x-hover);opacity:1}
.s3x button.s3x-crumb.s3x-here{opacity:1;font-weight:650}
.s3x .s3x-sep{display:flex;align-items:center;justify-content:center;width:14px;min-width:14px;flex:0 0 auto;font-size:0;
 opacity:.35}
.s3x .s3x-sep::before{width:12px;height:12px}
.s3x .s3x-search input,.s3x .s3x-path input,.s3x .s3x-setting input{height:32px;padding:0 10px;border-radius:8px;
 border:1px solid transparent;background:var(--s3x-fill);color:inherit;box-shadow:none}
.s3x .s3x-search input:focus,.s3x .s3x-path input:focus,.s3x .s3x-setting input:focus{outline:none;
 border-color:var(--s3x-accent);background:var(--s3x-bg);box-shadow:0 0 0 3px var(--s3x-tint)}
.s3x .s3x-path input{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px}
.s3x .s3x-side{border-right:1px solid var(--s3x-line)}
.s3x .s3x-side>*{margin:0}
.s3x .s3x-find{gap:8px;padding:10px 10px 10px;border-bottom:1px solid var(--s3x-line)}
.s3x .s3x-find>*,.s3x .s3x-searchrow>*{margin:0}
.s3x .s3x-searchrow{position:relative;align-items:center}
.s3x .s3x-search{position:relative}
.s3x .s3x-search::before{content:"";position:absolute;z-index:1;left:11px;top:50%;width:15px;height:15px;margin-top:-7.5px;
 background:currentColor;opacity:.45;pointer-events:none;-webkit-mask:var(--s3x-icon) center/contain no-repeat;
 mask:var(--s3x-icon) center/contain no-repeat}
.s3x .s3x-search input{padding:0 34px}
.s3x button.s3x-clear{position:absolute;z-index:1;right:4px;top:50%;margin-top:-12px;width:24px;min-width:24px;height:24px;
 border-radius:6px}
.s3x button.s3x-clear::before{width:12px;height:12px}
.s3x .s3x-kinds{align-items:center;gap:8px;flex-wrap:wrap}
.s3x .s3x-kinds>*,.s3x .s3x-segs>*,.s3x .s3x-types>*{margin:0}
.s3x .s3x-segs{flex:0 0 auto;gap:2px;padding:2px;border-radius:9px;background:var(--s3x-fill)}
.s3x button.s3x-seg{width:auto;height:26px;padding:0 10px;border-radius:7px;font-size:12px;font-weight:500;opacity:.68}
.s3x button.s3x-seg:hover{opacity:1}
.s3x button.s3x-seg.s3x-on{opacity:1;background:var(--s3x-raised);
 box-shadow:0 1px 2px rgba(0,0,0,.12),0 0 0 1px rgba(127,127,127,.14)}
.s3x .s3x-types{flex-wrap:wrap;gap:6px}
.s3x button.s3x-chip{width:auto;height:26px;padding:0 10px;border-radius:13px;border:1px solid var(--s3x-line);font-size:12px}
.s3x button.s3x-chip:hover{background:var(--s3x-hover)}
.s3x button.s3x-chip.s3x-on{background:var(--s3x-tint);border-color:var(--s3x-tint-line);color:var(--s3x-accent-fg);
 font-weight:600}
.s3x button.s3x-more{border-style:dashed;opacity:.75}
.s3x button.s3x-deep{display:inline-flex;align-items:center;gap:6px;margin-left:auto}
.s3x button.s3x-deep::before{width:13px;height:13px}
.s3x .s3x-list>*{margin:0}
.s3x .s3x-head{position:sticky;top:0;z-index:4;padding:0 12px;border-bottom:1px solid var(--s3x-line);
 background:var(--s3x-bg)}
.s3x .s3x-head>*{margin:0}
.s3x button.s3x-col{height:30px;line-height:30px;padding:0 8px;text-align:left;font-size:11px;font-weight:600;
 letter-spacing:.06em;text-transform:uppercase;opacity:.48}
.s3x button.s3x-col:hover{opacity:.85}
.s3x button.s3x-col.s3x-on{opacity:.9}
.s3x button.s3x-col.s3x-num{text-align:right}
.s3x button.s3x-namecol{padding-left:28px}
.s3x .s3x-rows{padding:6px}
.s3x .s3x-rows>*{margin:0}
.s3x .s3x-r{position:relative;height:32px;align-items:center;justify-content:flex-end;flex:0 0 auto}
.s3x .s3x-r>*{margin:0}
.s3x button.s3x-row{position:absolute;top:0;left:0;width:100%;height:32px;text-align:left;padding:0 164px 0 34px;
 border-radius:8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.s3x button.s3x-row:hover{background:var(--s3x-hover)}
.s3x button.s3x-row.s3x-picked{background:rgba(59,130,246,.07)}
.s3x button.s3x-row.s3x-on{background:var(--s3x-tint);font-weight:600;box-shadow:inset 0 0 0 1px var(--s3x-tint-line)}
.s3x button.s3x-check{position:absolute;z-index:2;top:50%;left:9px;display:inline-flex;align-items:center;
 justify-content:center;width:16px;min-width:16px;height:16px;margin:-8px 0 0;padding:0;font-size:0;border-radius:5px;
 border:1.5px solid rgba(127,127,127,.55);background:var(--s3x-bg);color:#fff;opacity:0}
.s3x button.s3x-check::before{width:11px;height:11px;opacity:0}
.s3x .s3x-r:hover button.s3x-check,.s3x .s3x-picking button.s3x-check,.s3x .s3x-list:hover .s3x-head button.s3x-check{
 opacity:1}
.s3x button.s3x-check:hover{border-color:var(--s3x-accent)}
.s3x button.s3x-check.s3x-on{opacity:1;background:var(--s3x-accent);border-color:var(--s3x-accent)}
.s3x button.s3x-check.s3x-on::before{opacity:1}
.s3x .s3x-head button.s3x-check{left:15px}
.s3x .s3x-r2 button.s3x-check{top:15px}
.s3x .s3x-r .widget-label{position:relative;z-index:1;pointer-events:none;text-align:right;opacity:.58;
 font-size:12px;font-variant-numeric:tabular-nums;padding-right:8px}
.s3x .s3x-buckets button.s3x-row{padding:0 88px 0 10px}
.s3x .s3x-buckets .s3x-size,.s3x .s3x-buckets button.s3x-check{display:none}
.s3x .s3x-buckets button.s3x-namecol{padding-left:8px}
.s3x .s3x-r2,.s3x .s3x-r2 button.s3x-row{height:46px}
.s3x .s3x-r2 button.s3x-row{padding-bottom:16px}
.s3x .s3x-r .widget-label.s3x-where{position:absolute;display:block;left:61px;right:164px;bottom:6px;height:16px;
 line-height:16px;padding:0;text-align:left;font-size:11.5px;opacity:.5;direction:rtl;overflow:hidden;text-overflow:ellipsis;
 white-space:nowrap}
.s3x .s3x-where::before,.s3x .s3x-where::after{content:"\\200E"}
.s3x .s3x-foot{flex-wrap:wrap;justify-content:center;gap:6px;padding:4px 12px 14px;align-items:center}
.s3x .s3x-foot>*{margin:0}
.s3x button.s3x-link{width:auto;height:28px;padding:0 10px;border-radius:8px;color:var(--s3x-accent-fg);font-weight:500}
.s3x button.s3x-link:hover{background:var(--s3x-tint)}
.s3x .s3x-empty{padding:22px 8px 4px;text-align:center;opacity:.62;font-size:12.5px}
.s3x .s3x-pages{align-items:center;gap:2px;padding:3px 8px 3px 14px;border-top:1px solid var(--s3x-line);
 background:var(--s3x-bg)}
.s3x .s3x-pages>*{margin:0}
.s3x .s3x-pages .widget-html-content{font-size:12px;font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;
 text-overflow:ellipsis;opacity:.72}
.s3x .s3x-pages b{font-weight:600}
.s3x .s3x-picks{align-items:center;gap:6px;padding:8px 10px 8px 14px;border-top:1px solid var(--s3x-line);
 background:var(--s3x-bg);box-shadow:0 -8px 18px -14px rgba(0,0,0,.3)}
.s3x .s3x-picks>*{margin:0}
.s3x .s3x-picks .widget-html-content{display:flex;flex-direction:column;line-height:1.3;min-width:0}
.s3x .s3x-picks-n{font-weight:600}
.s3x .s3x-picks-s{font-size:11.5px;opacity:.6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.s3x button.s3x-primary{width:auto;height:30px;padding:0 14px;border-radius:8px;background:var(--s3x-accent);
 color:#fff;font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,.15)}
.s3x button.s3x-primary:hover:enabled{background:#2f6fde}
.s3x button.s3x-primary:disabled{opacity:.4;cursor:default;box-shadow:none}
.s3x button.s3x-quiet{width:auto;height:30px;padding:0 10px;border-radius:8px;opacity:.72}
.s3x button.s3x-quiet:hover:enabled{opacity:1;background:var(--s3x-hover)}
.s3x .s3x-right{padding:0 18px 14px}
.s3x .s3x-right>*{margin:0;min-width:0}
.s3x .s3x-right .widget-html-content{min-width:0}
.s3x .s3x-actions{position:sticky;top:0;z-index:3;flex-wrap:wrap;gap:6px;padding:10px 0;background:var(--s3x-bg);
 border-bottom:1px solid var(--s3x-line)}
.s3x .s3x-actions>*{margin:0}
.s3x button.s3x-act{width:auto;height:28px;padding:0 12px;border-radius:8px;border:1px solid var(--s3x-line);
 font-weight:500}
.s3x button.s3x-act:hover{background:var(--s3x-hover)}
.s3x button.s3x-act.s3x-on{background:var(--s3x-tint);border-color:var(--s3x-tint-line);color:var(--s3x-accent-fg)}
.s3x a.s3x-act{display:flex;align-items:center;box-sizing:border-box;height:28px;padding:0 12px;border-radius:8px;
 border:1px solid var(--s3x-line);font-weight:500;color:inherit;text-decoration:none;white-space:nowrap;
 transition:background-color .12s ease}
.s3x a.s3x-act:hover{background:var(--s3x-hover)}
.s3x a.s3x-act:focus-visible{outline:2px solid var(--s3x-tint-line);outline-offset:-2px}
.s3x button.s3x-close{display:inline-flex;align-items:center;justify-content:center;margin-left:auto;width:28px;
 min-width:28px;padding:0;border-color:transparent;font-size:0;opacity:.7}
.s3x button.s3x-close:hover{opacity:1}
.s3x button.s3x-expand{margin-left:auto}
.s3x button.s3x-expand+button.s3x-close{margin-left:0}
.s3x .s3x-pager{flex-wrap:wrap;gap:6px;align-items:center;margin-top:10px;padding:10px 0 4px;
 border-top:1px solid var(--s3x-line)}
.s3x .s3x-pager>*{margin:0}
.s3x .s3x-pager .widget-label{opacity:.6;margin-right:6px}
.s3x .s3x-settings{gap:10px;padding:6px 0 8px}
.s3x .s3x-settings>*{margin:0}
.s3x .s3x-setting{gap:10px;align-items:center;flex-wrap:wrap}
.s3x .s3x-setting>*{margin:0}
.s3x .s3x-setting .widget-label{font-size:12px;font-weight:500}
.s3x .s3x-hint{opacity:.6;font-size:12px}
.s3x .s3x-status{padding:0 12px;border-top:1px solid var(--s3x-line);background:var(--s3x-fill);font-size:12px}
.s3x .s3x-status>.widget-html-content{display:flex;gap:12px;justify-content:space-between;align-items:center;
 line-height:30px;min-width:0}
.s3x .s3x-status .s3x-at{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;opacity:.78}
.s3x .s3x-status code{display:inline-block;max-width:62%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
 font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;line-height:18px;padding:0 7px;
 border-radius:6px;border:1px solid var(--s3x-line);background:var(--s3x-bg);user-select:all;-webkit-user-select:all;
 cursor:text}
@keyframes s3x-spin{to{transform:rotate(360deg)}}
@keyframes s3x-glow{from{background-position:100% 0}to{background-position:0 0}}
.s3x .s3x-busy::before,.s3x .s3x-spin{content:"";display:inline-block;flex:0 0 auto;width:10px;height:10px;
 margin-right:8px;vertical-align:-2px;border:2px solid rgba(127,127,127,.28);border-top-color:var(--s3x-accent);
 border-radius:50%;animation:s3x-spin .75s linear infinite}
.s3x .s3x-wait{padding:16px 0}
.s3x .s3x-wait-t{display:flex;align-items:center;opacity:.75;margin-bottom:16px}
.s3x .s3x-sk{height:12px;margin:10px 0;border-radius:6px;background-size:300% 100%;
 background-image:linear-gradient(90deg,var(--s3x-fill) 30%,var(--s3x-hover) 50%,var(--s3x-fill) 70%);
 animation:s3x-glow 1.3s ease-in-out infinite}
.s3x .s3x-sk.s3x-t{height:18px;width:45%;margin-bottom:16px}
.s3x .s3x-sk-cards{display:flex;gap:8px;margin:0 0 18px}
.s3x .s3x-sk-cards .s3x-sk{flex:1;height:50px;margin:0;border-radius:8px}
@media (prefers-reduced-motion:reduce){.s3x *,.s3x *::before{transition:none!important;animation-duration:2.5s!important}}
""" + _icon_rules() + "\n</style>"
_DOCUMENTS = ("pdf", "docx", "docm", "dotx", "pptx", "pptm", "potx", "ppsx")  # files "Read all" opens
_TEXT_PAGES = 50  # PDF pages one "Text" report shows (text is light, so more than the 20 "Read all" draws)
_CLICK_GRACE = 0.35  # seconds after the rows change during which a click is ignored (it was aimed at the old rows)
_BACKGROUND = ("preview", "head")  # quick reports (no progress bar) that load on worker threads in a notebook
_WORKERS = 4  # background reports loading at once, so a click doesn't wait for files clicked before it
_TYPE_CHIPS = 6  # file types shown as chips over the list before "+N more"
_PAGE_BUTTONS = (("first", "«", "The first page"), ("previous", "‹", "The page before"), ("next", "›", "The next page"),
                 ("last", "»", "The last page"))
_CHECK = ("s3x-check", "s3x-ic", "s3x-i-check")  # a row's checkbox; it gets s3x-on when ticked


class _Row:
    """One reusable row of the list: a full-width button (the name) under a checkbox and two labels (size, modified),
    and under the name, when the list holds everything below a folder, the folder the entry is in."""

    def __init__(self, widgets: Any, on_click: Callable[[_Row], None], on_check: Callable[[_Row], None]):
        self.entry: Entry | None = None
        self.button = widgets.Button(layout=widgets.Layout(width="100%"))
        self.button.add_class("s3x-row")
        self.button.on_click(lambda _: on_click(self))
        self.check = widgets.Button(description="✓", tooltip="Select it, to download several files in one .zip")
        for name in _CHECK:
            self.check.add_class(name)
        self.check.on_click(lambda _: on_check(self))
        self.where = widgets.Label(layout=widgets.Layout(display="none"))
        self.where.add_class("s3x-where")
        self.size = widgets.Label(layout=widgets.Layout(width="76px", flex="0 0 auto"))
        self.size.add_class("s3x-size")
        self.age = widgets.Label(layout=widgets.Layout(width="76px", flex="0 0 auto"))
        self.box = widgets.HBox([self.button, self.check, self.where, self.size, self.age],
                                layout=widgets.Layout(width="100%"))
        self.box.add_class("s3x-r")


class S3Explorer:
    """Browse S3 like a file explorer in a notebook: folders and files on the left, what's inside the file you
    click on the right. Click a folder to open it; ← → ↑ go back, forward and up; click the path to jump
    anywhere (paste an s3:// path or an S3 console link); click a column header to sort.

    Above the list, the search box narrows it by name or file type ('.csv', '.csv .json', 'part-0*'), All / Folders /
    Files show only one kind, a chip for each file type here shows only those files, and "Include subfolders"
    lists everything below the folder, not only its first level, to search all of it (x.filter() does the same).
    They cover the whole folder: a big one shows its first page of entries at once and lists the rest in the
    background, up to x.nav.list_limit (10,000) entries. The list shows page_size rows at a time, and « ‹ › » under
    it move between pages.
    Tick files (a checkbox shows when you point at a row) and "Download selected", in the bar under the list,
    downloads them as one .zip; x.picked lists what's ticked.

    uri: where to start ('s3://bucket/prefix/', 'bucket/prefix', a file's path, a console link); leave it
    out to start from your buckets.
    core: an S3Analyzer or S3View to use (else one is made from profile / region).
    height: the height of the two panes in pixels. page_size: rows on each page of the list.
    zip_max_size: the biggest folder "Download .zip" packs ('100MB', '2GB'); ⚙ Settings changes it, and the most
    files in a zip (zip_max_files, 10,000) and where zips go (zip_folder, the notebook's folder).
    mode: 'auto' (the clickable explorer in Jupyter, a text listing elsewhere), 'widgets' or 'text'.

    The previews and reports on the right come from s3.py's S3View; `x.ui` is one you can use in any cell
    (x.ui.summary(x.location)), and `x.nav` is the S3Navigator behind the list."""

    def __init__(
        self,
        uri: str = "",
        core: Any = None,
        *,
        profile: str | None = None,
        region: str | None = None,
        height: int = 560,
        page_size: int = 100,
        zip_max_size: int | str = "100MB",
        mode: str = "auto",
        progress: str = "auto",
    ):
        if mode not in ("auto", "widgets", "text"):
            raise ValueError("mode must be 'auto', 'widgets' or 'text'")
        self.height = height
        self.page_size = max(10, int(page_size))
        self.zip_max_size = zip_max_size  # "Download .zip" zips a folder up to this size (⚙ Settings changes it)
        self.zip_max_files = 10_000  # and up to this many files
        self.zip_folder = "."  # where the .zip goes: the notebook's folder
        self.selected = ""  # the uri of the file shown on the right ('' = the folder's overview)
        self.shown: list[list[Any]] = []  # the blocks of the last report on the right, for tests and curious users
        self._action = ""
        self._broken = ""
        self._shown_at: Any = object()
        self._widgets: Any = None
        self._quiet = False  # set while the code (not the user) clears the filter
        self._query = ""  # the search box's text
        self._kind = "all"  # All / Folders / Files: what the list shows (it stays as you move around, like the sort)
        self._deep = False  # "Include subfolders": the list holds everything below the folder (S3Navigator.below)
        self._all_types = False  # every file type has a chip, not only the most common
        self._picked: dict[str, Entry] = {}  # what's ticked in the list, by uri, to download as one .zip
        self._picks_auto = ""  # the zip name the explorer suggested, until it's typed over
        self._job = 0  # counts changes of the right pane; a background report made for an older one isn't shown
        self._workers: ThreadPoolExecutor | None = None  # the threads background reports load on
        self._task: Any = None  # the last background report's asyncio task (tests wait for it)
        self._first_page = 1  # where "Read all" or "Text" starts in a PDF; the pager under the report moves it
        self._page_counts: dict[tuple[str, str], int] = {}  # (uri, etag) -> a PDF's pages (0: couldn't count)
        self._expand_all = False  # ▾ Expand all: JSON trees show every object and array (it stays on, like the sort)
        self._reports: list[list[Any]] = []  # the reports on the right, to draw again when that changes
        try:
            self.s3 = _s3_module(core)
        except ImportError as exc:
            self.s3, self._broken = None, str(exc)
            self._say(self._broken)
            return
        if core is None and (profile or region):
            core = self.s3.S3Analyzer(profile=profile, region=region)
        self.nav = S3Navigator(core)
        self.core = self.nav.core
        self.ui = self.s3.S3View(self.core, progress=progress)  # for your own cells; reports show where you call them
        self._pane = self.s3.S3View(self.core, mode="text" if mode == "text" else "auto", progress=progress)
        self._pane._show = self._capture  # its reports land in the right-hand pane, not in a new output
        note = ""
        if mode != "text" and (mode == "widgets" or self.s3._in_notebook()):
            try:
                self._widgets = self.s3._require("ipywidgets", "Clicking through folders")
            except ImportError as exc:
                note = (f"{exc}, which SageMaker notebooks normally have. Install it and restart the kernel; "
                        "until then this is a plain listing, and open(...) moves around.")
        self._pane.use_html = self._widgets is not None or (mode != "text" and self.s3._in_notebook())
        self._cache: dict[tuple[str, str, str], list[list[Any]]] = {}
        self._sort, self._descending = "name", False
        self._offset = 0  # the list's page: the index of its first row in _visible
        self._visible: list[Entry] = []
        self._folder: Folder | None = None  # what the list was last drawn from
        self._overview: Folder | None = None  # the folder whose overview the right pane shows, if it does
        self._order: tuple[Any, ...] = ()  # (folder, what it was sorted by, its entries in that order)
        self._counted: tuple[Any, ...] = ()  # (folder, how many entries, their FolderStats)
        self._lister: Folder | None = None  # the folder being listed in the background
        self._list_task: Any = None  # its asyncio task (tests wait for it)
        self._list_error: tuple[Any, ...] = ()  # (folder, why its background listing stopped early)
        self._rows_key: tuple = ()
        self._rows_at = 0.0
        if self._widgets is not None:
            self._build()
        self._navigate(lambda: self.nav.open(uri))
        if note:
            self._say(note)
        self._display()

    # ------------------------------------------------------------------ public commands

    @property
    def location(self) -> str:
        """The folder you're in ('s3://bucket/prefix/'), or '' for the list of buckets."""
        return self.nav.location if self.s3 is not None else ""

    @property
    def picked(self) -> list[str]:
        """The files and folders ticked in the list (s3:// paths), in the order they were ticked: x.ui.download_zip(
        x.picked) zips them from code."""
        return list(self._picked)

    @_friendly_errors
    def open(self, uri: str = "") -> None:
        """Go to a folder, or show a file (its folder opens with the file on the right). Takes s3:// paths,
        bucket/prefix and S3 console links; open() with nothing goes to your list of buckets."""
        self._navigate(lambda: self.nav.open(uri))

    @_friendly_errors
    def back(self) -> None:
        """Go back to the folder you were in before (the ← button)."""
        self._navigate(self.nav.back)

    @_friendly_errors
    def forward(self) -> None:
        """Go forward again after back() (the → button)."""
        self._navigate(self.nav.forward)

    @_friendly_errors
    def up(self) -> None:
        """Go to the folder above this one (the ↑ button)."""
        self._navigate(self.nav.up)

    @_friendly_errors
    def refresh(self) -> None:
        """List this folder again, to pick up files added or removed since it was opened (the ↻ button)."""
        keep = self.selected
        self._cache.clear()
        self._navigate(self.nav.refresh, keep=keep, same=True)

    @_friendly_errors
    def filter(self, text: str = "", kind: str = "all", subfolders: bool = False) -> None:
        """Show only part of the list, as the search box and the buttons above it do: names with `text` in them,
        file types such as '.csv' or '.csv .json' (.csv also finds .csv.gz), patterns such as 'part-0*'; only
        kind='folders' or 'files'; and with subfolders=True everything below this folder, not only its first
        level (the first 10,000 files). It searches the whole folder, up to its first 10,000 entries, and in a bigger
        one it also asks S3 for the names that start with `text`. filter() shows everything again.

        x.filter(".parquet", subfolders=True)    # every Parquet file below this folder
        x.filter(kind="folders")                 # only the folders here"""
        if kind not in _KINDS:
            raise ValueError(f"kind={kind!r}: use 'all', 'folders' or 'files'")
        self._kind, self._all_types = _KINDS[kind], False
        if bool(subfolders) != self._deep:
            self._deep = bool(subfolders)
            self._busy("Listing everything below this folder…")
        folder = self._source()
        self._follow(folder)
        wanted = parse_filter(text)
        if (folder.more and self._lister is not folder and not folder.deep and len(wanted.words) == 1
                and not wanted.patterns and not wanted.types):
            self.nav.lookup(wanted.words[0])  # past what's listed: what "Look up" under the list does
        self._set_query(text or "", renew=True)
        if self._widgets is not None and not self.selected:
            self._show_folder(self._source())

    # ------------------------------------------------------------------ plumbing

    def __repr__(self) -> str:
        where = self.location or "all buckets" if self.s3 is not None else "not ready"
        return f"S3Explorer({where})"

    def _ipython_display_(self) -> None:
        if self._execution() != self._shown_at:  # it already showed itself when this cell made it
            self._display()

    @staticmethod
    def _execution() -> Any:
        try:
            from IPython.core.getipython import get_ipython
        except ImportError:
            return None
        shell = get_ipython()
        return getattr(shell, "execution_count", None) if shell is not None else None

    def _display(self) -> None:
        self._shown_at = self._execution()
        if self.s3 is None or self._widgets is None or self._shown_at is None:
            return  # a text explorer prints as it goes; outside IPython there's nowhere to show widgets
        from IPython.display import display

        display(self._app)

    def _say(self, text: str) -> None:
        """A warning note; works even when s3.py couldn't be loaded and nothing else can render."""
        if self.s3 is not None:
            self._set_pane([[self.s3._Note(text, "warn")]])
            return
        if self._execution() is not None:
            from IPython.display import HTML, display

            display(HTML(f'<div style="padding:6px 10px;border-left:3px solid #f59e0b;'
                         f'background:rgba(245,158,11,.1)">⚠ {html.escape(text)}</div>'))
            return
        print("[!] " + text)

    def _fail(self, text: str) -> None:
        self._set_pane([[self.s3._Note(text, "warn")]])

    def _capture(self, blocks: list[Any]) -> None:
        """The pane's S3View renders here (see _trim)."""
        self._captured.append(self._trim(blocks, self.selected))

    def _trim(self, blocks: list[Any], selected: str) -> list[Any]:
        """A report for the right pane: without its Next block (its calls name commands of a view you don't have
        here), and with the file's name instead of its whole path (the status bar shows that)."""
        kept = []
        for block in blocks:
            if isinstance(block, self.s3._Next):
                continue
            if isinstance(block, self.s3._Title) and selected and selected in block.text:
                block = dataclasses.replace(block, text=block.text.replace(selected, Entry(
                    "file", *parse_location(selected)).name))
            kept.append(block)
        return kept

    def _entry(self) -> Entry | None:
        """The file shown on the right, as listed."""
        return next((e for e in self._source().entries if e.uri == self.selected), None) if self.selected else None

    def _source(self) -> Folder:
        """What the list is drawn from: this folder's first level, or with "Include subfolders" everything below it.
        The first time, a notebook lists its first page (_follow lists the rest in the background) and anywhere else
        its first 10,000 files, with the count so far in the status bar."""
        if self._deep and parse_location(self.nav.location)[0]:
            first = self.nav.page_size if self._background() else self.nav.deep_limit
            return self.nav._deep(None, first, self._listing)
        return self.nav.folder()

    def _listing(self, count: int) -> None:
        self._busy(f"Listing everything below this folder… {_plural(count, 'file')} so far" if self._deep else
                   f"Listing this folder… {count:,} so far")

    # ------------------------------------------------------------------ listing the rest of a big folder

    def _background(self) -> bool:
        """Whether listings go on in the background: in a notebook, where the kernel's event loop runs the clicks."""
        return self._widgets is not None and self._loop() is not None

    def _goal(self, folder: Folder) -> int:
        """How much of a folder the explorer lists before it stops and offers "Load more from S3"."""
        return self.nav.deep_limit if folder.deep else self.nav.list_limit

    def _follow(self, folder: Folder) -> None:
        """The list now shows `folder`: list the rest of it, up to its first list_limit entries (deep_limit files
        with Include subfolders), so the search, the counts and the sort cover the whole folder and not only the
        first page S3 returned. A listing of another folder stops."""
        if self._lister is not None and self._lister is not folder:
            self._stop_listing()
        keys = self._goal(folder) - folder.listed
        if folder.more and keys > 0:
            self._list_more(folder, keys)

    def _list_more(self, folder: Folder, keys: int) -> None:
        """List up to `keys` more of a folder. In a notebook it's a page at a time on a worker thread, so the list can
        be searched and clicked meanwhile and each page updates it (_list_later); elsewhere it's right away."""
        if self._lister is folder:
            return
        if not self._background():
            self._busy("Listing…")
            self.nav._list(folder, keys, self._listing)
            return
        self._stop_listing()
        self._lister, self._list_error = folder, ()
        self._list_task = self._loop().create_task(self._list_later(folder, keys))

    def _stop_listing(self) -> None:
        if self._lister is not None:
            self._lister = None
            self._list_task.cancel()

    async def _list_later(self, folder: Folder, keys: int) -> None:
        """List a folder a page at a time: each request on a worker thread, and each page added on the kernel's event
        loop, where the clicks run, so nothing changes under them. It stops after `keys` entries, when S3 has no
        more, or when the list moves to another folder."""
        try:
            while keys > 0 and folder.more and self._lister is folder:
                token = folder.token
                page = await asyncio.wrap_future(self._threads().submit(
                    self.nav._request, folder, token, min(self.nav.page_size, keys)))
                if self._lister is not folder:
                    return
                if folder.token == token:  # else this page was listed meanwhile: ask for the one after it
                    keys -= self.nav._add(folder, page)
                    self._guard(lambda: self._listed(folder))
        except (ClientError, BotoCoreError) as exc:
            if self._lister is folder:
                code = exc.response.get("Error", {}).get("Code", "") if isinstance(exc, ClientError) else ""
                self._list_error = (folder, f"{code or type(exc).__name__}: {exc}")
        except Exception as exc:  # a bug: still say so under the list, and not in the kernel's log
            if self._lister is folder:
                self._list_error = (folder, f"something went wrong, {type(exc).__name__}: {exc}")
        finally:
            if self._lister is folder:
                self._lister = None
                self._guard(lambda: self._listed(folder))

    def _listed(self, folder: Folder) -> None:
        """After a page of a background listing (and once it's done): the counts, the chips, the list, the status bar
        and the folder's overview on the right again, if they still show that folder. The overview changes in place,
        where the reader has scrolled it to."""
        if folder is not self._folder or self._widgets is None:
            return
        self._draw_filters(folder)
        self._draw_rows(folder)
        self._draw_status(folder)
        if self._overview is folder:
            self.shown = self._folder_blocks(folder)
            self._content.value = self.s3._render_html(self.shown, self._pane.max_rows)

    def _report(self, action: str, method: Callable | str, *args: Any, cache: bool = True, variant: Any = None,
                **kwargs: Any) -> None:
        """Run one of the pane's S3View commands (by name), or a method of the explorer, and show its report on the
        right, with its progress bar above it while it runs. Reports are cached per file version (and `variant`,
        such as the first page shown).

        In a notebook, the quick reports (_BACKGROUND) load on worker threads instead: the click returns at once,
        so the next click is handled straight away instead of waiting for this file to finish loading, and a report
        that a newer click made out of date is cached but not shown."""
        entry = self._entry()
        key = (action, self.selected or self.location, entry.etag if entry else "", variant)
        self._action = action
        self._mark_actions()
        if cache and key in self._cache:
            self._set_pane(self._cache[key])
            return
        run = getattr(self._pane, method) if isinstance(method, str) else method
        self._captured: list[list[Any]] = []
        if self._widgets is None:
            run(*args, **kwargs)
            self._set_pane(self._captured)
            return
        name = Entry("file", *parse_location(self.selected)).name if self.selected else ""
        waiting = {"preview": f"Opening {name}…", "head": f"Reading the details of {name}…",
                   "document": f"Reading {name}…", "text": f"Reading the text of {name}…",
                   "download": f"Downloading {name}…",
                   "summary": "Reading every file below this folder…",
                   "zip": "Listing every file below this folder, then zipping them…",
                   "picks": f"Zipping the {_plural(len(self._picked), 'selected item')}…"}
        self._wait(waiting.get(action, "Working…"))
        loop = self._loop() if isinstance(method, str) and action in _BACKGROUND else None
        if loop is not None:
            self._task = loop.create_task(self._later(self._job, key, cache, method, self.selected, args, kwargs))
            return
        try:
            with self._progress:
                run(*args, **kwargs)
        finally:
            if self._execution() is not None:  # outside a kernel, clear_output() prints terminal escape codes
                self._progress.clear_output()
        self._keep(key, self._captured, cache)
        self._set_pane(self._captured)

    def _keep(self, key: tuple, reports: list[list[Any]], cache: bool) -> None:
        """Cache a report, but not an error note: trying again then asks AWS again."""
        if cache and reports and reports[0] and isinstance(reports[0][0], self.s3._Title):
            while len(self._cache) >= 24:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = reports

    @staticmethod
    def _loop() -> asyncio.AbstractEventLoop | None:
        """The kernel's event loop, which runs clicks and cells in a notebook; None elsewhere (a script, the
        tests), where reports load right away as the click waits."""
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    async def _later(self, job: int, key: tuple, cache: bool, method: str, selected: str, args: tuple,
                     kwargs: dict) -> None:
        """Wait, on the kernel's event loop, for a report loading on a worker thread, then show it unless the
        right pane has changed since (another file was clicked); it's cached either way."""
        try:
            reports = await asyncio.wrap_future(self._threads().submit(self._load, job, method, selected, args, kwargs))
            if reports is None:
                return
            self._keep(key, reports, cache)
            if job == self._job:
                self._set_pane(reports)
        except Exception as exc:  # a bug: still say so where the user is looking, and not in the kernel's log
            if job == self._job:
                self._fail(f"Something went wrong ({type(exc).__name__}: {exc}). Click the file again to try again.")

    def _threads(self) -> ThreadPoolExecutor:
        """The worker threads that background reports and listings run on."""
        if self._workers is None:
            self._workers = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="s3-explorer")
        return self._workers

    def _load(self, job: int, method: str, selected: str, args: tuple, kwargs: dict) -> list[list[Any]] | None:
        """On a worker thread: a report from an S3View of its own, which touches no widgets (no progress bar, and
        its reports are kept here). None when another click came before it started: it would only be thrown away."""
        if job != self._job:
            return None
        reports: list[list[Any]] = []
        view = self.s3.S3View(self.core, progress="off")
        view.use_html = self._pane.use_html
        view._show = lambda blocks: reports.append(self._trim(blocks, selected))
        getattr(view, method)(*args, **kwargs)
        return reports

    def _wait(self, text: str) -> None:
        """The right pane while a report loads: a spinner, what it's doing, and the outline of a report."""
        self._job += 1
        self._overview = None
        lines = "".join(f'<div class="s3x-sk" style="width:{width}%"></div>' for width in (92, 84, 88, 64, 76))
        self._content.value = (f'<div class="s3x-wait"><div class="s3x-wait-t"><span class="s3x-spin"></span>'
                               f'{html.escape(text)}</div><div class="s3x-sk s3x-t"></div><div class="s3x-sk-cards">'
                               + '<div class="s3x-sk"></div>' * 4 + f"</div>{lines}</div>")
        self._settings.layout.display = self._picks_panel.layout.display = "none"
        self._draw_pager(False)
        self._draw_expand(False)
        self._renew("right")

    def _busy(self, text: str) -> None:
        """The status bar while S3 is listing: a spinner and what it's doing."""
        if self._widgets is not None:
            self._status.value = f'<span class="s3x-at s3x-busy">{html.escape(text)}</span>'

    def _set_pane(self, reports: list[list[Any]], keep: bool = True) -> None:
        """Show reports on the right; the text explorer (no ipywidgets) shows them as the S3View would. keep=False
        is a note shown while a report loads."""
        self._job += 1
        self._overview = None
        if keep:
            self.shown = [block for blocks in reports for block in blocks]
            self._reports = reports
            for tree in self._trees():
                tree.unfold("all" if self._expand_all else "start")
        if self._widgets is None:
            for blocks in reports:
                type(self._pane)._show(self._pane, blocks)
            return
        self._content.value = "".join(self.s3._render_html(blocks, self._pane.max_rows) for blocks in reports)
        self._settings.layout.display = self._picks_panel.layout.display = "none"
        self._draw_pager(keep)
        self._draw_expand(keep)
        self._renew("right")

    # ------------------------------------------------------------------ navigation

    def _navigate(self, move: Callable[[], Folder], keep: str = "", same: bool = False) -> None:
        """Move (open / back / up / ...), then redraw the list, the path and the right pane. Another folder starts
        with an empty search box and only its first level listed (same=True, for ↻, keeps them); All / Folders /
        Files stays, like the sort."""
        self._busy("Listing…")
        move()
        self.selected, self._offset, self._action = "", 0, ""
        if not same:
            self._query, self._deep, self._all_types = "", False, False
            self._picked.clear()  # another folder: start a new selection
        if self.nav.focus and not same:
            self._kind = "all"  # so the file it opens is in the list
        if self._deep:
            self._busy("Listing everything below this folder…")
        folder = self._source()
        self._follow(folder)
        focus = self.nav.focus or keep
        if focus and folder.more and not folder.deep and all(e.uri != focus for e in folder.entries):
            self.nav.lookup(Entry("file", *parse_location(focus)).name)  # past what's listed: ask S3 for it
        if self._widgets is not None:
            self._quiet = True
            self._filter.value = self._query
            self._quiet = False
            self._draw_crumbs()
            self._draw_filters(folder)
            self._draw_rows(folder)
            self._renew("left")
        if focus and any(e.uri == focus for e in folder.entries):
            self._select(focus)
        elif self._widgets is None:
            self._set_pane([self._folder_blocks(folder, listing=True)])
        else:
            self._show_folder(folder)
        self._draw_status(folder)
        self._draw_picks()

    def _select(self, uri: str) -> None:
        """Show a file on the right, and the page of the list it's on."""
        self.selected = uri
        if self._widgets is not None:
            index = next((i for i, e in enumerate(self._visible) if e.uri == uri), -1)
            if index >= 0 and not self._offset <= index < self._offset + self.page_size:
                self._offset = index - index % self.page_size
                self._draw_rows(self._source())
                self._renew("left")
            self._mark_rows()
            self._draw_actions()
            self._draw_status(self._source())
        self._report("preview", "preview", uri)

    def _show_folder(self, folder: Folder) -> None:
        self.selected = ""
        self._mark_rows()
        self._draw_actions()
        self._show_overview(folder)
        self._draw_status(folder)

    def _show_overview(self, folder: Folder) -> None:
        """What the folder holds, on the right (a background listing redraws it as more comes in)."""
        self._set_pane([self._folder_blocks(folder)])
        self._overview = folder

    def _folder_blocks(self, folder: Folder, listing: bool = False) -> list[Any]:
        """The right pane when no file is chosen: what this folder holds, from the entries already listed (no
        extra AWS calls). listing=True adds the entries themselves, for the text explorer."""
        s3 = self.s3
        bucket, prefix = parse_location(folder.uri)
        title = breadcrumbs(folder.uri)[-1][0]
        blocks: list[Any] = [s3._Title(title if bucket else "Your buckets", folder.uri or "every bucket you can see")]
        if self.nav.notice:
            blocks.append(s3._Note(self.nav.notice, "warn"))
        if folder.error:
            blocks += [s3._Note(f"{explain_list_error(folder.error_code, bucket, prefix)} ({folder.error})", "warn")]
            return blocks
        stats = self._stats(folder)
        plus = "+" if folder.more else ""
        if not bucket:
            newest = max((e.modified for e in folder.entries if e.modified), default=None)
            blocks.append(s3._Cards([("Buckets", f"{len(folder.entries):,}")]
                                    + ([("Newest bucket", s3.human_age(newest))] if newest else [])))
            blocks.append(s3._Note("Click a bucket to open it. The “Every bucket” button above compares their size, "
                                   "cost and security settings." if folder.entries else
                                   "No buckets in this account. Type a bucket's path above to open one you "
                                   "can read in another account."))
        else:
            below = " below" if folder.deep else ""
            cards = [(f"Folders{below}", f"{stats.folders:,}{plus}")] if stats.folders else []
            if stats.files:
                cards += [(f"Files{below}", f"{stats.files:,}{plus}"),
                          ("Size of these files", s3.human_size(stats.size) + plus)]
            if stats.newest:
                cards.append(("Newest file", s3.human_age(stats.newest)))
            if stats.archived:
                cards.append(("Archived", f"{stats.archived:,}", "warn"))
            if cards:
                blocks.append(s3._Cards(cards))
            if self._lister is folder:
                what = (f"everything below this folder: these numbers cover the {_plural(stats.files, 'file')}"
                        if folder.deep else f"this folder: these numbers cover the {len(folder.entries):,} entries")
                blocks.append(s3._Note(f"Still listing {what} listed so far, and grow as more come in. The search "
                                       "over the list catches up too."))
            elif folder.more:
                if listing:  # the text view has no buttons: the calls that do what they do
                    more = (f" To list more, set x.nav.{'deep_limit' if folder.deep else 'list_limit'} = "
                            f"{2 * self._goal(folder):_} and run x.refresh(); x.open('s3://…') opens a file by its "
                            "path, however far down it is.")
                else:
                    more = f" “Load more from S3” at the end of the list lists the next {self._goal(folder):,}" + (
                        "." if folder.deep else "; to find a file past those, type the start of its name in the "
                        "search box and click “Look up”.")
                blocks.append(s3._Note(
                    (f"These numbers cover the first {stats.files:,} files below this folder." if folder.deep else
                     f"This folder has more than {len(folder.entries):,} entries, and these numbers cover the first "
                     f"{len(folder.entries):,}.") + more))
            if stats.archived:
                blocks.append(s3._Note(f"{_plural(stats.archived, 'file')} here {'is' if stats.archived == 1 else 'are'}"
                                       " in GLACIER or DEEP_ARCHIVE (❄ in the list): they can't be opened until "
                                       "they're restored.", "warn"))
            if not folder.entries:
                blocks.append(s3._Note("This folder is empty." if prefix else "This bucket is empty."))
            if stats.types:
                blocks.append(s3._Table(["Type", "Files", "Size"],
                                        [[ext, f"{n:,}", s3.human_size(size)] for ext, n, size in stats.types],
                                        title=f"File types {'below' if folder.deep else 'here'}", max_rows=0))
            if folder.entries and not listing:
                blocks.append(s3._Note(
                    "Everything below this folder is on the left, each under the folder it's in. Click a file to see "
                    "what's inside, or a folder to open it; the “What's in here” button above adds up every file, "
                    "with its cost." if folder.deep else
                    "Click a folder to open it, or a file to see what's inside. The sizes are for the files at this "
                    "level: “Include subfolders” over the list adds everything below, and the “What's in here” "
                    "button above adds it all up."))
        if listing:
            shown = self._shown_entries(folder)
            rows = [[f"{entry_icon(e, s3)} {e.key[len(prefix):] if folder.deep else e.name}"
                     f"{'/' if e.kind == 'folder' and not folder.deep else ''}{' ❄' if e.archived else ''}",
                     "" if e.is_folder else s3.human_size(e.size), s3.human_age(e.modified) if e.modified else ""]
                    for e in shown]
            blocks.append(s3._Table(["Name", "Size", "Modified" if bucket else "Created"], rows,
                                    title=("Below this folder" if folder.deep else "In this folder") if bucket
                                    else "Buckets"))
            searched = [f"“{self._query.strip()}”"] if self._query.strip() else []
            searched += [f"{self._kind} only"] if bucket and self._kind != "all" else []
            if searched:
                blocks.append(s3._Note(f"Showing {len(shown):,} of {len(folder.entries):,}: {', '.join(searched)}. "
                                       "x.filter() shows them all."))
            blocks.append(s3._Note("This is the text view: x.open('s3://…') moves around and x.filter('.csv') "
                                   "searches; in a Jupyter notebook with ipywidgets every row is clickable."))
        return blocks

    def _shown_entries(self, folder: Folder) -> list[Entry]:
        """The list's entries: the folder's, narrowed by the search box and All / Folders / Files, in the order of the
        column clicked."""
        kind = _KINDS[self._kind] if parse_location(folder.uri)[0] else "all"
        wanted = parse_filter(self._query)
        return [e for e in self._ordered(folder)
                if wanted.matches(e) and (kind == "all" or e.is_folder == (kind == "folders"))]

    def _ordered(self, folder: Folder) -> list[Entry]:
        """The folder's entries in the order of the column clicked, kept until the folder, the sort or what's listed
        changes, so a key typed in the search box only filters them. With "Include subfolders", Name sorts by path, so
        each folder comes before what's in it, and Size and Modified put the files first: the biggest or newest
        anywhere below."""
        key = (len(folder.entries), self._sort, self._descending)
        if not self._order or self._order[0] is not folder or self._order[1] != key:
            if folder.deep and self._sort == "name":
                entries = sort_entries(folder.entries, "path", self._descending)
            else:
                entries = sort_entries(folder.entries, self._sort, self._descending)
                if folder.deep:
                    entries = [e for e in entries if not e.is_folder] + [e for e in entries if e.is_folder]
            self._order = (folder, key, entries)
        return self._order[2]

    def _stats(self, folder: Folder) -> FolderStats:
        """folder_stats of what's listed, kept until more is listed (the status bar shows them after each key)."""
        if not self._counted or self._counted[0] is not folder or self._counted[1] != len(folder.entries):
            self._counted = (folder, len(folder.entries), folder_stats(folder.entries))
        return self._counted[2]

    # ------------------------------------------------------------------ widgets

    def _build(self) -> None:
        w = self._widgets

        def button(text: str, css: str, tip: str, on_click: Callable[[], None], **layout: Any) -> Any:
            b = w.Button(description=text, tooltip=tip, layout=w.Layout(**layout))
            for name in css.split():
                b.add_class(name)
            b.on_click(lambda _: self._guard(on_click))
            return b

        style = w.HTML(_CSS, layout=w.Layout(display="none"))
        # The icon buttons keep their glyph as the description: hidden by the style, which draws the icon instead.
        self._back_btn = button("←", "s3x-nav s3x-ic s3x-i-back", "Back", self.back)
        self._fwd_btn = button("→", "s3x-nav s3x-ic s3x-i-forward", "Forward", self.forward)
        self._up_btn = button("↑", "s3x-nav s3x-ic s3x-i-up", "Up one level", self.up)
        refresh = button("↻", "s3x-nav s3x-ic s3x-i-refresh", "List this folder again", self.refresh)
        navs = w.HBox([self._back_btn, self._fwd_btn, self._up_btn, refresh], layout=w.Layout(width="auto"))
        navs.add_class("s3x-navs")
        self._crumbs = w.HBox()
        self._crumbs.add_class("s3x-crumbs")
        self._path = w.Text(placeholder="s3://bucket/folder/ or an S3 console link, then Enter",
                            layout=w.Layout(flex="1 1 auto", display="none", width="auto"))
        self._path.add_class("s3x-path")
        # Go on Enter only (the box sends "submit"), not when it loses focus: clicking ✕ then cancels.
        self._path.on_msg(lambda _, content, __: content.get("event") == "submit" and self._guard(self._on_path))
        self._edit_btn = button("✎", "s3x-nav s3x-ic s3x-i-edit", "Type or paste a path", self._toggle_path)
        self._gear = button("⚙", "s3x-nav s3x-ic s3x-i-settings", "Settings: the biggest folder to zip, and where "
                            "zips go", self._toggle_settings)
        bar = w.HBox([navs, self._crumbs, self._path, self._edit_btn, self._gear], layout=w.Layout(width="100%"))
        bar.add_class("s3x-bar")

        self._filter = w.Text(continuous_update=True, layout=w.Layout(width="100%"))
        self._filter.add_class("s3x-search")
        self._filter.add_class("s3x-i-search")
        self._filter.observe(lambda _: self._quiet or self._guard(self._on_filter), "value")
        self._clear_btn = button("✕", "s3x-nav s3x-clear s3x-ic s3x-i-close", "Clear the search",
                                 lambda: self._set_query(""))
        search = w.HBox([self._filter, self._clear_btn], layout=w.Layout(width="100%"))
        search.add_class("s3x-searchrow")
        self._kind_btns = {
            kind: button(label, "s3x-seg", tip, lambda kind=kind: self._on_kind(kind))
            for kind, label, tip in (("all", "All", "Folders and files"), ("folders", "Folders", "Only the folders"),
                                     ("files", "Files", "Only the files"))
        }
        segs = w.HBox(list(self._kind_btns.values()), layout=w.Layout(width="auto"))
        segs.add_class("s3x-segs")
        self._deep_btn = button("Include subfolders", "s3x-chip s3x-deep s3x-ic s3x-i-below", "", self._on_deep)
        self._kinds = w.HBox([segs, self._deep_btn], layout=w.Layout(width="100%"))
        self._kinds.add_class("s3x-kinds")
        self._types = w.HBox(layout=w.Layout(width="100%"))
        self._types.add_class("s3x-types")
        self._chips: list[Any] = []  # a button per file type, reused as the types change
        self._chip_types: list[str] = []  # the type each chip stands for now
        self._more_types = button("", "s3x-chip s3x-more", "", self._on_more_types)
        self._finder = w.VBox([search, self._kinds, self._types], layout=w.Layout(width="100%", flex="0 0 auto"))
        self._finder.add_class("s3x-find")

        self._check_all = button("✓", " ".join(_CHECK), "Select everything listed", self._on_check_all)
        self._cols = {
            "name": button("Name", "s3x-col s3x-namecol", "Sort by name", lambda: self._on_sort("name"),
                           flex="1 1 auto", width="auto"),
            "size": button("Size", "s3x-col s3x-num", "Sort by size", lambda: self._on_sort("size"), width="76px"),
            "modified": button("Modified", "s3x-col s3x-num", "Sort by date", lambda: self._on_sort("modified"),
                               width="76px"),
        }
        head = w.HBox([self._check_all, *self._cols.values()], layout=w.Layout(width="100%", flex="0 0 auto"))
        head.add_class("s3x-head")
        self._rows_box = w.VBox(layout=w.Layout(width="100%", flex="0 0 auto"))
        self._rows_box.add_class("s3x-rows")
        self._pool: list[_Row] = []
        self._load_btn = button("Load more from S3", "s3x-link", "List more of this folder", self._on_load)
        self._lookup_btn = button("", "s3x-link", "Ask S3 for names starting with the search text", self._on_lookup)
        self._fix_btn = button("", "s3x-link", "", lambda: self._fix())  # what the note above it suggests
        self._fix: Callable[[], None] = lambda: None
        self._foot_note = w.HTML(layout=w.Layout(width="100%"))
        self._foot = w.HBox([self._foot_note, self._fix_btn, self._load_btn, self._lookup_btn],
                            layout=w.Layout(width="100%", flex="0 0 auto"))
        self._foot.add_class("s3x-foot")
        self._head = head
        # The glyphs are hidden by the style, which draws the icons instead.
        self._page_btns = {name: button(glyph, f"s3x-nav s3x-ic s3x-i-{name}", tip, lambda name=name: self._on_page(name))
                           for name, glyph, tip in _PAGE_BUTTONS}
        self._range = w.HTML(layout=w.Layout(flex="1 1 auto", min_width="0"))
        self._pages = w.HBox([self._range, *self._page_btns.values()],
                             layout=w.Layout(width="100%", flex="0 0 auto", display="none"))
        self._pages.add_class("s3x-pages")

        self._actions = w.HBox(layout=w.Layout(width="100%", flex="0 0 auto"))
        self._actions.add_class("s3x-actions")
        self._expand_btn = button("▾ Expand all", "s3x-act s3x-expand", "", self._on_expand)
        self._progress = w.Output(layout=w.Layout(width="100%", flex="0 0 auto"))
        self._content = w.HTML(layout=w.Layout(width="100%", flex="0 0 auto"))
        self._pager = w.HBox(layout=w.Layout(width="100%", flex="0 0 auto", display="none"))
        self._pager.add_class("s3x-pager")
        self._settings = self._build_settings(button)
        self._picks_panel = self._build_picks(button)
        self._picks_note = w.HTML(layout=w.Layout(flex="1 1 auto", min_width="0"))
        self._picks_bar = w.HBox([self._picks_note,
                                  button("Clear", "s3x-quiet", "Unselect everything", self._clear_picks),
                                  button("⬇ Download selected", "s3x-primary", "Download what's selected as one "
                                         ".zip: see what goes in and name it first", self._open_picks)],
                                 layout=w.Layout(width="100%", flex="0 0 auto", display="none"))
        self._picks_bar.add_class("s3x-picks")
        self._side = w.VBox([self._finder], layout=w.Layout(width="42%", min_width="300px", flex="0 0 auto"))
        self._side.add_class("s3x-side")
        self._body = w.HBox(layout=w.Layout(width="100%", height=f"{self.height}px"))
        self._left = self._right = None
        self._renew("left", "right")
        self._status = w.HTML(layout=w.Layout(width="100%"))
        self._status.add_class("s3x-status")
        self._app = w.VBox([style, bar, self._body, self._status], layout=w.Layout(width="100%"))
        self._app.add_class("s3x")
        self._act_buttons: dict[str, Any] = {}

    def _build_picks(self, button: Callable[..., Any]) -> Any:
        """The panel ⬇ Download .zip in the selection bar opens on the right, under what's selected: the zip's name
        (Enter downloads too), Download and Cancel, and the list of what goes in."""
        w = self._widgets
        self._picks_name = w.Text(layout=w.Layout(width="280px"))
        self._picks_name.on_msg(lambda _, content, __: content.get("event") == "submit" and self._guard(self._save_picks))
        self._picks_where = w.HTML()
        name = w.HBox([w.Label("Save as", layout=w.Layout(width="64px")), self._picks_name, self._picks_where],
                      layout=w.Layout(width="100%"))
        self._picks_go = button("⬇ Download", "s3x-primary", "Make the zip, after checking the size, disk space and "
                                "read access", self._save_picks)
        cancel = button("Cancel", "s3x-act", "Close this and keep the selection", self._close_picks)
        actions = w.HBox([self._picks_go, cancel], layout=w.Layout(width="100%"))
        for row in (name, actions):
            row.add_class("s3x-setting")
        self._picks_msg = w.HTML()
        self._picks_list = w.HTML(layout=w.Layout(width="100%"))
        panel = w.VBox([name, actions, self._picks_msg, self._picks_list],
                       layout=w.Layout(width="100%", flex="0 0 auto", display="none"))
        panel.add_class("s3x-settings")
        return panel

    def _build_settings(self, button: Callable[..., Any]) -> Any:
        """The ⚙ Settings panel, shown on the right under its title: a text box per setting, Save and Close.
        Enter in a box saves too."""
        w = self._widgets
        self._set_size = w.Text(placeholder="100MB", layout=w.Layout(width="140px"))
        self._set_files = w.Text(placeholder="10,000", layout=w.Layout(width="140px"))
        self._set_folder = w.Text(placeholder=".", layout=w.Layout(width="200px"))
        rows = []
        for label, box, hint in (
            ("Biggest zip", self._set_size, "500MB, 2GB, …: a bigger folder isn't zipped"),
            ("Most files", self._set_files, "files in one zip"),
            ("Save zips in", self._set_folder, ". is the notebook's folder"),
        ):
            box.on_msg(lambda _, content, __: content.get("event") == "submit" and self._guard(self._save_settings))
            hint_label = w.HTML(f'<span class="s3x-hint">{html.escape(hint)}</span>')
            row = w.HBox([w.Label(label, layout=w.Layout(width="96px")), box, hint_label], layout=w.Layout(width="100%"))
            row.add_class("s3x-setting")
            rows.append(row)
        save = button("Save", "s3x-act", "Use these settings", self._save_settings, width="auto")
        cancel = button("Close", "s3x-act", "Close the settings", self._close_settings, width="auto")
        actions = w.HBox([save, cancel], layout=w.Layout(width="100%"))
        actions.add_class("s3x-setting")
        self._set_note = w.HTML()
        panel = w.VBox([*rows, actions, self._set_note], layout=w.Layout(width="100%", flex="0 0 auto", display="none"))
        panel.add_class("s3x-settings")
        return panel

    def _renew(self, *sides: str) -> None:
        """Put the list ('left') or the report ('right') in a new box. Widgets can't scroll without JavaScript, but a
        new box starts at the top, so a folder you open shows its first rows and a new report shows its title."""
        w, old = self._widgets, [self._left, self._right]
        if "left" in sides:  # under the search box and its buttons, which stay where they are
            self._left = w.VBox([self._head, self._rows_box, self._foot],
                                layout=w.Layout(width="100%", flex="1 1 auto", min_height="0", overflow="auto"))
            self._left.add_class("s3x-list")
            self._side.children = (self._finder, self._left, self._pages, self._picks_bar)
        if "right" in sides:
            self._right = w.VBox([self._actions, self._progress, self._content, self._settings, self._picks_panel,
                                  self._pager],
                                 layout=w.Layout(flex="1 1 auto", width="auto", min_width="0", overflow="auto"))
            self._right.add_class("s3x-right")  # min_width 0: wide tables scroll
        self._body.children = (self._side, self._right)
        for box in old:
            if box is not None and box not in (self._left, self._right):
                box.close()  # closes the box only; the widgets in it live on in the new one

    def _guard(self, handler: Callable[[], None]) -> None:
        """Run a widget callback; anything that goes wrong becomes a note on the right. (A callback's
        exception would otherwise go to Jupyter's log, where nobody sees it, and the click would do nothing.)"""
        try:
            handler()
        except (ClientError, BotoCoreError, *self.s3._DATA_ERRORS) as exc:
            code = exc.response.get("Error", {}).get("Code", "Error") if isinstance(exc, ClientError) else ""
            self._fail(f"{code or type(exc).__name__}: {exc}")
        except KeyboardInterrupt:  # the kernel's stop button, during a long scan
            self._fail("Stopped. Click the file or button again to start over.")
        except Exception as exc:  # a bug: still say so where the user is looking
            self._fail(f"Something went wrong ({type(exc).__name__}: {exc}). Press ↻ to try again.")
        finally:
            if "…" in self._status.value:  # "Listing…" that never finished
                folder = (self.nav._below if self._deep else self.nav._cache).get(self.nav.location)
                if folder is not None:
                    self._draw_status(folder)
                else:
                    self._status.value = '<span class="s3x-at">Stopped</span>'

    def _draw_crumbs(self) -> None:
        w = self._widgets
        crumbs = breadcrumbs(self.nav.location)
        if len(crumbs) > 6:  # All buckets › bucket › … › the last three
            crumbs = crumbs[:2] + [("…", crumbs[-4][1])] + crumbs[-3:]
        old = self._crumbs.children
        children: list[Any] = []
        for i, (label, uri) in enumerate(crumbs):
            last = i == len(crumbs) - 1
            b = w.Button(description=label, tooltip=("Show what's in this folder" if last else f"Open {uri}")
                         if uri else ("Show your buckets" if last else "Open your list of buckets"),
                         layout=w.Layout(width="auto"))
            b.add_class("s3x-crumb")
            if last:
                b.add_class("s3x-here")
            b.on_click(lambda _, uri=uri, last=last: self._guard(
                lambda: self._show_folder(self._source()) if last else self.open(uri)))
            children.append(b)
            if not last:
                children.append(w.Label("›"))
                for name in ("s3x-sep", "s3x-ic", "s3x-i-chevron"):
                    children[-1].add_class(name)
        self._crumbs.children = children
        for widget in old:
            widget.close()
        self._back_btn.disabled = not self.nav.can_back
        self._fwd_btn.disabled = not self.nav.can_forward
        self._up_btn.disabled = not self.nav.location
        bucket = parse_location(self.nav.location)[0]
        self._cols["size"].layout.display = None if bucket else "none"  # buckets have no size: their names get the room
        self._draw_side()
        self._draw_header()

    def _draw_header(self) -> None:
        bucket = parse_location(self.nav.location)[0]
        labels = {"name": "Name", "size": "Size", "modified": "Modified" if bucket else "Created"}
        for column, b in self._cols.items():
            on = column == self._sort
            b.description = labels[column] + ((" ↓" if self._descending else " ↑") if on else "")
            b._dom_classes = ("s3x-col", "s3x-namecol" if column == "name" else "s3x-num") + (
                ("s3x-on",) if on else ())

    def _draw_side(self) -> None:
        """The list's state, for the style: on the list of buckets (no sizes, nothing to select), and while
        something is selected (every row shows its checkbox)."""
        bucket = parse_location(self.nav.location)[0]
        self._side._dom_classes = ("s3x-side",) + (() if bucket else ("s3x-buckets",)) + (
            ("s3x-picking",) if self._picked else ())

    def _draw_filters(self, folder: Folder) -> None:
        """Above the list: the search box, All / Folders / Files with how many of each match it, Include subfolders,
        and a chip for each file type here (the most common first), lit when the search asks for that type."""
        w, s3 = self._widgets, self.s3
        bucket = parse_location(self.nav.location)[0]
        self._filter.placeholder = "Search: a name, .csv, *.parquet, part-0*" if bucket else "Search your buckets"
        self._clear_btn.layout.display = None if self._query.strip() else "none"
        self._kinds.layout.display = self._types.layout.display = None if bucket else "none"
        if not bucket:
            return
        wanted, plus = parse_filter(self._query), "+" if folder.more else ""
        named = [e for e in folder.entries if wanted.named(e)]
        matching = [e for e in named if wanted.typed(e)]
        folders = sum(e.is_folder for e in matching)
        for kind, label, count in (("all", "All", len(matching)), ("folders", "Folders", folders),
                                   ("files", "Files", len(matching) - folders)):
            b = self._kind_btns[kind]
            b.description = f"{label} {count:,}{plus}"
            b._dom_classes = ("s3x-seg", "s3x-on") if kind == self._kind else ("s3x-seg",)
        self._deep_btn._dom_classes = ("s3x-chip", "s3x-deep", "s3x-ic", "s3x-i-below") + (
            ("s3x-on",) if self._deep else ())
        self._deep_btn.tooltip = (
            "Showing everything below this folder; click to show only its own folders and files" if self._deep else
            f"List everything below this folder (the first {self.nav.deep_limit:,} files), not only its first level, "
            "to search all of it")

        types = count_types(named)
        shown = types
        if not self._all_types and len(types) > _TYPE_CHIPS + 1:
            shown = [t for i, t in enumerate(types) if i < _TYPE_CHIPS or t[0] in wanted.types]
        while len(self._chips) < len(shown):
            chip = w.Button(layout=w.Layout(width="auto"))
            chip.on_click(lambda _, i=len(self._chips): self._guard(lambda: self._toggle_type(self._chip_types[i])))
            self._chips.append(chip)
        self._chip_types = [kind for kind, _, _ in shown]
        for chip, (kind, files, size) in zip(self._chips, shown):
            on = kind in wanted.types
            with chip.hold_sync():
                chip.description = f"{s3._file_icon('x.' + kind)} .{kind} {files:,}"
                chip.tooltip = (f"{_plural(files, f'.{kind} file')}, {s3.human_size(size)}"
                                f"{' so far' if folder.more else ''}: " + (
                                    "click to show every type again" if on else
                                    "click to show only these (click another type to add it)"))
                chip._dom_classes = ("s3x-chip", "s3x-on") if on else ("s3x-chip",)
        children = self._chips[: len(shown)]
        hidden = len(types) - len(shown)
        if hidden or (self._all_types and len(types) > _TYPE_CHIPS + 1):
            self._more_types.description = f"+{hidden} more" if hidden else "Fewer"
            self._more_types.tooltip = "Show every file type here" if hidden else "Show only the most common types"
            children.append(self._more_types)
        self._types.children = tuple(children)
        if self._kind == "folders" or (len(types) < 2 and not wanted.types):  # one type: its chip would change nothing
            self._types.layout.display = "none"

    def _draw_rows(self, folder: Folder) -> None:
        """Fill the list with a page of the folder's entries, filtered and sorted, reusing row widgets so redraws are
        quick; and the bar under it, which says which rows these are and moves to the others."""
        w = self._widgets
        entries = self._shown_entries(folder)
        self._visible, self._folder = entries, folder
        if self._offset >= len(entries):  # fewer match now: the last page
            self._offset = max(0, len(entries) - 1) // self.page_size * self.page_size
        shown = entries[self._offset: self._offset + self.page_size]
        while len(self._pool) < len(shown):
            self._pool.append(_Row(w, self._on_row, self._on_check))
        base = parse_location(folder.uri)[1] if folder.deep else None
        for row, entry in zip(self._pool, shown):
            self._fill(row, entry, base)
        self._rows_box.children = tuple(row.box for row in self._pool[: len(shown)])
        key = (folder.uri, tuple(e.uri for e in shown))
        if key[1] and key != self._rows_key[:2]:
            self._rows_at = time.monotonic()
        self._rows_key = key
        self._draw_pages(folder, len(entries))
        self._draw_foot(folder, entries, len(shown))
        self._draw_check_all()

    def _draw_pages(self, folder: Folder, total: int) -> None:
        """The bar under a list longer than a page: which rows these are, of how many, and « ‹ › » for the first,
        previous, next and last page."""
        size = self.page_size
        self._pages.layout.display = None if total > size else "none"
        if total <= size:
            return
        first, last = self._offset + 1, min(self._offset + size, total)
        page, pages = self._offset // size + 1, -(-total // size)
        plus = "+" if folder.more else ""
        self._range.value = (f'<span title="Page {page:,} of {pages:,}{plus}"><b>{first:,}–{last:,}</b> of '
                             f'{total:,}{plus}</span>')
        for name, b in self._page_btns.items():
            b.disabled = first == 1 if name in ("first", "previous") else last >= total

    def _fill(self, row: _Row, entry: Entry, base: str | None = None) -> None:
        """Show an entry in a row. In a list of everything below a folder (`base`, its prefix), the folder the entry
        is in goes under its name."""
        row.entry = entry
        s3 = self.s3
        if entry.kind == "file":
            tip = f"{entry.key}\n{s3.human_size(entry.size)} · modified {_fmt_dt(entry.modified)}"
            tip += f"\n{entry.storage_class}: restore it before it can be opened" if entry.archived else (
                f" · {entry.storage_class}" if entry.storage_class not in ("", "STANDARD") else "")
            size, age = s3.human_size(entry.size), s3.human_age(entry.modified) if entry.modified else ""
        elif entry.kind == "folder":
            tip, size, age = f"Open {entry.key}", "", ""
        else:
            tip = f"Open the bucket {entry.bucket}" + (f"\ncreated {_fmt_dt(entry.modified)}" if entry.modified else "")
            size, age = "", s3.human_age(entry.modified) if entry.modified else ""
        name = entry.name or "(no name)"
        with row.button.hold_sync():
            row.button.description = f"{entry_icon(entry, s3)}  {name}{'  ❄' if entry.archived else ''}"
            row.button.tooltip = tip
            row.button._dom_classes = self._row_classes(entry)
        row.check._dom_classes = _CHECK + (("s3x-on",) if entry.uri in self._picked else ())
        row.size.value, row.age.value = size, age
        if base is not None:
            where = _parent_prefix(entry.key[:-1] if entry.kind == "folder" else entry.key)[len(base):]
            row.where.value = where.rstrip("/") or "in this folder"
        row.where.layout.display = None if base is not None else "none"
        row.box._dom_classes = ("s3x-r", "s3x-r2") if base is not None else ("s3x-r",)

    def _row_classes(self, entry: Entry) -> tuple[str, ...]:
        """A row's button: lit for the file shown on the right, tinted when it's selected."""
        return ("s3x-row",) + (("s3x-on",) if entry.uri == self.selected else ()) + (
            ("s3x-picked",) if entry.uri in self._picked else ())

    def _mark_rows(self) -> None:
        if self._widgets is None:
            return
        for row in self._pool[: len(self._rows_box.children)]:
            if row.entry is not None:
                row.button._dom_classes = self._row_classes(row.entry)
                row.check._dom_classes = _CHECK + (("s3x-on",) if row.entry.uri in self._picked else ())

    def _draw_foot(self, folder: Folder, entries: list[Entry], shown: int) -> None:
        """At the end of the list: Load more from S3 and Look up for a folder bigger than what's listed, or why the
        list is empty and what to do."""
        text, esc = self._query.strip(), html.escape
        bucket = parse_location(folder.uri)[0]
        busy = self._lister is folder  # listing the rest in the background: it'll be in the list in a moment
        files = sum(not e.is_folder for e in folder.entries) if folder.deep else 0
        count = f"{_plural(files, 'file')} below" if folder.deep else f"{len(folder.entries):,} entries"
        if folder.deep:
            self._load_btn.description = f"Load more from S3 (the first {files:,} files below are listed)"
        else:
            self._load_btn.description = f"Load more from S3 (the first {len(folder.entries):,} are listed)"
        self._load_btn.tooltip = f"List the next {self._goal(folder):,}"
        last_page = self._offset + shown >= len(entries)
        self._load_btn.layout.display = None if folder.more and not busy and last_page else "none"
        wanted = parse_filter(text)
        lookup = (folder.more and not busy and not folder.deep and len(wanted.words) == 1 and not wanted.patterns
                  and not wanted.types and "/" not in text)
        self._lookup_btn.description = f"Look up names starting with “{text}” in S3"
        self._lookup_btn.layout.display = None if lookup else "none"
        note, fix = "", None
        among = f" yet ({count} listed so far)" if busy else f" among the first {count}" if folder.more else ""
        if folder.error:
            note = "Couldn't list this folder; the note on the right says why."
        elif self._list_error and self._list_error[0] is folder and folder.more and not busy and last_page:
            note = f"S3 stopped listing this folder ({esc(self._list_error[1])}). Load more from S3 tries again."
        elif not folder.entries:
            note = ("Nothing below this folder." if folder.deep else "This folder is empty.") if bucket else "No buckets."
        elif not entries:
            others = filter_entries(folder.entries, text)  # of either kind
            if bucket and self._kind != "all" and others:
                note = f"No {self._kind} {f'match “{esc(text)}”' if text else 'here'}{among}."
                other = "file" if self._kind == "folders" else "folder"
                fix = (f"Show the {_plural(len(others), other)}", lambda: self._on_kind("all"))
            else:
                note = f"Nothing here matches “{esc(text)}”{among}."
                if bucket and not folder.deep:
                    fix = ("Search the subfolders too", self._on_deep)
        elif last_page and folder.more and (text or self._kind != "all") and not busy:
            note = f"These are the matches {among.strip()}; this folder has more."
        if busy and not folder.error and not entries and folder.entries:
            note += " Still listing the rest…"
        self._fix = fix[1] if fix else (lambda: None)
        self._fix_btn.description = fix[0] if fix else ""
        self._fix_btn.layout.display = None if fix else "none"
        self._foot_note.value = f'<div class="s3x-empty">{note}</div>' if note else ""
        self._foot_note.layout.display = None if note else "none"

    def _draw_actions(self) -> None:
        """The buttons above the right pane: what you can do with the chosen file, or with this folder."""
        w = self._widgets
        if w is None:
            return
        bucket, prefix = parse_location(self.nav.location)
        if self.selected:
            name = Entry("file", *parse_location(self.selected)).name
            actions = [("preview", "👁️ Preview", "What's inside the file"),
                       ("head", "🏷️ Details", "Size, dates, storage class, metadata and tags")]
            kind = _extension(name).split(".")[0]
            if kind in _DOCUMENTS:
                actions.append(("document", "📖 Read all", "Every page as it looks, 20 at a time (click a page to see it "
                                "full size)" if kind == "pdf" else "The whole document, page by page"))
            if kind == "pdf":
                actions.append(("text", "📄 Text", f"The words of every page, {_TEXT_PAGES} at a time, laid out to read: "
                                "headings, paragraphs and lists, without the running headers and footers"))
            actions += [("download", "⬇ Download", "Save a copy in this notebook's folder"),
                        ("open", "↗ Open in new tab", "The file in a new browser tab: PDFs, pictures, sound, video "
                         "and text show there, other files download. The link works for an hour after you click the "
                         "file (right-click to copy it for someone without AWS access)"),
                        ("close", "✕", "Close the file and show this folder")]
        elif bucket:
            actions = [("summary", "📊 What's in here", "Every file below this folder: sizes, types, cost and "
                        "findings (reads the whole listing, so big folders take a while)")]
            if not prefix:
                actions.append(("bucket_info", "🛡️ Bucket settings", "Versioning, encryption, lifecycle, policy, risks"))
            actions.append(("zip", "⬇ Download .zip", f"Everything below this folder as one .zip on the notebook's disk, "
                            f"if it's no bigger than {self._zip_limit()} (⚙ changes that)"))
        else:
            actions = [("overview", "🪣 Every bucket", "Each bucket's size, cost and security warnings")]
        old = self._actions.children
        self._act_buttons = {}
        for action, label, tip in actions:
            if action == "open":  # a link, so the browser opens the tab (a button's click only reaches Python)
                link = self._open_link(label, tip)
                if link:
                    self._act_buttons[action] = w.HTML(link, layout=w.Layout(width="auto"))
                continue
            b = w.Button(description=label, tooltip=tip, layout=w.Layout(width="auto"))
            b.add_class("s3x-act")
            if action == "close":
                for name in ("s3x-close", "s3x-ic", "s3x-i-close"):
                    b.add_class(name)
            b.on_click(lambda _, action=action: self._guard(lambda: self._on_action(action)))
            self._act_buttons[action] = b
        self._draw_expand(False)
        for widget in old:
            if widget is not self._expand_btn:
                widget.close()
        self._action = "preview" if self.selected else ""
        self._mark_actions()

    def _open_link(self, label: str, tip: str) -> str:
        """↗ Open in new tab: a link to the chosen file that looks like the buttons beside it, presigned (for an
        hour) to show the file in the tab rather than save it. Empty when it can't be signed (no credentials)."""
        try:
            url = self.core.presigned_url(self.selected, inline=True)
        except (ClientError, BotoCoreError):
            return ""
        esc = html.escape
        return (f'<a class="s3x-act" href="{esc(url)}" target="_blank" rel="noopener noreferrer" '
                f'title="{esc(tip)}">{esc(label)}</a>')

    def _draw_expand(self, show: bool = True) -> None:
        """▾ Expand all, at the end of the buttons above the right pane (beside ✕), while the pane shows a JSON file
        with something collapsed in it, or with every part expanded by it."""
        acts = [b for action, b in self._act_buttons.items() if action != "close"]
        close = [b for action, b in self._act_buttons.items() if action == "close"]
        expand = show and any(tree.folded for tree in self._trees())
        self._actions.children = tuple(acts + [self._expand_btn] * expand + close)
        self._expand_btn._dom_classes = ("s3x-act", "s3x-expand") + (("s3x-on",) if self._expand_all else ())
        self._expand_btn.tooltip = (
            "Collapse the JSON back to its first levels, here and in the next files you open" if self._expand_all else
            "Expand every object and array in this JSON, and in the next files you open, instead of clicking them "
            "one by one (long strings stay collapsed: click one to read it)")

    def _trees(self) -> list[Any]:
        """The JSON trees in the report on the right (a .json file's preview)."""
        return [block for block in self.shown if isinstance(block, self.s3._JsonTree)]

    def _mark_actions(self) -> None:
        for action, b in getattr(self, "_act_buttons", {}).items():
            if action not in ("open", "close"):
                b._dom_classes = ("s3x-act", "s3x-on") if action == self._action else ("s3x-act",)

    def _draw_status(self, folder: Folder) -> None:
        """The bar at the bottom: what's listed (and how much of it matches the search), and where you are. While the
        rest of the folder is listed in the background, a spinner and "Listing…" in front."""
        if self._widgets is None:
            return
        s3, esc = self.s3, html.escape
        bucket = parse_location(folder.uri)[0]
        if folder.error:
            left = "Couldn't list this folder"
        elif not bucket:
            left = _plural(len(folder.entries), "bucket")
        else:
            stats = self._stats(folder)
            plus = "+" if folder.more else ""
            parts = [f"{stats.folders:,}{plus} folder{'' if stats.folders == 1 and not plus else 's'}"] if stats.folders else []
            if stats.files:
                parts += [f"{stats.files:,}{plus} file{'' if stats.files == 1 and not plus else 's'}",
                          s3.human_size(stats.size) + (" so far" if plus else "")]
            left = ("Below this folder: " if folder.deep and parts else "") + (" · ".join(parts) or "Empty")
        text = self._query.strip()
        if not folder.error and (text or (bucket and self._kind != "all")):
            left += f" · {len(self._visible):,} match “{esc(text)}”" if text else f" · {self._kind} only"
            size = sum(e.size or 0 for e in self._visible if not e.is_folder)
            left += f" ({s3.human_size(size)})" if size else ""
        here = self.selected or folder.uri
        right = f'<code title="Click to select, then copy">{esc(here)}</code>' if here else ""
        busy = self._lister is folder and not folder.error
        self._status.value = (f'<span class="s3x-at{" s3x-busy" if busy else ""}">{"Listing… " if busy else ""}{left}'
                              f'</span>{right}')

    # ------------------------------------------------------------------ events

    def _on_row(self, row: _Row) -> None:
        if row.entry is None or time.monotonic() - self._rows_at < _CLICK_GRACE:
            return  # a double click, or a click queued while the list was changing: it was meant for the old rows
        entry = row.entry
        self._guard(lambda: self.open(entry.uri) if entry.is_folder else self._click_file(entry))

    def _click_file(self, entry: Entry) -> None:
        if entry.uri == self.selected and self._action == "preview":
            return
        self._select(entry.uri)

    def _on_action(self, action: str) -> None:
        uri = self.selected or self.nav.location
        if action == "close":
            self._show_folder(self._source())
        elif action == "preview":
            self._report("preview", "preview", uri)
        elif action == "head":
            self._report("head", "head", uri)
        elif action == "document" and _extension(Entry("file", *parse_location(uri)).name).split(".")[0] == "pdf":
            self._show_pages(1)
        elif action == "text":
            self._show_pages(1, "text")
        elif action == "document":
            self._report("document", "document", uri)
        elif action == "download":
            self._report("download", "download", uri, cache=False)
        elif action == "summary":
            self._report("summary", "summary", uri)
        elif action == "bucket_info":
            self._report("bucket_info", "bucket_info", parse_location(uri)[0])
        elif action == "overview":
            self._report("overview", "overview")
        elif action == "zip":
            self._report("zip", self._zip, uri, cache=False)

    # ------------------------------------------------------------------ a folder as a .zip, and ⚙ Settings

    def _zip_limit(self) -> str:
        """The zip limits in words: '100.0 MB and 10,000 files'."""
        try:
            size = self.s3.human_size(self.s3.parse_size(self.zip_max_size))
        except (TypeError, ValueError):
            size = str(self.zip_max_size)
        return f"{size} and {self.zip_max_files:,} files"

    def _zip(self, target: str | list[Any], path: str = "") -> None:
        """⬇ Download .zip, for a folder (`target` is its uri) or what's selected (a list, and the path it goes to):
        s3's download_zip with the limits from ⚙ Settings. It checks the size, file count, disk space, memory and
        read access first, and writes nothing when one fails."""
        if not path:
            bucket, prefix = parse_location(target)
            name = (prefix.rstrip("/").rsplit("/", 1)[-1] or bucket) + ".zip"
            path = os.path.join(os.path.expanduser(self.zip_folder.strip() or "."), name)
        folder = os.path.dirname(path)
        if folder and folder != ".":
            os.makedirs(folder, exist_ok=True)
        self._pane.download_zip(target, os.path.normpath(path),
                                max_size=self.zip_max_size, max_files=self.zip_max_files)
        report = self._captured[-1] if self._captured else []
        refused = any(card[:2] == ("Can download", "no") for block in report if isinstance(block, self.s3._Cards)
                      for card in block.items)
        if refused:  # after the line that says why
            at = next((i + 1 for i, block in enumerate(report) if isinstance(block, self.s3._Note)), len(report))
            fix = ("⚙ at the top right raises them" if isinstance(target, str) else
                   "untick some files, or raise the limits with ⚙ at the top right")
            again = "⬇ Download .zip" if isinstance(target, str) else "⬇ Download selected"
            report.insert(at, self.s3._Note(f"If it's over a limit, {fix} (now {self._zip_limit()}); then click "
                                            f"{again} again."))

    # ------------------------------------------------------------------ selecting files to download as one .zip

    def _on_check(self, row: _Row) -> None:
        """A row's checkbox: select it, or unselect it."""
        entry = row.entry
        if entry is None or entry.kind == "bucket":
            return
        if self._picked.pop(entry.uri, None) is None:
            self._picked[entry.uri] = entry
        row.check._dom_classes = _CHECK + (("s3x-on",) if entry.uri in self._picked else ())
        row.button._dom_classes = self._row_classes(entry)
        self._draw_picks()

    def _on_check_all(self) -> None:
        """The header's checkbox: select everything the list shows (with a search, what matches it, past "Show
        more" too), or unselect it when it's all selected already."""
        shown = [e for e in self._visible if e.kind != "bucket"]
        if shown and all(e.uri in self._picked for e in shown):
            for entry in shown:
                self._picked.pop(entry.uri, None)
        else:
            self._picked.update((e.uri, e) for e in shown)
        self._mark_rows()
        self._draw_picks()

    def _clear_picks(self) -> None:
        self._picked.clear()
        self._mark_rows()
        self._draw_picks()

    def _draw_check_all(self) -> None:
        """The header's checkbox: empty, ticked when everything listed is selected, or a dash for some of it."""
        if self._widgets is None:
            return
        shown = [e for e in self._visible if e.kind != "bucket"]
        on = sum(e.uri in self._picked for e in shown)
        if shown and on == len(shown):
            self._check_all._dom_classes, tip = _CHECK + ("s3x-on",), "Unselect everything listed"
        elif on:
            self._check_all._dom_classes = ("s3x-check", "s3x-ic", "s3x-i-minus", "s3x-on")
            tip = f"Select all {len(shown):,} listed"
        else:
            self._check_all._dom_classes, tip = _CHECK, f"Select all {len(shown):,} listed"
        self._check_all.tooltip = tip
        self._check_all.layout.display = None if shown else "none"
        self._draw_side()

    def _picks_summary(self) -> tuple[list[Entry], int, int]:
        """(the selected files, how many folders are selected, the files' bytes)."""
        files = [e for e in self._picked.values() if not e.is_folder]
        return files, len(self._picked) - len(files), sum(e.size or 0 for e in files)

    def _draw_picks(self) -> None:
        """After the selection changed: the bar under the list (how many, how big, Clear and ⬇ Download .zip), the
        header's checkbox, and the panel on the right if it's open."""
        if self._widgets is None:
            return
        files, folders, size = self._picks_summary()
        if self._picked:
            more = f" + {_plural(folders, 'folder')}" if folders else ""
            self._picks_note.value = (f'<span class="s3x-picks-n">{len(self._picked):,} selected</span>'
                                      f'<span class="s3x-picks-s">{self.s3.human_size(size)}{more}</span>')
        self._picks_bar.layout.display = None if self._picked else "none"
        self._draw_check_all()
        if self._picks_panel.layout.display != "none":
            if self._picked:
                self._open_picks(keep_name=True)
            else:
                self._close_picks()

    def _picks_layout(self) -> tuple[str, str, str]:
        """(bucket, the folder the selected entries share, the zip's name without .zip), as download_zip names it."""
        bucket = parse_location(self.nav.location)[0]
        base, name = self.s3._zip_layout(bucket, [e.key for e in self._picked.values()])
        return bucket, base, name

    def _picks_default(self) -> str:
        """The zip's name: download_zip's ('churn-12-files.zip'), with -2, -3, ... when that file is already there."""
        name = self._picks_layout()[2]
        folder = os.path.expanduser(self.zip_folder.strip() or ".")
        number, candidate = 1, f"{name}.zip"
        while os.path.exists(os.path.join(folder, candidate)):
            number += 1
            candidate = f"{name}-{number}.zip"
        return candidate

    def _open_picks(self, keep_name: bool = False) -> None:
        """⬇ Download .zip in the selection bar: on the right, what goes in the zip and how big it is, its name,
        and the button that makes it. Nothing is downloaded until that's clicked."""
        s3 = self.s3
        files, folders, size = self._picks_summary()
        bucket, base, _ = self._picks_layout()
        try:
            limit = s3.parse_size(self.zip_max_size)
        except (TypeError, ValueError):
            limit = None
        over = limit is not None and size > limit
        parts = [_plural(len(files), "file")] if files else []
        parts += [_plural(folders, "folder")] if folders else []
        plus = "+" if folders else ""
        cards: list[tuple[str, ...]] = [("Files", f"{len(files):,}{plus}"),
                                        ("Size", s3.human_size(size) + plus, *(("warn",) if over else ()))]
        cards += [("Limit", s3.human_size(limit))] if limit is not None else []
        blocks: list[Any] = [s3._Title(f"Download {' and '.join(parts)} as one .zip", f"from s3://{bucket}/{base}"),
                             s3._Cards(cards)]
        if over:
            blocks.append(s3._Note(f"That's over the {s3.human_size(limit)} limit for one zip, so it won't be made: "
                                   "untick some files, or raise the limit with ⚙ at the top right.", "warn"))
        if folders:
            blocks.append(s3._Note(f"The {'folder goes' if folders == 1 else 'folders go'} in with everything below "
                                   f"{'it' if folders == 1 else 'them'}, which is listed and counted when you click "
                                   "Download; the size here is the selected files'."))
        archived = sum(e.archived for e in files)
        if archived:
            blocks.append(s3._Note(f"{_plural(archived, 'selected file')} {'is' if archived == 1 else 'are'} in "
                                   "GLACIER or DEEP_ARCHIVE (❄), so the zip leaves "
                                   f"{'it' if archived == 1 else 'them'} out until restored.", "warn"))
        self._action = "picks"
        self._mark_actions()
        self._set_pane([blocks])
        rows = [[e.key[len(base):] or e.key, "" if e.is_folder else s3.human_size(e.size),
                 s3.human_age(e.modified) if e.modified else ""]
                for e in sorted(self._picked.values(), key=lambda e: _natural(e.key))]
        self._picks_list.value = s3._render_html(
            [s3._Table(["Name", "Size", "Modified"], rows, title="What goes in the zip", path_cols=(0,), max_rows=0)],
            0)
        if not keep_name or self._picks_name.value == self._picks_auto:
            self._picks_name.value = self._picks_auto = self._picks_default()
        where = self.zip_folder.strip() or "."
        where = "the notebook's folder" if where == "." else html.escape(where)
        self._picks_where.value = f'<span class="s3x-hint">in {where}</span>'
        self._picks_msg.value = ""
        self._picks_go.disabled = over  # the note above says what to do instead
        self._picks_panel.layout.display = None

    def _close_picks(self) -> None:
        """Back to the file or folder that was shown; the selection stays."""
        self._picks_panel.layout.display = "none"
        if self.selected:
            self._select(self.selected)
        else:
            self._show_folder(self._source())

    def _save_picks(self) -> None:
        """Download in the panel: zip what's selected under the name typed, unless a file of that name is there."""
        s3 = self.s3
        name = self._picks_name.value.strip()
        if not name:
            self._picks_msg.value = s3._render_html([s3._Note("Type a name for the zip.", "warn")], 0)
            return
        if not name.lower().endswith(".zip"):
            name += ".zip"
        path = os.path.join(os.path.expanduser(self.zip_folder.strip() or "."), os.path.expanduser(name))
        if os.path.exists(path):
            self._picks_msg.value = s3._render_html([s3._Note(
                f"{name} is already there, and the explorer doesn't replace files: type another name.", "warn")], 0)
            return
        picks = [s3.ObjectInfo(e.bucket, e.key, e.size or 0, e.modified, e.storage_class or "STANDARD", e.etag)
                 if not e.is_folder and e.modified else e.uri for e in self._picked.values()]
        self._report("picks", self._zip, picks, path, cache=False)

    def _toggle_settings(self) -> None:
        if self._settings.layout.display == "none":
            self._open_settings()
        else:
            self._close_settings()

    def _open_settings(self) -> None:
        """⚙: the settings on the right, in place of the report."""
        s3 = self.s3
        self._action = "settings"
        self._mark_actions()
        try:
            self._set_size.value = s3.human_size(s3.parse_size(self.zip_max_size)).replace(".0 ", " ")
        except (TypeError, ValueError):
            self._set_size.value = str(self.zip_max_size)
        self._set_files.value = f"{self.zip_max_files:,}"
        self._set_folder.value = self.zip_folder
        self._set_note.value = ""
        self._set_pane([[s3._Title("Settings", "for this explorer, until the kernel restarts"),
                         s3._Note("“⬇ Download .zip” on a folder packs everything below it into one .zip on the "
                                  "notebook's disk, if it's within these limits. It also checks the disk space, memory "
                                  "and read access, and writes nothing when a check fails. To start with another "
                                  "limit, use S3Explorer(zip_max_size='2GB').")]], keep=False)
        self._settings.layout.display = None

    def _close_settings(self) -> None:
        """Back to the file or folder that was shown."""
        self._settings.layout.display = "none"
        if self.selected:
            self._select(self.selected)
        else:
            self._show_folder(self._source())

    def _save_settings(self) -> None:
        s3 = self.s3

        def say(text: str, level: str) -> None:
            self._set_note.value = s3._render_html([s3._Note(text, level)], 0)

        size_text = self._set_size.value.strip() or "100MB"
        try:
            size = s3.parse_size(size_text)
        except ValueError:
            size = None
        if not size or size <= 0:
            return say(f"“{size_text}” isn't a size; try 500MB or 2GB.", "warn")
        files_text = self._set_files.value.replace(",", "").replace("_", "").strip() or "10000"
        if not files_text.isdigit() or int(files_text) < 1:
            return say(f"“{self._set_files.value}” isn't a number of files; try 10,000.", "warn")
        folder = self._set_folder.value.strip() or "."
        if os.path.exists(os.path.expanduser(folder)) and not os.path.isdir(os.path.expanduser(folder)):
            return say(f"{folder} is a file, not a folder.", "warn")
        self.zip_max_size, self.zip_max_files, self.zip_folder = size, int(files_text), folder
        self._set_size.value = s3.human_size(size).replace(".0 ", " ")
        self._set_files.value = f"{self.zip_max_files:,}"
        where = "the notebook's folder" if folder == "." else folder + (
            "" if os.path.isdir(os.path.expanduser(folder)) else " (made when the first zip is saved)")
        say(f"Saved: “⬇ Download .zip” now packs folders up to {self._zip_limit()}, into {where}.", "ok")

    # ------------------------------------------------------------------ a PDF, page by page: as it looks, or its text

    def _show_pages(self, first: int, action: str = "document") -> None:
        """Read all (action 'document': the pages as they look) or Text ('text': their words laid out to read), for
        a PDF, from page `first` on; the pager under the report moves on."""
        self._first_page = first
        self._report(action, self._read_pdf if action == "document" else self._read_text, self.selected, variant=first)

    def _read_pdf(self, uri: str) -> None:
        """The pages of a PDF drawn as they print, one report's worth (s3's _MAX_PICTURES, 20) from _first_page on,
        each with its text folded underneath."""
        count = self._page_count(uri)
        if not count:  # a broken or locked PDF, or no pypdf: document() says what's wrong
            self._pane.document(uri, pictures=True)
            return
        first, last = self._page_span(count)
        self._pane.document(uri, pages=range(first, last + 1), pictures=True)
        report = self._captured[-1] if self._captured else []
        if report and isinstance(report[0], self.s3._Title):
            at = next((i + 1 for i, block in enumerate(report) if isinstance(block, self.s3._Cards)), 1)
            span = f"Pages {first}–{last} of {count:,}; the buttons at the end show the others. " if count > last - first + 1 else ""
            report.insert(at, self.s3._Note(f"{span}Click a page to see it full size; ‹ › there step through the pages."))

    def _read_text(self, uri: str) -> None:
        """Text, for a PDF: the words of one report's worth of pages (_TEXT_PAGES, 50) from _first_page on, as
        s3's document(pictures=False) lays them out: headings, paragraphs and lists, a line where each page starts,
        and no running headers or footers."""
        count = self._page_count(uri)
        if not count:  # a broken or locked PDF, or no pypdf: document() says what's wrong
            self._pane.document(uri, pictures=False)
            return
        first, last = self._page_span(count)
        self._pane.document(uri, pages=range(first, last + 1), pictures=False)
        report = self._captured[-1] if self._captured else []
        if report and isinstance(report[0], self.s3._Title) and count > last - first + 1:
            at = next((i + 1 for i, block in enumerate(report) if isinstance(block, self.s3._Cards)), 1)
            report.insert(at, self.s3._Note(f"Pages {first}–{last} of {count:,}; the buttons at the end show the "
                                            "others, and 📖 Read all shows the pages as they look."))

    def _page_count(self, uri: str) -> int:
        """How many pages a PDF has (0 when it can't be read), once per file version."""
        entry = self._entry()
        key = (uri, entry.etag if entry else "")
        if key not in self._page_counts:
            try:
                self._page_counts[key] = self.core.read_pdf(uri, pages=[1]).page_count or 0
            except (ClientError, BotoCoreError, *self.s3._READ_ERRORS):
                self._page_counts[key] = 0
        return self._page_counts[key]

    def _per_report(self) -> int:
        """PDF pages one report shows: Read all draws s3's _MAX_PICTURES (20), Text shows _TEXT_PAGES (50)."""
        return _TEXT_PAGES if self._action == "text" else self.s3._MAX_PICTURES

    def _page_span(self, count: int) -> tuple[int, int]:
        """The first and last page "Read all" or "Text" shows now."""
        first = max(1, min(self._first_page, count))
        return first, min(first + self._per_report() - 1, count)

    def _draw_pager(self, show: bool = True) -> None:
        """The buttons under a PDF's pages (or their text) that show the pages before and after them."""
        w, entry = self._widgets, self._entry()
        count = self._page_counts.get((self.selected, entry.etag if entry else ""), 0)
        per, action = self._per_report(), self._action
        if not (show and action in ("document", "text") and count > per):
            self._pager.layout.display = "none"
            return
        first, last = self._page_span(count)
        old, children = self._pager.children, [w.Label(f"Pages {first}–{last} of {count:,}")]
        for start, label, tip in ((max(1, first - per), f"‹ Pages {max(1, first - per)}–{first - 1}", "The pages before"),
                                  (last + 1, f"Pages {last + 1}–{min(last + per, count)} ›", "The pages after")):
            if 1 <= start <= count and start != first:
                b = w.Button(description=label, tooltip=tip, layout=w.Layout(width="auto"))
                b.add_class("s3x-act")
                b.on_click(lambda _, start=start: self._guard(lambda: self._show_pages(start, action)))
                children.append(b)
        self._pager.children = children
        self._pager.layout.display = None
        for widget in old:
            widget.close()

    def _on_expand(self) -> None:
        """▾ Expand all: every object and array of the JSON on the right, and of the JSON files opened after it,
        until it's clicked again. The report is drawn again in place, so the pane stays where it was scrolled to."""
        self._expand_all = not self._expand_all
        for tree in self._trees():
            tree.unfold("all" if self._expand_all else "start")
        self._content.value = "".join(self.s3._render_html(blocks, self._pane.max_rows) for blocks in self._reports)
        self._draw_expand()

    def _on_filter(self) -> None:
        self._query = self._filter.value
        self._refilter()

    def _set_query(self, text: str, renew: bool = False) -> None:
        """Put text in the search box (without it calling _on_filter too) and redraw the list for it."""
        self._query = text
        if self._widgets is not None:
            self._quiet = True
            self._filter.value = text
            self._quiet = False
        self._refilter(renew)

    def _refilter(self, renew: bool = False) -> None:
        """Redraw the list after the search, All / Folders / Files or Include subfolders changed (the text explorer
        prints it), from its first page. renew: start the list at the top."""
        self._offset = 0
        folder = self._source()
        if self._widgets is None:
            self._set_pane([self._folder_blocks(folder, listing=True)])
            return
        self._draw_filters(folder)
        self._draw_rows(folder)
        if renew:
            self._renew("left")
        self._draw_status(folder)

    def _on_kind(self, kind: str) -> None:
        self._kind = kind
        self._refilter(renew=True)

    def _on_deep(self) -> None:
        """Include subfolders: list everything below this folder (once; it's kept), or only its first level again."""
        self._deep, self._all_types = not self._deep, False
        if self._deep:
            self._busy("Listing everything below this folder…")
        self._follow(self._source())
        self._refilter(renew=True)
        if not self.selected:
            self._show_folder(self._source())

    def _toggle_type(self, kind: str) -> None:
        """A file type's chip: add '.csv' to the search, or take it out again. The search box stays the one place a
        filter is written down, so what a chip did can be read, changed or typed next time."""
        words = [word for word in re.split(r"[\s,]+", self._query.strip()) if word]
        kept = [word for word in words if _type_word(word) != kind]
        if len(kept) == len(words):
            kept.append(f".{kind}")
        self._set_query(" ".join(kept))

    def _on_more_types(self) -> None:
        self._all_types = not self._all_types
        self._draw_filters(self._source())

    def _on_sort(self, by: str) -> None:
        if by == self._sort:
            self._descending = not self._descending
        else:
            self._sort, self._descending = by, by != "name"  # biggest / newest first
        self._offset = 0
        self._draw_header()
        self._draw_rows(self._source())
        self._renew("left")

    def _on_page(self, to: str) -> None:
        """« ‹ › » under the list: its first, previous, next or last page."""
        size, total = self.page_size, len(self._visible)
        last = max(0, total - 1) // size * size
        self._offset = {"first": 0, "previous": max(0, self._offset - size), "next": min(last, self._offset + size),
                        "last": last}[to]
        self._draw_rows(self._source())
        self._renew("left")

    def _on_load(self) -> None:
        """Load more from S3, at the end of a folder bigger than what's listed: the next list_limit entries (deep_limit
        files with Include subfolders), in the background in a notebook."""
        folder = self._source()
        self._list_more(folder, self._goal(folder))
        self._draw_filters(folder)
        self._draw_rows(folder)
        self._draw_status(folder)
        if not self.selected:
            self._show_folder(folder)

    def _on_lookup(self) -> None:
        text = self._query.strip()
        self._busy("Looking up…")
        added = self.nav.lookup(text)
        folder = self.nav.folder()
        self._offset = 0
        self._draw_filters(folder)
        self._draw_rows(folder)
        self._draw_status(folder)
        if not added:
            self._foot_note.value = (f'<div class="s3x-empty">S3 has nothing else starting with '
                                     f'“{html.escape(text)}”.</div>')
            self._foot_note.layout.display = None

    def _toggle_path(self) -> None:
        editing = self._path.layout.display != "none"
        if not editing:
            self._path.value = self.nav.location or "s3://"
        self._path.layout.display = "none" if editing else None
        self._crumbs.layout.display = None if editing else "none"
        self._edit_btn.description = "✎" if editing else "✕"
        self._edit_btn.tooltip = "Type or paste a path" if editing else "Cancel"
        self._edit_btn._dom_classes = ("s3x-nav", "s3x-ic", "s3x-i-edit" if editing else "s3x-i-close")

    def _on_path(self) -> None:
        value = self._path.value.strip()
        self._toggle_path()
        if value and value not in (self.nav.location, "s3://"):
            self.open(value)
