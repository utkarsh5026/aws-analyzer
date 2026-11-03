---
name: agent-eval
description: Measure how well the agent (src/aws_analyzer/agent/) answers - ask it a fixed set of questions about demo.py's Lambda data in moto with a real model, and grade each answer on the facts it states, what it gets wrong, the tools it calls, how many calls it takes and what in it no tool returned. Use before and after changing the agent's prompt, toolkits, tools' results, grounding or model handling, to show the change helped and broke nothing, or to compare two models.
argument-hint: "[--model openai:gpt-4.1 | bedrock:sonnet | demo] [--cases a,b] [--repeat 3]"
allowed-tools: Bash(.venv/bin/python .claude/skills/agent-eval/evals.py:*), Bash(python .claude/skills/agent-eval/evals.py:*)
---

# Agent eval

`evals.py` seeds demo.py's Lambda data in moto (fresh for every run), asks the agent each question in `cases.py`,
and grades the answer. The data is synthetic and known (orders-etl's KeyError, AccessDenied and timeouts, report-api's
reserved concurrency of 10, churn-scoring's idle provisioned concurrency, feature-backfill never called, gdpr-export in
eu-west-1), so every case has a right answer on any day. Only the model is real.

```bash
.venv/bin/python .claude/skills/agent-eval/evals.py list
.venv/bin/python .claude/skills/agent-eval/evals.py run --model openai:gpt-4.1 --repeat 3 --out <scratchpad>/evals/before.json
.venv/bin/python .claude/skills/agent-eval/evals.py run --model bedrock:sonnet --profile dev --repeat 3 --out <scratchpad>/evals/after.json
.venv/bin/python .claude/skills/agent-eval/evals.py compare <scratchpad>/evals/before.json <scratchpad>/evals/after.json
.venv/bin/python .claude/skills/agent-eval/evals.py run --cases why-orders-etl,logs-denied --show
```

Arguments: `$ARGUMENTS`, passed to `run`. With none, run every case once with the environment's model.

## The model

`--model` takes what `AgentView(model=)` does (`openai:gpt-4.1`, `anthropic:claude-...`; `azure` with the
`AZURE_OPENAI_*` variables set), or `bedrock:<name or ID>`, made with your real AWS credentials (`--profile`,
`--region`) before moto starts, so only Bedrock's calls leave moto. Without `--model`, the environment picks one as
`AgentView()` would. If no model can be set up here (no key, no AWS account), say so and stop: don't report the
scripted model's scores as the agent's.

`--model demo` is demo.py's scripted stand-in: it answers every question with the same health table, so it passes
`which-failed` and `log-injection` and fails the rest. Use it only to check the harness after editing `evals.py` or
`cases.py`; `tests/test_agent_evals.py` runs it in CI.

## Grading

A run passes when the answer (its text and its findings' messages):

| Check | From the case | Fails when |
|---|---|---|
| facts | `must`, regexes | one isn't matched: the answer left out something a right answer states |
| wrong | `must_not`, regexes | one is matched: it said what a wrong answer says (the injected code, a cause for a function that isn't there) |
| tools | `tools`, regexes of tool names | it never called one (a cause given without reading the logs) |
| calls | `max_calls` | it took more tool calls than a sensible route |
| grounding | the agent's `check_grounding()` | the answer it gave has a name, ID, number or region no tool returned, or "none" over an incomplete read |
| error | | the model or the loop failed |

The table gives each case's runs passed, its facts stated, runs that said something wrong, claims per answer that no
tool returned, tool calls, seconds and cost. `--show` prints each failed run's reasons, its calls and its answer.

## Comparing a change

Models vary run to run, so use `--repeat 3` or more and the same model for both sides. Run on the base commit
(`git stash` or a worktree) with `--out before.json`, then on the change with `--out after.json`, then `compare`. It
marks each case ▲ or ▼ by the share of runs passed and exits 1 when one got worse. Report the overall line from both
runs and every ▼ case, with its reasons from `--show`. A case that fails both before and after isn't the change's
doing, but say so.

## Adding a case

A case is an `EvalCase` in `cases.py`: a question about what the demo data holds (read demo.py's
`seed_lambda_functions()` for the facts), the facts as regexes loose enough for any right wording, and what a wrong
answer would say. A `trap` tests what the agent does when the data can't answer or tries to mislead; its `setup`
changes the seeded world first (`deny_log_reads`, `inject_log_line`) and returns settings for the agent. Check a new
case with `run --model demo --cases <name> --show` (the harness runs it), then with a real model, and keep its facts
from what the data holds, not from one model's answer.
