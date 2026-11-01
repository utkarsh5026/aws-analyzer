"""
The chat window's Runs tab.
"""

from __future__ import annotations

from typing import Any

from ..._kit.blocks import _Cards, _Findings, _Next
from ..._kit.fmt import _plural, human_age
from ..._kit.render import _blocks_html, _call, _prose
from ..._kit.text import _clip, _esc

from ..helpers import RUNS_FILE
from ..analysis.batches import Batch
from ..analysis.ranking import run_score
from ..style import _CSS, _ROOT, _render_html
from ..blocks import _wrap


class _RunsTab:
    """
    The chat window's Runs tab: every test run, newest first, and Show, Use this setup, Compare, Save and Load.
    """

    # ------------------------------------------------------------------- Runs

    def _runs_tab(self) -> Any:
        """
        Runs: every test run, newest first, ranked against the others of the same questions; any one opened in
        the Test tab, switched to or compared, and all of them saved to a file or read back from one.
        """
        w, layout = self.w, self.w.Layout
        self.runs_view = w.HTML(layout=layout(width="100%"))
        self.run_pick = w.Dropdown(options=[], layout=layout(flex="1 1 100%", width="auto", min_width="0"))
        buttons = []
        for text, tip, handler in (
                ("Show", "Open this run in the Test tab: each question, opening to its answer", self._show_picked),
                ("Use this setup", "Switch the window to this run's settings, model, data source and files",
                 self._use_picked),
                ("⇄ Compare", "Every run of the same questions side by side, best first", self._compare_picked)):
            button = w.Button(description=text, tooltip=tip, layout=layout(width="auto", flex="0 0 auto",
                                                                          margin="6px 6px 0 0"))
            button.add_class("kbc-small")
            button.on_click(self._safely(handler))
            buttons.append(button)
        self.show_run_button, self.use_run_button, self.compare_button = buttons
        self.run_actions = w.VBox([self.run_pick, w.HBox(buttons, layout=layout(flex_flow="row wrap"))],
                                  layout=layout(width="100%", margin="8px 0 0 0"))
        self.compare_view = w.HTML(layout=layout(width="100%"))
        self.runs_file = w.Text(value=self.view.log or RUNS_FILE, placeholder=RUNS_FILE,
                                layout=layout(flex="1 1 auto", width="auto", min_width="0"))
        self.save_runs_button = save = w.Button(description="💾 Save", tooltip="Add every run to this file, a line "
                                                "each, and every later run as it finishes",
                                                layout=layout(width="auto", flex="0 0 auto", margin="0 0 0 6px"))
        save.add_class("kbc-small")
        save.on_click(self._safely(self._save_runs))
        self.load_runs_button = load = w.Button(description="📂 Load", tooltip="Read the runs in this file back in",
                                                layout=layout(width="auto", flex="0 0 auto", margin="0 0 0 6px"))
        load.add_class("kbc-small")
        load.on_click(self._safely(self._load_runs))
        self.runs_note = w.HTML(layout=layout(width="100%"))
        files = w.VBox([
            w.HTML(_wrap('<div class="ph">Keep them in a file<span class="hint">so they outlast a restart</span>'
                         '</div><div class="pd">One line per run: its setup, and each answer with its sources. '
                         "Load reads a file back in, yours from before or a teammate's.</div>")),
            w.HBox([self.runs_file, save, load], layout=layout(width="100%", align_items="center")),
            self.runs_note,
        ], layout=layout(width="100%"))
        files.add_class("kbc-card")
        head = w.HTML(_wrap('<div class="ph">Every test run<span class="hint">newest first · ranked against the other '
                            "runs of the same questions</span></div>"))
        return w.VBox([head, self.runs_view, self.run_actions, self.compare_view, files], layout=layout(width="100%"))

    def _tab_changed(self, change: dict[str, Any]) -> None:
        self._focus_moved()
        if change.get("new") == self.RUNS_TAB:  # drawn when it's looked at: "your setup now" follows the settings
            self._draw_runs()

    def _draw_runs(self) -> None:
        """
        Runs: the cards and findings of runs(), then a line per run (newest first) with its rank among the runs
        of its questions, opening to its setup; the run picker; and where the runs are saved.
        """
        view = self.view
        if not view.batches:
            self._set(self.runs_view, _wrap('<div class="more" style="margin:10px 0">No test runs yet: ask a list of '
                                            "questions in the Test tab (Try variations asks several setups at once), "
                                            "or load runs saved before.</div>"))
            self.run_actions.layout.display = "none"
            self._set(self.compare_view, "")
        else:
            blocks = view._runs_blocks(memory=False)  # the file card under the list says what isn't saved
            cards = next(b for b in blocks if isinstance(b, _Cards))
            findings = next(b for b in blocks if isinstance(b, _Findings))
            places = view._places()
            lines = [self._run_line(b, places.get(id(b))) for b in reversed(view.batches)]
            self._set(self.runs_view, _wrap(f'<div class="tests">{_blocks_html([cards, findings], root=_ROOT)}</div>'
                                            f'<div class="bqs narrow">{"".join(lines)}</div>'))
            options = [(self._run_option(b), view._run_number(b)) for b in reversed(view.batches)]
            numbers = [number for _, number in options]
            self._quietly(self.run_pick, options=options,
                          value=self.run_pick.value if self.run_pick.value in numbers else numbers[0])
            self.run_actions.layout.display = ""
        if view.log and self.runs_file.value != view.log:
            self._quietly(self.runs_file, value=view.log)
        unsaved = len(view._unsaved(view.batches))
        if view.log:
            text = f"Every run is added to {view.log} as it finishes" + (
                f" ({unsaved:,} not saved yet: Save adds {'it' if unsaved == 1 else 'them'})." if unsaved else ".")
        elif view.batches:
            text = (f"{'This run is' if len(view.batches) == 1 else 'These runs are'} in this notebook's memory only: "
                    "Save keeps them, and every later run.")
        else:
            text = ""
        self._set(self.runs_note, _wrap(f'<div class="pd" style="margin:6px 0 0">{_esc(text)}</div>') if text else "")

    def _run_line(self, batch: Batch, place: tuple[int, int] | None) -> str:
        """
        One run in the Runs tab: number, name, rank among the runs of its questions, result, and opening to its
        whole setup.
        """
        view = self.view
        result, expected = view._result_text(batch)
        name = (f"sweep {view._sweep_number(batch)}: {batch.label}" if batch.sweep else batch.label) or \
            view._setup_text(batch)
        failed = sum(bool(i.error_code) for i in batch.items)
        tone = "bad" if failed else "ok" if place and place[0] == 1 else ""
        rank = f'<span class="pill{" " + tone if tone else ""}">{place[0]} of {place[1]}</span>' if place else ""
        stats = " · ".join(filter(None, [result, expected if expected != "-" else "",
                                         f"{failed} failed" if failed else "", view._money(run_score(batch).cost),
                                         human_age(batch.started)]))
        body = (f'<div class="be">{_esc(view._setup_text(batch))}</div>'
                f'<div class="pd">{_esc(_plural(len(batch.items), "question"))} · '
                f"{_prose(_call('results', view._run_number(batch)))} shows it as a report.</div>")
        css = f"bq {tone}" if tone else "bq"
        return (f'<details class="{css}"><summary><span class="bn">{view._run_number(batch)}</span>'
                f'<span class="bt">{_esc(name)}</span><span class="bx">{rank}<span class="bm">{_esc(stats)}</span>'
                f'</span></summary><div class="bb">{body}</div></details>')

    def _run_option(self, batch: Batch) -> str:
        view = self.view
        name = (f"sweep {view._sweep_number(batch)}: {batch.label}" if batch.sweep else batch.label) or \
            view._setup_text(batch)
        return f"Run {view._run_number(batch)} · {_clip(name, 60)} · {view._result_text(batch)[0]}"

    def _picked(self) -> Batch:
        return self.view._run(self.run_pick.value)

    def _show_picked(self, *_: Any) -> None:
        batch = self._picked()
        if self.running:
            self._set_status("A test run is going on: Show opens another one once it's done.")
            return
        self._show_run(batch)
        self.tabs.selected_index = self.TEST_TAB
        self._set_status(f"The Test tab shows {self.view._run_name(batch)}.")

    def _use_picked(self, *_: Any) -> None:
        self._set_status(self.view._switch_to(self._picked()), "ok")
        self._draw_runs()

    def _compare_picked(self, *_: Any) -> None:
        batch = self._picked()
        family = self.view._family(batch)
        if len(family) < 2:
            self._set(self.compare_view, _wrap(
                f'<div class="note info">Only run {self.view._run_number(batch)} asked these questions this way, so '
                "there's nothing to compare it with yet: change a setting and run them again, or open Try variations "
                "in the Test tab.</div>"))
            return
        shown = [b for b in self.view._compare_blocks(family, memory=False) if not isinstance(b, _Next)]
        self._set(self.compare_view, f'<div class="tests" style="margin-top:10px">'
                                     f'{_render_html(shown, 0).removeprefix(_CSS)}</div>')

    def _save_runs(self, *_: Any) -> None:
        note = self.view._save(self.runs_file.value)
        self._draw_runs()
        self._set(self.runs_note, _wrap(f'<div class="note {note.level}">{_prose(note.text)}</div>'))

    def _load_runs(self, *_: Any) -> None:
        notes = self.view._load(self.runs_file.value)
        self._set(self.runs_note, _wrap("".join(f'<div class="note {n.level}">{_prose(n.text)}</div>' for n in notes)))
