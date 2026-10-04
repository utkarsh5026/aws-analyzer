"""
s3_explorer.py - browse S3 like a file explorer, inside a SageMaker / Jupyter notebook.

Folders and files are listed on the left. Click a folder to open it, or a file to see what's inside it on the
right: a table's first rows, a PDF's pages, a Word file with its pictures, an image, and so on. The toolbar has
back / forward / up buttons, a clickable path you can also type into, a filter box, and the column headers sort
by name, size or date. "Read all" shows a whole PDF as it looks, 20 pages at a time (click a page to see it full
size), and "Download .zip" packs the folder you're in into one .zip, within limits that ⚙ (Settings) changes.

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
    x.ui.summary(x.location)                        # any S3View report about where you are, in its own cell

    nav = S3Navigator()                             # the same navigation as data, with no UI
    folder = nav.open("s3://my-bucket/data/")
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
from urllib.parse import parse_qs, unquote, urlparse

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
    """One level of a bucket, or every bucket when `uri` is ''. Entries come in S3's order, a page at a time."""

    uri: str
    entries: list[Entry] = field(default_factory=list)
    more: bool = False  # S3 has more entries than these; S3Navigator.more() lists the next page
    token: str | None = None  # where the next page starts
    requests: int = 0  # list requests made for this folder so far
    error: str = ""  # why it couldn't be listed ('AccessDenied: ...'); entries is empty then
    error_code: str = ""


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
    'size' or 'modified'. Folders have no size, so a size sort keeps them by name."""
    if by not in ("name", "size", "modified"):
        raise ValueError("by must be 'name', 'size' or 'modified'")

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


def filter_entries(entries: list[Entry], text: str | None) -> list[Entry]:
    """Entries whose name contains `text` (any case), or matches it as a pattern when it has * ? or [."""
    text = (text or "").strip().lower()
    if not text:
        return list(entries)
    if any(ch in text for ch in "*?["):
        return [e for e in entries if fnmatch.fnmatchcase(e.name.lower(), text)]
    return [e for e in entries if text in e.name.lower()]


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
    session / region / profile / client."""

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
    ):
        self.s3 = _s3_module(core)
        core = getattr(core, "core", core)  # an S3View carries its analyzer as .core
        self.core = core or self.s3.S3Analyzer(session, region=region, profile=profile, client=client)
        self.page_size = max(1, min(int(page_size), 1000))
        self.cache_size = cache_size
        self.location = ""  # the folder you're in: 's3://bucket/prefix/', or '' for every bucket
        self.focus = ""  # after open(file uri): that file, for the UI to show
        self.notice = ""  # after open(): something worth saying about where you landed
        self._back: list[str] = []
        self._forward: list[str] = []
        self._cache: dict[str, Folder] = {}
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
        """The current folder (or `uri`), listed once and then cached."""
        uri = self.location if uri is None else uri
        if uri not in self._cache:
            folder = Folder(uri)
            self._load(folder)
            while len(self._cache) >= self.cache_size:
                self._cache.pop(next(iter(self._cache)))
            self._cache[uri] = folder
        return self._cache[uri]

    def more(self) -> Folder:
        """The next page of a folder with more entries than one listing returns."""
        folder = self.folder()
        if folder.more:
            self._load(folder)
        return folder

    def lookup(self, text: str) -> int:
        """Find entries whose names start with `text` in S3, for a folder too big to load: they're added to
        the current folder. Returns how many weren't loaded yet."""
        folder = self.folder()
        bucket, prefix = parse_location(folder.uri)
        if not bucket or not text:
            return 0
        known = {entry.key for entry in folder.entries}
        try:
            found, _ = self._page(bucket, prefix + text)
        except ClientError:
            return 0
        folder.requests += 1
        new = [entry for entry in found if entry.key not in known]
        folder.entries += new
        return len(new)

    def _load(self, folder: Folder) -> None:
        bucket, prefix = parse_location(folder.uri)
        try:
            if not bucket:
                folder.entries = [
                    Entry("bucket", info.name, modified=info.created)
                    for info in self.core.list_buckets(with_region=False)
                ]
                folder.requests += 1
                return
            known = {entry.key for entry in folder.entries}
            entries, folder.token = self._page(bucket, prefix, folder.token)
            folder.entries += [entry for entry in entries if entry.key not in known]
            folder.more = folder.token is not None
            folder.requests += 1
        except ClientError as exc:
            error = exc.response.get("Error", {})
            folder.error_code = str(error.get("Code", "Error"))
            folder.error = f"{folder.error_code}: {error.get('Message', exc)}"
        except BotoCoreError as exc:
            folder.error_code, folder.error = type(exc).__name__, f"{type(exc).__name__}: {exc}"

    def _page(self, bucket: str, prefix: str, token: str | None = None) -> tuple[list[Entry], str | None]:
        """One ListObjectsV2 page of the level under `prefix` -> (entries, token for the next page or None)."""
        request: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix, "Delimiter": "/", "MaxKeys": self.page_size}
        if token:
            request["ContinuationToken"] = token
        page = self.core.client.list_objects_v2(**request)
        base = prefix[: prefix.rfind("/") + 1]
        entries = [Entry("folder", bucket, p["Prefix"]) for p in page.get("CommonPrefixes", [])]
        entries += [
            Entry("file", bucket, o["Key"], o.get("Size", 0), o.get("LastModified"),
                  o.get("StorageClass", "STANDARD"), o.get("ETag", "").strip('"'))
            for o in page.get("Contents", [])
            if o["Key"] != base  # the folder's own marker object
        ]
        return entries, page.get("NextContinuationToken") if page.get("IsTruncated") else None

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


_CSS = """<style>
.s3x{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:13px;
 border:1px solid rgba(127,127,127,.3);border-radius:8px;overflow:hidden;margin:4px 0;
 background:var(--jp-layout-color0,var(--vscode-editor-background,transparent))}
.s3x .widget-html-content{line-height:1.45}
.s3x .jupyter-button,.s3x .widget-label,.s3x input{font-size:13px}
.s3x button.jupyter-button{color:inherit;box-shadow:none;outline:none}
.s3x button.jupyter-button:hover:enabled,.s3x button.jupyter-button:focus:enabled,.s3x button.jupyter-button:active,
.s3x button.jupyter-button.mod-active{box-shadow:none;outline:none}
.s3x button.jupyter-button:focus-visible:enabled{outline:2px solid rgba(59,130,246,.6);outline-offset:-2px}
.s3x button.jupyter-button:active:enabled{background-color:rgba(127,127,127,.22)}
.s3x .s3x-bar{align-items:center;gap:2px;padding:6px 8px;border-bottom:1px solid rgba(127,127,127,.25);
 background:rgba(127,127,127,.06)}
.s3x .s3x-bar>*{margin:0}
.s3x button.s3x-nav{width:30px;min-width:30px;padding:0;background:transparent;border-radius:6px;font-size:15px}
.s3x button.s3x-nav:hover:enabled{background:rgba(127,127,127,.16)}
.s3x button.s3x-nav:disabled{opacity:.3;cursor:default}
.s3x .s3x-crumbs{flex:1 1 auto;min-width:0;overflow:hidden;align-items:center;flex-wrap:nowrap;margin:0 6px}
.s3x .s3x-crumbs>*{margin:0;flex:0 1 auto;min-width:0}
.s3x button.s3x-crumb{width:auto;max-width:240px;padding:0 6px;background:transparent;border-radius:5px}
.s3x button.s3x-crumb:hover{background:rgba(127,127,127,.16)}
.s3x button.s3x-crumb.s3x-here{font-weight:600}
.s3x .s3x-sep{width:auto;min-width:0;padding:0 1px;opacity:.4;flex:0 0 auto}
.s3x .s3x-path input{border-radius:6px}
.s3x .s3x-filter input{border-radius:14px;padding-left:10px}
.s3x .s3x-left{border-right:1px solid rgba(127,127,127,.25)}
.s3x .s3x-left>*{margin:0}
.s3x .s3x-head{position:sticky;top:0;z-index:2;padding:0 6px;border-bottom:1px solid rgba(127,127,127,.3);
 background:var(--jp-layout-color0,var(--vscode-editor-background,#fff))}
.s3x .s3x-head>*{margin:0}
.s3x button.s3x-col{background:transparent;text-align:left;padding:0 8px;font-size:11px;font-weight:600;
 letter-spacing:.03em;text-transform:uppercase;opacity:.65;height:26px;line-height:26px}
.s3x button.s3x-col:hover{opacity:1}
.s3x button.s3x-col.s3x-num{text-align:right}
.s3x .s3x-rows{padding:4px 6px}
.s3x .s3x-rows>*{margin:0}
.s3x .s3x-r{position:relative;height:28px;align-items:center;justify-content:flex-end;flex:0 0 auto}
.s3x .s3x-r>*{margin:0}
.s3x button.s3x-row{position:absolute;top:0;left:0;width:100%;height:28px;text-align:left;padding:0 160px 0 8px;
 background:transparent;border-radius:5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.s3x button.s3x-row:hover{background:rgba(127,127,127,.13)}
.s3x button.s3x-row.s3x-on{background:rgba(59,130,246,.18);font-weight:600}
.s3x .s3x-r .widget-label{position:relative;z-index:1;pointer-events:none;text-align:right;opacity:.65;
 font-size:12px;font-variant-numeric:tabular-nums;padding-right:8px}
.s3x .s3x-foot{flex-wrap:wrap;gap:4px;padding:2px 10px 10px;align-items:center}
.s3x .s3x-foot>*{margin:0}
.s3x button.s3x-link{width:auto;background:transparent;color:#3b82f6;padding:0 6px;border-radius:5px}
.s3x button.s3x-link:hover{background:rgba(59,130,246,.10)}
.s3x .s3x-empty{opacity:.65;padding:6px 4px}
.s3x .s3x-right{padding:0 16px 12px}
.s3x .s3x-right>*{margin:0;min-width:0}
.s3x .s3x-right .widget-html-content{min-width:0}
.s3x .s3x-actions{flex-wrap:wrap;gap:6px;padding:10px 0 8px;border-bottom:1px dashed rgba(127,127,127,.3)}
.s3x .s3x-actions>*{margin:0}
.s3x button.s3x-act{width:auto;height:26px;line-height:26px;padding:0 12px;border-radius:13px;
 background:rgba(127,127,127,.12)}
.s3x button.s3x-act:hover{background:rgba(127,127,127,.2)}
.s3x button.s3x-act.s3x-on{background:rgba(59,130,246,.18);box-shadow:inset 0 0 0 1px rgba(59,130,246,.55)}
.s3x button.s3x-close{margin-left:auto;background:transparent}
.s3x .s3x-pager{flex-wrap:wrap;gap:6px;align-items:center;margin-top:8px;padding:10px 0 4px;
 border-top:1px dashed rgba(127,127,127,.3)}
.s3x .s3x-pager>*{margin:0}
.s3x .s3x-pager .widget-label{opacity:.65;margin-right:6px}
.s3x .s3x-settings{gap:8px;padding:4px 0 8px}
.s3x .s3x-settings>*{margin:0}
.s3x .s3x-setting{gap:10px;align-items:center;flex-wrap:wrap}
.s3x .s3x-setting>*{margin:0}
.s3x .s3x-setting .widget-label{font-size:12px}
.s3x .s3x-hint{opacity:.65;font-size:12px}
.s3x .s3x-status{padding:3px 10px;border-top:1px solid rgba(127,127,127,.25);background:rgba(127,127,127,.06);
 font-size:12px}
.s3x .s3x-status>.widget-html-content{display:flex;gap:12px;justify-content:space-between;align-items:center;
 line-height:22px;min-width:0}
.s3x .s3x-status .s3x-at{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.s3x .s3x-status code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11px;padding:0 4px;
 border-radius:4px;background:rgba(127,127,127,.15);user-select:all;-webkit-user-select:all;cursor:text}
</style>"""
_DOCUMENTS = ("pdf", "docx", "docm", "dotx", "pptx", "pptm", "potx", "ppsx")  # files "Read all" opens
_CLICK_GRACE = 0.35  # seconds after the rows change during which a click is ignored (it was aimed at the old rows)
_BACKGROUND = ("preview", "head")  # quick reports (no progress bar) that load on worker threads in a notebook
_WORKERS = 4  # background reports loading at once, so a click doesn't wait for files clicked before it


class _Row:
    """One reusable row of the list: a full-width button (the name) under two labels (size, modified)."""

    def __init__(self, widgets: Any, on_click: Callable[[_Row], None]):
        self.entry: Entry | None = None
        self.button = widgets.Button(layout=widgets.Layout(width="100%"))
        self.button.add_class("s3x-row")
        self.button.on_click(lambda _: on_click(self))
        self.size = widgets.Label(layout=widgets.Layout(width="76px", flex="0 0 auto"))
        self.age = widgets.Label(layout=widgets.Layout(width="76px", flex="0 0 auto"))
        self.box = widgets.HBox([self.button, self.size, self.age], layout=widgets.Layout(width="100%"))
        self.box.add_class("s3x-r")


class S3Explorer:
    """Browse S3 like a file explorer in a notebook: folders and files on the left, what's inside the file you
    click on the right. Click a folder to open it; ← → ↑ go back, forward and up; click the path to jump
    anywhere (paste an s3:// path or an S3 console link); type in the filter box to narrow the list; click a
    column header to sort.

    uri: where to start ('s3://bucket/prefix/', 'bucket/prefix', a file's path, a console link); leave it
    out to start from your buckets.
    core: an S3Analyzer or S3View to use (else one is made from profile / region).
    height: the height of the two panes in pixels. page_size: rows shown before "Show more".
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
        self._job = 0  # counts changes of the right pane; a background report made for an older one isn't shown
        self._workers: ThreadPoolExecutor | None = None  # the threads background reports load on
        self._task: Any = None  # the last background report's asyncio task (tests wait for it)
        self._first_page = 1  # where "Read all" starts in a PDF; the pager under the report moves it
        self._page_counts: dict[tuple[str, str], int] = {}  # (uri, etag) -> a PDF's pages (0: couldn't count)
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
        self._limit = self.page_size
        self._visible: list[Entry] = []
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
        self._navigate(self.nav.refresh, keep=keep)

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
        return next((e for e in self.nav.folder().entries if e.uri == self.selected), None) if self.selected else None

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
                   "document": f"Reading {name}…", "download": f"Downloading {name}…",
                   "summary": "Reading every file below this folder…",
                   "zip": "Listing every file below this folder, then zipping them…"}
        self._set_pane([[self.s3._Note(waiting.get(action, "Working…"))]], keep=False)
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
            if self._workers is None:
                self._workers = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="s3-explorer")
            reports = await asyncio.wrap_future(self._workers.submit(self._load, job, method, selected, args, kwargs))
            if reports is None:
                return
            self._keep(key, reports, cache)
            if job == self._job:
                self._set_pane(reports)
        except Exception as exc:  # a bug: still say so where the user is looking, and not in the kernel's log
            if job == self._job:
                self._fail(f"Something went wrong ({type(exc).__name__}: {exc}). Click the file again to try again.")

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

    def _set_pane(self, reports: list[list[Any]], keep: bool = True) -> None:
        """Show reports on the right; the text explorer (no ipywidgets) shows them as the S3View would. keep=False
        is a note shown while a report loads."""
        self._job += 1
        if keep:
            self.shown = [block for blocks in reports for block in blocks]
        if self._widgets is None:
            for blocks in reports:
                type(self._pane)._show(self._pane, blocks)
            return
        self._content.value = "".join(self.s3._render_html(blocks, self._pane.max_rows) for blocks in reports)
        self._settings.layout.display = "none"
        self._draw_pager(keep)
        self._renew("right")

    # ------------------------------------------------------------------ navigation

    def _navigate(self, move: Callable[[], Folder], keep: str = "") -> None:
        """Move (open / back / up / ...), then redraw the list, the path and the right pane."""
        if self._widgets is not None:
            self._status.value = '<span class="s3x-at">Listing…</span>'
        folder = move()
        self.selected, self._limit, self._action = "", self.page_size, ""
        if self._widgets is not None:
            self._quiet = True
            self._filter.value = ""
            self._quiet = False
            self._draw_crumbs()
            self._draw_rows(folder)
            self._renew("left")
        focus = self.nav.focus or keep
        if focus and any(e.uri == focus for e in folder.entries):
            self._select(focus)
        elif self._widgets is None:
            self._set_pane([self._folder_blocks(folder, listing=True)])
        else:
            self._show_folder(folder)
        self._draw_status(folder)

    def _select(self, uri: str) -> None:
        """Show a file on the right."""
        self.selected = uri
        if self._widgets is not None:
            for entry in self._visible[: self._limit]:  # bring it into view when it's past "Show more"
                if entry.uri == uri:
                    break
            else:
                index = next((i for i, e in enumerate(self._visible) if e.uri == uri), -1)
                if index >= 0:
                    self._limit = index + 1 + self.page_size // 2
                    self._draw_rows(self.nav.folder())
            self._mark_rows()
            self._draw_actions()
            self._draw_status(self.nav.folder())
        self._report("preview", "preview", uri)

    def _show_folder(self, folder: Folder) -> None:
        self.selected = ""
        self._mark_rows()
        self._draw_actions()
        self._set_pane([self._folder_blocks(folder)])
        self._draw_status(folder)

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
        stats = folder_stats(folder.entries)
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
            cards = [("Folders", f"{stats.folders:,}{plus}")] if stats.folders else []
            if stats.files:
                cards += [("Files", f"{stats.files:,}{plus}"), ("Size of these files", s3.human_size(stats.size) + plus)]
            if stats.newest:
                cards.append(("Newest file", s3.human_age(stats.newest)))
            if stats.archived:
                cards.append(("Archived", f"{stats.archived:,}", "warn"))
            if cards:
                blocks.append(s3._Cards(cards))
            if folder.more:
                blocks.append(s3._Note(f"These numbers cover the first {len(folder.entries):,} entries S3 returned; "
                                       "“Load more” at the end of the list fetches the rest."))
            if stats.archived:
                blocks.append(s3._Note(f"{_plural(stats.archived, 'file')} here {'is' if stats.archived == 1 else 'are'}"
                                       " in GLACIER or DEEP_ARCHIVE (❄ in the list): they can't be opened until "
                                       "they're restored.", "warn"))
            if not folder.entries:
                blocks.append(s3._Note("This folder is empty." if prefix else "This bucket is empty."))
            if stats.types:
                blocks.append(s3._Table(["Type", "Files", "Size"],
                                        [[ext, f"{n:,}", s3.human_size(size)] for ext, n, size in stats.types],
                                        title="File types here", max_rows=0))
            if folder.entries and not listing:
                blocks.append(s3._Note("Click a folder to open it, or a file to see what's inside. The sizes are "
                                       "for the files at this level; the “What's in here” button above adds up "
                                       "everything below."))
        if listing:
            rows = [[f"{entry_icon(e, s3)} {e.name}{'/' if e.kind == 'folder' else ''}{' ❄' if e.archived else ''}",
                     "" if e.is_folder else s3.human_size(e.size), s3.human_age(e.modified) if e.modified else ""]
                    for e in sort_entries(folder.entries, self._sort, self._descending)]
            blocks.append(s3._Table(["Name", "Size", "Modified" if bucket else "Created"], rows,
                                    title="In this folder" if bucket else "Buckets"))
            blocks.append(s3._Note("This is the text view: x.open('s3://…') moves around; in a Jupyter notebook "
                                   "with ipywidgets every row is clickable."))
        return blocks

    # ------------------------------------------------------------------ widgets

    def _build(self) -> None:
        w = self._widgets

        def button(text: str, css: str, tip: str, on_click: Callable[[], None], **layout: Any) -> Any:
            b = w.Button(description=text, tooltip=tip, layout=w.Layout(**layout))
            b.add_class(css)
            b.on_click(lambda _: self._guard(on_click))
            return b

        style = w.HTML(_CSS, layout=w.Layout(display="none"))
        self._back_btn = button("←", "s3x-nav", "Back", self.back)
        self._fwd_btn = button("→", "s3x-nav", "Forward", self.forward)
        self._up_btn = button("↑", "s3x-nav", "Up one level", self.up)
        refresh = button("↻", "s3x-nav", "List this folder again", self.refresh)
        self._crumbs = w.HBox()
        self._crumbs.add_class("s3x-crumbs")
        self._path = w.Text(placeholder="s3://bucket/folder/ or an S3 console link, then Enter",
                            layout=w.Layout(flex="1 1 auto", display="none", width="auto"))
        self._path.add_class("s3x-path")
        # Go on Enter only (the box sends "submit"), not when it loses focus: clicking ✕ then cancels.
        self._path.on_msg(lambda _, content, __: content.get("event") == "submit" and self._guard(self._on_path))
        self._edit_btn = button("✎", "s3x-nav", "Type or paste a path", self._toggle_path)
        self._filter = w.Text(placeholder="🔍  Filter", continuous_update=True,
                              layout=w.Layout(width="190px", flex="0 0 auto"))
        self._filter.add_class("s3x-filter")
        self._filter.observe(lambda _: self._quiet or self._guard(self._on_filter), "value")
        self._gear = button("⚙", "s3x-nav", "Settings: the biggest folder to zip, and where zips go",
                            self._toggle_settings)
        bar = w.HBox([self._back_btn, self._fwd_btn, self._up_btn, refresh, self._crumbs, self._path,
                      self._edit_btn, self._filter, self._gear], layout=w.Layout(width="100%"))
        bar.add_class("s3x-bar")

        self._cols = {
            "name": button("Name", "s3x-col", "Sort by name", lambda: self._on_sort("name"), flex="1 1 auto",
                           width="auto"),
            "size": button("Size", "s3x-col", "Sort by size", lambda: self._on_sort("size"), width="76px"),
            "modified": button("Modified", "s3x-col", "Sort by date", lambda: self._on_sort("modified"),
                               width="76px"),
        }
        for column in ("size", "modified"):
            self._cols[column].add_class("s3x-num")
        head = w.HBox(list(self._cols.values()), layout=w.Layout(width="100%", flex="0 0 auto"))
        head.add_class("s3x-head")
        self._rows_box = w.VBox(layout=w.Layout(width="100%", flex="0 0 auto"))
        self._rows_box.add_class("s3x-rows")
        self._pool: list[_Row] = []
        self._more_btn = button("Show more", "s3x-link", "Show the next rows", self._on_more)
        self._load_btn = button("Load more from S3", "s3x-link", "List the next page of this folder", self._on_load)
        self._lookup_btn = button("", "s3x-link", "Ask S3 for names starting with the filter text", self._on_lookup)
        self._foot_note = w.HTML()
        self._foot = w.HBox([self._foot_note, self._more_btn, self._load_btn, self._lookup_btn],
                            layout=w.Layout(width="100%", flex="0 0 auto"))
        self._foot.add_class("s3x-foot")
        self._head = head

        self._actions = w.HBox(layout=w.Layout(width="100%", flex="0 0 auto"))
        self._actions.add_class("s3x-actions")
        self._progress = w.Output(layout=w.Layout(width="100%", flex="0 0 auto"))
        self._content = w.HTML(layout=w.Layout(width="100%", flex="0 0 auto"))
        self._pager = w.HBox(layout=w.Layout(width="100%", flex="0 0 auto", display="none"))
        self._pager.add_class("s3x-pager")
        self._settings = self._build_settings(button)
        self._body = w.HBox(layout=w.Layout(width="100%", height=f"{self.height}px"))
        self._left = self._right = None
        self._renew("left", "right")
        self._status = w.HTML(layout=w.Layout(width="100%"))
        self._status.add_class("s3x-status")
        self._app = w.VBox([style, bar, self._body, self._status], layout=w.Layout(width="100%"))
        self._app.add_class("s3x")
        self._act_buttons: dict[str, Any] = {}

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
        if "left" in sides:
            self._left = w.VBox([self._head, self._rows_box, self._foot],
                                layout=w.Layout(width="42%", min_width="280px", overflow="auto", flex="0 0 auto"))
            self._left.add_class("s3x-left")
        if "right" in sides:
            self._right = w.VBox([self._actions, self._progress, self._content, self._settings, self._pager],
                                 layout=w.Layout(flex="1 1 auto", width="auto", min_width="0", overflow="auto"))
            self._right.add_class("s3x-right")  # min_width 0: wide tables scroll
        self._body.children = (self._left, self._right)
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
                folder = self.nav._cache.get(self.nav.location)
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
                lambda: self._show_folder(self.nav.folder()) if last else self.open(uri)))
            children.append(b)
            if not last:
                children.append(w.Label("›", layout=w.Layout(width="auto")))
                children[-1].add_class("s3x-sep")
        self._crumbs.children = children
        for widget in old:
            widget.close()
        self._back_btn.disabled = not self.nav.can_back
        self._fwd_btn.disabled = not self.nav.can_forward
        self._up_btn.disabled = not self.nav.location
        bucket = parse_location(self.nav.location)[0]
        self._cols["size"].layout.visibility = "visible" if bucket else "hidden"
        self._draw_header()

    def _draw_header(self) -> None:
        bucket = parse_location(self.nav.location)[0]
        labels = {"name": "Name", "size": "Size", "modified": "Modified" if bucket else "Created"}
        for column, b in self._cols.items():
            arrow = (" ▼" if self._descending else " ▲") if column == self._sort else ""
            b.description = labels[column] + arrow

    def _draw_rows(self, folder: Folder) -> None:
        """Fill the list from the folder, filtered and sorted, reusing row widgets so redraws are quick."""
        w = self._widgets
        entries = sort_entries(filter_entries(folder.entries, self._filter.value), self._sort, self._descending)
        self._visible = entries
        shown = entries[: self._limit]
        while len(self._pool) < len(shown):
            self._pool.append(_Row(w, self._on_row))
        for row, entry in zip(self._pool, shown):
            self._fill(row, entry)
        self._rows_box.children = tuple(row.box for row in self._pool[: len(shown)])
        key = (folder.uri, tuple(e.uri for e in shown))
        if key[1] and key != self._rows_key[:2]:
            self._rows_at = time.monotonic()
        self._rows_key = key
        self._draw_foot(folder, entries, len(shown))

    def _fill(self, row: _Row, entry: Entry) -> None:
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
            row.button._dom_classes = ("s3x-row", "s3x-on") if entry.uri == self.selected else ("s3x-row",)
        row.size.value, row.age.value = size, age

    def _mark_rows(self) -> None:
        if self._widgets is None:
            return
        for row in self._pool[: len(self._rows_box.children)]:
            on = row.entry is not None and row.entry.uri == self.selected
            row.button._dom_classes = ("s3x-row", "s3x-on") if on else ("s3x-row",)

    def _draw_foot(self, folder: Folder, entries: list[Entry], shown: int) -> None:
        text = self._filter.value.strip()
        hidden = len(entries) - shown
        self._more_btn.description = (f"Show {self.page_size:,} more (of {hidden:,})" if hidden > self.page_size
                                      else f"Show the last {hidden:,}")
        self._more_btn.layout.display = None if hidden > 0 else "none"
        self._load_btn.description = f"Load more from S3 (the first {len(folder.entries):,} are listed)"
        self._load_btn.layout.display = None if folder.more and not hidden else "none"
        self._lookup_btn.description = f"Look up names starting with “{text}” in S3"
        lookup = folder.more and text and not any(c in text for c in "*?[")
        self._lookup_btn.layout.display = None if lookup else "none"
        note = ""
        if folder.error:
            note = "Couldn't list this folder; the note on the right says why."
        elif not folder.entries:
            note = "Empty." if parse_location(folder.uri)[0] else "No buckets."
        elif not entries:
            note = f"Nothing here matches “{html.escape(text)}”" + (
                f" among the first {len(folder.entries):,} entries." if folder.more else ".")
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
            actions += [("download", "⬇ Download", "Save a copy in this notebook's folder"),
                        ("link", "🔗 Link", "A download link that works for an hour, without AWS access"),
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
            b = w.Button(description=label, tooltip=tip, layout=w.Layout(width="auto"))
            b.add_class("s3x-act")
            if action == "close":
                b.add_class("s3x-close")
            b.on_click(lambda _, action=action: self._guard(lambda: self._on_action(action)))
            self._act_buttons[action] = b
        self._actions.children = tuple(self._act_buttons.values())
        for widget in old:
            widget.close()
        self._action = "preview" if self.selected else ""
        self._mark_actions()

    def _mark_actions(self) -> None:
        for action, b in getattr(self, "_act_buttons", {}).items():
            if action != "close":
                b._dom_classes = ("s3x-act", "s3x-on") if action == self._action else ("s3x-act",)

    def _draw_status(self, folder: Folder) -> None:
        if self._widgets is None:
            return
        s3, esc = self.s3, html.escape
        if folder.error:
            left = "Couldn't list this folder"
        elif not parse_location(folder.uri)[0]:
            left = _plural(len(folder.entries), "bucket")
        else:
            stats = folder_stats(folder.entries)
            plus = "+" if folder.more else ""
            parts = [f"{stats.folders:,}{plus} folder{'' if stats.folders == 1 and not plus else 's'}"] if stats.folders else []
            if stats.files:
                parts += [f"{stats.files:,}{plus} file{'' if stats.files == 1 and not plus else 's'}",
                          s3.human_size(stats.size) + (" so far" if plus else "")]
            left = " · ".join(parts) or "Empty"
        text = self._filter.value.strip()
        if text and not folder.error:
            left += f" · {len(self._visible):,} match “{esc(text)}”"
        here = self.selected or folder.uri
        right = f'<code title="Click to select, then copy">{esc(here)}</code>' if here else ""
        self._status.value = f'<span class="s3x-at">{left}</span>{right}'

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
            self._show_folder(self.nav.folder())
        elif action == "preview":
            self._report("preview", "preview", uri)
        elif action == "head":
            self._report("head", "head", uri)
        elif action == "document" and _extension(Entry("file", *parse_location(uri)).name).split(".")[0] == "pdf":
            self._show_pages(1)
        elif action == "document":
            self._report("document", "document", uri)
        elif action == "download":
            self._report("download", "download", uri, cache=False)
        elif action == "link":
            self._report("link", "link", uri, cache=False)
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

    def _zip(self, uri: str) -> None:
        """⬇ Download .zip, for a folder: s3's download_zip with the limits from ⚙ Settings. It checks the size, file
        count, disk space, memory and read access first, and writes nothing when one fails."""
        bucket, prefix = parse_location(uri)
        name = (prefix.rstrip("/").rsplit("/", 1)[-1] or bucket) + ".zip"
        folder = os.path.expanduser(self.zip_folder.strip() or ".")
        if folder != ".":
            os.makedirs(folder, exist_ok=True)
        self._pane.download_zip(uri, name if folder == "." else os.path.join(folder, name),
                                max_size=self.zip_max_size, max_files=self.zip_max_files)
        report = self._captured[-1] if self._captured else []
        refused = any(card[:2] == ("Can download", "no") for block in report if isinstance(block, self.s3._Cards)
                      for card in block.items)
        if refused:  # after the line that says why
            at = next((i + 1 for i, block in enumerate(report) if isinstance(block, self.s3._Note)), len(report))
            report.insert(at, self.s3._Note(f"If it's over a limit, ⚙ at the top right raises them (now "
                                            f"{self._zip_limit()}); then click ⬇ Download .zip again."))

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
            self._show_folder(self.nav.folder())

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

    # ------------------------------------------------------------------ a PDF, page by page

    def _show_pages(self, first: int) -> None:
        """Read all, for a PDF: its pages as they look, from `first` on (the pager under the report moves on)."""
        self._first_page = first
        self._report("document", self._read_pdf, self.selected, variant=first)

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

    def _page_span(self, count: int) -> tuple[int, int]:
        """The first and last page "Read all" shows now."""
        first = max(1, min(self._first_page, count))
        return first, min(first + self.s3._MAX_PICTURES - 1, count)

    def _draw_pager(self, show: bool = True) -> None:
        """The buttons under a PDF's pages that show the pages before and after them."""
        w, entry = self._widgets, self._entry()
        count = self._page_counts.get((self.selected, entry.etag if entry else ""), 0)
        per = self.s3._MAX_PICTURES
        if not (show and self._action == "document" and count > per):
            self._pager.layout.display = "none"
            return
        first, last = self._page_span(count)
        old, children = self._pager.children, [w.Label(f"Pages {first}–{last} of {count:,}")]
        for start, label, tip in ((max(1, first - per), f"‹ Pages {max(1, first - per)}–{first - 1}", "The pages before"),
                                  (last + 1, f"Pages {last + 1}–{min(last + per, count)} ›", "The pages after")):
            if 1 <= start <= count and start != first:
                b = w.Button(description=label, tooltip=tip, layout=w.Layout(width="auto"))
                b.add_class("s3x-act")
                b.on_click(lambda _, start=start: self._guard(lambda: self._show_pages(start)))
                children.append(b)
        self._pager.children = children
        self._pager.layout.display = None
        for widget in old:
            widget.close()

    def _on_filter(self) -> None:
        self._limit = self.page_size
        folder = self.nav.folder()
        self._draw_rows(folder)
        self._mark_rows()
        self._draw_status(folder)

    def _on_sort(self, by: str) -> None:
        if by == self._sort:
            self._descending = not self._descending
        else:
            self._sort, self._descending = by, by != "name"  # biggest / newest first
        self._limit = self.page_size
        self._draw_header()
        self._draw_rows(self.nav.folder())
        self._renew("left")

    def _on_more(self) -> None:
        self._limit += self.page_size
        self._draw_rows(self.nav.folder())

    def _on_load(self) -> None:
        self._status.value = '<span class="s3x-at">Listing…</span>'
        folder = self.nav.more()
        self._limit += self.page_size
        self._draw_rows(folder)
        self._draw_status(folder)
        if not self.selected:
            self._show_folder(folder)

    def _on_lookup(self) -> None:
        text = self._filter.value.strip()
        self._status.value = '<span class="s3x-at">Looking up…</span>'
        added = self.nav.lookup(text)
        folder = self.nav.folder()
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

    def _on_path(self) -> None:
        value = self._path.value.strip()
        self._toggle_path()
        if value and value not in (self.nav.location, "s3://"):
            self.open(value)
