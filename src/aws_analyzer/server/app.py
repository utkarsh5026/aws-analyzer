"""
The web app: FastAPI over the ReportRunner. GET / is the page (static/index.html with the session's token in it),
GET /api/catalog the services and their commands, POST /api/run a command's report as HTML, and for now GET /ping, a
check of what the notebook's proxy passes (ping.py).

Every request but the page's own files needs the token the terminal printed: the page's address carries it once
(?token=...), the server keeps it in a cookie for that address, and the page sends it back with each API call in a
header, which another website open in the same browser can't add (CORS), so it can't make this server read AWS for
it. Every answer carries a Content-Security-Policy that runs only this server's own scripts: a report is HTML made by
the Views, which escape what AWS returns, and the policy is the second wall if anything slipped through.
"""

from __future__ import annotations

import secrets
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from .catalog import SERVICES
from .ping import add_ping_routes
from .runner import CantRun, ReportRunner

STATIC = Path(__file__).with_name("static")
TOKEN_HEADER = "X-AWS-Analyzer-Token"
POLICY = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
          "media-src 'self' data: https:; frame-src 'self' data:; connect-src 'self'; object-src 'none'; "
          "base-uri 'none'; form-action 'self'; frame-ancestors 'self'")
SECURITY_HEADERS = {"Content-Security-Policy": POLICY, "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"}
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


def _page_html(token: str) -> str:
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    return page.replace("__TOKEN__", token).replace("__VERSION__", __version__)


def _catalog(runner: ReportRunner) -> dict[str, Any]:
    """
    Every service and the commands the page offers for it (read from the View classes: nothing from AWS).
    """
    services = [{"key": service.key, "title": service.title, "icon": service.icon,
                 "commands": [asdict(command) for command in runner.commands(service.key)]} for service in SERVICES]
    settings = runner.settings
    return {"version": __version__, "region": settings.region or "", "profile": settings.profile or "",
            "services": services}


def _add_routes(app: FastAPI, runner: ReportRunner, token: str, cookie: str) -> None:
    def check_token(given: str | None = Header(default=None, alias=TOKEN_HEADER)) -> None:
        if not _is_token(given, token):
            raise HTTPException(status_code=403, detail="This needs the page's token: open the link the terminal "
                                                        "printed.")

    @app.get("/", response_class=HTMLResponse)
    def page(request: Request) -> Response:
        given = request.query_params.get("token") or request.cookies.get(cookie)
        if not _is_token(given, token):
            return HTMLResponse(LOCKED_PAGE, status_code=403)
        response = HTMLResponse(_page_html(token))
        response.set_cookie(cookie, token, httponly=True, samesite="strict")
        return response

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


def create_app(runner: ReportRunner, token: str, *, cookie: str = "aws_analyzer_token") -> FastAPI:
    """
    The app for one server: its runner, the token its link carries, and the cookie that keeps it (named for the
    port, so two servers on one machine don't take each other's).
    """
    app = FastAPI(title="aws-analyzer", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)

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
    add_ping_routes(app, token=token, cookie=cookie, is_token=lambda given: _is_token(given, token), header=TOKEN_HEADER)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
