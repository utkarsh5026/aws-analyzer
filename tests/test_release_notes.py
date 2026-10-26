"""
The pull request screenshot rule (.github/scripts/check_screenshots.py) and how release.py dresses release notes.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(path: str, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = load(".github/scripts/check_screenshots.py", "check_screenshots")
release = load(".claude/skills/release/release.py", "release_script")
TEMPLATE = (ROOT / ".github/pull_request_template.md").read_text(encoding="utf-8")
PACKAGE_FILE = ["src/aws_analyzer/s3/view.py"]


def test_the_untouched_template_fails_a_package_change() -> None:
    assert "no picture" in checker.problem(TEMPLATE, PACKAGE_FILE, "claude/x")


def test_a_picture_or_the_ticked_box_passes() -> None:
    with_picture = TEMPLATE.replace("## Checklist", "![summary](https://example.com/a.png)\n\n## Checklist")
    ticked = TEMPLATE.replace("- [ ] No screenshots", "- [x] No screenshots")
    attached = TEMPLATE.replace("## Checklist", "https://github.com/user-attachments/assets/abc\n\n## Checklist")
    for body in (with_picture, ticked, attached):
        assert checker.problem(body, PACKAGE_FILE, "claude/x") == ""


def test_a_picture_in_another_section_or_the_hint_does_not_count() -> None:
    elsewhere = TEMPLATE.replace("## Checklist", "## Checklist\n\n![x](https://example.com/a.png)")
    assert checker.problem(elsewhere, PACKAGE_FILE, "claude/x")
    assert "no '## Screenshots' section" in checker.problem("Fixes a bug.", PACKAGE_FILE, "claude/x")


def test_only_package_changes_need_screenshots() -> None:
    assert checker.problem("", ["tests/test_s3.py", "docs/s3.md"], "claude/x") == ""
    assert checker.problem("", PACKAGE_FILE, "release/v0.33.0") == ""
    assert checker.problem("", PACKAGE_FILE, "dependabot/pip/boto3") == ""


def test_notes_put_emojis_on_groups_and_breaking_changes() -> None:
    body = "### Changed\n\n- **Breaking**: `ls()` is `list()`.\n\n### Fixed\n\n- ⚡ **Lambda: right cost**"
    dressed = release.dressed(body)
    assert "### 🔄 Changed" in dressed and "### 🐛 Fixed" in dressed
    assert "- ⚠️ **Breaking**: `ls()`" in dressed


def test_notes_copy_each_pull_requests_screenshots(monkeypatch: pytest.MonkeyPatch) -> None:
    descriptions = {
        "7": {"title": "S3: previews Avro", "body": "Hi\n\n## Screenshots\n\n<!-- hint -->\n`ui.preview(...)`\n"
              "<img src=\"https://example.com/a.png\">\n\n- [ ] No screenshots: x\n\n## Checklist\n- [x] Tests"},
        "8": {"title": "Tests only", "body": "## Screenshots\n\n- [x] No screenshots: nothing changed"},
    }
    monkeypatch.setattr(release, "pull_request", lambda number: descriptions[number])
    entry = ("- 🪣 **S3: previews Avro** ([#7](https://github.com/o/r/pull/7))\n"
             "- ⚡ **Lambda** ([#8](https://github.com/o/r/pull/8))")
    section = release.screenshots(entry)
    assert section.startswith("## 📸 What it looks like")
    assert "**S3: previews Avro** ([#7](https://github.com/o/r/pull/7))" in section
    assert "`ui.preview(...)`" in section and '<img src="https://example.com/a.png">' in section
    assert "hint" not in section and "No screenshots" not in section and "#8" not in section
