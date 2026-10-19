"""
_ChatApp, the chat window: its layout, what the View tells it, and its header, conversation and
tabs from the mixins in this package.
"""

from __future__ import annotations

import threading
from typing import Any

from ..._kit.render import _prose
from ..._kit.window import _Window, _class_if, _css_height

from ..models import Answer
from ..analysis.batches import Batch, BatchItem, format_questions
from ..analysis.ranking import Sweep
from ..style import _CSS, _TABS, _TAB_TITLES
from ..blocks import _question_html, _wrap
from ..view.chat_view import BedrockChatView
from .widgets import _ChoicePicker
from .pickers import _PickersPane
from .conversation import _ConversationPane
from .settings import _SettingsTab
from .request import _RequestTab
from .test import _TestTab
from .runs import _RunsTab


_SIDE_MAX_WIDTH = "580px"

_CHAT_MARGIN = "0 16px 8px 0"

_CHAT_ALONE_MARGIN = "0 0 8px 0"


class _ChatApp(_PickersPane, _ConversationPane, _SettingsTab, _RequestTab, _TestTab, _RunsTab, _Window):
    """
    The chat window: ipywidgets wired to a BedrockChatView, which holds the settings and the conversation. Every
    handler shows its own errors in the window: an exception in a widget callback would only reach the browser's
    log, where nobody looks.
    """

    TEST_TAB, RUNS_TAB = _TAB_TITLES.index("Test"), _TAB_TITLES.index("Runs")  # where they are among the side tabs

    def __init__(self, view: BedrockChatView, widgets: Any):
        self.view, self.w = view, widgets
        self.schema = view.core.schema()
        self.inputs: dict[str, Any] = {}  # setting key -> the widget holding its value
        self.rows: dict[str, Any] = {}  # setting key -> its card
        self.row_notes: dict[str, Any] = {}  # setting key -> the lines under it (meaning and path, or what's wrong)
        self.removes: dict[str, Any] = {}  # setting key -> its ✕ button
        self.pending: set[str] = set()  # rows added but not filled in yet: not sent
        self.broken: dict[str, str] = {}  # setting key -> why what's in its box can't be sent
        self.fresh: str | None = None  # the setting added last: its card stays outlined until another is added
        self.headers: dict[str, Any] = {}
        self.chips: dict[str, Any] = {}
        self.picks: dict[str, tuple[Any, Any]] = {}  # setting key -> (its line in the list of settings, its button)
        self.pick_headers: dict[str, Any] = {}
        self.browsing = False  # the list shows every setting, not only the ones the search box matches
        self.bubbles: list[Any] = []
        self.quiet = False  # True while the code sets widget values, so their observers don't fire
        self.busy = False
        self.editing = False  # Edit JSON is open
        self.edit_base = ""  # the request the editor was filled with, to tell whether it's been changed since
        self.problems: list[str] = []  # why a picker couldn't list its choices
        self.unlisted: set[str] = set()  # knowledge bases whose data sources couldn't be listed: not tried again
        self.pickers: list[_ChoicePicker] = []  # the header's fields: one list open at a time
        self._params: dict[str, Any] | None = None
        self.batch: Batch | None = None  # the test run the Test tab shows
        self.batch_rows: list[Any] = []  # an HTML widget per question of it, reused by the next run
        self.batch_old: dict[str, BatchItem] = {}  # the run before it, by question: what each did then
        self.batch_task: Any = None  # the run going on in the background, in a notebook
        self.batch_stop: threading.Event | None = None  # Stop sets it: no more questions are sent
        self.running = False  # a test run is going on
        self.sweep: Sweep | None = None  # the sweep the Test tab shows (self.batch is the setup shown under it)
        self.confirm = ""  # a sweep over SWEEP_MAX_COST that Run was clicked for once: the next click asks it
        self.vary_chips: dict[str, Any] = {}  # Try variations' one-click lines, by setting
        self.root = self._build()

    # ------------------------------------------------------------------ layout

    def _build(self) -> Any:
        w, layout = self.w, self.w.Layout
        style = w.HTML(_CSS, layout=layout(display="none"))
        # over the window while a picker's list is open (under that picker), so a click anywhere else closes it
        self.backdrop = w.Button(layout=layout(display="none"))
        self.backdrop.add_class("kbc-backdrop")
        self.backdrop.on_click(self._safely(lambda _button: self._close_lists()))
        self.title = w.HTML(layout=layout(flex="1 1 auto", min_width="0"))
        self.new_button = w.Button(description="+ New chat", tooltip="Forget this conversation: the next question "
                                   "starts a new Bedrock session", layout=layout(width="auto", flex="0 0 auto"))
        self.new_button.add_class("kbc-new-chat")
        self.new_button.on_click(self._safely(self._new_chat))
        self.kb_pick = self._kb_picker()
        self.source_pick = self._source_picker()
        self.model_pick = self._model_picker()
        self.files_pick = self._files_picker()
        self.mode_pick = w.ToggleButtons(
            options=[("Answer", "answer"), ("Retrieve only", "retrieve")],
            value="retrieve" if self.view.retrieve_only else "answer",
            tooltips=["Search, then the model answers with citations (RetrieveAndGenerate)",
                      "Only search: every passage found, best first, with its score, and no answer (Retrieve)"],
            style={"button_width": "auto"}, layout=layout(flex="0 0 auto"))
        self.mode_pick.add_class("kbc-mode")
        self.mode_pick.observe(self._safely(self._mode_changed), names="value")
        self.chat_toggle = self._pane_toggle("Chat", "Show or hide the conversation")
        self.side_toggle = self._pane_toggle("Settings", "Show or hide the side tabs: Settings, Test, Runs, ...")
        top = w.HBox([self.title, self.mode_pick, self.new_button, self.chat_toggle, self.side_toggle],
                     layout=layout(width="100%", align_items="center"))
        top.add_class("kbc-top")
        pickers = w.HBox([self.kb_pick.field, self.source_pick.field, self.model_pick.field, self.files_pick.field],
                         layout=layout(width="100%", flex_flow="row wrap", margin="12px 0 0 0"))
        pickers.add_class("kbc-pickers")
        head = w.VBox([top, pickers, self.file_bar], layout=layout(width="100%"))
        head.add_class("kbc-head")

        # The style makes the conversation fill the browser window, but in VS Code, whose 100vh is the whole
        # notebook's height, 540px. The view's height= puts its own on the box, which wins over the style's.
        self.log = w.VBox(layout=layout(flex_flow="column-reverse", overflow="hidden auto",
                                        height=_css_height(self.view.height), width="100%"))
        self.log.add_class("kbc-log")  # column-reverse keeps it scrolled to the newest message, without a script
        self.question = w.Text(placeholder="Ask a question, then press Enter", continuous_update=True,
                               layout=layout(flex="1 1 auto", width="auto"))
        self.question.on_msg(self._on_enter(self._send))
        self.send_button = w.Button(description="Send", button_style="primary", tooltip="Ask (Enter does too)",
                                    layout=layout(width="auto", flex="0 0 auto"))
        self.send_button.on_click(self._safely(self._send))
        self.status = w.HTML(layout=layout(width="100%"))
        self.composer = w.HBox([self.question, self.send_button], layout=layout(width="100%"))
        self.composer.add_class("kbc-composer")
        self.chat = w.VBox([self.log, self.composer, self.status], layout=layout(
            flex="1 1 460px", min_width="320px", margin=_CHAT_MARGIN))
        side = self._side()
        body = w.HBox([self.chat, side], layout=layout(width="100%", flex_flow="row wrap", align_items="flex-start"))
        root = w.VBox([style, head, body, self.backdrop], layout=layout(width="100%"))
        root.add_class("kbc-app")

        self.bubbles = [w.HTML(_wrap(self._hello()), layout=layout(width="auto"))]
        for a in self.view.answers:  # questions asked with ask() before the window opened
            self._add(_question_html(a.question))
            self._add(self.view._turn_html(a))
        self._show_log()
        self._show_mode()
        self._sync_rows()
        self._refresh()
        self._render_response()
        if self.view.batches:  # test runs from before the window opened: the last one shows in the Test tab
            self._show_run(self.view.batches[-1])
        self._draw_runs()
        notes = list(self.problems) + list(self.view._notes)
        self.view._notes = []
        if notes:
            self._set_status(" ".join(notes), "warn")
        else:
            self._set_status(self.view._conversation_line())
        return root

    def _pane_toggle(self, label: str, tooltip: str) -> Any:
        """
        A small header button that shows or hides one of the window's two panes, lit while it's shown.
        """
        button = self.w.Button(description=label, tooltip=tooltip, layout=self.w.Layout(width="auto",
                                                                                         flex="0 0 auto"))
        button.add_class("kbc-small")
        button.add_class("kbc-toggle")
        button.add_class("kbc-on")
        button.on_click(self._safely(self._toggle_pane))
        return button

    def _toggle_pane(self, button: Any) -> None:
        """
        Hides the pane the button names, or shows it again. One pane always stays: hiding the last one shows the
        other, and the side tabs grow to the window's width while the conversation is hidden.
        """
        pane, other = (self.chat, self.tabs) if button is self.chat_toggle else (self.tabs, self.chat)
        hiding = pane.layout.display != "none"
        pane.layout.display = "none" if hiding else None
        if hiding and other.layout.display == "none":
            other.layout.display = None
        self.tabs.layout.max_width = "none" if self.chat.layout.display == "none" else _SIDE_MAX_WIDTH
        self.chat.layout.margin = _CHAT_MARGIN if self.tabs.layout.display != "none" else _CHAT_ALONE_MARGIN
        _class_if(self.chat_toggle, "kbc-on", self.chat.layout.display != "none")
        _class_if(self.side_toggle, "kbc-on", self.tabs.layout.display != "none")

    def _side(self) -> Any:
        w, layout = self.w, self.w.Layout
        # Settings: what's wrong first, then what's sent, then adding more
        self.findings = w.HTML(layout=layout(width="100%"))
        self.rows_box = w.VBox(layout=layout(width="100%"))
        self.chip_box = w.HBox(layout=layout(width="100%", flex_flow="row wrap", margin="6px 0 2px 0"))
        self.add_name = w.Text(placeholder="Search: rerank, latency, guardrail…", continuous_update=True,
                               layout=layout(flex="1 1 auto", width="auto"))
        self.add_name.observe(self._safely(self._typed), names="value")
        self.add_name.on_msg(self._on_enter(self._add_typed))
        self.browse_button = w.Button(description=f"Browse all {len(self.schema.fields)}", tooltip="Every setting "
                                      "RetrieveAndGenerate takes, by what it changes", layout=layout(
                                          width="auto", flex="0 0 auto", margin="0 0 0 6px"))
        self.browse_button.add_class("kbc-small")
        self.browse_button.on_click(self._safely(self._browse))
        self.add_help = w.HTML(layout=layout(width="100%"))
        self.results = w.VBox(layout=layout(width="100%"))
        self.results.add_class("kbc-results")
        search = w.HBox([self.add_name, self.browse_button],
                        layout=layout(width="100%", align_items="center"))
        search.add_class("kbc-search")
        # Add a setting stays folded under one button until it's wanted, so the tab shows what's sent, not
        # everything that could be
        self.add_button = w.Button(description="+ Add a setting", tooltip="A common setting in one click, or search "
                                   f"all {len(self.schema.fields)} fields the API takes",
                                   layout=layout(width="100%", margin="8px 0 0 0"))
        self.add_button.add_class("kbc-add")
        self.add_button.on_click(self._safely(lambda _button: self._show_adding(True)))
        self.close_adding = w.Button(description="✕", tooltip="Close Add a setting",
                                     layout=layout(width="22px", flex="0 0 auto"))
        self.close_adding.add_class("kbc-x")
        self.close_adding.on_click(self._safely(lambda _button: self._show_adding(False)))
        self.adding = w.VBox([
            w.HBox([w.HTML(_wrap('<div class="ph">Add a setting</div>'), layout=layout(flex="1 1 auto")),
                    self.close_adding],
                   layout=layout(width="100%", align_items="center")),
            w.HTML(_wrap('<div class="pd">One click adds a common one. Or search all '
                         f"{len(self.schema.fields)} fields by name or by what they do; Enter adds the best match."
                         "</div>")),
            self.chip_box, search, self.add_help, self.results,
        ], layout=layout(width="100%", display="none"))
        self.adding.add_class("kbc-card")
        self.stream_box = w.Checkbox(value=self.view.stream, description="Show answers as they're written",
                                     indent=False, layout=layout(width="auto"))
        self.stream_box.observe(self._safely(self._stream_changed), names="value")
        self.setup = w.HTML(layout=layout(width="100%"))
        window_options = w.VBox([self.stream_box, self.setup], layout=layout(width="100%"))
        window_options.add_class("kbc-foot")
        settings_tab = w.VBox([
            self.findings,
            w.HTML(_wrap('<div class="ph">Sent with every question<span class="hint">hover a name for what it '
                         "does</span></div>")),
            self.rows_box, self.add_button, self.adding, window_options,
        ], layout=layout(width="100%"))

        # Request: the JSON the next question sends
        self.request_mode = w.ToggleButtons(options=["Tree", "JSON", "Python"], value="Tree",
                                            tooltips=["Highlighted, folding JSON", "Plain JSON: click it to select "
                                                      "all", "The same call with boto3"],
                                            style={"button_width": "74px"})
        self.request_mode.observe(self._safely(lambda _change: self._render_request()), names="value")
        self.edit_button = w.Button(description="✎ Edit JSON", tooltip="Change the request by hand: the settings "
                                    "follow what you write", layout=layout(width="auto", flex="0 0 auto"))
        self.edit_button.add_class("kbc-small")
        self.edit_button.on_click(self._safely(self._edit))
        self.request_view = w.HTML(layout=layout(width="100%"))
        self.editor = w.Textarea(layout=layout(width="100%", height="360px"))
        self.editor.add_class("kbc-mono")
        apply_button = w.Button(description="Apply", button_style="primary", tooltip="Make the settings what's "
                                "written here", layout=layout(width="auto"))
        apply_button.on_click(self._safely(self._apply))
        self.restart_button = w.Button(description="Start over", tooltip="Put the request as it is now back in the "
                                       "box", layout=layout(width="auto", margin="0 0 0 6px"))
        self.restart_button.on_click(self._safely(self._restart_edit))
        cancel_button = w.Button(description="Cancel", layout=layout(width="auto", margin="0 0 0 6px"))
        cancel_button.add_class("kbc-ghost")
        cancel_button.on_click(self._safely(self._cancel_edit))
        self.edit_message = w.HTML(layout=layout(width="100%"))
        self.edit_box = w.VBox([self.edit_message, self.editor, w.HBox([apply_button, self.restart_button,
                                                                        cancel_button], layout=layout(
                                                                            margin="8px 0 0 0"))],
                               layout=layout(display="none", width="100%"))
        toolbar = w.HBox([self.request_mode, self.edit_button], layout=layout(
            width="100%", justify_content="space-between", align_items="center", flex_flow="row wrap",
            margin="0 0 6px 0"))
        request_tab = w.VBox([toolbar, self.request_view, self.edit_box], layout=layout(width="100%"))

        # Response: what came back to the last question
        self.response_mode = w.ToggleButtons(options=["Response", "Request sent"], value="Response",
                                             style={"button_width": "104px"})
        self.response_mode.observe(self._safely(lambda _change: self._render_response()), names="value")
        self.response_view = w.HTML(layout=layout(width="100%"))
        response_tab = w.VBox([self.response_mode, self.response_view], layout=layout(width="100%"))

        tabs = w.Tab(children=[settings_tab, self._test_tab(), self._runs_tab(), self._code_tab(), request_tab,
                               response_tab],
                     layout=layout(flex="1 1 400px", min_width="340px", max_width=_SIDE_MAX_WIDTH))
        for i, (title, _) in enumerate(_TABS):
            tabs.set_title(i, title)
        tabs.observe(self._safely(self._tab_changed), names="selected_index")
        height = _css_height(self.view.height)
        for tab in tabs.children if height else ():  # as tall as the conversation and the question box under it
            tab.layout.max_height = f"calc({height} + 80px)"
        tabs.add_class("kbc-side")
        self.tabs = tabs
        return tabs

    # ---------------------------------------------------------------- plumbing

    def _explain(self, code: str, message: str) -> str:
        return self.view._explain(code, message)

    def _warn(self, text: str) -> None:
        self._set_status(text, "warn")

    def _set_status(self, text: str, level: str = "") -> None:
        self.status.value = _wrap(f'<div class="st {level}">{_prose(text)}</div>') if text else ""

    def _add(self, inner: str) -> Any:
        bubble = self.w.HTML(_wrap(inner), layout=self.w.Layout(width="auto"))
        self.bubbles.append(bubble)
        return bubble

    def _show_log(self) -> None:
        self.log.children = tuple(reversed(self.bubbles))  # the box runs bottom-up, so the newest goes first

    def sync(self) -> None:
        """
        Follows changes made from another cell (set(), use(), new_chat(), ask()).
        """
        if self.view.kb is not None and self.kb_pick.value != self.view.kb:
            self.kb_pick.set_value(self.view.kb)
        if self.view.model is not None and self.model_pick.value != self.view.model:
            self.model_pick.set_value(self.view.model)
        if self.stream_box.value != self.view.stream:
            self._quietly(self.stream_box, value=self.view.stream)
        if (self.mode_pick.value == "retrieve") != self.view.retrieve_only:
            self._quietly(self.mode_pick, value="retrieve" if self.view.retrieve_only else "answer")
            self._show_mode()
        self._fill_sources()
        self._draw_files()
        self.pending -= set(self.view.values)
        self._sync_rows()
        self._refresh()

    def added(self, a: Answer) -> None:
        """
        An answer asked from another cell (ask()) joins the conversation shown here.
        """
        self._add(_question_html(a.question))
        self._add(self.view._turn_html(a))
        self._show_log()
        self._render_response()
        self._refresh()
        self._set_status(self.view._conversation_line())

    def cleared(self, note: str) -> None:
        """
        The conversation was reset (new_chat(), another knowledge base).
        """
        self.bubbles = [self.w.HTML(_wrap(self._hello()), layout=self.w.Layout(width="auto"))]
        if note:
            self._add(f'<div class="msg sys">{_prose(note)}</div>')
        self._show_log()
        self._render_response()
        self._refresh()
        self._set_status(self.view._conversation_line())

    def ran(self, batch: Batch) -> None:
        """
        A test run from another cell (ask_all()) shows in the Test tab.
        """
        self._quietly(self.test_box, value=format_questions(self.view.questions))
        self.sweep = None
        self._hide_sweep()
        self.batch, self.batch_old = batch, self.view._old_items(batch)
        self._tests_done(batch)
        self._render_code()

    def swept(self, sweep: Sweep) -> None:
        """
        A sweep from another cell (sweep()) shows in the Test tab.
        """
        self._quietly(self.test_box, value=format_questions(self.view.questions))
        self.sweep = sweep
        self._sweep_done(sweep)
        self._render_code()

    def loaded(self) -> None:
        """
        Runs read back from a file (load_runs(), or the Runs tab's Load): the last one shows in the Test tab.
        """
        self._quietly(self.test_box, value=format_questions(self.view.questions))
        if self.view.batches and not self.running:
            self._show_run(self.view.batches[-1])
        self._draw_test_note()
        self._render_code()
        self._draw_runs()

    def runs_changed(self) -> None:
        """
        The runs were saved (save_runs()): the Runs tab says where.
        """
        self._draw_runs()
