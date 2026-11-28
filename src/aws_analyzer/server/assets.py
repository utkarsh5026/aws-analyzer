"""
The page's built files: web/ (Preact and TypeScript) is built by Vite into static/dist/, which is committed so a
user's pip install needs no Node. Vite names each file by its content (assets/main-DvUJ64FW.js), and dist's
manifest.json says which files the page's entry point needs; the page's HTML gets a tag for each.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from html import escape
from pathlib import Path

DIST = Path(__file__).with_name("static") / "dist"
MANIFEST = "manifest.json"
DIST_URL = "static/dist"


class MissingBuild(RuntimeError):
    """
    static/dist/ has no build of the page: a checkout where `make web` hasn't run, or a broken install.
    """


@dataclass(frozen=True)
class PageAssets:
    """
    The built files the page loads, as paths from dist/: its scripts (ES modules) and its stylesheets.
    """

    scripts: tuple[str, ...]
    styles: tuple[str, ...]

    def tags(self) -> str:
        """
        The <link> and <script> tags for the page's <head>, the stylesheets first.
        """
        links = [f'<link rel="stylesheet" href="{escape(f"{DIST_URL}/{path}")}">' for path in self.styles]
        scripts = [f'<script type="module" src="{escape(f"{DIST_URL}/{path}")}"></script>' for path in self.scripts]
        return "\n".join(links + scripts)


def _entry(manifest: dict) -> dict:
    entries = [chunk for chunk in manifest.values() if chunk.get("isEntry")]
    if len(entries) != 1:
        raise MissingBuild(f"The page's build names {len(entries)} entry points, not one: run `make web` again.")
    return entries[0]


def page_assets(dist: Path = DIST) -> PageAssets:
    """
    The files the page's entry point loads, read from dist/manifest.json; MissingBuild when there's no build.
    """
    path = dist / MANIFEST
    if not path.exists():
        raise MissingBuild(f"The page isn't built: {path} is missing. In a checkout of aws-analyzer, run `make web`.")
    entry = _entry(json.loads(path.read_text(encoding="utf-8")))
    return PageAssets(scripts=(entry["file"],), styles=tuple(entry.get("css", ())))
