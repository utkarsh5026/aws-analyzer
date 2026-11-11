"""
MessagingExplorer, the window to click through SQS queues, SNS topics and EventBridge rules, and explore() to open
it.
"""

from __future__ import annotations

from typing import Any

from ..._kit.blocks import _Note
from ..._kit.errors import _error_name, _why
from ..._kit.fmt import _count, _plural
from ..._kit.text import _esc
from ..._kit.themes import _theme_css, _theme_name
from ..._kit.window import _Explorer, _class_if, _for_window, _skeleton, _window_errors

from ..analyzer import MessagingAnalyzer
from ..helpers import arn_name
from ..models import Overview
from ..style import _CSS, _ROOT, _render_html
from ..view import MessagingView
from .dead import _DeadTab
from .focus import _FocusReads
from .frame import _EXPLORER_CSS, _EXPLORER_TABS, _ITEM_TABS, _LOGO, _window_text
from .items import _EverythingTab, _Item
from .live import _LiveTab
from .map import _MapTab
from .widgets import _ItemField


class MessagingExplorer(_EverythingTab, _MapTab, _DeadTab, _LiveTab, _FocusReads, _Explorer):
    """
    The messaging explorer: a window to click through your SQS queues, SNS topics and EventBridge rules. The field at
    the top switches to another one; the cards beside it say how the one shown is doing.

        Everything    every queue, topic and rule in the region, the ones with problems first, under the region's
                      findings; search, pick a chip or sort by a column, and click one to open it
        Overview      the one open: its settings in plain English, its numbers by day, what sends to it and reads
                      it, and what needs attention, with the commands that fix it
        Map           where a message goes from it, hop by hop, and where it gets lost; click a queue, topic or rule
                      on the way to open it
        Dead letters  every dead-letter queue, the fullest first, what it catches and how to move messages back
        Live          how many messages wait in the queue open (else the fullest few), looked at every few seconds
                      while the tab is open, with which way the backlog is going

    where: what to open: a queue (name, URL or ARN), a topic, a rule ('bus/rule') or a bus; without it, a dead-letter
    queue holding messages, else the one with the most problems. tab: the tab to start on. view / core: a MessagingView or MessagingAnalyzer to use (else one is
    made from region / profile). days: the window of the numbers. height: the height of the tabs' pages (pixels, or
    CSS like '80vh'). mode: 'auto' (the window in Jupyter, reports elsewhere), 'widgets' or 'text'.

    It only reads: it never receives, sends, moves or deletes a message. x.ui is a MessagingView for reports in other
    cells; x.overview, x.report, x.found and x.looks hold the data behind what's shown.
    """

    _PREFIX, _THREADS = "mgx", "messaging-explorer"
    _reword = staticmethod(_window_text)

    def __init__(  # noqa: PLR0913
        self,
        where: str | None = None,
        *,
        tab: str | None = None,
        view: MessagingView | None = None,
        core: MessagingAnalyzer | None = None,
        region: str | None = None,
        profile: str | None = None,
        days: int = 7,
        height: int | str | None = None,
        mode: str = "auto",
    ):
        _check_choices(mode, tab)
        super().__init__()
        if view is None:
            view = MessagingView(core or MessagingAnalyzer(region=region, profile=profile),
                                 mode="text" if mode == "text" else "auto")
        self.ui = view
        self.core = view.core
        self.days = days
        self.height = height
        self.overview: Overview | None = None
        self.report: Any = None
        self.item_key = ""
        self._want_tab = tab
        self._tab = "everything"
        note = self._load_widgets(mode)
        if self._w is None:
            self._reports(where, note, mode)
            return
        self._build()
        self._begin(where)
        self._display()

    def __repr__(self) -> str:
        shown = arn_name(self.item_key.partition(":")[2]) if self.item_key else "nothing open"
        return f"MessagingExplorer({shown}) · help(MessagingExplorer) says what it shows"

    @_window_errors
    def open(self, where: str, *, tab: str | None = None) -> None:
        """
        Shows another queue (name, URL or ARN), topic, rule ('bus/rule') or bus, as picking it in the field does, on
        `tab` when one is named.
        """
        _check_choices("widgets", tab)
        if self.overview is None:
            raise ValueError("The queues, topics and rules are still being read: try again in a moment")
        self._open_item(self._key_for(where), tab=tab)

    @_window_errors
    def watch(self) -> None:
        """
        Opens the Live tab and watches the queue open (else the fullest few): how many messages wait, every few
        seconds, and which way the backlog is going.
        """
        self._show_tab("live")

    @_window_errors
    def refresh(self) -> None:
        """
        Reads everything again: the queues, topics and rules, and the one shown (the ↻ button).
        """
        keep = self.item_key
        self._want_tab = self._tab
        self._begin(None, keep=keep)

    def _key_for(self, where: str) -> str:
        """
        An item's key from what's typed or passed: a name, URL, ARN, 'bus/rule' or a bus.
        """
        kind, _, arn = self.core._start(self.overview, where)
        return f"{kind}:{arn}"

    def _reports(self, where: str | None, note: str, mode: str) -> None:
        """
        Without the window (no ipywidgets, or not in Jupyter): the same as reports.
        """
        if note:
            self.ui._show([_Note(note, "warn")])
        elif mode != "text":
            self.ui._show([_Note("The explorer window needs Jupyter (SageMaker, JupyterLab or VS Code). Here, the same "
                                 "in reports: overview() lists everything, follow('name') maps one.")])
        if where is None:
            self.ui.overview(days=self.days)
            return
        self.ui.follow(where, days=self.days)

    def _html(self, blocks: list[Any]) -> str:
        """
        Report blocks as the window shows them: the commands in their sentences turned into tabs, drawn in the report
        theme the window opened with, and without the report and theme CSS each one carries (the window's style holds
        them).
        """
        html = _render_html(_for_window(blocks, _window_text), self.ui.max_rows, theme=self._theme)
        return html.replace(_CSS, "", 1).replace(self._theme_style, "", 1)

    def _build(self) -> None:
        w, layout = self._w, self._w.Layout
        self.backdrop = w.Button(layout=layout(display="none"))
        self.backdrop.add_class("mgx-backdrop")
        self.backdrop.on_click(self._safely(lambda _button: self._close_lists()))
        self.title = w.HTML(layout=layout(flex="1 1 auto", min_width="0"))
        self.refresh_button = w.Button(description="↻ Refresh", tooltip="Read the queues, topics and rules again",
                                       layout=layout(width="auto", flex="0 0 auto"))
        self.refresh_button.add_class("mgx-small")
        self.refresh_button.on_click(self._safely(lambda _button: self.refresh()))
        top = w.HBox([self.title, self.refresh_button], layout=layout(width="100%"))
        top.add_class("mgx-top")
        self.field = _ItemField(self, on_pick=self._open_item, on_text=self._typed)
        self.stats = w.HTML(layout=layout(flex="1 1 360px", min_width="0"))
        meta = w.HBox([self.field.field, self.stats], layout=layout(width="100%"))
        meta.add_class("mgx-meta")
        head = w.VBox([top, meta], layout=layout(width="100%"))
        head.add_class("mgx-head")
        tabs = self._tab_bar(_EXPLORER_TABS)
        self.item_view = w.HTML(layout=layout(width="100%"))
        self._theme = _theme_name()
        self._theme_style = _theme_css(_ROOT, self._theme)
        self._frame(_CSS + _EXPLORER_CSS + self._theme_style, head, tabs, {
            "everything": self._everything_page(),
            "overview": w.VBox([self.item_view]),
            "map": self._map_page(),
            "dead": self._dead_page(),
            "live": self._live_page(),
        })
        self._show_tab(self._tab)
        self._draw_title()
        self._draw_stats()

    def _show_tab(self, key: str) -> None:
        was = self._tab
        self._tab = key
        for name, button in self.tab_buttons.items():
            _class_if(button, "mgx-on", name == key)
            self.pages[name].layout.display = "" if name == key else "none"
        if was == "live" and key != "live":
            self._stop_watch()
        if key == "live" and was != "live" and self.overview is not None:
            self._start_watch()
        if not self._said[2] and self._said[1] != "warn":
            self._status(self._tab_line(key))
        self._focus_moved()

    def _tab_line(self, key: str) -> str:
        """
        The status line for a tab: what's in it, or how to use it.
        """
        overview = self.overview
        if overview is None:
            return ""
        if key == "everything":
            return f"{len(self.items):,} queues, topics and rules in {self.core.region}: click one to open it."
        if key in _ITEM_TABS and not self.item_key:
            return "Pick a queue, topic or rule in the field above, or click one in the Everything tab."
        if key == "map" and self.found is not None:
            return f"{len(self.found.hops):,} hops from {self.found.name}: click a queue, topic or rule to open it."
        if key == "dead":
            return f"{len(self.dead_shown):,} dead-letter queues: click one to open it."
        if key == "live":
            return "Live looks at the queues every few seconds while this tab is open."
        return ""

    def _begin(self, where: str | None, *, keep: str = "") -> None:
        """
        Reads the region's queues, topics and rules, then opens the one asked for (else the one with most problems).
        """
        self._drop("item")
        self._status(f"Reading the queues, topics and rules in {self.core.region}…", busy=True, owner="list")
        self._set(self.everything_view, _skeleton("Reading the queues, topics, rules and their numbers…"))
        self._later("list", lambda: self.core.overview(days=self.days), lambda ov: self._listed(ov, where, keep),
                    self._list_failed)

    def _list_failed(self, exc: BaseException) -> None:
        reason = exc if isinstance(exc, ValueError) else _why(_error_name(exc), "sqs:ListQueues")
        self.field.problem = f"Couldn't list the queues, topics and rules ({reason})."
        self.field.set_choices([])
        self._status(f"Couldn't list the queues, topics and rules ({reason}).", "warn")
        self._set(self.everything_view, self._html([_Note(f"Couldn't read them: {reason}.", "warn")]))

    def _listed(self, overview: Overview, wanted: str | None, keep: str) -> None:
        self.overview = overview
        self._draw_everything()
        self._draw_dead()
        self.field.set_choices(self.items)
        self._draw_stats()
        if not self.items:
            self._status(f"No queues, topics or rules in {self.core.region}.", "warn")
            return
        problems = sum(1 for item in self.items if item.problems)
        self._status(f"{len(self.items):,} queues, topics and rules in {self.core.region}"
                     + (f" · {problems:,} with problems" if problems else ""), owner="list")
        target = self._target(wanted, keep)
        if target:
            self._open_item(target, tab=self._want_tab)
            self._want_tab = None
        elif self._want_tab:
            self._show_tab(self._want_tab)
            self._want_tab = None

    def _target(self, wanted: str | None, keep: str) -> str:
        """
        What to open first: the one asked for, else the one shown before a refresh, else the Everything tab's first
        line (a dead-letter queue holding messages, else the one with most problems).
        """
        if wanted:
            try:
                return self._key_for(wanted)
            except ValueError as exc:
                self._status(self._error_text(exc), "warn")
                return ""
        if keep:
            return keep
        return self.items_shown[0].key if self.items_shown else ""

    def _typed(self, text: str) -> None:
        self._open_item(self._key_for(text))

    def _draw_title(self) -> None:
        sub = "Every queue, topic and rule, where a message goes and where it gets lost · read-only"
        if self.item_key:
            kind, _, arn = self.item_key.partition(":")
            sub = f"{kind} {arn_name(arn)} · {arn}"
        self._set(self.title, f'<div class="mgx-brand"><span class="mgx-logo">{_LOGO}</span><div style="min-width:0">'
                              f'<div class="mgx-name">Messaging explorer<span>{_esc(self.core.region)}</span></div>'
                              f'<div class="mgx-sub">{_esc(sub)}</div></div></div>')

    def _draw_stats(self) -> None:
        """
        The cards beside the field: the queue, topic or rule shown at a glance, or the region's before one is.
        """
        item = next((i for i in self.items if i.key == self.item_key), None)
        cards = self._region_cards() if item is None else _item_cards(item, self.days)
        html_cards = "".join(f'<div class="mgx-stat {tone}"><span class="l">{_esc(label)}</span><b>{_esc(value)}</b>'
                             "</div>" for label, value, tone in cards)
        self._set(self.stats, f'<div class="mgx-stats">{html_cards}</div>')

    def _region_cards(self) -> list[tuple[str, str, str]]:
        overview = self.overview
        if overview is None:
            return [("Queues", "…", "sk-on"), ("Topics", "…", "sk-on"), ("Rules", "…", "sk-on")]
        dead = sum(q.messages for q in overview.queues if q.is_dlq)
        problems = sum(1 for item in self.items if item.problems)
        return [("Queues", f"{len(overview.queues):,}", ""), ("Topics", f"{len(overview.topics):,}", ""),
                ("Rules", f"{sum(1 for i in self.items if i.kind == 'rule'):,}", ""),
                ("Dead letters", f"{dead:,}", "bad" if dead else ""),
                ("With problems", f"{problems:,}", "warn" if problems else "")]

    def _open_item(self, key: str, *, tab: str | None = None) -> None:
        """
        Shows a queue, topic, rule or bus: its map at once (from the overview), then its report, read in the
        background; what's still being read for the one shown before is dropped when it comes back.
        """
        self._drop("item")
        self.item_key, self.report = key, None
        kind, _, arn = key.partition(":")
        self.field.value = key
        self.field.draw()
        self._draw_title()
        self._draw_stats()
        self._draw_item_rows()
        self._draw_map(kind, arn)
        self._draw_dead()
        if tab:
            self._show_tab(tab)
        if kind == "bus":
            self._draw("overview", self.item_view, [_Note(f"{arn} is an event bus: the Map tab shows its rules, and "
                                                          "a click on one opens it.")])
            return
        self._set(self.item_view, _skeleton(f"Reading {arn_name(arn)}…"))
        self._status(f"Reading {arn_name(arn)}…", busy=True, owner="item")
        self._later("item", lambda: self._read_report(kind, arn), lambda report: self._got_report(kind, report),
                    self._report_failed)
        if self._tab == "live":
            self._start_watch()

    def _read_report(self, kind: str, arn: str) -> Any:
        """
        The report the Overview tab shows, read off the loop: a queue's, a topic's or a rule's.
        """
        if kind == "queue":
            return self.core.queue_report(arn, days=self.days)
        if kind == "topic":
            return self.core.topic_report(arn, days=self.days)
        return self.core.rule_report(arn, days=self.days)

    def _report_failed(self, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._finish("item", text, "warn", tabs=("overview",))
        self._draw("overview", self.item_view, [_Note(text, "warn")])

    def _got_report(self, kind: str, report: Any) -> None:
        self.report = report
        blocks = {"queue": self.ui._queue_blocks, "topic": self.ui._topic_blocks, "rule": self.ui._rule_blocks}[kind](
            report)
        self._draw("overview", self.item_view, blocks)
        item = next((i for i in self.items if i.key == self.item_key), None)
        problems = _plural(item.problems, "problem") if item and item.problems else "nothing to fix"
        name = arn_name(self.item_key.partition(":")[2])
        self._finish("item", f"{name}: {problems} · the Map tab shows where its messages go.", tabs=_ITEM_TABS)
        self._focus_moved()


def _item_cards(item: _Item, days: int) -> list[tuple[str, str, str]]:
    """
    The cards beside the field for the queue, topic or rule shown.
    """
    came = {"queue": "Sent", "dlq": "Sent", "topic": "Published", "rule": "Matched"}[item.kind]
    cards = [(f"{came} · {days}d", _count(item.traffic), "")]
    if item.waiting is not None:
        cards.append(("Waiting", _count(item.waiting), "bad" if item.kind == "dlq" and item.waiting else ""))
    if item.failed is not None:
        cards.append(("Failed", _count(item.failed), "bad" if item.failed else ""))
    cards.append(("Problems", f"{item.problems:,}", "warn" if item.problems else "ok"))
    return cards


def _check_choices(mode: str, tab: str | None) -> None:
    """
    Refuses a mode or tab the window doesn't have, saying which it does.
    """
    if mode not in ("auto", "widgets", "text"):
        raise ValueError("mode must be 'auto', 'widgets' or 'text'")
    tabs = [key for key, _, _, _ in _EXPLORER_TABS]
    if tab is not None and tab not in tabs:
        raise ValueError(f"tab is one of {', '.join(tabs)}, not {tab!r}")


def explore(  # noqa: PLR0913
    where: str | None = None,
    *,
    tab: str | None = None,
    region: str | None = None,
    profile: str | None = None,
    days: int = 7,
    height: int | str | None = None,
) -> MessagingExplorer:
    """
    Opens the messaging explorer window and returns it: every SQS queue, SNS topic and EventBridge rule, the one you
    click in full, where its messages go hop by hop, the dead-letter queues, and Live, a queue's backlog every few
    seconds.

        explore()                                     # a dead-letter queue holding messages, else the most problems
        explore("orders-events")                      # a queue, topic, rule ('shop/order-placed') or bus
        explore("orders", tab="live")                 # watch a queue drain

    It only reads: it never receives, sends, moves or deletes a message.
    """
    return MessagingExplorer(where, tab=tab, region=region, profile=profile, days=days, height=height)
