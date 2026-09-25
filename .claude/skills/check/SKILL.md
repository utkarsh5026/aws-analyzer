---
name: check
description: Run this repo's CI locally - ruff, each analyzer imported alone with only boto3, pytest - plus project-rule checks CI doesn't have (read-only AWS calls, lazy optional imports, View conventions, IAM permissions listed in README) and drift between the duplicated helpers. Use after changing analyzers/ or tests/, before saying the work is done, and before committing.
argument-hint: "[matrix] [pytest args, e.g. -k policy]"
allowed-tools: Bash(.venv/bin/python .claude/skills/check/run.py:*), Bash(python .claude/skills/check/run.py:*), Bash(.venv/bin/python .claude/skills/check/rules.py:*), Bash(python .claude/skills/check/rules.py:*)
---

# Check

Run everything CI runs, locally, and fix what it finds.

## Run it

Use `.venv/bin/python` if it exists, otherwise `python`:

```bash
.venv/bin/python .claude/skills/check/run.py                  # ruff, imports, rules, pytest, drift
.venv/bin/python .claude/skills/check/run.py --matrix         # + pytest on CI's other Python versions (uv)
.venv/bin/python .claude/skills/check/run.py -- -k policy     # arguments after -- go to pytest
```

Arguments: `$ARGUMENTS`. If they include `matrix`, pass `--matrix`. Pass anything else to pytest after `--`.

The full run takes about a minute, mostly pytest. `--matrix` builds a throwaway uv environment for each Python in
`.github/workflows/ci.yml` other than the local one. The first build of each takes about a minute, and later runs
reuse it. The first line of the output names the local Python. On 3.10 the local run covers pandas 2 /
IPython 8, and on 3.11+ it covers pandas 3 / IPython 9. The matrix covers the other versions. Use `--matrix`
after touching anything that uses pandas, IPython or version-specific stdlib, or when asked for the full run.

## Steps

| Step | Fails when | Usual fix |
|---|---|---|
| `ruff` | `ruff check .` finds a real error (ruff.toml selects only E4/E7/E9/F) | Fix the code. Don't add `noqa` or change ruff.toml |
| `imports` | an analyzer, copied alone into an empty directory, doesn't import with only boto3 (the same as CI's job) | Move the optional import inside the function that needs it, through `_require(module, purpose)` |
| `rules` | `rules.py` finds an error: a write API call, a top-level non-stdlib import, an import of another analyzer, missing `from __future__ import annotations`, section banners out of order, a public View method without `@_friendly_errors` or a docstring, or `print` / `display` outside the rendering plumbing | Follow the message. Each one names the rule from CLAUDE.md |
| `pytest` | a test fails | Find the root cause. Change a test's expectation only when the behaviour change was intended, and say so |
| `py3.X` | (`--matrix`) a test fails on that Python only | Usually pandas 2 vs 3 (copy-on-write, the default `str` dtype) or stdlib added after 3.10 |
| `drift` | never; information only | If this change touched a duplicated helper, run `/sync-helpers` |

Rule **warnings** don't fail the run, but deal with each one that the current change caused:

- *needs `svc:Action`, which README.md's IAM permissions don't list*: add the permission to that service's
  IAM section in `README.md` and in `docs/<service>.html`. For S3, the action is often not the operation name
  (`ListObjectsV2` → `s3:ListBucket`). `rules.py --apis` prints the mapping.
- *execute_statement runs any PartiQL*: the analyzer must refuse anything but `SELECT` before calling it. Once
  it does, mark the call's line `# read-only: <how it's guarded>`.
- *imports X directly*: use `_require("X", "what it's for")`, so a missing package turns into a note that says
  what to pip install.
- *not annotated -> None*: View methods render and return nothing.

If a warning was already there before this change, mention it once in the report and leave it alone.

## Report

Give one line per step (PASS / FAIL / time), then for each failure the cause and what you changed. Don't
report "all green" unless the last run shows it. If you fixed something, run the check again and report that
run. Don't commit unless asked.
