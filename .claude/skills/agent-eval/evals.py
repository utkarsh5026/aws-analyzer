"""
Asks the agent each eval case (cases.py) on demo.py's Lambda data in moto, with the model you name, and grades the
answers: the facts a right answer states, what a wrong one says, the tools it should call, how many calls it took,
and what in the answer no tool returned (the agent's own check_grounding). Each run starts from freshly seeded data,
so cases can't leak into each other.

    python .claude/skills/agent-eval/evals.py run --model openai:gpt-4.1 --repeat 3 --out <scratchpad>/evals/a.json
    python .claude/skills/agent-eval/evals.py run --model bedrock:sonnet --profile dev --region us-east-1
    python .claude/skills/agent-eval/evals.py run --cases why-orders-etl,missing-function --show
    python .claude/skills/agent-eval/evals.py run --model demo --cases which-failed      # the harness, no model
    python .claude/skills/agent-eval/evals.py compare <scratchpad>/evals/a.json <scratchpad>/evals/b.json
    python .claude/skills/agent-eval/evals.py list

--model takes what AgentView(model=) does ('openai:gpt-4.1', 'anthropic:claude-...', 'azure' with the AZURE_OPENAI_*
variables), 'bedrock:<name or ID>' (called with your real AWS credentials, --profile/--region, while everything
else stays in moto), or 'demo', demo.py's scripted stand-in, which only knows one plan: it tests the harness, not an
agent. Without --model, the environment picks it as AgentView() would. A run costs what the model charges; the AWS
side is moto and costs nothing.

A case passes a run when the answer states every fact, says nothing a wrong answer would, calls the tools it should,
takes at most its number of calls, has nothing check_grounding() flags, and didn't fail. compare exits 1 when a case
passes fewer runs than before, so a prompt or toolkit change can be checked against the run before it.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

from cases import EvalCase, find_cases  # noqa: E402

REGION = "us-east-1"
_FAKE_AWS = {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_SESSION_TOKEN": "testing",
             "AWS_DEFAULT_REGION": REGION}
_BEDROCK_SERVICES = ("bedrock-runtime", "bedrock")
_ANSWER_SHOWN = 1200
_WIDTH = 18


def _load_demo() -> Any:
    """
    demo.py, which seeds the Lambda data and holds the scripted model.
    """
    spec = importlib.util.spec_from_file_location("aws_analyzer_demo", ROOT / ".claude/skills/demo/demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass
class CaseResult:
    """
    One run of one case, graded.

        facts / facts_total  how many of the case's facts the answer states
        missed               the facts it didn't state (their regexes)
        said_wrong           what it said that a wrong answer says (the text that matched)
        tools_missing        the tools it should have called and didn't (their regexes)
        ungrounded           what check_grounding() flagged in the answer it gave: names, numbers, 'region ...',
                             'none: <sentence>'
        rechecked            whether its first draft was sent back to be corrected (verify=True)
    """

    case: str
    kind: str
    run: int
    facts: int
    facts_total: int
    missed: list[str] = field(default_factory=list)
    said_wrong: list[str] = field(default_factory=list)
    tools_missing: list[str] = field(default_factory=list)
    tool_calls: int = 0
    max_calls: int = 0
    ungrounded: list[str] = field(default_factory=list)
    rechecked: bool = False
    error: str = ""
    stopped: str = ""
    seconds: float = 0.0
    cost: float | None = None
    tokens: int = 0
    answer: str = ""
    calls: list[str] = field(default_factory=list)

    def reasons(self) -> list[str]:
        """
        Why the run failed, a line each; empty when it passed.
        """
        found = [f"error: {self.error}"] if self.error else []
        found += [f"missed: {pattern}" for pattern in self.missed]
        found += [f"said: {text!r}" for text in self.said_wrong]
        found += [f"didn't call: {pattern}" for pattern in self.tools_missing]
        if self.tool_calls > self.max_calls:
            found.append(f"took {self.tool_calls} tool calls (at most {self.max_calls})")
        found += [f"not from the tools: {claim}" for claim in self.ungrounded]
        return found

    @property
    def passed(self) -> bool:
        return not self.reasons()


def _said(answer: Any) -> str:
    """
    Everything the answer states: its text and its findings' messages.
    """
    return "\n".join([answer.text, *(finding.message for finding in answer.findings)])


def _ungrounded(answer: Any) -> list[str]:
    from aws_analyzer.agent import check_grounding

    grounding = check_grounding(answer)
    return [*grounding.unknown_names, *grounding.unknown_numbers,
            *(f"region {region}" for region in grounding.unchecked_regions),
            *(f"none: {none.sentence}" for none in grounding.unbacked_nones)]


def _wrong_things_said(case: EvalCase, said: str) -> list[str]:
    wrong = []
    for pattern in case.must_not:
        found = re.search(pattern, said, re.I)
        if found:
            wrong.append(found.group(0))
    return wrong


def score(case: EvalCase, answer: Any, *, run: int = 1) -> CaseResult:
    """
    How an AgentAnswer did on a case: the facts it states, what it said wrong, the tools it missed, its calls and what
    no tool returned. Pure: it reads the answer only.
    """
    said = _said(answer)
    missed = [pattern for pattern in case.must if not re.search(pattern, said, re.I)]
    called = [call.name for call in answer.calls if call.refused is None]
    tools_missing = [pattern for pattern in case.tools if not any(re.fullmatch(pattern, name) for name in called)]
    return CaseResult(
        case.name, case.kind, run, facts=len(case.must) - len(missed), facts_total=len(case.must), missed=missed,
        said_wrong=_wrong_things_said(case, said), tools_missing=tools_missing, tool_calls=len(called),
        max_calls=case.max_calls, ungrounded=_ungrounded(answer), rechecked=answer.recheck is not None,
        error=answer.error or "", stopped=answer.stopped or "", seconds=round(answer.seconds, 1), cost=answer.cost,
        tokens=answer.input_tokens + answer.output_tokens, answer=answer.text, calls=[call.call for call in answer.calls])


@dataclass
class ModelChoice:
    """
    The model the cases are asked with: its name in the report, the AgentAnalyzer settings that pick it, and the AWS
    services moto lets through to the real AWS (Bedrock's, for a Bedrock model).
    """

    label: str
    settings: dict[str, Any]
    passthrough: tuple[str, ...] = ()


def _bedrock_choice(name: str, *, profile: str | None, region: str | None) -> ModelChoice:
    """
    A Bedrock model, made with your real AWS credentials before moto starts, so only its calls leave moto.
    """
    import boto3

    from aws_analyzer.agent import AgentAnalyzer

    session = boto3.Session(profile_name=profile, region_name=region or REGION)
    chat_model = AgentAnalyzer(session, provider="bedrock", model=name)._new_chat_model()
    return ModelChoice(f"bedrock:{name}", {"chat_model": chat_model}, passthrough=_BEDROCK_SERVICES)


def pick_model(spec: str, *, profile: str | None = None, region: str | None = None) -> ModelChoice:
    """
    The model --model names: 'demo', 'bedrock:<name>', a provider-prefixed model, or '' for the environment's.
    """
    from aws_analyzer.agent import AgentAnalyzer

    if spec == "demo":
        return ModelChoice("demo (scripted)", {"chat_model": _load_demo()._demo_model()})
    if spec.startswith("bedrock:"):
        return _bedrock_choice(spec.split(":", 1)[1], profile=profile, region=region)
    probe = AgentAnalyzer(region=REGION, model=spec or None)
    if not probe.provider:
        raise SystemExit("No model to ask: pass --model ('openai:gpt-4.1', 'bedrock:sonnet', 'demo', ...) or set "
                         "OPENAI_API_KEY / AZURE_OPENAI_* / AWS_ANALYZER_AGENT_MODEL")
    return ModelChoice(f"{probe.provider}:{probe.model_id}", {"model": spec} if spec else {})


@contextlib.contextmanager
def seeded_world(demo: Any, model: ModelChoice) -> Iterator[dict[str, Any]]:
    """
    moto with demo.py's Lambda data, for one run; the model's own AWS services pass through to AWS.
    """
    from moto import mock_aws

    config = {"core": {"passthrough": {"services": list(model.passthrough), "urls": []}}}
    mock = mock_aws(config=config) if model.passthrough else mock_aws()
    mock.start()
    try:
        yield demo.seed_lambda_functions()
    finally:
        mock.stop()


def run_case(case: EvalCase, model: ModelChoice, demo: Any, *, run: int = 1) -> CaseResult:
    """
    Asks one case once, on freshly seeded data, and grades the answer.
    """
    from aws_analyzer.agent import AgentAnalyzer

    with seeded_world(demo, model) as world:
        settings = {"lambda_clients": world["clients"], **(case.setup(world) if case.setup else {})}
        core = AgentAnalyzer(region=REGION, toolkits=["lambda"], interrupt_on=False, ask_more=False,
                             **model.settings, **settings)
        answer = core.ask(case.question)
    return score(case, answer, run=run)


@dataclass
class CaseSummary:
    """
    A case over its runs: how many passed, the share of its facts stated, the runs that said something wrong, and
    the averages of what no tool returned, tool calls, seconds and cost.
    """

    case: str
    kind: str
    runs: int
    passed: int
    facts: float
    wrong: int
    ungrounded: float
    calls: float
    seconds: float
    cost: float | None


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _fact_share(result: CaseResult) -> float:
    return result.facts / result.facts_total if result.facts_total else 1.0


def summarize(results: list[CaseResult]) -> list[CaseSummary]:
    """
    Each case's runs, summed up, in the order the cases first appear.
    """
    by_case: dict[str, list[CaseResult]] = {}
    for result in results:
        by_case.setdefault(result.case, []).append(result)
    summaries = []
    for name, runs in by_case.items():
        costs = [r.cost for r in runs if r.cost is not None]
        summaries.append(CaseSummary(
            name, runs[0].kind, len(runs), sum(r.passed for r in runs),
            _mean([_fact_share(r) for r in runs]),
            sum(bool(r.said_wrong) for r in runs), _mean([len(r.ungrounded) for r in runs]),
            _mean([r.tool_calls for r in runs]), _mean([r.seconds for r in runs]),
            sum(costs) if costs else None))
    return summaries


def _money(value: float | None) -> str:
    return f"${value:.3f}" if value is not None else "-"


def render(summaries: list[CaseSummary]) -> str:
    """
    The summaries as a table, then the overall line: cases that passed every run, runs passed, facts stated, runs
    that said something wrong, claims no tool returned per answer, and the cost.
    """
    header = f"{'case':<{_WIDTH}} {'kind':<5} {'passed':>7} {'facts':>6} {'wrong':>5} {'ungrnd':>6} {'calls':>5} " \
             f"{'secs':>6} {'cost':>8}"
    lines = [header, "-" * len(header)]
    for s in summaries:
        lines.append(f"{s.case:<{_WIDTH}} {s.kind:<5} {f'{s.passed}/{s.runs}':>7} {s.facts:>6.0%} {s.wrong:>5} "
                     f"{s.ungrounded:>6.1f} {s.calls:>5.1f} {s.seconds:>6.1f} {_money(s.cost):>8}")
    runs = sum(s.runs for s in summaries)
    every = sum(s.passed == s.runs for s in summaries)
    costs = [s.cost for s in summaries if s.cost is not None]
    lines += ["", f"{every}/{len(summaries)} cases passed every run; {sum(s.passed for s in summaries)}/{runs} runs "
                  f"passed; facts {_mean([s.facts for s in summaries]):.0%}; {sum(s.wrong for s in summaries)} runs "
                  f"said something wrong; {_mean([s.ungrounded for s in summaries]):.1f} claims per answer no tool "
                  f"returned; cost {_money(sum(costs) if costs else None)}"]
    return "\n".join(lines)


def _commit() -> str:
    try:
        found = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True,
                               text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return ""
    return found.stdout.strip()


def _run_line(result: CaseResult, runs: int) -> str:
    mark = "✓" if result.passed else "✗"
    why = "" if result.passed else f"  {'; '.join(result.reasons())[:200]}"
    return f"{mark} {result.case} ({result.run}/{runs}) {result.tool_calls} calls {result.seconds:.1f}s{why}"


def _show(results: list[CaseResult]) -> None:
    """
    Each failed run's reasons, tool calls and answer.
    """
    for result in results:
        if result.passed:
            continue
        print(f"\n=== {result.case} (run {result.run})")
        print("\n".join(f"  {reason}" for reason in result.reasons()))
        print("  calls: " + ", ".join(result.calls))
        print("  answer: " + result.answer[:_ANSWER_SHOWN].replace("\n", "\n          "))


def run(args: argparse.Namespace) -> int:
    """
    The run command: every case asked --repeat times, a line each as it finishes, then the table; with --out, the
    results as JSON for compare.
    """
    cases = find_cases(args.cases)
    model = pick_model(args.model, profile=args.profile, region=args.region)
    os.environ.update(_FAKE_AWS)
    os.environ.pop("AWS_PROFILE", None)
    demo = _load_demo()
    print(f"{len(cases)} cases x {args.repeat} with {model.label}", flush=True)
    results = []
    for case in cases:
        for number in range(1, args.repeat + 1):
            results.append(run_case(case, model, demo, run=number))
            print(_run_line(results[-1], args.repeat), flush=True)
    print("\n" + render(summarize(results)))
    if args.show:
        _show(results)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        record = {"model": model.label, "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "commit": _commit(), "repeat": args.repeat, "results": [asdict(r) for r in results]}
        args.out.write_text(json.dumps(record, indent=1), encoding="utf-8")
        print(f"\n(results: {args.out})")
    return 0


def load_results(path: Path) -> tuple[dict[str, Any], list[CaseResult]]:
    """
    A results file run --out wrote: its header (model, when, commit) and its runs.
    """
    record = json.loads(path.read_text(encoding="utf-8"))
    return record, [CaseResult(**result) for result in record.get("results", [])]


def _trend(change: float) -> str:
    """
    ▲ for a case that passed more of its runs, ▼ for fewer, a blank for the same.
    """
    if change > 0:
        return "▲"
    return "▼" if change < 0 else " "


def compare_lines(before: list[CaseSummary], after: list[CaseSummary]) -> tuple[list[str], bool]:
    """
    Each case before and after (runs passed, facts, claims no tool returned, calls), marked ▲ better or ▼ worse by
    the share of runs passed, and whether any case got worse.
    """
    old = {s.case: s for s in before}
    lines = [f"{'case':<{_WIDTH}} {'passed':>13} {'facts':>11} {'ungrounded':>11} {'calls':>11}"]
    worse = False
    for s in after:
        was = old.get(s.case)
        if was is None:
            lines.append(f"{s.case:<{_WIDTH}} {'new':>13} {s.facts:>11.0%}")
            continue
        change = s.passed / s.runs - was.passed / was.runs
        worse = worse or change < 0
        mark = _trend(change)
        lines.append(f"{s.case:<{_WIDTH}} {f'{was.passed}/{was.runs} → {s.passed}/{s.runs}':>13} "
                     f"{f'{was.facts:.0%} → {s.facts:.0%}':>11} {f'{was.ungrounded:.1f} → {s.ungrounded:.1f}':>11} "
                     f"{f'{was.calls:.1f} → {s.calls:.1f}':>11} {mark}")
    return lines, worse


def compare(args: argparse.Namespace) -> int:
    """
    The compare command: two results files case by case; exits 1 when a case passed fewer runs than before.
    """
    before_header, before = load_results(args.before)
    after_header, after = load_results(args.after)
    print(f"before: {before_header.get('model')} at {before_header.get('commit') or '?'} "
          f"({before_header.get('when')})")
    print(f"after:  {after_header.get('model')} at {after_header.get('commit') or '?'} ({after_header.get('when')})\n")
    lines, worse = compare_lines(summarize(before), summarize(after))
    print("\n".join(lines))
    print("\n" + render(summarize(after)).splitlines()[-1])
    return 1 if worse else 0


def list_cases(_: argparse.Namespace) -> int:
    """
    The list command: each case's name, kind and what it tests.
    """
    for case in find_cases():
        print(f"{case.name:<{_WIDTH}} {case.kind:<5} {case.about}")
    return 0


def main() -> int:
    intro, _, rest = (__doc__ or "").strip().partition("\n\n")
    parser = argparse.ArgumentParser(description=intro, epilog=rest,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    asking = commands.add_parser("run", help="ask the cases and grade the answers")
    asking.add_argument("--model", default="", help="'openai:gpt-4.1', 'bedrock:sonnet', 'demo', ... (default: the "
                                                    "environment's, as AgentView() picks it)")
    asking.add_argument("--cases", default="", help="comma-separated case names (default: all; see list)")
    asking.add_argument("--repeat", type=int, default=1, help="runs per case: models vary, so 3 or more to compare")
    asking.add_argument("--out", type=Path, help="write the results as JSON, for compare")
    asking.add_argument("--show", action="store_true", help="print each failed run's reasons, calls and answer")
    asking.add_argument("--profile", help="with a bedrock: model, the AWS profile to call it with")
    asking.add_argument("--region", help="with a bedrock: model, its region (default us-east-1)")
    asking.set_defaults(handler=run)
    comparing = commands.add_parser("compare", help="two results files, case by case")
    comparing.add_argument("before", type=Path)
    comparing.add_argument("after", type=Path)
    comparing.set_defaults(handler=compare)
    commands.add_parser("list", help="the cases").set_defaults(handler=list_cases)
    args = parser.parse_args()
    started = time.monotonic()
    code = args.handler(args)
    if args.command == "run":
        print(f"({time.monotonic() - started:.0f}s)")
    return code


if __name__ == "__main__":
    sys.exit(main())
