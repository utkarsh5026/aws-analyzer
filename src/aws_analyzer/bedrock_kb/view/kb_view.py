"""
BedrockKBView, made of its commands: the plumbing they share, help(), and explore().
"""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING, Any, Iterable

from botocore.exceptions import BotoCoreError, ClientError

from ..._bedrock.models import Passage
from ..._bedrock.prices import MODEL_PRICES
from ..._kit.deps import _require
from ..._kit.errors import _Hint
from ..._kit.view import _BaseView, _friendly_errors, _which_prices

from ..models import Answer, FileInventory, Retrieval
from ..analyzer.kb_analyzer import BedrockKBAnalyzer
from ..style import _render_html
from .knowledge_bases import _KnowledgeBaseCommands
from .indexed import _IndexCommands
from .search import _SearchCommands
from .measure import _MeasureCommands

if TYPE_CHECKING:
    from ..explorer.window import KBExplorer


class BedrockKBView(_KnowledgeBaseCommands, _IndexCommands, _SearchCommands, _MeasureCommands, _BaseView):
    """
    Notebook UI over BedrockKBAnalyzer. Each method renders a report and returns nothing; for the underlying
    data call the matching method on `view.core` (a BedrockKBAnalyzer).

    kb: the knowledge base to use when a command isn't given one (a name, ID or ARN); use() changes it. Without
    it, commands use the only knowledge base in the region, or say how to pick one.
    mode: 'auto' (HTML inside Jupyter, text elsewhere), 'html' or 'text'.
    max_rows: default cap for long tables (set to 0 for no cap).
    progress: 'auto' (a tqdm bar while long commands run, when tqdm is installed; else a line with the count,
    rate and time left), 'plain' (always that line) or 'off'.
    """

    _render_html = staticmethod(_render_html)  # its look: the stylesheet, root class and badge
    _GROUPS = {  # help() lists the commands in these groups, in this order
        "🧭 Explore by clicking": ("explore",),
        "📚 Knowledge bases": ("kbs", "use", "kb_info"),
        "📥 What's indexed": ("files", "save_file_list", "file", "search_file", "syncs", "documents", "unsynced"),
        "🔎 Search and answer": ("search", "chunk", "link", "ask", "follow_up", "models"),
        "📏 Measure retrieval": ("compare", "evaluate"),
        "❓ Help": ("help",),
    }
    _START = (
        ("explore()", "the explorer window: every file, how it was indexed, syncs and search, by clicking"),
        ("kbs()", "every knowledge base: status, last sync, cost and warnings"),
        ("use('name')", "pick the knowledge base later commands use"),
        ("ask('a question')", "an answer with citations"),
    )

    def __init__(
        self,
        core: BedrockKBAnalyzer | None = None,
        *,
        kb: str | None = None,
        mode: str = "auto",
        max_rows: int = 50,
        progress: str = "auto",
    ):
        super().__init__(mode=mode, max_rows=max_rows, progress=progress)
        self.core: BedrockKBAnalyzer = core or BedrockKBAnalyzer()
        self.kb = kb
        self._last: Retrieval | Answer | None = None  # what chunk() reads
        self._conversation: dict[str, Any] | None = None  # what follow_up() continues
        self._inventories: dict[str, FileInventory] = {}  # knowledge base ID -> its files, as last listed
        self.explorer: KBExplorer | None = None  # the window explore() opened last

    # ------------------------------------------------------------------ plumbing

    def _explain(self, code: str, message: str) -> str:
        """
        The AWS error message plus what to do about it.
        """
        lowered = message.lower()
        text = message.rstrip() + (
            "" if message.rstrip().endswith((".", "!", "?")) else "."
        )
        if code == "ResourceNotFoundException" and "model" not in lowered:
            return f"knowledge base (or data source) not found in {self.core.region}; kbs() lists them"
        if (
            code == "AccessDeniedException"
            and "model" in lowered
            and "not authorized to perform" not in lowered
        ):
            return f"{text} Enable the model in the Bedrock console (Model access), or pick one from models()."
        if code == "AccessDeniedException":
            return (
                f"{text} README lists the read-only IAM permissions each command needs."
            )
        if code == "ValidationException" and "on-demand throughput" in lowered:
            return f"this model needs an inference profile: pass model={self._profile_for(message)!r} (models() shows it)."
        if code == "ValidationException" and "hybrid" in lowered:
            return "this vector store only supports SEMANTIC search: drop search_type='HYBRID'."
        if code in (
            "ThrottlingException",
            "TooManyRequestsException",
            "ServiceQuotaExceededException",
        ):
            return f"{text} Bedrock throttled the call: wait a few seconds and retry."
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

    def _on(self, kb_id: str, name: str) -> dict[str, str]:
        """
        kb= for a next step's call, unless it's the knowledge base commands already use without one.
        """
        return {} if self.kb in (kb_id, name) else {"kb": name}

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

    def _url(self, uri: str, page: int | None = None) -> str:
        """
        The link that opens a source's file in a new tab, or '' (no file to open, or no credentials to sign the
        link with: the name then stays text, and the report still renders).
        """
        try:
            return self.core.file_url(uri, page=page) or ""
        except (BotoCoreError, ClientError, ValueError):
            return ""

    def _link(self, p: Passage) -> str:
        return self._url(p.uri, p.page)

    def _price_basis(self, models: bool = False) -> str:
        """
        The prices a cost was worked out with; models=True: the models' prices (model_prices=).
        """
        return _which_prices(self.core.model_prices, MODEL_PRICES) if models else super()._price_basis()

    def _kb(self, kb: str | None) -> str:
        """
        The knowledge base a command works on: `kb`, else the view's default, else the only one in the region.
        """
        if kb is not None:
            return self.core.resolve(kb)
        if self.kb is not None:
            return self.core.resolve(self.kb)
        names = self.core.knowledge_base_names()
        if len(names) == 1:
            return next(iter(names))
        if not names:
            raise _Hint(
                f"There are no knowledge bases in {self.core.region}. They're regional: try "
                "BedrockKBView(BedrockKBAnalyzer(region='us-west-2'))."
            )
        listed = sorted(names.values(), key=str.lower)
        raise _Hint(
            f"Which knowledge base? There are {len(names)} in {self.core.region}: "
            f"{', '.join(listed[:15])}{', …' if len(listed) > 15 else ''}. Pass kb='{listed[0]}', or pick one for "
            f"every command with use('{listed[0]}')."
        )

    # ------------------------------------------------------------------- the window

    @_friendly_errors
    def explore(self, kb: str | None = None, *, file: str | None = None, height: int | str | None = None) -> None:
        """
        The explorer window: every file next to Bedrock's record of it (failed and why, changed since the last
        sync, not synced yet...), and for the one you click, how it was indexed: the parser, its chunks in document
        order, its metadata, and whether a question finds it. Also the indexing pipeline, the sync history, a search
        box and every setting, by clicking. Needs Jupyter and ipywidgets; view.explorer is the window.
        """
        if not self.use_html:
            raise _Hint(
                "The explorer window needs Jupyter (SageMaker, JupyterLab or VS Code). Here, files() lists every file "
                "and whether it's indexed, file('name') shows how one was indexed, and search_file('name', "
                "'a question') whether a question finds it."
            )
        _require("ipywidgets", "The explorer window")
        from ..explorer.window import KBExplorer
        self.explorer = KBExplorer(kb if kb is not None else self.kb, file=file, view=self, height=height,
                                   mode="widgets")
