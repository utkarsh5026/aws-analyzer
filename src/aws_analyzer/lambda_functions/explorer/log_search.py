"""
The search box logs() puts over its report in a notebook: typing keeps the runs already read that hold the text
(no AWS call), and Enter searches every line of the time range in CloudWatch Logs and draws the report again.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from ..._kit.deps import _require
from ..._kit.text import _esc
from ..._kit.themes import _theme_name
from ..._kit.window import _Window

from ..blocks.logs import _LogRuns, _line_mentions, _runs_found

if TYPE_CHECKING:
    from ..view import LambdaView

_PLACEHOLDER = "Search these runs: KeyError, an order ID, KeyError OR Timeout…  (Enter searches CloudWatch)"

_OPEN_FOUND = 3

_BOX_WIDTH = "55%"


@dataclasses.dataclass
class _LogsAsked:
    """
    What logs() was asked for, so Enter asks again with a search: the function, time range, lines and region.
    """

    name: str
    since: Any = None
    n: int = 50
    region: str | None = None


class _LogSearch(_Window):
    """
    A text box above logs()' report and a line beside it saying what it found. The report is an ordinary HTML
    output (display_id), redrawn in place, so the notebook keeps its last state when it's saved; the box, a widget,
    lives only while the kernel does.
    """

    def __init__(self, view: LambdaView, blocks: list[Any], asked: _LogsAsked, search: str = "") -> None:
        widgets = _require("ipywidgets", "the search box over logs()")
        self.view = view
        self.asked = asked
        self.handle: Any = None
        self._take(blocks)
        self.box = widgets.Text(value=search, placeholder=_PLACEHOLDER, continuous_update=True,
                                layout=widgets.Layout(width=_BOX_WIDTH))
        self.box.observe(self._safely(self._typed), "value")
        self.box.on_msg(self._on_enter(self._search_cloudwatch))
        self.said = widgets.HTML(self._idle_text(), layout=widgets.Layout(margin="0 0 0 12px", flex="1 1 auto"))
        self.bar = widgets.HBox([self.box, self.said], layout=widgets.Layout(align_items="center"))

    def _take(self, blocks: list[Any]) -> None:
        """
        A report to filter: its blocks, its runs block (None when nothing was read) and each line's mark as read.
        """
        self.blocks = blocks
        self.base = next((block for block in blocks if isinstance(block, _LogRuns)), None)
        if self.base is not None:
            self.base = dataclasses.replace(self.base, search_hint="")
        self.read_runs = list(self.base.runs) if self.base is not None else []
        self.marked = {id(event): event.matched for run in self.read_runs for event in run.events}

    def show(self) -> None:
        from IPython.display import display

        display(self.bar)
        self.handle = display(self._report(self.base), display_id=True)

    def _report(self, block: _LogRuns | None) -> Any:
        from IPython.display import HTML

        blocks = [block if isinstance(each, _LogRuns) else each for each in self.blocks]
        return HTML(self.view._render_html(blocks, self.view.max_rows, theme=_theme_name()))

    def _draw(self, block: _LogRuns | None) -> None:
        if self.handle is not None:
            self.handle.update(self._report(block))

    def _warn(self, text: str) -> None:
        self.said.value = f'<span style="color:#c53030">{_esc(text)}</span>'

    def _explain(self, code: str, message: str) -> str:
        return self.view._explain(code, message)

    def _idle_text(self) -> str:
        count = len(self.read_runs)
        return f'<span style="opacity:.65">{count:,} run{"" if count == 1 else "s"} read · type to filter</span>'

    def filtered(self, text: str) -> _LogRuns | None:
        """
        The runs block for what's typed: the runs read that hold it, the lines holding it marked and the first few
        runs open; the report as read when the box is empty.
        """
        if self.base is None:
            return None
        runs, words = _runs_found(self.read_runs, text)
        for run in self.read_runs:
            for event in run.events:
                event.matched = _line_mentions(event.message, words) if words else self.marked[id(event)]
        if not words:
            return self.base
        opened = {run.key for run in runs[:_OPEN_FOUND]}
        return dataclasses.replace(self.base, runs=runs, words=words, opened=opened, searched=True)

    def _typed(self, change: dict[str, Any]) -> None:
        if self.quiet:
            return
        text = change["new"].strip()
        block = self.filtered(text)
        self._draw(block)
        if not text:
            self.said.value = self._idle_text()
            return
        found = len(block.runs) if block is not None else 0
        if found:
            self.said.value = (f"{found:,} of {len(self.read_runs):,} runs read hold it · Enter searches every line "
                               "in CloudWatch")
        else:
            self.said.value = f"No run read holds '{_esc(text)}' · press Enter to search CloudWatch"

    def _search_cloudwatch(self) -> None:
        """
        Enter: logs(search=...) on the same time range, drawn in place of the report.
        """
        text = self.box.value.strip()
        self.said.value = '<span style="opacity:.65">Searching CloudWatch…</span>'
        progress, self.view.progress = self.view.progress, "off"
        try:
            asked = self.asked
            blocks = self.view._logs_blocks(asked.name, since=asked.since, search=text or None, request_id=None,
                                            n=asked.n, region=asked.region)
        finally:
            self.view.progress = progress
        self._take(blocks)
        self._draw(self.base)
        found = len(self.read_runs)
        if not text:
            self.said.value = self._idle_text()
        elif found:
            self.said.value = f"CloudWatch: {found:,} run{'' if found == 1 else 's'} with '{_esc(text)}'"
        else:
            self.said.value = f"CloudWatch: no lines with '{_esc(text)}' in this time range"
