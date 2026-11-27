"""
SageMakerAnalyzer's search across this notebook's own files, like a smart grep: search_files() walks a folder (your
notebooks and data by default), reads each text file and notebook, and returns the files that hold every word of a
search, the best first; read_file() reads one file's lines, a notebook's cells with their text outputs. Both read the
disk only, never run anything, and never read a secrets file, a credentials folder or /proc.
"""

from __future__ import annotations

import os
import stat
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from .helpers import MB
from .models import FileHit, FileSearch, FileText
from .analysis.search import (
    FileQuery, close_words, file_kind, hit_score, is_environment, is_secret_file, is_skipped_folder, match_file,
    near_misses, notebook_rows, parse_file_query, rank_hits, text_rows, unknown_files_parts, wants_file, words_of,
)

MOST_HITS = 200

_FILE_BYTES = 20 * MB

_READ_BYTES = 200 * MB

_MOST_FILES_READ = 20_000

_SEARCH_SECONDS = 30.0

_BINARY_SNIFF = 8192

_VOCABULARY = 50_000

_PROGRESS_EVERY = 200

_SYSTEM_FOLDERS = ("proc", "sys", "dev")

_FORBIDDEN_PARTS = frozenset({".aws", ".ssh", ".gnupg", ".docker", ".kube"})

_WORDY_KINDS = frozenset({"notebook", "python", "code", "sql", "docs", "config"})


class _Walked:
    """
    One file a search's walk reached: its real path, its path as the search shows it, and its os.stat result.
    """

    __slots__ = ("real", "shown", "info")

    def __init__(self, real: str, shown: str, info: os.stat_result):
        self.real = real
        self.shown = shown
        self.info = info


class _SearchState:
    """
    What a search has found and read so far: its hits and near misses, the words of the files read (for close
    spellings), and its limits (_READ_BYTES, _MOST_FILES_READ, the deadline).
    """

    def __init__(self, search: FileSearch, query: FileQuery, deadline: float):
        self.search = search
        self.query = query
        self.deadline = deadline
        self.found: list[FileHit] = []
        self.vocabulary: set[str] = set()

    def stop_reason(self) -> str:
        """
        Why the search should stop now, '' while it can go on.
        """
        if self.search.bytes_read >= _READ_BYTES:
            return f"stopped after reading {_READ_BYTES // MB} MB: narrow it with folder= or files="
        if self.search.files_read >= _MOST_FILES_READ:
            return f"stopped after reading {_MOST_FILES_READ:,} files: narrow it with folder= or files="
        if time.monotonic() > self.deadline:
            return "stopped at its time limit: narrow it with folder= or files="
        return ""

    def skip(self, reason: str) -> None:
        self.search.skipped[reason] = self.search.skipped.get(reason, 0) + 1

    def learn_words(self, text: str) -> None:
        if len(self.vocabulary) < _VOCABULARY:
            self.vocabulary |= words_of(text)


def _found_nowhere(wanted: list[str], found: list[FileHit]) -> list[str]:
    """
    The words of a search that no file read holds, for close spellings.
    """
    nowhere = []
    for word in wanted:
        if all(word in hit.missing for hit in found):
            nowhere.append(word)
    return nowhere


def _modified(info: os.stat_result) -> datetime:
    return datetime.fromtimestamp(info.st_mtime, timezone.utc)


class _FileReads:
    """
    SageMakerAnalyzer's search across the notebook's own files and its read of one file, on the machine `root`
    points at (_MachineReads' home(), _real() and _shown()). search_seconds is how long a search may take.
    """

    search_seconds = _SEARCH_SECONDS

    def _resolve(self, path: str | Path | None) -> Path:
        """
        A path the user or the agent gives -> the real path to read: '' is home(), '~/x' and absolute paths are as
        the machine sees them, and anything else is under home().
        """
        text = str(path or "").strip()
        if not text:
            return self.home()
        if text == "~" or text.startswith(("~/", "/")):
            return self._real(text)
        return self.home() / text

    def _display(self, path: str | Path) -> str:
        """
        A path as search_files() and read_file() show it: from home() when it's under it ('churn/train.ipynb'),
        else as the machine sees it.
        """
        home = str(self.home())
        text = str(path)
        if text.startswith(home + os.sep):
            return text[len(home) + 1:].replace(os.sep, "/")
        return self._shown(path)

    def _forbidden(self, path: Path) -> str:
        """
        Why a path is never read, '' when it may be: /proc, /sys and /dev (a process's environment holds its AWS
        credentials), credentials folders (.aws, .ssh, ...) and secrets files (.env, keys). Links are followed
        first, so one can't point past this.
        """
        real = path.resolve()
        shown = self._shown(real)
        top = shown.strip("/").split("/", 1)[0]
        if top in _SYSTEM_FOLDERS:
            return f"/{top} is the system's view of its processes and devices, not your files"
        if _FORBIDDEN_PARTS & set(real.parts):
            return "credentials folders (.aws, .ssh, .gnupg, .docker, .kube) are never read"
        if real.is_file() and is_secret_file(real.name):
            return f"{real.name} looks like a secrets file, which is never read"
        return ""

    def _search_folder(self, folder: str | Path | None) -> Path:
        """
        The folder a search walks, or a ValueError saying why it can't be searched.
        """
        root = self._resolve(folder)
        if not root.exists():
            raise ValueError(f"There's no folder {self._shown(root)!r} here. search_files() with no folder searches "
                             f"{self._shown(self.home())}.")
        reason = self._forbidden(root)
        if reason:
            raise ValueError(f"{self._shown(root)!r} isn't searched: {reason}.")
        return root

    def _walk(self, root: Path, state: _SearchState) -> Iterator[_Walked]:
        """
        The files under a folder, each folder's in name order, without following links or crossing onto another
        disk, and without going into hidden folders, credentials folders, caches or Python environments.
        """
        if root.is_file():
            yield _Walked(str(root), self._display(root), root.stat())
            return
        device = root.stat().st_dev
        stack = [str(root)]
        while stack:
            folder = stack.pop()
            try:
                with os.scandir(folder) as scan:
                    entries = sorted(scan, key=lambda entry: entry.name)
            except OSError:
                state.skip("folders it couldn't open")
                continue
            if folder != str(root) and is_environment(entry.name for entry in entries):
                state.skip("Python environments")
                continue
            subfolders = []
            for entry in entries:
                walked = self._walked(entry, device, state)
                if isinstance(walked, _Walked):
                    yield walked
                elif walked:
                    subfolders.append(walked)
            stack.extend(reversed(subfolders))

    def _walked(self, entry: os.DirEntry[str], device: int, state: _SearchState) -> _Walked | str | None:
        """
        One entry of a folder the walk reads: a file to read, a folder's path to go into, or None to leave it.
        """
        try:
            if entry.is_symlink():
                return None
            info = entry.stat(follow_symlinks=False)
        except OSError:
            state.skip("files it couldn't open")
            return None
        if stat.S_ISDIR(info.st_mode):
            if info.st_dev != device:
                state.search.other_disks.append(self._shown(entry.path))
                return None
            return None if is_skipped_folder(entry.name) else entry.path
        if not stat.S_ISREG(info.st_mode):
            return None
        return _Walked(entry.path, self._display(entry.path), info)

    @staticmethod
    def _read_text(path: str, size: int) -> tuple[str | None, bool]:
        """
        A file's text and whether only its first _FILE_BYTES were read; None for a binary file (a NUL byte in its
        start).
        """
        with open(path, "rb") as handle:
            raw = handle.read(_FILE_BYTES)
        if b"\x00" in raw[:_BINARY_SNIFF]:
            return None, False
        return raw.decode("utf-8", errors="replace"), size > _FILE_BYTES

    def _rows(self, walked: _Walked, state: _SearchState) -> tuple[list[tuple[str, str]], str] | None:
        """
        A file's (cell, line) rows and a note on what wasn't read; None when its text can't be read, in which case
        only its name is matched.
        """
        try:
            text, partial = self._read_text(walked.real, walked.info.st_size)
        except OSError:
            state.skip("files it couldn't open")
            return None
        if text is None:
            state.skip("binary files (matched by name only)")
            return None
        state.search.files_read += 1
        state.search.bytes_read += min(walked.info.st_size, _FILE_BYTES)
        kind = file_kind(walked.shown)
        rows = notebook_rows(text) if kind == "notebook" else None
        if rows is None:
            rows = text_rows(text)
        if kind in _WORDY_KINDS:
            state.learn_words(text[:200_000])
        return rows, (f"only its first {_FILE_BYTES // MB} MB read" if partial else "")

    def _match(self, walked: _Walked, state: _SearchState) -> None:
        """
        Reads one file and keeps it when it holds any word of the search.
        """
        if is_secret_file(os.path.basename(walked.real)):
            state.skip("secrets files")
            return
        read = self._rows(walked, state)
        rows, note = read if read is not None else ([], "a binary file: matched by its name")
        hit = match_file(walked.shown, rows, state.query)
        if hit is None:
            return
        hit.size, hit.modified, hit.note = walked.info.st_size, _modified(walked.info), note
        hit.score = hit_score(hit, len(state.query.wanted))
        state.found.append(hit)

    def search_files(
        self,
        text: str,
        *,
        folder: str | Path | None = None,
        files: str = "",
        regex: bool = False,
        progress: Callable[[int], None] | None = None,
    ) -> FileSearch:
        """
        The files under a folder of this notebook (default: home(), where your notebooks and data live) that hold
        every word of a search, like a smart grep: words and "quoted phrases" in a file's text or path, -words left
        out, case ignored unless a word has a capital (regex=True reads the text as one regular expression);
        notebooks are read cell by cell with their printed outputs and errors. files= narrows it: kinds
        ('notebooks', 'python', 'code', 'data', 'config', 'docs', 'logs'), globs ('*.csv') or folders, separated by
        commas. The best files come first: a name holding the words, a line holding them all, code and
        notebooks over data. When no file holds every word, `near` has the files that hold most and `suggestions`
        close spellings. Hidden folders, caches, Python environments, links, other disks and secrets files are
        never read; it keeps the best MOST_HITS, and stops after 200 MB, 20,000 files or search_seconds, saying so
        in cut_short. progress is called
        with the running count of files read.
        """
        query = parse_file_query(text, regex=regex)
        unknown = unknown_files_parts(files)
        if unknown:
            raise ValueError(f"files={files!r}: {', '.join(repr(part) for part in unknown)} isn't a kind, a glob or a "
                             "folder. Kinds: notebooks, python, code, data, config, docs, logs; globs: '*.csv'.")
        started = time.monotonic()
        root = self._search_folder(folder)
        search = FileSearch(text, self._shown(root), regex=regex, files=files)
        state = _SearchState(search, query, started + self.search_seconds)
        for walked in self._walk(root, state):
            search.cut_short = state.stop_reason()
            if search.cut_short:
                break
            search.files_seen += 1
            if wants_file(walked.shown, files):
                self._match(walked, state)
            if progress and search.files_read and search.files_read % _PROGRESS_EVERY == 0:
                progress(search.files_read)
        self._sort_found(state)
        search.seconds = time.monotonic() - started
        return search

    @staticmethod
    def _sort_found(state: _SearchState) -> None:
        """
        Puts the files that hold every word in hits, the best first; and when there are none, the files holding
        most of them in near and close spellings of the words found nowhere in suggestions.
        """
        search = state.search
        complete = [hit for hit in state.found if not hit.missing]
        search.hits = rank_hits(complete)[:MOST_HITS]
        if complete:
            return
        search.near = near_misses(state.found)
        nowhere = _found_nowhere(state.query.wanted, state.found)
        if state.query.pattern is None:
            search.suggestions = close_words(nowhere, state.vocabulary, case_sensitive=state.query.case_sensitive)

    def read_file(self, path: str | Path) -> FileText:
        """
        One file of this notebook as lines of text: a path from search_files(), one under home() ('churn/train.py')
        or as the machine sees it ('~/x', '/opt/ml/...'). A notebook is shown cell by cell, each under a
        '# %% cell 3 [code]' line, with its printed outputs and errors marked '# >> ', the same lines and numbers
        search_files() gives. Only the first 20 MB of a file is read; a folder, a binary or a secrets file gives a
        note instead, and nothing under /proc or a credentials folder is read.
        """
        real = self._resolve(path)
        out = FileText(self._display(real))
        if not real.exists():
            out.note = f"There's no file {out.path!r}: search_files() gives the paths of the files it found."
            return out
        out.note = self._forbidden(real)
        if out.note:
            return out
        if real.is_dir():
            names = sorted(entry.name for entry in real.iterdir() if not entry.name.startswith("."))
            out.note = f"{out.path!r} is a folder holding {len(names):,} entries: {', '.join(names[:20])}"
            return out
        info = real.stat()
        out.kind, out.size, out.modified = file_kind(out.path), info.st_size, _modified(info)
        text, out.cut_short = self._read_text(str(real), info.st_size)
        if text is None:
            out.note = f"{out.path!r} is a binary file ({out.kind}), not text."
            return out
        rows = notebook_rows(text) if out.kind == "notebook" else None
        out.lines = [line for _, line in (rows if rows is not None else text_rows(text))]
        return out
