"""
BedrockChatView, made of its commands: the plumbing they share and help(), and chat() to open
the window.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING, Any, Iterable

from botocore.exceptions import BotoCoreError, ClientError

from ..._bedrock.filters import describe_sources
from ..._bedrock.helpers import source_name
from ..._bedrock.models import Passage
from ..._bedrock.prices import DEFAULT_MODEL, MODEL_PRICES, short_model
from ..._kit.errors import _Hint
from ..._kit.fmt import _plural, human_money
from ..._kit.view import _BaseView, _which_prices
from ..._kit.window import _cell_number

from ..helpers import DEFAULT_SETTINGS, _diff
from ..models import Answer
from ..analysis.choices import describe_files
from ..analysis.request import coerce_setting, normalize_settings
from ..analysis.findings import answer_cost, answer_findings, cited_ranks, compare_findings
from ..analysis.batches import Batch, _filled, question_list
from ..analysis.ranking import Sweep
from ..analyzer import BedrockChatAnalyzer
from ..style import _render_html
from ..blocks import _score, _search_html, _turn_html
from .chat import _ChatCommands
from .tests import _TestCommands
from .runs import _RunsCommands
from .settings import _SettingsCommands
from .knowledge_bases import _KnowledgeBaseCommands

if TYPE_CHECKING:
    from ..window.app import _ChatApp


class BedrockChatView(_ChatCommands, _TestCommands, _RunsCommands, _SettingsCommands, _KnowledgeBaseCommands,
                      _BaseView):
    """
    The chat window, and reports, over BedrockChatAnalyzer. Each command renders something and returns nothing:
    the conversation's answers are in `view.answers`, the settings in `view.values`, and the analyzer is
    `view.core`.

    kb: the knowledge base (a name, ID or ARN); without it, the only one in the region, or the window's first.
    data_source: ask only this one of its data sources (a name or ID, or a list of them); default all of them.
    files: ask only these files (names, paths in the bucket or s3:// paths); default all of them.
    model: an ID, inference profile, ARN or short name ('opus', 'sonnet', 'haiku', 'nova'...); default DEFAULT_MODEL.
    settings: what's sent with every question, {name: value} (default DEFAULT_SETTINGS); fields() lists the names.
    questions: test questions for ask_all(), sweep() and the window's Test tab: a list, or text with one per line.
    log: a file every test run is added to as it finishes (JSON Lines), so runs outlast a kernel restart:
    load_runs() reads them back. save_runs('file.jsonl') sets it too.
    stream: show answers in the window as they're written. height: the height of the window's conversation. It
    fills the browser window (at least 540 pixels); a number of pixels (800) or CSS ('70vh') sets it instead.
    retrieve_only: the window's Send only searches (Retrieve): every passage found, with no answer. The window's
    Answer / Retrieve only switch changes it; ask() always answers and retrieve() always only searches.
    mode: 'auto' (HTML inside Jupyter, text elsewhere), 'html' or 'text'. max_rows: default cap for long tables
    (0 for no cap). progress: 'auto' (a tqdm bar while a report's question runs, when tqdm is installed; else a line
    with the time), 'plain' (always that line) or 'off'.
    """

    _render_html = staticmethod(_render_html)  # its look: the stylesheet, root class and badge
    _GROUPS = {  # help() lists the commands in these groups, in this order
        "💬 Chat": ("app", "ask", "retrieve", "new_chat", "transcript", "last"),
        "🧪 Test a list of questions": ("ask_all", "sweep", "results"),
        "📈 Keep and compare test runs": ("runs", "compare_runs", "use_run", "save_runs", "load_runs"),
        "⚙️ Settings": ("settings", "set", "unset", "fields", "request", "code"),
        "📚 Knowledge base, files and model": ("use", "kbs", "files", "save_file_list", "models"),
        "❓ Help": ("help",),
    }
    _START = (
        ("app()", "the chat window: pick a model, change settings, see the JSON"),
        ("ask('a question')", "an answer with citations, as a report"),
        ("ask_all(['a question', 'another'])", "a list of test questions, each answered and checked"),
        ("sweep(n=[5, 10], search_type=['SEMANTIC', 'HYBRID'])", "every combination on the test questions, best first"),
        ("code()", "this setup as Python, JSON or an AWS CLI command, to copy"),
    )

    def __init__(
        self,
        core: BedrockChatAnalyzer | None = None,
        *,
        kb: str | None = None,
        model: str | None = None,
        settings: dict[str, Any] | None = None,
        stream: bool = True,
        data_source: Any = None,
        files: Any = None,
        retrieve_only: bool = False,
        questions: Any = None,
        height: int | str | None = None,
        log: str | None = None,
        mode: str = "auto",
        max_rows: int = 50,
        progress: str = "auto",
    ):
        super().__init__(mode=mode, max_rows=max_rows, progress=progress)
        self.core: BedrockChatAnalyzer = core or BedrockChatAnalyzer()
        self.kb = kb  # what kb= named; its ID once resolved
        self.data_source: Any = data_source  # what data_source= named; {ID: name} once resolved ({} = all of them)
        self.picked_files: Any = files  # what files= named; their s3:// paths once resolved ([] = all of them)
        self.model = model  # what model= named; the ID the window picked once it's open
        self.stream = stream
        self.height = height  # the window's conversation (None: it fills the browser window)
        self.retrieve_only = bool(retrieve_only)  # the window's Send only searches; request() shows that request
        self.values: dict[str, Any] = normalize_settings(
            DEFAULT_SETTINGS if settings is None else settings, self.core.schema()
        )
        self.answers: list[Answer] = []  # this conversation, oldest first; retrieve-only searches too
        self.session_id: str | None = None  # Bedrock's session for it, once the first answer came back
        # the test questions (the window's Test tab), and every test run of them, oldest first
        self.questions: list[tuple[str, Any]] = question_list(questions) if _filled(questions) else []
        self.batches: list[Batch] = []
        self.sweeps: list[Sweep] = []  # the sweeps among them, oldest first
        self.log = str(log) if log else None  # the file every finished run is added to (save_runs() sets it)
        self._in_file: dict[str, set[str]] = {}  # file -> the IDs of the runs in it, so none is written twice
        self._app: _ChatApp | None = None
        self._shown_in: Any = None  # the cell that last showed the window
        self._notes: list[str] = []  # problems with chat()'s arguments, shown when the window opens

    def __repr__(self) -> str:
        kb = self.core.kb_name(self.kb) if self.kb else None
        return (f"BedrockChatView(kb={kb!r}, model={self.model!r}, {_plural(len(self.values), 'setting')}, "
                f"{_plural(len(self.answers), 'question')}) · help() lists its commands")

    def _ipython_display_(self) -> None:
        """
        A cell ending with the view shows the chat window, unless the same cell already did (chat() shows it).
        """
        if self.use_html and self._shown_in is not None and self._shown_in == _cell_number():
            return
        if self.use_html:
            self.app()
        else:
            print(repr(self))

    # ------------------------------------------------------------------ plumbing

    def _display(self, widget: Any) -> None:
        from IPython.display import display

        display(widget)

    def _explain(self, code: str, message: str) -> str:
        """
        The AWS error message plus what to do about it.
        """
        lowered = message.lower()
        text = message.rstrip() + ("" if message.rstrip().endswith((".", "!", "?")) else ".")
        if "managed knowledge base" in lowered:
            return (f"{text} Search a managed knowledge base with bedrock_kb.py's search() instead: RetrieveAndGenerate "
                    "can't ask one.")
        if code == "ResourceNotFoundException" and "model" not in lowered and "session" not in lowered:
            return f"{text} kbs() lists the knowledge bases in {self._region() or 'this region'}."
        if code == "AccessDeniedException" and "model" in lowered and "not authorized to perform" not in lowered:
            return f"{text} Enable the model in the Bedrock console (Model access), or pick another one."
        if code == "AccessDeniedException":
            return f"{text} README lists the IAM permissions the chat needs."
        if code == "ValidationException" and "on-demand throughput" in lowered:
            return (f"This model needs an inference profile: pick {self._profile_for(message)!r} instead "
                    "(models() shows it).")
        if code == "ValidationException" and "hybrid" in lowered:
            return f"{text} This vector store only does SEMANTIC search: unset('search_type')."
        if "temperature" in lowered and ("top_p" in lowered or "topp" in lowered or "top p" in lowered):
            return f"{text} Keep one of them: unset('top_p') (in the window, ✕ next to Top P)."
        if "temperature" in lowered:
            return f"{text} This model may not take a temperature: unset('temperature')."
        if code in ("ThrottlingException", "TooManyRequestsException", "ServiceQuotaExceededException"):
            return f"{text} Bedrock throttled the call: wait a few seconds and ask again."
        return message

    def _profile_for(self, message: str) -> str:
        """
        The inference profile to use for the model an error message names ('<profile id>' if unknown).
        """
        match = re.search(r"model ID ([\w.:-]+)", message)
        try:
            found = [
                m.invoke_id
                for m in self.core.models()
                if match
                and m.id == match.group(1).rstrip(".")
                and m.via == "inference profile"
            ]
        except (ClientError, BotoCoreError):
            found = []
        return found[0] if found else "<profile id>"

    def _source_names(self, kb_id: str, passages: Iterable[Passage]) -> dict[str, str]:
        """
        {ID: name} of the data sources these passages came from, when that's more than one (else {}): names from
        one cached ListDataSources, or the IDs when it can't be read.
        """
        ids = sorted({p.data_source_id for p in passages if p.data_source_id})
        if len(ids) < 2:
            return {}
        try:
            names = self.core.data_source_names(kb_id)
        except (ClientError, BotoCoreError):
            names = {}
        return {ds_id: names.get(ds_id) or ds_id for ds_id in ids}

    @staticmethod
    def _top_source(
        passages: list[Passage], names: dict[str, str]
    ) -> tuple[str, int] | None:
        """
        (name, count) of the data source most of these passages came from, when they came from several.
        """
        if not names:
            return None
        counts = Counter(p.data_source_id for p in passages if p.data_source_id in names)
        if not counts:
            return None
        ds_id, count = counts.most_common(1)[0]
        return names[ds_id], count

    def _price_basis(self, models: bool = False) -> str:
        """
        The prices a cost was worked out with; models=True: the models' prices (model_prices=).
        """
        return _which_prices(self.core.model_prices, MODEL_PRICES) if models else super()._price_basis()

    def _region(self) -> str:
        try:
            return self.core.region
        except ValueError:
            return ""

    def _kb_id(self) -> str:
        """
        The knowledge base questions go to: kb=, else the only one in the region; a _Hint says how to pick.
        """
        if self.kb is not None:
            self.kb = self.core.resolve(self.kb)
            return self.kb
        names = self.core.knowledge_base_names()
        if len(names) == 1:
            self.kb = next(iter(names))
            return self.kb
        if not names:
            raise _Hint(f"There are no knowledge bases in {self.core.region}. They're regional: "
                        "chat(region='us-west-2') looks in another region.")
        listed = sorted(names.values(), key=str.lower)
        raise _Hint(f"Which knowledge base? There are {len(names)} in {self.core.region}: "
                    f"{', '.join(listed[:15])}{', …' if len(listed) > 15 else ''}. Pick one with use('{listed[0]}'), "
                    f"or chat('{listed[0]}').")

    def _model_label(self, model: str) -> str:
        """
        The model's name when the model list has been read ('Claude Sonnet 5'), else its short ID.
        """
        wanted = model or str(self.core.default_model or DEFAULT_MODEL)
        if self.core._models:  # read already, so no AWS call: a short name or the default finds the model it calls
            try:
                wanted = self.core.resolve_model(wanted)[0]
            except ValueError:
                pass
        for m in self.core._models or []:
            if wanted in (m.invoke_id, m.id, m.arn):
                return m.name or m.id
        return short_model(wanted)

    def _use_kb(self, kb_id: str) -> None:
        self.kb = kb_id
        self.data_source = self.picked_files = None  # another knowledge base has other data sources and files
        self._reset()

    def _sources(self, kb_id: str) -> dict[str, str]:
        """
        {ID: name} of the data sources questions search ({} = all of them), resolved from data_source=.
        """
        self.data_source = self.core.resolve_sources(kb_id, self.data_source)
        return self.data_source

    def _sources_now(self) -> dict[str, str]:
        """
        The data sources questions search, if they can be told yet ({} otherwise).
        """
        if not self.data_source:
            return {}
        try:
            return self._sources(self._kb_id())
        except (ValueError, ClientError, BotoCoreError):
            return {}

    def _sources_text(self) -> str:
        """
        'all', or the names of the only data sources questions search.
        """
        sources = self._sources_now()
        return ", ".join(name or ds_id for ds_id, name in sources.items()) if sources else "all"

    def _files_for(self, kb_id: str) -> list[str]:
        """
        The s3:// paths of the only files questions search ([] = all of them), resolved from files=.
        """
        self.picked_files = self.core.resolve_files(kb_id, self.picked_files)
        return self.picked_files

    def _files_now(self) -> list[str]:
        """
        The files questions search, if they can be told yet ([] otherwise).
        """
        if not self.picked_files:
            return []
        try:
            return self._files_for(self._kb_id())
        except (ValueError, ClientError, BotoCoreError):
            return []

    def _files_text(self) -> str:
        """
        'all', or the names of the only files questions search.
        """
        uris = self._files_now()
        if not uris:
            return "all"
        names = [source_name(u) for u in uris]
        return ", ".join(names) if len(names) <= 3 else f"{len(names)} files"

    def _reset(self) -> None:
        self.answers, self.session_id = [], None

    def _changed(self, note: str = "") -> None:
        """
        An open window follows what a command changed.
        """
        if self._app is not None:
            self._app.sync()
            if note:  # another knowledge base, or a new chat: the conversation starts over
                self._app.cleared(note)

    def _update(self, values: dict[str, Any]) -> list[str]:
        """
        Applies {name: value} (None removes) to the settings, all or nothing, and says what changed.
        """
        schema = self.core.schema()
        new = dict(self.values)
        problems = []
        for name, value in values.items():
            try:
                f = schema.find(name)
                if value is None:
                    new.pop(f.key, None)
                else:
                    new[f.key] = coerce_setting(f, value)
            except ValueError as exc:
                problems.append(str(exc).rstrip("."))
        if problems:
            raise ValueError(". ".join(problems) + ". Nothing was changed.")
        new = {key: new[key] for key in schema.fields if key in new}
        changes = _diff(self.values, new)
        self.values = new
        return changes

    def _counterpart(self, a: Answer) -> Answer | None:
        """
        The latest question before `a` that asked the same thing the other way (an answer for a search, a search
        for an answer), to compare the two.
        """
        wanted = " ".join(a.question.lower().split())
        before = next((i for i, t in enumerate(self.answers) if t is a), len(self.answers))
        for t in reversed(self.answers[:before]):
            if (t.retrieve_only != a.retrieve_only and t.kb_id == a.kb_id
                    and " ".join(t.question.lower().split()) == wanted):
                return t
        return None

    def _findings(self, a: Answer) -> list[tuple[str, str]]:
        """
        An answer's (or a search's) findings, and what comparing it with the same question asked the other way
        shows.
        """
        found = answer_findings(a)
        other = self._counterpart(a)
        if other is not None:
            found += compare_findings(a, other) if a.retrieve_only else compare_findings(other, a)
        return found

    def _ranks(self, a: Answer) -> dict[int, int]:
        """
        For a search: {rank: [n]} of its passages the answer to the same question cites ({} without one).
        """
        other = self._counterpart(a) if a.retrieve_only else None
        return cited_ranks(a, other) if other is not None else {}

    def _cost_text(self, a: Answer) -> str:
        cost = answer_cost(a, self.core.model_prices, self.core.prices)
        if cost is None:
            return "cost unknown (pass model_prices=...)"
        text = human_money(cost)
        return text if text.startswith("<") else "~" + text

    def _meta(self, a: Answer) -> str:
        """
        'Claude Sonnet 5 · 2.1s · 2 sources cited · 80% grounded · ~$0.004', or for a search 'Retrieve only · 8
        passages · best score 0.821 · 0.4s · <$0.01'.
        """
        only = [describe_sources(a.data_sources) if a.data_sources else "", describe_files(a.files) if a.files else ""]
        narrowed = [f"only {' and '.join(filter(None, only))}"] if any(only) else []
        if a.retrieve_only:
            top = max((p.score for p in a.sources if p.score is not None), default=None)
            parts = ["Retrieve only", *narrowed, _plural(len(a.sources), "passage")]
            parts += [f"best score {_score(top)}"] if top is not None else []
            return " · ".join(parts + [f"{a.seconds:.1f}s", self._cost_text(a)])
        parts = [self._model_label(a.model), *narrowed, f"{a.seconds:.1f}s"]
        if a.first_words is not None:
            parts[-1] += f" (first words {a.first_words:.1f}s)"
        if a.text.strip():
            parts += [f"{_plural(len(a.cited), 'source')} cited", f"{a.grounded_share:.0%} grounded"]
        return " · ".join(parts + [self._cost_text(a)])

    def _turn_html(self, a: Answer) -> str:
        if a.retrieve_only:
            return _search_html(a, self._meta(a), self._findings(a), self._ranks(a))
        return _turn_html(a, self._meta(a), self._findings(a))

    def _conversation_line(self) -> str:
        if not self.answers:
            return "A new conversation: Bedrock keeps the earlier questions in mind for follow-ups."
        costs = [answer_cost(a, self.core.model_prices, self.core.prices) for a in self.answers]
        total = human_money(sum(c for c in costs if c is not None))
        searches = sum(a.retrieve_only for a in self.answers)
        line = (f"{_plural(len(self.answers), 'question')} in this conversation"
                + (f" ({searches} retrieve only)" if searches else "") + f" · {total} so far (estimated)")
        if self.session_id:
            line += f" · session {self.session_id[:8]}…"
        return line + " · New chat starts over."

    def _retrieving(self, retrieve_only: bool | None) -> bool:
        return self.retrieve_only if retrieve_only is None else bool(retrieve_only)


def chat(
    kb: str | None = None,
    model: str | None = None,
    *,
    region: str | None = None,
    profile: str | None = None,
    settings: dict[str, Any] | None = None,
    stream: bool = True,
    data_source: Any = None,
    files: Any = None,
    retrieve_only: bool = False,
    questions: Any = None,
    height: int | str | None = None,
    log: str | None = None,
    assistant: Any = None,
    **values: Any,
) -> BedrockChatView:
    """
    Opens the chat window on a knowledge base and returns the view behind it.

        chat()                                        # pick the knowledge base and the model in the window
        chat("support-docs", model="sonnet")          # by name, ID or ARN; model by ID, profile or short name
        chat("support-docs", data_source="faq")       # ask only one of its data sources (name or ID)
        chat("support-docs", files=["refund-policy.pdf", "faq/returns.md"])   # or only these files
        chat("support-docs", n=8, temperature=0.2, search_type="hybrid", where={"team": "billing"})
        chat("support-docs", settings={"generationConfiguration.performanceConfig.latency": "optimized"})
        chat("support-docs", retrieve_only=True)      # questions only search: every passage found, no answer
        chat("support-docs", questions=["How long do refunds take? | refund-policy.pdf", "Can I return a gift?"])
        chat("support-docs", log="kb-test-runs.jsonl")   # every test run saved as it finishes: load_runs() reads them

    Settings passed as keywords (or settings=, for paths) are added to DEFAULT_SETTINGS; settings={} starts from none.
    A setting that can't be used is named in the window instead of stopping it. region / profile pick the AWS
    region and profile; stream=False shows each answer only when it's complete. retrieve_only=True opens the window
    on Retrieve only: questions show every passage the search finds, and no answer. questions= fills the Test tab
    with a list of test questions to ask with the window's settings (a list, or text with one per line). log= names a
    file every test run is added to as it finishes, so runs outlast a kernel restart (load_runs() reads them back).
    The window fills the browser's height; height= sets the conversation's instead (800 pixels, or CSS such as
    '70vh'). assistant=AgentView(...) adds ✦ Ask: the agent's chat beside the conversation, asking why an answer went
    the way it did about the question, answer and setup you have open.
    """
    view = BedrockChatView(BedrockChatAnalyzer(region=region, profile=profile), kb=kb, model=model, settings={},
                           stream=stream, data_source=data_source, files=files, retrieve_only=retrieve_only,
                           height=height, log=log)
    wanted = {**(DEFAULT_SETTINGS if settings is None else settings), **values}
    for name, value in wanted.items():
        try:
            view._update({name: value})
        except ValueError as exc:
            view._notes.append(f"Not used: {str(exc).replace(' Nothing was changed.', '')}")
    if _filled(questions):
        try:
            view.questions = question_list(questions)
        except ValueError as exc:
            view._notes.append(f"The test questions weren't used: {exc}.")
    view.app(assistant=assistant)
    return view
