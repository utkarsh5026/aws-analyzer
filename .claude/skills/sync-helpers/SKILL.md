---
name: sync-helpers
description: Compare the helpers still duplicated across src/aws_analyzer/*.py (what bedrock_kb.py and bedrock_chat.py share, the explorer windows' parts, ...) and port fixes between the copies. Use after changing one of those helpers in one analyzer, when /check reports drift, or when a new analyzer copies them in.
argument-hint: "[helper names, e.g. search_rank _marked]"
allowed-tools: Bash(.venv/bin/python .claude/skills/sync-helpers/drift.py:*), Bash(python .claude/skills/sync-helpers/drift.py:*)
---

# Sync the duplicated helpers

The analyzers share code through `src/aws_analyzer/_kit/`, and the helpers not moved there yet are still copied into
every file (see "Hard constraints" in CLAUDE.md and `.claude/plans/shared-code.md`). A fix made to one copy has to
reach the others by hand. This skill finds the copies that have drifted apart and brings them back in line. A helper
in `_kit` has one copy, so it never drifts: change it there. The View and Analyzer plumbing is there since Phase 4
(`_BaseView` with `_show`, `_progress` and `help()`, `@_friendly_errors`, `_BaseAnalyzer`), so what still drifts is
what `bedrock_kb.py` and `bedrock_chat.py` share (until Phase 5) and the explorer windows' parts (until Phase 7).
The render blocks and the renderer are there since Phase 3, except S3's own renderer (until Phase 6), which
`drift.py` lists next to `_kit`'s versions under "Defined in an analyzer and in _kit": a fix to `_kit/render.py`'s
table, findings or prose may be wanted in `s3.py`'s too, and the other way round.

## 1. See what differs

```bash
.venv/bin/python .claude/skills/sync-helpers/drift.py $ARGUMENTS
```

(Use `python` if there's no `.venv`.) The script compares every top-level definition that two or more analyzers
share, plus the private and `help` methods of the View and Analyzer classes. It normalizes the service names
first (`S3View` / `DynamoDBView` → `<View>`, `S3_PRICES` → `<PRICES>`, the CSS root class → `<css>`) and
separates code differences from docstring or comment differences. It also lists any analyzer that lacks one of
the helpers CLAUDE.md names as shared. `--summary` prints the names only.

## 2. Sort each difference

For each name listed under "Differ in code", decide which kind of difference it is:

- **Intentional, service-specific.** Examples: each service's look (`_ROOT`, `_CSS`, `_EXTRA_CSS`, `_BADGE`, and
  S3's `_render_html`); what each View says about an error (`_explain`, DynamoDB's `_explain_call`, OpenSearch's
  `_extra_note`) and its class attributes (`_GROUPS`, `_START`, `_PROGRESS`, `_DATA_ERRORS`); the signatures of
  `__init__`, `_section` and `_price_basis`. Leave these alone. The shared part of the function around them should
  still match, though.
- **Drift.** One copy got a fix, a new edge case or better wording that the other copy didn't get. To find out
  which copy is newer and why it changed:
  `git log -L :<name>:src/aws_analyzer/<service>.py --oneline | head -40`, or `git log -S '<changed line>' --oneline`.
- **Unclear.** Say what you found and ask before changing it.

Docstring-only differences are fine when the wording is service-specific ("objects" vs "items"). Otherwise,
keep the clearer wording in both copies.

## 3. Port the fix

- Copy the newer version into every other analyzer, and change only the service-specific names. Keep everything
  else character for character, so the next drift check reports the helper as identical.
- If an analyzer is missing a shared helper, copy it in from the analyzer that changed most recently, into the
  same section (helpers in section 1, View helpers in section 5 before the View).
- A helper change affects every analyzer's output, so run the tests for all of them.

## 4. Verify and report

Run `drift.py` again: the helpers you synced should now be identical or docs-only. Then run `/check`.

Report a short table with one row per helper that differed: whether it was synced (and in which direction),
whether the difference was intentional (and why), or whether it was left for the user to decide.
