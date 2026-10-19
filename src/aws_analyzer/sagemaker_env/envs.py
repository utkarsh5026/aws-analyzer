"""
SageMakerAnalyzer's Python environments: the ones here and their kernels, the plan to make one, and
create_env(), which runs it: the only code in sagemaker_env that makes anything.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .._kit.errors import _Hint

from .helpers import (
    _ENV_NAME_RE, _clean_version, env_name, human_runtime, kernel_label, new_env_call, parse_python_version,
    parse_pyvenv_cfg, shell_path,
)
from .models import EnvPlan, EnvReport, EnvResult, KernelSpec, PythonEnv
from .analysis.envs import (
    KERNELS_FOLDER, TOOLS, _ANACONDA, _CONDA_ROOTS, _HOME_CONDA_ROOTS, _HOME_TOOL_FOLDERS, _JUPYTER_SERVERS,
    _TOOL_FOLDERS, _same_python, env_steps, output_tail, package_list, parse_conda_history, step_command,
)


class _EnvReads:
    """
    SageMakerAnalyzer's reads of the Python environments here: the tools that make them, the environments and
    Jupyter kernels found by their files (never by running them), and the plan new_env() follows.
    """

    # ----------------------------------------------------- Python environments

    def tools(self) -> dict[str, str]:
        """
        The programs here that make Python environments, in the order new_env() prefers them: mamba, conda,
        micromamba, uv -> the path of each one installed.
        """
        home = self._user_home()
        folders = [*_TOOL_FOLDERS, *(f"{home}/{folder}" for folder in _HOME_TOOL_FOLDERS)]
        found: dict[str, str] = {}
        for name in TOOLS:
            candidates: list[str | None] = []
            if self.root in ("", "/"):
                candidates += [
                    shutil.which(name),
                    os.environ.get("CONDA_EXE") if name == "conda" else None,
                    str(Path(sys.executable).parent / name),  # %pip install uv puts it next to this kernel's Python
                ]
            candidates += [str(self._real(f"{folder}/{name}")) for folder in folders]
            path = next((c for c in candidates if c and os.path.isfile(c) and os.access(c, os.X_OK)), None)
            if path:
                found[name] = path
        return found

    def envs_folder(self) -> Path:
        """
        Where new_env() puts environments: ~/SageMaker/envs on a notebook instance (the notebook volume is the
        only disk it keeps when it stops), ~/.conda/envs elsewhere (a Studio home folder is kept).
        """
        if self.environment().kind == "notebook instance":
            return self.home() / "envs"
        return self._real("~/.conda/envs")

    def _kept(self, path: str | Path) -> bool | None:
        """
        Is `path` (a real path) on the disk that's kept when the notebook stops? None off SageMaker.
        """
        if self.environment().kind not in ("notebook instance", "studio"):
            return None
        home, path = Path(os.path.normpath(self.home())), Path(os.path.normpath(path))
        return path == home or home in path.parents

    @staticmethod
    def _env_python(prefix: Path) -> str:
        """
        The Python version of the environment at `prefix`: '3.11.9', or '' when it can't tell. Reads its files
        (conda-meta, pyvenv.cfg, lib/python3.x), never runs it.
        """
        for meta in sorted((prefix / "conda-meta").glob("python-[0-9]*.json")):
            version = _clean_version(meta.name[len("python-"):])
            if version:
                return version
        try:
            values = parse_pyvenv_cfg((prefix / "pyvenv.cfg").read_text(encoding="utf-8", errors="replace"))
            version = _clean_version(values.get("version_info") or values.get("version") or "")
            if version:
                return version
        except OSError:
            pass
        libs = [p.name[len("python"):] for p in prefix.glob("lib/python3.*")]
        return max(libs, key=lambda v: tuple(int(n) for n in v.split(".") if n.isdigit()), default="")

    @staticmethod
    def _env_made(prefix: Path) -> datetime | None:
        """
        When the environment at `prefix` was made: conda-meta/history's first entry, else when its pyvenv.cfg was
        written.
        """
        history = prefix / "conda-meta" / "history"
        try:
            with open(history, encoding="utf-8", errors="replace") as f:
                made = parse_conda_history(f.read(4096))
            if made is not None:
                return made
        except OSError:
            pass
        for path in (history, prefix / "pyvenv.cfg"):
            try:
                return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            except OSError:
                continue
        return None

    def _env_at(self, prefix: Path, name: str = "") -> PythonEnv | None:
        """
        The environment at `prefix` (a real path), or None when it isn't one: no conda-meta or pyvenv.cfg, or no
        bin/python (a link to a Python that's gone still counts).
        """
        conda = (prefix / "conda-meta").is_dir()
        if not (conda or (prefix / "pyvenv.cfg").is_file()) or not os.path.lexists(prefix / "bin" / "python"):
            return None
        return PythonEnv(
            name or prefix.name,
            self._shown(prefix),
            self._env_python(prefix),
            "conda" if conda else "venv",
            ipykernel=any(prefix.glob("lib/python3*/site-packages/ipykernel")),
            kept=self._kept(prefix),
            made=self._env_made(prefix),
        )

    def _env_prefixes(self, tools: dict[str, str], folder: Path) -> dict[str, str]:
        """
        Where environments may be: each conda installation and its envs/, ~/.conda/envs, new_env()'s folder, what
        conda noted in ~/.conda/environments.txt, and this kernel's -> {real path: name}.
        """
        found: dict[str, str] = {}

        def add(path: Path, name: str = "") -> None:
            found.setdefault(os.path.normpath(path), name)

        def children(path: Path) -> None:
            try:
                for child in sorted(path.iterdir()):
                    if child.is_dir() and not child.name.startswith("."):
                        add(child)
            except OSError:
                pass

        roots = [Path(tools[t]).parent.parent for t in ("mamba", "conda") if t in tools]
        roots += [self._real(r) for r in (*_CONDA_ROOTS, *(f"~/{r}" for r in _HOME_CONDA_ROOTS))]
        for root in roots:
            if root.is_dir():
                add(root, "base")
                children(root / "envs")
        for path in (folder, self._real("~/.conda/envs")):
            children(path)
        try:
            noted = self._real("~/.conda/environments.txt").read_text(encoding="utf-8", errors="replace")
            for line in noted.splitlines():
                if line.strip().startswith("/"):
                    add(self._real(line.strip()))
        except OSError:
            pass
        if self.root in ("", "/"):
            add(Path(sys.prefix))
        return found

    def _kernel_folders(self) -> list[Path]:
        """
        The folders Jupyter finds kernels in: yours (~/.local/share/jupyter/kernels), JUPYTER_PATH's, the Jupyter
        server's environment's, and the system's.
        """
        folders = []
        if self.root in ("", "/"):
            data = os.environ.get("JUPYTER_DATA_DIR")
            folders += [Path(data, "kernels")] if data else []
            folders += [Path(p, "kernels") for p in os.environ.get("JUPYTER_PATH", "").split(os.pathsep) if p]
        folders.append(self._real(f"~/{KERNELS_FOLDER}"))
        servers = [self._real(p) for p in _JUPYTER_SERVERS]
        if self.environment().kind == "local" and self.root in ("", "/"):
            servers.append(Path(sys.prefix))  # off SageMaker, Jupyter usually runs from this kernel's environment
        folders += [p / "share/jupyter/kernels" for p in servers]
        folders += [self._real(p) for p in ("/usr/local/share/jupyter/kernels", "/usr/share/jupyter/kernels")]
        return folders

    def _kernel_at(self, spec: Path, envs: dict[str, PythonEnv]) -> KernelSpec | None:
        """
        The kernel described by `spec` (a kernel.json), or None when it can't be read.
        """
        try:
            data = json.loads(spec.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        argv = data.get("argv") if isinstance(data, dict) else None
        program = str(argv[0]) if isinstance(argv, list) and argv else ""
        kernel = KernelSpec(
            spec.parent.name,
            str(data.get("display_name") or spec.parent.name),
            self._shown(spec.parent),
            program,
            str(data.get("language") or ""),
            kept=self._kept(spec.parent),
        )
        if program.startswith("/"):
            real = self._real(program)
            kernel.missing = not real.exists()
            env = envs.get(os.path.normpath(real.parent.parent))
        else:  # 'python': the Python of the environment the kernel's folder is in
            env = envs.get(os.path.normpath(spec.parents[4])) if len(spec.parents) > 4 else None
        if env is not None:
            kernel.env = env.path
            env.kernels.append(kernel.display)
        return kernel

    def environments(self) -> EnvReport:
        """
        The Python environments on this machine (conda environments and virtual environments) with their Python
        versions, the Jupyter kernels and the environment each starts, the programs here that make environments,
        and where new_env() puts them. Reads local files only.
        """
        env = self.environment()
        report = EnvReport(env.kind, python=platform.python_version(), executable=sys.executable)
        report.tools = self.tools()
        folder = self.envs_folder()
        report.folder, report.folder_kept = self._shown(folder), self._kept(folder)
        report.booted = self._booted()
        here = os.path.normpath(sys.prefix) if self.root in ("", "/") else ""
        envs: dict[str, PythonEnv] = {}
        for path, name in self._env_prefixes(report.tools, folder).items():
            found = self._env_at(Path(path), name)
            if found is not None:
                found.this = path == here
                envs[path] = found
        seen: set[str] = set()
        for kernels in self._kernel_folders():
            try:
                specs = sorted(kernels.glob("*/kernel.json"))
            except OSError as exc:
                report.errors[self._shown(kernels)] = str(exc)
                continue
            for spec in specs:
                if spec.parent.name.lower() in seen:
                    continue  # Jupyter uses the first kernel of a name it finds
                kernel = self._kernel_at(spec, envs)
                if kernel is not None:
                    seen.add(kernel.name.lower())
                    report.kernels.append(kernel)
        if env.kind == "notebook instance":
            # A notebook instance's Jupyter lists each conda environment in ~/anaconda3/envs that has ipykernel as
            # conda_<name> by itself (its EnvironmentKernelSpecManager); there's no kernel.json for those.
            anaconda = os.path.normpath(self._real(f"{_ANACONDA}/envs"))
            for path, e in envs.items():
                if os.path.dirname(path) == anaconda and e.ipykernel and e.name != "JupyterSystemEnv":
                    label = f"conda_{e.name}"
                    e.kernels.append(label)
                    report.kernels.append(
                        KernelSpec(label, label, "", self._shown(Path(path, "bin", "python")), "python", e.path,
                                   e.kept)
                    )
        report.envs = sorted(envs.values(), key=lambda e: (not e.this, e.path))
        return report

    def env_plan(
        self,
        python: Any = "3.11",
        name: str | None = None,
        *,
        packages: Any = None,
        path: str | None = None,
        tool: str = "auto",
    ) -> EnvPlan:
        """
        What new_env() does, without doing it: where the environment goes, what makes it, and the commands.
        Raises ValueError for a version, name, folder or tool it can't use.
        """
        version = parse_python_version(python)
        prefix = self._real(path) if path else None
        if prefix is not None and not prefix.is_absolute():
            prefix = Path.cwd() / prefix  # from the notebook's folder; the kernel needs the whole path
        name = str(name).strip() if name is not None else (prefix.name if prefix else env_name(version))
        if not _ENV_NAME_RE.match(name):
            raise ValueError(f"name takes letters, digits, '.', '_' and '-', like 'py311' (got {name!r}).")
        folder = self.envs_folder()
        prefix = prefix or folder / name
        tools = self.tools()
        tool = str(tool or "auto").strip().lower()
        if tool == "auto":
            if not tools:
                raise _Hint(
                    "There's no conda, mamba, micromamba or uv on this machine to make an environment with. "
                    "%pip install uv adds uv; then run new_env() again."
                )
            tool = next(iter(tools))
        elif tool not in TOOLS:
            raise ValueError(f"tool takes 'auto', {', '.join(map(repr, TOOLS))} (got {tool!r}).")
        elif tool not in tools:
            others = f" ({', '.join(tools)} {'is' if len(tools) == 1 else 'are'}: leave out tool=)" if tools else ""
            how = " %pip install uv adds it." if tool == "uv" else ""
            raise _Hint(f"There's no {tool} on this machine{others}.{how}")
        home = self._user_home()
        existing = self._env_at(prefix) if prefix.exists() else None
        if existing is None and prefix.exists() and (not prefix.is_dir() or any(prefix.iterdir())):
            raise ValueError(
                f"{shell_path(self._shown(prefix), home)} is there already and isn't a Python environment. "
                f"Pick another name=, or remove it first: rm -rf {shell_path(self._shown(prefix), home)}"
            )
        if existing is not None and existing.python and not _same_python(existing.python, version):
            raise ValueError(
                f"{shell_path(existing.path, home)} is there already with Python {existing.python}. Pick another "
                f"name ({new_env_call(version, name + '-new')}), or remove it first: "
                f"rm -rf {shell_path(existing.path, home)}"
            )
        python_dir = ""
        if tool == "uv" and self.environment().kind == "notebook instance":
            python_dir = str(folder / ".python")  # uv's own (~/.local/share/uv) is reset when the instance stops
        steps = env_steps(
            tool, tools[tool], prefix, version, name, package_list(packages), exists=existing is not None,
            ipykernel=bool(existing and existing.ipykernel), python_dir=python_dir,
        )
        for step in steps:
            step.command = step_command(step, self._shown, home)
        plan = EnvPlan(
            name, version, self._shown(prefix), tool, kernel_label(version, name), package_list(packages), steps,
            exists=existing is not None, found=existing.python if existing else "", kept=self._kept(prefix),
            where=self.environment().kind, folder=self._shown(folder),
            kernel_kept=self._kept(self._real(f"~/{KERNELS_FOLDER}")),
        )
        there = next((p for p in (prefix, *prefix.parents) if p.exists()), None)
        if there is not None:
            try:
                plan.free = self._disk_usage(str(there))[2]
            except OSError:
                pass
        if plan.where == "notebook instance" and plan.kept and plan.kernel_kept is False:
            plan.on_start = self._shown(prefix.parent)
        return plan


class _EnvMaking:
    """
    SageMakerAnalyzer's create_env(): it runs an env_plan()'s commands, through _run (a subprocess without a
    shell). The one place in sagemaker_env that makes anything: a Python environment and its Jupyter kernel.
    """

    def _run(self, argv: list[str], env: dict[str, str], timeout: float) -> tuple[int, str]:
        """
        Runs one command -> (its exit status, what it printed). -1 when it ran past `timeout` seconds and was
        stopped, 127 when it couldn't be started. Tests and the demo replace this.
        """
        variables = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
        try:
            done = subprocess.run(
                argv,
                env={**variables, **env},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            output = exc.output or ""
            text = output.decode("utf-8", "replace") if isinstance(output, bytes) else output
            return -1, text + f"\nStopped after {human_runtime(timeout)}."
        except OSError as exc:
            return 127, f"{argv[0]}: {exc.strerror or exc}"
        return done.returncode, done.stdout or ""

    def create_env(
        self,
        plan: EnvPlan,
        *,
        progress: Callable[..., None] | None = None,
        timeout: float = 1800,
    ) -> EnvResult:
        """
        Runs a plan's commands in order and stops at the first that fails -> EnvResult. Makes the folder the
        environment goes in, and never removes anything. Each command may run `timeout` seconds; progress is called
        with (steps done, steps).
        """
        result = EnvResult(plan)
        started = time.monotonic()
        prefix = self._real(plan.path)
        prefix.parent.mkdir(parents=True, exist_ok=True)
        total = len(plan.steps)
        for done, step in enumerate(plan.steps):
            if progress:
                progress(done, total)
            began = time.monotonic()
            code, output = self._run(step.argv, step.env, timeout)
            step.seconds, step.code, step.output = time.monotonic() - began, code, output_tail(output)
            if code != 0:
                result.failed = step
                break
        else:
            if progress:
                progress(total, total)
        result.ok = result.failed is None
        result.python = self._env_python(prefix) if prefix.is_dir() else ""
        result.leftover = (
            not result.ok and not plan.exists and prefix.exists() and self._env_at(prefix) is None
            and (not prefix.is_dir() or any(prefix.iterdir()))
        )
        result.seconds = time.monotonic() - started
        return result
