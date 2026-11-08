"""
AgentAnalyzer: the agent's logic layer. It holds the settings and puts the parts together (the toolkits and their
tools, the read-only guard, the budget, the model and the deepagents loop) and answers questions as AgentAnswer data.
It never prints; AgentView shows what it returns.
"""

from __future__ import annotations

import difflib
import json
import os
import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable

from botocore.exceptions import BotoCoreError, ClientError

from .._bedrock.foundation_models import parse_models
from .._bedrock.models import ModelInfo
from .._kit.analyzer import _BaseAnalyzer
from .._kit.errors import _error_name, _Hint
from .._kit.fmt import _display_zone, _utcnow, _zone_from
from .._kit.focus import Focus
from . import harness
from .answers import AgentAnswer, AgentStep, ToolCall, check_findings, choice_of
from .budget import Budget, _Run
from .grounding import Recheck, check_grounding, recheck_request, recheck_said
from .chats import (
    ChatStore,
    ChatSummary,
    chat_title,
    chats_path,
    clean_title,
    find_chats,
    no_chats_hint,
    open_checkpointer,
)
from .workspace.charts import CHARTS_FOLDER, write_charts
from .guard import _Guard
from .memory import (
    UNKNOWN_ACCOUNT,
    MemoryState,
    Note,
    drop_notes,
    is_notes_file,
    memory_backend,
    memory_path,
    notes_text,
    open_store,
    parse_notes,
    remove_note,
)
from .mcp_servers import (
    EditorConfig,
    McpServer,
    ServerState,
    editor_configs,
    parse_server_text,
    parse_settings_text,
    read_servers,
)
from .mcp_servers.config import unique_name
from .llm import (
    AZURE_API_VERSION,
    DEFAULT_TEMPERATURE,
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
    temperature_for,
)
from .prompt import asked_at, focus_of, looking_at, system_prompt
from .runbooks import runbook_sources
from .runtime import as_tool
from .toolkits import DEFAULT_TOOLKITS, TOOLKITS, PythonToolkit, RunbookToolkit, Toolkit, make_toolkits
from .toolkits.mcp_tools import McpToolkit, mcp_toolkit
from .workspace import Workspace, workspace_of
from .workspace.files import workspace_backend

if TYPE_CHECKING:
    from ..bedrock_kb import BedrockKBAnalyzer
    from ..lambda_functions import LambdaAnalyzer
    from ..s3 import S3Analyzer


_THREAD_ID_LENGTH = 12
_WORKERS = 8
_MODEL_SETTINGS = ("provider", "model_id", "deployment", "endpoint", "api_version", "_api_key", "_token_provider",
                   "chat_model", "given_chat_model")


@dataclass
class _Paused:
    """
    A question waiting for the user's choice on a big read: its state, its answer so far, and each pause's ID ->
    the key of the call that asked (what resume() answers, in the order they asked).
    """

    run: _Run
    answer: AgentAnswer
    pauses: dict[str, str] = field(default_factory=dict)


def _interrupting(interrupt_on: bool | str | Iterable[str] | None, toolkits: list[Toolkit]) -> frozenset[str]:
    """
    The tools that ask before a big read, from interrupt_on=: True for every tool that can ask (each toolkit's
    `asks`), False or None for none, or tool names ('find_files', ...). ValueError for a name no toolkit asks with.
    """
    can_ask = {name for toolkit in toolkits for name in toolkit.asks}
    if interrupt_on is True:
        return frozenset(can_ask)
    if not interrupt_on:
        return frozenset()
    names = [interrupt_on] if isinstance(interrupt_on, str) else list(interrupt_on)
    unknown = sorted(set(names) - can_ask)
    if unknown:
        raise ValueError(f"interrupt_on= names {unknown[0]!r}, which never asks first. The tools that can are "
                         f"{', '.join(sorted(can_ask)) or 'none'} (interrupt_on=True for all of them)")
    return frozenset(names)


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


def _toolkit_settings(cores: dict[str, Any] | None, *, lambda_core: Any, lambda_clients: dict[str, Any] | None,  # noqa: PLR0913
                      prices: dict[str, float] | None, s3_core: Any, read_files: bool, kb_core: Any,
                      workspace: Workspace | None) -> dict[str, dict[str, Any]]:
    """
    Each built-in toolkit's own settings, by its name: cores= ({'stepfunctions': StepFunctionsAnalyzer(...)}) gives
    any toolkit the analyzer to read through, and lambda_core=, s3_core= and kb_core= are the same for those three.
    A name cores= has that isn't a toolkit is a ValueError naming the ones there are.
    """
    given = dict(cores or {})
    unknown = sorted(set(given) - set(TOOLKITS))
    if unknown:
        close = difflib.get_close_matches(unknown[0], TOOLKITS, n=1)
        hint = f" Did you mean {close[0]!r}?" if close else ""
        raise ValueError(f"cores= has no toolkit {unknown[0]!r}.{hint} It takes {', '.join(sorted(TOOLKITS))}")
    settings: dict[str, dict[str, Any]] = {name: {"analyzer": core} for name, core in given.items()
                                           if name != PythonToolkit.name}
    settings["lambda"] = {"analyzer": lambda_core or given.get("lambda"), "clients": lambda_clients, "prices": prices}
    settings["s3"] = {"analyzer": s3_core or given.get("s3"), "read_files": read_files}
    settings["bedrock_kb"] = {"analyzer": kb_core or given.get("bedrock_kb")}
    settings["python"] = {"workspace": workspace}
    return settings


def _settings_of(value: dict[str, str] | str) -> dict[str, str]:
    """
    env= or headers= as a dict: given as one, or as 'NAME=value' / 'Header: value' lines.
    """
    return parse_settings_text(value) if isinstance(value, str) else dict(value)


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
    10.00)} (USD per 1M input and output tokens, then optionally per 1M tokens read from the prompt cache and written
    to it: (2.50, 10.00, 1.25)) puts a price on each answer, over Bedrock's list prices; without either the cost is
    unknown and only the tokens are counted. On Bedrock, Claude and Amazon Nova keep the rules and the tools'
    descriptions in Bedrock's prompt cache for five minutes, so a follow-up reads them at a tenth (Nova: a quarter)
    of the input price; OpenAI caches by itself. max_tokens caps each of the model's replies.
    temperature (0 by default) makes the model pick its likeliest words, so the same question over the same data
    gets the same answer; None leaves the provider's default, and a model that takes only its own (OpenAI's o-series
    and GPT-5, told by name) is sent none. A chat_model= passed in keeps its own settings.
    use_model() switches to another model later (the chat window's ⚙ model does it by clicking).

    What it reads: toolkits= picks the services it has tools for (names like 'lambda' and 's3', or Toolkit objects;
    the default is DEFAULT_TOOLKITS: Lambda, S3, Bedrock knowledge bases, Step Functions, API Gateway, SageMaker,
    DynamoDB and OpenSearch, with s3_triggers joining the first two). The Lambda toolkit reads through LambdaAnalyzer: lambda_core= to pass your own, lambda_clients= its clients (for tests), prices= its prices. The
    S3 toolkit reads through S3Analyzer: s3_core= to pass your own (with its prices=). It never reads what's inside a
    file unless read_files=True, which adds preview_file: a file's first rows, lines or text, with anything that
    looks like a secret hidden, sent to the model's provider (budget's file_bytes and file_chars cap it).
    The Bedrock knowledge base toolkit ('bedrock_kb') reads through BedrockKBAnalyzer in the session's region (kb_core= to pass your own): health, syncs, files, Retrieve searches and RetrieveAndGenerate
    answers, whose passages and answers go to the model's provider (budget's kb_searches caps them). The Step
    Functions ('stepfunctions'), API Gateway ('apigateway'), SageMaker ('sagemaker'), DynamoDB ('dynamodb') and
    OpenSearch ('opensearch') toolkits read through those services' analyzers in the session's region; a failed
    step's or call's Lambda run is read through the Lambda toolkit's LambdaAnalyzer, so the Lambda tools go on from
    it. DynamoDB's table_shape scans a sample of items (budget's dynamodb_items caps it) and sends the model only
    their shape. cores= passes your own analyzer to any toolkit by its name ({'stepfunctions': core}). python= takes a
    Workspace (aws_analyzer.agent.Workspace: a sandbox with no AWS credentials or network) and adds the Python toolkit:
    run_python runs code the model writes over files the other toolkits save there (Lambda's save_function_code),
    and what it prints goes to the model, secrets hidden (budget's python_runs, python_seconds and workspace_bytes
    cap it). It looks in `region` (the
    session's) or `regions` (a list, or 'all'); buckets are read wherever they are. The user's time zone, so "last night" means their night, is set_timezone()'s (or
    AWS_ANALYZER_TZ's), the zone every report shows times in; `timezone` ('Asia/Kolkata', 'kolkata', '+05:30') sets
    one for the agent alone. `notes` are facts about the account the agent is told ("orders-etl runs at 02:00
    IST"), or a file of them (notes='AGENTS.md', read again for each new conversation). memory=True lets the agent
    keep its own notes between conversations, like "orders-etl runs in eu-west-1", in a SQLite file
    (~/.cache/aws-analyzer/agent/memory.sqlite, or the path memory= gives), one set per AWS account: memories()
    shows them and forget() clears them. chats=True saves every conversation on this machine, titled from its first
    question (~/.cache/aws-analyzer/agent/chats.sqlite, or the path chats= gives): chats() lists them, open_chat()
    carries one on, even after the kernel restarts, and rename_chat() gives one a title of your own. `budget` caps each question (Budget, or a dict of its fields). For a question of several steps the agent
writes a short plan first and ticks it off as it works (answer.plan, a checklist in the chat window and the report);
plan=False leaves the plan out, which saves its instructions' tokens on every model call. It ends each question by
filling in a set shape (findings=True, the default): the answer, and what it found that needs your attention
(answer.findings: warn or info, what's wrong and what to do, and the View call that shows more, kept only when it's
a real command with arguments it takes), which the report shows as its findings and next calls. findings=False
has it answer in plain text, as does a model that can't be made to call a tool.

    Checked before it's shown (verify=True, the default): once the model has answered, its draft is compared with
    what the tools returned (check_grounding: names, IDs and numbers no tool gave, regions it didn't read, "no
    errors" over a read that failed or was cut short). When part of it doesn't check out, the model gets one more
    turn, with its tools, to read what it needs, take the claim out or say it couldn't check it, and that answer is
    shown instead (answer.recheck says what was flagged, keeps the draft, and whether the answer now checks out).
    It costs a model call only when the draft didn't check out. verify=False shows the draft as it is, with the same
    warnings.

    Runbooks: step-by-step guides for common problems ("a Lambda is failing", "an S3 upload didn't start my
    function"), each a folder with a SKILL.md. The model sees only each one's name and one-line description, and reads
    the whole guide (read_runbook) when a question needs it. runbooks=True (the default) gives it the built-in ones; a
    folder of your own, or a list of them, adds yours (a runbook of yours with a built-in one's name replaces it), and
    False gives it none. runbooks() lists what it found.

    Big reads: before a read that would take a long time or cost money (an S3 listing past the budget's ask_keys, a
    log read longer than log_days, a save into the workspace past ask_bytes, or a save that finds more files than
    the model asked for) the agent pauses and asks: ask() returns the answer with .waiting set (each call's .waiting
    says what it would read), and resume('go' | 'smaller' | 'skip') goes on. interrupt_on= picks the tools that ask:
    True (the default) for every one that can, a list of tool names, or False to never ask (listings stop at s3_keys
    and long log reads are refused, as before). It's about time and cost: the agent only reads, whichever you pick.

    Used-up limits: when a question runs out of a limit of its budget (tool calls, time, model cost, Python runs,
    knowledge base searches, charts, the workspace's size), the tool that hit it pauses the same way and asks
    whether to raise it: go raises it for this question (twice as much, or what the save needs) and the agent goes
    on; smaller or skip has it answer with what it found. ask_more=False never asks (the limit stops the agent, as
    before); by default it asks whenever interrupt_on asks about anything.

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
        model_prices: dict[str, tuple[float, ...]] | None = None,
        timezone: str | tzinfo | None = None,
        regions: Any = None,
        notes: str | os.PathLike = "",
        memory: bool | str | os.PathLike = False,
        chats: bool | str | os.PathLike = False,
        budget: Budget | dict[str, Any] | None = None,
        max_tokens: int | None = 4096,
        temperature: float | None = DEFAULT_TEMPERATURE,
        toolkits: Iterable[str | Toolkit | type[Toolkit]] | None = None,
        lambda_core: LambdaAnalyzer | None = None,
        lambda_clients: dict[str, Any] | None = None,
        prices: dict[str, float] | None = None,
        s3_core: S3Analyzer | None = None,
        read_files: bool = False,
        kb_core: BedrockKBAnalyzer | None = None,
        cores: dict[str, Any] | None = None,
        python: Workspace | None = None,
        plan: bool = True,
        runbooks: bool | str | Path | Iterable[str | Path] = True,
        mcp: bool | str | os.PathLike | dict[str, Any] | Iterable[Any] | McpToolkit = True,
        findings: bool = True,
        verify: bool = True,
        interrupt_on: bool | str | Iterable[str] | None = True,
        ask_more: bool | None = None,
    ):
        super().__init__(session, region=region, profile=profile, prices=model_prices)
        self.given_chat_model: Any = None
        self._set_model(provider, model, deployment=deployment, endpoint=endpoint, api_key=api_key,
                        api_version=api_version, token_provider=azure_ad_token_provider, chat_model=chat_model)
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timezone = _checked_zone(timezone)
        self.regions = regions
        self.notes = notes
        self.memory_file = memory_path(memory)
        self._store: Any = None
        self._notes: Any = None
        self._account: str | None = None
        self._saver: Any = None
        self.chats_file = chats_path(chats)
        self._chat_store: ChatStore | None = None
        self._prompt_notes = ""
        self.budget = Budget.of(budget)
        self.plan = plan
        self.findings = findings
        self.verify = verify
        self._clients: dict[Any, Any] = {}
        self._lock = threading.Lock()
        self.max_workers = _WORKERS
        self._guard = _Guard()
        workspace = workspace_of(python)
        self.toolkits: list[Toolkit] = [toolkit.bind(self) for toolkit in make_toolkits(
            _with_python(toolkits, workspace),
            _toolkit_settings(cores, lambda_core=lambda_core, lambda_clients=lambda_clients, prices=prices,
                              s3_core=s3_core, read_files=read_files, kb_core=kb_core, workspace=workspace))]
        mcp_kit = mcp_toolkit(mcp)
        if mcp_kit is not None:
            self.toolkits.append(mcp_kit.bind(self))
        if runbooks is not False and runbooks is not None:
            self.toolkits.append(RunbookToolkit(runbook_sources(runbooks)).bind(self))
        self.interrupt_on = _interrupting(interrupt_on, self.toolkits)
        self.ask_more = bool(self.interrupt_on) if ask_more is None else bool(ask_more)
        self._graph: Any = None
        self._run: _Run | None = None
        self._paused: _Paused | None = None
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
    def runbook_kit(self) -> RunbookToolkit | None:
        """
        The agent's runbooks (the Runbooks toolkit: .shelf is what it found, .offered what it can follow), or None
        with runbooks=False.
        """
        return next((t for t in self.toolkits if isinstance(t, RunbookToolkit)), None)

    @property
    def mcp_kit(self) -> McpToolkit | None:
        """
        The agent's MCP servers (the MCP toolkit: .servers, .states, .path), or None with mcp=False.
        """
        return next((t for t in self.toolkits if isinstance(t, McpToolkit)), None)

    def _mcp(self) -> McpToolkit:
        kit = self.mcp_kit
        if kit is None:
            raise _Hint("MCP servers are off for this agent (mcp=False): AgentView(mcp=True) keeps the ones you add in "
                        "~/.cache/aws-analyzer/agent/mcp.json, and AgentView(mcp='.vscode/mcp.json') uses an editor's.")
        return kit

    def _tools_changed(self) -> None:
        """
        The tools the agent has changed (an MCP server added, removed or switched): its tools and prompt are made
        again before the next question, in the same conversation (the checkpointer stays).
        """
        self._tools = None
        self._graph = None

    def mcp_servers(self, *, connect: bool = True) -> list[ServerState]:
        """
        The agent's MCP servers as they stand (ServerState: connected or why not, its tools, its calls), connecting
        the enabled ones that are off first (connect=False doesn't).
        """
        kit = self._mcp()
        if connect:
            kit.connected()
        return kit.states

    def add_mcp(  # noqa: PLR0913
        self,
        server: str | dict[str, Any] | McpServer,
        *,
        name: str = "",
        env: dict[str, str] | str | None = None,
        headers: dict[str, str] | str | None = None,
        asks: str | None = None,
        replace: bool = False,
        connect: bool = True,
    ) -> list[ServerState]:
        """
        Adds an MCP server, saved in the agent's mcp.json, and connects it (connect=False doesn't): `server` is the
        command that starts it ('npx -y @modelcontextprotocol/server-memory'), its URL ('https://.../mcp'), its JSON
        from an mcp.json (an entry, or a whole file's, which adds each), or an McpServer. name= names it (else it's
        named from what it runs), env= gives a command its environment variables and headers= a URL its HTTP
        headers (a dict, or 'NAME=value' lines; '${env:NAME}' reads the kernel's variable when it starts), asks=
        says when the agent asks before its tools run ('unmarked', the default: unless a tool says it only reads;
        'every'; 'never'). replace=True puts it in place of the server with its name. Returns each server's state:
        one that couldn't connect stays, with its error, to fix or remove.
        """
        kit = self._mcp()
        servers = [server] if isinstance(server, McpServer) else self._parsed_servers(server, name)
        added = []
        for each in servers:
            if env is not None and not each.is_remote:
                each.env = {**each.env, **_settings_of(env)}
            if headers is not None and each.is_remote:
                each.headers = {**each.headers, **_settings_of(headers)}
            if asks is not None:
                each.asks = asks
            each.source = each.source or "saved"
            added.append(kit.add(each, replace=replace).name)
        if connect:
            kit.hub.connect(added)
        return [kit.hub.states[name] for name in added]

    @staticmethod
    def _parsed_servers(server: str | dict[str, Any], name: str) -> list[McpServer]:
        if isinstance(server, dict):
            return parse_server_text(json.dumps(server), name=name)
        return parse_server_text(str(server), name=name)

    def remove_mcp(self, name: str) -> McpServer:
        """
        Removes an MCP server from the agent and its mcp.json (a stdio server's process stops). Returns what it was.
        """
        return self._mcp().remove(name)

    def update_mcp(self, name: str, *, enabled: bool | None = None, asks: str | None = None,
                   tools: dict[str, bool] | None = None) -> ServerState:
        """
        Changes what the agent does with an MCP server, saved in its mcp.json: enabled= turns it on or off, asks=
        when it asks before a tool runs, tools= turns tools on or off by their names ({'delete_file': False}).
        """
        kit = self._mcp()
        server = kit.update(name, enabled=enabled, asks=asks, tools=tools)
        if server.enabled:
            kit.hub.connect([server.name])
        return kit.hub.states[server.name]

    def reconnect_mcp(self, name: str | None = None) -> list[ServerState]:
        """
        Connects an MCP server again (every enabled one without a name): after fixing what made it fail, or to see
        tools it added. The agent's tools are made again before the next question.
        """
        kit = self._mcp()
        names = None if name is None else [kit.find(name).name]
        states = kit.hub.connect(names, again=True)
        self._tools_changed()
        return states

    def mcp_editor_files(self) -> list[EditorConfig]:
        """
        The mcp.json files editors and apps keep beside this notebook and in the home folder (VS Code's, Claude
        Code's, Cursor's, Claude Desktop's), with the servers each holds, for import_mcp().
        """
        return editor_configs(skip=self._mcp().path)

    def import_mcp(self, path: str | os.PathLike, *, connect: bool = True) -> list[ServerState]:
        """
        Adds the servers of another app's mcp.json (VS Code's .vscode/mcp.json, Claude Desktop's, ...) to the
        agent's own, leaving that file as it is. A server it has already, started the same way, is left out; one
        whose name is taken gets a number ('github-2').
        """
        kit = self._mcp()
        source = Path(path).expanduser()
        servers, problem = read_servers(source, source=str(source))
        if problem:
            raise _Hint(problem)
        added = []
        for server in servers:
            if any(server.same_place(mine) for mine in kit.servers):
                continue
            server.name = unique_name(server.name, [mine.name for mine in kit.servers])
            added.append(kit.add(server).name)
        if connect and added:
            kit.hub.connect(added)
        return [kit.hub.states[name] for name in added]

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
                                    max_tokens=self.max_tokens, temperature=self._temperature(self.deployment))
        if self.provider == "bedrock":
            model_id = self._bedrock_model()
            return bedrock_chat_model(model_id, runtime=self._bedrock_runtime(), control=self._bedrock_control(),
                                      max_tokens=self.max_tokens, temperature=self._temperature(model_id))
        if self.provider == "openai":
            return openai_chat_model(self.model_id, api_key=self._api_key, endpoint=self.endpoint,
                                     max_tokens=self.max_tokens, temperature=self._temperature(self.model_id))
        return langchain_chat_model(self.provider, self.model_id, api_key=self._api_key, endpoint=self.endpoint,
                                    max_tokens=self.max_tokens, temperature=self._temperature(self.model_id))

    def _temperature(self, model: str | None) -> float | None:
        """
        The temperature this model is sent: the agent's, or None for one that takes only its own (temperature_for).
        """
        return temperature_for(model or self.model_id, self.temperature)

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
        return system_prompt(self.default_regions(), notes=notes_text(self.notes), budget=self.budget,
                             toolkits=self.toolkits, findings=self.findings)

    def _harness(self) -> Any:
        """
        The deepagents agent: the model, the tools, the instructions and a memory of the conversation, with its file
        tools on the Python workspace when there is one, its notes between conversations with memory=, and its plan
        unless plan=False. Made once, and again when the file notes= names has changed; new_conversation() starts a
        new thread in it, and every conversation is kept across a rebuild. Before anything else is made,
        check_ready() says what's missing.
        """
        if self._graph is None:
            harness.check_ready()
            self._prompt_notes = notes_text(self.notes)
            prompt = self.instructions()
            workspace = self.workspace
            backend = workspace_backend(workspace, lambda: self._current_run().budget) if workspace is not None else None
            if self._saver is None:
                self._saver = self._new_checkpointer()
            self._graph = harness.build_graph(self._chat(), self.tools(), prompt, backend=backend,
                                              notes=self._memory(), current_run=self._current_run, plan=self.plan,
                                              checkpointer=self._saver, reply=self.findings)
        return self._graph

    def _new_checkpointer(self) -> Any:
        """
        Where the conversations are kept for the model: in the chats' file with chats=, so a chat opened again
        carries on; in memory otherwise.
        """
        if self.chats_file is not None:
            return open_checkpointer(self.chats_file)
        return harness.new_checkpointer()

    def _view_commands(self) -> dict[str, dict[str, Callable[..., Any]]]:
        """
        The calls a finding can name: each toolkit's View -> its commands (Toolkit.view_commands()).
        """
        return {toolkit.view: toolkit.view_commands() for toolkit in self.toolkits if toolkit.view}

    def account(self) -> str:
        """
        The AWS account the agent's session is in (STS GetCallerIdentity, read once), which its notes are kept under;
        'unknown' when it can't be read.
        """
        if self._account is None:
            try:
                sts = self._cached_client("sts", lambda: self.session.client("sts", config=self._config))
                self._account = str(sts.get_caller_identity()["Account"])
            except (ClientError, BotoCoreError, KeyError):
                self._account = UNKNOWN_ACCOUNT
        return self._account

    def _today(self) -> Any:
        """
        The user's date (in the agent's time zone), which a new note is dated with.
        """
        return _utcnow().astimezone(self.zone).date()

    def _memory(self) -> Any:
        """
        The agent's notes about its account as a deepagents backend (memory.py's), made once; None when memory is off.
        """
        if self.memory_file is None:
            return None
        if self._notes is None:
            if self._store is None:
                self._store = open_store(self.memory_file)
            self._notes = memory_backend(self._store, self.account(), lambda: self.budget, self._today)
        return self._notes

    def _memory_or_hint(self) -> Any:
        """
        The agent's notes backend, or _Hint saying how to turn memory on.
        """
        notes = self._memory()
        if notes is None:
            raise _Hint("The agent's memory is off: AgentView(memory=True) lets it keep notes about your account "
                        "between conversations, in ~/.cache/aws-analyzer/agent/memory.sqlite.")
        return notes

    def memories(self) -> MemoryState:
        """
        What the agent remembers about its account: each note it wrote (its text and the day it learned it), the
        file they're in, where it's stored and how much it may hold. _Hint when memory is off.
        """
        text = self._memory_or_hint().notes_text()
        return MemoryState(parse_notes(text), text, self.account(), self.memory_file, self.budget.memory_bytes)

    def forget(self, match: str) -> list[Note]:
        """
        Clears the agent's notes whose text holds `match` (any case: forget('orders-etl')), and returns them. The
        next conversation starts without them.
        """
        if not str(match or "").strip():
            raise ValueError("forget() needs a few words of the note to clear, like forget('orders-etl'); "
                             "forget_all() clears every note")
        notes = self._memory_or_hint()
        kept, dropped = drop_notes(notes.notes_text(), str(match))
        if dropped:
            notes.keep(kept)
        return dropped

    def forget_note(self, note: Note) -> bool:
        """
        Clears one note, exactly as memories() returned it (the chat window's ✕), and says whether it was there.
        """
        notes = self._memory_or_hint()
        kept, removed = remove_note(notes.notes_text(), note)
        if removed:
            notes.keep(kept)
        return removed

    def forget_all(self) -> int:
        """
        Clears every note the agent kept about its account, and returns how many there were.
        """
        notes = self._memory_or_hint()
        count = len(parse_notes(notes.notes_text()))
        notes.clear()
        return count

    def new_conversation(self) -> str:
        """
        Starts a new conversation: the next question won't see the earlier ones, and a question waiting for your
        choice on a big read is dropped. Returns its ID.
        """
        self.thread = _new_thread()
        self._paused = None
        if is_notes_file(self.notes) and notes_text(self.notes) != self._prompt_notes:
            self._graph = None
        return self.thread

    def _chats(self) -> ChatStore:
        """
        The saved chats' file, opened once; _Hint when chats aren't saved (chats=False).
        """
        if self.chats_file is None:
            raise no_chats_hint()
        with self._lock:
            if self._chat_store is None:
                self._chat_store = ChatStore(self.chats_file)
        return self._chat_store

    def chats(self) -> list[ChatSummary]:
        """
        Every saved chat (chats=True), the one asked in last first: its ID, title, when it started and was last asked
        in, how many questions it holds and what they cost.
        """
        return self._chats().chats()

    def find_chat(self, match: str | int) -> ChatSummary:
        """
        The saved chat `match` names: a number as chats() lists them (1 is the newest), its ID, or words of its title.
        _Hint when none does, or several do.
        """
        saved = self.chats()
        found = find_chats(saved, match)
        if not found:
            listed = "; ".join(f"{i}. {chat.title}" for i, chat in enumerate(saved[:3], 1)) or "none saved yet"
            raise _Hint(f"No saved chat matches {str(match)!r}. The newest: {listed} (chats() lists them all).")
        if len(found) > 1:
            names = "; ".join(repr(chat.title) for chat in found[:3])
            raise _Hint(f"{len(found)} chats match {str(match)!r} ({names}): name one by more of its title, or by "
                        "its number in chats().")
        return found[0]

    @property
    def chat_title(self) -> str:
        """
        The title of the conversation going on: its saved title with chats=True, else its first question's; '' before
        anything is asked in it.
        """
        if self.chats_file is not None:
            saved = self._chats().chat(self.thread)
            if saved is not None:
                return saved.title
        asked = [answer for answer in self.history if answer.thread == self.thread]
        return chat_title(asked[0].question) if asked else ""

    def open_chat(self, match: str | int) -> ChatSummary:
        """
        Carries on a saved chat (find_chat() says which): the next question follows its last, and its questions join
        `history`. A question waiting for your choice on a big read is dropped. Returns the chat.
        """
        chat = self.find_chat(match)
        answers = self._chats().answers(chat.thread)
        self.thread = chat.thread
        self._paused = None
        if not any(answer.thread == chat.thread for answer in self.history):
            self.history.extend(answers)
        return chat

    def rename_chat(self, title: str, chat: str | int | None = None) -> ChatSummary:
        """
        Gives a saved chat a title of your own: the one going on unless `chat` names another (as find_chat() takes).
        Returns it renamed.
        """
        title = clean_title(title)
        store = self._chats()
        saved = self.find_chat(chat) if chat is not None else store.chat(self.thread)
        if saved is None:
            raise _Hint("Nothing is saved in this chat yet: ask its first question, then rename it.")
        store.rename(saved.thread, title)
        return replace(saved, title=title)

    def delete_chat(self, match: str | int) -> ChatSummary:
        """
        Removes a saved chat from the file (find_chat() says which): its answers, and the conversation the model
        would carry on; removing the chat going on starts a new conversation. Returns what was removed.
        """
        chat = self.find_chat(match)
        self._chats().delete(chat.thread)
        if self._saver is None:
            self._saver = self._new_checkpointer()
        self._saver.delete_thread(chat.thread)
        if chat.thread == self.thread:
            self.new_conversation()
        return chat

    def interrupts(self, tool: str) -> bool:
        """
        Whether a tool asks the user before a big read (interrupt_on=).
        """
        return tool in self.interrupt_on

    @property
    def waiting(self) -> list[ToolCall]:
        """
        The tool calls waiting for your choice on a big read (resume() goes on), in the order they asked; empty when
        nothing waits.
        """
        return self._paused.answer.waiting if self._paused is not None else []

    def ask(  # noqa: PLR0913
        self,
        question: str,
        *,
        new: bool = False,
        focus: Any = None,
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
        set (and .exception) instead of raising. Before a big read (interrupt_on=) it pauses: the answer comes back
        with .waiting set and no text yet, and resume() goes on with your choice. focus= is what you're looking at in
        an explorer window (the window itself, x = LambdaView().explore(), or its x.focus): the model is told what it
        names (the function, tab, time range, run or file), so "why does this fail?" starts from there, and the
        answer keeps it as .focus.
        """
        question = str(question or "").strip()
        if not question:
            raise ValueError("ask() needs a question, like ask('Which Lambda failed last night?')")
        if new:
            self.new_conversation()
        if self._paused is not None:
            waiting = self.waiting[0]
            raise _Hint(f"The agent is waiting for your choice on {waiting.call}: resume('go'), resume('smaller') "
                        "or resume('skip') first, or ask(..., new=True) to drop that question.")
        shown = focus_of(focus)
        graph = self._harness()
        moment = now or _utcnow()
        run = _Run(self.budget, stop=stop or threading.Event(), listener=on_step, ask_more=self.ask_more,
                   first_ref=self._next_ref(self.thread))
        answer = AgentAnswer(question=question, model=self.model, provider=self.provider, thread=self.thread,
                             asked=moment, focus=shown)
        self._guard.reset()
        self._run = run
        try:
            answer.text = harness.stream_turn(graph, self._message(question, moment, shown),
                                              thread=self.thread, run=run, answer=answer, model_prices=self.prices,
                                              progress=progress, provider=self.provider)
            self._recheck(graph, run, answer, progress)
        finally:
            self._run = None
        self.history.append(answer)
        return self._finish(graph, run, answer)

    def _message(self, question: str, moment: datetime, focus: Focus | None) -> str:
        """
        What the model is sent for a question: when it was asked, what the user has open (when they said), then
        the question.
        """
        lines = [asked_at(moment, self.zone)]
        if focus is not None:
            lines.append(looking_at(focus))
        return "\n".join(lines) + f"\n\n{question}"

    def resume(  # noqa: PLR0913
        self,
        choice: str,
        *,
        stop: threading.Event | None = None,
        progress: Callable[..., None] | None = None,
        on_step: Callable[[AgentStep], None] | None = None,
    ) -> AgentAnswer:
        """
        Goes on with the question waiting for your choice on a big read, and returns its answer: 'go' reads it all,
        'smaller' reads the smaller part its question names, 'skip' answers without it. When several reads wait (the
        model asked for them side by side), each resume() answers the next one, and the question goes on once all are
        answered. stop, progress and on_step are what ask() takes.
        """
        paused = self._paused
        if paused is None:
            raise _Hint("Nothing is waiting for your choice: resume() goes on after a question pauses before a big "
                        "read.")
        picked = choice_of(choice)
        paused.answer.waiting[0].waiting.choice = picked
        if paused.answer.waiting:
            return paused.answer
        choices = {pause: self._choice(paused.answer, key) for pause, key in paused.pauses.items()}
        self._paused = None
        run, answer = paused.run, paused.answer
        run.stop = stop or threading.Event()
        run.listener = on_step
        run.go_on()
        graph = self._harness()
        self._run = run
        try:
            answer.text = harness.resume_turn(graph, choices, thread=answer.thread, run=run, answer=answer,
                                              model_prices=self.prices, progress=progress, provider=self.provider)
            self._recheck(graph, run, answer, progress)
        finally:
            self._run = None
        return self._finish(graph, run, answer)

    @staticmethod
    def _choice(answer: AgentAnswer, key: str) -> str:
        """
        The choice made on the last ask of the call whose key is `key` ('skip' when the call isn't found).
        """
        call = next((c for c in answer.calls if c.key == key and c.asks), None)
        return (call.asks[-1].choice or "skip") if call is not None else "skip"

    def earlier(self, answer: AgentAnswer) -> list[AgentAnswer]:
        """
        The answers asked before `answer` in its conversation, oldest first: what a follow-up may repeat and cite.
        """
        found: list[AgentAnswer] = []
        for each in self.history:
            if each is answer:
                break
            if each.thread == answer.thread:
                found.append(each)
        return found

    def _next_ref(self, thread: str) -> int:
        """
        The ref the next question's first tool call gets in a conversation: one past the calls asked before it, so
        the answer can cite an earlier question's calls by theirs.
        """
        return 1 + sum(len(each.calls) for each in self.history if each.thread == thread)

    def _can_recheck(self, run: _Run, answer: AgentAnswer) -> bool:
        """
        Whether a draft can be checked and corrected: verify is on, it's the question's first check, the model
        answered (no error, no stop, nothing waiting for the user), and the budget has time, cost and a tool call
        left for the correction.
        """
        budget = run.budget
        is_answered = answer.error is None and answer.stopped is None and bool(answer.text.strip())
        is_waiting = any(call.waiting is not None for call in run.calls)
        used = sum(1 for call in run.calls if call.refused is None)
        has_cost_left = budget.usd is None or run.cost < budget.usd
        has_room = run.elapsed < budget.seconds and used < budget.tool_calls and has_cost_left
        return self.verify and answer.recheck is None and is_answered and not is_waiting and has_room

    def _recheck(self, graph: Any, run: _Run, answer: AgentAnswer, progress: Callable[..., None] | None) -> None:
        """
        With verify=True, checks the model's draft against what its tools returned (check_grounding) and, when part
        of it doesn't check out, gives the model one more turn in the same conversation, with its tools, to correct
        it: that answer replaces the draft, and answer.recheck keeps what was flagged and the draft. A turn that
        fails or ends without an answer leaves the draft as it was, with why in recheck.failed; one that pauses to
        ask about a big read leaves no text, as any pause does.
        """
        if not self._can_recheck(run, answer):
            return
        answer.calls = list(run.calls)
        flagged = check_grounding(answer, self.earlier(answer))
        if flagged.ok:
            return
        draft_text, draft_findings, calls_before = answer.text, list(answer.findings), len(run.calls)
        answer.recheck = recheck = Recheck.of(flagged, draft_text)
        run.tell(AgentStep("thinking", text=recheck_said(flagged)))
        text = harness.stream_turn(graph, recheck_request(flagged, reply=self.findings), thread=answer.thread,
                                   run=run, answer=answer, model_prices=self.prices, progress=progress,
                                   provider=self.provider)
        recheck.tool_calls = len(run.calls) - calls_before
        if text.strip():
            answer.text = text
            return
        if any(call.waiting is not None for call in run.calls):
            answer.text = ""
            return
        recheck.failed = answer.error or "the model ended its turn without an answer"
        answer.text, answer.findings = draft_text, draft_findings
        if answer.stopped in ("error", "steps"):
            answer.stopped, answer.error, answer.exception = None, None, None

    def _finish(self, graph: Any, run: _Run, answer: AgentAnswer) -> AgentAnswer:
        """
        Fills in the answer from what the question's run, the guard and the model counted, and keeps it as paused
        when a tool is waiting for the user's choice.
        """
        answer.calls = list(run.calls)
        check_findings(answer.findings, self._view_commands())
        answer.plan = list(run.plan)
        answer.charts = list(run.charts)
        answer.aws_calls = dict(self._guard.counts)
        answer.blocked = list(self._guard.blocked)
        answer.metrics_read = run.metrics_read
        answer.aws_cost = run.aws_cost + sum(t.request_cost(answer.aws_calls) for t in self.toolkits)
        answer.rates = model_rates(answer.model, self.prices, self.provider)
        answer.cost = answer_cost(answer.model, answer.input_tokens, answer.output_tokens, self.prices,
                                  provider=self.provider, cache_read=answer.cache_read_tokens,
                                  cache_write=answer.cache_write_tokens)
        answer.seconds = run.elapsed
        if answer.recheck is not None and not answer.recheck.failed and not answer.waiting:
            answer.recheck.fixed = check_grounding(answer, self.earlier(answer)).ok
        pauses = harness.pauses(graph, answer.thread) if answer.waiting and answer.error is None else {}
        if pauses:
            run.pause()
            self._paused = _Paused(run, answer, {pause: str(value.get("call", "")) for pause, value in pauses.items()})
            return answer
        if answer.stopped is None:
            refused = [c.refused for c in answer.calls if c.refused in ("tool_calls", "time", "cost")]
            answer.stopped = refused[0] if refused else None
        if self.chats_file is not None:
            self._chats().save(answer)
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
