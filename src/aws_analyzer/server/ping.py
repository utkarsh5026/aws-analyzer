"""
A check of what the notebook's proxy passes, for the web app's next phase (live updates): GET /ping is a page that
opens a Server-Sent Events stream (/api/ping-stream) and a WebSocket (/api/ping-socket), each sending a line a second
for ten seconds, and says whether the lines arrived one by one or all at once when the response ended (some
jupyter-server-proxy versions hold a streamed body back; 4.x streams only a request whose Accept header is exactly
text/event-stream, so the page's fetch sends it), and whether the socket carried messages both ways.

It's temporary: once the transport is picked from what it finds, these routes go. Nothing here reads AWS. The page
needs the link's token like the main page; an EventSource can't send a header, so the stream takes it as a query
parameter, and a WebSocket as its first message.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import time
from collections.abc import AsyncIterator, Callable
from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse

PINGS = 10
PING_SECONDS = 1.0
TOKEN_WAIT_SECONDS = 5.0
LONGEST_ECHO = 200
WRONG_TOKEN = 1008
SOCKET_LIBRARIES = ("websockets", "wsproto")
STREAM_HEADERS = {"X-Accel-Buffering": "no"}

IsToken = Callable[[str | None], bool]

PING_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="aws-analyzer-token" content="__TOKEN__">
<meta name="aws-analyzer-server" content="__SERVER__">
<title>aws-analyzer: proxy check</title>
<link rel="icon" href="static/icons/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="static/ping.css">
<script type="module" src="static/ping.js"></script>
</head>
<body>
<main class="aa-ping">
  <h1>Does the notebook's proxy pass live updates?</h1>
  <p>This page opens three connections to the server, each sending a line a second for ten seconds, and checks how they
  arrive here. It reads nothing from AWS. It takes about fifteen seconds; then copy the results and send them back.</p>
  <button type="button" id="again" disabled>Checking…</button>
  <section aria-labelledby="stream-title">
    <h2 id="stream-title">Server-Sent Events <code>/api/ping-stream</code></h2>
    <p class="aa-verdict" id="stream-verdict" role="status">Waiting…</p>
    <ol class="aa-lines" id="stream-lines"></ol>
  </section>
  <section aria-labelledby="fetch-title">
    <h2 id="fetch-title">A streamed fetch of the same <code>/api/ping-stream</code>, as <code>text/event-stream</code></h2>
    <p class="aa-verdict" id="fetch-verdict" role="status">Waiting…</p>
    <ol class="aa-lines" id="fetch-lines"></ol>
  </section>
  <section aria-labelledby="socket-title">
    <h2 id="socket-title">WebSocket <code>/api/ping-socket</code></h2>
    <p class="aa-verdict" id="socket-verdict" role="status">Waiting…</p>
    <ol class="aa-lines" id="socket-lines"></ol>
  </section>
  <section aria-labelledby="results-title">
    <h2 id="results-title">Results to send back</h2>
    <button type="button" id="copy">Copy the results</button>
    <pre id="results" tabindex="0">Waiting for the checks to finish…</pre>
  </section>
</main>
</body>
</html>"""


def _now_ms() -> int:
    return int(time.time() * 1000)


def socket_library() -> str:
    """
    The WebSocket library uvicorn will use ('websockets 15.0'), or '' when there's none and it can't take a WebSocket
    at all: then a failed socket is this server's doing, not the proxy's.
    """
    for name in SOCKET_LIBRARIES:
        if importlib.util.find_spec(name) is None:
            continue
        try:
            return f"{name} {version(name)}"
        except PackageNotFoundError:
            return name
    return ""


def _server_facts() -> str:
    try:
        uvicorn_version = version("uvicorn")
    except PackageNotFoundError:
        uvicorn_version = "unknown"
    return json.dumps({"uvicorn": uvicorn_version, "socket_library": socket_library() or "none"})


def ping_page_html(token: str) -> str:
    """
    The /ping page, with the session's token and what the server runs on (its uvicorn and WebSocket library).
    """
    server = _server_facts().replace('"', "&quot;")
    return PING_PAGE.replace("__TOKEN__", token).replace("__SERVER__", server)


async def ping_events() -> AsyncIterator[str]:
    """
    The stream's events: PINGS 'ping' events PING_SECONDS apart, each with its number and when the server sent it
    (epoch milliseconds), then 'done'.
    """
    for number in range(1, PINGS + 1):
        if number > 1:
            await asyncio.sleep(PING_SECONDS)
        line = json.dumps({"n": number, "of": PINGS, "sent": _now_ms()})
        yield f"event: ping\ndata: {line}\n\n"
    yield "event: done\ndata: {}\n\n"


async def _wait_for_token(socket: WebSocket, is_token: IsToken) -> bool:
    try:
        first = await asyncio.wait_for(socket.receive_text(), timeout=TOKEN_WAIT_SECONDS)
    except TimeoutError:
        return False
    try:
        given = json.loads(first).get("token")
    except (ValueError, AttributeError):
        return False
    return isinstance(given, str) and is_token(given)


async def _echo_until(socket: WebSocket, due: float) -> None:
    """
    Answers each message the page sends with a 'pong' until `due` (the loop's time), so the check sees messages go
    both ways, as a job's cancel would.
    """
    loop = asyncio.get_running_loop()
    while (left := due - loop.time()) > 0:
        try:
            said = await asyncio.wait_for(socket.receive_text(), timeout=left)
        except TimeoutError:
            return
        await socket.send_json({"kind": "pong", "said": said[:LONGEST_ECHO], "sent": _now_ms()})


async def ping_socket(socket: WebSocket, is_token: IsToken) -> None:
    """
    The WebSocket's side of the check: the token as the first message (else it closes with 1008), then PINGS 'tick'
    messages PING_SECONDS apart, a 'pong' for every message the page sends meanwhile, and a normal close.
    """
    await socket.accept()
    if not await _wait_for_token(socket, is_token):
        await socket.close(code=WRONG_TOKEN, reason="This needs the page's token")
        return
    await socket.send_json({"kind": "hello", "of": PINGS, "sent": _now_ms()})
    start = asyncio.get_running_loop().time()
    for number in range(1, PINGS + 1):
        await _echo_until(socket, start + number * PING_SECONDS)
        await socket.send_json({"kind": "tick", "n": number, "of": PINGS, "sent": _now_ms()})
    await socket.close()


def add_ping_routes(app: FastAPI, *, token: str, cookie: str, is_token: IsToken, header: str) -> None:
    """
    GET /ping, GET /api/ping-stream and the WebSocket /api/ping-socket, each checking the token its own way.
    """

    @app.get("/ping", response_class=HTMLResponse)
    def ping_page(request: Request) -> Response:
        given = request.query_params.get("token") or request.cookies.get(cookie)
        if not is_token(given):
            raise HTTPException(status_code=403, detail="This needs the page's token: open the link the terminal "
                                                        "printed, with ping before ?token=.")
        response = HTMLResponse(ping_page_html(token))
        response.set_cookie(cookie, token, httponly=True, samesite="strict")
        return response

    @app.get("/api/ping-stream")
    def ping_stream(request: Request, given: str | None = Header(default=None, alias=header)) -> Response:
        if not is_token(given or request.query_params.get("token")):
            raise HTTPException(status_code=403, detail="This needs the page's token.")
        return StreamingResponse(ping_events(), media_type="text/event-stream", headers=STREAM_HEADERS)

    @app.websocket("/api/ping-socket")
    async def ping_socket_route(socket: WebSocket) -> None:
        try:
            await ping_socket(socket, is_token)
        except WebSocketDisconnect:
            return
