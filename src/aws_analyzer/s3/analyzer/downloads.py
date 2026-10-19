"""
S3Analyzer's downloads and the downloads folder, with the guards that keep
clean_downloads() to a folder it made: the one place aws-analyzer deletes files.
"""

from __future__ import annotations

import difflib
import os
import posixpath
import shutil
import threading
import time
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Iterator

from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.errors import _error_code
from ..._kit.fmt import file_extension, human_size, parse_size, parse_time

from ..helpers import KB, MB, base_prefix, parse_s3_uri, relative_key, s3_uri
from ..prices import ARCHIVE_CLASSES
from ..models import DownloadsCleanup, DownloadsFolder, FolderDownload, LocalDownload, ObjectInfo, ZipDownload
from ..readers.data import _zip_time_of
from ..analysis.downloads import DOWNLOADS, _ALREADY_COMPRESSED, _DOWNLOADS_MARK, _ZIP_SMALL_FILE, ZipPlan
from .threads import _pool_size, _run_in_threads


def _free_space(path: str) -> int:
    """
    Bytes free on the disk that holds `path` (or the nearest folder above it that exists).
    """
    folder = os.path.abspath(path)
    while not os.path.isdir(folder):
        folder = os.path.dirname(folder)
    return shutil.disk_usage(folder).free


def _protected_folder(folder: str) -> str:
    """
    Why clean_downloads must never empty `folder` (absolute), or '': the notebook's folder or one above it, the
    home folder, or a disk's root.
    """
    here = os.path.abspath(os.getcwd())
    if folder == here:
        return "the notebook's own folder"
    if os.path.dirname(folder) == folder:
        return "the root of the disk"
    if folder == os.path.abspath(os.path.expanduser("~")):
        return "your home folder"
    if here.startswith(folder.rstrip(os.sep) + os.sep):
        return "a folder that holds the notebook's own"
    return ""


def _made_for_downloads(folder: str) -> bool:
    """
    Whether `folder` has the .gitignore download() puts in a downloads folder it makes (_DOWNLOADS_MARK).
    """
    try:
        with open(os.path.join(folder, ".gitignore"), encoding="utf-8") as mark:
            return mark.readline() == _DOWNLOADS_MARK.split("\n")[0] + "\n"
    except (OSError, UnicodeDecodeError):
        return False


def _local_download(path: str, name: str) -> LocalDownload:
    """
    One entry of the downloads folder: its size, files and when it was last written (a folder's newest file).
    Downloads keep S3's time as their modified time, so the change time says when they were downloaded.
    """
    info = os.lstat(path)
    newest, size, files = max(info.st_mtime, info.st_ctime), info.st_size, 1
    is_folder = os.path.isdir(path) and not os.path.islink(path)
    if is_folder:
        size = files = 0
        for root, _, names in os.walk(path):  # doesn't follow links
            for file in names:
                try:
                    found = os.lstat(os.path.join(root, file))
                except OSError:
                    continue
                size, files = size + found.st_size, files + 1
                newest = max(newest, found.st_mtime, found.st_ctime)
    lower = name.lower()
    kind = ("folder" if is_folder else "unfinished" if lower.endswith(".zip.part")
            else "zip" if lower.endswith(".zip") else "file")
    return LocalDownload(name, path, kind, size, files, datetime.fromtimestamp(newest, timezone.utc))


def _memory_available() -> int | None:
    """
    RAM the notebook can still use (MemAvailable on Linux), or None when it can't be read.
    """
    try:
        with open("/proc/meminfo") as meminfo:
            for line in meminfo:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * KB
    except (OSError, ValueError, IndexError):
        pass
    try:
        return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return None


def _zip_layout(bucket: str, keys: list[str]) -> tuple[str, str]:
    """
    For zipping several keys together -> (the folder they share, the zip's name without .zip): 'churn-12-files'
    from the folder and how many there are ('-items' when some are folders), or the one key's own name.
    """
    base = base_prefix(
        os.path.commonprefix([key if key.endswith("/") else base_prefix(key) for key in keys])
    )
    if len(keys) == 1:
        return base, keys[0].rstrip("/").rsplit("/", 1)[-1] or bucket
    files = all(not key.endswith("/") for key in keys)
    return base, f"{base.rstrip('/').rsplit('/', 1)[-1] or bucket}-{len(keys)}-{'files' if files else 'items'}"


def _zip_name(key: str, base: str, used: set[str]) -> str | None:
    """
    The name a file gets in the zip, relative to `base`; None if it would unzip outside the folder or clash.
    """
    name = relative_key(key, base)
    if name.startswith("/"):
        return None
    name = posixpath.normpath(name)
    if name in (".", "..") or name.startswith("../") or name in used:
        return None
    used.add(name)
    return name


def _check_disk_space(path: str, needed: int) -> None:
    folder = os.path.abspath(path)
    while not os.path.isdir(folder):
        folder = os.path.dirname(folder)
    free = _free_space(folder)
    if needed > free:
        raise ValueError(
            f"Not enough disk space: {human_size(needed)} to download, {human_size(free)} free in "
            f"{folder}. Pass limit=, or a path on a bigger disk."
        )


class _DownloadReads:
    """
    S3Analyzer's downloads: a file or folder, or one .zip of them, into the downloads folder, and what's
    there. clean_downloads() is the one method in aws-analyzer that deletes files, and only from a downloads
    folder it made.
    """

    def download(
        self,
        uri: str,
        path: str | None = None,
        *,
        progress: Callable[[int, int], None] | None = None,
    ) -> str:
        """
        Download one file to `path` (a file or directory; default: the downloads folder, see downloads_folder()).
        Returns the local path. Refuses when the disk hasn't room. progress gets (bytes downloaded, file size).
        """
        bucket, key = parse_s3_uri(uri)
        path = path or os.path.join(self.downloads_folder(), os.path.basename(key))
        if os.path.isdir(path):
            path = os.path.join(path, os.path.basename(key))
        head = self.client.head_object(Bucket=bucket, Key=key)
        _check_disk_space(path, head["ContentLength"])
        failed = self._fetch(
            [(bucket, key, path, head["ContentLength"], head.get("LastModified"))],
            progress,
        )
        if failed:
            raise failed[key]
        return os.path.abspath(path)

    def download_folder(
        self,
        uri: str,
        path: str | None = None,
        *,
        limit: int | None = None,
        max_workers: int = 8,
        progress: Callable[[int, int], None] | None = None,
        list_progress: Callable[[int], None] | None = None,
    ) -> FolderDownload:
        """
        Download every file under a folder into `path` (default: a folder of the same name in the downloads
        folder, see downloads_folder()), keeping the sub-folders. A file already there with the same size and time
        is skipped, so running it again resumes. GLACIER / DEEP_ARCHIVE files are skipped (they need a restore
        first). Refuses when the disk hasn't room. progress gets (bytes downloaded, bytes to download).
        """
        bucket, prefix = parse_s3_uri(uri)
        if prefix and not prefix.endswith("/"):
            prefix += "/"  # 's3://b/data' means the folder data/, not also data-old/
        root = os.path.abspath(
            path or os.path.join(self.downloads_folder(), prefix.rstrip("/").rsplit("/", 1)[-1] or bucket)
        )
        result = FolderDownload(uri=s3_uri(bucket, prefix), path=root)
        started = time.monotonic()
        jobs = []
        listing = self.iter_objects(
            result.uri,
            limit=None if limit is None else limit + 1,
            progress=list_progress,
        )
        for i, obj in enumerate(listing):
            if limit is not None and i >= limit:
                result.truncated = True
                break
            local = os.path.normpath(os.path.join(root, relative_key(obj.key, prefix)))
            if obj.is_folder_marker or obj.key.endswith("/"):
                continue
            if not local.startswith(root + os.sep):
                result.skipped[obj.key] = "its name leads outside the folder"
            elif obj.storage_class in ARCHIVE_CLASSES:
                result.skipped[obj.key] = obj.storage_class
            elif (
                os.path.isfile(local)
                and os.path.getsize(local) == obj.size
                and int(os.path.getmtime(local)) == int(obj.last_modified.timestamp())
            ):
                result.already_there.add(obj.size)
            else:
                jobs.append((bucket, obj.key, local, obj.size, obj.last_modified))
        _check_disk_space(root, sum(job[3] for job in jobs))
        failed = self._fetch(jobs, progress, max_workers)
        for _, key, _, size, _ in jobs:
            if key in failed:
                error = failed[key]
                result.skipped[key] = (
                    _error_code(error) if isinstance(error, ClientError) else str(error)
                )
            else:
                result.downloaded.add(size)
        result.seconds = time.monotonic() - started
        return result

    def _fetch(
        self,
        jobs: list[tuple[str, str, str, int, datetime | None]],
        progress: Callable[[int, int], None] | None,
        max_workers: int = 1,
    ) -> dict[str, Exception]:
        """
        Download (bucket, key, local path, size, last modified) jobs in parallel, stamping each file with
        the object's time. Returns {key: error} for the ones that failed (AWS errors, a full disk).
        """
        total, done, lock, stop = (
            sum(job[3] for job in jobs),
            [0],
            threading.Lock(),
            threading.Event(),
        )

        def add(
            count: int,
        ) -> None:  # boto3 calls this from its own threads as bytes arrive
            if stop.is_set():
                raise RuntimeError("Download stopped")
            with lock:
                done[0] += count

        def one(job: tuple[str, str, str, int, datetime | None]) -> None:
            bucket, key, local, _, modified = job
            os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
            self.client.download_file(bucket, key, local, Callback=add)
            if modified is not None:
                os.utime(local, (modified.timestamp(), modified.timestamp()))

        def report() -> None:
            if progress:
                progress(done[0], total)

        self._make_downloads_folder([job[2] for job in jobs])
        report()
        failed: dict[str, Exception] = {}
        for job, _, error in _run_in_threads(
            one, jobs, min(max_workers, _pool_size(self.client)), report, stop
        ):
            if isinstance(error, (ClientError, BotoCoreError, OSError)):
                failed[job[1]] = error
            elif error is not None:
                raise error
        return failed

    def plan_zip(
        self,
        uri: str | Iterable[str | ObjectInfo],
        path: str | None = None,
        *,
        max_size: int | str = "100MB",
        max_files: int = 10_000,
        progress: Callable[[int], None] | None = None,
    ) -> ZipPlan:
        """
        Check whether a file or folder can be zipped here, without downloading it: what would go in, the size
        and file-count limits, free disk space and memory, and whether the files can be read (one 1-byte read).
        `path` is the .zip to write (default: named after the folder, in the downloads folder, see
        downloads_folder()). `uri` can also be a list of files and folders from one bucket (s3:// paths, or
        ObjectInfo from a listing): they go in one zip, laid out as they are under the folder they share, which
        names it ('churn-12-files.zip').
        """
        limit = parse_size(max_size)
        if limit is None:
            raise ValueError("max_size can't be None; pass a size such as '2GB'")
        if not isinstance(uri, str):
            bucket, base, name, picked, listing = self._zip_picks(list(uri), progress)
            plan_uri = s3_uri(bucket, base)
        else:
            picked = []
            bucket, key = parse_s3_uri(uri)
            first = self.client.list_objects_v2(Bucket=bucket, Prefix=key, MaxKeys=1).get(
                "Contents", []
            )
            single = (
                bool(key)
                and not key.endswith("/")
                and bool(first)
                and first[0]["Key"] == key
            )
            if single:  # a file (it lists first among the keys it prefixes)
                base, name = base_prefix(key), key.rsplit("/", 1)[-1]
            else:
                base = (
                    key if not key or key.endswith("/") else key + "/"
                )  # 's3://b/data' means the folder data/
                name = base.rstrip("/").rsplit("/", 1)[-1] or bucket
            plan_uri = s3_uri(bucket, key if single else base)
            if single:
                item = first[0]
                listing: Iterable[ObjectInfo] = [
                    ObjectInfo(
                        bucket,
                        key,
                        item["Size"],
                        item["LastModified"],
                        item.get("StorageClass", "STANDARD"),
                        item.get("ETag", "").strip('"'),
                    )
                ]
            else:
                listing = self.iter_objects(plan_uri, progress=progress)
        if path is None:
            path = os.path.join(self.downloads_folder(), f"{name}.zip")
        elif os.path.isdir(path):
            path = os.path.join(path, f"{name}.zip")
        elif not path.lower().endswith(".zip"):
            path += ".zip"
        plan = ZipPlan(
            uri=plan_uri,
            path=os.path.abspath(path),
            max_size=limit,
            max_files=max_files,
            picked=picked,
        )
        used_keys: set[str] = set()
        used: set[str] = set()
        for obj in listing:
            if obj.is_folder_marker or obj.key.endswith("/"):
                continue
            if obj.key in used_keys:  # a file picked on its own and inside a picked folder
                continue
            used_keys.add(obj.key)
            if len(plan.files) + len(plan.left_out) >= max_files:
                plan.more = True
                break
            name_in_zip = _zip_name(obj.key, base, used)
            if obj.storage_class in ARCHIVE_CLASSES:
                plan.archived.add(obj.size)
                plan.left_out[obj.key] = obj.storage_class
            elif name_in_zip is None:
                plan.left_out[obj.key] = (
                    "its name would unzip outside the folder, or clash with another file's"
                )
            else:
                plan.files.append((obj, name_in_zip))
        try:
            plan.disk_free = _free_space(os.path.dirname(plan.path))
        except OSError:
            pass
        plan.memory_free = _memory_available()
        probe = next((obj for obj, _ in plan.files if obj.size), None)
        if probe is not None:
            plan.probed = probe.key
            try:
                self.client.get_object(Bucket=bucket, Key=probe.key, Range="bytes=0-0")[
                    "Body"
                ].close()
            except ClientError as exc:
                plan.read_error = _error_code(exc)
        return plan

    def _zip_picks(
        self, items: list[str | ObjectInfo], progress: Callable[[int], None] | None
    ) -> tuple[str, str, str, list[str], Iterator[ObjectInfo]]:
        """
        plan_zip for a list -> (bucket, the folder they share, the zip's name, the s3:// paths, every file in
        them). A path without a '/' at the end is a file when there is one with that key, else a folder.
        """
        if not items:
            raise ValueError("Nothing to zip: pass at least one file or folder")
        found = [
            (item.bucket, item.key, item)
            if isinstance(item, ObjectInfo)
            else (*parse_s3_uri(item), None)
            for item in items
        ]
        buckets = {bucket for bucket, _, _ in found}
        if len(buckets) != 1 or "" in buckets:
            raise ValueError(
                f"The files to zip must all be in one bucket, not {', '.join(sorted(buckets)) or 'none'}: "
                "make one zip per bucket"
            )
        bucket = found[0][0]
        base, name = _zip_layout(bucket, [key for _, key, _ in found])
        picked = [s3_uri(bucket, key) for _, key, _ in found]

        def listing() -> Iterator[ObjectInfo]:
            listed = 0
            for at, (_, key, info) in enumerate(found):
                if info is not None and not key.endswith("/"):
                    listed += 1
                    yield info
                    continue
                if key and not key.endswith("/"):
                    first = self.client.list_objects_v2(
                        Bucket=bucket, Prefix=key, MaxKeys=1
                    ).get("Contents", [])
                    if first and first[0]["Key"] == key:
                        item = first[0]
                        listed += 1
                        yield ObjectInfo(
                            bucket,
                            key,
                            item["Size"],
                            item["LastModified"],
                            item.get("StorageClass", "STANDARD"),
                            item.get("ETag", "").strip('"'),
                        )
                        continue
                    key += "/"
                    picked[at] = s3_uri(bucket, key)  # it was a folder
                done = listed
                for obj in self.iter_objects(
                    s3_uri(bucket, key),
                    progress=(lambda n: progress(done + n)) if progress else None,
                ):
                    listed += 1
                    yield obj

        return bucket, base, name, picked, listing()

    def download_zip(
        self,
        uri: str | Iterable[str | ObjectInfo],
        path: str | None = None,
        *,
        max_size: int | str = "100MB",
        max_files: int = 10_000,
        dry_run: bool = False,
        max_workers: int = 8,
        progress: Callable[[int, int], None] | None = None,
        list_progress: Callable[[int], None] | None = None,
    ) -> ZipDownload:
        """
        Zip a file or a folder (with its sub-folders) into one .zip on the notebook's disk, after plan_zip's
        checks pass: at most max_size of files (100 MB by default) and max_files files, room on the disk, and read
        access. Nothing is written when a check fails, or with dry_run=True. Already-compressed files (parquet,
        gz, images, ...) are stored as they are, the rest compressed. progress gets (bytes zipped, bytes to zip).
        `uri` can be a list of files and folders from one bucket too (see plan_zip).
        """
        plan = self.plan_zip(
            uri, path, max_size=max_size, max_files=max_files, progress=list_progress
        )
        result = ZipDownload(plan)
        if dry_run or not plan.can_download:
            return result
        started, done = time.monotonic(), [0]

        def report() -> None:
            if progress:
                progress(done[0], plan.size)

        window = (
            16
            if plan.memory_free is None or plan.memory_free > 2 * 16 * _ZIP_SMALL_FILE
            else 2
        )
        part = (
            plan.path + ".part"
        )  # renamed once complete, so a stopped zip never looks finished
        self._make_downloads_folder([plan.path])
        os.makedirs(os.path.dirname(plan.path), exist_ok=True)
        try:
            with (
                zipfile.ZipFile(part, "w", allowZip64=True) as archive,
                ThreadPoolExecutor(
                    max_workers=max(1, min(max_workers, _pool_size(self.client)))
                ) as pool,
            ):
                ahead: deque[tuple[ObjectInfo, str, Any]] = deque()
                pending = iter(plan.files)

                def fetch_ahead() -> None:
                    while len(ahead) < window:
                        item = next(pending, None)
                        if item is None:
                            return
                        obj, name = item
                        small = obj.size <= _ZIP_SMALL_FILE
                        ahead.append(
                            (
                                obj,
                                name,
                                pool.submit(self._read_object, obj) if small else None,
                            )
                        )

                fetch_ahead()
                report()
                try:
                    while ahead:
                        obj, name, future = ahead.popleft()
                        fetch_ahead()
                        info = zipfile.ZipInfo(
                            name, date_time=_zip_time_of(obj.last_modified)
                        )
                        info.compress_type = (
                            zipfile.ZIP_STORED
                            if file_extension(name).rsplit(".", 1)[-1]
                            in _ALREADY_COMPRESSED
                            else zipfile.ZIP_DEFLATED
                        )
                        info.file_size = (
                            obj.size
                        )  # lets zipfile pick ZIP64 for files over 2 GB
                        info.external_attr = (
                            0o644 << 16
                        )  # unzipped files: readable, writable by you
                        try:
                            if future is not None:
                                data = future.result()
                                archive.writestr(info, data)
                                done[0] += len(data)
                            else:
                                self._stream_into(archive, info, obj, done, report)
                        except (ClientError, BotoCoreError) as exc:
                            result.failed[obj.key] = (
                                _error_code(exc)
                                if isinstance(exc, ClientError)
                                else type(exc).__name__
                            )
                            continue
                        result.files.add(obj.size)
                        report()
                except BaseException:
                    for _, _, future in ahead:
                        if future is not None:
                            future.cancel()
                    raise
            os.replace(part, plan.path)
        except BaseException:
            if os.path.exists(part):
                os.remove(part)
            raise
        result.written, result.zip_size = True, os.path.getsize(plan.path)
        result.seconds = time.monotonic() - started
        return result

    def _read_object(self, obj: ObjectInfo) -> bytes:
        match = (
            {"IfMatch": f'"{obj.etag}"'} if obj.etag else {}
        )  # the file listed, not one written since
        return self.client.get_object(Bucket=obj.bucket, Key=obj.key, **match)[
            "Body"
        ].read()

    def _stream_into(
        self,
        archive: zipfile.ZipFile,
        info: zipfile.ZipInfo,
        obj: ObjectInfo,
        done: list[int],
        report: Callable[[], None],
    ) -> None:
        """
        Copy a big object into the zip 1 MB at a time, so it never sits in memory whole.
        """
        match = {"IfMatch": f'"{obj.etag}"'} if obj.etag else {}
        body = self.client.get_object(Bucket=obj.bucket, Key=obj.key, **match)["Body"]
        try:
            with archive.open(info, "w") as entry:
                while chunk := body.read(MB):
                    entry.write(chunk)
                    done[0] += len(chunk)
                    report()
        finally:
            body.close()

    # ------------------------------------------------------------------ the downloads folder (local, no AWS calls)

    def downloads_folder(self) -> str:
        """
        The folder downloads go into when they're given no path (absolute): `self.downloads`, 's3-downloads'
        next to the notebook unless S3Analyzer(downloads=...) or S3View(downloads=...) says otherwise.
        """
        return os.path.abspath(os.path.expanduser(str(self.downloads).strip() or "."))

    def _make_downloads_folder(self, paths: Iterable[str]) -> None:
        """
        Before writing `paths`: when one goes in the downloads folder and that folder is new (or empty), make it
        with aws-analyzer's .gitignore, which keeps it out of git and lets clean_downloads empty it.
        """
        folder = self.downloads_folder()
        if _protected_folder(folder) or not any(
            os.path.abspath(path).startswith(folder.rstrip(os.sep) + os.sep) for path in paths
        ):
            return
        try:
            if os.path.isdir(folder) and os.listdir(folder):
                return  # it held files already: maybe the user's own, so it isn't marked as one to empty
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, ".gitignore"), "w", encoding="utf-8") as mark:
                mark.write(_DOWNLOADS_MARK)
        except OSError:
            pass  # the download itself says what's wrong with the folder

    def list_downloads(self) -> DownloadsFolder:
        """
        What's in the downloads folder (see downloads_folder()): each file, folder and zip download() and
        download_zip() saved, newest first, with its size, files and when it was downloaded; the disk's free space;
        and whether clean_downloads may empty it (`managed`: a folder made for downloads). The notebook's own
        folder, the home folder and the ones above them (`protected`) aren't listed: what's there isn't downloads.
        """
        path = self.downloads_folder()
        folder = DownloadsFolder(path, exists=os.path.isdir(path), protected=_protected_folder(path))
        if not folder.exists:
            return folder
        folder.managed = _made_for_downloads(path)
        try:
            folder.disk_free = _free_space(path)
        except OSError:
            pass
        if folder.protected:
            return folder
        try:
            names = sorted(os.listdir(path))
        except OSError as exc:
            folder.errors["."] = exc.strerror or type(exc).__name__
            return folder
        for name in names:
            if name == ".gitignore" and folder.managed:
                continue
            try:
                folder.entries.append(_local_download(os.path.join(path, name), name))
            except OSError as exc:
                folder.errors[name] = exc.strerror or type(exc).__name__
        folder.entries.sort(key=lambda entry: entry.downloaded, reverse=True)
        return folder

    def clean_downloads(
        self,
        names: str | Iterable[Any] | None = None,
        *,
        older_than: datetime | date | timedelta | str | None = None,
        dry_run: bool = False,
    ) -> DownloadsCleanup:
        """
        Delete downloads from the downloads folder to free the disk: all of them, the ones named (a name as
        list_downloads gives it, 'churn.zip', or a path to it), or those downloaded before older_than ('7d',
        '2024-05-01'). dry_run=True deletes nothing and says what would go. Nothing in S3 changes.

        Only a folder made for downloads is emptied (it has aws-analyzer's .gitignore, which stays): one that held
        other files first, the notebook's own folder or the home folder raise ValueError, as does a name that
        isn't there.
        """
        folder = self.list_downloads()
        own = f"S3View(downloads={DOWNLOADS!r})"
        if folder.protected:
            raise ValueError(f"{folder.path} is {folder.protected}, so clean_downloads won't empty it. Give downloads "
                             f"a folder of their own: {own}")
        if folder.exists and not folder.managed:
            raise ValueError(f"{folder.path} wasn't made for downloads (it has no aws-analyzer .gitignore), so "
                             "clean_downloads won't empty it, in case it holds your own files. Delete what you don't "
                             f"need there yourself, or give downloads a folder of their own: {own}")
        chosen = folder.entries
        if names is not None:
            by_name = {entry.name: entry for entry in folder.entries}
            wanted = set()
            for item in [names] if isinstance(names, (str, LocalDownload)) else list(names):
                name = self._download_name(item, folder.path)
                if name not in by_name:
                    close = difflib.get_close_matches(os.path.basename(str(name)).rstrip("/"), list(by_name), n=3)
                    hint = f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
                    raise ValueError(f"No download named {str(getattr(item, 'name', item))!r} in {folder.path}."
                                     f"{hint} downloads() lists what's there")
                wanted.add(name)
            chosen = [entry for entry in chosen if entry.name in wanted]
        cutoff = parse_time(older_than)
        if cutoff is not None:
            chosen = [entry for entry in chosen if entry.downloaded < cutoff]
        result = DownloadsCleanup(folder.path, removed=chosen, dry_run=dry_run, older_than=cutoff,
                                  kept=[entry for entry in folder.entries if entry not in chosen])
        if dry_run:
            return result
        for entry in chosen:
            try:
                if entry.is_folder:
                    shutil.rmtree(entry.path)
                else:
                    os.remove(entry.path)
            except OSError as exc:
                result.failed[entry.name] = exc.strerror or type(exc).__name__
        return result

    @staticmethod
    def _download_name(item: Any, folder: str) -> str:
        """
        A name or path given to clean_downloads -> the name of the entry it is in the downloads folder ('churn/',
        's3-downloads/churn.zip', a LocalDownload); '' for a path elsewhere, or inside one of them.
        """
        text = os.path.expanduser(str(getattr(item, "path", item)).strip())
        for base in (os.getcwd(), folder):  # a path from the notebook's folder, or a name in the downloads folder
            full = os.path.abspath(os.path.join(base, text))
            if os.path.dirname(full) == folder:
                return os.path.basename(full)
        return text
