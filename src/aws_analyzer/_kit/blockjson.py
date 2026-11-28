"""
A report's blocks as JSON, for the web page: the third drawing beside HTML and text (@_draws(kind, "json")).

Each block becomes a dict with its "kind" ('title', 'cards', 'table', ...) and its parts as plain JSON: the page draws
them with its own components. A block that has no JSON drawing (a service's own: _Wiring, _Passage, ...) goes as
{"kind": "html", "html": ...}, drawn by its HTML drawer without the report's stylesheet, so a page always shows every
block; the stylesheet comes once per service (_look_css), and the rules a report's blocks add (a table's filters) in
the report's "rules". Text the tool wrote (notes, findings, a title's sub) stays as written: the page finds the calls
in it, as _prose does in HTML.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

from .blocks import (
    _CELLS,
    _JSON,
    _Cards,
    _Findings,
    _Next,
    _Note,
    _Report,
    _Table,
    _Tabs,
    _Text,
    _Title,
    _Tone,
    _Verdict,
    _drawer,
    _draws,
)
from .charts import _Bars, _Dots, _Meter, _Split, _Treemap
from .datasets import json_value
from .render import _block_html, _Look, _ordered, _tone
from .themes import _theme_class, _theme_css

_STYLE_TAG = re.compile(r"</?style[^>]*>")
_CHARTS = {_Meter: "meter", _Split: "split", _Bars: "bars", _Treemap: "treemap", _Dots: "dots"}


@_draws(_Title, "json")
def _title_json(block: _Title, report: _Report) -> dict[str, Any]:
    return {"kind": "title", "text": block.text, "sub": block.sub, "badge": report.badge}


@_draws(_Cards, "json")
def _cards_json(block: _Cards, report: _Report) -> dict[str, Any]:
    """
    Each card as {label, value, tone}, the tone '' when the card has none.
    """
    cards = [{"label": str(item[0]), "value": str(item[1]), "tone": _tone(item)} for item in block.items]
    return {"kind": "cards", "items": cards}


@_draws(_Note, "json")
def _note_json(block: _Note, report: _Report) -> dict[str, Any]:
    return {"kind": "note", "text": block.text, "level": block.level}


@_draws(_Text, "json")
def _text_json(block: _Text, report: _Report) -> dict[str, Any]:
    return {"kind": "text", "text": block.text, "title": block.title, "wrap": block.wrap, "code": block.code,
            "collapsed": block.collapsed}


@_draws(_Findings, "json")
def _findings_json(block: _Findings, report: _Report) -> dict[str, Any]:
    """
    The findings, warnings first (as the panel shows them), and what to say when there are none.
    """
    items = [{"level": level, "message": message} for level, message in _ordered(block.items)]
    return {"kind": "findings", "items": items, "empty": block.empty}


@_draws(_Verdict, "json")
def _verdict_json(block: _Verdict, report: _Report) -> dict[str, Any]:
    return {"kind": "verdict", "headline": block.headline, "detail": block.detail, "tone": block.tone}


@_draws(_Next, "json")
def _next_json(block: _Next, report: _Report) -> dict[str, Any]:
    steps = [{"call": call, "why": why} for call, why in block.items]
    return {"kind": "next", "title": block.title, "items": steps, "numbered": block.numbered, "hint": block.hint}


def _cell_json(cell: Any) -> Any:
    """
    A table cell as JSON: its value as it is (text, a number), a _Tone as {text, tone}, and a cell of a type with its
    own HTML (bedrock_kb's _Link) as {text, html}.
    """
    if isinstance(cell, _Tone):
        return {"text": cell.text, "tone": cell.tone}
    draw = _drawer(_CELLS, cell)
    if draw is not None:
        text = str(cell)
        return {"text": text, "html": draw(cell, text)}
    return json_value(cell)


@_draws(_Table, "json")
def _table_json(block: _Table, report: _Report) -> dict[str, Any]:
    """
    Every row (the page decides how many to show), with what the table says about its columns, and max_rows, the
    cap the notebook would show (0: none).
    """
    rows = [[_cell_json(cell) for cell in row] for row in block.rows]
    cap = report.max_rows if block.max_rows is None else block.max_rows
    return {"kind": "table", "title": block.title, "headers": list(block.headers), "rows": rows,
            "bars": block.bars, "bar_label": block.bar_label, "tree": block.tree, "max_rows": cap,
            "code_cols": list(block.code_cols), "prose_cols": list(block.prose_cols), "collapsed": block.collapsed,
            "path_cols": list(block.path_cols), "sortable": block.sortable}


@_draws(_Tabs, "json")
def _tabs_json(block: _Tabs, report: _Report) -> dict[str, Any]:
    tabs = [{"name": name, "blocks": [_block_json(each, report) for each in blocks]} for name, blocks in block.tabs]
    return {"kind": "tabs", "tabs": tabs}


def _chart_json(block: Any, report: _Report) -> dict[str, Any]:
    """
    A chart as its fields (parts, values, texts, limit...), which the page draws with its own charts.
    """
    return {"kind": _CHARTS[type(block)], **json_value(asdict(block))}


for _chart in _CHARTS:
    _draws(_chart, "json")(_chart_json)


def _block_json(block: Any, report: _Report) -> dict[str, Any]:
    """
    One block as JSON: its own drawing, or {"kind": "html"} with its HTML when it has none.
    """
    draw = _drawer(_JSON, block)
    if draw is not None:
        return draw(block, report)
    return {"kind": "html", "block": type(block).__name__.lstrip("_"), "html": _block_html(block, report)}


def _report_json(blocks: list[Any], max_rows: int, look: _Look, *, theme: str = "") -> dict[str, Any]:
    """
    A report as JSON: its blocks (each _block_json), the class of the box they go in (the service's root class, and
    the theme's when it has rules: _look_css's are scoped to it), the badge, and the CSS rules its blocks added (a
    table's filters and columns), which go after the stylesheet.
    """
    report = _Report(max_rows, look.badge, look.root, tuple(blocks))
    drawn = [_block_json(block, report) for block in blocks]
    box_class = f"{look.root} {_theme_class(theme)}" if _theme_css(look.root, theme) else look.root
    return {"box_class": box_class, "badge": look.badge, "rules": sorted(report.rules), "blocks": drawn}


def _look_css(look: _Look, theme: str = "") -> str:
    """
    A service's stylesheet and its theme's rules as plain CSS (the rules of the <style> elements a notebook report
    starts with): the page puts them in once for every report of the service.
    """
    return _STYLE_TAG.sub("", look.css + _theme_css(look.root, theme)).strip()
