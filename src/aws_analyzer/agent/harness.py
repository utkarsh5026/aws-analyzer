"""
The agent loop: LangChain's deepagents, built once with the model, the tools and the system prompt, and one
question streamed through it (stream_turn), counting the model's calls and tokens and stopping when asked or out of
time. What changes how the agent works rather than what it can read (a subagent, a middleware, another memory)
goes in build_graph(); everything about LangGraph's messages stays in this file.

_MIN_PYTHON is deepagents' floor; the package's own is 3.10, and everything but the agent itself runs there.
HIDDEN_TOOLS are deepagents' tools the model never sees (see _hide_tools). PLAN_TOOL is LangChain's write_todos (its
TodoListMiddleware, which deepagents doesn't add by itself): the model writes a short plan for a question of several
steps and ticks it off as it goes, and _record_plan keeps each version on the question and tells its listener, so the
chat window and the report show it. _PLAN_PROMPT and _PLAN_TOOL_TEXT replace the middleware's own instructions, which
are written for coding tasks and run to about a thousand tokens. FILE_TOOLS are deepagents' file tools (with
the parameter a call's line names first): with a Python workspace they work on its files (workspace/files.py), and
_record_file_tools counts each call against the question's budget and records it like the toolkits' tools.

A question pauses when a tool asks the user about a big read (pause_for: LangGraph's interrupt, kept with the
conversation by the checkpointer): the stream ends, pauses() lists what's waiting, and resume_turn() goes on with the
user's choices.
"""

from __future__ import annotations

import functools
import sys
import time
from typing import Any, Callable, Iterable

from .._kit.deps import _require
from .._kit.text import _clip
from .answers import AgentAnswer, AgentStep, ToolCall, parse_plan
from .budget import _REFUSED, _Refused, _Run, check_budget
from .llm import answer_cost, explain_model_error
from .memory import memory_middleware, with_memory

_MIN_PYTHON = (3, 11)
HIDDEN_TOOLS = frozenset({"task", "execute"})
PLAN_TOOL = "write_todos"
_PLAN_PROMPT = """## Your plan (`write_todos`)

For a question that needs three or more tool calls, first write a short plan with `write_todos`: two to six steps,
each a few plain words saying what you'll check ("check every function's health", "read orders-etl's errors",
"read the code where it fails"). The user sees it as a checklist that ticks off as you work. Mark a step in_progress
when you start it and completed as soon as it's done, add or drop steps when what you find changes the plan, and
never call `write_todos` twice in one turn. Skip the plan for a question one or two tool calls answer. The plan isn't
the answer: write the answer in a message after your last `write_todos` call."""
_PLAN_TOOL_TEXT = ("Writes your plan for the question as a checklist the user sees: the whole list each time, every "
                   "step with its status (pending, in_progress or completed). Use it only for questions that need "
                   "three or more tool calls.")
FILE_TOOLS = {"ls": "path", "read_file": "file_path", "write_file": "file_path", "edit_file": "file_path",
              "glob": "pattern", "grep": "pattern", "delete": "file_path"}
_FILE_SUMMARY_CHARACTERS = 80
_FILE_RESULT_CHARACTERS = 4000
_OVERTIME_SECONDS = 60
_WRAP_UP_STEPS = 8
_WRAP_UP_SECONDS = 90
_WRAP_UP = ("[The user pressed stop before you finished. Don't call any more tools: they won't run. Answer the "
            "question now from what your tools already returned, and say what you didn't get to check.]")
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


def new_checkpointer() -> Any:
    """
    Where the conversations are kept: LangGraph's InMemorySaver, for as long as the kernel runs.
    """
    memory = _require("langgraph.checkpoint.memory", "The agent's conversation memory", package="langgraph")
    return memory.InMemorySaver()


def build_graph(model: Any, tools: list[Callable[..., Any]], system_prompt: str, *,  # noqa: PLR0913
                hidden: Iterable[str] = HIDDEN_TOOLS, backend: Any = None, notes: Any = None,
                current_run: Callable[[], _Run] | None = None, plan: bool = True, checkpointer: Any = None) -> Any:
    """
    The deepagents agent: the model, the tools, the instructions and a memory of each conversation (`checkpointer`,
    by default a new InMemorySaver, keyed by the thread ID each question is asked in). `hidden` are deepagents' own
    tools the model is never shown. `backend` is where its file tools keep their files (the Python workspace's, from
    workspace/files.py; by default deepagents' own in-memory files). `notes` is the agent's memory between
    conversations (memory.py's backend, or None): its file tools reach it at MEMORY_FOLDER, and its notes go in the
    prompt when a conversation starts. With a backend or notes and `current_run` (the question being answered),
    each file tool call is recorded on it and counted against its budget. With `plan`, the model has write_todos
    (PLAN_TOOL) to write its plan, which is kept on `current_run` when there is one.
    """
    check_ready()
    deepagents = _require("deepagents", "The agent", package="aws-analyzer[agent]")
    if notes is not None:
        backend = with_memory(backend, notes)
    middleware = [_hide_tools(set(hidden))]
    if notes is not None:
        middleware.append(memory_middleware(backend))
    if plan:
        middleware.append(_planning())
        if current_run is not None:
            middleware.append(_record_plan(current_run))
    if current_run is not None and backend is not None:
        middleware.append(_record_file_tools(current_run))
    return deepagents.create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=middleware,
        checkpointer=checkpointer if checkpointer is not None else new_checkpointer(),
        name="aws-analyzer",
        **({"backend": backend} if backend is not None else {}),
    )


def stream_turn(graph: Any, content: str, *, thread: str, run: _Run, answer: AgentAnswer,  # noqa: PLR0913
                model_prices: dict[str, tuple[float, float]] | None = None,
                progress: Callable[..., None] | None = None, provider: str = "") -> str:
    """
    Streams one question (`content`) through the graph in the conversation `thread`, and returns the answer's
    text. Fills `answer` as it goes (model calls, tokens, the model that served it) and keeps `run.cost` current
    for the tools' cost budget, at model_prices= or, for a `provider` with list prices (Amazon Bedrock), those.
    The run's listener is told what the model says before calling tools (the tools tell it their own steps). Each step comes whole: the model isn't streamed token by token, which on Bedrock would
    need another permission (InvokeModelWithResponseStream).
    Stops when `run.stop` is set, the kernel is interrupted (the notebook's stop button: it keeps what it found) or
    the time budget is well past. Stopped by `run.stop` before it wrote an answer, it asks the model for one
    (_wrap_up). A model or loop that fails, whatever it raises, sets answer.error (what it means with this provider)
    and answer.exception instead of raising. A tool that asks the user about a big read ends the stream with no text
    yet: pauses() says what's waiting.
    """
    message = {"role": "user", "content": content}
    return _stream(graph, {"messages": [message]}, thread=thread, run=run, answer=answer, model_prices=model_prices,
                   progress=progress, provider=provider)


def resume_turn(graph: Any, choices: dict[str, str], *, thread: str, run: _Run,  # noqa: PLR0913
                answer: AgentAnswer, model_prices: dict[str, tuple[float, float]] | None = None,
                progress: Callable[..., None] | None = None, provider: str = "") -> str:
    """
    Goes on with a paused question once the user has chosen: `choices` is each pause's ID (from pauses()) -> 'go',
    'smaller' or 'skip'. Streams the rest of the question like stream_turn(), and returns the answer's text.
    """
    command = _require("langgraph.types", "The agent", package="aws-analyzer[agent]").Command(resume=choices)
    return _stream(graph, command, thread=thread, run=run, answer=answer, model_prices=model_prices,
                   progress=progress, provider=provider)


def pauses(graph: Any, thread: str) -> dict[str, dict[str, Any]]:
    """
    The pauses a question in the conversation `thread` waits on: each one's ID -> what its tool asked (a
    BigRead.as_value() dict). Empty when nothing waits.
    """
    state = graph.get_state({"configurable": {"thread_id": thread}})
    return {pause.id: dict(pause.value) for pause in getattr(state, "interrupts", ()) or ()
            if isinstance(pause.value, dict)}


def pause_for(value: dict[str, Any]) -> Any:
    """
    Pauses the question to ask the user (LangGraph's interrupt, with `value` kept with the conversation), from a tool
    while it runs; returns their choice once they've made it.
    """
    return _require("langgraph.types", "The agent", package="aws-analyzer[agent]").interrupt(value)


def is_pause(exc: BaseException) -> bool:
    """
    Whether an exception is LangGraph leaving a tool to pause the question (its GraphBubbleUp), which must reach
    the agent loop rather than become the tool's error.
    """
    return any(cls.__name__ == "GraphBubbleUp" for cls in type(exc).__mro__)


def _stream(graph: Any, start: Any, *, thread: str, run: _Run, answer: AgentAnswer,  # noqa: PLR0913
            model_prices: dict[str, tuple[float, float]] | None, progress: Callable[..., None] | None,
            provider: str) -> str:
    """
    Streams the graph from `start` (a question's message, or a Command going on after a pause): see stream_turn().
    """
    cost_of = functools.partial(answer_cost, model_prices=model_prices, provider=provider)
    count = functools.partial(_count_usage, answer=answer, base=answer.model, run=run, cost_of=cost_of)
    steps = 0
    text = ""
    config = {"configurable": {"thread_id": thread}, "recursion_limit": run.budget.steps}
    try:
        for update in graph.stream(start, config=config, stream_mode="updates"):
            for msg in _model_messages(update):
                count(msg)
                if getattr(msg, "tool_calls", None):
                    _tell_thinking(msg, run)
                else:
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
    asked_to_stop = answer.stopped == "stopped" and run.stop.is_set()
    if asked_to_stop and not text:
        text = _wrap_up(graph, config, count)
    return text


def _wrap_up(graph: Any, config: dict[str, Any], count: Callable[[Any], None]) -> str:
    """
    The answer to a question the user stopped: one more turn in the same conversation, telling the model to answer
    from what its tools returned, each model message counted like the question's own (`count`). Every tool it calls anyway is refused (run.stop is set, so check_budget says
    so), and the turn ends after _WRAP_UP_STEPS steps or _WRAP_UP_SECONDS. A tool call the stop cut off is closed
    by deepagents before the model sees it. A wrap-up that fails, or is interrupted, leaves the answer without text
    and the stop as the reason, rather than an error the user didn't cause.
    """
    message = {"role": "user", "content": _WRAP_UP}
    deadline = time.monotonic() + _WRAP_UP_SECONDS
    text = ""
    try:
        for update in graph.stream({"messages": [message]}, config={**config, "recursion_limit": _WRAP_UP_STEPS},
                                   stream_mode="updates"):
            for msg in _model_messages(update):
                count(msg)
                if not getattr(msg, "tool_calls", None):
                    text = _message_text(msg) or text
            if text or time.monotonic() > deadline:
                break
    except (KeyboardInterrupt, Exception):  # noqa: BLE001
        return text
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


def _tell_thinking(message: Any, run: _Run) -> None:
    """
    Tells the run's listener what the model said before the tools it's calling ("I'll check last night's errors
    first"), when it said anything.
    """
    thought = _message_text(message)
    if thought:
        run.tell(AgentStep("thinking", text=thought))


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


def _tool_name(tool: Any) -> str | None:
    """
    The name of a tool in a model request: a dict's "name", or a tool object's name.
    """
    if isinstance(tool, dict):
        return tool.get("name")
    return getattr(tool, "name", None)


def _hide_tools(names: set[str]) -> Any:
    """
    A deepagents middleware that keeps these tools away from the model (and refuses a call to one anyway):
    'task' (deepagents' general-purpose subagent, which the agent doesn't need) and 'execute' (shell commands, which
    the in-memory files deepagents uses can't run anyway).

    deepagents' HarnessProfile (excluded_tools, GeneralPurposeSubagentProfile(enabled=False)) does the same, but only
    through register_harness_profile, a registry for the whole Python process keyed by the model: registering one
    would also take these tools from any other deepagents agent the user builds on that model in the same kernel.
    This middleware applies to this agent only.
    """
    types = _require("langchain.agents.middleware.types", "The agent", package="aws-analyzer[agent]")
    messages = _require("langchain_core.messages", "The agent", package="aws-analyzer[agent]")

    def visible(request: Any) -> Any:
        tools = [tool for tool in request.tools if _tool_name(tool) not in names]
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


def _tool_text(result: Any) -> str:
    """
    What a deepagents tool handed the model, as text: its ToolMessage's content, or the last message a Command adds.
    """
    content = getattr(result, "content", None)
    if content is None:
        added = (getattr(result, "update", None) or {}).get("messages") or []
        content = getattr(added[-1], "content", "") if added else ""
    return content if isinstance(content, str) else str(content)


def _begin_file_call(run: _Run, tool_call: dict[str, Any]) -> tuple[ToolCall, str | None]:
    """
    Records a file tool call on the question and tells its listener, then checks the budget: (the call, the refusal
    to hand the model instead of running it, or None).
    """
    name = tool_call.get("name") or ""
    call = ToolCall(name, dict(tool_call.get("args") or {}), first=FILE_TOOLS.get(name))
    with run.lock:
        run.calls.append(call)
        used = sum(1 for c in run.calls if c.refused is None) - 1
    run.tell(AgentStep("tool", call=call))
    try:
        check_budget(run, used)
    except _Refused as exc:
        call.refused, call.summary = exc.reason, _REFUSED.get(exc.reason, "not run")
        run.tell(AgentStep("tool_done", call=call))
        return call, exc.message
    return call, None


def _end_file_call(run: _Run, call: ToolCall, result: Any, started: float) -> None:
    """
    Fills in a finished file tool call from what it handed the model, and tells the question's listener.
    """
    text = _tool_text(result)
    failed = getattr(result, "status", None) == "error" or text.lstrip().lower().startswith("error")
    call.seconds = time.monotonic() - started
    call.summary = _clip(text.strip().splitlines()[0] if text.strip() else "done", _FILE_SUMMARY_CHARACTERS)
    call.error = call.summary if failed else None
    call.result = {"output": _clip(text, _FILE_RESULT_CHARACTERS)}
    run.tell(AgentStep("tool_done", call=call))


def _record_file_tools(current: Callable[[], _Run]) -> Any:
    """
    A deepagents middleware that records each call of its file tools (FILE_TOOLS) on the question being answered
    (`current()`), as the toolkits' tools are (runtime.as_tool): counted against its budget (a call past it isn't run:
    the model is told why), told to its listener (the chat window) and listed in its report.
    """
    types = _require("langchain.agents.middleware.types", "The agent", package="aws-analyzer[agent]")
    messages = _require("langchain_core.messages", "The agent", package="aws-analyzer[agent]")

    def refusal(request: Any, message: str) -> Any:
        return messages.ToolMessage(content=f"Error: {message}", tool_call_id=request.tool_call.get("id") or "",
                                    name=request.tool_call.get("name"), status="error")

    class RecordFileTools(types.AgentMiddleware):
        def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
            if request.tool_call.get("name") not in FILE_TOOLS:
                return handler(request)
            run = current()
            call, refused = _begin_file_call(run, request.tool_call)
            if refused is not None:
                return refusal(request, refused)
            started = time.monotonic()
            result = handler(request)
            _end_file_call(run, call, result, started)
            return result

        async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
            if request.tool_call.get("name") not in FILE_TOOLS:
                return await handler(request)
            run = current()
            call, refused = _begin_file_call(run, request.tool_call)
            if refused is not None:
                return refusal(request, refused)
            started = time.monotonic()
            result = await handler(request)
            _end_file_call(run, call, result, started)
            return result

    return RecordFileTools()


def _planning() -> Any:
    """
    LangChain's TodoListMiddleware, which gives the model write_todos, with instructions for this agent's questions
    (_PLAN_PROMPT, _PLAN_TOOL_TEXT). Its name replaces the one a deepagents model profile may add (OpenAI's Codex).
    """
    middleware = _require("langchain.agents.middleware", "The agent's plan", package="aws-analyzer[agent]")
    return middleware.TodoListMiddleware(system_prompt=_PLAN_PROMPT, tool_description=_PLAN_TOOL_TEXT)


def _keep_plan(run: _Run, tool_call: dict[str, Any]) -> None:
    """
    Keeps the plan a write_todos call wrote on the question, and tells its listener (the chat window) the whole list.
    """
    plan = parse_plan((tool_call.get("args") or {}).get("todos"))
    with run.lock:
        run.plan = plan
    run.tell(AgentStep("plan", plan=list(plan)))


def _record_plan(current: Callable[[], _Run]) -> Any:
    """
    A deepagents middleware that keeps each plan the model writes (PLAN_TOOL) on the question being answered
    (`current()`), once the tool has taken it. Writing a plan reads nothing from AWS, so it isn't a tool call of the
    question's: it isn't counted against the budget or listed with the tools.
    """
    types = _require("langchain.agents.middleware.types", "The agent", package="aws-analyzer[agent]")

    def took(result: Any) -> bool:
        return getattr(result, "status", None) != "error"

    class RecordPlan(types.AgentMiddleware):
        def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
            result = handler(request)
            if request.tool_call.get("name") == PLAN_TOOL and took(result):
                _keep_plan(current(), request.tool_call)
            return result

        async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
            result = await handler(request)
            if request.tool_call.get("name") == PLAN_TOOL and took(result):
                _keep_plan(current(), request.tool_call)
            return result

    return RecordPlan()
