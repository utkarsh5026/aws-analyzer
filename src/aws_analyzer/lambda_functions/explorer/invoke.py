"""
The Logs tab's ▶ Invoke: a button beside Live that opens a box to write an event in, and runs the function once with
it. The run lands first among the runs, opened and marked "you ran", with what was sent, what came back and what to
change above its lines. An open run whose event is known (it logged it, or you sent it) gets ▶ Invoke with this
event, which puts that event in the box; nothing runs before ▶ Run is clicked.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from ..._kit.blocks import _Findings
from ..._kit.highlight import _json_source_html
from ..._kit.text import _clip, _esc
from ..._kit.window import _class_if

from ..helpers import human_ms
from ..analysis.invoke import Invoked, event_payload, invoke_findings
from ..analysis.logs import LogRun
from ..analysis.runs import logged_event

if TYPE_CHECKING:
    from .widgets import _RunRow

_INVOKE_TIP = ("Run this function once with an event you write, and see what it returned and logged right here. It "
               "really runs: whatever its code writes or sends happens")

_INVOKE_NOTE = "Runs it for real, once (never retried) · needs lambda:InvokeFunction"

_EVENT_LINES = 5

_ANSWER_CHARS = 4_000

_ONE_LINE_CHARS = 70

_INVOKE_CSS = """<style>
.lmx-app.lmx-app button.lmx-invoke{flex:0 0 auto;height:32px;padding:0 14px;border-radius:999px;font-weight:700;
border-color:var(--lx-accent);color:var(--lx-accent)}
.lmx-app.lmx-app button.lmx-invoke.lmx-on{background:var(--lx-soft)}
.lmx-app .lmx-ivk{border:1px solid var(--lx-accent);border-radius:10px;margin:8px 0 4px;padding:8px 10px;gap:6px}
.lmx-app .lmx-ivk-head{align-items:center;gap:8px}
.lmx-app .lmx-ivk-head .it{font-weight:700}
.lmx-app .lmx-ivk-head .is{opacity:.65;font-size:12px}
.lmx-app.lmx-app .lmx-ivk textarea{font-family:var(--lx-mono,monospace);font-size:12.5px;line-height:1.55;
border-radius:8px;min-height:72px}
.lmx-app .lmx-ivk-foot{align-items:center;gap:8px}
.lmx-app .lmx-ivk-foot .ic{font-size:12px}
.lmx-app .lmx-ivk-foot .ic.ok{color:var(--lx-ink-ok)}
.lmx-app .lmx-ivk-foot .ic.bad{color:var(--lx-ink-bad)}
.lmx-app .lmx-ivk-foot .in{font-size:11.5px;opacity:.6}
.lmx-app .rr .ry{font-size:10.5px;font-weight:700;color:#7c3aed;border:1px solid rgba(124,58,237,.4);
border-radius:6px;padding:0 5px;margin-right:6px}
.lmx-app .lmx-run.lmx-mine .rr{background:rgba(124,58,237,.06)}
.lmx-app.lmx-app button.lmx-chip.lmx-t-mine::before{background:#7c3aed}
.lmx-app .ivr{padding:6px 14px 8px 40px}
.lmx-app .ivr .ivg{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:6px}
.lmx-app .ivr .ivb{border:1px solid var(--lx-line);border-radius:8px;overflow:hidden;min-width:0}
.lmx-app .ivr .ivb b{display:block;font-size:11.5px;padding:4px 10px;border-bottom:1px solid var(--lx-line)}
.lmx-app .ivr .ivb b span{font-weight:400;opacity:.65;margin-left:6px}
.lmx-app .ivr .ivb pre{margin:0;padding:6px 10px;font-size:12px;white-space:pre-wrap;word-break:break-word;
max-height:220px;overflow:auto}
.lmx-app .ivr .ivb.bad b{color:var(--lx-ink-bad)}
</style>"""


def _event_check(text: str) -> tuple[str, str]:
    """
    (tone, what the event box says about its text): 'ok' with how it will be sent, or 'bad' with what's wrong.
    """
    try:
        payload = event_payload(text)
    except ValueError as exc:
        return "bad", f"✕ {exc}"
    size = f"{len(payload):,} bytes"
    stripped = text.strip()
    if not stripped:
        return "ok", "✓ an empty event: {} is sent"
    try:
        json.loads(stripped)
    except ValueError:
        how = "a Python dict, sent as JSON" if stripped.startswith(("{", "[")) else "text, sent as a JSON string"
        return "ok", f"✓ read as {how} · {size}"
    return "ok", f"✓ valid JSON · {size}"


def _pretty(payload: bytes) -> str:
    """
    Bytes sent or returned as JSON (on one line when it's short, else indented) when they are JSON, else as their
    text, cut to _ANSWER_CHARS.
    """
    text = payload.decode("utf-8", errors="replace")
    try:
        value = json.loads(text)
    except ValueError:
        return _clip(text, _ANSWER_CHARS)
    one_line = json.dumps(value, ensure_ascii=False)
    if len(one_line) <= _ONE_LINE_CHARS:
        return one_line
    return _clip(json.dumps(value, indent=2, ensure_ascii=False), _ANSWER_CHARS)


def _code_box(title: str, note: str, payload: bytes, *, tone: str = "") -> str:
    """
    One side of Sent / Returned: its title and a note, then the JSON in colour (or the text as it is).
    """
    text = _pretty(payload)
    try:
        json.loads(text)
        body = _json_source_html(text)
    except ValueError:
        body = _esc(text)
    return (f'<div class="ivb {tone}"><b>{_esc(title)}<span>{_esc(note)}</span></b>'
            f"<pre>{body or '&nbsp;'}</pre></div>")


def _returned_note(invoked: Invoked) -> str:
    """
    What the Returned box's title says: the error kind when it failed, else the status code.
    """
    failure = invoked.failure
    if failure is not None:
        return str(failure.get("errorType") or invoked.function_error or "error")
    return f"status {invoked.status_code}" if invoked.status_code else ""


def _invoked_html(invoked: Invoked, findings_html: str) -> str:
    """
    What sits above the lines of a run you invoked: the findings, then what was sent and what came back side by side.
    """
    sent = _code_box("Sent", "the event", invoked.event)
    returned = _code_box("Returned", _returned_note(invoked), invoked.payload or b"null",
                         tone="" if invoked.ok else "bad")
    return f'<div class="ivr">{findings_html}<div class="ivg">{sent}{returned}</div></div>'


def _known_event(run: LogRun, mine: Invoked | None) -> str | None:
    """
    The event a run was called with, as text for the event box: the one you sent, or the one it logged; None when
    neither is known.
    """
    if mine is not None:
        return _pretty(mine.event)
    logged = logged_event(run)
    if logged is None:
        return None
    return json.dumps(logged.event, indent=2, default=str, ensure_ascii=False)


class _InvokeBox:
    """
    The Logs tab's ▶ Invoke and its event box (a mixin of LambdaExplorer). `self._mine` is request ID -> Invoked
    for the runs invoked here for the function shown, `self._sent_events` function ARN -> the event text sent last,
    which the box starts with the next time, and `self._invoking` when the run under way was sent (time.monotonic(),
    0 while none is).
    """

    def _invoke_widgets(self) -> tuple[Any, Any]:
        """
        The ▶ Invoke button for the Logs tab's bar, and the event box it opens (hidden until then).
        """
        w, layout = self._w, self._w.Layout
        self.invoke_button = w.Button(description="▶ Invoke", tooltip=_INVOKE_TIP, layout=layout(width="auto"))
        self.invoke_button.add_class("lmx-invoke")
        self.invoke_button.on_click(self._safely(lambda _button: self._toggle_invoke()))
        self.invoke_title = w.HTML()
        close = w.Button(description="✕", tooltip="Close the event box", layout=layout(width="auto"))
        close.add_class("lmx-x")
        close.on_click(self._safely(lambda _button: self._toggle_invoke(False)))
        head = w.HBox([self.invoke_title, w.HBox(layout=layout(flex="1 1 auto")), close],
                      layout=layout(width="100%"))
        head.add_class("lmx-ivk-head")
        self.event_box = w.Textarea(rows=_EVENT_LINES, continuous_update=True, layout=layout(width="100%"))
        self.event_box.observe(self._safely(lambda _change: self._draw_event_check()), names="value")
        self.run_button = w.Button(description="▶ Run", tooltip="Run it once with this event",
                                   layout=layout(width="auto"))
        self.run_button.add_class("lmx-invoke")
        self.run_button.on_click(self._safely(lambda _button: self._run_invoke()))
        dry = w.Button(description="Dry run", tooltip="Only check that you may invoke it: nothing runs",
                       layout=layout(width="auto"))
        dry.add_class("lmx-small")
        dry.on_click(self._safely(lambda _button: self._run_invoke(dry_run=True)))
        self.event_said = w.HTML(layout=layout(flex="1 1 auto"))
        foot = w.HBox([self.run_button, dry, self.event_said], layout=layout(width="100%"))
        foot.add_class("lmx-ivk-foot")
        self.invoke_panel = w.VBox([head, self.event_box, foot], layout=layout(width="100%", display="none"))
        self.invoke_panel.add_class("lmx-ivk")
        return self.invoke_button, self.invoke_panel

    def _toggle_invoke(self, show: bool | None = None) -> None:
        """
        Opens the event box (with the event sent last, else the newest one a run logged, else {}), or closes it.
        """
        if self.function is None:
            return
        shown = self.invoke_panel.layout.display != "none"
        show = not shown if show is None else show
        self.invoke_panel.layout.display = "" if show else "none"
        _class_if(self.invoke_button, "lmx-on", show)
        if show and not shown:
            fn = self.function
            self._set(self.invoke_title, f'<span class="it">Invoke {_esc(fn.name)}</span>'
                                         '<span class="is"> with this event</span>')
            self.event_box.value = self._starting_event()
            self._draw_event_check()

    def _starting_event(self) -> str:
        """
        What the event box starts with: the event sent last to this function, else the newest event its runs
        logged, else {}.
        """
        fn = self.function
        sent = self._sent_events.get(fn.arn) if fn is not None else None
        if sent is not None:
            return sent
        for run in self._runs:
            known = _known_event(run, None)
            if known is not None:
                return known
        return "{}"

    def _draw_event_check(self) -> None:
        if self._invoking:
            return
        tone, said = _event_check(self.event_box.value)
        self._set(self.event_said, f'<span class="ic {tone}">{_esc(said)}</span>'
                                   f'<span class="in"> · {_esc(_INVOKE_NOTE)}</span>')
        self.run_button.disabled = tone == "bad"

    def _invoke_with(self, row: _RunRow) -> None:
        """
        ▶ Invoke with this event under an open run: puts its event in the box and opens it; nothing runs yet.
        """
        run = row.run
        if run is None:
            return
        event = _known_event(run, self._mine.get(run.request_id or ""))
        if event is None:
            return
        self._toggle_invoke(True)
        self.event_box.value = event
        self._status(f"The event of run {(run.request_id or '')[:8]} is in the box above · ▶ Run runs it")

    def _run_invoke(self, *, dry_run: bool = False) -> None:
        """
        Runs the function once with the box's event (in the background), saying how long it has run meanwhile.
        """
        fn = self.function
        if fn is None or self._invoking:
            return
        text = self.event_box.value
        payload = event_payload(text)
        self._sent_events[fn.arn] = _pretty(payload)
        full = self._full()
        self._invoking = time.monotonic()
        self.run_button.disabled = True
        verb = "Checking that you may invoke" if dry_run else "Running"
        self._status(f"{verb} {fn.name}…", busy=True, owner="invoke")
        self._later("invoke", lambda: self.core._invoke_function(full, payload, dry_run=dry_run),
                    self._invoked, self._invoke_failed, owner="invoke", ticking=self._draw_invoke_wait)
        self._draw_invoke_wait()

    def _draw_invoke_wait(self) -> None:
        """
        While it runs: how long so far, against its timeout.
        """
        if not self._invoking or self.function is None:
            return
        seconds = time.monotonic() - self._invoking
        timeout = self._full().timeout
        self._set(self.event_said, f'<span class="ic">⟳ running… {seconds:.1f} s of its {timeout} s timeout</span>')

    def _invoke_done(self) -> None:
        self._invoking = 0.0
        self._draw_event_check()

    def _invoke_failed(self, exc: BaseException) -> None:
        self._invoke_done()
        text = self._error_text(exc)
        self._set(self.event_said, f'<span class="ic bad">✕ {_esc(text)}</span>')
        self._status(f"Couldn't invoke it: {text}", "warn")

    def _invoked(self, invoked: Invoked) -> None:
        """
        A call came back: a dry run says so; a run joins the runs, first and opened, and a search that would hide it
        is cleared.
        """
        self._invoke_done()
        fn = invoked.function
        if invoked.dry_run:
            self._status(f"You may invoke {fn.name}: Lambda checked, and nothing ran · ▶ Run runs it", "ok")
            return
        run = invoked.run
        if run is None:
            self._status(f"{fn.name} {'ran' if invoked.ok else 'failed'}, and logged nothing that came back")
            return
        self._mine[invoked.request_id] = invoked
        self._runs = self._with_mine(self._runs)
        cleared = bool(self._log_query or self.found is not None)
        if cleared:
            self.log_find.value = ""
        self._open_runs.add(run.key)
        self._run_chip = self._run_chip if self._run_chip in ("", "mine") else ""
        self._run_offset = 0
        self._draw_run_chips()
        self._draw_runs(renew=True)
        line = self._invoked_line(invoked) + (" · the search was cleared to show it" if cleared else "")
        self._status(line, "" if invoked.ok else "warn")

    def _invoked_line(self, invoked: Invoked) -> str:
        """
        The status line after a run: how it went, and where to look.
        """
        report = invoked.report
        took = f" in {human_ms(report.duration)}" if report is not None else ""
        failure = invoked.failure
        if failure is not None:
            what = f"failed with {failure.get('errorType') or 'an error'}{took}"
        else:
            what = f"{'ran' if invoked.ok else 'failed'}{took}"
        return f"{invoked.function.name} {what} · your run is first below, opened"

    def _with_mine(self, runs: list[LogRun]) -> list[LogRun]:
        """
        The runs read, with the runs invoked here that they don't have (CloudWatch hasn't got them yet, or they're
        outside the time range), newest first.
        """
        held = {run.request_id for run in runs}
        missing = [mine.run for rid, mine in self._mine.items() if rid not in held and mine.run is not None]
        return sorted([*missing, *runs], key=lambda run: (run.start, run.end), reverse=True)

    def _is_mine(self, run: LogRun) -> bool:
        return bool(run.request_id) and run.request_id in self._mine

    def _mine_html(self, run: LogRun) -> str:
        """
        What an open run you invoked shows above its lines; '' for any other run.
        """
        invoked = self._mine.get(run.request_id or "")
        if invoked is None:
            return ""
        found = invoke_findings(invoked)
        return _invoked_html(invoked, self._html([_Findings(found)]) if found else "")

    def _draw_run_invoke(self, row: _RunRow, opened: bool) -> None:
        """
        ▶ Invoke with this event under an open run whose event is known; hidden otherwise. Also marks a run you
        invoked.
        """
        run = row.run
        mine = self._mine.get(run.request_id or "") if run is not None else None
        _class_if(row.box, "lmx-mine", mine is not None)
        known = opened and run is not None and _known_event(run, mine) is not None
        row.invoke_button.layout.display = "" if known else "none"
