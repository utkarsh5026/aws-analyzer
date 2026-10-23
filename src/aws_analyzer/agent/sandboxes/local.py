"""
LocalSandbox: the agent's built-in Python workspace. The model's code runs in Pyodide (CPython compiled to
WebAssembly) on Deno, a JavaScript runtime that denies everything it isn't granted: it gets read access to the Pyodide
folder and nothing else, so the code has no network (not even the instance metadata service that hands out the
notebook role's credentials), no environment variables, no files of the machine and no subprocesses. Its files live in
memory here and are copied into Pyodide's in-memory file system for each run (runner.mjs), which starts a fresh Deno.

What it needs: Deno (pip install deno, or the [sandbox] extra), and the Pyodide files, which prepare() downloads once
from the jsDelivr CDN into `cache` (PYODIDE_URL; about 13 MB, plus the packages: pandas and what it needs, about 8 MB),
checking each file's SHA-256: the core files' against _CORE_FILES, pinned here, and the packages' against the lock
file's. Without the internet (a VPC-only notebook), put those files in `cache` from elsewhere, or point index_url= at a
mirror. Nothing here deletes a file, and nothing is written outside `cache`.

_RESULT_LIMIT is the most a run's output (stdout and stderr each) the runner keeps, in characters; _DOWNLOAD_SECONDS
how long one download may stall.
"""

from __future__ import annotations

import base64
import binascii
import difflib
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from ..._kit.deps import _require
from ..._kit.fmt import human_size, parse_size
from ..workspace import PythonRun, Workspace, WorkspaceFile, clean_path

PYODIDE_VERSION = "314.0.7"
PYODIDE_URL = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/"
DEFAULT_PACKAGES = ("pandas",)

_LOCK_FILE = "pyodide-lock.json"
_CORE_FILES = {
    _LOCK_FILE: "5dc2fc119108bc148c7457dc86e7675b5c87e1cafd420b9c34c1eaef7b36c010",
    "pyodide.mjs": "6f1d60f7bf529beb300f0f47983c921d3982363640ba20af0e38efdddbc66109",
    "pyodide.asm.mjs": "f7cdc8ece80678ceb712f8e65ebe6d3a83203a180c399865f49612a051693635",
    "pyodide.asm.wasm": "cc36e3cab04fdfc9a63ff13eb52eae2b911bf46c025cc7b281f394bd3de1d5e6",
    "python_stdlib.zip": "fa1957e5777068fc4f7437f96d860ae2fbe9c19732ba06c84e004ec16dd7dd7a",
}
_RUNNER = Path(__file__).with_name("runner.mjs")
_WASM_PAGE_BYTES = 64 * 1024
_RESULT_LIMIT = 2_000_000
_DOWNLOAD_SECONDS = 60
_DENO_OUTPUT_SHOWN = 2000


@dataclass(frozen=True)
class PyodideFile:
    """
    One file the sandbox needs from Pyodide's release: its name and the SHA-256 it must have.
    """

    name: str
    sha256: str


def default_cache() -> Path:
    """
    Where the Pyodide files are kept: $XDG_CACHE_HOME (else ~/.cache) / aws-analyzer / pyodide / <version>.
    """
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "aws-analyzer" / "pyodide" / PYODIDE_VERSION


def needed_packages(lock: dict[str, Any], wanted: Iterable[str]) -> list[str]:
    """
    The packages of Pyodide's lock file (`lock`, its 'packages') that `wanted` needs, each once: the ones asked for,
    then what they depend on. ValueError for a name the lock doesn't have, with the closest ones.
    """
    needed: list[str] = []
    queue = [str(name).strip().lower() for name in wanted]
    while queue:
        name = queue.pop(0)
        if name in needed:
            continue
        if name not in lock:
            close = difflib.get_close_matches(name, lock, n=3)
            hint = f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
            raise ValueError(f"Pyodide {PYODIDE_VERSION} has no package {name!r}.{hint} Its packages are listed in "
                             f"{PYODIDE_URL}{_LOCK_FILE}; the standard library is always there")
        needed.append(name)
        queue.extend(lock[name].get("depends", []))
    return needed


def package_files(lock: dict[str, Any], names: Iterable[str]) -> list[PyodideFile]:
    """
    The files of these lock-file packages, with the SHA-256 the lock file gives each.
    """
    return [PyodideFile(lock[name]["file_name"], lock[name]["sha256"]) for name in names]


def parse_result(output: str, nonce: str) -> dict[str, Any] | None:
    """
    The runner's result from what Deno printed: the last line that starts with this run's nonce, as JSON. None when
    there's none (Deno failed, or the code ended it early). The code can print lines of its own (through js.console),
    so only the last is the runner's.
    """
    lines = [line for line in output.splitlines() if line.startswith(nonce)]
    if not lines:
        return None
    try:
        result = json.loads(lines[-1][len(nonce):])
    except json.JSONDecodeError:
        return None
    return result if isinstance(result, dict) else None


def _sha256(path: Path) -> str:
    """
    A file's SHA-256, as hex.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _deno_binary(given: str | None) -> str:
    """
    The Deno to run: `given`, else the one on PATH, else the one pip install deno brings (a notebook's kernel may not
    have its environment's bin folder on PATH). ImportError saying how to install it when there's none.
    """
    if given:
        return given
    found = shutil.which("deno")
    if found:
        return found
    deno = _require("deno", "The local sandbox (it runs Python on Deno)", package="deno")
    return str(deno.find_deno_bin())


class LocalSandbox(Workspace):
    """
    A Python workspace on this machine: the code runs in Pyodide (Python 3.14 compiled to WebAssembly) on Deno, with
    no network, no environment variables, no access to the machine's files and no subprocesses, in a fresh
    interpreter each run. Its files are kept in memory.

        packages   Pyodide packages the code can import besides the standard library ('pandas' brings numpy too)
        cache      where the Pyodide files are kept (default_cache())
        index_url  where prepare() downloads them from: jsDelivr's copy of Pyodide's release, or your mirror
        memory     the most memory one run may use ('2GB')
        deno       the Deno executable (default: the one on PATH, or pip install deno's)

    prepare() downloads what's missing (the first run does it too) and says what it has.
    """

    title = "a local sandbox"
    place = "(Pyodide on Deno, in WebAssembly: no network, and none of this machine's files)"

    def __init__(self, *, packages: Iterable[str] = DEFAULT_PACKAGES, cache: str | os.PathLike | None = None,
                 index_url: str = PYODIDE_URL, memory: str | int = "2GB", deno: str | None = None):
        self.packages = tuple(packages)
        self.cache = Path(cache) if cache is not None else default_cache()
        self.index_url = index_url if index_url.endswith("/") else index_url + "/"
        if not self.index_url.startswith("https://"):
            raise ValueError(f"index_url= must be an https:// URL, not {index_url!r}")
        self.memory = parse_size(memory) or 0
        if self.memory < _WASM_PAGE_BYTES:
            raise ValueError(f"memory= is too small: {memory!r}")
        self.deno = deno
        self._files: dict[str, bytes] = {}
        self._offered: list[str] | None = None
        self._lock = threading.Lock()
        self._preparing = threading.Lock()

    def put(self, path: str, data: bytes) -> None:
        with self._lock:
            self._files[clean_path(path)] = bytes(data)

    def files(self) -> list[WorkspaceFile]:
        with self._lock:
            return [WorkspaceFile(path, len(data)) for path, data in sorted(self._files.items())]

    def prepare(self, progress: Callable[[str], None] | None = None) -> str:
        """
        Makes sure Deno is there and the Pyodide files are in `cache`, downloading what's missing (each checked
        against its SHA-256), and returns what's ready: "Pyodide 314.0.7 with pandas, numpy, ... in ~/.cache/...".
        progress(name) is told each file it downloads. Raises with what's missing and how to get it.
        """
        _deno_binary(self.deno)
        with self._preparing:
            self._download((PyodideFile(name, sha) for name, sha in _CORE_FILES.items()), progress)
            lock = json.loads((self.cache / _LOCK_FILE).read_text())["packages"]
            offered = needed_packages(lock, self.packages)
            self._download(package_files(lock, offered), progress)
            self._offered = offered
        return f"Pyodide {PYODIDE_VERSION} with {', '.join(offered) or 'the standard library only'}, in {self.cache}"

    def run(self, code: str, *, timeout: float) -> PythonRun:
        """
        Runs `code` in a fresh Pyodide on Deno with the workspace's files in its folder, for at most `timeout`
        seconds (starting Pyodide and loading the packages the code imports count too), and keeps the files it
        wrote, changed or deleted.
        """
        if self._offered is None:
            self.prepare()
        with self._lock:
            files = dict(self._files)
        nonce = secrets.token_hex(16)
        request = {"nonce": nonce, "code": code, "packages": self._offered, "limit": _RESULT_LIMIT,
                   "files": {path: base64.b64encode(data).decode() for path, data in files.items()}}
        started = time.monotonic()
        try:
            done = subprocess.run(self._command(), input=json.dumps(request), capture_output=True, text=True,
                                  timeout=timeout, env=self._environment(), check=False)
        except subprocess.TimeoutExpired:
            return PythonRun(False, stderr=f"Stopped at the {timeout:g}-second time limit.",
                             seconds=time.monotonic() - started, timed_out=True)
        seconds = time.monotonic() - started
        result = parse_result(done.stdout, nonce)
        if result is None:
            return PythonRun(False, stderr=self._deno_failed(done), seconds=seconds)
        return PythonRun(bool(result.get("ok")), str(result.get("stdout", "")), str(result.get("stderr", "")),
                         seconds, new_files=self._keep(files, result))

    def _command(self) -> list[str]:
        """
        Deno's command line: read access to the Pyodide folder and nothing else (no network, environment, writes,
        subprocesses or native code), never asking, no remote modules or config files, and WebAssembly's memory
        capped at `memory`.
        """
        pages = max(1, self.memory // _WASM_PAGE_BYTES)
        return [_deno_binary(self.deno), "run", "--quiet", "--no-prompt", "--no-remote", "--no-config", "--no-lock",
                f"--allow-read={self.cache}", f"--v8-flags=--wasm-max-mem-pages={pages}", str(_RUNNER),
                str(self.cache)]

    def _environment(self) -> dict[str, str]:
        """
        What Deno runs with: no AWS settings or credentials (Deno can't read them anyway: it has no --allow-env),
        only what it needs to start, and its own cache beside Pyodide's files.
        """
        keep = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "TEMP", "TMP") if name in os.environ}
        return {**keep, "NO_COLOR": "1", "DENO_NO_UPDATE_CHECK": "1", "DENO_DIR": str(self.cache / "deno")}

    def _keep(self, sent: dict[str, bytes], result: dict[str, Any]) -> list[str]:
        """
        Applies a run's changes to the workspace's files (unless the workspace changed under it, for those files) and
        returns the paths it wrote or changed.
        """
        changed: dict[str, bytes] = {}
        for path, data in (result.get("changed") or {}).items():
            try:
                changed[clean_path(path)] = base64.b64decode(data, validate=True)
            except (ValueError, binascii.Error):
                continue
        deleted = [path for path in result.get("deleted") or [] if path in sent]
        with self._lock:
            self._files.update(changed)
            for path in deleted:
                if self._files.get(path) is sent[path]:
                    del self._files[path]
        return sorted(changed)

    def _deno_failed(self, done: subprocess.CompletedProcess) -> str:
        """
        What to say when Deno gave no result: its own error, or that the code ended it before it finished.
        """
        said = (done.stderr or done.stdout or "").strip()
        if not said:
            return "The code ended the sandbox before it finished (it gave no result)."
        return f"The sandbox failed (Deno exited with {done.returncode}): {said[-_DENO_OUTPUT_SHOWN:]}"

    def _download(self, files: Iterable[PyodideFile], progress: Callable[[str], None] | None = None) -> None:
        """
        Downloads each file that isn't in `cache` with the right SHA-256 yet, to <name>.part, then renames it.
        """
        for file in files:
            target = self.cache / file.name
            if target.exists() and _sha256(target) == file.sha256:
                continue
            if progress is not None:
                progress(file.name)
            self.cache.mkdir(parents=True, exist_ok=True)
            data = self._fetch(file)
            part = target.with_name(target.name + ".part")
            part.write_bytes(data)
            os.replace(part, target)

    def _fetch(self, file: PyodideFile) -> bytes:
        """
        One file from index_url, checked against its SHA-256. Raises a ConnectionError that says what to do without
        the internet when it can't be downloaded, and a ValueError when it isn't the file it should be.
        """
        url = self.index_url + file.name
        try:
            with urllib.request.urlopen(url, timeout=_DOWNLOAD_SECONDS) as response:
                data = response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ConnectionError(
                f"Couldn't download {url} ({exc}). The local sandbox needs Pyodide {PYODIDE_VERSION}'s files once: "
                f"without the internet, copy {file.name} and the other files it needs from {PYODIDE_URL} into "
                f"{self.cache}, or pass index_url= a mirror of them") from exc
        if hashlib.sha256(data).hexdigest() != file.sha256:
            raise ValueError(f"{url} ({human_size(len(data))}) isn't the file Pyodide {PYODIDE_VERSION} released "
                             "(its SHA-256 differs), so it wasn't used")
        return data
