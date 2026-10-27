"""
local_files() and clean_up(), public as aws_analyzer.local_files and aws_analyzer.clean_up: what aws-analyzer saved
on this machine as a report, and the command that removes it. The finding and removing is local_files.py's; this
module draws it.
"""

from __future__ import annotations

import os
from typing import Any

from .blocks import _Cards, _Findings, _Next, _Note, _Table, _Title
from .fmt import _plural, human_age, human_size
from .local_files import COMES_BACK, Cleanup, LocalFiles, LocalFolder, find_local_files, remove_local_files
from .render import _base_css, _call, _Look, _report_html
from .view import _BaseView

_FILES_ROOT = "aal"
_FILES_CSS = _base_css(_FILES_ROOT, "")
_FILES_BADGE = "🧹 Local files"
_FILES_LOOK = _Look(_FILES_CSS, _FILES_ROOT, _FILES_BADGE)

_NEVER_TOUCHED = (
    "Never touched: Python environments new_env() made (SageMakerView().envs() shows how to remove each), files you "
    "saved to a path you named, the chat's save_runs() logs and the file lists save_file_list() wrote."
)


def _files_html(blocks: list[Any], max_rows: int, theme: str = "") -> str:
    """
    A local-files report as HTML, in `theme` when one is named (set_theme's).
    """
    return _report_html(blocks, max_rows, _FILES_LOOK, theme=theme)


class _LocalFilesView(_BaseView):
    """
    The View local_files() and clean_up() show their reports with: no commands of its own.
    """

    _render_html = staticmethod(_files_html)


def _short_path(path: str) -> str:
    """
    A path from the notebook's folder when it's under it, else from the home folder ('~/.cache/aws-analyzer').
    """
    here, home = os.getcwd(), os.path.expanduser("~")
    for base, prefix in ((here, ""), (home, "~/")):
        if path.startswith(base.rstrip(os.sep) + os.sep):
            return prefix + os.path.relpath(path, base)
    return path


def _folder_row(folder: LocalFolder) -> list[str]:
    return [folder.what, _short_path(folder.path), f"{folder.files:,}", human_size(folder.size), human_age(folder.newest)]


def _kept_findings(kept: list[LocalFolder]) -> list[tuple[str, str]]:
    """
    Why each folder stays, as info findings: not made by aws-analyzer, holds the notebook, or not asked for.
    """
    findings = []
    for folder in kept:
        reason = folder.error or folder.kept
        findings.append(("info", f"{folder.what} in {_short_path(folder.path)} stay: {reason}."))
    return findings


def _listing_blocks(found: LocalFiles, base: str) -> list[Any]:
    removable = [folder for folder in found.folders if folder.removable]
    by_default = [folder for folder in removable if folder.kind in COMES_BACK]
    blocks: list[Any] = [
        _Title("What aws-analyzer saved here", f"beside {base}, and its cache"),
        _Cards([
            ("Folders", f"{len(found.folders):,}"),
            ("Files", f"{sum(folder.files for folder in found.folders):,}"),
            ("Size", human_size(found.size)),
            ("clean_up() frees", human_size(sum(folder.size for folder in by_default))),
        ]),
        _Findings(_kept_findings([folder for folder in found.folders if not folder.removable])),
        _Table(["What", "Folder", "Files", "Size", "Last written", "Comes back"],
               [[*_folder_row(folder), folder.comes_back] for folder in found.folders],
               title="Saved by aws-analyzer", path_cols=(1,), prose_cols=(5,)),
        _Note(_NEVER_TOUCHED),
        _Next([
            (_call("clean_up", dry_run=True), "what clean_up() would remove, removing nothing"),
            (_call("clean_up"), "remove the downloads and the sandbox's cache: everything that comes back by itself"),
            (_call("clean_up", "all"), "the agent's charts too"),
        ]),
    ]
    return blocks


def local_files(*, folder: str | os.PathLike | None = None, downloads: list[str | os.PathLike] | None = None,
                mode: str = "auto") -> None:
    """
    What aws-analyzer saved on this machine: the S3 and Lambda downloads folders beside the notebook, the agent's
    charts and the agent sandbox's cache, each with its size, when it was last written and how it comes back, and
    the clean_up() calls that remove them.

    folder= is the notebook folder to look in (this one by default); downloads= adds downloads folders you moved
    elsewhere (S3View(downloads='~/scratch/s3')'s).
    """
    found = find_local_files(folder=folder, downloads=downloads)
    base = _short_path(os.path.abspath(os.path.expanduser(str(folder or os.getcwd())))) or "."
    view = _LocalFilesView(mode=mode)
    if not found.folders:
        view._show([_Title("What aws-analyzer saved here", f"beside {base}, and its cache"),
                    _Note("Nothing: no downloads, charts or sandbox files are saved on this machine."),
                    _Note(_NEVER_TOUCHED)])
        return
    view._show(_listing_blocks(found, base))


def _cleanup_title(result: Cleanup) -> _Title:
    if not result.removed:
        return _Title("Nothing to remove", "aws-analyzer's local files")
    if result.dry_run:
        return _Title(f"Would remove {_plural(len(result.removed), 'folder')}", "aws-analyzer's local files")
    removed = len(result.removed) - len(result.failed)
    return _Title(f"Removed {_plural(removed, 'folder')}", "aws-analyzer's local files")


def _cleanup_blocks(result: Cleanup, kinds: tuple[str, ...]) -> list[Any]:
    freed = "Would free" if result.dry_run else "Freed"
    failed = [("warn", f"Couldn't remove {_short_path(path)}: {why}. Close what has its files open, then run clean_up() "
                       f"again.") for path, why in result.failed.items()]
    blocks: list[Any] = [
        _cleanup_title(result),
        _Cards([(freed, human_size(result.freed)), ("Kept", f"{len(result.kept):,}"),
                *((("Failed", f"{len(result.failed):,}", "warn"),) if result.failed else ())]),
        _Findings(failed + _kept_findings(result.kept)),
    ]
    if result.removed:
        blocks.append(_Table(["What", "Folder", "Files", "Size", "Last written", "Comes back"],
                             [[*_folder_row(folder), folder.comes_back] for folder in result.removed],
                             title="Would be removed" if result.dry_run else "Removed",
                             path_cols=(1,), prose_cols=(5,)))
    blocks.append(_Note(_NEVER_TOUCHED))
    if result.dry_run and result.removed:
        blocks.append(_Next([(_call("clean_up", *kinds), "remove them")]))
    return blocks


def clean_up(*kinds: str, folder: str | os.PathLike | None = None, downloads: list[str | os.PathLike] | None = None,
             dry_run: bool = False, mode: str = "auto") -> None:
    """
    Remove what aws-analyzer saved on this machine to free the disk: by default what comes back by itself (S3 and
    Lambda downloads beside the notebook, and the agent sandbox's cache in ~/.cache/aws-analyzer). clean_up('charts')
    removes the agent's charts, clean_up('all') everything; dry_run=True only shows what would go.

    It removes only what aws-analyzer made: a downloads folder without its .gitignore, or one that holds the
    notebook, is kept and the report says why, and only chart pictures leave agent-charts/. Nothing in AWS changes,
    and it never removes Python environments or files you saved to a path of your own. local_files() lists what's
    there first.
    """
    view = _LocalFilesView(mode=mode)
    try:
        result = remove_local_files(*kinds, folder=folder, downloads=downloads, dry_run=dry_run)
    except ValueError as exc:
        view._show([_Note(f"ValueError: {exc}  [clean_up]", "warn")])
        return
    view._show(_cleanup_blocks(result, kinds))
