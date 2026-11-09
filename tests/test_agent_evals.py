"""
The agent's eval set (.claude/skills/agent-eval): its cases are well formed, its grading is right on answers built by
hand, compare says when a case got worse, and the harness runs a case end to end on demo.py's data with the scripted
model, so it can't break unnoticed between the runs that use a real model.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import urllib.request
from pathlib import Path

import pytest

from aws_analyzer.agent import AgentAnswer, AgentFinding, ToolCall

ROOT = Path(__file__).resolve().parents[1]
EVALS = ROOT / ".claude/skills/agent-eval"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cases = _load("cases", EVALS / "cases.py")
evals = _load("agent_evals", EVALS / "evals.py")


def health_call(**result):
    return ToolCall("function_health", {"since": "24h"}, first=None, coverage={"regions": ["us-east-1"]},
                    result={"regions": ["us-east-1"], "functions_checked": 6, **result})


def test_the_cases_are_well_formed():
    names = [case.name for case in cases.CASES]
    assert len(names) == len(set(names)) and len(names) >= 10
    for case in cases.CASES:
        case.patterns()
        assert case.kind in ("fact", "trap") and case.must and case.max_calls > 0 and case.about, case.name
    assert {case.kind for case in cases.CASES} == {"fact", "trap"}
    assert [c.name for c in cases.find_cases("no-runs, which-failed")] == ["no-runs", "which-failed"]
    with pytest.raises(ValueError, match="No case named nope; the cases are which-failed, "):
        cases.find_cases("nope")


def test_a_right_answer_passes():
    case = cases.find_cases("which-failed")[0]
    answer = AgentAnswer("q", text="**orders-etl** failed 35 times and report-api 10 times.", calls=[
        health_call(failed=[{"name": "orders-etl", "errors": 35}, {"name": "report-api", "errors": 10}])])
    result = evals.score(case, answer)
    assert result.passed and result.facts == result.facts_total == 2 and result.reasons() == []


def test_each_way_to_fail_is_named():
    case = cases.find_cases("which-failed")[0]
    answer = AgentAnswer("q", text="orders-etl failed 99 times [9]; churn-scoring failed too. `billing-sync` is fine.",
                         calls=[health_call(failed=[{"name": "orders-etl", "errors": 35}])] * 6,
                         findings=[AgentFinding("warn", "report-api fails.")])
    reasons = evals.score(case, answer).reasons()
    assert "said: 'churn-scoring failed'" in reasons
    assert "took 6 tool calls (at most 5)" in reasons
    assert "not from the tools: billing-sync" in reasons and "not from the tools: 99 times" in reasons
    assert "not from the tools: citation [9]" in reasons
    assert not any(r.startswith("missed:") for r in reasons), "a finding's message counts as said"
    missing = evals.score(case, AgentAnswer("q", text="report-api failed.", calls=[ToolCall("function_errors")]))
    assert missing.reasons()[:2] == ["missed: orders-etl", "didn't call: function_health"]
    failed = evals.score(case, AgentAnswer("q", error="ThrottlingException"))
    assert failed.reasons()[0] == "error: ThrottlingException" and not failed.passed


def test_summaries_and_compare_say_what_changed():
    passing = evals.CaseResult("which-failed", "fact", 1, facts=2, facts_total=2, max_calls=5, tool_calls=2,
                               input_tokens=30_000, cache_read_tokens=18_000)
    failing = evals.CaseResult("which-failed", "fact", 2, facts=1, facts_total=2, max_calls=5, tool_calls=4,
                               missed=["report-api"], ungrounded=["99 times"], input_tokens=10_000)
    [summary] = evals.summarize([passing, failing])
    assert (summary.runs, summary.passed, summary.facts, summary.ungrounded, summary.calls) == (2, 1, 0.75, 0.5, 3.0)
    table = evals.render([summary])
    assert re.search(r"which-failed\s+fact\s+1/2\s+75%", table)
    assert "0/1 cases passed every run; 1/2 runs passed; facts 75%" in table
    assert table.endswith("prompt cache served 45% of 40,000 input tokens")
    lines, worse = evals.compare_lines(evals.summarize([passing]), [summary])
    assert worse and lines[1].endswith("▼") and "1/1 → 1/2" in lines[1]
    assert not evals.compare_lines([summary], evals.summarize([passing]))[1]


def test_a_case_runs_end_to_end_on_the_demo_data(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", urllib.request.urlopen)
    demo = evals._load_demo()
    model = evals.pick_model("demo")
    which = evals.run_case(cases.find_cases("which-failed")[0], model, demo)
    assert which.passed, which.reasons()
    assert "function_health()" in which.calls and which.ungrounded == []
    denied = evals.run_case(cases.find_cases("logs-denied")[0], model, demo)
    assert not denied.passed and denied.missed, "the scripted model doesn't say its logs couldn't be read"
