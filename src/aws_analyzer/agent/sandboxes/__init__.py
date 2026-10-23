"""
The agent's built-in Python workspaces: sandboxes that give the model's code no AWS credentials and no network, so
the agent stays read-only (see workspace.py). LocalSandbox (local.py) runs it on this machine, in Pyodide on Deno;
AgentAnalyzer(python="local") makes one. runner.mjs is what Deno runs for each of its runs.
"""

from __future__ import annotations

from .local import DEFAULT_PACKAGES, PYODIDE_URL, PYODIDE_VERSION, LocalSandbox

SANDBOXES = {"local": LocalSandbox}

__all__ = ["DEFAULT_PACKAGES", "PYODIDE_URL", "PYODIDE_VERSION", "SANDBOXES", "LocalSandbox"]
