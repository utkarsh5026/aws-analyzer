"""
The REST side's errors and paths: what OpenSearchAnalyzer.request() may POST to, and its error type.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

from .._kit.text import _clip


_READ_POSTS = re.compile(r"/_(?:search|count)$")  # the only paths a body is sent to (searches): nothing else is POSTed


class OpenSearchError(Exception):
    """
    A request to an OpenSearch endpoint that failed. `status` is the HTTP status (None when the endpoint couldn't
    be reached), `kind` the error type OpenSearch gave (index_not_found_exception, security_exception, ...) and
    `reason` its message.
    """

    def __init__(self, store: Any, path: str, status: int | None, kind: str, reason: str):
        self.store, self.path, self.status, self.kind, self.reason = store, path, status, kind, reason
        super().__init__(f"{status or 'no answer'} {kind}: {reason} ({path})")


def _error_details(status: int, content: bytes) -> tuple[str, str]:
    """
    (error type, reason) from an OpenSearch error answer, whichever shape it has.
    """
    text = (content or b"").decode("utf-8", "replace").strip()
    try:
        body = json.loads(text) if text else {}
    except ValueError:
        return f"HTTP {status}", _clip(text, 300) or "no message"
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            causes = error.get("root_cause")
            root = causes[0] if isinstance(causes, list) and causes and isinstance(causes[0], dict) else {}
            kind = error.get("type") or root.get("type") or f"HTTP {status}"
            return str(kind), _clip(str(error.get("reason") or root.get("reason") or ""), 500)
        message = error if isinstance(error, str) else body.get("Message") or body.get("message")
        if message:
            return f"HTTP {status}", _clip(str(message), 500)
    return f"HTTP {status}", _clip(text, 300)


def _path(name: str) -> str:
    """
    An index name for a URL path (commas and wildcards kept).
    """
    return quote(name, safe=",*")


def _store_url(store: Any) -> str | None:
    return store.url
