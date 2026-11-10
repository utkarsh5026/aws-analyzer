"""
The guides' video tours (.claude/skills/demo/tour_video.py): what the player reads matches the tours as written, each
cell shows a figure that exists, and each guide embeds its service's tour.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(path: str, name: str) -> ModuleType:
    """
    A script from .claude/skills as a module, registered first, as its dataclasses need.
    """
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


tours = load(".claude/skills/demo/tour_video.py", "tour_video")
SERVICES = [tour.service for tour in tours.TOURS]


def written_tours() -> dict:
    """
    The scenes in docs/javascripts/tours.js, as the player reads them.
    """
    text = tours.DATA.read_text(encoding="utf-8")
    return json.loads(text[text.index("{"):text.rindex("}") + 1])


def test_tours_js_is_up_to_date() -> None:
    assert tours.DATA.read_text(encoding="utf-8") == tours.tours_text(), (
        "docs/javascripts/tours.js is out of date: run .claude/skills/demo/tour_video.py --data")


@pytest.mark.parametrize("service", SERVICES)
def test_each_cell_shows_a_figure_in_both_themes(service: str) -> None:
    tour = next(tour for tour in tours.TOURS if tour.service == service)
    for cell in tour.cells:
        tours.figure_named(cell.figure)
        for theme in tours.THEMES:
            assert (tours.shots.IMAGES / f"{cell.figure}-{theme}.webp").exists()
        assert len(cell.captions) >= 2, f"{cell.figure}: one caption while typing, at least one on the report"


@pytest.mark.parametrize("service", SERVICES)
def test_each_guide_embeds_its_tour(service: str) -> None:
    guide = (tours.DOCS / f"{service}.md").read_text(encoding="utf-8")
    assert f'<div class="aa-tour" data-tour="{service}">' in guide
    assert f"videos/{service}-tour-light.mp4" in guide
    assert (tours.DOCS / "images" / "aws" / f"{next(t for t in tours.TOURS if t.service == service).icon}.svg").exists()


def test_cells_type_the_figures_calls_with_the_guides_variable() -> None:
    lambda_tour = written_tours()["lambda_functions"]
    cells = [scene for scene in lambda_tour["scenes"] if scene["kind"] == "cell"]
    assert cells[0]["code"] == "%pip install aws-analyzer"
    assert cells[1]["code"].startswith("from aws_analyzer import LambdaView\n\nui = LambdaView()\nui.functions(")
    step_functions = written_tours()["stepfunctions"]["scenes"]
    assert step_functions[2]["code"].endswith("\nsfn = StepFunctionsView()\nsfn.overview()")
    assert step_functions[3]["code"] == 'sfn.failures("order-pipeline")'


def test_scenes_last_long_enough_to_read_every_caption() -> None:
    for tour in written_tours().values():
        for scene in tour["scenes"]:
            times = [at for at, _ in scene.get("captions", [])]
            assert times == sorted(times)
            assert all(scene["dur"] - at >= tours.CAPTION_SECONDS - 0.01 for at in times[1:])


def test_end_card_lists_the_calls_made() -> None:
    end = written_tours()["dynamodb"]["scenes"][-1]
    assert end["kind"] == "end"
    assert end["calls"] == ["ui.tables()", "ui.table_info(…)", "ui.query(…)", "ui.scan(…)", "ui.schema(…)", "ui.help()"]


def test_captions_escape_text_and_mark_stress_and_code() -> None:
    assert tours.caption_html("**Read** `x < 1` & go") == "<b>Read</b> <code>x &lt; 1</code> &amp; go"
