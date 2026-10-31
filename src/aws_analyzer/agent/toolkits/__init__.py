"""
The agent's toolkits: one module per AWS service (lambda_tools.py, s3_tools.py, bedrock_kb_tools.py), each a
Toolkit subclass (base.py) with the tools the agent can call about that service. TOOLKITS names the built-in ones;
AgentAnalyzer(toolkits=[...]) picks which the agent gets, by name or as Toolkit objects (yours too), and
DEFAULT_TOOLKITS is what it gets without that.

Adding a service: a module here (`<service>_tools.py`) with a Toolkit subclass that reads through the service's
analyzer, its name in TOOLKITS (and in DEFAULT_TOOLKITS when every agent should have it), the analyzer in
COMPANIONS["agent"] in .claude/skills/check/rules.py, and its tools' reads in README's IAM permissions. A tool that
joins two services (S3's s3_triggers, which reads Lambda too) goes in one of their toolkits, which offers it when the
agent has the other (Toolkit.peer()). The Python toolkit (python_tools.py) isn't about a service: it runs the model's
code in the agent's workspace (AgentAnalyzer(python=...)), and each service's toolkit offers a save_* tool that puts
its data there when the agent has it (peer('python')). Nor is the Runbooks toolkit (runbook_tools.py): it reads the
agent's step-by-step guides (AgentAnalyzer(runbooks=...)), which the analyzer adds last, not by name.
"""

from __future__ import annotations

import difflib
from typing import Any, Iterable

from .base import Toolkit
from .bedrock_kb_tools import BedrockKBToolkit
from .lambda_tools import LambdaToolkit
from .python_tools import PythonToolkit
from .runbook_tools import RunbookToolkit
from .s3_tools import S3Toolkit

TOOLKITS: dict[str, type[Toolkit]] = {"lambda": LambdaToolkit, "s3": S3Toolkit, "bedrock_kb": BedrockKBToolkit,
                                      "python": PythonToolkit}
DEFAULT_TOOLKITS = ("lambda", "s3", "bedrock_kb")


def make_toolkit(spec: str | Toolkit | type[Toolkit], **settings: Any) -> Toolkit:
    """
    A Toolkit from its name in TOOLKITS ('lambda', 's3'), a Toolkit subclass, or a Toolkit (returned as it is).
    settings are the new toolkit's own (LambdaToolkit's analyzer=, clients=, prices=; S3Toolkit's and
    BedrockKBToolkit's analyzer=; PythonToolkit's workspace=).
    """
    if isinstance(spec, Toolkit):
        return spec
    if isinstance(spec, type) and issubclass(spec, Toolkit):
        return spec(**settings)
    if isinstance(spec, str):
        name = spec.strip().lower()
        if name not in TOOLKITS:
            close = difflib.get_close_matches(name, TOOLKITS, n=1)
            hint = f" Did you mean {close[0]!r}?" if close else ""
            raise ValueError(f"There's no {spec!r} toolkit.{hint} The agent has: {', '.join(sorted(TOOLKITS))}")
        return TOOLKITS[name](**settings)
    raise TypeError(f"toolkits= takes names like 'lambda' or 's3', Toolkit subclasses or Toolkit objects, not {spec!r}")


def default_toolkits() -> list[Toolkit]:
    """
    A new, unbound Toolkit for each of DEFAULT_TOOLKITS.
    """
    return [make_toolkit(name) for name in DEFAULT_TOOLKITS]


def make_toolkits(specs: Iterable[str | Toolkit | type[Toolkit]] | None,
                  settings: dict[str, dict[str, Any]] | None = None) -> list[Toolkit]:
    """
    The toolkits toolkits= names (DEFAULT_TOOLKITS for None), each once. settings: a built-in toolkit's own
    settings by its name ({'lambda': {'clients': ...}}), for the ones made from a name.
    """
    settings = settings or {}
    made: list[Toolkit] = []
    for spec in DEFAULT_TOOLKITS if specs is None else ([specs] if isinstance(specs, (str, Toolkit, type))
                                                          else list(specs)):
        name = spec.strip().lower() if isinstance(spec, str) else None
        toolkit = make_toolkit(spec, **settings.get(name or "", {}))
        if any(t.name == toolkit.name for t in made):
            raise ValueError(f"toolkits= has {toolkit.name!r} twice")
        made.append(toolkit)
    if not made:
        raise ValueError(f"toolkits= is empty: the agent needs at least one, like {list(DEFAULT_TOOLKITS)}")
    return made


__all__ = ["DEFAULT_TOOLKITS", "TOOLKITS", "BedrockKBToolkit", "LambdaToolkit", "PythonToolkit", "RunbookToolkit", "S3Toolkit", "Toolkit",
           "default_toolkits", "make_toolkit", "make_toolkits"]
