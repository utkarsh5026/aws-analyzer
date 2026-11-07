"""
A small MCP server for the agent's tests, run as `python tests/fake_mcp_server.py` over stdio: a ticket desk with a
tool that only reads (find_tickets, marked readOnlyHint), one that writes (close_ticket, unmarked), one that fails
(broken) and one that answers with structured content (ticket_counts). FAKE_MCP_SAY=... prints a line to stderr at
start, FAKE_MCP_QUIT=1 makes it quit before it answers, so tests can see what a failed start says.
"""

from __future__ import annotations

import os
import sys

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

TICKETS = {"T-1": "orders-etl fails at 02:00", "T-2": "report-api is slow", "T-3": "refund batch stuck"}

desk = FastMCP("ticket-desk", instructions="Tickets about the acme team's AWS jobs.")


@desk.tool(annotations=ToolAnnotations(readOnlyHint=True, title="Find tickets"))
def find_tickets(text: str = "", limit: int = 10) -> str:
    """
    Finds open tickets whose title holds the text.
    """
    found = [f"{key}: {title}" for key, title in TICKETS.items() if text.lower() in title.lower()]
    return "\n".join(found[:limit]) or "no tickets"


@desk.tool()
def close_ticket(ticket: str, note: str = "") -> str:
    """
    Closes a ticket, with a note.
    """
    if TICKETS.pop(ticket, None) is None:
        raise ValueError(f"no ticket {ticket}")
    return f"closed {ticket}"


@desk.tool(annotations=ToolAnnotations(readOnlyHint=True))
def broken() -> str:
    """
    Always fails.
    """
    raise RuntimeError("the desk is down")


@desk.tool(annotations=ToolAnnotations(readOnlyHint=True))
def ticket_counts() -> dict:
    """
    How many tickets are open, by team.
    """
    return {"open": len(TICKETS), "by_team": {"data": 2, "web": 1}, "token": "password=hunter2"}


if __name__ == "__main__":
    said = os.environ.get("FAKE_MCP_SAY")
    if said:
        print(said, file=sys.stderr, flush=True)
    if os.environ.get("FAKE_MCP_QUIT"):
        sys.exit(3)
    desk.run()
