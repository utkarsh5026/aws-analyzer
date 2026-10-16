"""The Toolkit base class: what a set of tools about one AWS service gives the agent, and the helpers its tools share.

A toolkit reads through one of the package's analyzers (LambdaAnalyzer for the Lambda toolkit), made on first use and
hooked to the agent's read-only guard. It declares, as class attributes, how reports and the system prompt name it,
each tool's AWS reads and the IAM permissions its operations need, and it defines:

    make_analyzer(session)        the analyzer its tools read through
    tools()                       its tools: plain functions whose names, type hints and docstrings are what the model
                                  reads. Each returns a dict the model can read (results.py's helpers), with
                                  coverage keys (window, regions, cut_short, covered_from), and `_summary` (the report's
                                  one line) and `_next` ((View call, what it shows) pairs), which the agent takes out
    instructions(budget)          its section of the system prompt: how to use its tools well
    not_found(exc, arguments)     the result for a resource that isn't there (close names), or None

A tool raises what it likes: the agent's runtime (runtime.py) turns every error into the tool's result, and checks the
question's budget before the tool runs. A tool that reads logs over a window checks the budget's log_days itself
(raising _Refused, as the Lambda toolkit's _log_window does).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

import boto3
from botocore.exceptions import ClientError

from ..._kit.render import _call
from ..budget import Budget

if TYPE_CHECKING:
    from ..analyzer import AgentAnalyzer


class Toolkit:
    """The tools the agent has for one AWS service. A subclass sets the class attributes below and defines
    make_analyzer(), tools() and, when it helps, instructions() and not_found(); see this module's docstring.
    `analyzer` is one to read through instead of the one make_analyzer() would make."""

    name: str = ""  # its key in TOOLKITS and AgentAnalyzer(toolkits=[...]): 'lambda'
    title: str = ""  # how reports name it: 'Lambda'
    covers: str = ""  # what it lets the agent answer, for the system prompt's first paragraph
    privacy: str = ""  # what it never reads or sends to the model, for the tools() report's note
    reads: dict[str, tuple[str, ...]] = {}  # tool name -> the AWS reads it makes (tools() and README list them)
    service: str = ""  # the IAM prefix of most of its operations, for permission_for(): 'lambda'
    services: dict[str, str] = {}  # operation -> its IAM prefix where that's another: {'FilterLogEvents': 'logs'}
    view: str = ""  # the View whose commands its tools suggest next: 'LambdaView'
    client_factory: str | None = None  # the analyzer's method that makes clients, which the guard hooks too

    def __init__(self, analyzer: Any = None):
        self.agent: AgentAnalyzer | None = None  # set by bind(), when an AgentAnalyzer takes the toolkit
        self._analyzer = analyzer

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name!r}>"

    def bind(self, agent: AgentAnalyzer) -> Toolkit:
        """Ties the toolkit to the agent whose session, settings, budget and guard its tools use."""
        if self.agent is not None and self.agent is not agent:
            raise ValueError(f"{self!r} already belongs to another AgentAnalyzer: make a new one for this agent")
        self.agent = agent
        return self

    # ------------------------------------------------------------------ what a toolkit defines

    def make_analyzer(self, session: Any) -> Any:
        """The analyzer its tools read through, on the agent's boto3 session (None when it has none)."""
        raise NotImplementedError

    def tools(self) -> list[Callable[..., dict[str, Any]]]:
        """Its tools, as plain functions (see this module's docstring)."""
        raise NotImplementedError

    def instructions(self, budget: Budget) -> str:
        """Its section of the system prompt, under a heading of its own: how to use its tools well. '' for none."""
        return ""

    def not_found(self, exc: ClientError, arguments: dict[str, Any]) -> dict[str, Any] | None:
        """The result for a tool call whose resource isn't there (`exc`, with the call's `arguments`): an 'error' that
        says so, and the closest names. None when the error is something else."""
        return None

    # ------------------------------------------------------------------ what it gets

    @property
    def analyzer(self) -> Any:
        """The analyzer its tools read through, made on first use and hooked to the agent's read-only guard."""
        if self._analyzer is None:
            self._analyzer = self.make_analyzer(self.agent.session if self.agent is not None else None)
        if self.agent is not None:
            self.agent._guard.attach(self._analyzer, self.client_factory)
        return self._analyzer

    @property
    def region(self) -> str:
        return self.analyzer.region

    @property
    def client(self) -> Any:
        return self.analyzer.client

    @property
    def budget(self) -> Budget:
        """The agent's budget per question (the defaults before it's bound)."""
        return self.agent.budget if self.agent is not None else Budget()

    def regions(self, which: Any = None) -> list[str]:
        """The regions `which` means (None: the analyzer's; 'all'; a name or a list), as its analyzer reads them."""
        regions = getattr(self.analyzer, "regions", None)
        return regions(which) if callable(regions) else [self.region]

    def permission_for(self, exc: ClientError) -> str:
        """The IAM permission a refused call needed: 'lambda:GetFunction' from the operation that failed."""
        op = getattr(exc, "operation_name", "") or ""
        service = self.services.get(op, self.service)
        return f"{service}:{op}" if op and service else ""

    # ------------------------------------------------------------------ helpers for its tools

    def _regions(self, regions: str) -> Any:
        """A tool's regions= ('', 'all', 'us-east-1,eu-west-1') -> what the analyzer's regions() takes: '' is the
        agent's own regions= setting."""
        text = str(regions or "").strip()
        return text or (self.agent.regions if self.agent is not None else None)

    def _count_metrics(self, count: int, price: float) -> None:
        """Counts CloudWatch metrics a tool requested (at `price` USD per 1,000) on the question being answered."""
        run = self.agent._run if self.agent is not None else None
        if run is not None:
            run.read_metrics(count, price)

    def _view_call(self, command: str, *args: Any, region: str | None = None, **kwargs: Any) -> str:
        """A call of the toolkit's View, as it runs in a new cell: "LambdaView().errors('etl', since='24h')". The View
        reads the notebook's default region, so region= is added when the resource is anywhere else."""
        if region and region != self._default_region():
            kwargs = {**kwargs, "region": region}
        return f"{self.view}()." + _call(command, *args, **kwargs)

    @staticmethod
    def _default_region() -> str | None:
        """The region a new boto3 session (and so a new View) uses: AWS_DEFAULT_REGION or the AWS config's."""
        return boto3.session.Session().region_name
