"""The read-only guard: every AWS client a toolkit's analyzer makes for the agent is hooked, and any operation that
isn't a read is refused before its request is built, whatever asks for it (the model, a tool, a bug). It also counts
the reads it lets through, for the report's "AWS reads it made"."""

from __future__ import annotations

import threading
from collections import Counter
from typing import Any

READ_PREFIXES = ("Get", "List", "Describe", "Filter")  # the AWS operations the tools may call: reads only
# (FilterLogEvents reads a log group). The guard refuses every other one before its request is built.


class ReadOnlyError(PermissionError):
    """An AWS call the read-only guard refused: it isn't a Get, List, Describe or Filter call."""


class _Guard:
    """Refuses every AWS call a tool's client would make that isn't a read, before its request is built, and counts
    the ones it lets through. A toolkit hooks it onto its analyzer (attach) or onto a client it made (watch)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._watched: set[int] = set()
        self.counts: Counter[str] = Counter()
        self.blocked: list[str] = []

    def reset(self) -> None:
        with self._lock:
            self.counts.clear()
            self.blocked.clear()

    def _check(self, model: Any = None, **_: Any) -> None:
        if model is None:
            return
        op = f"{model.service_model.service_name}:{model.name}"
        if not model.name.startswith(READ_PREFIXES):
            with self._lock:
                self.blocked.append(op)
            raise ReadOnlyError(f"{op} isn't a read: the agent only reads, so it wasn't sent")
        with self._lock:
            self.counts[op] += 1

    def watch(self, client: Any) -> Any:
        """Hooks one boto3 client (once) and returns it."""
        events = getattr(getattr(client, "meta", None), "events", None)
        if events is not None and id(client) not in self._watched:
            events.register_first("before-parameter-build.*.*", self._check)
            self._watched.add(id(client))
        return client

    def attach(self, core: Any, factory: str | None = None) -> Any:
        """Hooks the clients an analyzer (`core`) has made: its main `client` when it's set in __init__, the ones in
        its `_clients`, and, when it makes clients through a method (`factory`, LambdaAnalyzer's `_service`), every
        one it makes from now on. Returns `core`."""
        if factory and not getattr(core, "_agent_guard", None):
            make = getattr(core, factory)

            def made(*args: Any, **kwargs: Any) -> Any:
                return self.watch(make(*args, **kwargs))

            setattr(core, factory, made)
            core._agent_guard = self
        attributes = getattr(core, "__dict__", {})  # not a `client` property: reading one could make a client
        for client in [attributes.get("client"), *getattr(core, "_clients", {}).values()]:
            if client is not None:
                self.watch(client)
        return core
