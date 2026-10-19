"""
_PageLink: a report saved as a page of its own, with the link that opens it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import quote

from ..._kit.blocks import _Report, _draws
from ..._kit.text import _esc

from ..analysis.package import _local_path


@dataclass
class _PageLink:
    """
    A page saved on the notebook's disk (`path`, absolute), with a link that opens it: in HTML, its path from the
    notebook's folder, which JupyterLab serves; just where it is when it's outside that folder, or in text.
    """

    path: str


def _page_href(path: str) -> str:
    """
    The link to a saved page from the notebook's folder ('' when it isn't in it: Jupyter may not serve it).
    """
    shown = _local_path(path)
    return "" if os.path.isabs(shown) else quote(shown.replace(os.sep, "/"))


@_draws(_PageLink, "html")
def _page_link_html(block: _PageLink, report: _Report) -> str:
    where = _local_path(block.path)
    href = _page_href(block.path)
    if not href:
        return f'<div class="pgw"><span class="pgp">Saved in {_esc(where)}</span></div>'
    return (f'<div class="pgw"><a class="pgo" href="{_esc(href)}" target="_blank" rel="noopener" '
            f'title="Open it in a tab of its own">↗ Open {_esc(os.path.basename(block.path))}</a>'
            f'<span class="pgp">saved as {_esc(where)}</span></div>')


@_draws(_PageLink, "text")
def _page_link_lines(block: _PageLink, report: _Report) -> list[str]:
    return [f"Saved as {_local_path(block.path)}: open it in a browser."]
