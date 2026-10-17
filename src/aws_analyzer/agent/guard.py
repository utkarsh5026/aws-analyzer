"""
The read-only guard: every AWS client a toolkit's analyzer makes for the agent is hooked, and any operation that
isn't a read is refused before its request is built, whatever asks for it (the model, a tool, a bug). It also counts
the reads it lets through, for the report's "AWS reads it made".

READ_PREFIXES are the AWS operations the tools may call: reads only (FilterLogEvents reads a log group; HeadObject
and HeadBucket read an object's or a bucket's headers). The guard refuses every other one before its request is
built.
"""

from __future__ import annotations

import threading
from collections import Counter
from typing import Any

READ_PREFIXES = ("Get", "List", "Describe", "Head", "Filter")


class ReadOnlyError(PermissionError):
    """
    An AWS call the read-only guard refused: it isn't a Get, List, Describe, Head or Filter call.
    """


def _is_client(value: Any) -> bool:
    """
    Whether `value` is a boto3 client (or a stand-in for one, as tests use): it has meta.events to hook.
    """
    return getattr(getattr(value, "meta", None), "events", None) is not None


class _GuardedSession:
    """
    A boto3 session whose every client goes through the guard. An analyzer the guard attaches to reads AWS through
    one in place of its own session, so a client it makes later (in another region, for another service, in any
    method) is hooked as it's made. Everything else is the session's own.
    """

    def __init__(self, session: Any, guard: _Guard):
        self._session, self._guard = session, guard

    def client(self, *args: Any, **kwargs: Any) -> Any:
        return self._guard.watch(self._session.client(*args, **kwargs))

    def resource(self, *args: Any, **kwargs: Any) -> Any:
        resource = self._session.resource(*args, **kwargs)
        self._guard.watch(resource.meta.client)
        return resource

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    def __repr__(self) -> str:
        return f"<read-only {self._session!r}>"


class _Guard:
    """
    Refuses every AWS call a tool's client would make that isn't a read, before its request is built, and counts
    the ones it lets through. A toolkit hooks it onto its analyzer (attach) or onto a client it made (watch). Its
    hook has one ID, so each client's event emitter keeps it once, however often it's asked to.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hook = f"aws-analyzer-read-only-{id(self)}"
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
        """
        Hooks one boto3 client (once: its event emitter keeps the hook's unique_id, so asking again does nothing)
        and returns it. Tools run side by side, so the hook is registered under the lock.
        """
        events = getattr(getattr(client, "meta", None), "events", None)
        if events is None:
            return client
        with self._lock:
            events.register_first("before-parameter-build.*.*", self._check, unique_id=self._hook)
        return client

    def attach(self, core: Any, factory: str | None = None) -> Any:
        """
        Hooks an analyzer (`core`): its session becomes a _GuardedSession, so every client it makes from now on is
        hooked; the clients it already has are hooked (its main `client` when it's set in __init__, and the ones it
        keeps in dicts, like `_clients`); and, when it makes clients through a method (`factory`, LambdaAnalyzer's
        `_service`), every client that returns, which covers clients passed in (clients=) too. Returns `core`.

        It reads the analyzer's attributes as they're stored, never a `client` property, since reading one could
        make a client. A tool running beside this one can add a client while they're read; that one was made hooked.
        """
        if factory and not getattr(core, "_agent_guard", None):
            make = getattr(core, factory)

            def made(*args: Any, **kwargs: Any) -> Any:
                return self.watch(make(*args, **kwargs))

            setattr(core, factory, made)
            core._agent_guard = self
        session = getattr(core, "session", None)
        if session is not None and not isinstance(session, _GuardedSession):
            core.session = _GuardedSession(session, self)
        attributes = getattr(core, "__dict__", {})
        for value in list(attributes.values()):
            try:
                found = list(value.values()) if isinstance(value, dict) else [value]
            except RuntimeError:
                continue
            for client in found:
                if _is_client(client):
                    self.watch(client)
        return core
