"""
The Logs tab's plain-English search: ✦ beside the search box, shown while the window has an assistant that writes
queries (an AgentView: its _ask_query_). In ✦ mode, Enter sends the words to the assistant, which writes a query in
the box's own language (and a time range when the words name one) with one model call and no tools; the window checks
it with parse_query before showing it, and Use it puts it in the box, where it filters the runs as typing does.
"""

from __future__ import annotations

from typing import Any

from ..._kit.fmt import _duration
from ..._kit.focus import QueryDraft
from ..._kit.text import _clip, _esc

from ..analysis.logs import LogRun
from ..analysis.performance import percentile
from ..analysis.query import QueryError, parse_query
from ..analysis.runs import BUILTIN_FIELDS, run_table
from ..analyzer import LambdaAnalyzer
from .logs import _QUERY_HINT

_ASK_QUERY = "_ask_query_"
_PLAIN_HINT = "Describe the runs you want, then Enter: slow runs yesterday afternoon that started cold"
_LOGGED_FIELDS = 40
_MEDIAN = 50
_MS_PER_SECOND = 1000
_QUERY_CHARS = 300

_PLAIN_CSS = """<style>
.lmx-app.lmx-app button.lmx-plain{flex:0 0 auto;font-weight:700;color:#0d7a6f;border:1px solid #9fd3ca;
background:#e3f3f0;border-radius:8px;padding:0 10px}
.lmx-app.lmx-app button.lmx-plain.lmx-plain-on{color:#fff;background:#0d7a6f;border-color:#0d7a6f}
.lmx-app.lmx-app .lmx-draft{align-items:center;gap:8px;padding:6px 10px;margin:4px 0;border:1px solid #9fd3ca;
border-radius:9px;background:rgba(13,122,111,.08)}
.lmx-app .lmx-draft .dk{font-weight:700;color:#0d7a6f;margin-right:6px}
.lmx-app .lmx-draft code{font-weight:600}
.lmx-app .lmx-draft .dn{display:block;font-size:12px;opacity:.8;margin-top:2px}
</style>"""


def _runs_context(runs: list[LogRun], timeout_seconds: int) -> str:
    """
    What the runs read look like, for the model to read vague words by: how many, their median and slowest run
    time, and the timeout. '' before any run with a run time is read.
    """
    durations = [run.duration for run in runs if run.duration is not None]
    if not durations:
        return ""
    median = percentile(durations, _MEDIAN) or 0.0
    return (f"The runs read: {len(runs):,}, median {_duration(median / _MS_PER_SECOND)}, slowest "
            f"{_duration(max(durations) / _MS_PER_SECOND)}; the function's timeout is {timeout_seconds} s.")


def _check_draft(draft: QueryDraft) -> str:
    """
    What's wrong with a query the assistant wrote ('' when nothing is): a query parse_query can't read, or a time
    range time_range can't. Nothing it wrote is used before this says it's fine.
    """
    if not draft.query:
        return "It wrote no query."
    try:
        parse_query(draft.query)
    except QueryError as exc:
        return str(exc)
    if not draft.since and not draft.until:
        return ""
    try:
        LambdaAnalyzer.time_range(draft.since or "24h", draft.until or None)
    except ValueError as exc:
        return f"Its time range can't be read: {exc}"
    return ""


def _draft_html(draft: QueryDraft, problem: str) -> str:
    """
    The strip under the search box: the query the assistant wrote, its time range and its note; or, when it can't
    be used, why, with what it wrote.
    """
    query = f"<code>{_esc(_clip(draft.query, _QUERY_CHARS))}</code>"
    if problem:
        return f'<span class="dk">✦ Couldn\'t use</span>{query}<span class="dn">{_esc(problem)}</span>'
    when = f" · from {_esc(draft.since)}" if draft.since else ""
    until = f" until {_esc(draft.until)}" if draft.until else ""
    said = [_esc(draft.note)] if draft.note else []
    note = " · ".join([*said, "one model call, no tools"])
    return f'<span class="dk">✦ Read as</span>{query}{when}{until}<span class="dn">{note}</span>'


class _PlainSearch:
    """
    The Logs tab's ✦ plain-English search, in LambdaExplorer: its button beside the search box, the strip that
    shows the query written, and Enter in ✦ mode.
    """

    def _plain_widgets(self) -> tuple[Any, Any]:
        """
        The ✦ button (shown while the assistant writes queries) and the strip under the search bar (hidden until
        a query is written).
        """
        w, layout = self._w, self._w.Layout
        self._plain, self._draft = False, None
        self.plain_button = w.Button(description="✦", tooltip="Search in plain English: the agent writes the query",
                                     layout=layout(width="auto", display="" if self._writes_queries else "none"))
        self.plain_button.add_class("lmx-plain")
        self.plain_button.on_click(self._safely(lambda _button: self._toggle_plain()))
        self.draft_view = w.HTML(layout=layout(flex="1 1 auto", width="auto", min_width="0"))
        self.draft_use = w.Button(description="Use it ⏎", tooltip="Put the query in the search box", layout=layout(
            width="auto", flex="0 0 auto"))
        self.draft_use.on_click(self._safely(lambda _button: self._use_draft()))
        drop = w.Button(description="✕", tooltip="Drop it", layout=layout(width="auto", flex="0 0 auto"))
        drop.add_class("lmx-x")
        drop.on_click(self._safely(lambda _button: self._drop_draft()))
        self.draft_row = w.HBox([self.draft_view, self.draft_use, drop], layout=layout(width="100%", display="none"))
        self.draft_row.add_class("lmx-draft")
        return self.plain_button, self.draft_row

    @property
    def _writes_queries(self) -> bool:
        return callable(getattr(self.assistant, _ASK_QUERY, None))

    def _assistant_changed(self) -> None:
        """
        Another assistant (or none): ✦ shows only while it writes queries.
        """
        self.plain_button.layout.display = "" if self._writes_queries else "none"
        if not self._writes_queries:
            self._set_plain(False)
            self._drop_draft()

    def _toggle_plain(self) -> None:
        self._set_plain(not self._plain)

    def _set_plain(self, on: bool) -> None:
        self._plain = on
        self.log_find.placeholder = _PLAIN_HINT if on else _QUERY_HINT
        description = "✦ on" if on else "✦"
        self.plain_button.description = description
        if on:
            self.plain_button.add_class("lmx-plain-on")
        else:
            self.plain_button.remove_class("lmx-plain-on")

    def _log_entered(self) -> None:
        """
        Enter in the Logs search box: in ✦ mode the words go to the assistant, else CloudWatch is searched.
        """
        if self._plain:
            self._write_query(str(self.log_find.value or "").strip())
        else:
            self._log_search()

    def _query_fields(self) -> dict[str, str]:
        """
        The fields the model may use: Lambda's own, then the ones the runs read log, by name.
        """
        fields = dict(BUILTIN_FIELDS)
        if self.logs_page is None:
            return fields
        logged = [name for name in run_table(self.logs_page).field_names() if name not in fields]
        for name in logged[:_LOGGED_FIELDS]:
            fields[name] = "a field its logs carry"
        return fields

    def _write_query(self, text: str) -> None:
        """
        Asks the assistant for a query of what the words describe, off the event loop.
        """
        if not text:
            self._status("Describe the runs you want first, then press Enter.")
            return
        fields = self._query_fields()
        timeout = self.function.timeout if self.function is not None else 0
        context = _runs_context(self._runs, timeout)
        self._status("✦ Writing the query…", busy=True, owner="plain")

        def work() -> QueryDraft:
            return getattr(self.assistant, _ASK_QUERY)(text, fields=fields, context=context)

        self._later("plain", work, self._draft_written, owner="plain")

    def _draft_written(self, draft: QueryDraft) -> None:
        """
        The query the assistant wrote, checked and shown under the search box, with Use it when it can be used.
        """
        problem = _check_draft(draft)
        self._draft = None if problem else draft
        self._set(self.draft_view, _draft_html(draft, problem))
        self.draft_use.layout.display = "none" if problem else ""
        self.draft_row.layout.display = ""
        if problem:
            self._finish("plain", "The agent's query can't be used: change the words and press Enter again.", "warn",
                         tabs=("logs",))
        else:
            self._finish("plain", "✦ Read as a query: Use it puts it in the search box.", tabs=("logs",))

    def _use_draft(self) -> None:
        """
        Use it: the written query goes in the search box (and filters the runs read), and its time range, when it
        named one, becomes the tab's, read again.
        """
        draft = self._draft
        if draft is None:
            return
        self._drop_draft()
        self._set_plain(False)
        ranged = bool(draft.since or draft.until)
        if ranged:
            self._set_range(draft.since or "24h", draft.until or None)
        self.log_find.value = draft.query
        if ranged:
            self._load_logs()

    def _drop_draft(self) -> None:
        self._draft = None
        self.draft_row.layout.display = "none"
