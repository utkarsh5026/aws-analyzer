"""
The agent's Python workspace: a sandbox where the model's own code runs over files the tools save into it. A
Workspace is the interface every sandbox gives (put a file, list them, run code); AgentAnalyzer(python=...) takes one,
and the Python toolkit (toolkits/python_tools.py) gives the model run_python over it.

What a sandbox must promise, since nothing here can check it: the code it runs has no AWS credentials and no network,
and sees only the workspace's files. That keeps the agent read-only: data reaches the workspace only through the
toolkits' tools, whose AWS clients the guard hooks, and the code itself can't reach AWS. Each run starts a fresh
interpreter; the files stay between runs, the variables don't.

clean_path() is pure: the one way a path into the workspace is read, so no tool writes outside it.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field

_MAX_PATH_CHARACTERS = 300


@dataclass
class WorkspaceFile:
    """
    One file in the workspace: its path from the workspace's root ('lambda/etl/app.py') and its size in bytes.
    """

    path: str
    size: int


@dataclass
class PythonRun:
    """
    What one run of the model's code did: whether it finished without an error, what it printed (stdout) and its
    errors (stderr, a traceback when it raised), how long it took, whether the sandbox stopped it at its time limit,
    and the files it wrote or changed, by path.
    """

    ok: bool
    stdout: str = ""
    stderr: str = ""
    seconds: float = 0.0
    timed_out: bool = False
    new_files: list[str] = field(default_factory=list)


class Workspace:
    """
    A sandbox the agent runs Python in. A subclass sets the class attributes below and defines put(), files() and
    run(); close() lets it go when the agent is done with it. The code it runs must have no AWS credentials and no
    network, and see only the workspace's files (see this module's docstring).

        title     how reports name it: 'a local sandbox'
        place     what tells it apart, after its title: '(Pyodide on Deno, in WebAssembly: no network, ...)'
        packages  what the code can import besides the standard library: ('pandas', 'numpy')
    """

    title: str = "a workspace"
    place: str = ""
    packages: tuple[str, ...] = ()

    def put(self, path: str, data: bytes) -> None:
        """
        Writes a file at `path` (clean_path()'s, from the workspace's root), replacing one that's there.
        """
        raise NotImplementedError

    def files(self) -> list[WorkspaceFile]:
        """
        Every file in the workspace, by path.
        """
        raise NotImplementedError

    def run(self, code: str, *, timeout: float) -> PythonRun:
        """
        Runs `code` in a fresh interpreter whose working directory is the workspace's root, for at most `timeout`
        seconds, and returns what it did. Raises nothing for the code's own errors: they're the run's stderr.
        """
        raise NotImplementedError

    def close(self) -> None:
        """
        Lets the sandbox go. The default has nothing to let go.
        """

    def __repr__(self) -> str:
        return f"<{type(self).__name__}: {self.title}>"


def clean_path(path: str) -> str:
    """
    A path into the workspace as it's stored: '/lambda//etl/./app.py' -> 'lambda/etl/app.py'. ValueError for one
    that would leave the workspace ('../x'), names no file, or is too long.
    """
    text = str(path or "").replace("\\", "/").strip()
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if not parts:
        raise ValueError(f"{path!r} names no file in the workspace")
    if ".." in parts:
        raise ValueError(f"{path!r} leaves the workspace: paths can't use '..'")
    cleaned = posixpath.join(*parts)
    if len(cleaned) > _MAX_PATH_CHARACTERS:
        raise ValueError(f"The path {cleaned[:40]!r}... is longer than {_MAX_PATH_CHARACTERS} characters")
    return cleaned


def workspace_of(value: object) -> Workspace | None:
    """
    AgentAnalyzer(python=...) as a Workspace: one passed in, a built-in sandbox by its name ('local': LocalSandbox,
    Pyodide on Deno), or None for no workspace.
    """
    if value is None or isinstance(value, Workspace):
        return value
    if isinstance(value, str):
        from .sandboxes import SANDBOXES

        name = value.strip().lower()
        if name in SANDBOXES:
            return SANDBOXES[name]()
        raise ValueError(f"There's no {value!r} sandbox: python= takes {', '.join(map(repr, SANDBOXES))} or a "
                         "Workspace")
    raise TypeError(f"python= takes 'local' (a sandbox on this machine) or a Workspace (aws_analyzer.agent.Workspace: "
                    f"a sandbox with no AWS credentials or network), not {value!r}")
