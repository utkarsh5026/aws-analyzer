"""
The agent's investigators (subagents=True): investigate(service, task) hands a task to a second loop of the model
with one service's tools, and only its conclusion goes back to the main conversation, while every call it makes
stays one of the question's own: recorded, budgeted, guarded and what the answer is checked against.
"""

import threading
import time

import pytest
import test_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatResult
from test_agent import (
    NOW, Notes, calls, enter, make, page, run, says, scripted, system_text, window, with_s3,
)

from aws_analyzer.agent import (
    AgentAnalyzer, AgentView, InvestigatorToolkit, check_grounding, investigator_prompt, summarize_investigation,
)
from aws_analyzer.agent.answers import ToolCall
from aws_analyzer.agent.chats import answer_from_record, answer_record
from aws_analyzer.agent.harness import Investigation
from aws_analyzer.agent.window.tool_words import _TOOL_WORDS

aws, seeded, buckets = test_agent.aws, test_agent.seeded, test_agent.buckets
REGION = "us-east-1"
HEALTH = ("function_health", {"since": "2h"})
ERRORS = ("function_errors", {"function": "etl-nightly", "since": "1h"})
TASK = "Find which functions failed in the last 2 hours and why. Report each one's failures and its error."


def hands_over(service="lambda", task=TASK):
    return calls(("investigate", {"service": service, "task": task}))


def notes_agent(model, **settings):
    settings = {"plan": False, "findings": False, "mcp": False, "runbooks": False, "verify": False, **settings}
    return AgentAnalyzer(region=REGION, chat_model=model, toolkits=[Notes], subagents=True, **settings)


def tool_names(messages):
    return [message.name for message in messages if type(message).__name__ == "ToolMessage"]


def test_investigators_are_off_unless_asked_for(seeded):
    core = make(scripted(says("ok")))
    assert core.investigator_kit is None and "investigate" not in [fn.__name__ for fn in core.tools()]
    assert "Investigators" not in core.instructions()
    asked = make(scripted(says("ok")), subagents=True)
    assert isinstance(asked.investigator_kit, InvestigatorToolkit)
    assert list(asked.investigator_kit.offered) == ["lambda", "s3", "bedrock_kb", "stepfunctions", "apigateway",
                                                    "sagemaker", "dynamodb", "opensearch", "cloudwatch"]
    section = asked.instructions().split("Investigators\n", 1)[1]
    assert "investigate(service, task) hands one task to an investigator" in section and "- lambda: Lambda" in section
    assert "runbooks:" not in section.split("How to answer")[0].split("- The services:")[1].split("\n\n")[0]
    assert "investigate" in _TOOL_WORDS


def test_an_investigation_keeps_what_it_reads_out_of_the_conversation(seeded):
    model = scripted(
        hands_over(),
        calls(HEALTH),
        calls(ERRORS),
        says("etl-nightly failed 40 times [2]: KeyError 'customer_id' [3]. Nothing was cut short."),
        says("**etl-nightly** failed 40 times [2] on a KeyError [3]."),
    )
    core = make(model, subagents=True, plan=False, findings=False, model_prices={"gpt-test": (2.0, 8.0)})
    told = []
    answer = core.ask("Which Lambda failed?", now=NOW, on_step=told.append)
    assert answer.text == "**etl-nightly** failed 40 times [2] on a KeyError [3]." and answer.error is None
    assert [(c.ref, c.within, c.name) for c in answer.calls] == [
        (1, 0, "investigate"), (2, 1, "function_health"), (3, 1, "function_errors")]
    handed = answer.calls[0]
    assert handed.is_investigation and handed.ok and answer.investigated == ["lambda"]
    assert handed.result["conclusion"].startswith("etl-nightly failed 40 times [2]") and handed.result["complete"]
    assert [(line["ref"], line["complete"]) for line in handed.result["read"]] == [(2, True), (3, True)]
    assert handed.result["read"][0]["found"] == "1 of 2 functions had failed calls: etl-nightly (40)"
    assert handed.summary.startswith("2 tool calls: etl-nightly failed 40 times [2]")
    assert "cut_short" not in handed.result and handed.brief.startswith("investigate('lambda', task=")

    main_tools, investigator_tools = model.bound[0], model.bound[1]
    assert "investigate" in main_tools and "function_health" in main_tools
    assert "function_errors" in investigator_tools and "function_health" in investigator_tools
    assert not {"investigate", "find_buckets", "write_file", "ls", "write_todos"} & set(investigator_tools)
    assert not any(name.startswith("save_") for name in investigator_tools)
    assert system_text(model, 1).startswith("You are an investigator working for aws-analyzer's assistant")
    assert "Lambda\n" in system_text(model, 1) and model.prompts[1][-1].content == TASK
    assert tool_names(model.prompts[3]) == ["function_health", "function_errors"]
    assert tool_names(model.prompts[4]) == ["investigate"] and len(model.prompts[4]) == 4
    assert "KeyError" in model.prompts[4][-1].content and "share_of_errors_pct" not in model.prompts[4][-1].content

    assert (answer.model_calls, answer.input_tokens, answer.output_tokens) == (5, 600, 120)
    assert answer.cost == pytest.approx(600 / 1e6 * 2 + 120 / 1e6 * 8)
    assert answer.aws_calls["lambda:ListFunctions"] == 1 and answer.aws_calls["logs:FilterLogEvents"] >= 1
    assert not answer.blocked and answer.metrics_read > 0
    assert [(s.kind, s.call.name) for s in told if s.call] == [
        ("tool", "investigate"), ("tool", "function_health"), ("tool_done", "function_health"),
        ("tool", "function_errors"), ("tool_done", "function_errors"), ("tool_done", "investigate")]
    assert check_grounding(answer, []).ok


def test_a_follow_up_can_cite_what_an_investigator_read(seeded):
    model = scripted(
        hands_over(), calls(HEALTH), says("etl-nightly failed 40 times [2]."), says("etl-nightly failed 40 times [2]."),
        hands_over(task="Read etl-nightly's errors over the last hour."), calls(ERRORS),
        says("One KeyError [4]."), says("It failed 40 times [2], on a KeyError [4]."),
    )
    core = make(model, subagents=True, plan=False, findings=False)
    core.ask("Which Lambda failed?")
    again = core.ask("Why?")
    assert [(c.ref, c.within, c.name) for c in again.calls] == [(3, 0, "investigate"), (4, 3, "function_errors")]
    grounding = check_grounding(again, core.earlier(again))
    assert not grounding.unknown_refs and not grounding.unknown_numbers


def test_a_service_it_has_no_investigator_for_and_a_task_left_empty(seeded):
    model = scripted(hands_over(service="lamda"), hands_over(task="  "), says("Couldn't hand it over."))
    core = make(model, subagents=True, plan=False, findings=False, toolkits=["lambda", "s3"])
    answer = core.ask("Which Lambda failed?")
    unknown, empty = answer.calls
    assert unknown.result["error"] == "There's no investigator for 'lamda'." and unknown.result["services"] == [
        "lambda", "s3"]
    assert unknown.summary == "no investigator for 'lamda'" and not unknown.result["complete"]
    assert empty.error.startswith("ValueError: investigate needs a task") and len(model.prompts) == 3


def test_an_investigator_never_stops_to_ask_about_a_big_read(buckets):
    model = scripted(
        hands_over(service="s3", task="List every file in s3://acme-raw/ and report how many there are."),
        calls(("find_files", {"location": "s3://acme-raw/"})),
        says("acme-raw holds 4 files [2]."),
        says("acme-raw holds 4 files [2]."),
    )
    core = with_s3(model, subagents=True, plan=False, findings=False, budget={"ask_keys": 2})
    answer = core.ask("What's in acme-raw?")
    assert not answer.waiting and core.waiting == [] and answer.text == "acme-raw holds 4 files [2]."
    listing = answer.calls[1]
    assert (listing.name, listing.within, listing.asks) == ("find_files", 1, []) and listing.ok
    asking = with_s3(scripted(calls(("find_files", {"location": "s3://acme-raw/"}))), subagents=True, plan=False,
                     findings=False, budget={"ask_keys": 2})
    assert [c.name for c in asking.ask("What's in acme-raw?").waiting] == ["find_files"]


def test_an_investigators_calls_count_against_the_questions_budget(seeded):
    model = scripted(
        hands_over(),
        calls(HEALTH),
        calls(ERRORS),
        says("etl-nightly failed 40 times [2]. I couldn't read its errors: the budget was used up."),
        says("etl-nightly failed 40 times [2]; its errors weren't read."),
    )
    core = make(model, subagents=True, plan=False, findings=False, budget={"tool_calls": 2})
    answer = core.ask("Which Lambda failed?")
    handed, health, errors = answer.calls
    assert not answer.waiting and health.ok and errors.refused == "tool_calls" and errors.within == 1
    assert handed.result["cut_short"] is True and handed.result["complete"] is False
    assert handed.result["stopped_at"] == "1 call didn't read everything (refs 3)"
    assert handed.coverage == {"cut_short": True, "stopped_at": "1 call didn't read everything (refs 3)"}
    assert answer.stopped == "tool_calls"


def test_an_investigator_whose_model_fails_or_says_nothing_is_an_error_the_agent_reads(seeded):
    model = scripted(
        hands_over(), calls(HEALTH), RuntimeError("the model is down"),
        hands_over(), says(""),
        says("I couldn't finish the check."),
    )
    core = make(model, subagents=True, plan=False, findings=False)
    answer = core.ask("Which Lambda failed?")
    failed, health, silent = answer.calls
    assert failed.result["error"].startswith("The lambda investigator's model failed: ")
    assert "the model is down" in failed.result["error"] and [line["ref"] for line in failed.result["read"]] == [2]
    assert health.within == failed.ref and not failed.result["complete"]
    assert silent.result["error"] == "The lambda investigator wrote no conclusion." and silent.result["read"] == []
    assert answer.text == "I couldn't finish the check." and answer.error is None


def test_stop_ends_an_investigation_and_the_agent_answers_with_what_it_has(seeded):
    stop = threading.Event()

    def stop_at_the_first_read(step):
        if step.kind == "tool" and step.call.name == "function_health":
            stop.set()

    model = scripted(hands_over(), calls(HEALTH), says("Stopped before anything was read."))
    core = make(model, subagents=True, plan=False, findings=False)
    answer = core.ask("Which Lambda failed?", stop=stop, on_step=stop_at_the_first_read)
    handed, health = answer.calls
    assert health.refused == "stopped" and health.within == 1 and answer.stopped == "stopped"
    assert handed.result["stopped_at"].startswith("it stopped early (the user pressed stop)")
    assert handed.result["error"] == "The lambda investigator wrote no conclusion."
    assert answer.text == "Stopped before anything was read." and not model.replies


class ByPrompt(BaseChatModel):
    """
    A chat model that answers by what it was last sent, so loops running side by side each get their own replies:
    the agent hands out a task per note, and an investigator reads the note its task names, then reports.
    """

    notes: list

    @property
    def _llm_type(self):
        return "by-prompt"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        is_investigator = str(messages[0].content).startswith("You are an investigator")
        last = messages[-1]
        is_start = type(last).__name__ == "HumanMessage"
        time.sleep(0.02)
        if is_investigator and is_start:
            reply = calls(("read_note", {"name": last.content}), ("list_notes", {}))
        elif is_investigator:
            reply = says("Read.")
        elif is_start:
            reply = calls(*[("investigate", {"service": "notes", "task": name}) for name in self.notes])
        else:
            reply = says("All read.")
        return ChatResult(generations=[ChatGeneration(message=reply)])


def test_investigations_of_one_turn_run_side_by_side_and_keep_their_own_calls():
    wanted = ["plan", "plans"] * 4
    answer = notes_agent(ByPrompt(notes=wanted), budget={"tool_calls": 100}).ask("What do my notes say?")
    handed = [call for call in answer.calls if call.is_investigation]
    assert answer.text == "All read." and sorted(call.args["task"] for call in handed) == sorted(wanted)
    for task in handed:
        made = [call for call in answer.calls if call.within == task.ref]
        assert sorted(call.name for call in made) == ["list_notes", "read_note"]
        assert [call.args for call in made if call.name == "read_note"] == [{"name": task.args["task"]}]
        assert [line["ref"] for line in task.result["read"]] == [call.ref for call in made]
    assert len(answer.calls) == 24 and sorted(call.ref for call in answer.calls) == list(range(1, 25))
    assert (answer.model_calls, answer.input_tokens, answer.output_tokens) == (18, 2250, 450)


def test_a_number_only_the_investigator_said_is_not_grounded():
    model = scripted(
        hands_over(service="notes", task="Read the note plan."),
        calls(("read_note", {"name": "plan"})),
        says("plan says 'ship it' [2], and 73 other notes agree."),
        says("Your plan says ship it [2], and 73 other notes agree [1]."),
    )
    answer = notes_agent(model).ask("What do my notes say?")
    grounding = check_grounding(answer, [])
    assert grounding.unknown_numbers == ["73 other"] and not grounding.unknown_refs and not grounding.miscited
    cited = notes_agent(scripted(
        hands_over(service="notes", task="Read the note plan."), calls(("read_note", {"name": "plan"})),
        says("plan says 'ship it' [2]."), says("Your `plan` note says ship it [1]."),
    )).ask("What do my notes say?")
    assert check_grounding(cited, []).ok


def test_the_report_shows_who_investigated_and_each_call_it_made(seeded, capsys):
    model = scripted(hands_over(), calls(HEALTH), says("etl-nightly failed 40 times [2]."),
                     says("etl-nightly failed 40 times [2]."))
    view = AgentView(make(model, subagents=True, plan=False, findings=False), mode="text", progress="off")
    out = run(capsys, view.ask, "Which Lambda failed?")
    assert "Investigated: Lambda" in out and "↳ function_health(since='2h')" in out
    assert out.index("investigate('lambda'") < out.index("↳ function_health")
    listed = run(capsys, view.tools)
    assert "none itself: its investigator reads with that service's tools" in listed
    assert "An investigator is the same model with one service's tools" in listed
    assert "Lambda, S3" in listed and "Investigators, read-only" not in listed


def test_the_chat_window_sets_an_investigators_calls_under_its_task(seeded):
    model = scripted(hands_over(), calls(HEALTH), says("etl-nightly failed 40 times [2]."),
                     says("**etl-nightly** failed 40 times [2]."))
    x = window(model, subagents=True, plan=False, findings=False)
    enter(x, "Which Lambda failed?")
    shown = page(x)
    assert "# an investigator looked into lambda" in shown and "<strong>etl-nightly</strong> failed 40 times" in shown
    assert shown.count("ag-within") == 1 and "2 tool calls ·" in shown
    assert shown.index("# an investigator looked into lambda") < shown.index("ag-within")
    assert shown.index("ag-within") < shown.index("# checked every function&#x27;s health")
    assert "conclusion" in shown and "data the model read" in shown


def test_a_saved_chat_keeps_which_calls_an_investigator_made():
    model = scripted(hands_over(service="notes", task="Read the note plan."), calls(("read_note", {"name": "plan"})),
                     says("plan says 'ship it' [2]."), says("Ship it [2]."))
    answer = notes_agent(model).ask("What do my notes say?")
    back = answer_from_record(answer_record(answer))
    assert [(c.ref, c.within) for c in back.calls] == [(1, 0), (2, 1)] and back.investigated == ["notes"]


def test_another_model_gets_investigators_of_its_own():
    first = scripted(hands_over(service="notes", task="Read the note plan."), calls(("read_note", {"name": "plan"})),
                     says("plan says 'ship it' [2]."), says("Ship it [2]."))
    core = notes_agent(first)
    core.ask("What do my notes say?")
    assert list(core.investigator_kit._graphs) == ["notes"]
    second = scripted(hands_over(service="notes", task="Read the note plans."),
                      calls(("read_note", {"name": "plans"})), says("plans says 'later' [2]."), says("Later [2]."))
    core.use_model(chat_model=second)
    assert core.investigator_kit._graphs == {}
    assert core.ask("And the other note?").text == "Later [2]." and not second.replies and len(second.bound) == 4


def test_summarize_investigation_says_what_was_read_and_what_stopped_it():
    read = ToolCall("function_health", {"since": "2h"}, ref=4, within=3, summary="1 of 2 functions had failed calls",
                    result={"ref": 4, "complete": True})
    cut = ToolCall("function_errors", {"function": "etl"}, ref=5, within=3, error="AccessDeniedException",
                   result={"ref": 5, "complete": False})
    done = summarize_investigation("lambda", Investigation("**etl** failed 3 times [4].\nNothing else."), [read])
    assert done == {"service": "lambda", "conclusion": "**etl** failed 3 times [4].\nNothing else.",
                    "read": [{"ref": 4, "call": "function_health(since='2h')",
                              "found": "1 of 2 functions had failed calls", "complete": True}],
                    "_summary": "1 tool call: etl failed 3 times [4]."}
    partial = summarize_investigation("lambda", Investigation("etl failed [4].", stopped="steps"), [read, cut])
    assert partial["cut_short"] is True and partial["read"][1] == {
        "ref": 5, "call": "function_errors('etl')", "found": "AccessDeniedException", "complete": False}
    assert partial["stopped_at"] == ("it stopped early (it took too many steps) and 1 call didn't read everything "
                                     "(refs 5)")
    secret = summarize_investigation("lambda", Investigation("It logs DB_PASSWORD=hunter2 [4]."), [read])
    assert "hunter2" not in secret["conclusion"]
    failed = summarize_investigation("s3", Investigation(error="The model refused."), [])
    assert failed["error"] == "The s3 investigator's model failed: The model refused." and "conclusion" not in failed
    assert failed["_summary"].startswith("0 tool calls: The s3 investigator's model failed")


def test_the_investigators_prompt_has_one_toolkits_section_and_how_to_report():
    toolkit = Notes()
    prompt = investigator_prompt(toolkit, ["eu-west-1"])
    assert prompt.startswith("You are an investigator working for aws-analyzer's assistant")
    assert "one task about Notes; you cover the user's notes: what they wrote down and when." in prompt
    assert "Regions: check eu-west-1 unless" in prompt and "Read a note with read_note before quoting it." in prompt
    assert prompt.index("How to work") < prompt.index("Notes\n- Read a note") < prompt.index("How to report")
    assert "Put the ref of the result each fact came from" in prompt and "final_answer" not in prompt
    assert "the notebook's default region" in investigator_prompt(toolkit, [])
