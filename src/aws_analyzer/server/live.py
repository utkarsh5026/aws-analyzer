"""
Live streams: GET /api/live/<service>/<feed>?<param>=...&token=... is a stream of Server-Sent Events (streams.py) of
what a feed (feeds.py) reads, every few seconds, as 'sample' messages ('note' when a read fails; the stream goes on).

The server reads each stream once however many tabs follow it: the first page that asks starts it (a task on the
server's loop, each read on a worker thread), the others share its samples (a newcomer gets the newest at once), and
it stops after its last follower leaves. A page follows for at most LIVE_MINUTES, like the explorers' Live tabs, so
a tab left open doesn't read CloudWatch all day: then the stream says 'done' and the page offers to go on.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import traceback
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from .._kit.errors import _error_code, _Hint
from .feeds import FEEDS, Feed, FeedProblem, Reader, feed_params
from .streams import HEARTBEAT, HEARTBEAT_SECONDS, STREAM_HEADERS, STREAM_TYPE, sse_message

log = logging.getLogger("aws_analyzer.server")
LIVE_MINUTES = 15
WAITING_MESSAGES = 8


@dataclass
class LiveStream:
    """
    One stream being read: its feed and parameters, its reader, the pages following it (a queue each), its newest
    message, and the task reading it.
    """

    feed: Feed
    params: dict[str, str]
    reader: Reader | None = None
    followers: list[asyncio.Queue] = field(default_factory=list)
    newest: str = ""
    task: asyncio.Task | None = None

    def send(self, message: str) -> None:
        """
        Puts a message in every follower's queue, dropping a slow one's oldest when it's full.
        """
        self.newest = message
        for queue in self.followers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(message)


def _words(exc: BaseException) -> str:
    """
    What a failed read says on the page: AWS's error code and message, or the problem in words.
    """
    if isinstance(exc, ClientError):
        message = exc.response.get("Error", {}).get("Message", "")
        return f"Couldn't read: {_error_code(exc)}. {message}".strip()
    if isinstance(exc, (_Hint, FeedProblem)):
        return str(exc)
    return f"Couldn't read: {type(exc).__name__}: {exc}"


class LiveHub:
    """
    The live streams this server reads, one per feed and parameters, on the runner's analyzers.
    """

    def __init__(self, runner: Any):
        self.runner = runner
        self._streams: dict[tuple, LiveStream] = {}

    def follow(self, feed: Feed, params: dict[str, str]) -> tuple[LiveStream, asyncio.Queue]:
        """
        Follows a stream, starting it when nobody does yet: a queue the stream's messages arrive in, the newest
        already in it.
        """
        key = (feed.service, feed.name, tuple(sorted(params.items())))
        stream = self._streams.get(key)
        if stream is None:
            stream = self._streams[key] = LiveStream(feed, params)
            stream.task = asyncio.create_task(self._read(key, stream))
        queue: asyncio.Queue = asyncio.Queue(maxsize=WAITING_MESSAGES)
        if stream.newest:
            queue.put_nowait(stream.newest)
        stream.followers.append(queue)
        return stream, queue

    def leave(self, stream: LiveStream, queue: asyncio.Queue) -> None:
        with contextlib.suppress(ValueError):
            stream.followers.remove(queue)

    async def _read(self, key: tuple, stream: LiveStream) -> None:
        """
        Reads the stream every feed.every seconds while anyone follows it, then forgets it.
        """
        try:
            while stream.followers:
                stream.send(await self._sample(stream))
                await asyncio.sleep(stream.feed.every)
        finally:
            self._streams.pop(key, None)

    async def _sample(self, stream: LiveStream) -> str:
        """
        One read, on a worker thread, as a 'sample' message, or a 'note' saying why it failed.
        """
        try:
            if stream.reader is None:
                core = await asyncio.to_thread(self.runner.core, stream.feed.service)
                stream.reader = await asyncio.to_thread(stream.feed.make, core, stream.params)
            return sse_message("sample", await asyncio.to_thread(stream.reader))
        except (ClientError, BotoCoreError, _Hint, FeedProblem) as exc:
            return sse_message("note", {"text": _words(exc), "level": "warn"})
        except Exception as exc:  # noqa: BLE001 - the server's guard: the page gets a note, the log the traceback
            log.error("live %s/%s failed:\n%s", stream.feed.service, stream.feed.name, traceback.format_exc())
            return sse_message("note", {"text": _words(exc), "level": "warn"})

    async def close(self) -> None:
        tasks = [stream.task for stream in self._streams.values() if stream.task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _messages(hub: LiveHub, feed: Feed, params: dict[str, str], request: Request) -> AsyncIterator[str]:
    """
    A page's stream: the samples as they come, a heartbeat between them, and 'done' after LIVE_MINUTES.
    """
    stream, queue = hub.follow(feed, params)
    ends = time.monotonic() + LIVE_MINUTES * 60
    try:
        yield sse_message("start", {"service": feed.service, "feed": feed.name, "params": params,
                                    "every": feed.every, "minutes": LIVE_MINUTES, "about": feed.about})
        while time.monotonic() < ends:
            try:
                yield await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
            except TimeoutError:
                yield HEARTBEAT
            if await request.is_disconnected():
                return
        yield sse_message("done", {"reason": f"Stopped after {LIVE_MINUTES} minutes, so it doesn't read AWS all day.",
                                   "minutes": LIVE_MINUTES})
    finally:
        hub.leave(stream, queue)


def add_live_routes(app: FastAPI, hub: LiveHub, *, check: Callable[[Request, str | None], None], header: str) -> None:
    """
    GET /api/live: the feeds there are. GET /api/live/<service>/<feed>?...: one as Server-Sent Events, 404 for a
    feed there isn't, 400 for parameters it doesn't take or needs.
    """

    @app.get("/api/live")
    def feeds(request: Request, given: str | None = Header(default=None, alias=header)) -> dict[str, Any]:
        check(request, given)
        listed = [{"service": feed.service, "feed": feed.name, "every": feed.every, "about": feed.about,
                   "params": list(feed.params)} for feed in FEEDS.values()]
        return {"feeds": listed, "minutes": LIVE_MINUTES}

    @app.get("/api/live/{service}/{name}")
    def live(service: str, name: str, request: Request,
             given: str | None = Header(default=None, alias=header)) -> StreamingResponse:
        check(request, given)
        feed = FEEDS.get((service, name))
        if feed is None:
            raise HTTPException(status_code=404, detail=f"There's no live {name} for {service}.")
        asked = {key: value for key, value in request.query_params.items() if key != "token"}
        try:
            params = feed_params(feed, asked)
        except FeedProblem as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return StreamingResponse(_messages(hub, feed, params, request), media_type=STREAM_TYPE,
                                 headers=STREAM_HEADERS)
