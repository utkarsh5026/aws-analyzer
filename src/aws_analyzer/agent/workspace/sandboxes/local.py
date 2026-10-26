"""
LocalSandbox: the agent's built-in Python workspace. The model's code runs in Pyodide (CPython compiled to
WebAssembly) on Deno, a JavaScript runtime that denies everything it isn't granted: it gets read access to the Pyodide
folder and nothing else, so the code has no network (not even the instance metadata service that hands out the
notebook role's credentials), no environment variables, no files of the machine and no subprocesses. Its files live in
memory here and are copied into Pyodide's in-memory file system for each run (runner.mjs), which starts a fresh Deno.

What it needs: Deno (pip install deno, or the [sandbox] extra), and the Pyodide files, which prepare() downloads once
from the jsDelivr CDN into `cache` (PYODIDE_URL; about 13 MB, plus the packages: pandas, Altair and what they need,
about 10 MB, and Vega and Vega-Lite for drawing charts from VEGA_URL, under 1 MB),
checking each file's SHA-256: the core files' against _CORE_FILES and Vega's against _VEGA_FILES, pinned here, and
the packages' against the lock file's. Without the internet (a VPC-only notebook), put those files in `cache` from elsewhere, or point index_url= at a
mirror. Nothing here deletes a file, and nothing is written outside `cache`.

Charts: the code saves a chart with Altair (chart.save('charts/x.json'), a Vega-Lite spec), and draw_vega_lite()
draws it as SVG with Vega and Vega-Lite in a fresh Deno (charts.mjs), with the same permissions as a run: read access
to `cache`, nothing else. A sandbox given matplotlib (packages=[..., 'matplotlib']) draws with its Agg backend (Pyodide's own needs a browser page) in CHART_STYLE, a matplotlibrc
outside the workspace, so the model's charts look alike without it setting a style: one size, recessive axes and
grid, and the categorical colours of a palette checked for colour-blind readers, in a fixed order.

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
import re
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

from ...._kit.deps import _require
from ...._kit.fmt import human_size, parse_size
from .. import PythonRun, Workspace, WorkspaceFile, clean_path

PYODIDE_VERSION = "314.0.7"
PYODIDE_URL = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/"
DEFAULT_PACKAGES = ("pandas", "altair")
VEGA_URL = "https://cdn.jsdelivr.net/npm/"
VEGA_VERSION, VEGA_LITE_VERSION = "6.4.0", "6.4.3"
CHART_COLORS = ("2a78d6", "eb6834", "1baf7a", "eda100", "e87ba4", "008300", "6250d6", "e34948")
CHART_STYLE = f"""\
backend: Agg
figure.figsize: 8, 4.5
figure.dpi: 110
figure.facecolor: fcfcfb
savefig.dpi: 110
savefig.bbox: tight
savefig.facecolor: fcfcfb
font.size: 10
text.color: 0b0b0b
axes.facecolor: fcfcfb
axes.edgecolor: c3c2b7
axes.labelcolor: 52514e
axes.titlesize: 12.5
axes.titleweight: semibold
axes.titlelocation: left
axes.titlepad: 12
axes.spines.top: False
axes.spines.right: False
axes.grid: True
axes.grid.axis: y
axes.axisbelow: True
axes.prop_cycle: cycler('color', [{", ".join(f"'{color}'" for color in CHART_COLORS)}])
grid.color: e6e5e0
grid.linewidth: 0.8
xtick.color: 52514e
ytick.color: 52514e
xtick.major.size: 0
ytick.major.size: 0
lines.linewidth: 2
lines.markersize: 6
patch.force_edgecolor: True
patch.edgecolor: fcfcfb
patch.linewidth: 1
legend.frameon: False
image.cmap: Blues
date.converter: concise
"""

_LOCK_FILE = "pyodide-lock.json"
_CORE_FILES = {
    _LOCK_FILE: "5dc2fc119108bc148c7457dc86e7675b5c87e1cafd420b9c34c1eaef7b36c010",
    "pyodide.mjs": "6f1d60f7bf529beb300f0f47983c921d3982363640ba20af0e38efdddbc66109",
    "pyodide.asm.mjs": "f7cdc8ece80678ceb712f8e65ebe6d3a83203a180c399865f49612a051693635",
    "pyodide.asm.wasm": "cc36e3cab04fdfc9a63ff13eb52eae2b911bf46c025cc7b281f394bd3de1d5e6",
    "python_stdlib.zip": "fa1957e5777068fc4f7437f96d860ae2fbe9c19732ba06c84e004ec16dd7dd7a",
}
_RUNNER = Path(__file__).with_name("runner.mjs")
_CHART_RUNNER = Path(__file__).with_name("charts.mjs")
_WASM_PAGE_BYTES = 64 * 1024
_RESULT_LIMIT = 2_000_000
_DOWNLOAD_SECONDS = 60
_DENO_OUTPUT_SHOWN = 2000


@dataclass(frozen=True)
class PyodideFile:
    """
    One file the sandbox needs (from Pyodide's release, or Vega's): its name in the cache, the SHA-256 it must have,
    and where it is under the URL it's downloaded from, when that isn't its name.
    """

    name: str
    sha256: str
    source: str = ""

    @property
    def location(self) -> str:
        """
        Where the file is under the URL it's downloaded from: its `source`, else its name.
        """
        return self.source or self.name


_VEGA_FILES = (
    PyodideFile("vega.min.js", "8f6a3587cf8d4f42c7e08120e3eb05d067e746d554e39d2dcf52acc0bd5ba28f",
                f"vega@{VEGA_VERSION}/build/vega.min.js"),
    PyodideFile("vega-lite.min.js", "35a9821df838825b05a6a73e9414b58747a1b18321583858ed903c66393a5c7e",
                f"vega-lite@{VEGA_LITE_VERSION}/build/vega-lite.min.js"),
)


def default_cache() -> Path:
    """
    Where the Pyodide files are kept: $XDG_CACHE_HOME (else ~/.cache) / aws-analyzer / pyodide / <version>.
    """
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "aws-analyzer" / "pyodide" / PYODIDE_VERSION


def package_key(name: str) -> str:
    """
    A package's name as Pyodide's lock file files it: lower case, with runs of '-', '_' and '.' as one '-'
    ('jsonschema_specifications' -> 'jsonschema-specifications'), the way pip compares names.
    """
    return re.sub(r"[-_.]+", "-", str(name).strip()).lower()


def needed_packages(lock: dict[str, Any], wanted: Iterable[str]) -> list[str]:
    """
    The packages of Pyodide's lock file (`lock`, its 'packages') that `wanted` needs, each once: the ones asked for,
    then what they depend on, named as the lock file files them (package_key). ValueError for a name the lock
    doesn't have, with the closest ones.
    """
    keys = {package_key(name): name for name in lock}
    needed: list[str] = []
    queue = [package_key(name) for name in wanted]
    while queue:
        key = queue.pop(0)
        name = keys.get(key)
        if name in needed:
            continue
        if name is None:
            close = difflib.get_close_matches(key, lock, n=3)
            hint = f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
            raise ValueError(f"Pyodide {PYODIDE_VERSION} has no package {key!r}.{hint} Its packages are listed in "
                             f"{PYODIDE_URL}{_LOCK_FILE}; the standard library is always there")
        needed.append(name)
        queue.extend(package_key(dependency) for dependency in lock[name].get("depends", []))
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


def _https_folder(url: str, setting: str) -> str:
    """
    A download URL as the sandbox uses it, ending in '/'. ValueError for one that isn't https://.
    """
    if not url.startswith("https://"):
        raise ValueError(f"{setting}= must be an https:// URL, not {url!r}")
    return url if url.endswith("/") else url + "/"


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

        packages   Pyodide packages the code can import besides the standard library ('pandas' brings numpy too,
                   'altair' makes the charts show_chart draws; add 'matplotlib' to draw pictures instead)
        cache      where the Pyodide files are kept (default_cache())
        index_url  where prepare() downloads them from: jsDelivr's copy of Pyodide's release, or your mirror
        vega_url   where it downloads Vega and Vega-Lite from: jsDelivr's copy of npm, or your mirror
        memory     the most memory one run may use ('2GB')
        deno       the Deno executable (default: the one on PATH, or pip install deno's)

    prepare() downloads what's missing (the first run does it too) and says what it has.
    """

    title = "a local sandbox"
    place = "(Pyodide on Deno, in WebAssembly: no network, and none of this machine's files)"
    draws_charts = True

    def __init__(self, *, packages: Iterable[str] = DEFAULT_PACKAGES, cache: str | os.PathLike | None = None,  # noqa: PLR0913
                 index_url: str = PYODIDE_URL, vega_url: str = VEGA_URL, memory: str | int = "2GB",
                 deno: str | None = None):
        self.packages = tuple(packages)
        self.cache = Path(cache) if cache is not None else default_cache()
        self.index_url = _https_folder(index_url, "index_url")
        self.vega_url = _https_folder(vega_url, "vega_url")
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

    def read(self, path: str) -> bytes:
        with self._lock:
            data = self._files.get(clean_path(path))
        if data is None:
            raise FileNotFoundError(f"No file {path!r} in the workspace")
        return data

    def delete(self, path: str) -> None:
        with self._lock:
            if self._files.pop(clean_path(path), None) is None:
                raise FileNotFoundError(f"No file {path!r} in the workspace")

    def files(self) -> list[WorkspaceFile]:
        with self._lock:
            return [WorkspaceFile(path, len(data)) for path, data in sorted(self._files.items())]

    def prepare(self, progress: Callable[[str], None] | None = None) -> str:
        """
        Makes sure Deno is there and the Pyodide and Vega files are in `cache`, downloading what's missing (each
        checked against its SHA-256), and returns what's ready: "Pyodide 314.0.7 with pandas, numpy, ..., and Vega-Lite
        6.4.3 for charts, in ~/.cache/...".
        progress(name) is told each file it downloads. Raises with what's missing and how to get it.
        """
        _deno_binary(self.deno)
        with self._preparing:
            self._download((PyodideFile(name, sha) for name, sha in _CORE_FILES.items()), self.index_url, progress)
            lock = json.loads((self.cache / _LOCK_FILE).read_text())["packages"]
            offered = needed_packages(lock, self.packages)
            self._download(package_files(lock, offered), self.index_url, progress)
            self._download(_VEGA_FILES, self.vega_url, progress)
            self._offered = offered
        packages = ", ".join(offered) or "the standard library only"
        return f"Pyodide {PYODIDE_VERSION} with {packages}, and Vega-Lite {VEGA_LITE_VERSION} for charts, in {self.cache}"

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
                   "matplotlibrc": CHART_STYLE,
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

    def draw_vega_lite(self, spec: dict, *, config: dict, timeout: float) -> bytes:
        """
        Draws a Vega-Lite chart as SVG with Vega in a fresh Deno that can read only `cache` (so nothing it says can
        be fetched), `config` under the chart's own. ValueError with Vega's own message when the chart can't be drawn.
        """
        if self._offered is None:
            self.prepare()
        nonce = secrets.token_hex(16)
        request = {"nonce": nonce, "spec": spec, "config": config}
        try:
            done = subprocess.run(self._command(_CHART_RUNNER), input=json.dumps(request), capture_output=True,
                                  text=True, timeout=timeout, env=self._environment(), check=False)
        except subprocess.TimeoutExpired:
            raise ValueError(f"Drawing the chart took more than {timeout:g} seconds: show fewer points") from None
        result = parse_result(done.stdout, nonce)
        if result is None:
            raise ValueError(self._deno_failed(done))
        if not result.get("ok"):
            raise ValueError(f"Vega-Lite couldn't draw the chart: {result.get('error') or 'no reason given'}")
        return str(result.get("svg", "")).encode()

    def _command(self, script: Path = _RUNNER) -> list[str]:
        """
        Deno's command line for `script` (a run's, or the charts'): read access to the Pyodide folder and nothing else
        (no network, environment, writes, subprocesses or native code), never asking, no remote modules or config
        files, and WebAssembly's memory capped at `memory`.
        """
        pages = max(1, self.memory // _WASM_PAGE_BYTES)
        return [_deno_binary(self.deno), "run", "--quiet", "--no-prompt", "--no-remote", "--no-config", "--no-lock",
                f"--allow-read={self.cache}", f"--v8-flags=--wasm-max-mem-pages={pages}", str(script),
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

    def _download(self, files: Iterable[PyodideFile], base_url: str,
                  progress: Callable[[str], None] | None = None) -> None:
        """
        Downloads each file that isn't in `cache` with the right SHA-256 yet from `base_url`, to <name>.part, then
        renames it.
        """
        for file in files:
            target = self.cache / file.name
            if target.exists() and _sha256(target) == file.sha256:
                continue
            if progress is not None:
                progress(file.name)
            self.cache.mkdir(parents=True, exist_ok=True)
            data = self._fetch(file, base_url)
            part = target.with_name(target.name + ".part")
            part.write_bytes(data)
            os.replace(part, target)

    def _fetch(self, file: PyodideFile, base_url: str) -> bytes:
        """
        One file from `base_url`, checked against its SHA-256. Raises a ConnectionError that says what to do without
        the internet when it can't be downloaded, and a ValueError when it isn't the file it should be.
        """
        url = base_url + file.location
        try:
            with urllib.request.urlopen(url, timeout=_DOWNLOAD_SECONDS) as response:
                data = response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ConnectionError(
                f"Couldn't download {url} ({exc}). The local sandbox needs Pyodide {PYODIDE_VERSION}'s files and Vega's "
                f"once: without the internet, copy {file.name} and the other files it needs from {base_url} into "
                f"{self.cache}, or pass {self._mirror_setting(base_url)}= a mirror of them") from exc
        if hashlib.sha256(data).hexdigest() != file.sha256:
            raise ValueError(f"{url} ({human_size(len(data))}) isn't the file that was released (its SHA-256 differs), "
                             "so it wasn't used")
        return data

    def _mirror_setting(self, base_url: str) -> str:
        """
        The setting that points the sandbox at a mirror of `base_url`'s files: vega_url or index_url.
        """
        return "vega_url" if base_url == self.vega_url else "index_url"
