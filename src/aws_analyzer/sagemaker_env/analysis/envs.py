"""
Python environments: the steps that make one, the lifecycle configuration that adds its kernel
back at each start, what's wrong with a plan or the environments here, and what a failed step
means.
"""

from __future__ import annotations

import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from ..._kit.fmt import _plural, human_size

from ..helpers import GB, _minor, kernel_label, new_env_call, shell_path
from ..models import EnvPlan, EnvReport, EnvStep, PythonEnv


TOOLS = ("mamba", "conda", "micromamba", "uv")  # what new_env() makes environments with, in the order it prefers

CHANNEL = "conda-forge"  # conda's defaults channel needs its Terms of Service accepted (and, at bigger companies, a license)

LOW_DISK = 3 * GB  # less free than this where an environment goes gets a warning

_SUGGESTED = ("3.11", "3.12", "3.13", "3.10")  # what envs() suggests making, the first one there isn't one of

KERNELS_FOLDER = ".local/share/jupyter/kernels"  # under your home folder: where `ipykernel install --user` puts a kernel

LIFECYCLE_NAME = "python-envs"  # the lifecycle configuration that adds a notebook instance's kernels back at each start

_ANACONDA = "/home/ec2-user/anaconda3"  # a notebook instance's conda: its environments are on the system disk

_CONDA_ROOTS = ("/opt/conda", _ANACONDA)  # Studio's SageMaker Distribution image, a notebook instance

_HOME_CONDA_ROOTS = ("miniforge3", "miniconda3", "anaconda3", "micromamba")  # under your home folder

_TOOL_FOLDERS = (*(f"{root}/{sub}" for root in _CONDA_ROOTS for sub in ("bin", "condabin")), "/usr/local/bin", "/usr/bin")

_HOME_TOOL_FOLDERS = (".local/bin", ".cargo/bin", *(f"{root}/bin" for root in _HOME_CONDA_ROOTS))

_JUPYTER_SERVERS = ("/opt/conda", f"{_ANACONDA}/envs/JupyterSystemEnv")  # the environment Jupyter itself runs in

_HISTORY_RE = re.compile(r"^==> (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) <==", re.MULTILINE)


def parse_conda_history(text: str) -> datetime | None:
    """
    When a conda environment was made: the first '==> 2025-10-14 09:12:33 <==' in its conda-meta/history,
    which conda writes in the machine's local time.
    """
    match = _HISTORY_RE.search(text)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").astimezone(timezone.utc)


def package_list(packages: Any) -> list[str]:
    """
    What to pip install: 'pandas boto3>=1.34', 'pandas, boto3', ['pandas'] or '-r requirements.txt' -> a list.
    """
    if packages is None:
        return []
    words = shlex.split(packages) if isinstance(packages, str) else [str(p) for p in packages]
    return [w.strip().rstrip(",") for w in words if w.strip().rstrip(",")]


def env_steps(
    tool: str,
    exe: str,
    prefix: str | Path,
    python: str,
    name: str,
    packages: Iterable[str] = (),
    *,
    exists: bool = False,
    ipykernel: bool = False,
    python_dir: str = "",
) -> list[EnvStep]:
    """
    The commands that make an environment at `prefix` with this Python (unless it `exists`), pip install
    `packages` in it, and add it to Jupyter as kernel `name` -> [EnvStep]. Nothing is run. tool: 'mamba', 'conda',
    'micromamba' (conda-forge only) or 'uv' (a virtual environment with pip in it; python_dir: where uv keeps the
    Pythons it downloads).
    """
    python_exe = str(Path(prefix, "bin", "python"))
    install = list(packages)
    steps = []
    if not exists:
        if tool == "uv":
            steps.append(
                EnvStep(
                    "Make the environment",
                    [exe, "venv", "--seed", "--python", python, str(prefix)],
                    {"UV_PYTHON_INSTALL_DIR": python_dir} if python_dir else {},
                )
            )
            install = ["ipykernel", *install]
        else:
            steps.append(
                EnvStep(
                    "Make the environment",
                    [exe, "create", "--yes", "--quiet", "--prefix", str(prefix), "--override-channels",
                     "--channel", CHANNEL, f"python={python}", "ipykernel"],
                )
            )
    elif not ipykernel:
        install = ["ipykernel", *install]
    if install:
        steps.append(
            EnvStep(
                "Install packages" if packages else "Install ipykernel",
                [exe, "pip", "install", "--python", python_exe, *install]
                if tool == "uv"
                else [python_exe, "-m", "pip", "install", "--disable-pip-version-check", *install],
            )
        )
    steps.append(
        EnvStep(
            "Add the Jupyter kernel",
            [python_exe, "-m", "ipykernel", "install", "--user", "--name", name,
             "--display-name", kernel_label(python, name)],
        )
    )
    return steps


def step_command(step: EnvStep, shown: Callable[[str], str] | None = None, home: str | None = None) -> str:
    """
    A step as you'd type it in a terminal, with your home folder as ~: 'conda create --yes ... --prefix
    ~/SageMaker/envs/py311 ...'. shown: turns a path into the one this machine sees (tests use a fake root).
    """
    shown = shown or (lambda text: text)

    def word(text: str) -> str:
        text = shown(text)
        return shell_path(text, home) if text.startswith("/") else shlex.quote(text)

    variables = [f"{key}={word(value)}" for key, value in step.env.items()]
    return " ".join(variables + [word(arg) for arg in step.argv])


def output_tail(text: str, lines: int = 30) -> str:
    """
    The end of what a command printed: its last `lines` lines, each as it was left after progress updates.
    """
    kept = [line.rstrip("\r").rsplit("\r", 1)[-1].rstrip() for line in text.split("\n")]
    kept = [line for line in kept if line]
    return "\n".join(kept[-lines:])[-4000:]


def on_start_script(folder: str) -> str:
    """
    A notebook instance's on-start lifecycle script that adds the Jupyter kernel of each environment in `folder`
    back at every start: the environments are on the notebook volume, which is kept, but kernels are in ~/.local,
    which is reset when the instance stops.
    """
    return "\n".join(
        [
            "#!/bin/bash",
            f"# Adds back the Jupyter kernel of each environment in {folder} at every start.",
            "sudo -u ec2-user -i <<'EOF'",
            f"for env in {shlex.quote(folder)}/*/; do",
            '    env="${env%/}"; name="$(basename "$env")"',
            '    "$env/bin/python" -c "import ipykernel" 2>/dev/null || continue',
            "    version=\"$(\"$env/bin/python\" -c 'import sys; print(\"%d.%d\" % sys.version_info[:2])')\"",
            '    "$env/bin/python" -m ipykernel install --user --name "$name" --display-name "Python $version ($name)"',
            "done",
            "EOF",
        ]
    )


def kernels_lifecycle(folder: str, notebook: str = "") -> str:
    """
    The commands that save on_start_script() and attach it to notebook instance `notebook` as a lifecycle
    configuration, so its kernels come back by themselves after a restart (shown, never run).
    """
    lines = [
        "# 1. In a terminal here (File > New > Terminal), save the script:",
        "cat > on-start.sh <<'SCRIPT'",
        on_start_script(folder),
        "SCRIPT",
        "# 2. Make it a lifecycle configuration. A notebook instance has one: if it has one already (auto-stop,",
        "#    say), add the script's lines to that one's on-start script instead.",
        "aws sagemaker create-notebook-instance-lifecycle-config \\",
        f"    --notebook-instance-lifecycle-config-name {LIFECYCLE_NAME} \\",
        '    --on-start Content="$(base64 -w0 on-start.sh)"',
    ]
    if notebook:
        lines += [
            "# 3. Attach it while the notebook instance is stopped. Stopping it ends this terminal, so run these",
            "#    from AWS CloudShell or your laptop:",
            f"aws sagemaker stop-notebook-instance --notebook-instance-name {notebook}",
            f"aws sagemaker wait notebook-instance-stopped --notebook-instance-name {notebook}",
            f"aws sagemaker update-notebook-instance --notebook-instance-name {notebook} "
            f"--lifecycle-config-name {LIFECYCLE_NAME}",
            f"aws sagemaker start-notebook-instance --notebook-instance-name {notebook}",
        ]
    return "\n".join(lines)


def _disks(kind: str) -> tuple[str, str, str]:
    """
    (the disk that's kept, the one that's reset, when it's reset) for where this runs.
    """
    if kind == "notebook instance":
        return "notebook volume", "system disk", "the notebook instance stops"
    if kind == "studio":
        return "home folder", "app disk", "the app stops"
    return "", "", ""


def _same_python(found: str, wanted: str) -> bool:
    """
    Does the Python an environment has ('3.11.9') match the one asked for ('3.11')?
    """
    return found.split(".")[: len(wanted.split("."))] == wanted.split(".")


def plan_findings(plan: EnvPlan, home: str | None = None) -> list[tuple[str, str]]:
    """
    What to know before new_env() runs a plan: an environment that's there already, a disk too full, a folder
    that's reset when the notebook stops, a kernel that is -> [(level, message)].
    """
    found: list[tuple[str, str]] = []
    _, reset_disk, event = _disks(plan.where)
    where = shell_path(plan.path, home)
    if plan.exists:
        rest = "its packages are installed and its" if plan.packages else "its"
        found.append(
            (
                "info",
                f"{where} is there already with Python {plan.found or plan.python}, so it isn't made again: only "
                f"{rest} Jupyter kernel is added.",
            )
        )
    if plan.free is not None and plan.free < LOW_DISK:
        found.append(
            (
                "warn",
                f"Only {human_size(plan.free)} is free where it goes, and an environment takes from about 300 MB to "
                "several GB (PyTorch alone is about 4 GB). When the disk fills, the install fails. disk() shows what "
                "fills it and what's safe to clear.",
            )
        )
    if plan.kept is False and event:
        found.append(
            (
                "warn",
                f"{where} is on the {reset_disk}, which is reset when {event}: the environment goes with it. Leave "
                f"out path= to put it in {shell_path(plan.folder, home)}, which is kept.",
            )
        )
    if plan.kept and plan.kernel_kept is False:
        again = new_env_call(plan.python, plan.name)
        found.append(
            (
                "info",
                f"Its kernel goes in ~/{KERNELS_FOLDER}, which is reset when {event}, while the environment is kept. "
                f"After a restart, {again} adds the kernel back in seconds, or the lifecycle configuration below "
                "does it at every start.",
            )
        )
    return found


def lost_envs(report: EnvReport) -> list[PythonEnv]:
    """
    The environments made since this machine started on the disk that's reset when the notebook stops: they
    go then. (The image's own come back; a conda installation's base isn't made, only changed.)
    """
    return [
        e
        for e in report.envs
        if e.kept is False and e.name != "base" and e.made and report.booted and e.made > report.booted
    ]


def env_findings(report: EnvReport, home: str | None = None) -> list[tuple[str, str]]:
    """
    What to know about the Python environments and kernels here: environments that go when the notebook stops,
    kernels that fail to start, environments missing from the kernel menu -> [(level, message)].
    """
    found: list[tuple[str, str]] = []
    _, reset_disk, event = _disks(report.kind)
    folder = shell_path(report.folder, home) if report.folder else ""
    lost = lost_envs(report)
    if lost and event:
        e = lost[0]
        names = ", ".join(x.name for x in lost[:5]) + (" …" if len(lost) > 5 else "")
        again = new_env_call(_minor(e.python), e.name)
        found.append(
            (
                "warn",
                f"{_plural(len(lost), 'environment')} made since this machine started "
                f"{'is' if len(lost) == 1 else 'are'} on the {reset_disk}, which is reset when {event} ({names}): "
                f"{'it goes' if len(lost) == 1 else 'they go'}, with {'its' if len(lost) == 1 else 'their'} packages "
                f"and kernels. {again} makes {e.name} again in {folder}, which is kept; then install its packages "
                "there.",
            )
        )
    broken = [k for k in report.kernels if k.missing]
    for k in broken[:3]:
        found.append(
            (
                "warn",
                f"Kernel '{k.display}' starts {shell_path(k.program, home)}, which isn't there any more: picking it "
                f"in Jupyter fails. To take it off the kernel menu: rm -rf {shell_path(k.path, home)}",
            )
        )
    if len(broken) > 3:
        found.append(("warn", f"{_plural(len(broken) - 3, 'more kernel')} start a Python that isn't there any more."))
    unregistered = [
        e for e in report.envs if not e.kernels and report.folder and Path(e.path).parent == Path(report.folder)
    ]
    if unregistered:
        e = unregistered[0]
        names = ", ".join(x.name for x in unregistered[:5]) + (" …" if len(unregistered) > 5 else "")
        again = new_env_call(_minor(e.python), e.name)
        why = (
            f" Kernels are in ~/.local, which is reset when {event}, while the environments are kept."
            if report.kind == "notebook instance"
            else ""
        )
        found.append(
            (
                "info",
                f"{names} in {folder} {'has' if len(unregistered) == 1 else 'have'} no Jupyter kernel, so "
                f"{'it isn' if len(unregistered) == 1 else 'they aren'}'t in the kernel menu.{why} {again} adds "
                f"{e.name}'s back in seconds"
                + (", and the lifecycle configuration below adds them all at every start." if why else "."),
            )
        )
    if not report.tools:
        found.append(
            (
                "info",
                "There's no conda, mamba, micromamba or uv here, so new_env() can't make an environment. "
                "%pip install uv adds uv; new_env('3.11') then uses it.",
            )
        )
    if report.errors:
        found.append(
            ("info", "Couldn't read " + ", ".join(f"{part} ({why})" for part, why in report.errors.items()) + ".")
        )
    return found


_FAILURES = [  # (what the output says, what it means and what to do); {python} is the version asked for
    (
        re.compile(r"No space left on device", re.IGNORECASE),
        "The disk filled up. disk() shows what fills it and what's safe to clear.",
    ),
    (
        re.compile(r"No matching distribution found for (\S+)", re.IGNORECASE),
        "pip can't find {match}: check its name and version on pypi.org.",
    ),
    (
        re.compile(
            r"PackagesNotFound|nothing provides (?:requested )?python|No interpreter found|No download found|"
            r"not available from current channels",
            re.IGNORECASE,
        ),
        "Python {python} isn't there to download: ask for a release that exists, like '3.12' or '3.11'.",
    ),
    (
        re.compile(
            r"resolve host|Name or service not known|name resolution|Failed to establish|Connection (?:refused|reset)|"
            r"timed? ?out|CondaHTTPError|Failed to download|error sending request|dns error|ProxyError|"
            r"Network is unreachable",
            re.IGNORECASE,
        ),
        "It couldn't download what it needs. A notebook without direct internet access needs a NAT gateway or a "
        "package mirror such as AWS CodeArtifact; instance() shows its internet access.",
    ),
]


def env_failure(step: EnvStep, python: str = "") -> str:
    """
    What a failed step's output means and what to do about it.
    """
    if step.code == 127:
        return f"{step.argv[0]} couldn't be run: it isn't there any more. envs() shows what's installed."
    if step.code == -1:
        return (
            "It took too long and was stopped. Run the commands below in a terminal (File > New > Terminal) to see "
            "where it's stuck."
        )
    for pattern, meaning in _FAILURES:
        match = pattern.search(step.output)
        if match:
            return meaning.format(python=python, match=match.group(1) if match.groups() else "")
    return (
        "The end of what it printed is below. The commands are below too, to run in a terminal (File > New > "
        "Terminal) and see all of it."
    )
