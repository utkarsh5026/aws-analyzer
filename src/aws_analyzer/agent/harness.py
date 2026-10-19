"""
The agent loop: LangChain's deepagents, built once with the model, the tools and the system prompt, and one
question streamed through it (stream_turn), counting the model's calls and tokens and stopping when asked or out of
time. What changes how the agent works rather than what it can read (a subagent, a middleware, another memory)
goes in build_graph(); everything about LangGraph's messages stays in this file.

_MIN_PYTHON is deepagents' floor; the package's own is 3.10, and everything but the agent itself runs there.
HIDDEN_TOOLS are deepagents' tools the model never sees (see _hide_tools).
"""

from __future__ import annotations

import functools
import sys
from typing import Any, Callable, Iterable

from .._kit.deps import _require
from .answers import AgentAnswer
from .budget import _Run
from .llm import answer_cost, explain_model_error

_MIN_PYTHON = (3, 11)
HIDDEN_TOOLS = frozenset({"task", "execute"})
_OVERTIME_SECONDS = 60
_AI_MESSAGES = ("AIMessage", "AIMessageChunk")


def check_ready() -> None:
    """
    ImportError saying what's missing when this Python or its packages can't run the agent.
    """
    if sys.version_info < _MIN_PYTHON:
        raise ImportError("The agent needs Python 3.11 or newer: it runs on deepagents, which does. This is "
                          f"Python {sys.version_info.major}.{sys.version_info.minor}")
    _require("deepagents", "The agent", package="aws-analyzer[agent]")
    _require("langgraph.checkpoint.memory", "The agent's conversation memory", package="langgraph")


def build_graph(model: Any, tools: list[Callable[..., Any]], system_prompt: str, *,
                hidden: Iterable[str] = HIDDEN_TOOLS) -> Any:
    """
    The deepagents agent: the model, the tools, the instructions and a memory of each conversation (an
    InMemorySaver, keyed by the thread ID each question is asked in). `hidden` are deepagents' own tools the model
    is never shown.
    """
    check_ready()
    deepagents = _require("deepagents", "The agent", package="aws-analyzer[agent]")
    memory = _require("langgraph.checkpoint.memory", "The agent's conversation memory", package="langgraph")
    return deepagents.create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=[_hide_tools(set(hidden))],
        checkpointer=memory.InMemorySaver(),
        name="aws-analyzer",
    )


def stream_turn(graph: Any, content: str, *, thread: str, run: _Run, answer: AgentAnswer,  # noqa: PLR0913
                model_prices: dict[str, tuple[float, float]] | None = None,
                progress: Callable[..., None] | None = None, provider: str = "") -> str:
    """
    Streams one question (`content`) through the graph in the conversation `thread`, and returns the answer's
    text. Fills `answer` as it goes (model calls, tokens, the model that served it) and keeps `run.cost` current
    for the tools' cost budget, at model_prices= or, for a `provider` with list prices (Amazon Bedrock), those.
    Stops when `run.stop` is set, the kernel is interrupted (the notebook's stop button: it keeps what it found) or
    the time budget is well past. A model or loop that fails, whatever it raises, sets answer.error (what it means
    with this provider) and answer.exception instead of raising.
    """
    cost_of = functools.partial(answer_cost, model_prices=model_prices, provider=provider)
    base = answer.model
    steps = 0
    text = ""
    message = {"role": "user", "content": content}
    config = {"configurable": {"thread_id": thread}, "recursion_limit": run.budget.steps}
    try:
        for update in graph.stream({"messages": [message]}, config=config, stream_mode="updates"):
            for msg in _model_messages(update):
                _count_usage(msg, answer, base, run, cost_of)
                if not getattr(msg, "tool_calls", None):
                    text = _message_text(msg) or text
            steps += 1
            if progress:
                progress(steps)
            reason = _stop_reason(run)
            if reason:
                answer.stopped = reason
                break
    except KeyboardInterrupt:
        answer.stopped = "stopped"
    except Exception as exc:  # noqa: BLE001
        answer.stopped = "steps" if type(exc).__name__ == "GraphRecursionError" else "error"
        answer.error = explain_model_error(exc, provider)
        answer.exception = exc
    return text


def _model_messages(update: Any) -> list[Any]:
    """
    The model's messages in one LangGraph stream update, from every node in it.
    """
    return [msg for node in (update or {}).values() for msg in _update_messages(node)
            if type(msg).__name__ in _AI_MESSAGES]


def _count_usage(message: Any, answer: AgentAnswer, base: str, run: _Run,
                 cost_of: Callable[[str, int, int], float | None]) -> None:
    """
    Adds one model message to the answer: a model call, its tokens, and the model that served it, which for a
    deployment reads 'prod-gpt-4o (gpt-4o-2024-11-20)'. Keeps run.cost current for the tools' cost budget, at what
    cost_of(model, input_tokens, output_tokens) says the tokens so far cost.
    """
    answer.model_calls += 1
    usage = getattr(message, "usage_metadata", None) or {}
    answer.input_tokens += int(usage.get("input_tokens") or 0)
    answer.output_tokens += int(usage.get("output_tokens") or 0)
    served = (getattr(message, "response_metadata", None) or {}).get("model_name")
    if served:
        answer.model = served if base in ("", served) else f"{base} ({served})"
    cost = cost_of(answer.model, answer.input_tokens, answer.output_tokens)
    with run.lock:
        run.cost = cost or 0.0


def _stop_reason(run: _Run) -> str:
    """
    Why the loop stops now: 'stopped' when run.stop is set, 'time' once the time budget is _OVERTIME_SECONDS past
    (the tools stopped at the budget, but the model kept going); '' to go on.
    """
    if run.stop.is_set():
        return "stopped"
    if run.elapsed > run.budget.seconds + _OVERTIME_SECONDS:
        return "time"
    return ""


def _update_messages(node: Any) -> list[Any]:
    """
    The messages a LangGraph stream update carries for one node (a list, or an Overwrite wrapping one).
    """
    if not isinstance(node, dict):
        return []
    messages = node.get("messages") or []
    messages = getattr(messages, "value", messages)
    return list(messages) if isinstance(messages, (list, tuple)) else [messages]


def _message_text(message: Any) -> str:
    """
    The text of a model message: its content when that's a string, else its text blocks joined.
    """
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    parts = [block.get("text", "") if isinstance(block, dict) else str(block)
             for block in content or [] if not isinstance(block, dict) or block.get("type") in ("text", None)]
    return "".join(parts).strip()


def _hide_tools(names: set[str]) -> Any:
    """
    A deepagents middleware that keeps these tools away from the model (and refuses a call to one anyway):
    'task' (deepagents' general-purpose subagent, which the agent doesn't need) and 'execute' (shell commands, which
    the in-memory files deepagents uses can't run anyway).
    """
    types = _require("langchain.agents.middleware.types", "The agent", package="aws-analyzer[agent]")
    messages = _require("langchain_core.messages", "The agent", package="aws-analyzer[agent]")

    def visible(request: Any) -> Any:
        tools = [t for t in request.tools
                 if (t.get("name") if isinstance(t, dict) else getattr(t, "name", None)) not in names]
        return request.override(tools=tools)

    def refused(request: Any) -> Any:
        name = request.tool_call.get("name")
        if name not in names:
            return None
        return messages.ToolMessage(content=f"Error: {name} isn't available.",
                                    tool_call_id=request.tool_call.get("id") or "", name=name, status="error")

    class HideTools(types.AgentMiddleware):
        def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
            return handler(visible(request))

        async def awrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
            return await handler(visible(request))

        def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
            return refused(request) or handler(request)

        async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
            return refused(request) or await handler(request)

    return HideTools()
