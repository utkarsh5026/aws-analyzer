"""
The "⚙ Ran with" fold every report ends with (_kit/options.py): how its command ran and other ways to run it, read
from the command's signature and docstring, on a toy View with no AWS behind it.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import pytest

from aws_analyzer._kit.analyzer import _BaseAnalyzer
from aws_analyzer._kit.blocks import _Next, _Note, _Title
from aws_analyzer._kit.options import _Asked, _Example, _Options, command_options, docstring_examples
from aws_analyzer._kit.render import _Look, _report_html
from aws_analyzer._kit.view import _BaseView, _friendly_errors


class _Core(_BaseAnalyzer):
    _LIST_PRICES = {"thing": 1.0}


@dataclass
class _DataGuide:
    """
    Stands in for the data fold, which the options fold goes before.
    """

    name: str


class _Toy(_BaseView):
    """
    A View whose commands draw a report, so their folds can be read.
    """

    _render_html = staticmethod(lambda blocks, max_rows, theme="": _report_html(blocks, max_rows, _Look("", "toy", "")))
    _GROUPS = {"Help": ("help", "next"), "Reports": ("speed", "fixed", "broken", "items")}

    def __init__(self):
        super().__init__(mode="text")
        self.core = _Core(region="us-east-1")

    @_friendly_errors
    def speed(self, name: str, *, since: str = "24h", limit: int = 100, compare: bool = True,
              columns: list | None = None) -> None:
        """
        How fast it ran. since='7d' or '2026-10-01' looks further back; compare=False skips reading the window
        before; columns=[...] picks the columns.
        """
        if name == "slow":
            self._suggest("were the timeouts there before?", since="30d")
        self._show([_Title(f"Speed of {name}"), _Next([(f"speed({name!r}, since='7d')", "a week")])])

    @_friendly_errors
    def fixed(self, name: str) -> None:
        """
        A report with no option to change.
        """
        self._show([_Title(f"Fixed {name}")])

    @_friendly_errors
    def broken(self, name: str, *, since: str = "24h") -> None:
        """
        Fails as AWS would.
        """
        raise ValueError(f"no {name}")

    @_friendly_errors
    def items(self, table: str, *, n: int = 20, days: int = 14) -> None:
        """
        Items, with no example in its docstring.
        """
        self._show([_Title(f"Items of {table}"), _DataGuide("items")])


@pytest.fixture
def ui(monkeypatch):
    view = _Toy()
    monkeypatch.setattr(sys.modules["__main__"], "ui", view, raising=False)
    return view


def fold(capsys) -> str:
    out = capsys.readouterr().out
    start = out.find("Ran with")
    return out[start:] if start >= 0 else ""


def test_docstring_examples_read_values_and_what_they_do():
    doc = ("since='7d' or '2026-10-01' looks further back; compare=False skips reading the window before. "
           "status='failed' (or 'running', ...) and metrics=False skips CloudWatch and n=3 x=1")
    examples = docstring_examples(doc, {"since", "compare", "status", "metrics", "n"})
    assert examples["since"] == [_Example("'7d'", "looks further back"), _Example("'2026-10-01'", "looks further back")]
    assert examples["compare"] == [_Example("False", "skips reading the window before")]
    assert [example.value for example in examples["status"]] == ["'failed'", "'running'"]
    assert examples["metrics"] == [_Example("False", "skips CloudWatch")]
    assert examples["n"] == [_Example("3", "")]
    assert "x" not in examples


def test_a_report_ends_with_how_it_ran_and_other_ways(ui, capsys):
    ui.speed("etl")
    text = fold(capsys)
    assert text.startswith("Ran with: since='24h' · limit=100 · compare=True · other ways to run it:")
    assert "ui.speed('etl', since='7d')" in text and "looks further back" in text
    assert "ui.speed('etl', since='2026-10-01')" in text
    assert "ui.speed('etl', compare=False)" in text and "skips reading the window before" in text
    assert "columns=" not in text
    assert "Every option: ui.help('speed')" in text


def test_the_reports_own_suggestion_comes_first(ui, capsys):
    ui.speed("slow")
    lines = fold(capsys).splitlines()
    assert "ui.speed('slow', since='30d')" in lines[1] and "were the timeouts there before?" in lines[1]


def test_an_option_passed_offers_its_default_again(ui, capsys):
    ui.speed("etl", since="7d", compare=False)
    text = fold(capsys)
    assert text.startswith("Ran with: since='7d' · limit=100 · compare=False")
    defaults = [" ".join(line.split()) for line in text.splitlines() if "back to its default" in line]
    assert defaults == ["since='24h' ui.speed('etl', compare=False) back to its default",
                        "compare=True ui.speed('etl', since='7d') back to its default"]
    assert "ui.speed('etl', since='7d')" in text and "ui.speed('etl', since='2026-10-01', compare=False)" in text


def test_common_options_get_values_without_docstring_examples(ui, capsys):
    ui.items("orders")
    text = fold(capsys)
    assert "ui.items('orders', n=40)" in text and "shows twice as many" in text
    assert "ui.items('orders', days=7)" in text and "ui.items('orders', days=30)" in text


def test_the_fold_goes_before_the_data_fold(ui):
    ui._show = lambda blocks: setattr(ui, "drawn", ui._prepared(blocks))
    ui.items("orders")
    kinds = [type(block).__name__ for block in ui.drawn]
    assert kinds == ["_Title", "_Options", "_DataGuide"]


def test_no_fold_without_options_a_report_or_a_command(ui, capsys):
    ui.fixed("etl")
    ui.broken("etl")
    ui._show([_Title("Not a command's report")])
    ui.help()
    assert fold(capsys) == ""


def test_a_step_run_by_next_gets_its_own_fold_once(ui, capsys):
    ui.speed("etl")
    capsys.readouterr()
    ui.next(1)
    text = fold(capsys)
    assert text.startswith("Ran with: since='7d'") and text.count("Ran with") == 1


def test_the_fold_in_html_is_closed_with_two_ways_under_it(ui):
    options = command_options(_Asked(_Toy.speed.__wrapped__, ("etl",), {}), help_call="ui.help('speed')",
                              prefixed=lambda call: f"ui.{call}")
    assert isinstance(options, _Options) and len(options.tries) == 3
    html = ui._render_html([_Title("Speed"), options], 50)
    assert '<details class="op">' in html and "<b>⚙ Ran with</b>" in html and "3 other ways to run it" in html
    assert 'class="op-peek"' in html and html.count('class="op-ch"') == 3 + 2
    assert '<table class="op-all">' in html and '<td class="op-name">columns</td>' in html
    assert "details.op[open]+.op-peek{display:none}" in html
    assert 'op-tag set">you set' in html and 'op-tag">default' in html


def test_arguments_that_dont_fit_give_no_fold():
    assert command_options(_Asked(_Toy.speed.__wrapped__, (), {"nope": 1}), help_call="", prefixed=str) is None


def test_values_that_arent_literals_show_but_make_no_calls(ui):
    options = command_options(_Asked(_Toy.speed.__wrapped__, (object(),), {}), help_call="", prefixed=str)
    assert options is not None and options.tries == [] and options.used[0].name == "name"


def test_an_error_note_has_no_fold(ui, capsys):
    ui.broken("etl")
    out = capsys.readouterr().out
    assert "ValueError: no etl" in out and "Ran with" not in out
    assert all(not isinstance(block, _Options) for block in ui._prepared([_Note("x")]))
