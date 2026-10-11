"""The code every service shares (aws_analyzer._kit): what the renderer does with a block nothing draws, and the
stylesheet every report starts with. Each service's tests cover how its reports read."""

import pytest

from aws_analyzer import dynamodb as ddbmod
from aws_analyzer._kit.blocks import _Note, _Title
from aws_analyzer._kit.render import _base_css, _render_text, _report_html


class _Mystery:  # a block no service registers a drawing for
    pass


class _Loud(_Note):  # a block made from another one
    pass


def test_a_block_nothing_draws_becomes_a_note():
    html = _report_html([_Title("Report"), _Mystery()], 0, "<style></style>", "ddb", "🗄️ DynamoDB")
    assert html.startswith('<style></style><div class="ddb"><h3><span class="badge">🗄️ DynamoDB</span>Report</h3>')
    assert '<div class="note warn">Part of this report isn&#x27;t shown: there&#x27;s no way to draw a _Mystery' in html
    assert _render_text([_Mystery()], 0).startswith("[!] Part of this report isn't shown")


def test_a_block_made_from_another_is_drawn_like_it():
    assert _report_html([_Loud("Careful", "warn")], 0, "", "ddb", "") == (
        '<div class="ddb"><div class="note warn">Careful</div></div>')
    assert _render_text([_Loud("Careful", "warn")], 0) == "[!] Careful"


def test_base_css_restyles_only_a_rule_it_finds_once():
    assert _base_css("ddb") == ddbmod._CSS and _base_css("x").count(".x ") == 51
    css = _base_css("x", ".x .own{}", theme=".x{--c:red}", restyle={".pill{": ".x .pill{color:var(--c)}"},
                     after={"pre.code{": ".x pre.hl{}"})
    lines = css.split("\n")
    assert lines[:2] == ["<style>", ".x{--c:red}"] and lines[-2:] == [".x .own{}", "</style>"]
    assert ".x .pill{color:var(--c)}" in lines and lines[lines.index(".x pre.hl{}") - 1].startswith(".x pre.code{")
    with pytest.raises(ValueError, match="'.nope{' starts 0 of the shared CSS rules"):
        _base_css("x", restyle={".nope{": ".x .nope{}"})
    with pytest.raises(ValueError, match="'.track{' starts 2 of"):  # .track{display:..} and .track{vertical-align:..}
        _base_css("x", restyle={".track{": ".x .track{}"})
