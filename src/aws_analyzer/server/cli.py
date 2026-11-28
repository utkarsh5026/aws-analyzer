"""
The terminal command: `aws-analyzer serve` (or `python -m aws_analyzer serve`) starts the web page's server and prints
the address to open it at. It runs until Ctrl+C, and it's a process of its own, so a kernel that restarts or a
notebook that's closed doesn't stop it.
"""

from __future__ import annotations

import argparse
import errno
import logging
import secrets
import socket
import sys
from functools import partial

import boto3
import uvicorn
from botocore.exceptions import BotoCoreError

from .. import __version__
from .app import create_app
from .links import PageLink, notebook_instance_address, page_link
from .runner import ReportRunner, Settings

DEFAULT_PORT = 8765
PORTS_TRIED = 20
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aws-analyzer", description="aws-analyzer's reports, in a browser tab.")
    commands = parser.add_subparsers(dest="command", metavar="serve")
    serve = commands.add_parser("serve", help="start the web page's server and print its address",
                                description="Start the web page's server and print the address to open it at. "
                                            "Ctrl+C stops it.")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT,
                       help=f"the port to listen on (default {DEFAULT_PORT}; the next free one if it's taken)")
    serve.add_argument("--host", default="127.0.0.1",
                       help="the address to listen on (default 127.0.0.1: only this machine, which SageMaker's "
                            "proxy forwards to)")
    serve.add_argument("--region", help="the AWS region to read (default: boto3's, the notebook's own on SageMaker)")
    serve.add_argument("--profile", help="the AWS profile to read with (default: boto3's)")
    serve.add_argument("--max-rows", type=int, default=100, help="the most rows a table shows (default 100)")
    return parser


def _is_free(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError as exc:
            if exc.errno in (errno.EADDRINUSE, errno.EACCES):
                return False
            raise
    return True


def free_port(host: str, wanted: int) -> int:
    """
    The port asked for, or the next free one after it (another server, a TensorBoard, may have it).
    """
    for port in range(wanted, wanted + PORTS_TRIED):
        if _is_free(host, port):
            return port
    raise SystemExit(f"Ports {wanted} to {wanted + PORTS_TRIED - 1} are all taken: pick another with --port.")


def _region_in_use(settings: Settings) -> str:
    """
    The region the Views will read: --region, else the one boto3 finds (AWS_DEFAULT_REGION, the profile's, the
    notebook's own on SageMaker). Nothing is asked of AWS.
    """
    if settings.region:
        return settings.region
    try:
        found = boto3.Session(profile_name=settings.profile).region_name
    except BotoCoreError as exc:
        raise SystemExit(f"Can't read AWS with these settings: {exc}") from exc
    return found or "no region (boto3 found none: pass --region us-east-1, or set AWS_DEFAULT_REGION)"


def ping_link(url: str) -> str:
    """
    The proxy check's address (server/ping.py): the page's own, with ping before its ?token=.
    """
    base, _, query = url.partition("?")
    return f"{base}ping?{query}" if query else f"{base}ping"


def welcome(link: PageLink, settings: Settings, host: str, *, moved_from: int | None = None) -> str:
    """
    What the terminal shows once the server is up: where to open the page, and what it reads.
    """
    lines = [f"aws-analyzer {__version__} is serving its reports for your browser.", "", f"  Open: {link.url}"]
    if moved_from:
        lines += [f"        (port {moved_from} was taken, so it's on the next free one)"]
    if link.is_proxied:
        lines += [f"        (through Jupyter's proxy on {link.where}: the same sign-in as your notebook)"]
        if link.is_guess:
            lines += ["        SageMaker didn't say this notebook's address (it needs sagemaker:DescribeNotebookInstance),",
                      "        so this one is guessed from its name. If it doesn't open, take the address your notebook",
                      f"        is open at, up to .sagemaker.aws, and add /proxy/{link.port}/?token={link.token}"]
        lines += [f"  On this machine: {link.local}"]
        lines += ["", f"  Checking the proxy for live updates: {ping_link(link.url)}"]
    profile = f" with the profile {settings.profile}" if settings.profile else ""
    lines += ["", f"  Reads AWS in {_region_in_use(settings)}{profile}, read-only, as the notebook does."]
    if host not in LOOPBACK:
        lines += [f"  Listening on {host}: anyone who can reach it and has the link can read what it reads."]
    lines += ["  Keep the link to yourself: its token is what lets a browser in.", "", "Ctrl+C stops it."]
    return "\n".join(lines)


def serve(args: argparse.Namespace) -> int:
    settings = Settings(region=args.region, profile=args.profile, max_rows=args.max_rows)
    port = free_port(args.host, args.port)
    token = secrets.token_urlsafe(24)
    app = create_app(ReportRunner(settings), token, cookie=f"aws_analyzer_{port}")
    moved_from = args.port if port != args.port else None
    find_address = partial(notebook_instance_address, profile=settings.profile)
    link = page_link(port, token, host=args.host, find_address=find_address)
    print(welcome(link, settings, args.host, moved_from=moved_from), flush=True)
    uvicorn.run(app, host=args.host, port=port, log_level="warning")
    print("Stopped.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """
    `aws-analyzer serve [--port 8765] [--region ...] [--profile ...]`.
    """
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command != "serve":
        parser.print_help()
        return 2
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    return serve(args)
