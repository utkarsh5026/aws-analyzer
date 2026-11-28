"""
The web app: FastAPI over the ReportRunner. GET / is the page (static/index.html with the session's token in it),
GET /api/catalog the services and their commands, POST /api/run a command's report (as HTML, and as blocks of JSON),
GET /api/style/<service> the stylesheet those blocks are drawn with, the jobs (POST /api/jobs runs a command in the
background, GET /api/jobs/<id>/events follows its progress and reports as Server-Sent Events, DELETE /api/jobs/<id>
stops it: jobs.py), the live streams (GET /api/live/...: live.py), GET /api/data/... the tables a report is drawn from
as JSON (for the services whose Views have data()), GET /api/resources/<service> one service's names for the page's
search (resources.py), GET /api/attention/<service> what needs attention in one service (attention.py) and
GET /api/attention the checks already made, and for now GET /ping, a check of what the notebook's proxy passes
(ping.py).

Every request but the page's own files needs the token the terminal printed: the page's address carries it once
(?token=...), the server keeps it in a cookie for that address, and the page sends it back with each API call in a
header (a stream, which an EventSource opens, as ?token=: it can't add a header), which another website open in the same browser can't add (CORS), so it can't make this server read AWS for
it. Every answer carries a Content-Security-Policy that runs only this server's own scripts: a report is HTML made by
the Views, which escape what AWS returns, and the policy is the second wall if anything slipped through.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from .assets import MissingBuild, PageAssets, page_assets
from .catalog import SERVICES
from .jobs import Job
from .live import LiveHub, add_live_routes
from .ping import add_ping_routes
from .attention import check_for
from .resources import is_searchable
from .runner import CantList, CantRun, NoData, ReportRunner, gives_data, style_of
from .streams import HEARTBEAT, HEARTBEAT_SECONDS, STREAM_HEADERS, STREAM_TYPE, last_seen, sse_message

log = logging.getLogger("aws_analyzer.server")
STATIC = Path(__file__).with_name("static")
TOKEN_HEADER = "X-AWS-Analyzer-Token"
POLICY = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
          "media-src 'self' data: https:; frame-src 'self' data:; connect-src 'self'; object-src 'none'; "
          "base-uri 'none'; form-action 'self'; frame-ancestors 'self'")
SECURITY_HEADERS = {"Content-Security-Policy": POLICY, "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"}
UNBUILT_PAGE = ("<!doctype html><title>aws-analyzer</title><p>This copy of aws-analyzer has no build of its page. "
                "In a checkout, run <code>make web</code>; an installed copy needs installing again. The terminal "
                "running <code>aws-analyzer serve</code> says more.</p>")
LOCKED_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>aws-analyzer</title></head>
<body style="font:16px system-ui,sans-serif;max-width:36rem;margin:15vh auto;padding:0 1rem;line-height:1.6">
<h1 style="font-size:1.4rem">This page needs its link</h1>
<p>Open the address <code>aws-analyzer serve</code> printed in the terminal: it ends in <code>?token=...</code>,
which keeps other people and other websites from using this server. Started it again? Its new link has a new
token.</p></body></html>"""


class RunRequest(BaseModel):
    """
    What the page asks to run: a service's command with what its form's boxes hold, or a call written as in a
    notebook (a report's next step, "errors('orders-etl', since='7d')").
    """

    service: str
    command: str = ""
    arguments: dict[str, str] = Field(default_factory=dict)
    call: str = ""


def _is_token(given: str | None, token: str) -> bool:
    return bool(given) and secrets.compare_digest(given.encode(), token.encode())


def _page_html(token: str, assets: PageAssets) -> str:
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    return page.replace("__TOKEN__", token).replace("__VERSION__", __version__).replace("__ASSETS__", assets.tags())


def _unbuilt_page(problem: MissingBuild) -> HTMLResponse:
    """
    What the browser gets when the page isn't built: a fixed sentence, since the problem names the server's files;
    the problem itself goes to the terminal's log.
    """
    log.error("Can't serve the page: %s", problem)
    return HTMLResponse(UNBUILT_PAGE, status_code=500)


def _catalog(runner: ReportRunner) -> dict[str, Any]:
    """
    Every service and the commands the page offers for it (read from the View classes: nothing from AWS).
    """
    services = [{"key": service.key, "title": service.title, "icon": service.icon, "data": gives_data(service.key),
                 "searchable": is_searchable(service.key), "checkable": check_for(service.key) is not None,
                 "commands": [asdict(command) for command in runner.commands(service.key)]} for service in SERVICES]
    settings = runner.settings
    return {"version": __version__, "region": settings.region or "", "profile": settings.profile or "",
            "services": services}


def _page_response(request: Request, token: str, cookie: str) -> Response:
    """
    The page, for a browser with the token (in the address the first time, then in the cookie this sets), else the
    page that asks for the link; without a build of the page, what to run to make one.
    """
    given = request.query_params.get("token") or request.cookies.get(cookie)
    if not _is_token(given, token):
        return HTMLResponse(LOCKED_PAGE, status_code=403)
    try:
        assets = page_assets()
    except MissingBuild as problem:
        return _unbuilt_page(problem)
    response = HTMLResponse(_page_html(token, assets))
    response.set_cookie(cookie, token, httponly=True, samesite="strict")
    return response


def _add_routes(app: FastAPI, runner: ReportRunner, token: str, cookie: str) -> None:
    def check_token(given: str | None = Header(default=None, alias=TOKEN_HEADER)) -> None:
        if not _is_token(given, token):
            raise HTTPException(status_code=403, detail="This needs the page's token: open the link the terminal "
                                                        "printed.")

    @app.get("/", response_class=HTMLResponse)
    def page(request: Request) -> Response:
        return _page_response(request, token, cookie)

    catalog: dict[str, Any] = {}

    @app.get("/api/catalog", dependencies=[Depends(check_token)])
    def services() -> dict[str, Any]:
        if not catalog:
            catalog.update(_catalog(runner))
        return catalog

    @app.post("/api/run", dependencies=[Depends(check_token)])
    def run(asked: RunRequest) -> dict[str, Any]:
        try:
            if asked.call:
                report = runner.run_call(asked.service, asked.call)
            else:
                report = runner.run(asked.service, asked.command, asked.arguments)
        except CantRun as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return asdict(report)

    _add_style_route(app, check_token)
    _add_data_routes(app, runner, check_token)
    _add_resource_routes(app, runner, check_token)
    _add_job_routes(app, runner, check_token)
    _add_job_stream(app, runner, token)
    _add_attention_routes(app, runner, check_token)


def _add_attention_routes(app: FastAPI, runner: ReportRunner, check_token: Callable[..., None]) -> None:
    """
    GET /api/attention/<service>[?refresh=true]: what needs attention in one service, from its overview, with the
    report it came from; kept by the runner, so a second look reads nothing. GET /api/attention: the checks kept,
    for Home (nothing is read). 400 for a service there isn't, 422 when it has no overview to check.
    """

    @app.get("/api/attention", dependencies=[Depends(check_token)])
    def checked() -> dict[str, Any]:
        return {"checked": [asdict(attention) for attention in runner.checked()]}

    @app.get("/api/attention/{service}", dependencies=[Depends(check_token)])
    def attention(service: str, refresh: bool = False) -> dict[str, Any]:
        try:
            return asdict(runner.attention(service, refresh=refresh))
        except CantRun as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except CantList as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


def _add_style_route(app: FastAPI, check_token: Callable[..., None]) -> None:
    """
    GET /api/style/<service>: the CSS a service's reports are drawn with (its stylesheet and the theme's rules), for
    the reports the page draws from their blocks.
    """

    @app.get("/api/style/{service}", dependencies=[Depends(check_token)])
    def style(service: str) -> dict[str, str]:
        try:
            return {"service": service, "css": style_of(service)}
        except CantRun as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


def _started(start: Callable[[], Job]) -> dict[str, Any]:
    try:
        return start().summary()
    except CantRun as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _job_named(runner: ReportRunner, job_id: str) -> Job:
    job = runner.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="There's no such job: it ended a while ago, or the server was "
                                                    "started again.")
    return job


def _add_job_routes(app: FastAPI, runner: ReportRunner, check_token: Callable[..., None]) -> None:
    """
    POST /api/jobs starts a command (what the form's boxes hold, or a call as written in a notebook) and answers at
    once with the job; GET /api/jobs lists them, GET /api/jobs/<id> is one with its events so far (?after=n: the
    ones after the nth), DELETE /api/jobs/<id> stops it.
    """

    @app.post("/api/jobs", dependencies=[Depends(check_token)])
    def start(asked: RunRequest) -> dict[str, Any]:
        if asked.call:
            return _started(lambda: runner.start_call_job(asked.service, asked.call))
        return _started(lambda: runner.start_job(asked.service, asked.command, asked.arguments))

    @app.get("/api/jobs", dependencies=[Depends(check_token)])
    def jobs() -> dict[str, Any]:
        return {"jobs": [job.summary() for job in runner.jobs.all()]}

    @app.get("/api/jobs/{job_id}", dependencies=[Depends(check_token)])
    def job(job_id: str, after: int = 0) -> dict[str, Any]:
        found = _job_named(runner, job_id)
        events = [asdict(event) for event in found.events_after(after)]
        return {**found.summary(), "events_after": events}

    @app.delete("/api/jobs/{job_id}", dependencies=[Depends(check_token)])
    def stop(job_id: str) -> dict[str, Any]:
        _job_named(runner, job_id)
        return runner.jobs.stop(job_id).summary()


async def _job_events(job: Job, after: int, request: Request) -> AsyncIterator[str]:
    """
    A job's events after the `after`th, as they come, ending with 'done' (said again to a page that comes back after
    it ended); a heartbeat while nothing happens; nothing more once the page has gone.
    """
    sent = after
    with job.listening() as woken:
        while True:
            woken.clear()
            for event in job.events_after(sent):
                sent = event.number
                yield sse_message(event.kind, event.data, event.number)
                if event.kind == "done":
                    return
            if not job.is_running:
                yield sse_message("done", {"state": job.state, "seconds": job.seconds})
                return
            if await request.is_disconnected():
                return
            try:
                await asyncio.wait_for(woken.wait(), HEARTBEAT_SECONDS)
            except TimeoutError:
                yield HEARTBEAT


def _add_job_stream(app: FastAPI, runner: ReportRunner, token: str) -> None:
    """
    GET /api/jobs/<id>/events?token=...: the job's events as Server-Sent Events (streams.py), from the one after
    Last-Event-ID (or ?after=) on.
    """

    @app.get("/api/jobs/{job_id}/events")
    def events(job_id: str, request: Request, after: int | None = None,
               last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
               given: str | None = Header(default=None, alias=TOKEN_HEADER)) -> StreamingResponse:
        _check_stream_token(request, given, token)
        job = _job_named(runner, job_id)
        stream = _job_events(job, last_seen(last_event_id, after), request)
        return StreamingResponse(stream, media_type=STREAM_TYPE, headers=STREAM_HEADERS)


def _check_stream_token(request: Request, given: str | None, token: str) -> None:
    """
    A stream's token: in the header like any API call, or as ?token=, since an EventSource can't add a header.
    """
    if not (_is_token(given, token) or _is_token(request.query_params.get("token"), token)):
        raise HTTPException(status_code=403, detail="This needs the page's token: open the link the terminal printed.")


def _add_resource_routes(app: FastAPI, runner: ReportRunner, check_token: Callable[..., None]) -> None:
    """
    GET /api/resources/<service>[?refresh=true]: one service's functions, buckets, tables... for the page's search,
    kept by the runner so a second search reads nothing. 400 for a service there isn't, 422 when it can't be listed.
    """

    @app.get("/api/resources/{service}", dependencies=[Depends(check_token)])
    def resources(service: str, refresh: bool = False) -> dict[str, Any]:
        try:
            return asdict(runner.resources(service, refresh=refresh))
        except CantRun as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except CantList as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


def _data_answer(read: Callable[[], Any]) -> dict[str, Any]:
    """
    A report's data as the API answers it: 400 when the page asked for something that can't run, 422 when it ran
    and gave no tables, with the reason in words either way.
    """
    try:
        return asdict(read())
    except CantRun as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NoData as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _add_data_routes(app: FastAPI, runner: ReportRunner, check_token: Callable[..., None]) -> None:
    """
    GET /api/data/<service>?call=errors('orders-etl') and GET /api/data/<service>/<command>?name=orders-etl&since=7d:
    the tables behind a report, its columns' meanings and kinds, its charts and its summary, as JSON.
    """

    @app.get("/api/data/{service}", dependencies=[Depends(check_token)])
    def data_of_call(service: str, call: str) -> dict[str, Any]:
        return _data_answer(lambda: runner.data_call(service, call))

    @app.get("/api/data/{service}/{command}", dependencies=[Depends(check_token)])
    def data_of_command(service: str, command: str, request: Request) -> dict[str, Any]:
        arguments = dict(request.query_params)
        return _data_answer(lambda: runner.data(service, command, arguments))


def create_app(runner: ReportRunner, token: str, *, cookie: str = "aws_analyzer_token") -> FastAPI:
    """
    The app for one server: its runner, the token its link carries, and the cookie that keeps it (named for the
    port, so two servers on one machine don't take each other's).
    """
    hub = LiveHub(runner)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        runner.jobs.shutdown()
        await hub.close()

    app = FastAPI(title="aws-analyzer", version=__version__, docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)

    @app.middleware("http")
    async def secured(request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    @app.exception_handler(HTTPException)
    async def said(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"message": exc.detail}, status_code=exc.status_code)

    _add_routes(app, runner, token, cookie)
    add_live_routes(app, hub, check=lambda request, given: _check_stream_token(request, given, token),
                    header=TOKEN_HEADER)
    add_ping_routes(app, token=token, cookie=cookie, is_token=lambda given: _is_token(given, token), header=TOKEN_HEADER)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
