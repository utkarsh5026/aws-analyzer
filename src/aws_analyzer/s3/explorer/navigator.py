"""
Where the explorer is and what's there, with no UI: s3:// paths and console links, entries,
folders, searches and their counts, and S3Navigator, which lists them a page at a time.
"""

from __future__ import annotations

import fnmatch
import importlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.render import _file_icon
from ..analyzer.s3_analyzer import S3Analyzer


def _s3_module() -> Any:
    """
    The s3 package this explorer is part of, where the previews, the formatting and the AWS calls come from.
    """
    return importlib.import_module(__package__.rpartition(".")[0])


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
    """
    Sort key that orders 'part-2' before 'part-10'.
    """
    return [int(piece) if piece.isdigit() else piece.lower() for piece in _DIGITS.split(name)]


def _extension(name: str) -> str:
    """
    'data.csv.gz' -> 'csv.gz', 'part-0.snappy.parquet' -> 'parquet', 'README' -> '' (like s3.file_extension).
    """
    parts = name.lower().split(".")
    if len(parts) < 2 or not parts[-1] or (len(parts) == 2 and not parts[0]):
        return ""
    if parts[-1] in _COMPRESSION and len(parts) > 2 and parts[-2].isalpha() and len(parts[-2]) <= 8:
        return f"{parts[-2]}.{parts[-1]}"
    return parts[-1]


def _file_type(name: str) -> str:
    """
    The type a file is counted under: 'a.csv.gz' -> 'csv', 'x.JPEG' -> 'jpg', 'logs.tar.gz' -> 'tar', 'README' -> ''.
    """
    found = _extension(name).split(".")[0]
    return _SAME_TYPE.get(found, found)


def _types_of(name: str) -> set[str]:
    """
    Every type a file name answers to in a search: 'a.csv.gz' -> {'csv', 'gz', 'csv.gz'}, 'x.jpeg' -> {'jpg'}.
    """
    parts = name.lower().split(".")
    found = {_SAME_TYPE.get(t, t) for t in (".".join(parts[i:]) for i in range(1, len(parts)) if all(parts[i:]))}
    return found | {_file_type(name)} - {""}


def _type_word(word: str) -> str:
    """
    The file type a word of a filter asks for: '.csv', '*.csv' or 'ext:csv' -> 'csv', '.jpeg' -> 'jpg'; '' when the
    word isn't one.
    """
    match = _TYPE_WORD.match(word.lower())
    return _SAME_TYPE.get(match.group(1), match.group(1)) if match else ""


def _parent_prefix(key: str) -> str:
    """
    The folder part of a key: 'a/b/c.csv' -> 'a/b/', 'c.csv' -> '', '/c.csv' -> '/'.
    """
    head, sep, _ = key.rpartition("/")
    return head + sep


@dataclass
class Entry:
    """
    One row of the explorer: a bucket, a folder or a file.
    """

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
        """
        Buckets and folders open; files show what's inside them.
        """
        return self.kind != "file"

    @property
    def name(self) -> str:
        """
        What the explorer shows: 'events' for s3://b/raw/events/, 'a.json' for a file, the bucket's name.
        """
        if self.kind == "bucket":
            return self.bucket
        key = self.key[:-1] if self.kind == "folder" else self.key
        return key.rsplit("/", 1)[-1]

    @property
    def archived(self) -> bool:
        return self.storage_class in _ARCHIVED


@dataclass
class Folder:
    """
    One level of a bucket, or every bucket when `uri` is ''. Entries come in S3's order, a page at a time.
    A deep folder (S3Navigator.below) holds everything below `uri` instead: every file, and the folders between.
    """

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
    """
    What a filter box's text asks for (see parse_filter): words a name must contain, patterns it must match, and
    file types it must have one of.
    """

    words: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)  # with * ? or [, matched against the whole name
    types: list[str] = field(default_factory=list)  # 'csv', 'csv.gz', ...

    def __bool__(self) -> bool:
        return bool(self.words or self.patterns or self.types)

    def named(self, entry: Entry) -> bool:
        """
        The name has every word in it and matches every pattern (any case).
        """
        name = entry.name.lower()
        return all(word in name for word in self.words) and all(fnmatch.fnmatchcase(name, p) for p in self.patterns)

    def typed(self, entry: Entry) -> bool:
        """
        It's a file of one of the types (anything is, when no type was asked for; a folder never is).
        """
        return not self.types or (not entry.is_folder and not _types_of(entry.name).isdisjoint(self.types))

    def matches(self, entry: Entry) -> bool:
        return self.named(entry) and self.typed(entry)


@dataclass
class FolderStats:
    """
    What the loaded entries of a folder add up to. Sizes cover the files at this level, not sub-folders.
    """

    folders: int = 0
    files: int = 0
    size: int = 0
    newest: datetime | None = None
    oldest: datetime | None = None
    archived: int = 0  # files in GLACIER / DEEP_ARCHIVE
    types: list[tuple[str, int, int]] = field(default_factory=list)  # (extension, files, bytes), biggest first


def parse_location(text: str | None) -> tuple[str, str]:
    """
    Where a pasted path points -> (bucket, key); ('', '') means every bucket.

    Takes 's3://bucket/key', 'bucket/key', S3 console links (…/s3/buckets/bucket?prefix=key,
    …/s3/object/bucket?prefix=key) and object URLs (https://bucket.s3.region.amazonaws.com/key,
    https://s3.region.amazonaws.com/bucket/key).
    """
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
    """
    '' for no bucket (every bucket), else 's3://bucket/prefix' with prefix ending in '/'.
    """
    if not bucket:
        return ""
    return f"s3://{bucket}/{prefix if not prefix or prefix.endswith('/') else prefix + '/'}"


def parent_uri(uri: str) -> str:
    """
    The folder above: 's3://b/x/y/' -> 's3://b/x/', 's3://b/' -> '' (every bucket), '' -> ''.
    """
    bucket, key = parse_location(uri)
    if not bucket or not key:
        return ""
    return folder_uri(bucket, _parent_prefix(key[:-1] if key.endswith("/") else key))


def breadcrumbs(uri: str) -> list[tuple[str, str]]:
    """
    [(label, uri)] from every bucket down to `uri`: [('All buckets', ''), ('b', 's3://b/'), ('x', 's3://b/x/')].
    """
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
    """
    🪣 bucket, 📁 folder, or a file's icon by its type (📊 tables, 📕 PDF, 🖼️ images, 🧠 models, ...), the same
    icons the reports put in front of keys (_kit's _file_icon). `s3` isn't needed any more; it's kept so older calls
    still work.
    """
    if entry.kind == "bucket":
        return "🪣"
    if entry.kind == "folder":
        return "📁"
    return _file_icon(entry.name)


def sort_entries(entries: list[Entry], by: str = "name", descending: bool = False) -> list[Entry]:
    """
    Folders (or buckets) first, then files, each sorted `by` 'name' (natural order: part-2 before part-10),
    'size' or 'modified'. Folders have no size, so a size sort keeps them by name. 'path' sorts by the whole key
    instead, folders and files together, so everything below a folder reads like a tree: each folder, then what's
    in it.
    """
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
    """
    What a filter box's text asks for. Spaces (or commas) separate words, and a name must contain every word, in
    any case. A word that starts with '.' or '*.' is a file type ('.csv', '*.parquet', or 'ext:csv'), and a file
    must be one of the types asked for: '.csv .json' finds both, '.csv' also finds .csv.gz files and '.jpg' .jpeg
    ones. Any other word with * ? or [ is a pattern for the whole name ('part-0*').
    """
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
    """
    The entries that match a filter: names with the words of `text` in them, file types such as '.csv' or
    '.csv .json', patterns such as 'part-0*' (see parse_filter), and only folders or files: kind is 'all',
    'folders' (buckets count as folders) or 'files'.
    """
    if kind not in _KINDS:
        raise ValueError("kind must be 'all', 'folders' or 'files'")
    kind, wanted = _KINDS[kind], parse_filter(text)
    return [e for e in entries if wanted.matches(e) and (kind == "all" or e.is_folder == (kind == "folders"))]


def count_types(entries: list[Entry]) -> list[tuple[str, int, int]]:
    """
    (type, files, bytes) for the files among `entries`, most files first: a filter's choices. Types are what
    '.csv' in a filter finds: .csv.gz files count as csv and .jpeg as jpg. Files without an extension aren't counted.
    """
    found: dict[str, list[int]] = {}
    for entry in entries:
        kind = "" if entry.is_folder else _file_type(entry.name)
        if kind:
            counts = found.setdefault(kind, [0, 0])
            counts[0] += 1
            counts[1] += entry.size or 0
    return sorted(((kind, n, size) for kind, (n, size) in found.items()), key=lambda t: (-t[1], -t[2], t[0]))


def folder_stats(entries: list[Entry], top: int = 8) -> FolderStats:
    """
    Counts, total size, newest / oldest file and the `top` file types by size, for one folder's entries.
    """
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
    """
    What a listing error means and what to do about it, in a sentence.
    """
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


class S3Navigator:
    """
    The explorer's logic, without the UI: lists one level at a time (a page of `page_size` entries per S3
    request), remembers where you've been for back / forward / up, and caches each folder so going back is
    instant. Every method returns the Folder you're now in. Listing errors land in Folder.error instead of
    raising, so a folder you can't read is a note, not a crash. Never prints.

    core: an S3Analyzer (or an S3View, whose analyzer is used); without one, an S3Analyzer is made from
    session / region / profile / client. list_limit: how many entries of one folder list_rest() lists (the
    explorer lists that many, so its search covers them). deep_limit: how many files below() lists at a time.
    """

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
        self.s3 = _s3_module()
        core = getattr(core, "core", core)  # an S3View carries its analyzer as .core
        self.core = core or S3Analyzer(session, region=region, profile=profile, client=client)
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
        """
        Go to a folder. A file's path opens its folder with `focus` set to the file. A path that doesn't
        end in '/' is checked first: 's3://b/data' opens the folder data/ when there is one.
        """
        target, self.focus, self.notice = self._resolve(uri)
        self._go(target)
        return self.folder()

    def back(self) -> Folder:
        """
        The folder you were in before.
        """
        if self._back:
            self._forward.append(self.location)
            self.location = self._back.pop()
        self.focus = self.notice = ""
        return self.folder()

    def forward(self) -> Folder:
        """
        Undo the last back().
        """
        if self._forward:
            self._back.append(self.location)
            self.location = self._forward.pop()
        self.focus = self.notice = ""
        return self.folder()

    def up(self) -> Folder:
        """
        The folder above (from a bucket's top level: every bucket).
        """
        self.focus = self.notice = ""
        if self.location:
            self._go(parent_uri(self.location))
        return self.folder()

    def refresh(self) -> Folder:
        """
        List the current folder again (other folders stay cached).
        """
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
        """
        The current folder (or `uri`), listed once (its first page) and then cached.
        """
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
        """
        The next page of a folder with more entries than one listing returns; below=True: the next deep_limit
        files of below()'s listing.
        """
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
        """
        The current folder (or `uri`) with the rest of its entries listed, one S3 request per 1,000, until S3 has
        no more or it has listed `limit` of them (list_limit, 10,000, unless given); more() goes on from there. This
        is what the explorer does, so its search, counts and sort cover the whole folder.
        progress(entries) is called after each request.
        """
        folder = self.folder(uri)
        if folder.more:
            self._list(folder, (self.list_limit if limit is None else limit) - folder.listed, progress)
        return folder

    def below(self, uri: str | None = None, progress: Callable[[int], None] | None = None) -> Folder:
        """
        Everything below the current folder (or `uri`), not only its first level: every file, and each folder
        between it and them, the first deep_limit (10,000) files; more(below=True) lists the next ones. It's one S3
        request per 1,000 files. Cached like folder(); on the list of buckets it's the buckets.
        progress(files) is called after each request.
        """
        return self._deep(uri, self.deep_limit, progress)

    def lookup(self, text: str) -> int:
        """
        Find entries whose names start with `text` in S3, for a folder too big to load: they're added to
        the current folder. Returns how many weren't loaded yet.
        """
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
        """
        below(), listing only the first `keys` keys when it isn't cached yet (the explorer lists one page, then
        the rest in the background).
        """
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
        """
        A folder's first page, or every bucket.
        """
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
        """
        Note why a folder couldn't be listed, in place of raising.
        """
        if isinstance(exc, ClientError):
            error = exc.response.get("Error", {})
            folder.error_code = str(error.get("Code", "Error"))
            folder.error = f"{folder.error_code}: {error.get('Message', exc)}"
        else:
            folder.error_code, folder.error = type(exc).__name__, f"{type(exc).__name__}: {exc}"

    def _list(self, folder: Folder, keys: int, progress: Callable[[int], None] | None = None) -> None:
        """
        List up to `keys` more of a folder (from its first page when it has none yet), a page per request; an
        error lands in folder.error. progress(entries, or files for a deep folder) after each request.
        """
        try:
            while keys > 0 and (folder.more or not folder.requests):
                keys -= self._add(folder, self._request(folder, folder.token, min(self.page_size, keys)))
                if progress is not None:
                    progress(sum(not e.is_folder for e in folder.entries) if folder.deep else len(folder.entries))
        except (ClientError, BotoCoreError) as exc:
            self._failed(folder, exc)

    def _request(self, folder: Folder, token: str | None, keys: int) -> dict[str, Any]:
        """
        One ListObjectsV2 page of a folder, from `token` on: its first level (S3's '/' delimiter), or every key
        below it for a deep folder. It changes nothing, so it can run on a worker thread; _add adds the page.
        """
        bucket, prefix = parse_location(folder.uri)
        request: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": max(1, min(keys, 1000))}
        if not folder.deep:
            request["Delimiter"] = "/"
        if token:
            request["ContinuationToken"] = token
        return self.core.client.list_objects_v2(**request)

    def _add(self, folder: Folder, page: dict[str, Any]) -> int:
        """
        Add a page from _request to its folder (nothing twice) and move on its token; returns how many keys and
        folders the page had.
        """
        folder.entries += self._entries(folder, page, {entry.key for entry in folder.entries})
        folder.token = page.get("NextContinuationToken") if page.get("IsTruncated") else None
        folder.more = folder.token is not None
        folder.requests += 1
        listed = len(page.get("Contents", [])) + len(page.get("CommonPrefixes", []))
        folder.listed += listed
        return listed

    @staticmethod
    def _entries(folder: Folder, page: dict[str, Any], known: set[str]) -> list[Entry]:
        """
        The entries a ListObjectsV2 page adds to a folder, leaving out the keys in `known` (and adding the new
        ones to it). A deep folder gets each file and each folder on the way to it (S3 has no folders of its own; a
        folder's marker object becomes the folder); a folder's own marker object isn't one of its files.
        """
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
        """
        (folder to open, file to focus, notice) for a path someone typed or pasted.
        """
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
