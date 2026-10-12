---
name: sync-helpers
description: Compare the names still defined in more than one of src/aws_analyzer/*.py (each service's own look and words, and the few kept apart on purpose) and port fixes between the copies. Use after changing one of those helpers in one analyzer, when /check reports drift, or when a new analyzer copies them in.
argument-hint: "[helper names, e.g. search_rank _marked]"
allowed-tools: Bash(.venv/bin/python .claude/skills/sync-helpers/drift.py:*), Bash(python .claude/skills/sync-helpers/drift.py:*)
---

# Sync the duplicated helpers

The analyzers share code through `src/aws_analyzer/_kit/`, and the helpers not moved there yet are still copied into
every file (see "Hard constraints" in CLAUDE.md and `.claude/plans/shared-code.md`). A fix made to one copy has to
reach the others by hand. This skill finds the copies that have drifted apart and brings them back in line. A helper
in `_kit` has one copy, so it never drifts: change it there. The View and Analyzer plumbing is there since Phase 4
(`_BaseView` with `_show`, `_progress` and `help()`, `@_friendly_errors`, `_BaseAnalyzer`), and what `bedrock_kb.py`
and `bedrock_chat.py` share is in `_bedrock.py` since Phase 5 (one copy too: change it there), and since Phase 7 what
the notebook windows share is in `_kit/window.py` (the helpers, `_Window`, `_Explorer`, `@_window_errors`, `_Picker`,
`_row_button`), so what `drift.py` still lists is mostly names that mean something different in each file, and the
few identical ones kept apart on purpose (see the plan). The render blocks and the renderer are there since Phase 3, and since
Phase 6 S3's tables (sorting, filters, the Columns menu, key columns) and finding headlines are the renderer every
service draws with, so a name `drift.py` lists under "Defined in an analyzer and in _kit or its shared module" is a
copy to delete (`rules.py` fails on it too).

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
- **Same name, different thing.** Each file's own idea under a name another file also uses: DynamoDB's and
  OpenSearch's `IndexInfo`, Lambda's and OpenSearch's `Overview`, OpenSearch's and SageMaker's `INSTANCE_TYPES`, the
  filter languages' `build_filter` / `describe_filter`, and in the Bedrock pair `Answer`, `DEFAULT_PROMPT`,
  `parse_rag`, `describe_filter`, `answer_findings`, `_question_text` and `View._answer_blocks` (the knowledge base's
  answers come from Converse too; the chat's keep their request and response). Never merge these.
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
