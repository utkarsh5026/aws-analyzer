"""
The explorer's Tables tab: every table in the region with a dot for what's wrong with it, chips (warnings, on-demand,
provisioned, no backups, idle) and a search box, and on the right the table picked as table_info() shows it, drawn
from what the window already read (no request). Picking a table also opens it for the Items and Schema tabs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..._kit.blocks import _Note
from ..._kit.errors import _why
from ..._kit.text import search_rank

from ..models import TableReport
from ..analysis import table_findings, table_monthly_cost
from .frame import _table_face
from .widgets import _Chip, _Line, _ListPage, _Shown

if TYPE_CHECKING:
    from .window import DynamoDBExplorer


_TABLE_CHIPS = (
    _Chip("", "All", "all", "Every table"),
    _Chip("warnings", "To fix", "warn", "The tables with a warning: what's wrong and what to change is on the right"),
    _Chip("on_demand", "On-demand", "none", "The tables billed per request"),
    _Chip("provisioned", "Provisioned", "none", "The tables billed for the capacity they keep"),
    _Chip("no_backup", "No backups", "warn", "The tables without point-in-time recovery"),
    _Chip("idle", "Idle", "none", "The tables nothing read or wrote in the last 24 hours"),
)


def _warnings(report: TableReport, prices: dict[str, Any]) -> list[str]:
    """
    A table's warnings, as tables() counts them.
    """
    if "describe" in report.info.errors:
        return []
    return [text for level, text in table_findings(report.info, report.metrics, prices) if level == "warn"]


def _in_table_chip(report: TableReport, chip: str, prices: dict[str, Any]) -> bool:
    """
    Whether a table belongs under a chip ('' is every one).
    """
    info, usage = report.info, report.metrics
    if chip == "warnings":
        return bool(_warnings(report, prices))
    if chip == "on_demand":
        return info.on_demand
    if chip == "provisioned":
        return not info.on_demand and "describe" not in info.errors
    if chip == "no_backup":
        return info.pitr is False
    if chip == "idle":
        return usage is not None and not usage.has_data
    return True


def _table_text(report: TableReport) -> tuple[str, ...]:
    """
    What the Tables tab's search box looks through: the name first, then the keys, the indexes and the tags.
    """
    info = report.info
    return (info.name, info.partition_key, info.sort_key or "", " ".join(i.name for i in info.indexes),
            " ".join(f"{key} {value}" for key, value in info.tags.items()))


class _TablesTab:
    """
    The Tables tab, a part of DynamoDBExplorer: table_list is its page, reports what was read (one TableReport per
    table, by name).
    """

    def _tables_page(self: DynamoDBExplorer) -> Any:
        self.reports: dict[str, TableReport] = {}
        self.table_list = _ListPage(self, placeholder="Search: table name, key, index, tag…", chips=_TABLE_CHIPS,
                                    on_click=self._clicked_table)
        self.table_list.redraw = self._draw_table_rows
        return self.table_list.page

    def _table_cost(self: DynamoDBExplorer, report: TableReport) -> float:
        if "describe" in report.info.errors:
            return 0.0
        return sum(table_monthly_cost(report.info, self.core.prices, report.metrics).values())

    def _draw_table_rows(self: DynamoDBExplorer) -> None:
        page, prices = self.table_list, self.core.prices
        reports = sorted(self.reports.values(), key=lambda r: r.info.name)
        page.draw_chips({chip.key: sum(1 for r in reports if _in_table_chip(r, chip.key, prices))
                         for chip in _TABLE_CHIPS})
        matching = [r for r in reports if _in_table_chip(r, page.chip, prices)]
        if page.query:
            matching = [r for r in matching if search_rank(page.query, _table_text(r)) is not None]
        words = page.query.split()
        lines = [_Shown(r.info.name, _table_face(r, warnings=len(_warnings(r, prices)), cost=self._table_cost(r),
                                                 words=words))
                 for r in matching]
        empty = "No table matches the search." if page.query or page.chip else f"No tables in {self.core.region}."
        page.fill(lines, total=len(reports), empty=empty, noun="tables")

    def _clicked_table(self: DynamoDBExplorer, line: _Line) -> None:
        if line.key:
            self._open_table(line.key)

    def _draw_table(self: DynamoDBExplorer, name: str) -> None:
        """
        The table picked, on the right of the Tables tab, as table_info() shows it, from its report.
        """
        report = self.reports[name]
        if "describe" in report.info.errors:
            why = _why(report.info.errors["describe"], "dynamodb:DescribeTable")
            self._draw("tables", self.table_list.view, [_Note(f"Couldn't describe {name} ({why}).", "warn")])
            return
        blocks = self.ui._table_info_blocks(report.info, report.metrics, usage_error=report.metrics_error)
        self._draw("tables", self.table_list.view, blocks)
