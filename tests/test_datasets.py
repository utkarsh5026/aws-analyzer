"""
A report as data (_kit/datasets.py), its workbook (_kit/workbook.py) and the fold a report ends with
(_kit/dataview.py), on a toy report with no AWS behind it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import openpyxl
import pytest

from aws_analyzer._kit import dataview, workbook
from aws_analyzer._kit.blocks import _Cards, _Findings, _Title, _Verdict
from aws_analyzer._kit.datasets import (
    DataChart,
    DataColumn,
    DataTable,
    Recipe,
    ReportData,
    by_hour,
    median_of,
    nearest_rank,
)
from aws_analyzer._kit.errors import _Hint
from aws_analyzer._kit.render import _render_text
from aws_analyzer._kit.view import _BaseView, _friendly_errors

START = datetime(2026, 11, 1, 9, 15, tzinfo=timezone.utc)


def toy_data(rows: int = 3) -> ReportData:
    runs = DataTable(
        "runs", "Every run", "a run",
        [DataColumn("time", "when it ended", "time"),
         DataColumn("duration_ms", "how long it ran", "ms", bar=True, read="Over 1,000 is slow."),
         DataColumn("share", "of the timeout", "share"),
         DataColumn("status", "how it ended", tones=(("timeout", "bad"),)),
         DataColumn("cold", "a cold start", "flag"),
         DataColumn("tags", "its tags")],
        [{"time": START + timedelta(minutes=40 * i), "duration_ms": 100.0 * (i + 1), "share": 0.1 * i,
          "status": "timeout" if i == 1 else "ok", "cold": i == 0, "tags": ["a", "b"]} for i in range(rows)],
        notes=["Newest last."],
        recipes=[Recipe("df.duration_ms.median()", "the typical run")],
    )
    charts = [DataChart("Run time", "runs", "time", ("duration_ms",), "scatter", "A dot per run."),
              DataChart("By status", "runs", "status", ("duration_ms",), "bar")]
    return ReportData("Toy report", [runs], charts, stem="toy-report", source="toy()")


def test_a_table_becomes_a_dataframe_that_says_what_its_columns_hold():
    df = toy_data()["runs"]
    assert list(df.columns) == ["time", "duration_ms", "share", "status", "cold", "tags"]
    assert str(df["time"].dt.tz) == "UTC" and df.duration_ms.tolist() == [100.0, 200.0, 300.0]
    assert df.attrs["about"] == "Every run: a run."
    assert df.attrs["columns"]["duration_ms"] == "how long it ran"


def test_report_data_lists_and_explains_its_tables():
    data = toy_data()
    assert data.names == ["runs"] and list(data) == ["runs"] and len(data) == 1
    assert set(data.frames()) == {"runs"}
    with pytest.raises(KeyError, match="this report has 'runs'"):
        data["nope"]
    guide = data.guide()
    assert "duration_ms  how long it ran. [milliseconds] Over 1,000 is slow." in guide
    assert "- Newest last." in guide and "df.duration_ms.median()    # the typical run" in guide
    assert repr(guide) == str(guide)
    assert "runs (3 rows)" in repr(data)


def test_summary_numbers():
    assert nearest_rank([5, 1, 4, 2, 3], 0.95) == 5 and nearest_rank([5, 1, 4, 2, 3], 0.5) == 3
    assert nearest_rank([], 0.5) is None and median_of([]) is None and median_of([1, 3]) == 2
    hours = by_hour([{"t": START}, {"t": START + timedelta(minutes=50)}, {"t": None}], time="t")
    assert [len(rows) for rows in hours.values()] == [1, 1]
    assert list(hours)[0] == START.replace(minute=0)


def test_cells_take_what_excel_can_hold():
    assert workbook.cell_value(START).tzinfo is None
    assert workbook.cell_value(["a", "b"]) == "a, b"
    assert workbook.cell_value("bad\x00\x07text") == "badtext"
    assert len(workbook.cell_value("x" * 40_000)) == 32_000
    assert workbook.cell_value(True) is True and workbook.cell_value(None) is None
    assert workbook._sheet_name("a/b:c?" * 10, set()) == "a_b_c_a_b_c_a_b_c_a_b_c_a_b_c_a"
    taken = {"runs"}
    assert workbook._sheet_name("Runs", taken) == "Runs 2"


def test_the_workbook_has_a_summary_charts_tables_and_a_guide(tmp_path):
    data = toy_data()
    data.cards = [("Runs", "3"), ("Timeouts", "1", "bad")]
    data.findings = [("info", "All fine."), ("warn", "One run timed out.")]
    data.verdict = "One run in three timed out."
    path = data.to_excel(str(tmp_path / "toy.xlsx"))
    book = openpyxl.load_workbook(path)
    assert book.sheetnames == ["Summary", "Charts", "runs", "How to read"]
    summary = [str(cell.value) for row in book["Summary"].iter_rows() for cell in row if cell.value]
    assert "Toy report" in summary and "One run in three timed out." in summary
    assert summary.index("⚠ One run timed out.") < summary.index("ℹ All fine.")
    assert "RUNS" in summary and "TIMEOUTS" in summary
    assert len(book["Charts"]._charts) == 2
    runs = book["runs"]
    assert [cell.value for cell in runs[4]] == ["time", "duration_ms", "share", "status", "cold", "tags"]
    assert runs["B5"].number_format == "#,##0" and runs["C5"].number_format == "0.0%"
    assert runs["A5"].value == START.replace(tzinfo=None) and runs["F5"].value == "a, b"
    assert list(runs.tables) == ["t_runs"] and runs.freeze_panes == "A5"
    guide = [str(cell.value) for row in book["How to read"].iter_rows() for cell in row if cell.value]
    assert "how long it ran" in guide and "df.duration_ms.median()" in guide
    assert not list(tmp_path.glob("*.part"))


def test_a_table_without_rows_still_writes(tmp_path):
    book = openpyxl.load_workbook(toy_data(rows=0).to_excel(str(tmp_path / "empty.xlsx")))
    assert book["runs"]["A5"].value == "No rows." and not book["runs"].tables


def test_a_report_ends_with_how_to_use_its_data():
    data = toy_data()
    guide = dataview._DataGuide(data, "ui.data()", "ui.to_excel()", "ui.data('toy')")
    text = "\n".join(dataview._data_guide_lines(guide, None))
    assert "Tables: runs (3 rows)." in text and "data = ui.data()" in text and "ui.data('toy')" in text
    report = dataview._Report(max_rows=10, root="toy")
    html = dataview._data_guide_html(guide, report)
    assert "🐼 This report as data" in html and "data[&#x27;runs&#x27;]" in html
    assert "how long it ran" in html and report.rules


def test_the_summary_comes_from_the_report_blocks():
    blocks = [_Title("Toy", "a sub"), _Verdict("It's fine."), _Cards([("Runs", 3, "ok")]),
              _Findings([("warn", "Check it.")])]
    filled = dataview._with_summary(toy_data(), blocks)
    assert (filled.title, filled.subtitle, filled.verdict) == ("Toy", "a sub", "It's fine.")
    assert filled.cards == [("Runs", "3", "ok")] and filled.findings == [("warn", "Check it.")]


def test_workbooks_go_to_a_free_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first = dataview._target_path(toy_data(), None)
    open(first, "w").close()
    assert first == "aws-analyzer-exports/toy-report.xlsx"
    assert dataview._target_path(toy_data(), None) == "aws-analyzer-exports/toy-report-2.xlsx"
    assert dataview._target_path(toy_data(), "out/mine") == "out/mine.xlsx"
    assert dataview._target_path(toy_data(), str(tmp_path)) == str(tmp_path / "toy-report.xlsx")


class ToyView(dataview._DataCommands, _BaseView):
    """
    A View with one report that has tables, and one that doesn't.
    """

    _GROUPS = {"Toy": ("toy", "plain", "data", "to_excel")}

    def __init__(self):
        super().__init__(mode="text")
        self.core = None

    def _render_html(self, blocks, max_rows, theme=""):
        return ""

    @_friendly_errors
    def toy(self, size: int = 3) -> None:
        """
        A toy report.
        """
        self._show(self._with_data([_Title("Toy report"), _Cards([("Runs", str(size))])],
                                   ReportData("Toy", toy_data(size).tables, source=f"toy({size})", stem="toy")))

    @_friendly_errors
    def plain(self) -> None:
        """
        A report without tables.
        """
        raise _Hint("Nothing here.")


def test_data_and_to_excel_on_a_view(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    view = ToyView()
    assert view.data() is None
    assert "No report with tables yet" in capsys.readouterr().out
    view.toy()
    out = capsys.readouterr().out
    assert "-- This report as data (pandas, Excel) --" in out and "data('toy', 5)" not in out
    assert view.data()["runs"].shape == (3, 6) and view.data().source == "toy(3)"
    assert view.data().again == "data('toy', 3)" and view.data().cards == [("Runs", "3")]
    quiet = view.data("toy", 5)
    assert len(quiet["runs"]) == 5 and capsys.readouterr().out == ""
    assert view.data("plain") is None
    assert "plain() gave no tables. Nothing here." in capsys.readouterr().out
    view.data("to_excel")
    assert "isn't one of this View's reports" in capsys.readouterr().out
    assert view.data().source == "toy(5)"
    view.to_excel()
    saved = capsys.readouterr().out
    assert "Saved Toy report as a workbook" in saved and "Sheets: 3" in saved
    assert (tmp_path / "aws-analyzer-exports" / "toy.xlsx").exists()
    view.to_excel("toy", 2, path=str(tmp_path / "two.xlsx"))
    assert len(openpyxl.load_workbook(tmp_path / "two.xlsx")["runs"].tables["t_runs"].ref.split(":")) == 2


def test_text_mode_draws_the_fold():
    data = toy_data()
    guide = dataview._DataGuide(data, "ui.data()", "ui.to_excel()")
    assert "data.guide()" in _render_text([guide], 10)
