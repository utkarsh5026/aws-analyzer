"""
The chat window's Test tab: test questions, Try variations, and runs drawn as they come back.
"""

from __future__ import annotations

import asyncio
import functools
import math
import queue
import re
import threading
from typing import Any, Callable, Iterable

from botocore.exceptions import BotoCoreError, ClientError

from ..._bedrock.prices import DEFAULT_MODEL, _MODEL_ALIASES
from ..._kit.blocks import _Cards, _Findings, _Next, _Note, _Title
from ..._kit.fmt import _duration, _plural, human_money
from ..._kit.render import _blocks_html, _prose
from ..._kit.text import _esc
from ..._kit.window import _running_loop

from ..helpers import BATCH_LIMIT, SWEEP_LIMIT, SWEEP_MAX_COST
from ..models import DataSource
from ..analysis.request import retrieve_settings
from ..analysis.batches import (
    Batch, BatchItem, _filled, _settings_text, batch_estimate, format_questions, parse_questions,
)
from ..analysis.sweeps import _PICKED, _setup_key, _sweep_name, apply_setup, parse_variations, run_setup, sweep_setups
from ..analysis.ranking import Sweep, _came_back, run_score
from ..style import _CSS, _ROOT, _render_html
from ..blocks import _result_html, _waiting_html, _wrap


class _TestTab:
    """
    The chat window's Test tab: the test questions, Try variations, running them in the background, and drawing
    a run or a sweep.
    """

    def _test_tab(self) -> Any:
        """
        Test: a list of questions asked with the window's setup, each on its own, and how each did; with Try
        variations open, with every combination of the values typed there, the setups ranked.
        """
        w, layout = self.w, self.w.Layout
        self.test_box = w.Textarea(value=format_questions(self.view.questions), rows=7, continuous_update=True,
                                   placeholder="How long do refunds take? | refund-policy.pdf\nCan I return a digital "
                                               "product?\nWhat does error E1234 mean?", layout=layout(width="100%"))
        self.test_box.observe(self._safely(self._tests_typed), names="value")
        # Try variations stays folded under one button until it's wanted, like Add a setting
        self.vary_button = w.Button(description="+ Try variations", tooltip="Ask the questions with several "
                                    "settings, models or data sources at once, and see which setup does best",
                                    layout=layout(width="100%", margin="6px 0 0 0"))
        self.vary_button.add_class("kbc-add")
        self.vary_button.on_click(self._safely(lambda _button: self._show_varying(True)))
        self.close_vary = close = w.Button(description="✕", tooltip="Close Try variations: Run asks with one setup "
                                           "again", layout=layout(width="22px", flex="0 0 auto"))
        close.add_class("kbc-x")
        close.on_click(self._safely(lambda _button: self._show_varying(False)))
        self.vary_chip_box = w.HBox(layout=layout(width="100%", flex_flow="row wrap", margin="6px 0 2px 0"))
        self.vary_box = w.Textarea(rows=4, continuous_update=True, layout=layout(width="100%"),
                                   placeholder="n = 5, 10\nsearch_type = SEMANTIC, HYBRID\nmodel = haiku, sonnet")
        self.vary_box.add_class("kbc-mono")
        self.vary_box.observe(self._safely(self._tests_typed), names="value")
        self.vary_note = w.HTML(layout=layout(width="100%"))
        self.varying_card = w.VBox([
            w.HBox([w.HTML(_wrap('<div class="ph">Try variations</div>'), layout=layout(flex="1 1 auto")), close],
                   layout=layout(width="100%", align_items="center")),
            w.HTML(_wrap('<div class="pd">A setting per line, then = and the values to try, separated by commas: Run '
                         "asks the questions with every combination and ranks the setups. <b>none</b> leaves a "
                         "setting out; <b>model</b>, <b>data_source</b> and <b>files</b> work too.</div>")),
            self.vary_chip_box, self.vary_box, self.vary_note,
        ], layout=layout(width="100%", display="none"))
        self.varying_card.add_class("kbc-card")
        self.run_button = w.Button(description="▶ Run", button_style="primary", tooltip="Ask every question with "
                                   "these settings, each on its own (not as a follow-up)",
                                   layout=layout(width="auto", flex="0 0 auto"))
        self.run_button.on_click(self._safely(self._run_tests))
        self.stop_button = w.Button(description="■ Stop", tooltip="Send no more questions: the ones already sent "
                                    "finish", layout=layout(width="auto", flex="0 0 auto", margin="0 0 0 6px",
                                                            display="none"))
        self.stop_button.on_click(self._safely(self._stop_tests))
        self.test_note = w.HTML(layout=layout(flex="1 1 auto", min_width="0", margin="0 0 0 10px"))
        actions = w.HBox([self.run_button, self.stop_button, self.test_note],
                         layout=layout(width="100%", align_items="center", margin="6px 0 4px 0"))
        # a sweep: its ranking, then which setup's answers show under it
        self.sweep_head = w.HTML(layout=layout(width="100%"))
        self.setup_pick = w.Dropdown(options=[], layout=layout(flex="1 1 auto", width="auto", min_width="0"))
        self.setup_pick.observe(self._safely(self._setup_picked), names="value")
        self.use_setup_button = use = w.Button(description="Use this setup", tooltip="Switch the window to this "
                                               "setup: its settings, model, data source and files",
                                               layout=layout(width="auto", flex="0 0 auto", margin="0 0 0 6px"))
        use.add_class("kbc-small")
        use.on_click(self._safely(self._use_shown))
        self.sweep_bar = w.HBox([w.HTML(_wrap('<div class="pd" style="margin:0 8px 0 0">Answers of</div>'),
                                        layout=layout(flex="0 0 auto")), self.setup_pick, use],
                                layout=layout(width="100%", align_items="center", margin="10px 0 0 0", display="none"))
        self.test_head = w.HTML(layout=layout(width="100%"))
        self.test_rows = w.VBox(layout=layout(width="100%"))
        intro = w.HTML(_wrap(
            '<div class="ph">Test a list of questions<span class="hint">each asked on its own</span></div>'
            '<div class="pd">One question per line, asked with the knowledge base, model and settings above. Add '
            "<b>|</b> and a file name to check that the answer cites it. Change a setting and run them again to see "
            "which did better, or open <b>Try variations</b> to ask with several settings at once.</div>"))
        return w.VBox([intro, self.test_box, self.vary_button, self.varying_card, actions, self.sweep_head,
                       self.sweep_bar, self.test_head, self.test_rows], layout=layout(width="100%"))

    # ------------------------------------------------- test questions and code

    def _tests_typed(self, change: dict[str, Any]) -> None:
        if self.quiet:
            return
        self.confirm = ""  # a sweep confirmed for its cost was another one
        self._draw_test_note()
        self._render_code()  # the script asks the test questions

    # ------------------------------------------------------------ Try variations

    def _show_varying(self, show: bool) -> None:
        """
        Opens Try variations in place of its button (a starting pair of lines in an empty box), or folds it away:
        Run then asks with one setup again. What's in the box stays for next time.
        """
        self.varying_card.layout.display = "" if show else "none"
        self.vary_button.layout.display = "none" if show else ""
        if show:
            self._draw_vary_chips()
            if not self.vary_box.value.strip():
                self._quietly(self.vary_box, value="\n".join([self._variation_line("n"),
                                                               self._variation_line("search_type")]))
            if hasattr(self.vary_box, "focus"):  # ipywidgets 8
                self.vary_box.focus()
        self.confirm = ""
        self._draw_test_note()

    def _varying(self) -> bool:
        """
        Run asks every combination: Try variations is open and has something in it.
        """
        return self.varying_card.layout.display != "none" and bool(self.vary_box.value.strip())

    def _vary_key(self, name: str) -> str:
        """
        The name a variation line's setting goes by: 'Passages' and 'numberOfResults' -> 'n'.
        """
        key = _sweep_name(name)
        if key in _PICKED:
            return key
        try:
            return self.schema.find(key).key
        except ValueError:
            return key

    def _variation_line(self, key: str) -> str:
        """
        A line to start from, for a chip: 'n = 5, 10', 'model = haiku, sonnet', 'data_source = all, faq, manuals'.
        """
        view = self.view
        if key == "n":
            n = int(view.values.get("n") or 5)
            return f"n = {n}, {min(100, n * 2)}" if n < 100 else "n = 50, 100"
        if key == "model":
            wanted = str(view.model or view.core.default_model or DEFAULT_MODEL).lower()
            now = next((alias for alias, family in _MODEL_ALIASES.items() if family in wanted), "haiku")
            return f"model = {now}, {'sonnet' if now != 'sonnet' else 'haiku'}"
        if key == "data_source":
            names = [s.name or s.id for s in self._data_sources()]
            return "data_source = " + ", ".join(["all", *(f'"{n}"' if "," in n else n for n in names[:4])])
        return {"search_type": "search_type = SEMANTIC, HYBRID", "reranker": "reranker = none, cohere",
                "temperature": "temperature = 0, 0.5"}.get(key, f"{key} = ")

    def _data_sources(self) -> list[DataSource]:
        try:
            return self.view.core.data_sources(self.view._kb_id())
        except (ValueError, ClientError, BotoCoreError):
            return []

    def _draw_vary_chips(self) -> None:
        """
        One click adds a line of values to try: passages, search type, reranker, model and temperature (answers
        only), and the data source when the knowledge base has more than one.
        """
        chips = [("n", "Passages", "How many passages to retrieve"), ("search_type", "Search type", "SEMANTIC "
                 "(meaning) or HYBRID (meaning and exact words)"), ("reranker", "Reranker", "Without and with "
                 "reranking the passages")]
        if not self.view.retrieve_only:
            chips += [("model", "Model", "The model that answers"), ("temperature", "Temperature", "How much the "
                      "answers vary")]
        if len(self._data_sources()) > 1:
            chips.append(("data_source", "Data source", "Every data source, then each one alone"))
        shown = []
        for key, label, tip in chips:
            if key not in self.vary_chips:
                chip = self.w.Button(description=f"+ {label}", tooltip=tip, layout=self.w.Layout(width="auto"))
                chip.add_class("kbc-chip")
                chip.on_click(self._safely(lambda _button, key=key: self._add_variation(key)))
                self.vary_chips[key] = chip
            shown.append(self.vary_chips[key])
        self.vary_chip_box.children = shown

    def _add_variation(self, key: str) -> None:
        """
        A chip's line: it takes the place of the line for the same setting, or goes at the end.
        """
        line = self._variation_line(key)
        lines = [text for text in self.vary_box.value.splitlines() if text.strip()]
        same = [k for k, text in enumerate(lines) if self._vary_key(re.split(r"[=:]", text, maxsplit=1)[0]) == key]
        if same:
            lines[same[0]] = line
        else:
            lines.append(line)
        self.vary_box.value = "\n".join(lines)  # its observer redraws the note

    def _plan(self, cases: list[tuple[str, Any]]) -> tuple[int, float | None, bool]:
        """
        What Try variations asks, without building a request: (how many different setups, roughly what asking the
        questions with each costs, whether the model varies). A ValueError says what's wrong with a line.
        """
        view = self.view
        retrieve = view.retrieve_only
        grid = parse_variations(self.vary_box.value)
        count = math.prod(len(values) for values in grid.values())
        if count > SWEEP_LIMIT:
            raise ValueError(f"That's {count} setups, and Try variations asks up to {SWEEP_LIMIT} at a time: try fewer "
                             "values.")
        setups = sweep_setups(grid, limit=None) if count > 1 else []
        questions = [q for q, _ in cases[:BATCH_LIMIT]]
        seen: set[str] = set()
        models: set[str] = set()
        total: float | None = 0.0
        for changes in setups:
            sent = apply_setup(view.values, changes, self.schema)
            sent = retrieve_settings(sent, self.schema) if retrieve else sent
            model = "" if retrieve else str((changes["model"] if "model" in changes else view.model)
                                            or view.core.default_model or DEFAULT_MODEL)
            model = _MODEL_ALIASES.get(model.lower(), model)  # 'sonnet' -> 'claude-sonnet-5', which has a price
            key = _setup_key([sent, model, changes.get("data_source"), changes.get("files")])
            if key in seen:
                continue
            seen.add(key)
            models.add(model)
            cost = batch_estimate(questions, sent, model, retrieve_only=retrieve, model_prices=view.core.model_prices,
                                  prices=view.core.prices)
            total = None if total is None or cost is None else total + cost
        if len(seen) < 2:
            raise ValueError("Those come out as one setup" + (" (Retrieve only sends the search settings alone)"
                                                              if retrieve else "")
                             + ": give two or more values to try, like n = 5, 10.")
        return len(seen), total, len(models) > 1

    def _draw_test_note(self) -> None:
        """
        Beside Run: how many questions it asks (with how many setups), how (answers from which model, or searches
        only), and roughly what that costs.
        """
        if self.running:
            return
        cases = parse_questions(self.test_box.value)
        count = min(len(cases), BATCH_LIMIT)
        self.run_button.description = f"▶ Run {_plural(count, 'question')}" if count else "▶ Run"
        self.run_button.disabled = not count
        self._set(self.vary_note, "")
        if not cases:
            self._set(self.test_note, _wrap('<div class="pd" style="margin:0">Type or paste questions above.</div>'))
            return
        view = self.view
        retrieve = view.retrieve_only
        model = str(view.model or view.core.default_model or DEFAULT_MODEL)
        first = f"the first {count} of {len(cases)}" if len(cases) > count else ""
        if self._varying():
            try:
                setups, cost, models = self._plan(cases)
            except ValueError as exc:
                self.run_button.disabled = True
                self._set(self.vary_note, _wrap(f'<div class="note warn">{_prose(str(exc))}</div>'))
                self._set(self.test_note, _wrap('<div class="pd" style="margin:0">Fix the line above, or close Try '
                                                "variations to ask with one setup.</div>"))
                return
            self.run_button.description = f"▶ Run {setups} setups × {_plural(count, 'question')}"
            if self.confirm:
                self.run_button.description = "▶ Run anyway" + (f" (about {human_money(cost)})" if cost else "")
            parts = [first, f"{setups * count:,} calls",
                     "searches only (Retrieve only)" if retrieve else "" if models else view._model_label(model),
                     f"about {human_money(cost)} (estimate)" if cost is not None else "cost unknown"]
            self._set(self.vary_note, _wrap(f'<div class="pd" style="margin:4px 0 0">{setups} setups: every '
                                            "combination of these values, each starting from the settings in use."
                                            "</div>"))
        else:
            cost = batch_estimate([q for q, _ in cases[:count]],
                                  retrieve_settings(view.values, self.schema) if retrieve else view.values, model,
                                  retrieve_only=retrieve, model_prices=view.core.model_prices, prices=view.core.prices)
            parts = [first, "searches only (Retrieve only)" if retrieve else view._model_label(model),
                     f"about {human_money(cost)} (estimate)" if cost is not None else "cost unknown"]
        self._set(self.test_note, _wrap(f'<div class="pd" style="margin:0">{_esc(" · ".join(filter(None, parts)))}'
                                        "</div>"))

    # ------------------------------------------------------------ running them

    def _run_tests(self, *_: Any) -> None:
        """
        Run: every question in the box, asked with the window's setup (with Try variations open, with every
        combination), each on its own. In a notebook the questions are asked on worker threads while the window stays
        usable, and each line fills in as its answer comes back; elsewhere (a script, the tests) right away.
        """
        if self.running:
            return
        cases = parse_questions(self.test_box.value)
        if not cases:
            self._set_status("Type or paste questions in the Test tab first, one per line.")
            return
        if self._varying():
            self._run_sweep(cases)
            return
        view = self.view
        batch = view._prepare(cases, view.retrieve_only, BATCH_LIMIT)
        self.sweep = None
        self._hide_sweep()
        self.batch, self.batch_old, self.running = batch, view._old_items(batch), True
        self.batch_stop = stop = threading.Event()
        self._show_stop()
        self._show_progress(batch)
        self._draw_batch(batch)
        self._render_code()

        def work(on_item: Callable[[BatchItem], None]) -> Any:
            return view.core._run_batch(batch, stop=stop, on_item=on_item)

        self._start(work, self._tested, lambda: self._tests_done(batch, view._log_runs([batch])))

    def _run_sweep(self, cases: list[tuple[str, Any]]) -> None:
        """
        Run with Try variations open: the sweep checked (a costly one asks for a second click), then asked.
        """
        view = self.view
        sweep = view._prepare_sweep(cases, parse_variations(self.vary_box.value), retrieve_only=view.retrieve_only,
                                    limit=BATCH_LIMIT)
        cost = view._sweep_cost(sweep)
        plan = _setup_key([[run_setup(b) for b in sweep.batches], [i.question for i in sweep.batches[0].items]])
        over = view._too_costly(sweep, cost, SWEEP_MAX_COST)
        if over and self.confirm != plan:
            self.confirm = plan
            self._draw_test_note()
            self._set_status(f"{over}, more than the ${SWEEP_MAX_COST:.2f} the window asks without checking: click Run "
                             "again to ask them, or try fewer values or questions.", "warn")
            return
        self.confirm = ""
        view._keep(sweep, cases)
        self.sweep, self.batch, self.batch_old, self.running = sweep, None, {}, True
        self.batch_stop = stop = threading.Event()
        self._show_stop()
        self._draw_sweep(sweep)
        self._render_code()

        def work(on_item: Callable[[BatchItem], None]) -> Any:
            return view.core._run_sweep(sweep, stop=stop, on_item=on_item)

        self._start(work, self._swept_item, lambda: self._sweep_done(sweep, view._log_runs(sweep.batches)))

    def _show_stop(self) -> None:
        self.stop_button.disabled, self.stop_button.description = False, "■ Stop"
        self.stop_button.layout.display = ""

    def _start(self, work: Callable[[Callable[[BatchItem], None]], Any], tested: Callable[[BatchItem], None],
               done: Callable[[], None]) -> None:
        """
        Runs `work` (which takes the on_item callback): in a notebook on a worker thread, drawing each question on
        the kernel's event loop as it comes back; without a running loop, right away.
        """
        loop = _running_loop()
        if loop is None:
            try:
                work(tested)
            finally:
                done()
            return
        self.batch_task = loop.create_task(self._run_later(work, tested, done))

    async def _run_later(self, work: Callable[[Callable[[BatchItem], None]], Any],
                         tested: Callable[[BatchItem], None], done: Callable[[], None]) -> None:
        """
        A test run on a worker thread: each question's line is drawn on the kernel's event loop, where the clicks
        run, as its answer comes back, so Stop and the rest of the window keep working meanwhile.
        """
        finished: queue.SimpleQueue = queue.SimpleQueue()
        job = asyncio.get_running_loop().run_in_executor(None, functools.partial(work, finished.put))
        try:
            while not job.done():
                await asyncio.wait([job], timeout=0.2)
                self._drain(finished, tested)
            await job
        except Exception as exc:  # a bug: still said in the window, and not in the kernel's log
            self._set_status(self._error_text(exc), "warn")
        finally:
            self._drain(finished, tested)
            self._safely(done)()

    def _drain(self, finished: queue.SimpleQueue, tested: Callable[[BatchItem], None]) -> None:
        while True:
            try:
                item = finished.get_nowait()
            except queue.Empty:
                return
            self._safely(tested)(item)

    def _show_progress(self, batch: Batch) -> None:
        self._show_count([batch], batch.retrieve_only)

    def _show_count(self, batches: list[Batch], retrieve_only: bool) -> None:
        sent = [i for b in batches for i in b.items if i.request]
        back = sum(_came_back(i) for i in sent)
        self.run_button.disabled = True
        self.run_button.description = f"{'Searching' if retrieve_only else 'Asking'}… {back} of {len(sent)}"

    def _tested(self, item: BatchItem) -> None:
        """
        A test question came back: its line shows how it did, and Run how many are back.
        """
        batch = self.batch
        index = next((k for k, i in enumerate(batch.items) if i is item), None) if batch is not None else None
        if index is None or index >= len(self.batch_rows):
            return
        row = self.view._result_row(index + 1, item, self.batch_old)
        self._set(self.batch_rows[index], _wrap(f'<div class="bqs narrow">{_result_html(row)}</div>'))
        self._show_progress(batch)

    def _swept_item(self, item: BatchItem) -> None:
        """
        A question of the sweep came back: its setup's line counts it, and Run how many are back.
        """
        if self.sweep is not None and self.running:
            self._draw_sweep(self.sweep)

    def _stop_tests(self, *_: Any) -> None:
        if self.running and self.batch_stop is not None:
            self.batch_stop.set()
            self.stop_button.disabled, self.stop_button.description = True, "Stopping…"
            self._set_status("Stopping: no more questions are sent, and the ones already sent finish first.")

    def _tests_done(self, batch: Batch, notes: Iterable[_Note] = ()) -> None:
        """
        The run is over, or stopped: every line, the summary and findings, and Run again.
        """
        if batch is not self.batch:  # another run took the tab meanwhile (ask_all() in another cell)
            return
        self.running, self.batch_stop, self.batch_task = False, None, None
        self.stop_button.layout.display = "none"
        self._draw_batch(batch)
        self._draw_test_note()
        number = self.view._run_number(batch)
        line = (f"Test run {number}: {len(batch.asked)} of {_plural(len(batch.items), 'question')} came back in "
                f"{_duration(batch.seconds)} · {self.view._batch_cost_text(batch)} (estimated)")
        if batch.stopped:
            line += " · stopped before the rest were asked"
        line += (" · the Runs tab lists every run, and ui.results() shows this one as a report that stays in the "
                 "notebook.")
        problems = [n.text for n in notes]
        self._set_status(" ".join([line, *problems]), "warn" if batch.failed or problems else "ok")
        self._draw_runs()

    def _sweep_done(self, sweep: Sweep, notes: Iterable[_Note] = ()) -> None:
        """
        The sweep is over, or stopped: the setups ranked, the best one's answers under them, and Run again.
        """
        if sweep is not self.sweep:
            return
        self.running, self.batch_stop, self.batch_task = False, None, None
        self.stop_button.layout.display = "none"
        self._draw_sweep(sweep)
        self._draw_test_note()
        best = sweep.best
        view = self.view
        questions = len(sweep.batches[0].items)
        line = (f"Sweep {self._sweep_index(sweep)}: {len(sweep.batches)} setups × {_plural(questions, 'question')} in "
                f"{_duration(sweep.seconds)} · {view._money(sweep.cost)} (estimated)")
        if sweep.stopped:
            line += " · stopped before every setup was asked every question"
        if best is not None:
            line += f" · best: run {view._run_number(best)} ({best.label}), whose answers are below"
        problems = [n.text for n in notes]
        self._set_status(" ".join([line + ".", *problems]), "warn" if problems else "ok")
        self._draw_runs()

    # ------------------------------------------------------------ drawing them

    def _sweep_index(self, sweep: Sweep) -> int:
        return next((k for k, sw in enumerate(self.view.sweeps, 1) if sw is sweep), len(self.view.sweeps))

    def _show_run(self, batch: Batch) -> None:
        """
        A run in the Test tab: a sweep's run shows its sweep, with that setup's answers under the ranking.
        """
        sweep = next((sw for sw in self.view.sweeps if batch.sweep and sw.id == batch.sweep), None)
        if sweep is not None:
            self.sweep = sweep
            self._draw_sweep(sweep, batch)
            return
        self.sweep = None
        self._hide_sweep()
        self.batch, self.batch_old = batch, self.view._old_items(batch)
        self._draw_batch(batch)

    def _hide_sweep(self) -> None:
        self._set(self.sweep_head, "")
        self.sweep_bar.layout.display = "none"

    def _draw_sweep(self, sweep: Sweep, pick: Batch | None = None) -> None:
        """
        The Test tab's sweep: while it's asked, a line per setup with how many questions are back; then the setups
        ranked, what to do, how each question did with each, and a picker for the setup whose answers show below
        (`pick`, else the one shown, else the best).
        """
        view = self.view
        questions = len(sweep.batches[0].items)
        how = "searches only (Retrieve)" if sweep.retrieve_only else (
            "" if "model" in sweep.varied else view._model_label(sweep.batches[0].model))
        what = " · ".join(filter(None, [sweep.batches[0].kb_name or sweep.batches[0].kb_id, how,
                                        f"{len(sweep.batches)} setups × {_plural(questions, 'question')}"]))
        head = (f'<div class="ph" style="margin-top:12px">Sweep {self._sweep_index(sweep)}'
                f'<span class="hint">{_esc(what)}</span></div>')
        if self.running:
            lines = []
            for k, b in enumerate(sweep.batches, 1):
                sent = [i for i in b.items if i.request]
                back = sum(_came_back(i) for i in sent)
                spin = "" if back == len(sent) else '<span class="spin"></span>'
                state = f"{back} of {len(sent)} back" + (f" · {run_score(b).answered} "
                                                          f"{'found passages' if b.retrieve_only else 'answered'}"
                                                          if back else "")
                lines.append(f'<div class="bq wait"><span class="bn">{k}</span><span class="bt">{_esc(b.label)}</span>'
                             f'<span class="bx">{spin}<span class="bm">{_esc(state)}</span></span></div>')
            body = ('<div class="pd">Each question is asked with every setup before the next one, so Stop leaves '
                    "every setup with the same questions to compare.</div>"
                    f'<div class="bqs narrow">{"".join(lines)}</div>')
            self._set(self.sweep_head, _wrap(head + body))
            self.sweep_bar.layout.display = "none"
            self._set(self.test_head, "")
            self.test_rows.children = ()
            self._show_count(sweep.batches, sweep.retrieve_only)
            return
        shown = [b for b in view._compare_blocks(sweep.batches, sweep=sweep, memory=False)
                 if not isinstance(b, (_Title, _Next))]
        self._set(self.sweep_head, _wrap(head + f'<div class="tests">{_render_html(shown, 0).removeprefix(_CSS)}</div>'))
        ranked = sweep.ranked
        options = [(f"#{rank} · run {view._run_number(b)} · {b.label}", view._run_number(b))
                   for rank, (b, _) in enumerate(ranked, 1)]
        chosen = pick or (self.batch if any(self.batch is b for b in sweep.batches) else ranked[0][0])
        self._quietly(self.setup_pick, options=options, value=view._run_number(chosen))
        self.sweep_bar.layout.display = ""
        self.batch, self.batch_old = chosen, view._old_items(chosen)
        self._draw_batch(chosen)

    def _setup_picked(self, change: dict[str, Any]) -> None:
        """
        Another setup of the sweep picked: its answers show under the ranking.
        """
        if self.quiet or self.sweep is None or change.get("new") is None:
            return
        batch = self.view._run(change["new"])
        self.batch, self.batch_old = batch, self.view._old_items(batch)
        self._draw_batch(batch)

    def _use_shown(self, *_: Any) -> None:
        """
        Use this setup, under a sweep: the window switches to the setup picked.
        """
        if self.batch is not None:
            self._set_status(self.view._switch_to(self.batch), "ok")

    def _draw_batch(self, batch: Batch) -> None:
        """
        The Test tab's results: the run's summary and findings, then a line per question, which opens to its
        answer (a spinner while it's being asked).
        """
        view = self.view
        while len(self.batch_rows) < len(batch.items):
            self.batch_rows.append(self.w.HTML(layout=self.w.Layout(width="100%")))
        for k, item in enumerate(batch.items):
            if item.answer is not None or item.error:
                inner = _result_html(view._result_row(k + 1, item, self.batch_old))
            else:
                inner = _waiting_html(k + 1, item.question, sending=self.running)
            self._set(self.batch_rows[k], _wrap(f'<div class="bqs narrow">{inner}</div>'))
        self.test_rows.children = tuple(self.batch_rows[:len(batch.items)])
        number = view._run_number(batch)
        how = "searches only (Retrieve)" if batch.retrieve_only else view._model_label(batch.model)
        what = " · ".join(filter(None, [batch.kb_name or batch.kb_id, how, _settings_text(batch.settings)]))
        title = f"Test run {number}" + (f" ({batch.label})" if batch.label else "")
        head = f'<div class="ph" style="margin-top:12px">{_esc(title)}<span class="hint">{_esc(what)}</span></div>'
        if self.running:
            head += ('<div class="pd">Each question is asked on its own, not as a follow-up. Each line fills in as its '
                     "answer comes back; click one to read it.</div>")
        else:
            checked = any(_filled(i.expected) for i in batch.items)
            empty = (("Every search found passages" if batch.retrieve_only else "Every question was answered, citing "
                      "its sources") + (", and the expected one." if checked else "."))
            parts = [_Cards(view._batch_cards(batch)), _Findings(view._batch_findings(batch), empty)]
            head += f'<div class="tests">{_blocks_html(parts, root=_ROOT)}</div>'
        self._set(self.test_head, _wrap(head))
