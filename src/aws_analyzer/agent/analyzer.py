"""
AgentAnalyzer: the agent's logic layer. It holds the settings and puts the parts together (the toolkits and their
tools, the read-only guard, the budget, the model and the deepagents loop) and answers questions as AgentAnswer data.
It never prints; AgentView shows what it returns.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, tzinfo
from typing import TYPE_CHECKING, Any, Callable, Iterable

from .._kit.analyzer import _BaseAnalyzer
from .._kit.fmt import _display_zone, _utcnow, _zone_from
from . import harness
from .answers import AgentAnswer
from .budget import Budget, _Run
from .guard import _Guard
from .llm import answer_cost, azure_chat_model, azure_settings, model_name
from .prompt import asked_at, system_prompt
from .runtime import as_tool
from .toolkits import Toolkit, make_toolkits

if TYPE_CHECKING:
    from ..lambda_functions import LambdaAnalyzer
    from ..s3 import S3Analyzer


class AgentAnalyzer(_BaseAnalyzer):
    """
    Answers questions about your AWS account with an agent: a model (an Azure OpenAI deployment) calling read-only
    tools built on the package's analyzers, in LangChain's deepagents loop. Every method returns data; nothing is
    printed.

    The model: deployment= (or AZURE_OPENAI_DEPLOYMENT), endpoint= (or AZURE_OPENAI_ENDPOINT), api_key= (or
    AZURE_OPENAI_API_KEY; azure_ad_token_provider= for Microsoft Entra ID instead) and api_version= (or
    OPENAI_API_VERSION, else AZURE_API_VERSION). chat_model= takes any LangChain chat model instead (one on Amazon
    Bedrock, a fake one in tests). model_prices= {'gpt-4o': (2.50, 10.00)} (USD per 1M input and output tokens) puts
    a price on each answer; without it the cost is unknown and only the tokens are counted.

    What it reads: toolkits= picks the services it has tools for (names like 'lambda' and 's3', or Toolkit objects;
    the default is DEFAULT_TOOLKITS: Lambda and S3, with s3_triggers joining them). The Lambda toolkit reads through
    LambdaAnalyzer: lambda_core= to pass your own, lambda_clients= its clients (for tests), prices= its prices. The
    S3 toolkit reads through S3Analyzer: s3_core= to pass your own (with its prices=). It looks in `region` (the
    session's) or `regions` (a list, or 'all'); buckets are read wherever they are. The user's time zone, so "last night" means their night, is set_timezone()'s (or
    AWS_ANALYZER_TZ's), the zone every report shows times in; `timezone` ('Asia/Kolkata', 'kolkata', '+05:30') sets
    one for the agent alone. `notes` are facts about the account the agent is told ("orders-etl runs at 02:00
    IST"). `budget` caps each question (Budget, or a dict of its fields).
    """

    _LIST_PRICES: dict[str, Any] = {}  # no list prices: Azure OpenAI's depend on the deployment; see model_prices=

    def __init__(
        self,
        session: Any = None,
        *,
        region: str | None = None,
        profile: str | None = None,
        deployment: str | None = None,
        endpoint: str | None = None,
        api_key: str | None = None,
        api_version: str | None = None,
        azure_ad_token_provider: Callable[[], str] | None = None,
        chat_model: Any = None,
        model_prices: dict[str, tuple[float, float]] | None = None,
        timezone: str | tzinfo | None = None,
        regions: Any = None,
        notes: str = "",
        budget: Budget | dict[str, Any] | None = None,
        max_tokens: int | None = 4096,
        toolkits: Iterable[str | Toolkit | type[Toolkit]] | None = None,
        lambda_core: LambdaAnalyzer | None = None,
        lambda_clients: dict[str, Any] | None = None,
        prices: dict[str, float] | None = None,
        s3_core: S3Analyzer | None = None,
    ):
        super().__init__(session, region=region, profile=profile, prices=model_prices)
        self.deployment, self.endpoint, self.api_version = azure_settings(deployment, endpoint, api_version)
        self._api_key = api_key  # never shown; None reads AZURE_OPENAI_API_KEY when the model is made
        self._token_provider = azure_ad_token_provider
        self.chat_model = chat_model
        self.max_tokens = max_tokens
        if timezone is not None:
            _zone_from(timezone)  # a misspelled time zone is an error now, not at the first question
        self.timezone = timezone  # None: the zone reports show times in (set_timezone())
        self.regions = regions
        self.notes = notes
        self.budget = Budget.of(budget)
        self._clients: dict[Any, Any] = {}
        self._lock = threading.Lock()
        self.max_workers = 8
        self._guard = _Guard()
        self.toolkits: list[Toolkit] = [toolkit.bind(self) for toolkit in make_toolkits(
            toolkits, {"lambda": {"analyzer": lambda_core, "clients": lambda_clients, "prices": prices},
                       "s3": {"analyzer": s3_core}})]
        self._graph: Any = None
        self._run: _Run | None = None
        self._tools: list[Callable[..., dict[str, Any]]] | None = None
        self.thread = uuid.uuid4().hex[:12]  # the conversation follow-up questions continue
        self.history: list[AgentAnswer] = []  # every question asked, in order

    def toolkit(self, name: str) -> Toolkit:
        """
        One of the agent's toolkits, by its name ('lambda', 's3').
        """
        for toolkit in self.toolkits:
            if toolkit.name == name:
                return toolkit
        raise KeyError(f"The agent has no {name!r} toolkit; it has {', '.join(t.name for t in self.toolkits)}")

    @property
    def region(self) -> str:
        """
        The region its first toolkit reads by default (the session's).
        """
        return self.toolkits[0].region

    @property
    def client(self) -> Any:
        """
        The main client of its first toolkit (Lambda's, in the default region).
        """
        return self.toolkits[0].client

    def default_regions(self) -> list[str]:
        """
        The regions it checks unless a question names others: regions= as its first toolkit reads it.
        """
        return self.toolkits[0].regions(self.regions)

    @property
    def zone(self) -> tzinfo | None:
        """
        The user's time zone, which "last night" and "today" are read in: timezone= when given, else the zone
        reports show times in (set_timezone(), AWS_ANALYZER_TZ, else UTC). None is this machine's own zone.
        """
        return _zone_from(self.timezone) if self.timezone is not None else _display_zone()

    @property
    def reads(self) -> dict[str, tuple[str, ...]]:
        """
        Each tool's AWS reads, from every toolkit: {'function_health': ('lambda:ListFunctions', ...), ...}.
        """
        return {name: reads for toolkit in self.toolkits for name, reads in toolkit.reads.items()}

    # ------------------------------------------------------------------ the model and the loop

    @property
    def model(self) -> str:
        """
        The deployment or model that answers: the Azure deployment name, or the chat model's own name.
        """
        return model_name(self.chat_model, self.deployment)

    def _chat(self) -> Any:
        """
        The LangChain chat model: chat_model= when given, else AzureChatOpenAI from the settings.
        """
        if self.chat_model is None:
            self.chat_model = azure_chat_model(endpoint=self.endpoint, deployment=self.deployment,
                                               api_version=self.api_version, api_key=self._api_key,
                                               token_provider=self._token_provider, max_tokens=self.max_tokens)
        return self.chat_model

    def instructions(self) -> str:
        """
        The system prompt the agent starts every conversation with (system_prompt() for its toolkits).
        """
        return system_prompt(self.default_regions(), notes=self.notes, budget=self.budget, toolkits=self.toolkits)

    def _harness(self) -> Any:
        """
        The deepagents agent: the model, the tools, the instructions and a memory of the conversation. Made once;
        new_conversation() starts a new thread in it.
        """
        if self._graph is None:
            harness.check_ready()  # says what's missing before anything else is made
            prompt = self.instructions()
            self._graph = harness.build_graph(self._chat(), self.tools(), prompt)
        return self._graph

    def new_conversation(self) -> str:
        """
        Starts a new conversation: the next question won't see the earlier ones. Returns its ID.
        """
        self.thread = uuid.uuid4().hex[:12]
        return self.thread

    # ------------------------------------------------------------------ asking

    def ask(
        self,
        question: str,
        *,
        new: bool = False,
        stop: threading.Event | None = None,
        progress: Callable[..., None] | None = None,
        now: datetime | None = None,
    ) -> AgentAnswer:
        """
        Answers a question: the model calls the tools until it can answer, within the budget. Follow-up questions
        continue the conversation (new=True starts a new one). Set `stop` (a threading.Event) to stop it early, or
        interrupt the kernel: either way the answer covers what it read until then. progress(count) is told each
        step. A model or loop that fails gives an answer with .error set (and .exception) instead of raising.
        """
        question = str(question or "").strip()
        if not question:
            raise ValueError("ask() needs a question, like ask('Which Lambda failed last night?')")
        if new:
            self.new_conversation()
        graph = self._harness()
        moment = now or _utcnow()
        run = _Run(self.budget, stop=stop or threading.Event())
        answer = AgentAnswer(question=question, model=self.model, thread=self.thread, asked=moment)
        self._guard.reset()
        self._run = run
        try:
            answer.text = harness.stream_turn(graph, f"{asked_at(moment, self.zone)}\n\n{question}",
                                              thread=self.thread, run=run, answer=answer, model_prices=self.prices,
                                              progress=progress)
        finally:
            self._run = None
        answer.calls = list(run.calls)
        answer.aws_calls = dict(self._guard.counts)
        answer.blocked = list(self._guard.blocked)
        answer.metrics_read = run.metrics_read
        answer.aws_cost = run.aws_cost + sum(t.request_cost(answer.aws_calls) for t in self.toolkits)
        answer.cost = answer_cost(answer.model, answer.input_tokens, answer.output_tokens, self.prices)
        answer.seconds = run.elapsed
        if answer.stopped is None:
            refused = [c.refused for c in answer.calls if c.refused in ("tool_calls", "time", "cost")]
            answer.stopped = refused[0] if refused else None
        self.history.append(answer)
        return answer

    # ------------------------------------------------------------------ the tools

    def tools(self) -> list[Callable[..., dict[str, Any]]]:
        """
        The tools the agent can call, from every toolkit: plain Python functions (type hints and docstrings say how
        to call them), each reading through a package analyzer and returning a compact, JSON-ready summary with what
        it covered. Each one counts against the question's budget, and an error becomes part of its answer rather
        than an exception.
        """
        if self._tools is None:
            tools: list[Callable[..., dict[str, Any]]] = []
            owners: dict[str, str] = {}
            for toolkit in self.toolkits:
                for fn in toolkit.tools():
                    if fn.__name__ in owners:
                        raise ValueError(f"The {owners[fn.__name__]!r} and {toolkit.name!r} toolkits both have a tool "
                                         f"named {fn.__name__!r}: the model can't tell them apart")
                    owners[fn.__name__] = toolkit.name
                    tools.append(as_tool(fn, toolkit, self._current_run))
            self._tools = tools
        return list(self._tools)

    def _current_run(self) -> _Run:
        """
        The question being answered, or a run of its own for a tool called outside ask().
        """
        return self._run or _Run(self.budget)
