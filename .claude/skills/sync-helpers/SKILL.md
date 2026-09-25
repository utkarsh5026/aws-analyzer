---
name: sync-helpers
description: Compare the helpers duplicated across analyzers/*.py (human_size, human_money, _require, _esc, the render blocks, _render_html / _render_text, _friendly_errors, View._progress, View.help, ...) and port fixes between the copies. Use after changing one of those helpers in one analyzer, when /check reports drift, or when a new analyzer copies them in.
argument-hint: "[helper names, e.g. _render_text help]"
allowed-tools: Bash(.venv/bin/python .claude/skills/sync-helpers/drift.py:*), Bash(python .claude/skills/sync-helpers/drift.py:*)
---

# Sync the duplicated helpers

Each analyzer must work alone in a notebook, so the helpers they share are copied into every file on purpose
(see "Hard constraints" in CLAUDE.md). A fix made to one copy has to reach the others by hand. This skill finds
the copies that have drifted apart and brings them back in line.

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

- **Intentional, service-specific.** Examples: S3's extra render blocks (`_Frame`, `_Image`, `_Link`, `_Media`,
  `_Text.wrap`) and the CSS and render branches for them; the not-found message in `_friendly_errors`
  ("object not found" vs "table not found in <region>"); default labels in `_progress`; the signatures of
  `__init__`, `_section` and `_price_basis`. Leave these alone. The shared part of the function around them
  should still match, though: a branch for a block both analyzers have should be identical.
- **Drift.** One copy got a fix, a new edge case or better wording that the other copy didn't get. To find out
  which copy is newer and why it changed:
  `git log -L :<name>:analyzers/<service>.py --oneline | head -40`, or `git log -S '<changed line>' --oneline`.
- **Unclear.** Say what you found and ask before changing it.

Docstring-only differences are fine when the wording is service-specific ("objects" vs "items"). Otherwise,
keep the clearer wording in both copies.

## 3. Port the fix

- Copy the newer version into every other analyzer, and change only the service-specific names. Keep everything
  else character for character, so the next drift check reports the helper as identical.
- If an analyzer is missing a shared helper, copy it in from the analyzer that changed most recently, into the
  same section (helpers in section 1, render blocks and `_render_*` at the start of section 5).
- A helper change affects every analyzer's output, so run the tests for all of them.

## 4. Verify and report

Run `drift.py` again: the helpers you synced should now be identical or docs-only. Then run `/check`.

Report a short table with one row per helper that differed: whether it was synced (and in which direction),
whether the difference was intentional (and why), or whether it was left for the user to decide.
