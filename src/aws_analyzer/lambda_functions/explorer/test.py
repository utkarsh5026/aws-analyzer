"""
The Test tab: the Lambda console's Test page in the window. Paste an event (or start from one shaped like what S3,
SQS, API Gateway, SNS or EventBridge send), see it as tidy coloured JSON beside the box, with the line and column where
it breaks when it isn't valid, then ▶ Run runs the function once with it. What comes back is drawn below in full:
whether it worked, what it returned, its run time, memory and cost, every line it logged, and everything else Lambda
returned (request ID, version, X-Ray trace, billed and start-up times, headers). Each run is kept for the session, so
an earlier one can be shown again, and it joins the Logs tab's runs as one "you ran".
"""

from __future__ import annotations

import json
import time
from typing import Any

from ..._kit.blocks import _Note
from ..._kit.fmt import _fmt_dt
from ..._kit.highlight import _json_source_html
from ..._kit.render import _call
from ..._kit.text import _esc

from ..helpers import human_ms
from ..analysis.invoke import EVENT_SAMPLES, EventCheck, Invoked, check_event, event_payload, format_event, sample_event
from ..blocks.invoke import invoke_blocks

_EDITOR_LINES = 12

_PREVIEW_LINES = 400

_TEST_TIP = ("Runs the function once with this event and waits for it, like the Lambda console's Test button. It "
             "really runs: whatever its code writes or sends happens")

_TEST_NOTE = "Runs it for real, once (never retried) · needs lambda:InvokeFunction"

_START_FROM = "Start from…"

_LAST_SENT = "The event sent last"

_TEST_CSS = """<style>
.lmx-app .lmx-tst{gap:8px;padding:2px 2px 8px}
.lmx-app .lmx-tst-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.lmx-app .lmx-tst-head .tt{font-weight:700;font-size:14px}
.lmx-app .lmx-tst-head .ts{opacity:.65;font-size:12px}
.lmx-app .lmx-tst-head .tw{font-size:11.5px;color:var(--lx-ink-warn)}
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
.lmx-app .lmx-tst-foot{align-items:center;gap:8px}
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


class _TestTab:
    """
    The Test tab of LambdaExplorer (a mixin). `self._tests` is function ARN -> the calls made from it this session,
    oldest first, `self._test_texts` function ARN -> what its event box holds, and `self._testing` when the run under
    way was sent (time.monotonic(), 0 while none is).
    """

    def _test_page(self) -> Any:
        w, layout = self._w, self._w.Layout
        self.test_head = w.HTML(layout=layout(width="100%"))
        senders = "; ".join(f"{sample.name}: {sample.sender}" for sample in EVENT_SAMPLES)
        self.sample_pick = w.Dropdown(options=self._sample_options(), value="", tooltip="Put an event shaped like "
                                      f"what a service sends in the box ({senders})", layout=layout(width="auto"))
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
        tool_row = w.HBox([*tools, w.HBox(layout=layout(flex="1 1 auto")), self.test_runs_pick],
                          layout=layout(width="100%"))
        tool_row.add_class("lmx-tst-tools")
        self.test_box = w.Textarea(rows=_EDITOR_LINES, continuous_update=True,
                                   placeholder='Paste the event here, e.g. {"order_id": 42}', layout=layout(width="100%"))
        self.test_box.observe(self._safely(lambda _change: self._test_typed()), names="value")
        self.test_preview = w.HTML(layout=layout(width="100%"))
        self.test_preview.add_class("lmx-tst-pv")
        grid = w.Box([self.test_box, self.test_preview], layout=layout(width="100%"))
        grid.add_class("lmx-tst-grid")
        self.test_run_button = w.Button(description="▶ Run", tooltip=_TEST_TIP, layout=layout(width="auto"))
        self.test_run_button.add_class("lmx-invoke")
        self.test_run_button.on_click(self._safely(lambda _button: self._run_test()))
        dry = w.Button(description="Dry run", tooltip="Only check that you may invoke it: nothing runs",
                       layout=layout(width="auto"))
        dry.add_class("lmx-small")
        dry.on_click(self._safely(lambda _button: self._run_test(dry_run=True)))
        self.test_logs_button = w.Button(description="Open this run in the Logs tab", layout=layout(
            width="auto", display="none"), tooltip="The run among its other runs, read from CloudWatch Logs")
        self.test_logs_button.add_class("lmx-small")
        self.test_logs_button.on_click(self._safely(lambda _button: self._test_in_logs()))
        self.test_said = w.HTML(layout=layout(flex="1 1 auto"))
        foot = w.HBox([self.test_run_button, dry, self.test_said, self.test_logs_button], layout=layout(width="100%"))
        foot.add_class("lmx-tst-foot")
        editor = w.VBox([self.test_head, tool_row, grid, foot], layout=layout(width="100%"))
        editor.add_class("lmx-tst")
        self.test_result = w.HTML(layout=layout(width="100%"))
        return self._scroll_page([editor, self.test_result])

    def _sample_options(self) -> list[tuple[str, str]]:
        samples = [(sample.name, sample.name) for sample in EVENT_SAMPLES]
        return [(_START_FROM, ""), (_LAST_SENT, _LAST_SENT), *samples]

    def _load_test(self) -> None:
        """
        The Test tab for the function picked: its event box as it was left (else the event sent last from the Logs
        tab, else {}), and its last run.
        """
        fn = self.function
        if fn is None:
            return
        self._asked.add("test")
        self._set(self.test_head, f'<div class="lmx-tst-head"><span class="tt">Test {_esc(fn.name)}</span>'
                                  '<span class="ts">paste an event, or start from one a service sends · ▶ Run runs '
                                  'it once and shows everything that came back</span>'
                                  '<span class="tw">⚠ it really runs: what its code writes or sends happens</span>'
                                  "</div>")
        text = self._test_texts.get(fn.arn)
        if text is None:
            text = format_event(self._sent_events.get(fn.arn) or "{}")
        self.test_box.value = text
        self._test_typed()
        self._draw_test_runs()
        runs = self._function_tests()
        if runs:
            self._show_test(runs[-1])
        else:
            self._set(self.test_result, "")
            self.test_logs_button.layout.display = "none"
            self.shown.pop("test", None)
            self._shown_test = None

    def _test_typed(self) -> None:
        """
        The box changed: keep what it holds for the function, and redraw the preview and what Run will send.
        """
        fn = self.function
        if fn is None:
            return
        text = self.test_box.value
        self._test_texts[fn.arn] = text
        check = check_event(text)
        self._set(self.test_preview, _preview_html(text, check))
        if self._testing:
            return
        self._draw_test_check(check)

    def _draw_test_check(self, check: EventCheck) -> None:
        tone = "ok" if check.ok else "bad"
        mark = "✓" if check.ok else "✕"
        self._set(self.test_said, f'<span class="ic {tone}">{mark} {_esc(check.message)}</span>'
                                  f'<span class="in"> · {_esc(_TEST_NOTE)}</span>')
        self.test_run_button.disabled = not check.ok

    def _sample_picked(self, change: dict[str, Any]) -> None:
        name = change["new"]
        if not name or self.function is None:
            return
        if name == _LAST_SENT:
            sent = self._sent_events.get(self.function.arn)
            self.test_box.value = format_event(sent) if sent else "{}"
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
        self._sent_events[fn.arn] = format_event(self.test_box.value)
        full = self._full()
        self._testing = time.monotonic()
        self.test_run_button.disabled = True
        verb = "Checking that you may invoke" if dry_run else "Running"
        self._status(f"{verb} {fn.name}…", busy=True, owner="test")
        self._later("test", lambda: self.core._invoke_function(full, payload, dry_run=dry_run),
                    self._tested, self._test_failed, owner="test", ticking=self._draw_test_wait)
        self._draw_test_wait()

    def _draw_test_wait(self) -> None:
        if not self._testing or self.function is None:
            return
        seconds = time.monotonic() - self._testing
        timeout = self._full().timeout
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
        A call came back: kept with the function's other runs, drawn in full, and added to the Logs tab's runs.
        """
        self._test_done()
        fn = invoked.function
        self._tests.setdefault(fn.arn, []).append(invoked)
        if fn.arn != (self.function.arn if self.function is not None else None):
            return
        self._draw_test_runs()
        self._show_test(invoked)
        if not invoked.dry_run and invoked.run is not None:
            self._join_logs(invoked)
        self._status(self._tested_line(invoked), "" if invoked.ok else "warn")

    def _tested_line(self, invoked: Invoked) -> str:
        name = invoked.function.name
        if invoked.dry_run:
            return f"You may invoke {name}: Lambda checked, and nothing ran · ▶ Run runs it"
        report = invoked.report
        took = f" in {human_ms(report.duration)}" if report is not None else ""
        failure = invoked.failure
        what = f"failed with {failure.get('errorType') or 'an error'}" if failure is not None else (
            "ran" if invoked.ok else "failed")
        return f"{name} {what}{took} · what it returned and logged is below"

    def _join_logs(self, invoked: Invoked) -> None:
        """
        Puts the run among the Logs tab's runs, as one you ran, redrawing them when they're already read.
        """
        self._mine[invoked.request_id] = invoked
        if "logs" not in self._asked:
            return
        self._runs = self._with_mine(self._runs)
        self._draw_run_chips()
        self._draw_runs()

    def _show_test(self, invoked: Invoked) -> None:
        """
        One run's whole report under the event box.
        """
        fn = invoked.function

        def call(command: str, *args: Any, **kwargs: Any) -> str:
            return _call(command, fn.name, *args, **kwargs)

        self._draw("test", self.test_result, invoke_blocks(invoked, call=call))
        self._renew("test")
        has_run = not invoked.dry_run and bool(invoked.request_id)
        self.test_logs_button.layout.display = "" if has_run else "none"
        self._shown_test = invoked

    def _function_tests(self) -> list[Invoked]:
        """
        The calls made from the Test tab to the function picked, oldest first.
        """
        if self.function is None:
            return []
        return self._tests.get(self.function.arn) or []

    def _draw_test_runs(self) -> None:
        """
        The menu of this function's earlier runs, newest first, picking the newest; hidden until there's one.
        """
        runs = self._function_tests()
        if not runs:
            self.test_runs_pick.layout.display = "none"
            return
        options = [(_test_run_label(number, invoked), number - 1) for number, invoked in enumerate(runs, start=1)]
        self._quietly(self.test_runs_pick, options=list(reversed(options)), value=len(runs) - 1)
        self.test_runs_pick.layout.display = ""

    def _test_run_picked(self, change: dict[str, Any]) -> None:
        fn = self.function
        index = change["new"]
        runs = self._function_tests()
        if fn is None or index is None or not 0 <= index < len(runs):
            return
        invoked = runs[index]
        self._show_test(invoked)
        self.test_box.value = format_event(invoked.event.decode("utf-8", errors="replace"))
        self._status(f"Run {index + 1} of {fn.name}, with the event it was sent in the box")

    def _test_in_logs(self) -> None:
        """
        The run shown, among the function's other runs in the Logs tab, opened, with any search there cleared so it
        shows.
        """
        invoked = self._shown_test
        run = invoked.run if invoked is not None else None
        if invoked is None or run is None:
            return
        self._mine[invoked.request_id] = invoked
        self._open_runs.add(run.key)
        if self._log_query or self.found is not None:
            self.log_find.value = ""
        self._run_chip, self._run_offset = "", 0
        already_read = "logs" in self._asked
        self._show_tab("logs")
        if already_read:
            self._runs = self._with_mine(self._runs)
            self._draw_run_chips()
            self._draw_runs(renew=True)

    def _test_line(self) -> str:
        """
        The status line on the Test tab.
        """
        fn = self.function
        if fn is None:
            return ""
        count = len(self._function_tests())
        earlier = f" · {count} run{'s' if count != 1 else ''} this session in the menu on the right" if count else ""
        return f"Paste an event for {fn.name}, or start from one a service sends · ▶ Run runs it once{earlier}"
