"""
A report's tables for pandas and Excel, in the notebook: the _DataGuide block a report ends with (folded: the code
that gives its tables as DataFrames, then each table's columns in plain words and pandas lines worth running), and
_DataCommands, the mixin that gives a View data() and to_excel().

A View that has it passes what a command read to _show(self._with_data(blocks, data)): a ReportData (datasets.py) the service
built from it. _show fills in the report's own summary from its blocks (title, verdict, cards, findings), keeps it
for data() and to_excel(), and adds the _DataGuide. So the tables come from what the report already read: neither
command reads AWS again unless it's given a command to run.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from typing import Any

from .blocks import _Cards, _Findings, _Note, _Report, _Title, _Verdict, _draws
from .datasets import ReportData
from .errors import _Hint
from .fmt import _count, human_size
from .highlight import _python_html
from .render import _call, _copyable, _prose
from .text import _esc
from .view import _friendly_errors

EXPORTS = "aws-analyzer-exports"
_GUIDE_COLUMNS_SHOWN = 40


@dataclass
class _DataGuide:
    """
    The fold at the end of a report: the code that gives its tables (data_call, again_call for the same read later)
    and writes its workbook (excel_call), and each table's columns, notes and recipes.
    """

    data: ReportData
    data_call: str
    excel_call: str
    again_call: str = ""


def _guide_code(guide: _DataGuide) -> str:
    """
    The lines to copy: the tables as DataFrames, the guide, the workbook, and the read again later.
    """
    first = guide.data.names[0] if guide.data.names else "name"
    lines = [
        f"data = {guide.data_call}",
        f"df = data[{first!r}]",
        "data.guide()",
        guide.excel_call,
    ]
    width = max(map(len, lines))
    says = [
        "this report's tables: no new read from AWS",
        "one of them, as a pandas DataFrame",
        "what every column means, and pandas lines worth running",
        "an Excel workbook: summary, charts, tables, how to read",
    ]
    code = [f"{line.ljust(width)}  # {why}" for line, why in zip(lines, says)]
    if guide.again_call:
        code += ["", "# the same tables later, read again:", f"data = {guide.again_call}"]
    return "\n".join(code)


_GUIDE_RULES = """
.{root} details.dg{{margin:18px 0 6px;border:1px solid var(--line,#e6dfd6);border-radius:8px;background:var(--card,#faf7f2)}}
.{root} details.dg>summary{{cursor:pointer;padding:9px 12px;list-style:none;display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}}
.{root} details.dg>summary::-webkit-details-marker{{display:none}}
.{root} details.dg>summary::before{{content:"▸";opacity:.6}}
.{root} details.dg[open]>summary::before{{content:"▾"}}
.{root} .dg-what{{opacity:.7;font-size:.92em}}
.{root} .dg-body{{padding:2px 14px 12px}}
.{root} .dg-body p{{margin:6px 0}}
.{root} .dg-t{{margin:10px 0;border-top:1px solid var(--line,#e6dfd6);padding-top:8px}}
.{root} .dg-t>summary{{cursor:pointer}}
.{root} .dg-t table{{border-collapse:collapse;margin:6px 0;font-size:.92em}}
.{root} .dg-t td{{padding:3px 10px 3px 0;vertical-align:top;border-bottom:1px solid var(--line,#eee)}}
.{root} .dg-t td.n{{font-family:var(--mono,monospace);white-space:nowrap}}
.{root} .dg-t td.u{{opacity:.65;white-space:nowrap}}
.{root} .dg-t ul{{margin:4px 0 4px 18px;padding:0}}
.{root} .dg pre .js,.{root} .dg pre .jk{{color:#a85a26}}
.{root} .dg pre .jn,.{root} .dg pre .jl{{color:#6b5bb0}}
.{root} .dg pre .pk{{color:#b5473a}}
.{root} .dg pre .pf{{color:#2f6b8a}}
.{root} .dg pre .pa{{color:#7a5a00}}
.{root} .dg pre .pc{{color:#8a817a;font-style:italic}}
"""


def _guide_code_html(code: str, what: str) -> str:
    return _copyable(f'<pre class="code">{_python_html(code)}</pre>', code, what)


def _guide_columns_html(guide: _DataGuide, name: str) -> str:
    """
    One table's columns as a small table: name, what it holds and its unit, and how to read it.
    """
    table = guide.data.table(name)
    rows = []
    for column in table.columns[:_GUIDE_COLUMNS_SHOWN]:
        read = f" {_esc(column.read)}" if column.read else ""
        rows.append(f'<tr><td class="n">{_esc(column.name)}</td><td>{_esc(column.sentence)}{read}</td>'
                    f'<td class="u">{_esc(column.unit)}</td></tr>')
    more = len(table.columns) - _GUIDE_COLUMNS_SHOWN
    if more > 0:
        rows.append(f'<tr><td></td><td>and {more} more: data.guide({name!r}) lists them all</td><td></td></tr>')
    return f"<table>{''.join(rows)}</table>"


def _table_html(guide: _DataGuide, name: str) -> str:
    """
    One table's fold: its key, title and size, then its columns, notes and pandas recipes.
    """
    table = guide.data.table(name)
    head = (f"<summary><code>data[{_esc(repr(name))}]</code> · {_esc(table.title)} · "
            f"{_count(len(table.rows))} rows, each {_esc(table.row)}</summary>")
    notes = "".join(f"<li>{_prose(note)}</li>" for note in table.notes)
    recipes = ""
    if table.recipes:
        code = "\n".join(f"{recipe.code}  # {recipe.tells}" for recipe in table.recipes)
        recipes = f"<p>Worth running, with <code>df = data[{_esc(repr(name))}]</code>:</p>"
        recipes += _guide_code_html(code, "the pandas lines")
    notes_html = f"<p><b>How to read it</b></p><ul>{notes}</ul>" if notes else ""
    return f'<details class="dg-t">{head}{_guide_columns_html(guide, name)}{notes_html}{recipes}</details>'


@_draws(_DataGuide, "html")
def _data_guide_html(guide: _DataGuide, report: _Report) -> str:
    report.rules.add(_GUIDE_RULES.format(root=report.root or "dg-any"))
    sizes = ", ".join(f"{name} ({_count(len(guide.data.table(name).rows))} rows)" for name in guide.data.names)
    summary = (f'<summary><b>🐼 This report as data</b><span class="dg-what">{_esc(sizes)} · pandas and '
               f"Excel</span></summary>")
    lead = ("<p>The tables this report was drawn from, ready for pandas, and a workbook to share. Each column is "
            "explained below; nothing is read from AWS again.</p>")
    tables = "".join(_table_html(guide, name) for name in guide.data.names)
    body = f'<div class="dg-body">{lead}{_guide_code_html(_guide_code(guide), "the code")}{tables}</div>'
    return f'<details class="dg">{summary}{body}</details>'


@_draws(_DataGuide, "text")
def _data_guide_lines(guide: _DataGuide, report: _Report) -> list[str]:
    sizes = ", ".join(f"{name} ({_count(len(guide.data.table(name).rows))} rows)" for name in guide.data.names)
    lines = ["", "-- This report as data (pandas, Excel) --", f"  Tables: {sizes}."]
    lines += [f"  {line}" for line in _guide_code(guide).splitlines()]
    return lines


def _with_summary(data: ReportData, blocks: list[Any]) -> ReportData:
    """
    The data with the report's own summary filled in from its blocks: the first title, the verdict, every card and
    every finding, for the workbook's Summary sheet.
    """
    title = next((block for block in blocks if isinstance(block, _Title)), None)
    verdict = next((block for block in blocks if isinstance(block, _Verdict)), None)
    cards = [tuple(str(part) for part in card) for block in blocks if isinstance(block, _Cards)
             for card in block.items]
    findings = [finding for block in blocks if isinstance(block, _Findings) for finding in block.items]
    return replace(
        data,
        title=title.text if title else data.title,
        subtitle=title.sub if title and not data.subtitle else data.subtitle,
        verdict=verdict.headline if verdict and not data.verdict else data.verdict,
        cards=cards or data.cards,
        findings=findings or data.findings,
    )


def _file_stem(text: str) -> str:
    """
    'Errors in orders-etl' -> 'errors-in-orders-etl': a file name's words.
    """
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80] or "report"


def _free_path(folder: str, stem: str) -> str:
    """
    folder/<stem>.xlsx, or with -2, -3... when that name is taken: a workbook never replaces a file.
    """
    path = os.path.join(folder, f"{stem}.xlsx")
    number = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{stem}-{number}.xlsx")
        number += 1
    return path


def _target_path(data: ReportData, path: str | None) -> str:
    """
    Where to_excel() writes: the path given (a folder gets the report's name in it), else a free name in
    aws-analyzer-exports beside the notebook. A file given by name may be replaced: the user named it.
    """
    if path and not os.path.isdir(path):
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        return path if path.lower().endswith(".xlsx") else f"{path}.xlsx"
    folder = path or EXPORTS
    os.makedirs(folder, exist_ok=True)
    return _free_path(folder, _file_stem(data.stem))


class _DataCommands:
    """
    data() and to_excel() for a View whose commands show their reports with _show(self._with_data(blocks, data)). The View keeps
    the last report's data (_last_data) and, while data('errors', ...) runs a command for its tables, shows nothing
    (_quiet).
    """

    _last_data: ReportData | None = None
    _quiet: bool = False
    _quiet_blocks: list[Any] | None = None

    def _show(self, blocks: list[Any]) -> None:
        if self._quiet:
            self._quiet_blocks = blocks
            return
        super()._show(blocks)  # type: ignore[misc]

    def _with_data(self, blocks: list[Any], data: ReportData) -> list[Any]:
        """
        Keeps the report's data, its summary filled in from the blocks, for data() and to_excel(), and returns the
        blocks with the fold that says how to use it at the end.
        """
        again = self._again_call(data)
        source = self._prefixed(data.source) if data.source else ""  # type: ignore[attr-defined]
        data = replace(_with_summary(data, blocks), source=source, again=again)
        self._last_data = data
        return [*blocks, self._data_guide(data)]

    def _data_guide(self, data: ReportData) -> _DataGuide:
        """
        The fold the report ends with, its calls written as the notebook runs them.
        """
        prefixed = self._prefixed  # type: ignore[attr-defined]
        return _DataGuide(data, data_call=prefixed("data()"), excel_call=prefixed("to_excel()"),
                          again_call=data.again)

    def _again_call(self, data: ReportData) -> str:
        """
        "errors('etl', since='24h')" -> "ui.data('errors', 'etl', since='24h')": the same read as data().
        """
        name, bracket, rest = data.source.partition("(")
        if not bracket:
            return ""
        args = rest[:-1]
        inside = f"{name!r}, {args}" if args else repr(name)
        return self._prefixed(f"data({inside})")  # type: ignore[attr-defined]

    def _read_quietly(self, command: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> ReportData:
        """
        Runs one of the View's commands without showing its report, and returns what it read.
        """
        method = getattr(self, command, None)
        if command in ("data", "to_excel") or not callable(method) or command.startswith("_"):
            raise _Hint(f"{command!r} isn't one of this View's reports: data() takes a command such as "
                        f"{self._data_example()}.")
        before = self._last_data
        self._last_data, self._quiet, self._quiet_blocks = None, True, None
        try:
            method(*args, **kwargs)
        finally:
            self._quiet = False
        read, self._last_data = self._last_data, self._last_data or before
        if read is None:
            notes = [block.text for block in self._quiet_blocks or [] if isinstance(block, _Note)]
            why = f" {notes[0]}" if notes else ""
            raise _Hint(f"{command}() gave no tables.{why}")
        return read

    def _data_example(self) -> str:
        return "data('errors', 'my-function')"

    @_friendly_errors
    def data(self, command: str | None = None, *args: Any, **kwargs: Any) -> ReportData:
        """
        The last report's tables as pandas DataFrames, with what every column means: data = ui.data(), then
        data['runs'] is one table, data.frames() all of them and data.guide() how to read them. Nothing is read
        from AWS again. data('performance', 'etl', since='7d') runs that report without showing it and gives its
        tables. When there's nothing to give, it says why and gives None.
        """
        if command is not None:
            return self._read_quietly(command, args, kwargs)
        if self._last_data is None:
            raise _Hint(f"No report with tables yet: run one first (it ends with 'This report as data'), or "
                        f"name one: {self._prefixed(self._data_example())}.")  # type: ignore[attr-defined]
        return self._last_data

    @_friendly_errors
    def to_excel(self, command: str | None = None, *args: Any, path: str | None = None, **kwargs: Any) -> None:
        """
        The last report as an Excel workbook: a Summary sheet with its numbers and findings, native Excel charts, a
        formatted sheet per table with filters, and How to read, every column explained. It's saved in
        aws-analyzer-exports/ beside the notebook (a new name, never over a file), or at path=. With a command,
        to_excel('errors', 'etl', since='7d') reads that report first.
        """
        data = self._read_quietly(command, args, kwargs) if command else self.data()
        target = _target_path(data, path)
        data.to_excel(target)
        self._show_saved(data, target)

    def _show_saved(self, data: ReportData, target: str) -> None:
        sheets = (["Summary"] + (["Charts"] if data.charts else []) + data.names + ["How to read"])
        rows = sum(len(table.rows) for table in data.tables)
        cards = [("File", os.path.basename(target)), ("Sheets", _count(len(sheets))), ("Rows", _count(rows)),
                 ("Charts", _count(len(data.charts))), ("Size", human_size(os.path.getsize(target)))]
        note = (f"Saved {target}. Its sheets: {', '.join(sheets)}. Open it in Excel, or read it back with "
                f"pandas: {_call('pd.read_excel', target, sheet_name=None)}.")
        super()._show([_Title(f"Saved {data.title} as a workbook", target), _Cards(cards), _Note(note)])  # type: ignore[misc]
