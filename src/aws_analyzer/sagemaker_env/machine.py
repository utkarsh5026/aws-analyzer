"""
SageMakerAnalyzer's reads of the machine it runs on: the metadata file, /proc, the disks, the GPUs and
what fills a folder.
"""

from __future__ import annotations

import heapq
import importlib.metadata
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .._kit.fmt import _utcnow, human_size

from .models import Clearable, DiskEntry, DiskReport, Environment, Machine, ProcessInfo, Volume
from .analysis.machine import parse_gpus, parse_loadavg, parse_meminfo, parse_metadata, parse_process
from .analysis.disk import _CLEARABLE, clear_command, clearable_kind


METADATA_FILE = "opt/ml/metadata/resource-metadata.json"  # under the analyzer's root, "/" on SageMaker

_HOMES = (
    "home/ec2-user/SageMaker",
    "home/sagemaker-user",
)  # a notebook instance's volume, a Studio space's home

_PACKAGES = (
    "boto3",
    "sagemaker",
    "pandas",
    "numpy",
    "pyarrow",
    "scikit-learn",
    "xgboost",
    "torch",
    "tensorflow",
    "transformers",
    "jupyterlab",
    "ipykernel",
)


class _MachineReads:
    """
    SageMakerAnalyzer's reads of the machine it runs on: SageMaker's metadata file, /proc (memory, load,
    processes, uptime), the disks, nvidia-smi and what fills a folder. `root` points all of it at a folder of
    fake files, and _shown() / _real() make paths look and work as the machine sees them.
    """

    # ------------------------------------------------------------ this machine

    def _path(self, relative: str) -> Path:
        return Path(self.root, relative)

    def _shown(self, path: str | Path) -> str:
        """
        A path as this machine sees it: under a fake root (tests, demos), without the root's prefix.
        """
        text = str(path)
        if self.root in ("", "/"):
            return text
        root = str(Path(self.root))
        if text == root:
            return "/"
        return text[len(root) :] if text.startswith(root + os.sep) else text

    def _user_home(self) -> str:
        """
        Your home folder (~) as this machine sees it.
        """
        if self.root in ("", "/"):
            return os.path.expanduser("~")
        home = self._shown(self.home())
        return home[: -len("/SageMaker")] if home.endswith("/SageMaker") else home

    def _real(self, path: str | Path) -> Path:
        """
        A path you give (~ is your home folder) -> the folder to read, under the fake root when there is one.
        """
        text = str(path)
        if text == "~" or text.startswith("~/"):
            text = self._user_home() + text[1:]
        real = Path(text)
        root = Path(self.root)
        if (
            self.root not in ("", "/")
            and real.is_absolute()
            and real != root
            and root not in real.parents
        ):
            real = root / text.lstrip("/")
        return real

    def environment(self, *, refresh: bool = False) -> Environment:
        """
        Where this code runs, from SageMaker's metadata file. kind='local' when there isn't one.
        """
        if self._env is None or refresh:
            path = self._path(METADATA_FILE)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._env = parse_metadata(data if isinstance(data, dict) else {})
            except FileNotFoundError:
                self._env = Environment()
            except (OSError, ValueError) as exc:
                self._env = Environment(
                    error=f"{path} couldn't be read ({type(exc).__name__}: {exc})"
                )
        return self._env

    def home(self) -> Path:
        """
        The folder your notebooks and data live in: a notebook instance's volume (/home/ec2-user/SageMaker), a
        Studio space's home (/home/sagemaker-user), otherwise your home folder.
        """
        for relative in _HOMES:
            path = self._path(relative)
            if path.is_dir():
                return path
        return Path.home() if self.root == "/" else self._path("")

    @staticmethod
    def _volume_labels(env: Environment) -> tuple[tuple[str, str], tuple[str, str]]:
        """
        (label, note) for the home disk and the system disk, saying what survives a stop.
        """
        if env.kind == "notebook instance":
            return ("notebook volume", "kept when the instance stops"), (
                "system disk",
                "reset when the instance stops",
            )
        if env.kind == "studio" and env.space:
            return ("space volume", "kept when the app stops"), (
                "app disk",
                "reset when the app stops",
            )
        if env.kind == "studio":
            return ("home folder", "EFS, shared by your apps"), (
                "app disk",
                "reset when the app stops",
            )
        return ("home folder", ""), ("system disk", "")

    @staticmethod
    def _cpu_count() -> int | None:
        try:
            return len(
                os.sched_getaffinity(0)
            )  # the CPUs this process may use (a container's, not the host's)
        except (AttributeError, OSError):
            return os.cpu_count()

    @staticmethod
    def _disk_usage(path: str) -> tuple[int, int, int]:
        usage = shutil.disk_usage(path)
        return usage.total, usage.used, usage.free

    def volume(self, path: str | Path, label: str = "disk", note: str = "") -> Volume:
        """
        How full the disk holding `path` is.
        """
        total, used, free = self._disk_usage(str(path))
        return Volume(self._shown(path), label, total, used, free, note)

    def volumes(self) -> list[Volume]:
        """
        The disks that matter here: where notebooks live, and the system disk (once each).
        """
        home_label, system_label = self._volume_labels(self.environment())
        seen: set[int] = set()
        found = []
        for path, (label, note) in (
            (self.home(), home_label),
            (self._path(""), system_label),
        ):
            try:
                device = os.stat(path).st_dev
                if device in seen:
                    continue
                seen.add(device)
                found.append(self.volume(path, label, note))
            except OSError:
                continue
        return found

    def _gpu_query(self) -> str | None:
        """
        nvidia-smi's CSV line per GPU, or None when there's no NVIDIA driver.
        """
        exe = shutil.which("nvidia-smi")
        if not exe:
            return None
        result = subprocess.run(
            [
                exe,
                "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise OSError(
                (result.stderr or result.stdout).strip()[:200]
                or f"nvidia-smi exited {result.returncode}"
            )
        return result.stdout

    def _processes(self) -> list[ProcessInfo]:
        processes = []
        with os.scandir(self._path("proc")) as entries:
            for entry in entries:
                if not entry.name.isdigit():
                    continue
                try:
                    status = Path(entry.path, "status").read_text(
                        encoding="utf-8", errors="replace"
                    )
                    cmdline = Path(entry.path, "cmdline").read_bytes()
                except OSError:  # it exited, or isn't ours to read
                    continue
                process = parse_process(int(entry.name), status, cmdline)
                if process is not None:
                    process.this = process.pid == self.pid
                    processes.append(process)
        return processes

    def machine(self, *, top: int = 8) -> Machine:
        """
        This machine right now: vCPUs and load, memory, disks, GPUs, the biggest processes (and how much memory
        the Jupyter kernels hold), Python and the common data packages. Reads local files only.
        """
        m = Machine(
            cpus=self._cpu_count(),
            python=platform.python_version(),
            executable=sys.executable,
        )

        def read(part: str, fn: Callable[[], Any]) -> Any:
            try:
                return fn()
            except (OSError, ValueError, IndexError, subprocess.SubprocessError) as exc:
                m.errors[part] = str(exc).strip() or type(exc).__name__
            return None

        if (
            text := read("load", lambda: self._path("proc/loadavg").read_text())
        ) is not None:
            m.load = read("load", lambda: parse_loadavg(text))
        if (
            text := read("memory", lambda: self._path("proc/meminfo").read_text())
        ) is not None:
            info = parse_meminfo(text)
            m.memory_total, m.memory_available = (
                info.get("MemTotal"),
                info.get("MemAvailable"),
            )
        if (
            text := read("uptime", lambda: self._path("proc/uptime").read_text())
        ) is not None:
            seconds = read("uptime", lambda: float(text.split()[0]))
            m.booted = (
                None if seconds is None else _utcnow() - timedelta(seconds=seconds)
            )
        m.volumes = read("disks", self.volumes) or []
        if (text := read("GPUs", self._gpu_query)) is not None:
            m.gpus = parse_gpus(text)
        processes = read("processes", self._processes) or []
        kernels = [p for p in processes if p.kernel]
        m.kernels, m.kernels_memory = len(kernels), sum(p.memory for p in kernels)
        this = next((p for p in processes if p.this), None)
        m.this_memory = this.memory if this else None
        m.processes = sorted(processes, key=lambda p: -p.memory)[:top]
        if this is not None and this not in m.processes:
            m.processes.append(this)
        for name in _PACKAGES:
            try:
                m.packages[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                continue
        return m

    # -------------------------------------------------------------------- disk

    def disk(
        self,
        path: str | Path | None = None,
        *,
        top: int = 20,
        limit: int | None = 200_000,
        progress: Callable[[int], None] | None = None,
    ) -> DiskReport:
        """
        Sizes under a folder (default: home()): per folder up to 3 levels deep, the `top` biggest files, and the
        caches and trash in it. Stays on the folder's disk and doesn't follow links. Stops after `limit` files
        (None: no limit); progress is called with the running count of files.
        """
        started = time.monotonic()
        root = self._real(path) if path else self.home()
        if not root.exists():
            raise ValueError(
                f"There's no folder {self._shown(root)!r} here. disk() with no path measures "
                f"{self._shown(self.home())}."
            )
        if not root.is_dir():
            raise ValueError(
                f"{self._shown(root)!r} is a file ({human_size(root.stat().st_size)}). disk() measures "
                f"a folder: disk({self._shown(root.parent)!r})."
            )
        env = self.environment()
        home = self.home()
        on_home = (
            root.resolve() == home.resolve() or home.resolve() in root.resolve().parents
        )
        label, note = self._volume_labels(env)[0] if on_home else ("disk", "")
        report = DiskReport(self._shown(root))
        try:
            report.volume = self.volume(root, label, note)
        except OSError:
            pass
        device = root.stat().st_dev
        found: dict[int, Clearable] = {}
        largest: list[tuple[int, str, float]] = []  # min-heap of (size, path, mtime)
        stack: list[tuple[str, tuple[str, ...], int | None]] = [(str(root), (), None)]
        count = 0
        while stack and not report.truncated:
            folder, parts, tag = stack.pop()
            try:
                with os.scandir(folder) as scan:
                    entries = list(scan)
            except OSError:
                report.skipped += 1
                continue
            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    report.skipped += 1
                    continue
                if stat.S_ISDIR(info.st_mode):
                    if info.st_dev != device:
                        report.other_disks.append(self._shown(entry.path))
                        continue
                    kind = tag if tag is not None else clearable_kind(entry.path)
                    if kind is not None and kind not in found:
                        _pattern, what, why, _command = _CLEARABLE[kind]
                        found[kind] = Clearable(what, why)
                    if kind is not None and tag is None:
                        found[kind].paths.append(self._shown(entry.path))
                    stack.append((entry.path, parts + (entry.name,), kind))
                    continue
                if not stat.S_ISREG(info.st_mode):
                    continue
                size, modified = (
                    info.st_size,
                    datetime.fromtimestamp(info.st_mtime, timezone.utc),
                )
                count += 1
                report.total.add(size, modified)
                for depth in range(1, min(len(parts), 3) + 1):
                    key = "/".join(parts[:depth])
                    report.folders.setdefault(key, DiskEntry(key)).add(size, modified)
                if tag is not None:
                    found[tag].size += size
                    found[tag].files += 1
                relative = "/".join(parts + (entry.name,))
                if len(largest) < top:
                    heapq.heappush(largest, (size, relative, info.st_mtime))
                elif size > largest[0][0]:
                    heapq.heapreplace(largest, (size, relative, info.st_mtime))
                if progress and count % 1000 == 0:
                    progress(count)
                if limit is not None and count >= limit:
                    report.truncated = True
                    break
        report.largest = [
            DiskEntry(p, s, 1, datetime.fromtimestamp(t, timezone.utc))
            for s, p, t in sorted(largest, reverse=True)
        ]
        report.clearable = sorted(
            (c for c in found.values() if c.size), key=lambda c: -c.size
        )
        for item in report.clearable:
            item.command = clear_command(item, report.path, self._user_home())
        report.seconds = time.monotonic() - started
        return report

    def _booted(self) -> datetime | None:
        """
        When this machine started, from /proc/uptime.
        """
        try:
            seconds = float(self._path("proc/uptime").read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return None
        return _utcnow() - timedelta(seconds=seconds)
