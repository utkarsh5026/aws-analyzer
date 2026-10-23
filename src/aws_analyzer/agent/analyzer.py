"""
AgentAnalyzer: the agent's logic layer. It holds the settings and puts the parts together (the toolkits and their
tools, the read-only guard, the budget, the model and the deepagents loop) and answers questions as AgentAnswer data.
It never prints; AgentView shows what it returns.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable

from botocore.exceptions import BotoCoreError, ClientError

from .._bedrock.foundation_models import parse_models
from .._bedrock.models import ModelInfo
from .._kit.analyzer import _BaseAnalyzer
from .._kit.errors import _error_name, _Hint
from .._kit.fmt import _display_zone, _utcnow, _zone_from
from . import harness
from .answers import AgentAnswer, AgentStep
from .budget import Budget, _Run
from .charts import CHARTS_FOLDER, write_charts
from .guard import _Guard
from .llm import (
    AZURE_API_VERSION,
    answer_cost,
    azure_chat_model,
    azure_settings,
    bedrock_chat_model,
    bedrock_model_id,
    is_bedrock_profile,
    is_bedrock_version,
    langchain_chat_model,
    model_name,
    model_rates,
    no_model_hint,
    openai_chat_model,
    pick_provider,
    provider_info,
)
from .prompt import asked_at, system_prompt
from .runtime import as_tool
from .toolkits import DEFAULT_TOOLKITS, PythonToolkit, Toolkit, make_toolkits
from .workspace import Workspace, workspace_of
from .workspace_files import workspace_backend

if TYPE_CHECKING:
    from ..lambda_functions import LambdaAnalyzer
    from ..s3 import S3Analyzer


_THREAD_ID_LENGTH = 12
_WORKERS = 8
_MODEL_SETTINGS = ("provider", "model_id", "deployment", "endpoint", "api_version", "_api_key", "_token_provider",
                   "chat_model", "given_chat_model")


def _new_thread() -> str:
    """
    A new conversation's ID.
    """
    return uuid.uuid4().hex[:_THREAD_ID_LENGTH]


def _with_python(toolkits: Iterable[str | Toolkit | type[Toolkit]] | None,
                 workspace: Workspace | None) -> Iterable[str | Toolkit | type[Toolkit]] | None:
    """
    toolkits= with the Python toolkit added last when there's a workspace (python=) and toolkits= doesn't name it.
    """
    if workspace is None:
        return toolkits
    if toolkits is None:
        specs: list[str | Toolkit | type[Toolkit]] = list(DEFAULT_TOOLKITS)
    elif isinstance(toolkits, (str, Toolkit, type)):
        specs = [toolkits]
    else:
        specs = list(toolkits)
    if any(_is_python(spec) for spec in specs):
        return specs
    return [*specs, "python"]


def _is_python(spec: str | Toolkit | type[Toolkit]) -> bool:
    """
    Whether a toolkits= entry is the Python toolkit: its name, its class or one made from it.
    """
    if isinstance(spec, str):
        return spec.strip().lower() == PythonToolkit.name
    if isinstance(spec, type):
        return issubclass(spec, PythonToolkit)
    return isinstance(spec, PythonToolkit)


def _checked_zone(timezone: str | tzinfo | None) -> str | tzinfo | None:
    """
    timezone= as it's kept, after checking it names a zone, so a misspelled one is an error now rather than at the
    first question. None is the zone reports show times in (set_timezone()).
    """
    if timezone is not None:
        _zone_from(timezone)
    return timezone


class AgentAnalyzer(_BaseAnalyzer):
    """
    Answers questions about your AWS account with an agent: a model (on Amazon Bedrock, OpenAI, Azure OpenAI or any
    provider LangChain supports) calling read-only tools built on the package's analyzers, in LangChain's deepagents
    loop. Every method returns data; nothing is printed.

    The model: provider= says who serves it ('bedrock', 'openai', 'azure', or any provider LangChain's
    init_chat_model knows: 'anthropic', 'google_genai', 'ollama', 'mistralai', ...) and model= which one;
    model='openai:gpt-4.1' says both. Without them, pick_provider() works it out from the model's name and the
    environment: AWS_ANALYZER_AGENT_MODEL, then Azure's AZURE_OPENAI_* settings, then OPENAI_API_KEY.
    Amazon Bedrock is called with the agent's own session (the notebook's credentials and region), so nothing leaves
    AWS. Its model is Claude Haiku 4.5 unless model= names another: a model ID, an inference profile, or a short name
    like 'sonnet' that the region's model list resolves. Its tokens are priced at Bedrock's list prices.
    OpenAI answers with OPENAI_MODEL (gpt-4.1) unless model= says otherwise, with api_key= (or OPENAI_API_KEY);
    endpoint= points it at any server that speaks OpenAI's API (a LiteLLM or vLLM gateway, OpenRouter).
    Azure OpenAI takes deployment= (or AZURE_OPENAI_DEPLOYMENT; model= works too), endpoint= (or
    AZURE_OPENAI_ENDPOINT), api_key= (or AZURE_OPENAI_API_KEY; azure_ad_token_provider= for Microsoft Entra ID
    instead) and api_version= (or OPENAI_API_VERSION, else AZURE_API_VERSION).
    Another LangChain provider takes api_key= (else its own environment variable) and endpoint= as its base URL.
    chat_model= takes any LangChain chat model instead (a fake one in tests). model_prices= {'gpt-4o': (2.50,
    10.00)} (USD per 1M input and output tokens) puts a price on each answer, over Bedrock's list prices; without
    either the cost is unknown and only the tokens are counted. max_tokens caps each of the model's replies.
    use_model() switches to another model later (the chat window's ⚙ model does it by clicking).

    What it reads: toolkits= picks the services it has tools for (names like 'lambda' and 's3', or Toolkit objects;
    the default is DEFAULT_TOOLKITS: Lambda and S3, with s3_triggers joining them). The Lambda toolkit reads through
    LambdaAnalyzer: lambda_core= to pass your own, lambda_clients= its clients (for tests), prices= its prices. The
    S3 toolkit reads through S3Analyzer: s3_core= to pass your own (with its prices=). It never reads what's inside a
    file unless read_files=True, which adds preview_file: a file's first rows, lines or text, with anything that
    looks like a secret hidden, sent to the model's provider (budget's file_bytes and file_chars cap it). python= takes a
    Workspace (aws_analyzer.agent.Workspace: a sandbox with no AWS credentials or network) and adds the Python toolkit:
    run_python runs code the model writes over files the other toolkits save there (Lambda's save_function_code),
    and what it prints goes to the model, secrets hidden (budget's python_runs, python_seconds and workspace_bytes
    cap it). It looks in `region` (the
    session's) or `regions` (a list, or 'all'); buckets are read wherever they are. The user's time zone, so "last night" means their night, is set_timezone()'s (or
    AWS_ANALYZER_TZ's), the zone every report shows times in; `timezone` ('Asia/Kolkata', 'kolkata', '+05:30') sets
    one for the agent alone. `notes` are facts about the account the agent is told ("orders-etl runs at 02:00
    IST"). `budget` caps each question (Budget, or a dict of its fields).

    Its only list prices are Amazon Bedrock's (_bedrock's MODEL_PRICES): the other providers' depend on the plan or
    the deployment (see model_prices=). `provider` and `model_id` are the provider and model it was given or worked
    out, `thread` is the conversation follow-up questions continue, and `history` every question asked, in order.
    """

    _LIST_PRICES: dict[str, Any] = {}

    def __init__(  # noqa: PLR0913
        self,
        session: Any = None,
        *,
        region: str | None = None,
        profile: str | None = None,
        provider: str | None = None,
        model: str | None = None,
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
        read_files: bool = False,
        python: Workspace | None = None,
    ):
        super().__init__(session, region=region, profile=profile, prices=model_prices)
        self.given_chat_model: Any = None
        self._set_model(provider, model, deployment=deployment, endpoint=endpoint, api_key=api_key,
                        api_version=api_version, token_provider=azure_ad_token_provider, chat_model=chat_model)
        self.max_tokens = max_tokens
        self.timezone = _checked_zone(timezone)
        self.regions = regions
        self.notes = notes
        self.budget = Budget.of(budget)
        self._clients: dict[Any, Any] = {}
        self._lock = threading.Lock()
        self.max_workers = _WORKERS
        self._guard = _Guard()
        workspace = workspace_of(python)
        self.toolkits: list[Toolkit] = [toolkit.bind(self) for toolkit in make_toolkits(
            _with_python(toolkits, workspace),
            {"lambda": {"analyzer": lambda_core, "clients": lambda_clients, "prices": prices},
             "s3": {"analyzer": s3_core, "read_files": read_files}, "python": {"workspace": workspace}})]
        self._graph: Any = None
        self._run: _Run | None = None
        self._tools: list[Callable[..., dict[str, Any]]] | None = None
        self.thread = _new_thread()
        self.history: list[AgentAnswer] = []

    def _set_model(self, provider: str | None, model: str | None, *, deployment: str | None,  # noqa: PLR0913
                   endpoint: str | None, api_key: str | None, api_version: str | None,
                   token_provider: Callable[[], str] | None, chat_model: Any) -> None:
        """
        The model settings as AgentAnalyzer() takes them: the provider and model pick_provider() works out, Azure's
        deployment, endpoint and API version (from the environment when not given), the key and the chat model. A
        chat model passed in is also kept apart (given_chat_model), so the agent can switch back to it.
        """
        azure_given = bool(deployment or token_provider or "azure" in (endpoint or "").lower())
        self.provider, wanted = pick_provider(provider, model, chat_model=chat_model, azure=azure_given,
                                              api_key=bool(api_key))
        self.deployment, self.endpoint, self.api_version = deployment, endpoint, api_version
        if self.provider == "azure":
            self.deployment, self.endpoint, self.api_version = azure_settings(deployment or wanted or None, endpoint,
                                                                              api_version)
            wanted = self.deployment or ""
        self.model_id = wanted or provider_info(self.provider).default
        self._api_key = api_key
        self._token_provider = token_provider
        self.chat_model = chat_model
        if chat_model is not None:
            self.given_chat_model = chat_model

    def use_model(  # noqa: PLR0913
        self,
        provider: str | None = None,
        model: str | None = None,
        *,
        endpoint: str | None = None,
        api_key: str | None = None,
        deployment: str | None = None,
        api_version: str | None = None,
        azure_ad_token_provider: Callable[[], str] | None = None,
        chat_model: Any = None,
    ) -> str:
        """
        Switches the model the agent answers with and starts a new conversation with it; returns the conversation's
        ID. provider=, model= and the rest are what AgentAnalyzer() takes. Without api_key= (or
        azure_ad_token_provider=) it keeps the key it has when the provider stays the same, and otherwise reads the
        provider's environment variable. The model is made at once, so a missing key, setting or package raises here
        (_Hint, ImportError) and the agent keeps the model it had.
        """
        before = {name: getattr(self, name) for name in _MODEL_SETTINGS}
        self._set_model(provider, model, deployment=deployment, endpoint=endpoint, api_key=api_key,
                        api_version=api_version, token_provider=azure_ad_token_provider, chat_model=chat_model)
        keeps_key = api_key is None and azure_ad_token_provider is None and self.provider == before["provider"]
        if keeps_key:
            self._api_key, self._token_provider = before["_api_key"], before["_token_provider"]
        if self.endpoint != before["endpoint"]:
            self._clients.pop("bedrock-runtime", None)
        try:
            self._chat()
        except Exception:
            for name, value in before.items():
                setattr(self, name, value)
            raise
        self._graph = None
        return self.new_conversation()

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
    def workspace(self) -> Workspace | None:
        """
        The Python workspace the agent runs its code in (python=), or None when it has none.
        """
        python = next((t for t in self.toolkits if isinstance(t, PythonToolkit)), None)
        return python.workspace if python is not None else None

    @property
    def reads(self) -> dict[str, tuple[str, ...]]:
        """
        Each tool's AWS reads, from every toolkit: {'function_health': ('lambda:ListFunctions', ...), ...}.
        """
        return {name: reads for toolkit in self.toolkits for name, reads in toolkit.reads.items()}

    @property
    def model(self) -> str:
        """
        The model that answers: the chat model's own name once it's made (a Bedrock model's resolved ID or inference
        profile), else the model or Azure deployment it was given.
        """
        return model_name(self.chat_model, self.model_id)

    def _chat(self) -> Any:
        """
        The LangChain chat model: chat_model= when given, else one made for its provider and model.
        """
        if self.chat_model is None:
            self.chat_model = self._new_chat_model()
        return self.chat_model

    def _new_chat_model(self) -> Any:
        """
        The chat model for its provider (see pick_provider()): Azure OpenAI, OpenAI, a Bedrock model called through
        the agent's own session, or another provider through LangChain's init_chat_model. _Hint says how to set one up
        when there's none.
        """
        if not self.provider:
            raise _Hint(no_model_hint(self.model_id))
        if self.provider == "azure":
            return azure_chat_model(endpoint=self.endpoint, deployment=self.deployment,
                                    api_version=self.api_version or AZURE_API_VERSION,
                                    api_key=self._api_key, token_provider=self._token_provider,
                                    max_tokens=self.max_tokens)
        if self.provider == "bedrock":
            return bedrock_chat_model(self._bedrock_model(), runtime=self._bedrock_runtime(),
                                      control=self._bedrock_control(), max_tokens=self.max_tokens)
        if self.provider == "openai":
            return openai_chat_model(self.model_id, api_key=self._api_key, endpoint=self.endpoint,
                                     max_tokens=self.max_tokens)
        return langchain_chat_model(self.provider, self.model_id, api_key=self._api_key, endpoint=self.endpoint,
                                    max_tokens=self.max_tokens)

    def _bedrock_runtime(self) -> Any:
        """
        The bedrock-runtime client the Bedrock model calls Converse with, made once from the agent's own session (the
        notebook's credentials, region and retries). The read-only guard is for the tools' clients: Converse only
        generates text. endpoint= is its endpoint URL, for a VPC endpoint without private DNS.
        """
        return self._cached_client("bedrock-runtime", lambda: self.session.client(
            "bedrock-runtime", endpoint_url=self.endpoint or None, config=self._config))

    def _bedrock_control(self) -> Any:
        """
        The bedrock client the region's models and inference profiles are listed with, made once from the agent's own
        session.
        """
        return self._cached_client("bedrock", lambda: self.session.client("bedrock", config=self._config))

    def _inference_profiles(self) -> list[dict[str, Any]] | None:
        """
        The inference profiles in the agent's region (ListInferenceProfiles); None when they can't be listed.
        """
        profiles: list[dict[str, Any]] = []
        try:
            for page in self._bedrock_control().get_paginator("list_inference_profiles").paginate():
                profiles.extend(page.get("inferenceProfileSummaries", []))
        except (ClientError, BotoCoreError):
            return None
        return profiles

    def _bedrock_models(self) -> list[ModelInfo]:
        """
        The text models Bedrock offers in the agent's region and how to call each, on demand or through an inference
        profile (ListFoundationModels and ListInferenceProfiles). Without the profiles, a model that needs one can't
        be picked by name.
        """
        bedrock = self._bedrock_control()
        summaries = bedrock.list_foundation_models(byOutputModality="TEXT").get("modelSummaries", [])
        return parse_models(summaries, self._inference_profiles(), bedrock.meta.region_name)

    def _bedrock_model(self) -> str:
        """
        The ID or inference profile the Bedrock model is called with: an ARN or a cross-region profile as given, else
        what bedrock_model_id() finds in the region's model list for the model it was given (a short name like
        'sonnet', a family, or a model ID served through a profile). When the list can't be read, a model ID with its
        version is called as given, and a short name gets a _Hint saying what to pass instead.
        """
        wanted = self.model_id
        if is_bedrock_profile(wanted):
            return wanted
        try:
            models = self._bedrock_models()
        except (ClientError, BotoCoreError) as exc:
            if is_bedrock_version(wanted):
                return wanted
            raise _Hint(f"Couldn't list Amazon Bedrock's models to find the one {wanted!r} names "
                        f"({_error_name(exc)}). Pass its model ID or inference profile instead, like "
                        "AgentView(provider='bedrock', model='us.anthropic.claude-haiku-4-5-20251001-v1:0'), or allow "
                        "bedrock:ListFoundationModels and bedrock:ListInferenceProfiles.") from exc
        return bedrock_model_id(wanted, models)

    def instructions(self) -> str:
        """
        The system prompt the agent starts every conversation with (system_prompt() for its toolkits).
        """
        return system_prompt(self.default_regions(), notes=self.notes, budget=self.budget, toolkits=self.toolkits)

    def _harness(self) -> Any:
        """
        The deepagents agent: the model, the tools, the instructions and a memory of the conversation, with its file
        tools on the Python workspace when there is one. Made once; new_conversation() starts a new thread in it. Before anything else is made, check_ready() says what's missing.
        """
        if self._graph is None:
            harness.check_ready()
            prompt = self.instructions()
            workspace = self.workspace
            if workspace is None:
                self._graph = harness.build_graph(self._chat(), self.tools(), prompt)
            else:
                backend = workspace_backend(workspace, lambda: self.budget)
                self._graph = harness.build_graph(self._chat(), self.tools(), prompt, backend=backend,
                                                  current_run=self._current_run)
        return self._graph

    def new_conversation(self) -> str:
        """
        Starts a new conversation: the next question won't see the earlier ones. Returns its ID.
        """
        self.thread = _new_thread()
        return self.thread

    def ask(  # noqa: PLR0913
        self,
        question: str,
        *,
        new: bool = False,
        stop: threading.Event | None = None,
        progress: Callable[..., None] | None = None,
        on_step: Callable[[AgentStep], None] | None = None,
        now: datetime | None = None,
    ) -> AgentAnswer:
        """
        Answers a question: the model calls the tools until it can answer, within the budget. Follow-up questions
        continue the conversation (new=True starts a new one). Set `stop` (a threading.Event) to stop it early: the
        tools stop, and the model writes its answer from what it read until then. Interrupting the kernel stops it at
        once, with what it read but no answer written. progress(count) is told each
        step, and on_step(AgentStep) what the agent does as it does it (its words, its plan, each tool call as it
        starts and ends), from the threads the tools run on. A model or loop that fails gives an answer with .error
        set (and .exception) instead of raising.
        """
        question = str(question or "").strip()
        if not question:
            raise ValueError("ask() needs a question, like ask('Which Lambda failed last night?')")
        if new:
            self.new_conversation()
        graph = self._harness()
        moment = now or _utcnow()
        run = _Run(self.budget, stop=stop or threading.Event(), listener=on_step)
        answer = AgentAnswer(question=question, model=self.model, provider=self.provider, thread=self.thread,
                             asked=moment)
        self._guard.reset()
        self._run = run
        try:
            answer.text = harness.stream_turn(graph, f"{asked_at(moment, self.zone)}\n\n{question}",
                                              thread=self.thread, run=run, answer=answer, model_prices=self.prices,
                                              progress=progress, provider=self.provider)
        finally:
            self._run = None
        answer.calls = list(run.calls)
        answer.charts = list(run.charts)
        answer.aws_calls = dict(self._guard.counts)
        answer.blocked = list(self._guard.blocked)
        answer.metrics_read = run.metrics_read
        answer.aws_cost = run.aws_cost + sum(t.request_cost(answer.aws_calls) for t in self.toolkits)
        answer.rates = model_rates(answer.model, self.prices, self.provider)
        answer.cost = answer_cost(answer.model, answer.input_tokens, answer.output_tokens, self.prices,
                                  provider=self.provider)
        answer.seconds = run.elapsed
        if answer.stopped is None:
            refused = [c.refused for c in answer.calls if c.refused in ("tool_calls", "time", "cost")]
            answer.stopped = refused[0] if refused else None
        self.history.append(answer)
        return answer

    def save_charts(self, folder: str | os.PathLike = CHARTS_FOLDER, *, answer: int = -1) -> list[Path]:
        """
        Saves the charts of an answer (the last one; answer= picks another from history) as files in `folder`
        ('agent-charts', beside the notebook; made when it isn't there), each named after its title, and returns
        their paths. A file that's there already is never replaced: the new one gets a number.
        """
        if not self.history:
            raise _Hint("Nothing asked yet: ask a question that needs a chart first, like ask('Chart the errors per "
                        "day of each function this week').")
        try:
            picked = self.history[answer]
        except IndexError:
            raise _Hint(f"There's no answer {answer}: {len(self.history)} so far (answer=-1 is the last)") from None
        if not picked.charts:
            raise _Hint(f"The answer to {picked.question!r} has no charts. Ask for one (it needs a Python workspace: "
                        "AgentView(python='local')).")
        return write_charts(picked.charts, folder)

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
