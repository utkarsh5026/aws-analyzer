"""
A ReportData as an Excel workbook that reads like the report: a Summary sheet (title, the report's answer, its
numbers as cards, its findings and a link to every sheet), a Charts sheet of native Excel charts drawn from the
tables, a sheet per table (an Excel table with a filter on every column, number formats by kind, data bars and
coloured cells), and How to read, each column in plain words with the pandas lines worth running.

openpyxl is imported only here, inside write_workbook, through _require (the `data` extra installs it). The charts
are Excel's own, so they stay editable and need no plotting package.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .datasets import DataChart, DataColumn, DataTable, ReportData
from .deps import _require
from .fmt import _fmt_dt, _in_zone, _tz_label

INK = "2B2724"
MUTED = "7A716A"
ACCENT = "A85A26"
PAPER = "FAF7F2"
LINE = "E6DFD6"
HEADER_FILL = "3A3530"
TONES = {
    "warn": ("FBEFD5", "8A5A00"),
    "bad": ("F8DEDA", "9B2C1F"),
    "ok": ("E3EFE3", "2F6B3A"),
}
SERIES_COLOURS = ("A85A26", "5B6770", "D9A441", "B5473A", "8C7B6B")
FORMATS = {
    "count": "#,##0",
    "number": "#,##0.00",
    "ms": "#,##0",
    "seconds": "#,##0.00",
    "mb": "#,##0",
    "bytes": "#,##0",
    "usd": '"$"#,##0.00',
    "share": "0.0%",
    "time": "yyyy-mm-dd hh:mm:ss",
}
_OPENPYXL_PARTS = ("chart", "formatting.rule", "styles", "utils", "worksheet.table")
_FONT = "Calibri"
_SHEET_CODE_FONT = "Consolas"
_HEADER_ROW = 4
_ROW_HEIGHT = 15
_CARDS_PER_LINE = 4
_CARD_COLUMNS = 2
_WIDEST_COLUMN = 60
_NARROWEST_COLUMN = 8
_WIDTH_SAMPLE = 200
_CELL_CHARACTERS = 32_000
_CHART_ROWS = 25
_SCATTER_ROWS = 5_000
_CHART_WIDTH_CM = 24
_CHART_HEIGHT_CM = 8.5
_ROWS_PER_CHART = 22
_CHARS_PER_FINDING_LINE = 125
_LAST_SUMMARY_COLUMN = 1 + _CARDS_PER_LINE * _CARD_COLUMNS
_SHEET_NAME_CHARACTERS = 31
_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_SHEET_BANNED = re.compile(r"[\[\]:*?/\\]")


@dataclass
class _Book:
    """
    The workbook being written, the openpyxl modules it's written with, and where each table's sheet and rows are,
    so the charts can point at them.
    """

    workbook: Any
    xl: Any
    sheets: dict[str, Any]


def _modules() -> Any:
    """
    The openpyxl parts the workbook uses, loaded on first use.
    """
    openpyxl = _require("openpyxl", "to_excel()")
    for part in _OPENPYXL_PARTS:
        _require(f"openpyxl.{part}", "to_excel()", "openpyxl")
    return openpyxl


def _font(xl: Any, *, size: float = 11, bold: bool = False, colour: str = INK, name: str = _FONT) -> Any:
    return xl.styles.Font(name=name, size=size, bold=bold, color=colour)


def _solid_fill(xl: Any, colour: str) -> Any:
    return xl.styles.PatternFill("solid", start_color=colour, end_color=colour)


def _border(xl: Any) -> Any:
    side = xl.styles.Side(style="thin", color=LINE)
    return xl.styles.Border(left=side, right=side, top=side, bottom=side)


def _sheet_name(name: str, taken: set[str]) -> str:
    """
    A name Excel accepts for a sheet (31 characters, none of []:*?/\\), made unique among `taken`.
    """
    base = _SHEET_BANNED.sub("_", name)[:_SHEET_NAME_CHARACTERS] or "data"
    candidate, number = base, 2
    while candidate.lower() in {sheet.lower() for sheet in taken}:
        suffix = f" {number}"
        candidate = base[: _SHEET_NAME_CHARACTERS - len(suffix)] + suffix
        number += 1
    taken.add(candidate)
    return candidate


def cell_value(value: Any, column: DataColumn | None = None) -> Any:
    """
    A value as an Excel cell holds it: a time in the reports' zone without its zone (Excel has none), a list
    joined with commas, text without the control characters Excel refuses and cut to what a cell holds.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return _in_zone(value).replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (list, tuple, set)):
        value = ", ".join(str(item) for item in value)
    return _ILLEGAL.sub("", str(value))[:_CELL_CHARACTERS]




def _write_heading(sheet: Any, xl: Any, data: ReportData) -> int:
    """
    The title, subtitle and where the data came from; returns the next free row.
    """
    sheet["B1"] = cell_value(data.title)
    sheet["B1"].font = _font(xl, size=20, bold=True)
    sheet.row_dimensions[1].height = 30
    _merged(sheet, 2, 2, _LAST_SUMMARY_COLUMN)
    sheet["B2"] = cell_value(data.subtitle)
    sheet["B2"].font = _font(xl, colour=MUTED)
    sheet["B2"].alignment = xl.styles.Alignment(wrap_text=True, vertical="top")
    sheet.row_dimensions[2].height = _wrapped_height(data.subtitle)
    read = f"Read {_fmt_dt(data.read_at, zone=True)}" + (f" by {data.source}" if data.source else "")
    sheet["B3"] = cell_value(f"{read}. Times in this workbook are in {_tz_label()}.")
    sheet["B3"].font = _font(xl, size=9, colour=MUTED)
    row = 5
    if data.verdict:
        _merged(sheet, row, 2, _LAST_SUMMARY_COLUMN)
        verdict = sheet.cell(row, 2, cell_value(data.verdict))
        verdict.font = _font(xl, size=13, bold=True, colour=ACCENT)
        verdict.alignment = xl.styles.Alignment(wrap_text=True, vertical="top")
        sheet.row_dimensions[row].height = _wrapped_height(data.verdict, line=20, characters=95)
        row += 2
    return row


def _printable(sheet: Any) -> None:
    """
    Printed or saved as PDF, the sheet goes across the page: landscape, as wide as the paper, as long as it needs.
    """
    sheet.page_setup.orientation = "landscape"
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0


def _merged(sheet: Any, row: int, first: int, last: int) -> None:
    sheet.merge_cells(start_row=row, start_column=first, end_row=row, end_column=last)


def _write_card(sheet: Any, xl: Any, place: tuple[int, int], card: tuple[str, ...]) -> None:
    """
    One card: its label small over its value large, in a box two columns wide, coloured by its tone.
    """
    row, column = place
    label, value = card[0], card[1]
    tone = card[2] if len(card) > 2 else ""
    background, colour = TONES.get(tone, (PAPER, INK))
    for offset, (text, font) in enumerate(((label.upper(), _font(xl, size=8, colour=MUTED)),
                                           (value, _font(xl, size=16, bold=True, colour=colour)))):
        _merged(sheet, row + offset, column, column + _CARD_COLUMNS - 1)
        cell = sheet.cell(row + offset, column, cell_value(text))
        cell.font = font
        cell.alignment = xl.styles.Alignment(horizontal="left", vertical="center", indent=1)
        for spot in range(column, column + _CARD_COLUMNS):
            sheet.cell(row + offset, spot).fill = _solid_fill(xl, background)
    sheet.row_dimensions[row + 1].height = 26


def _write_cards(sheet: Any, xl: Any, cards: list[tuple[str, ...]], row: int) -> int:
    """
    The report's cards, four to a line; returns the next free row.
    """
    for index, card in enumerate(cards):
        line, place = divmod(index, _CARDS_PER_LINE)
        _write_card(sheet, xl, (row + line * 3, 2 + place * _CARD_COLUMNS), card)
    lines = -(-len(cards) // _CARDS_PER_LINE)
    return row + lines * 3 + (1 if cards else 0)


def _wrapped_height(text: str, *, line: int = 15, characters: int = _CHARS_PER_FINDING_LINE) -> float:
    """
    About how tall a row must be to show `text` wrapped across the Summary's width.
    """
    return line * (len(text) // characters + 1)


def _summary_section(sheet: Any, xl: Any, row: int, title: str) -> int:
    sheet.cell(row, 2, title).font = _font(xl, size=13, bold=True, colour=ACCENT)
    return row + 1


def _write_findings(sheet: Any, xl: Any, findings: list[tuple[str, str]], row: int) -> int:
    """
    The findings, warnings first, each across the page with its mark and wrapped; returns the next free row.
    """
    if not findings:
        return row
    row = _summary_section(sheet, xl, row, "What it found")
    last = _LAST_SUMMARY_COLUMN
    ordered = sorted(findings, key=lambda finding: finding[0] != "warn")
    for level, message in ordered:
        _merged(sheet, row, 2, last)
        mark = "⚠ " if level == "warn" else "ℹ "
        cell = sheet.cell(row, 2, cell_value(mark + message))
        cell.alignment = xl.styles.Alignment(wrap_text=True, vertical="top", indent=1)
        background, colour = TONES["warn"] if level == "warn" else (PAPER, INK)
        cell.font = _font(xl, colour=colour)
        for spot in range(2, last + 1):
            sheet.cell(row, spot).fill = _solid_fill(xl, background)
        sheet.row_dimensions[row].height = _wrapped_height(message) + 4
        row += 1
    return row + 1


def _write_contents(sheet: Any, xl: Any, book: _Book, data: ReportData, row: int) -> int:
    """
    A line per sheet: its name as a link, what one row is and how many rows it has.
    """
    row = _summary_section(sheet, xl, row, "In this workbook")
    entries = [("Charts", f"{len(data.charts)} charts drawn from the tables")] if data.charts else []
    entries += [(table.name, f"{table.title}: {len(table.rows):,} rows, each {table.row}") for table in data.tables]
    entries.append(("How to read", "what every column holds, and pandas lines worth running on each table"))
    for name, about in entries:
        sheet_name = book.sheets[name].title if name in book.sheets else name
        link = sheet.cell(row, 2, sheet_name)
        link.hyperlink = f"#'{sheet_name}'!A1"
        link.font = xl.styles.Font(name=_FONT, color=ACCENT, underline="single")
        _merged(sheet, row, 3, _LAST_SUMMARY_COLUMN)
        sheet.cell(row, 3, cell_value(about)).font = _font(xl, colour=MUTED)
        row += 1
    return row


def _write_summary(book: _Book, data: ReportData) -> None:
    sheet, xl = book.sheets["Summary"], book.xl
    sheet.sheet_view.showGridLines = False
    sheet.column_dimensions["A"].width = 2
    for index in range(2, 2 + _CARDS_PER_LINE * _CARD_COLUMNS):
        sheet.column_dimensions[xl.utils.get_column_letter(index)].width = 17
    row = _write_heading(sheet, xl, data)
    row = _write_cards(sheet, xl, data.cards, row)
    row = _write_findings(sheet, xl, data.findings, row)
    _write_contents(sheet, xl, book, data, row)




def _width(table: DataTable, column: DataColumn) -> float:
    """
    How wide a column should be to show its name and most of its values, within reason.
    """
    sample = [cell_value(value, column) for value in table.values(column.name)[:_WIDTH_SAMPLE]]
    longest = max((len(_fmt_cell(value, column)) for value in sample if value is not None), default=0)
    return max(_NARROWEST_COLUMN, min(_WIDEST_COLUMN, max(longest, len(column.name)) + 3))


def _fmt_cell(value: Any, column: DataColumn) -> str:
    """
    About how a value reads in its cell, for sizing the column.
    """
    if column.kind == "time":
        return "2026-01-01 00:00:00"
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _write_rows(sheet: Any, xl: Any, table: DataTable) -> None:
    """
    The header and the rows, each cell formatted by its column's kind.
    """
    for index, column in enumerate(table.columns, start=1):
        cell = sheet.cell(_HEADER_ROW, index, column.name)
        cell.font = _font(xl, bold=True, colour="FFFFFF")
        cell.fill = _solid_fill(xl, HEADER_FILL)
        cell.alignment = xl.styles.Alignment(vertical="center")
        letter = xl.utils.get_column_letter(index)
        sheet.column_dimensions[letter].width = _width(table, column)
    for offset, row in enumerate(table.rows, start=1):
        for index, column in enumerate(table.columns, start=1):
            cell = sheet.cell(_HEADER_ROW + offset, index, cell_value(row.get(column.name), column))
            if column.kind in FORMATS:
                cell.number_format = FORMATS[column.kind]
        sheet.row_dimensions[_HEADER_ROW + offset].height = _ROW_HEIGHT
    sheet.row_dimensions[_HEADER_ROW].height = 20


def _table_name(name: str, taken: set[str]) -> str:
    """
    A name Excel accepts for a table: letters, digits and underscores, starting with a letter, unique.
    """
    base = "t_" + re.sub(r"\W", "_", name)
    candidate, number = base, 2
    while candidate in taken:
        candidate, number = f"{base}_{number}", number + 1
    taken.add(candidate)
    return candidate


def _add_table(sheet: Any, xl: Any, table: DataTable, taken: set[str]) -> None:
    """
    Makes the rows an Excel table: banded, with a filter and sort on every column.
    """
    last = xl.utils.get_column_letter(len(table.columns))
    ref = f"A{_HEADER_ROW}:{last}{_HEADER_ROW + len(table.rows)}"
    excel_table = xl.worksheet.table.Table(displayName=_table_name(table.name, taken), ref=ref)
    excel_table.tableStyleInfo = xl.worksheet.table.TableStyleInfo(
        name="TableStyleLight1", showRowStripes=True, showColumnStripes=False)
    sheet.add_table(excel_table)


def _colour_cells(sheet: Any, xl: Any, table: DataTable) -> None:
    """
    Data bars under the columns marked bar=True, and cells coloured by value for the columns with tones.
    """
    rule = xl.formatting.rule
    last_row = _HEADER_ROW + len(table.rows)
    for index, column in enumerate(table.columns, start=1):
        letter = xl.utils.get_column_letter(index)
        cells = f"{letter}{_HEADER_ROW + 1}:{letter}{last_row}"
        if column.bar and column.is_number:
            sheet.conditional_formatting.add(cells, rule.DataBarRule(
                start_type="min", end_type="max", color="E7B98F", showValue=True))
        for value, tone in column.tones:
            background, colour = TONES.get(tone, TONES["warn"])
            sheet.conditional_formatting.add(cells, rule.FormulaRule(
                formula=[f'{letter}{_HEADER_ROW + 1}="{value}"'],
                fill=_solid_fill(xl, background), font=xl.styles.Font(color=colour, bold=True)))


def _write_table(book: _Book, table: DataTable, taken: set[str]) -> None:
    sheet, xl = book.sheets[table.name], book.xl
    sheet.sheet_view.showGridLines = False
    sheet["A1"] = cell_value(table.title)
    sheet["A1"].font = _font(xl, size=15, bold=True)
    times = any(column.kind == "time" for column in table.columns)
    about = f"One row is {table.row}. {len(table.rows):,} rows." + (f" Times are in {_tz_label()}." if times else "")
    sheet["A2"] = cell_value(about + " The How to read sheet says what each column holds.")
    sheet["A2"].font = _font(xl, colour=MUTED)
    sheet["A2"].hyperlink = "#'How to read'!A1"
    _write_rows(sheet, xl, table)
    if not table.rows:
        sheet.cell(_HEADER_ROW + 1, 1, "No rows.").font = _font(xl, colour=MUTED)
        return
    _add_table(sheet, xl, table, taken)
    _colour_cells(sheet, xl, table)
    sheet.freeze_panes = sheet.cell(_HEADER_ROW + 1, 1)




def _write_guide_columns(sheet: Any, xl: Any, table: DataTable, row: int) -> int:
    """
    A line per column: its name, what it holds, its unit and how to read it; returns the next free row.
    """
    for index, title in enumerate(("Column", "What it holds", "Unit", "How to read it"), start=1):
        cell = sheet.cell(row, index, title)
        cell.font = _font(xl, bold=True, colour="FFFFFF")
        cell.fill = _solid_fill(xl, HEADER_FILL)
    for column in table.columns:
        row += 1
        values = (column.name, column.meaning, column.unit, column.read)
        for index, value in enumerate(values, start=1):
            cell = sheet.cell(row, index, cell_value(value))
            cell.alignment = xl.styles.Alignment(wrap_text=True, vertical="top")
            cell.border = _border(xl)
        sheet.cell(row, 1).font = _font(xl, bold=True, name=_SHEET_CODE_FONT, size=10)
    return row + 2


def _write_guide_table(sheet: Any, xl: Any, table: DataTable, row: int) -> int:
    """
    One table's part of How to read; returns the next free row.
    """
    sheet.cell(row, 1, cell_value(f"{table.name}: {table.title}")).font = _font(xl, size=14, bold=True,
                                                                                colour=ACCENT)
    sheet.cell(row + 1, 1, cell_value(f"One row is {table.row}.")).font = _font(xl, colour=MUTED)
    row = _write_guide_columns(sheet, xl, table, row + 3)
    for note in table.notes:
        sheet.cell(row, 1, cell_value(f"• {note}")).alignment = xl.styles.Alignment(wrap_text=False)
        row += 1
    if table.recipes:
        row += 1 if table.notes else 0
        sheet.cell(row, 1, cell_value(f"In pandas: df = data[{table.name!r}]")).font = _font(xl, bold=True)
        row += 1
        for recipe in table.recipes:
            sheet.cell(row, 1, cell_value(recipe.code)).font = _font(xl, name=_SHEET_CODE_FONT, size=10, colour=ACCENT)
            sheet.cell(row, 2, cell_value(recipe.tells)).font = _font(xl, colour=MUTED)
            row += 1
    return row + 2


def _write_guide(book: _Book, data: ReportData) -> None:
    sheet, xl = book.sheets["How to read"], book.xl
    sheet.sheet_view.showGridLines = False
    for letter, width in zip("ABCD", (26, 60, 24, 60)):
        sheet.column_dimensions[letter].width = width
    sheet["A1"] = "How to read these tables"
    sheet["A1"].font = _font(xl, size=18, bold=True)
    sheet["A2"] = cell_value(
        f"Each table is also a pandas DataFrame: data = {data.again or 'ui.data()'} gives them, data['name'] one "
        "of them, and data.guide() this page as text.")
    sheet["A2"].font = _font(xl, colour=MUTED)
    row = 4
    for table in data.tables:
        row = _write_guide_table(sheet, xl, table, row)




def _column_index(table: DataTable, name: str) -> int:
    return next(index for index, column in enumerate(table.columns, start=1) if column.name == name)


def _chart_shape(xl: Any, chart: DataChart) -> Any:
    """
    An empty openpyxl chart of the kind asked for.
    """
    charts = xl.chart
    if chart.kind == "scatter":
        shape = charts.ScatterChart()
        shape.style = 13
    elif chart.kind == "line":
        shape = charts.LineChart()
    else:
        shape = charts.BarChart()
        shape.type = "bar" if chart.kind == "bar" else "col"
        shape.gapWidth = 60
    return shape


def _style_series(series: Any, chart: DataChart, colour: str) -> None:
    """
    A series in the workbook's colours: filled bars, a line two points wide, or dots without a line.
    """
    props = series.graphicalProperties
    if chart.kind == "scatter":
        series.marker.symbol = "circle"
        series.marker.size = 5
        series.marker.graphicalProperties.solidFill = colour
        series.marker.graphicalProperties.line.solidFill = colour
        props.line.noFill = True
    elif chart.kind == "line":
        props.line.solidFill = colour
        props.line.width = 25_400
        series.smooth = False
    else:
        props.solidFill = colour
        props.line.solidFill = colour


def _chart_rows(chart: DataChart, table: DataTable) -> int:
    """
    How many of the table's rows the chart draws.
    """
    most = chart.rows or (_SCATTER_ROWS if chart.kind in ("scatter", "line") else _CHART_ROWS)
    return min(len(table.rows), most)


def _fill_chart(shape: Any, book: _Book, chart: DataChart, table: DataTable) -> None:
    """
    Points the chart at the table's sheet: its x column and a series per y column.
    """
    xl, sheet = book.xl, book.sheets[table.name]
    last = _HEADER_ROW + _chart_rows(chart, table)
    x_index = _column_index(table, chart.x)
    xs = xl.chart.Reference(sheet, min_col=x_index, min_row=_HEADER_ROW + 1, max_row=last)
    for number, name in enumerate(chart.ys):
        y_index = _column_index(table, name)
        ys = xl.chart.Reference(sheet, min_col=y_index, min_row=_HEADER_ROW, max_row=last)
        if chart.kind == "scatter":
            series = xl.chart.Series(ys, xs, title_from_data=True)
            shape.series.append(series)
        else:
            shape.add_data(ys, titles_from_data=True)
            series = shape.series[-1]
        _style_series(series, chart, SERIES_COLOURS[number % len(SERIES_COLOURS)])
    if chart.kind != "scatter":
        shape.set_categories(xs)


def _dress_chart(shape: Any, chart: DataChart, table: DataTable) -> None:
    """
    Its size, axes and legend: axes shown (newer openpyxl hides them by default), times along the bottom
    formatted as times, bars across in the table's order, and no legend for one series.
    """
    shape.width, shape.height = _CHART_WIDTH_CM, _CHART_HEIGHT_CM
    shape.x_axis.delete = False
    shape.y_axis.delete = False
    shape.y_axis.title = chart.y_title or None
    shape.y_axis.scaling.min = 0
    shape.x_axis.majorGridlines = None
    x_column = table.column(chart.x)
    if x_column is not None and x_column.kind == "time":
        shape.x_axis.number_format = "mm-dd hh:mm"
        shape.x_axis.majorTimeUnit = "hours" if chart.kind != "scatter" else None
    if chart.kind == "bar":
        shape.x_axis.scaling.orientation = "maxMin"
    if len(chart.ys) == 1:
        shape.legend = None
    else:
        shape.legend.position = "b"


def _write_charts(book: _Book, data: ReportData) -> None:
    sheet, xl = book.sheets["Charts"], book.xl
    sheet.sheet_view.showGridLines = False
    sheet.column_dimensions["A"].width = 2
    row = 2
    for chart in data.charts:
        table = data.table(chart.table)
        sheet.cell(row, 2, cell_value(chart.title)).font = _font(xl, size=14, bold=True)
        sheet.cell(row + 1, 2, cell_value(chart.says)).font = _font(xl, colour=MUTED)
        if not table.rows:
            sheet.cell(row + 3, 2, "Nothing to draw: the table has no rows.").font = _font(xl, colour=MUTED)
            row += 6
            continue
        shape = _chart_shape(xl, chart)
        _fill_chart(shape, book, chart, table)
        _dress_chart(shape, chart, table)
        sheet.add_chart(shape, f"B{row + 3}")
        row += _ROWS_PER_CHART




def _open_book(data: ReportData) -> _Book:
    """
    A new workbook with its sheets in order: Summary, Charts (when there are any), a sheet per table, How to read.
    """
    xl = _modules()
    workbook = xl.Workbook()
    workbook.properties.title = data.title
    workbook.properties.creator = "aws-analyzer"
    summary = workbook.active
    summary.title = "Summary"
    sheets = {"Summary": summary}
    taken = {"Summary", "How to read"} | ({"Charts"} if data.charts else set())
    if data.charts:
        sheets["Charts"] = workbook.create_sheet("Charts")
    for table in data.tables:
        sheets[table.name] = workbook.create_sheet(_sheet_name(table.name, taken))
    sheets["How to read"] = workbook.create_sheet("How to read")
    summary.sheet_properties.tabColor = ACCENT
    for sheet in sheets.values():
        _printable(sheet)
    return _Book(workbook, xl, sheets)


def build_workbook(data: ReportData) -> Any:
    """
    The report as an openpyxl Workbook, not yet saved: Summary, Charts, a sheet per table and How to read.
    """
    book = _open_book(data)
    table_names: set[str] = set()
    for table in data.tables:
        _write_table(book, table, table_names)
    _write_summary(book, data)
    if data.charts:
        _write_charts(book, data)
    _write_guide(book, data)
    return book.workbook


def write_workbook(data: ReportData, path: str) -> str:
    """
    Writes the report's workbook to path, first as <path>.part and then renamed, so a half-written file never has
    the name; returns the path.
    """
    workbook = build_workbook(data)
    partial = f"{path}.part"
    workbook.save(partial)
    os.replace(partial, path)
    return path
