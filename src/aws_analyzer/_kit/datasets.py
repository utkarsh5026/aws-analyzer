"""
A report as data: the tables behind it, each column with what it holds and how to read it, the charts worth drawing
from them, and the report's own summary (its title, cards and findings). Pure: it holds rows and words, and turns
them into pandas DataFrames (pandas is imported only then) or, through workbook.py, an Excel workbook.

A service builds a ReportData from what a command read (lambda_functions' analysis/datasets.py, ...); the View fills
in the summary from the report's blocks and keeps it, so ui.data() and ui.to_excel() need no second read.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from statistics import median
from typing import Any, Callable, Iterable

from .deps import _require
from .fmt import _utcnow

KINDS = {
    "text": "words",
    "id": "an ID",
    "count": "a count",
    "number": "a number",
    "ms": "milliseconds",
    "seconds": "seconds",
    "mb": "megabytes (MB)",
    "bytes": "bytes",
    "usd": "US dollars",
    "share": "a share from 0 to 1 (0.25 is 25%)",
    "time": "a moment in time, UTC in pandas",
    "flag": "True or False",
}
_NUMERIC = frozenset({"count", "number", "ms", "seconds", "mb", "bytes", "usd", "share"})


@dataclass(frozen=True)
class DataColumn:
    """
    One column of a table: its name, what it holds in plain words, its kind (KINDS: 'ms', 'time', 'share', ...),
    and optionally how to read it ('over 0.9 means it nearly timed out'). bar=True draws a data bar under its
    numbers in Excel; tones colours a cell by its value ({'timeout': 'bad', 'error': 'warn'}).
    """

    name: str
    meaning: str
    kind: str = "text"
    read: str = ""
    bar: bool = False
    tones: tuple[tuple[str, str], ...] = ()

    @property
    def is_number(self) -> bool:
        """
        Whether it holds numbers (a count, a duration, money...), which pandas can add up and Excel can chart.
        """
        return self.kind in _NUMERIC

    @property
    def sentence(self) -> str:
        """
        The meaning as a sentence: with a full stop unless it already ends in one.
        """
        return self.meaning if self.meaning.endswith((".", "!", "?")) else f"{self.meaning}."

    @property
    def unit(self) -> str:
        """
        The kind in words: 'milliseconds', 'a share from 0 to 1 (0.25 is 25%)'.
        """
        return KINDS.get(self.kind, self.kind)


@dataclass(frozen=True)
class Recipe:
    """
    A line of pandas worth running on a table, with what it tells you: "df.groupby('version').duration_ms.median()"
    -> 'the typical run time of each version'.
    """

    code: str
    tells: str


@dataclass
class DataTable:
    """
    One table behind a report: `name` is its key (data['runs']) and the Excel sheet's name, `row` says what one row
    is, with its article ('a run of the function'), `columns` what each column holds, `notes` how to read the table as a whole, and
    `recipes` pandas lines worth running on it (written for a DataFrame called df).
    """

    name: str
    title: str
    row: str
    columns: list[DataColumn]
    rows: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    recipes: list[Recipe] = field(default_factory=list)

    def column(self, name: str) -> DataColumn | None:
        """
        The column named, or None.
        """
        return next((col for col in self.columns if col.name == name), None)

    def values(self, name: str) -> list[Any]:
        """
        A column's values, top to bottom.
        """
        return [row.get(name) for row in self.rows]

    def to_df(self):
        """
        The table as a pandas DataFrame, its columns in order. df.attrs['about'] says what a row is, and
        df.attrs['columns'] what each column holds.
        """
        pd = _require("pandas", "DataTable.to_df()")
        names = [col.name for col in self.columns]
        frame = pd.DataFrame([[row.get(name) for name in names] for row in self.rows], columns=names)
        for col in self.columns:
            if col.kind == "time":
                frame[col.name] = pd.to_datetime(frame[col.name], utc=True)
        frame.attrs["about"] = f"{self.title}: {self.row}."
        frame.attrs["columns"] = {col.name: col.meaning for col in self.columns}
        return frame


@dataclass(frozen=True)
class DataChart:
    """
    A chart to draw from one of the tables: `kind` is 'column' (bars up, for a value per period), 'bar' (bars
    across, for a value per name), 'line' (values over time) or 'scatter' (a dot per row: x and y both numbers or
    times). `x` names the column along the bottom, `ys` the columns drawn, and `says` how to read it.
    """

    title: str
    table: str
    x: str
    ys: tuple[str, ...]
    kind: str = "column"
    says: str = ""
    y_title: str = ""
    rows: int = 0


class Guide(str):
    """
    Text that shows as it is when a notebook cell ends with it (data.guide()), not as a quoted string.
    """

    def __repr__(self) -> str:
        return str(self)


@dataclass
class ReportData:
    """
    What a report read, as tables: data['runs'] is a pandas DataFrame, data.frames() all of them, data.guide()
    what each column means and how to read it, and data.to_excel() a workbook with the summary, charts and tables.

        title, subtitle  the report's
        verdict          the report's answer in a sentence, when it has one
        cards            the numbers it led with: (label, value, tone)
        findings         its findings: ('warn' | 'info', message)
        tables, charts   the data, and the charts worth drawing from it
        source           the call that read it ("ui.errors('etl', since='24h')")
        again            the call that gives these tables again ("ui.data('errors', 'etl', since='24h')")
        read_at          when it was read
        stem             the start of the file name to_excel() picks ('lambda-errors-etl')
    """

    title: str
    tables: list[DataTable]
    charts: list[DataChart] = field(default_factory=list)
    subtitle: str = ""
    verdict: str = ""
    cards: list[tuple[str, ...]] = field(default_factory=list)
    findings: list[tuple[str, str]] = field(default_factory=list)
    source: str = ""
    again: str = ""
    read_at: datetime = field(default_factory=_utcnow)
    stem: str = "report"

    @property
    def names(self) -> list[str]:
        """
        The tables' names, in order: what data['...'] takes.
        """
        return [table.name for table in self.tables]

    def table(self, name: str) -> DataTable:
        """
        The table named; a KeyError that lists the names there are otherwise.
        """
        for table in self.tables:
            if table.name == name:
                return table
        raise KeyError(f"No table {name!r}: this report has {', '.join(map(repr, self.names))}.")

    def __getitem__(self, name: str):
        return self.table(name).to_df()

    def __iter__(self):
        return iter(self.names)

    def __len__(self) -> int:
        return len(self.tables)

    def frames(self) -> dict[str, Any]:
        """
        Every table as a pandas DataFrame, by name.
        """
        return {table.name: table.to_df() for table in self.tables}

    def guide(self, name: str | None = None) -> Guide:
        """
        What each table holds, column by column, how to read it, and pandas lines worth running on it; name= one
        table.
        """
        tables = [self.table(name)] if name else self.tables
        return Guide("\n\n".join(table_guide(table) for table in tables))

    def to_excel(self, path: str) -> str:
        """
        Writes the workbook to path (replacing a file there) and returns the path: a Summary sheet, a Charts sheet,
        a sheet per table and How to read.
        """
        from .workbook import write_workbook

        return write_workbook(self, path)

    def __repr__(self) -> str:
        tables = ", ".join(f"{table.name} ({len(table.rows):,} rows)" for table in self.tables)
        return f"<ReportData {self.title!r}: {tables}. data.guide() says what each column means.>"


def table_guide(table: DataTable) -> str:
    """
    One table's guide as text: what a row is, each column with its unit and how to read it, the notes and recipes.
    """
    width = max((len(col.name) for col in table.columns), default=0)
    lines = [f"{table.name}: {table.title}", f"  One row is {table.row}. {len(table.rows):,} rows.", "", "  Columns:"]
    for col in table.columns:
        read = f" {col.read}" if col.read else ""
        lines.append(f"    {col.name.ljust(width)}  {col.sentence} [{col.unit}]{read}")
    if table.notes:
        lines += ["", "  How to read it:"] + [f"    - {note}" for note in table.notes]
    if table.recipes:
        lines += ["", f"  In pandas (df = data[{table.name!r}]):"]
        lines += [f"    {recipe.code}    # {recipe.tells}" for recipe in table.recipes]
    return "\n".join(lines)


def nearest_rank(values: Iterable[float], share: float) -> float | None:
    """
    The value `share` of the way up the sorted values (0.95 -> the 95th percentile), by the nearest rank; None for
    no values.
    """
    ordered = sorted(values)
    if not ordered:
        return None
    rank = max(0, min(len(ordered) - 1, math.ceil(share * len(ordered)) - 1))
    return ordered[rank]


def median_of(values: Iterable[float]) -> float | None:
    """
    The median, or None for no values.
    """
    listed = list(values)
    return median(listed) if listed else None


def hour_of(moment: datetime) -> datetime:
    """
    The start of the hour a moment falls in.
    """
    return moment.replace(minute=0, second=0, microsecond=0)


def by_hour(rows: Iterable[dict[str, Any]], *, time: str) -> dict[datetime, list[dict[str, Any]]]:
    """
    Rows grouped by the hour their `time` column falls in, oldest hour first; rows without a time are left out.
    """
    hours: dict[datetime, list[dict[str, Any]]] = {}
    for row in rows:
        moment = row.get(time)
        if isinstance(moment, datetime):
            hours.setdefault(hour_of(moment), []).append(row)
    return dict(sorted(hours.items()))


def counted(rows: Iterable[dict[str, Any]], keep: Callable[[dict[str, Any]], bool]) -> int:
    """
    How many rows keep() says yes to.
    """
    return sum(1 for row in rows if keep(row))
