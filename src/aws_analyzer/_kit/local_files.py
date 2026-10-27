"""
What aws-analyzer saved on this machine, found and removed: the downloads folders S3 and Lambda made beside the
notebook, the agent's charts, and the agent sandbox's cache of Pyodide files. No AWS call, and no UI: local_files()
and clean_up() in _kit/housekeeping.py draw these as reports.

Only what aws-analyzer made is ever removed: a downloads folder whose .gitignore it wrote (_made_by_aws_analyzer),
the chart pictures in the charts folder (never another file there), and its own cache folder. The notebook's
folder, the home folder and the ones above them never are (_protected_folder), even when marked.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone

S3_DOWNLOADS = "s3-downloads"
LAMBDA_DOWNLOADS = "lambda-downloads"
AGENT_CHARTS = "agent-charts"
CACHE_NAME = "aws-analyzer"

KINDS = ("s3", "lambda", "sandbox", "charts")
COMES_BACK = ("s3", "lambda", "sandbox")

_MARK_START = "# Made by aws-analyzer"
_CHART_ENDINGS = (".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".vl.json", ".part")

_WHAT = {
    "s3": "S3 downloads",
    "lambda": "Lambda packages and pages",
    "sandbox": "The agent sandbox's files",
    "charts": "The agent's charts",
}
_AGAIN = {
    "s3": "download() fetches a file again",
    "lambda": "download() or how_it_works_page() saves it again",
    "sandbox": "the sandbox downloads them again on its next run",
    "charts": "save_charts() writes an answer's charts again while it's in the notebook",
}


def _protected_folder(folder: str) -> str:
    """
    Why a clean-up must never empty `folder` (absolute), or '': the notebook's folder or one above it, the home
    folder, or a disk's root.
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


def _made_by_aws_analyzer(folder: str) -> bool:
    """
    Whether `folder` has the .gitignore S3's and Lambda's download() write in a downloads folder they make.
    """
    try:
        with open(os.path.join(folder, ".gitignore"), encoding="utf-8") as mark:
            return mark.readline().startswith(_MARK_START)
    except (OSError, UnicodeDecodeError):
        return False


def sandbox_cache() -> str:
    """
    The folder the agent's Python sandbox keeps its downloads in: $XDG_CACHE_HOME (else ~/.cache) / aws-analyzer.
    """
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.abspath(os.path.join(base, CACHE_NAME))


def _is_chart(name: str) -> bool:
    """
    Whether a file in the charts folder is one save_charts() writes: a picture, a Vega-Lite spec, or a half-written
    .part.
    """
    return name.lower().endswith(_CHART_ENDINGS)


@dataclass
class LocalFolder:
    """
    One place aws-analyzer saved files: what it holds, its size, and whether clean_up() may remove it (`kept` says
    why not).
    """

    kind: str
    path: str
    size: int = 0
    files: int = 0
    newest: datetime | None = None
    kept: str = ""
    error: str = ""

    @property
    def what(self) -> str:
        return _WHAT[self.kind]

    @property
    def comes_back(self) -> str:
        return _AGAIN[self.kind]

    @property
    def removable(self) -> bool:
        return not self.kept and not self.error


@dataclass
class LocalFiles:
    """
    Everything aws-analyzer saved on this machine that it knows of, one LocalFolder each.
    """

    folders: list[LocalFolder] = field(default_factory=list)

    @property
    def size(self) -> int:
        return sum(folder.size for folder in self.folders)


@dataclass
class Cleanup:
    """
    What clean_up() removed (or with dry_run, would remove), what it kept and why, and what failed.
    """

    removed: list[LocalFolder] = field(default_factory=list)
    kept: list[LocalFolder] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    dry_run: bool = False

    @property
    def freed(self) -> int:
        return sum(folder.size for folder in self.removed if folder.path not in self.failed)


def _measure(folder: LocalFolder, *, only_charts: bool = False) -> None:
    """
    Adds up the files in `folder` (not following links): their count, size and newest change. With only_charts,
    only the files save_charts() writes, at the top of the folder.
    """
    newest = 0.0
    try:
        for root, folders, names in os.walk(folder.path):
            if only_charts:
                folders.clear()
                names = [name for name in names if _is_chart(name)]
            for name in names:
                info = os.lstat(os.path.join(root, name))
                folder.size += info.st_size
                folder.files += 1
                newest = max(newest, info.st_mtime, info.st_ctime)
    except OSError as exc:
        folder.error = exc.strerror or type(exc).__name__
    if newest:
        folder.newest = datetime.fromtimestamp(newest, timezone.utc)


def _downloads_folder(kind: str, path: str) -> LocalFolder:
    """
    A downloads folder, kept unless aws-analyzer made it and it isn't one that holds the notebook.
    """
    folder = LocalFolder(kind, path)
    _measure(folder)
    protected = _protected_folder(path)
    if protected:
        folder.kept = f"it's {protected}"
    elif not _made_by_aws_analyzer(path):
        folder.kept = "aws-analyzer didn't make it (it has no aws-analyzer .gitignore), so it may hold your own files"
    return folder


def _charts_folder(path: str) -> LocalFolder:
    """
    The agent's charts folder: only its chart files count, and clean_up() takes them only when named ('charts'),
    since they're your work.
    """
    folder = LocalFolder("charts", path)
    _measure(folder, only_charts=True)
    return folder


def _sandbox_folder(path: str) -> LocalFolder:
    folder = LocalFolder("sandbox", path)
    _measure(folder)
    return folder


def _named_kinds(kinds: tuple[str, ...]) -> tuple[str, ...]:
    """
    The kinds asked for, checked: 'all' is every one, none is those that come back by themselves (COMES_BACK).
    """
    if not kinds:
        return COMES_BACK
    wanted = tuple(kind.strip().lower() for kind in kinds)
    if "all" in wanted:
        return KINDS
    unknown = [kind for kind in wanted if kind not in KINDS]
    if unknown:
        raise ValueError(f"clean_up() takes {', '.join(map(repr, KINDS))} or 'all'; got {', '.join(map(repr, unknown))}")
    return wanted


def find_local_files(*, folder: str | os.PathLike | None = None,
                     downloads: list[str | os.PathLike] | None = None) -> LocalFiles:
    """
    Everything aws-analyzer saved on this machine that it knows of: s3-downloads/, lambda-downloads/ and agent-charts/
    in `folder` (the notebook's, by default), any other downloads folders you name (S3View(downloads=...)'s), and the
    agent sandbox's cache. Only what's there is listed.
    """
    base = os.path.abspath(os.path.expanduser(str(folder or os.getcwd())))
    places = [("s3", os.path.join(base, S3_DOWNLOADS)), ("lambda", os.path.join(base, LAMBDA_DOWNLOADS))]
    for extra in downloads or []:
        path = os.path.abspath(os.path.expanduser(str(extra)))
        kind = "lambda" if os.path.basename(path).startswith("lambda") else "s3"
        places.append((kind, path))
    found = LocalFiles()
    seen: set[str] = set()
    for kind, path in places:
        if path not in seen and os.path.isdir(path):
            seen.add(path)
            found.folders.append(_downloads_folder(kind, path))
    charts = os.path.join(base, AGENT_CHARTS)
    if os.path.isdir(charts):
        found.folders.append(_charts_folder(charts))
    cache = sandbox_cache()
    if os.path.isdir(cache):
        found.folders.append(_sandbox_folder(cache))
    return found


def _remove(folder: LocalFolder) -> None:
    """
    Removes what clean_up() may of `folder`: the chart files in the charts folder (and the folder, once empty), or the
    whole folder otherwise.
    """
    if folder.kind != "charts":
        shutil.rmtree(folder.path)
        return
    for name in os.listdir(folder.path):
        path = os.path.join(folder.path, name)
        if _is_chart(name) and os.path.isfile(path) and not os.path.islink(path):
            os.remove(path)
    if not os.listdir(folder.path):
        os.rmdir(folder.path)


def remove_local_files(*kinds: str, folder: str | os.PathLike | None = None,
                       downloads: list[str | os.PathLike] | None = None, dry_run: bool = False) -> Cleanup:
    """
    Removes what aws-analyzer saved, of the kinds named ('s3', 'lambda', 'sandbox', 'charts' or 'all'; by default
    the ones that come back by themselves: s3, lambda and sandbox). A folder aws-analyzer didn't make, or one that
    holds the notebook, is kept and says why. dry_run=True removes nothing and says what would go.
    """
    wanted = _named_kinds(kinds)
    result = Cleanup(dry_run=dry_run)
    for found in find_local_files(folder=folder, downloads=downloads).folders:
        if found.kind not in wanted:
            found.kept = found.kept or f"not asked for (clean_up('{found.kind}') removes it)"
        if not found.removable:
            result.kept.append(found)
            continue
        result.removed.append(found)
        if dry_run:
            continue
        try:
            _remove(found)
        except OSError as exc:
            result.failed[found.path] = exc.strerror or type(exc).__name__
    return result
