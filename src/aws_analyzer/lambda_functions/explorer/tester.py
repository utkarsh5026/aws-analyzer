"""
LambdaTester, the test window: ui.test('orders-etl') opens it on one function, like the Lambda console's Test page.
Paste an event (or start from one shaped like what S3, SQS, API Gateway, SNS or EventBridge send), see it as tidy
coloured JSON beside the box, with the line and column where it breaks when it isn't valid, then ▶ Run runs the
function once with it. What comes back is drawn below in full: whether it worked, what it returned, its run time,
memory and cost, every line it logged, and everything else Lambda returned (request ID, version, X-Ray trace, billed
and start-up times, headers). Each run is kept, so an earlier one can be shown again.
"""

from __future__ import annotations

import json
import time
from typing import Any

from ..._kit.blocks import _Note
from ..._kit.fmt import _fmt_dt
from ..._kit.highlight import _json_source_html
from ..._kit.render import _call
from ..._kit.text import _clip, _esc
from ..._kit.themes import _theme_css, _theme_name
from ..._kit.window import _Explorer, _for_window, _window_errors

from ..helpers import human_ms
from ..models import Function
from ..analysis.invoke import (
    EVENT_SAMPLES, EventCheck, Invoked, check_event, event_payload, format_event, sample_event,
)
from ..analyzer import LambdaAnalyzer
from ..blocks.invoke import invoke_blocks
from ..style import _CSS, _ROOT, _render_html
from ..view import LambdaView
from .frame import _EXPLORER_CSS
from .function import _LOGO
from .tester_logs import _WATCH_SECONDS, _TesterLogs

_EDITOR_LINES = 12

_PREVIEW_LINES = 400

_DESCRIPTION_CHARS = 90

_TEST_TIP = ("Runs the function once with this event and waits for it, like the Lambda console's Test button. It "
             "really runs: whatever its code writes or sends happens")

_TEST_NOTE = "Runs it for real, once (never retried) · needs lambda:InvokeFunction"

_START_FROM = "Start from…"

_LAST_SENT = "The event sent last"

_TEST_CSS = """<style>
.lmx-app.lmx-app .lmx-tabs.lmx-none{display:none}
.lmx-app .lmx-tst{gap:8px;padding:2px 2px 8px}
.lmx-app .lmx-tst-warn{font-size:11.5px;color:var(--lx-ink-warn)}
.lmx-app .lmx-tst-tools{align-items:center;gap:6px;flex-wrap:wrap}
.lmx-app .lmx-tst-grid{display:grid !important;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:10px;
align-items:stretch}
.lmx-app.lmx-app .lmx-tst-grid textarea{font-family:var(--lx-mono,monospace);font-size:12.5px;line-height:1.55;
border-radius:10px;min-height:240px;height:100%;tab-size:2;white-space:pre;overflow:auto;resize:vertical}
.lmx-app .lmx-tst-pv{border:1px solid var(--lx-line);border-radius:10px;background:var(--lx-code);overflow:auto;
max-height:360px;min-height:240px}
.lmx-app .lmx-tst-pv .pvh{font-size:11.5px;padding:5px 10px;border-bottom:1px solid var(--lx-line);opacity:.8;
position:sticky;top:0;background:var(--lx-surface)}
.lmx-app .lmx-tst-pv .pvh.bad{color:var(--lx-ink-bad);opacity:1}
.lmx-app .lmx-tst-pv .pvh.ok{color:var(--lx-ink-ok);opacity:1}
.lmx-app .lmx-tst-pv table{border-collapse:collapse;width:100%;font-family:var(--lx-mono,monospace);font-size:12.5px;
line-height:1.55}
.lmx-app .lmx-tst-pv td{padding:0 10px 0 0;vertical-align:top;white-space:pre-wrap;word-break:break-word}
.lmx-app .lmx-tst-pv td.ln{width:1%;padding:0 8px 0 10px;text-align:right;opacity:.4;user-select:none;
white-space:nowrap}
.lmx-app .lmx-tst-pv tr.bad td{background:var(--lx-hit)}
.lmx-app .lmx-tst-pv tr.bad td.ln{opacity:1;color:var(--lx-ink-bad);font-weight:700}
.lmx-app .lmx-tst-pv .caret{color:var(--lx-ink-bad);font-weight:700}
.lmx-app .lmx-tst-pv .jk{color:var(--lx-tok-k);font-weight:600}
.lmx-app .lmx-tst-pv .js{color:var(--lx-tok-s)}
.lmx-app .lmx-tst-pv .jn{color:var(--lx-tok-n)}
.lmx-app .lmx-tst-pv .jl{color:var(--lx-tok-f)}
.lmx-app.lmx-app button.lmx-invoke{flex:0 0 auto;height:32px;padding:0 14px;border-radius:999px;font-weight:700;
border-color:var(--lx-accent);color:var(--lx-accent)}
.lmx-app .lmx-tst-foot{align-items:center;gap:8px}
.lmx-app .lmx-tst-views{align-items:center;gap:6px;margin:4px 0 8px;padding-top:8px;border-top:1px solid var(--lx-line)}
.lmx-app .lmx-tst-ws{font-size:12px;opacity:.65;padding:0 6px}
.lmx-app .lmx-tst-ws.on{opacity:1;color:var(--lx-ink-ok)}
.lmx-app .lmx-tst-foot .ic{font-size:12px}
.lmx-app .lmx-tst-foot .ic.ok{color:var(--lx-ink-ok)}
.lmx-app .lmx-tst-foot .ic.bad{color:var(--lx-ink-bad)}
.lmx-app .lmx-tst-foot .in{font-size:11.5px;opacity:.6}
@container (max-width:760px){.lmx-app .lmx-tst-grid{grid-template-columns:minmax(0,1fr)}}
</style>"""


def _numbered(lines: list[str], *, bad_line: int = 0, column: int = 0) -> str:
    """
    Lines of HTML as a table with their numbers in a gutter; `bad_line` (from 1) marked, with a caret under its
    `column`.
    """
    rows = []
    for number, line in enumerate(lines[:_PREVIEW_LINES], start=1):
        is_bad = number == bad_line
        rows.append(f'<tr class="{"bad" if is_bad else ""}"><td class="ln">{number}</td><td>{line or " "}</td></tr>')
        if is_bad and column:
            caret = " " * (column - 1) + "^"
            rows.append(f'<tr class="bad"><td class="ln"></td><td><span class="caret">{caret}</span></td></tr>')
    if len(lines) > _PREVIEW_LINES:
        rows.append(f'<tr><td class="ln">…</td><td>{len(lines) - _PREVIEW_LINES:,} more lines</td></tr>')
    return f"<table>{''.join(rows)}</table>"


def _preview_html(text: str, check: EventCheck) -> str:
    """
    The preview beside the event box: the event as it will be sent, indented and in colour, with line numbers; or,
    when it can't be sent, the text as typed with the line where it breaks marked.
    """
    if check.ok:
        head = f'<div class="pvh ok">✓ What is sent · {check.size:,} bytes</div>'
        return head + _numbered(_json_source_html(check.pretty).split("\n"))
    head = f'<div class="pvh bad">✕ {_esc(check.message)}</div>'
    lines = [_esc(line) for line in text.split("\n")]
    return head + _numbered(lines, bad_line=check.line, column=check.column)


def _compact(text: str) -> str:
    """
    An event on one line, as it would be sent; text that can't be sent comes back as it is.
    """
    try:
        payload = event_payload(text)
    except ValueError:
        return text
    try:
        return json.dumps(json.loads(payload), ensure_ascii=False, separators=(", ", ": "))
    except ValueError:
        return text


def _test_run_label(number: int, invoked: Invoked) -> str:
    """
    An earlier run as the runs menu lists it: its number, how it went, its run time and when.
    """
    report = invoked.report
    took = f" · {human_ms(report.duration)}" if report is not None else ""
    how = "dry run" if invoked.dry_run else ("✓ ran" if invoked.ok else "✕ failed")
    return f"Run {number} · {how}{took} · {_fmt_dt(invoked.started, seconds=True)}"


def _event_text(event: Any) -> str:
    """
    What the event box starts with: event= as tidy JSON, '{}' when there's none. ValueError when it can't be sent.
    """
    if event is None:
        return "{}"
    text = event if isinstance(event, str) else event_payload(event).decode("utf-8", errors="replace")
    event_payload(text)
    return format_event(text)


def _same_words(text: str) -> str:
    return text


class LambdaTester(_TesterLogs, _Explorer):
    """
    The test window for one function, like the Lambda console's Test page: paste an event on the left (or pick one
    under Start from…, shaped like what S3, SQS, SNS, API Gateway or EventBridge send), and see it on the right as it
    will be sent, indented and in colour with line numbers; JSON that breaks shows the line and column where it does,
    and ▶ Run stays off until it's fixed. { } Format tidies the box, Compact puts it on one line. ▶ Run runs the
    function once and draws below whether it worked, its run time, memory, cold start and cost, what to change, what
    it returned, every line it logged, and everything else Lambda returned: the request ID, the version that ran, the
    X-Ray trace, billed and start-up times, the lines Lambda and its extensions wrote, and the response headers.
    Dry run only checks that you may invoke it. Earlier runs stay in a menu on the right.

    Live logs, beside the Result, watches the function's log group while you test it: every few seconds, its runs
    newest first as they come, yours open, so a long run's every line, retries and what other callers run show as
    they happen. ▶ Run starts watching; Watch logs turns it on or off, and it stops by itself after 15 minutes.

    It really runs the function, once per click and never retried, with whatever its code writes or sends; it needs
    lambda:InvokeFunction. name: its name, 'name:alias' (runs that alias), an ARN or a console link. event: what the
    box starts with (a dict, a list or JSON text). x.run() runs it as ▶ Run does (x.run(event) with another event),
    x.event(value) puts an event in the box, x.watch() / x.watch(False) watches the logs or stops, x.runs are the
    calls made (Invoked, oldest first), x.watched the log lines read and x.function the function.
    """

    _PREFIX, _THREADS = "lmx", "lambda-tester"
    _reword = staticmethod(_same_words)

    def __init__(  # noqa: PLR0913
        self,
        name: str,
        event: Any = None,
        *,
        region: str | None = None,
        view: LambdaView | None = None,
        core: LambdaAnalyzer | None = None,
        height: int | str | None = None,
        mode: str = "auto",
    ):
        if mode not in ("auto", "widgets", "text"):
            raise ValueError("mode must be 'auto', 'widgets' or 'text'")
        start_text = _event_text(event)
        super().__init__()
        if view is None:
            view = LambdaView(core or LambdaAnalyzer(region=region), mode="text" if mode == "text" else "auto",
                              progress="off")
        self.ui, self.core = view, view.core
        self.height = height
        self.ref, self.region = name, region
        self.function: Function | None = None
        self.qualifier: str | None = None
        self.runs: list[Invoked] = []
        self._tab = "test"
        self._start_text = start_text
        self._sent_last: str | None = None
        self._testing = 0.0
        self._shown_test: Invoked | None = None
        self._init_watch()
        note = self._load_widgets(mode)
        if self._w is None:
            self._without_window(note)
            return
        self._build()
        self._display()
        self._read_function()

    def __repr__(self) -> str:
        name = self.function.name if self.function is not None else self.ref
        return f"LambdaTester({name}) · help(LambdaTester) says what it does"

    def _without_window(self, note: str) -> None:
        """
        Without ipywidgets or Jupyter: says how to run it from a cell instead (nothing runs here).
        """
        text = note or "The test window needs Jupyter (SageMaker, JupyterLab or VS Code)."
        self.ui._show([_Note(f"{text} Here, ui.invoke({self.ref!r}, event) runs it once and shows the same report.",
                             "warn")])

    def _build(self) -> None:
        w, layout = self._w, self._w.Layout
        self.backdrop = w.Button(layout=layout(display="none"))
        self.title = w.HTML(layout=layout(flex="1 1 auto", min_width="0"))
        top = w.HBox([self.title], layout=layout(width="100%"))
        top.add_class("lmx-top")
        head = w.VBox([top], layout=layout(width="100%"))
        head.add_class("lmx-head")
        tabs = w.HBox(layout=layout(width="100%"))
        for name in ("lmx-tabs", "lmx-none"):
            tabs.add_class(name)
        self._theme = _theme_name()
        self._theme_style = _theme_css(_ROOT, self._theme)
        self._frame(_CSS + _EXPLORER_CSS + _TEST_CSS + self._theme_style, head, tabs, {"test": self._test_page()})
        self._draw_title()
        self.test_box.value = self._start_text
        self._test_typed()

    def _draw_title(self) -> None:
        fn = self.function
        if fn is None:
            sub = ["reading its settings…"]
        else:
            qualified = f"{fn.name}:{self.qualifier}" if self.qualifier else fn.name
            sub = [qualified, fn.runtime or "container image", f"{fn.memory:,} MB", f"{fn.timeout} s timeout",
                   fn.architecture or "", fn.region or "", _clip(fn.description, _DESCRIPTION_CHARS)]
        self._set(self.title, f'<div class="lmx-brand"><span class="lmx-logo">{_LOGO}</span><div style="min-width:0">'
                              f'<div class="lmx-name">Test {_esc(self.ref)}<span>run it with an event</span></div>'
                              f'<div class="lmx-sub">{_esc(" · ".join(p for p in sub if p))}</div></div></div>')

    def _test_page(self) -> Any:
        w, layout = self._w, self._w.Layout
        warn = w.HTML('<div class="lmx-tst-warn">⚠ It really runs: whatever its code writes or sends happens. '
                      "Paste an event, or start from one a service sends.</div>", layout=layout(width="100%"))
        self.tool_row = self._tool_row()
        self.test_box = w.Textarea(rows=_EDITOR_LINES, continuous_update=True,
                                   placeholder='Paste the event here, e.g. {"order_id": 42}', layout=layout(width="100%"))
        self.test_box.observe(self._safely(lambda _change: self._test_typed()), names="value")
        self.test_preview = w.HTML(layout=layout(width="100%"))
        self.test_preview.add_class("lmx-tst-pv")
        grid = w.Box([self.test_box, self.test_preview], layout=layout(width="100%"))
        grid.add_class("lmx-tst-grid")
        self.test_run_button = w.Button(description="▶ Run", tooltip=_TEST_TIP, layout=layout(width="auto"),
                                        disabled=True)
        self.test_run_button.add_class("lmx-invoke")
        self.test_run_button.on_click(self._safely(lambda _button: self._run_test()))
        self.dry_button = w.Button(description="Dry run", tooltip="Only check that you may invoke it: nothing runs",
                                   layout=layout(width="auto"), disabled=True)
        self.dry_button.add_class("lmx-small")
        self.dry_button.on_click(self._safely(lambda _button: self._run_test(dry_run=True)))
        self.test_said = w.HTML(layout=layout(flex="1 1 auto"))
        foot = w.HBox([self.test_run_button, self.dry_button, self.test_said], layout=layout(width="100%"))
        foot.add_class("lmx-tst-foot")
        editor = w.VBox([warn, self.tool_row, grid, foot], layout=layout(width="100%"))
        editor.add_class("lmx-tst")
        self.test_result = w.HTML(layout=layout(width="100%"))
        views = self._view_row()
        scroller = w.VBox([editor, views, self.test_result, self.logs_view], layout=layout(width="100%"))
        scroller.add_class("lmx-scroll")
        return w.VBox([scroller])

    def _tool_row(self) -> Any:
        """
        Start from…, { } Format, Compact and ✕ Clear on the left; the menu of earlier runs on the right.
        """
        w, layout = self._w, self._w.Layout
        senders = "; ".join(f"{sample.name}: {sample.sender}" for sample in EVENT_SAMPLES)
        options = [(_START_FROM, ""), (_LAST_SENT, _LAST_SENT), *((s.name, s.name) for s in EVENT_SAMPLES)]
        self.sample_pick = w.Dropdown(options=options, value="", layout=layout(width="auto"),
                                      tooltip=f"Put an event shaped like what a service sends in the box ({senders})")
        self.sample_pick.add_class("lmx-sort")
        self.sample_pick.observe(self._safely(self._sample_picked), names="value")
        tools = [self.sample_pick]
        for label, tip, act in (("{ } Format", "Indent the event as tidy JSON", self._format_test),
                                ("Compact", "Put the event on one line", self._compact_test),
                                ("✕ Clear", "Empty the box ({} is sent)", self._clear_test)):
            button = w.Button(description=label, tooltip=tip, layout=layout(width="auto"))
            button.add_class("lmx-small")
            button.on_click(self._safely(lambda _button, act=act: act()))
            tools.append(button)
        self.test_runs_pick = w.Dropdown(options=[("No runs yet", -1)], value=-1, tooltip="Show an earlier run again",
                                         layout=layout(width="auto", display="none"))
        self.test_runs_pick.add_class("lmx-sort")
        self.test_runs_pick.observe(self._safely(self._test_run_picked), names="value")
        row = w.HBox([*tools, w.HBox(layout=layout(flex="1 1 auto")), self.test_runs_pick],
                     layout=layout(width="100%"))
        row.add_class("lmx-tst-tools")
        return row

    def _show_tab(self, key: str) -> None:
        self._tab = key

    def _html(self, blocks: list[Any]) -> str:
        """
        Report blocks as the window shows them: without the Next steps, in the theme it opened with, and without the
        report and theme CSS each one carries (the window's style holds them).
        """
        html = _render_html(_for_window(blocks, _same_words), self.ui.max_rows, theme=self._theme)
        return html.replace(_CSS, "", 1).replace(self._theme_style, "", 1)

    def _read_function(self) -> None:
        """
        Reads the function's settings (GetFunction) in the background: its timeout, memory and log group, for the
        report. ▶ Run waits for it.
        """
        self._status(f"Reading {self.ref}'s settings…", busy=True, owner="function")
        self._later("function", self._located_function, self._got_function, self._function_failed, owner="function")

    def _located_function(self) -> tuple[Function, str | None]:
        _, qualifier, _ = self.core.locate(self.ref, self.region)
        return self.core.function(self.ref, region=self.region), qualifier

    def _got_function(self, found: tuple[Function, str | None]) -> None:
        self.function, self.qualifier = found
        self._draw_title()
        self._test_typed()
        self._status(f"Paste an event for {self.function.name}, or start from one a service sends · ▶ Run runs it "
                     "once")

    def _function_failed(self, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._set(self.test_result, self._html([_Note(f"Couldn't read {self.ref}: {text}", "warn")]))
        self._status(text, "warn")

    def _test_typed(self) -> None:
        """
        The box changed: redraw the preview and what Run will send.
        """
        text = self.test_box.value
        check = check_event(text)
        self._set(self.test_preview, _preview_html(text, check))
        if not self._testing:
            self._draw_test_check(check)

    def _draw_test_check(self, check: EventCheck) -> None:
        tone = "ok" if check.ok else "bad"
        mark = "✓" if check.ok else "✕"
        self._set(self.test_said, f'<span class="ic {tone}">{mark} {_esc(check.message)}</span>'
                                  f'<span class="in"> · {_esc(_TEST_NOTE)}</span>')
        can_run = check.ok and self.function is not None
        self.test_run_button.disabled = not can_run
        self.dry_button.disabled = not can_run

    def _sample_picked(self, change: dict[str, Any]) -> None:
        name = change["new"]
        if not name:
            return
        if name == _LAST_SENT:
            self.test_box.value = self._sent_last or "{}"
        else:
            self.test_box.value = sample_event(name)
        self._quietly(self.sample_pick, value="")

    def _format_test(self) -> None:
        check = check_event(self.test_box.value)
        if not check.ok:
            self._status(f"Can't format it: {check.message}", "warn")
            return
        self.test_box.value = check.pretty

    def _compact_test(self) -> None:
        self.test_box.value = _compact(self.test_box.value)

    def _clear_test(self) -> None:
        self.test_box.value = "{}"

    def _run_test(self, *, dry_run: bool = False) -> None:
        """
        Runs the function once with the box's event (in the background), saying how long it has run meanwhile.
        """
        fn = self.function
        if fn is None or self._testing:
            return
        payload = event_payload(self.test_box.value)
        self._sent_last = format_event(self.test_box.value)
        self._testing = time.monotonic()
        self.test_run_button.disabled = self.dry_button.disabled = True
        verb = "Checking that you may invoke" if dry_run else "Running"
        self._status(f"{verb} {fn.name}…", busy=True, owner="test")
        qualifier = self.qualifier
        self._later("test", lambda: self.core._invoke_function(fn, payload, qualifier=qualifier, dry_run=dry_run),
                    self._tested, self._test_failed, owner="test", ticking=self._draw_test_wait)
        self._draw_test_wait()

    def _draw_test_wait(self) -> None:
        if not self._testing or self.function is None:
            return
        seconds = time.monotonic() - self._testing
        timeout = self.function.timeout
        self._set(self.test_said, f'<span class="ic">⟳ running… {seconds:.1f} s of its {timeout} s timeout</span>')

    def _test_done(self) -> None:
        self._testing = 0.0
        self._draw_test_check(check_event(self.test_box.value))

    def _test_failed(self, exc: BaseException) -> None:
        self._test_done()
        text = self._error_text(exc)
        self._set(self.test_said, f'<span class="ic bad">✕ {_esc(text)}</span>')
        self._set(self.test_result, self._html([_Note(f"Couldn't invoke it: {text}", "warn")]))
        self._status(f"Couldn't invoke it: {text}", "warn")

    def _tested(self, invoked: Invoked) -> None:
        """
        A call came back: kept with the earlier runs and drawn in full under the box.
        """
        self._test_done()
        self.runs.append(invoked)
        self._draw_test_runs()
        self._show_test(invoked)
        self._show_view("result")
        self._status(self._tested_line(invoked), "" if invoked.ok else "warn")
        self._watch_after_run(invoked)

    def _tested_line(self, invoked: Invoked) -> str:
        name = invoked.function.name
        if invoked.dry_run:
            return f"You may invoke {name}: Lambda checked, and nothing ran · ▶ Run runs it"
        report = invoked.report
        took = f" in {human_ms(report.duration)}" if report is not None else ""
        failure = invoked.failure
        if failure is not None:
            what = f"failed with {failure.get('errorType') or 'an error'}"
        else:
            what = "ran" if invoked.ok else "failed"
        return f"{name} {what}{took} · what it returned and logged is below"

    def _show_test(self, invoked: Invoked) -> None:
        """
        One run's whole report under the event box.
        """
        fn = invoked.function

        def call(command: str, *args: Any, **kwargs: Any) -> str:
            return _call(command, fn.name, *args, **kwargs)

        self._draw("test", self.test_result, invoke_blocks(invoked, call=call))
        self._shown_test = invoked

    def _draw_test_runs(self) -> None:
        """
        The menu of earlier runs, newest first, picking the newest; hidden until there's one.
        """
        if not self.runs:
            self.test_runs_pick.layout.display = "none"
            return
        options = [(_test_run_label(number, invoked), number - 1) for number, invoked in enumerate(self.runs, start=1)]
        self._quietly(self.test_runs_pick, options=list(reversed(options)), value=len(self.runs) - 1)
        self.test_runs_pick.layout.display = ""

    def _test_run_picked(self, change: dict[str, Any]) -> None:
        index = change["new"]
        if index is None or not 0 <= index < len(self.runs):
            return
        invoked = self.runs[index]
        self._show_test(invoked)
        self.test_box.value = format_event(invoked.event.decode("utf-8", errors="replace"))
        self._status(f"Run {index + 1} of {invoked.function.name}, with the event it was sent in the box")

    @_window_errors
    def event(self, value: Any) -> None:
        """
        Puts an event in the box, as tidy JSON: a dict, a list or JSON text. Nothing runs.
        """
        if self._w is None:
            return
        self.test_box.value = _event_text(value)

    @_window_errors
    def run(self, event: Any = None) -> None:
        """
        Runs the function once with the box's event, as ▶ Run does; event= puts that event in the box first.
        """
        if self._w is None:
            return
        if event is not None:
            self.test_box.value = _event_text(event)
        check = check_event(self.test_box.value)
        if not check.ok:
            raise ValueError(check.message)
        if self.function is None:
            raise ValueError(f"{self.ref}'s settings are still being read: run it again in a moment")
        self._run_test()

    @_window_errors
    def watch(self, on: bool = True) -> None:
        """
        Watches the function's logs in Live logs (every few seconds, for up to 15 minutes), or stops (on=False).
        """
        if self._w is None:
            return
        if self.function is None:
            raise ValueError(f"{self.ref}'s settings are still being read: watch again in a moment")
        self._toggle_watch(on)
        if on:
            self._show_view("logs")
            self._status(f"Watching {self.function.name}'s logs every {_WATCH_SECONDS} seconds")
