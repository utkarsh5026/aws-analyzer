"""
deepagents' file tools on the agent's Python workspace: workspace_backend() is a deepagents backend whose files are the
workspace's, so ls, read_file, write_file, edit_file, glob and grep see what the save_* tools saved and what run_python
wrote, and the model can write a script, run it (run_python(file=...)) and fix it with edit_file. A path there is the
workspace's from its root: '/analysis.py' is 'analysis.py' to run_python.

What a read or a grep hands the model has anything that looks like a secret hidden, as run_python's output does (an
edit works on the file as it is). A write that would take the workspace past the budget's workspace_bytes is refused.
deepagents' summaries of a long conversation (_STATE_ROUTES) stay in its own in-memory files, out of the workspace.

_WorkspaceBackend holds the backend's methods; workspace_backend() makes it a deepagents BackendProtocol once deepagents
is loaded (it's loaded only when the agent first runs). The other helpers here are pure.
"""

from __future__ import annotations

from typing import Any, Callable

from .._kit.deps import _require
from .._kit.fmt import human_size
from .budget import Budget
from .results import hide_secrets
from .workspace import Workspace, clean_path

_STATE_ROUTES = ("/conversation_history/",)
_ROOT = "/"


def workspace_path(path: str) -> str:
    """
    A file tool's path as the workspace's: '/lambda/etl/app.py' -> 'lambda/etl/app.py'. ValueError for one outside it.
    """
    return clean_path(path)


def tool_path(path: str) -> str:
    """
    A workspace path as the file tools show it: 'lambda/etl/app.py' -> '/lambda/etl/app.py'.
    """
    return _ROOT + path


def _folder_prefix(path: str | None) -> str:
    """
    A folder a tool names, as the start of the workspace paths in it: '/lambda/' -> 'lambda/', '/' or None -> ''.
    """
    text = (path or "").strip()
    if text.strip("/.") == "":
        return ""
    return workspace_path(text) + "/"


def listing(paths: list[tuple[str, int]], folder: str | None) -> list[dict[str, Any]]:
    """
    What ls shows of a folder: its files (path, size) and the folders in it (path ending in '/'), sorted, from the
    workspace's (path, size) pairs.
    """
    prefix = _folder_prefix(folder)
    files: list[dict[str, Any]] = []
    folders: set[str] = set()
    for path, size in paths:
        if not path.startswith(prefix):
            continue
        rest = path[len(prefix):]
        if "/" in rest:
            folders.add(tool_path(prefix + rest.split("/")[0]) + "/")
        else:
            files.append({"path": tool_path(path), "is_dir": False, "size": size})
    entries = files + [{"path": folder_path, "is_dir": True, "size": 0} for folder_path in folders]
    return sorted(entries, key=lambda entry: entry["path"])


def _decoded_text(data: bytes) -> str | None:
    """
    A file's bytes as text, or None for a binary file.
    """
    if b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


class _WorkspaceBackend:
    """
    deepagents' file tools on the agent's Python workspace: the methods of a deepagents BackendProtocol, written
    against `protocol` and `utils` (deepagents.backends.protocol and .utils), which workspace_backend() passes in once
    deepagents is loaded. budget() is the agent's Budget, read at each write.
    """

    def __init__(self, workspace: Workspace, budget: Callable[[], Budget], protocol: Any, utils: Any):
        self.workspace = workspace
        self.budget = budget
        self.protocol = protocol
        self.utils = utils

    def _text_of(self, path: str) -> str | None:
        return _decoded_text(self.workspace.read(workspace_path(path)))

    def _room_for(self, path: str, size: int) -> str | None:
        """
        Why writing `size` bytes at `path` would take the workspace past its budget, or None when it fits.
        """
        limit = self.budget().workspace_bytes
        kept = sum(f.size for f in self.workspace.files() if f.path != path)
        if kept + size <= limit:
            return None
        return (f"Not written: {human_size(size)} would take the workspace past its {human_size(limit)} limit "
                f"({human_size(kept)} is there already)")

    def _store(self, path: str, data: bytes) -> str | None:
        """
        Writes `data` at the file tools' `path`; the reason it didn't, or None.
        """
        try:
            target = workspace_path(path)
        except ValueError as exc:
            return str(exc)
        refused = self._room_for(target, len(data))
        if refused is None:
            self.workspace.put(target, data)
        return refused

    def _visible_files(self, folder: str | None) -> dict[str, Any]:
        """
        The text files under `folder`, as deepagents' FileData by the file tools' paths, secrets hidden.
        """
        prefix = _folder_prefix(folder)
        found: dict[str, Any] = {}
        for entry in self.workspace.files():
            if not entry.path.startswith(prefix):
                continue
            text = _decoded_text(self.workspace.read(entry.path))
            if text is not None:
                found[tool_path(entry.path)] = self.utils.create_file_data(hide_secrets(text))
        return found

    def ls(self, path: str) -> Any:
        try:
            entries = listing([(f.path, f.size) for f in self.workspace.files()], path)
        except ValueError as exc:
            return self.protocol.LsResult(error=str(exc))
        return self.protocol.LsResult(entries=entries)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> Any:
        try:
            text = self._text_of(file_path)
        except (ValueError, FileNotFoundError):
            return self.protocol.ReadResult(error=f"File '{file_path}' not found")
        if text is None:
            return self.protocol.ReadResult(error=f"'{file_path}' is a binary file: read it with run_python")
        return self.utils.slice_read_response(self.utils.create_file_data(hide_secrets(text)), offset, limit)

    def write(self, file_path: str, content: str) -> Any:
        refused = self._store(file_path, content.encode())
        return self.protocol.WriteResult(error=refused) if refused else self.protocol.WriteResult(path=file_path)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> Any:
        try:
            text = self._text_of(file_path)
        except (ValueError, FileNotFoundError):
            return self.protocol.EditResult(error=f"Error: File '{file_path}' not found")
        if text is None:
            return self.protocol.EditResult(error=f"Error: '{file_path}' is a binary file")
        replaced = self.utils.perform_string_replacement(text, old_string, new_string, replace_all)
        if isinstance(replaced, str):
            return self.protocol.EditResult(error=replaced)
        content, occurrences = replaced
        refused = self._store(file_path, content.encode())
        if refused:
            return self.protocol.EditResult(error=refused)
        return self.protocol.EditResult(path=file_path, occurrences=int(occurrences))

    def glob(self, pattern: str, path: str | None = None) -> Any:
        try:
            prefix = _folder_prefix(path)
            matches = self.utils.compile_grep_include_glob(pattern)
        except (ValueError, self.utils.InvalidGlobPatternError) as exc:
            return self.protocol.GlobResult(error=str(exc))
        under = [f for f in self.workspace.files() if f.path.startswith(prefix)]
        found = [{"path": tool_path(f.path), "is_dir": False, "size": f.size} for f in under
                 if matches(f.path[len(prefix):])]
        return self.protocol.GlobResult(matches=found)

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None, *,
             max_count: int | None = None) -> Any:
        try:
            files = self._visible_files(path)
        except ValueError as exc:
            return self.protocol.GrepResult(error=str(exc))
        return self.utils.grep_matches_from_files(files, pattern, _ROOT, glob, max_count=max_count)

    def delete(self, file_path: str) -> Any:
        try:
            self.workspace.delete(workspace_path(file_path))
        except (ValueError, FileNotFoundError):
            return self.protocol.DeleteResult(error=f"File '{file_path}' not found")
        return self.protocol.DeleteResult(path=file_path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[Any]:
        return [self.protocol.FileUploadResponse(path=path, error=self._store(path, data)) for path, data in files]

    def download_files(self, paths: list[str]) -> list[Any]:
        return [self._download(path) for path in paths]

    def _download(self, path: str) -> Any:
        try:
            data = self.workspace.read(workspace_path(path))
        except (ValueError, FileNotFoundError):
            return self.protocol.FileDownloadResponse(path=path, error="file_not_found")
        return self.protocol.FileDownloadResponse(path=path, content=data)


def workspace_backend(workspace: Workspace, budget: Callable[[], Budget]) -> Any:
    """
    A deepagents backend (a CompositeBackend) whose files are `workspace`'s, with deepagents' conversation summaries
    kept apart in its in-memory StateBackend. budget() is the agent's Budget, read at each write.
    """
    backends = _require("deepagents.backends", "The agent's file tools", package="aws-analyzer[agent]")
    protocol = _require("deepagents.backends.protocol", "The agent's file tools", package="aws-analyzer[agent]")
    utils = _require("deepagents.backends.utils", "The agent's file tools", package="aws-analyzer[agent]")
    backend_class = type("WorkspaceBackend", (_WorkspaceBackend, protocol.BackendProtocol), {})
    files = backend_class(workspace, budget, protocol, utils)
    return backends.CompositeBackend(default=files, routes=dict.fromkeys(_STATE_ROUTES, backends.StateBackend()))
