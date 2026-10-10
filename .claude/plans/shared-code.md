# Plan: one shared code base for every analyzer

**Status:** Phase 0 (`snapshot.py`) and Phase 1 (the move to `src/aws_analyzer/`) merged in #61. Phase 2 (`_kit`
with the leaf helpers, install-only docs) is the next pull request. The last pull request deletes this file.

**Decisions confirmed:** D1 `src/aws_analyzer/`, one pull request per phase, D5 per-service CSS roots kept.

**Decision already made:** `pip install aws-analyzer` becomes the way to use the project. The analyzers stop being
standalone files and import their shared code from one place. There is no code generator.

Numbers below are from `main` at `3a2d534` (v0.13.0, with the Lambda explorer), measured in Phase 0.

---

## 1. Why, in numbers

- 7 analyzers plus the `s3_explorer.py` companion add up to 58,881 lines.
- 50 helpers are copied into all 7 analyzers. Each copy is about 770 lines, so about 5,400 lines are repeats.
- `bedrock_kb.py` and `bedrock_chat.py` also share 103 more names, about 1,600 lines.
- `drift.py` finds 201 names defined in more than one analyzer. 155 are identical, 40 differ in code and 6 differ
  only in docstrings.
- Improvements stay in one file. S3's tables sort, filter and choose columns (its `_CSS` is 200 lines, 54
  elsewhere), and no other service has that. Only 2 of the 56 commits to `analyzers/` changed 4 or more files.
- The four windows (S3 explorer, chat, KB explorer and now the Lambda explorer) each have their own picker,
  background runner, error guard and row button. The Lambda explorer copied about 440 lines from the KB explorer
  (`_EXPLORER_CSS`, `_explorer_rules`, `_window_text`, `_for_window`, `_window_errors`, `explore`, ...), shares
  `_cell_number`, `_class_if`, `_css_height`, `_marked`, `_running_loop` and `search_rank` with both Bedrock files,
  and `_python_html` / `_json_source_html` with the chat. The four window classes share 39 method names
  (`_later`, `_safely`, `_set`, `_renew`, `_draw_rows`, ...), and only 5 of them are identical; `drift.py` doesn't
  compare window classes, so it sees none of this.

**What success looks like:** a change to reports, tables, findings, progress, `help()` or a window primitive is
one edit in one file, and every service gets it. A new service subclasses two base classes instead of pasting
about 800 lines.

## 2. Decisions to confirm before Phase 0

| # | Decision | Recommendation | Why |
|---|---|---|---|
| D1 | Where the code lives | Move `analyzers/*.py` to `src/aws_analyzer/` with `git mv` | A normal package: relative imports just work, no `force-include` mapping, and tests import what users import. A pure rename keeps `git log --follow` and blame. |
| D2 | One import line for everyone | `from aws_analyzer import S3View` | The same line works for pip, a wheel copied from S3, or the `aws_analyzer/` folder uploaded next to the notebook. `from s3 import S3View` goes away. |
| D3 | Name of the shared package | `aws_analyzer/_kit/` (private) | Helpers keep their names and are imported into each service, so `aws_analyzer.s3.human_size` keeps working. |
| D4 | Bedrock's shared domain code | `aws_analyzer/_bedrock.py` | It's Bedrock logic (models, prices, markdown, filters), not UI, so it stays out of `_kit`. |
| D5 | CSS root class per service (`.s3a`, `.ddb`, `.kba`, `.kbc`, `.smk`, `.osv`, `.lmb`) | Keep them; the shared CSS takes the root as a parameter | The HTML stays byte-identical, and the window CSS that targets these roots needs no edits. |
| D6 | Services importing each other | Still forbidden. Services import only `_kit` and `_bedrock`; `s3_explorer` may import `s3` (the one companion) | Importing `aws_analyzer.dynamodb` must not load S3's 12,000 lines. Each service stays readable on its own. |
| D7 | Releases | Each phase is a separate pull request that changes no behaviour, so a release can go out between any two. The first release containing Phase 2 carries the **Breaking** note for file users. | pip users see no API change. People using copied files break at Phase 2 whether or not a release goes out. |
| D8 | Offline / VPC-only notebooks | Install the wheel from S3: `aws s3 cp s3://bucket/tools/aws_analyzer-X-py3-none-any.whl . && pip install --no-deps aws_analyzer-X-py3-none-any.whl`. Optionally attach the wheel to each GitHub release. | This replaces "copy s3.py from S3". boto3 is already on SageMaker images, hence `--no-deps`. |

## 3. Target layout

```
src/aws_analyzer/
  __init__.py          lazy exports, unchanged API (S3View, DynamoDBView, chat, KBExplorer, ...)
  _kit/                shared by every service. Top-level imports: stdlib + boto3/botocore only. Never imports a service.
    fmt.py             human_size, human_money, human_age, _plural, _fmt_dt, _utcnow, parse_size, parse_time, _as_int, ...
    deps.py            _require, _in_notebook
    errors.py          _error_code, _error_name, _why, _Hint, _friendly_errors (with hooks, see Phase 4)
    blocks.py          _Title, _Cards, _Tone, _Table, _Note, _Text, _Findings, _Next, plus the block/cell registries
    render.py          base CSS (templated on the root class), _prose, _call, _signature, _render_html, _render_text
    progress.py        _progress_bar_class, _progress_bar, _progress_text, _duration
    view.py            _BaseView: _show, help, _progress, _price_basis common part, _commands(cls)
    analyzer.py        _BaseAnalyzer: session/region/profile/prices, _cached_client, _paginate, _map
    widgets/           Phase 7: jobs, guard, app shell (style, icons, tabs), picker, rows
  _bedrock.py          shared by bedrock_kb and bedrock_chat
  s3.py  s3_explorer.py  dynamodb.py  bedrock_kb.py  bedrock_chat.py  sagemaker_env.py  opensearch.py  lambda_functions.py
```

The table above is a guide. In each phase, a helper goes where its own imports say it belongs.

**Each service keeps** its five numbered sections: helpers that only it uses, data models, pure analysis and
findings, the `<Service>Analyzer`, and the `<Service>View` commands, plus any block types only it draws (`_Passage`,
`_Pages`, `_Steps`, ...). It sets class attributes on its View instead of editing shared code: `_ROOT`, `_BADGE`,
`_GROUPS`, `_START`, `_EXTRA_CSS`, `_DATA_ERRORS`, progress labels.

## 4. What moves, and what doesn't

**Moves:** a name moves only when its copies are identical once the service names are normalized (what `drift.py`
already computes), or when the only difference can be written as a parameter or a hook.

**Never merged just because the names match.** These share a name but do different things, so they stay in their
services:

- `IndexInfo` (DynamoDB vs OpenSearch)
- `Overview` (Lambda vs OpenSearch)
- `build_filter` / `describe_filter` (DynamoDB, OpenSearch and Bedrock filter languages)
- `_SECTIONS`, `_section`, `Analyzer._service`, `View._explain`, `View._settings_rows`, `_number`

**Drifted copies** (30 today) are sorted the `/sync-helpers` way before moving. If one copy has a fix the other
lacks, port the fix first in its own commit; if its output changes, the snapshot diff shows it and it gets a
CHANGELOG "Fixed" line. If the difference is intentional, it becomes a parameter. If it's unclear, ask.

## 5. Guardrails (in place from the phase that needs them, kept afterwards)

1. **Output snapshots** (new in Phase 0, `.claude/skills/check/snapshot.py`). For every service, it runs the demo
   tour from `/demo` plus `help()`, in text mode and in HTML mode, on demo.py's seeded data. It normalizes what
   changes from run to run (durations, rates, today's timestamps, moto's random version and request IDs) and writes
   one file per command. `--compare <dir>` prints the diffs. The baseline is taken from `main` in a `git worktree`.
   **Every phase except Phase 6 must produce byte-identical snapshots.** An unexpected diff stops the work: find
   the cause, never refresh the baseline to make it pass.
2. **Public names.** A dump of every name each module exposes (`dir(module)`, with and without `_`), taken before
   the work. After each phase, every name that existed is still importable from the same module.
3. **AWS calls.** `rules.py --apis` output is identical before and after. Clients are still created with the
   service name spelled out, in the service file (`self.session.client("lambda", ...)` passed to the base's
   `_cached_client(name, make)`), so `rules.py` keeps finding every operation and the README IAM list stays right.
4. **Independence test** (`tests/test_package.py`): in a fresh subprocess, importing `aws_analyzer.<service>` loads
   no other service module (except `s3` for `s3_explorer`). `_kit` never imports a service.
5. **No re-copying** (new `rules.py` error): a service module may not define a top-level name that `_kit` (or, for
   the Bedrock pair, `_bedrock`) defines. This replaces `drift.py` for everything that has moved.
6. **One copy of each module** (conftest): the test session fails if a top-level `s3`, `dynamodb`, ... module is in
   `sys.modules` beside `aws_analyzer.s3`. Two copies would make `isinstance` checks fail between them.
7. **Only boto3 at import time**, as now: CI imports every module, `_kit` included, with only boto3 installed.

**Definition of done for every pull request:** `ruff check .`; `python -m pytest` (CI runs 3.10–3.14);
`rules.py` passes with `--apis` unchanged; the package builds, passes `twine check --strict` and imports with
only boto3; snapshots and public names are unchanged; `mkdocs build --strict` passes; CHANGELOG has an entry if
a user would notice; CLAUDE.md and the skills describe what the pull request changed.

## 6. Phases

Each phase is one pull request, or a few. Each one reverts cleanly. Within a phase, convert one service per
commit and run the tests after each.

### Phase 0: baseline (no change to `analyzers/`)

- Start the branch fresh from `main` once the Lambda window has merged.
- Run `drift.py` again, and compare the windows (now four) primitive by primitive, to update section 1 and the
  Phase 7 list.
- Add `snapshot.py` (guardrail 1) and the public-names dump (guardrail 2). Record the baselines from `main`.
- Confirm D1–D8 with the user.

### Phase 1: move to a package (renames and paths only)

- `git mv analyzers/*.py src/aws_analyzer/`, in a commit with no content changes, so git records 100% renames.
- `pyproject.toml`:
  - drop `force-include` and `/analyzers/*.py` from the sdist
  - add `[tool.pytest.ini_options] pythonpath = ["src"]`
  - update the comments
- Tests: `import s3 as s3mod` becomes `from aws_analyzer import s3 as s3mod`, and `from s3 import (...)` becomes
  `from aws_analyzer.s3 import (...)`. conftest drops the `analyzers/` path and adds guardrail 6.
  `test_wheel_ships_every_analyzer` checks the package folder instead of `force-include`.
- `mypy.ini`: set `mypy_path = src` and drop the `aws_analyzer.*` ignore. In `__init__.py`, drop the pyright
  comment.
- Point these at `src/aws_analyzer/` and the package imports:
  - CI's "import each analyzer on its own" step: each module, with only boto3, `PYTHONPATH=src`
  - `run.py`'s imports step
  - `rules.py`'s `ANALYZERS` (skip `__init__.py`), `drift.py` and `release.py`'s package paths
  - `demo.py`, `shots.py`, and the three JupyterLab shot scripts (each writes a `sys.path` line into the kernel)
- README and docs: update links from `analyzers/x.py` to `src/aws_analyzer/x.py`. The files still work alone in
  this phase, so the install text stays true. Update CLAUDE.md's paths.
- Gates: everything in section 5. Snapshots are identical by construction.

### Phase 2: `_kit` foundation (leaf helpers) and the switch to install-only

Done: `_kit/fmt.py`, `text.py` (`_esc`, `_clip`, `_pad`, `_width`, `_text_bar`), `deps.py` and `errors.py`, 31 names
in all; every copy was checked to be the same code before it was removed. Dead private copies were dropped rather
than imported (`bedrock_chat._fmt_dt`, three `_share`, `sagemaker_env._count`, `lambda_functions._error_name`, and the
regexes only the moved helpers used), and `s3_explorer`'s own `_fmt_dt`, which differs, became `_tip_time`.

- Create `_kit/__init__.py`, `fmt.py`, `deps.py` and `errors.py` (only `_error_code`, `_error_name`, `_why`, `_Hint`).
  Move the identical leaf helpers: formatting, `_require` / `_in_notebook`, `_esc`, `_clip`, `_pad`, `_width`,
  `_plural`, `parse_size` / `parse_time` and the partly shared ones (`_as_int`, `_as_count`, `_count`, `_share`,
  `HOURS_PER_MONTH`, ...). Each service deletes its copy and imports the names it uses (only those, for ruff F401).
- Add `rules.py` guardrail 5, and import rules: a service imports only `._kit.*`, `._bedrock` and its companion
  parent; `_kit` imports no service. `_kit` files skip the banner and View checks but get the import, `print()`
  and read-only checks.
- Tests that patch a moved helper on a service module now patch it where it's looked up: `_require` and
  `_in_notebook` here; `_progress_bar_class` in Phase 4; `_cell_number` (14 places) in Phase 7. Run
  `grep -n 'setattr(<mod>, "<name>"'` for every moved name.
- **This pull request also rewrites the install documentation**, because it's the first one where a file no
  longer works alone:
  - README "Get started" (pip, the wheel from S3 for VPC-only, or upload the `aws_analyzer/` folder), the
    "One file, boto3 only" card, the Services table's File column, and every `from s3 import ...` line
  - each guide's setup steps in `docs/*.md`, and `s3_explorer.md`'s "put both files"
  - CONTRIBUTING.md, the `pyproject.toml` comments and the `__init__.py` docstring
  - CLAUDE.md's Hard constraints and "What this is"
  - CHANGELOG, under Unreleased, a **Breaking** entry: install with pip and import from `aws_analyzer`. Copied
    0.12.0 files keep working as they are.
- Gates: snapshots identical.

### Phase 3: one renderer, extended per service

1. **Inside each service, before anything moves**, replace the `isinstance` chains in `_render_html` /
   `_render_text` with registries:
   - `_HTML[type] = fn` / `_TEXT[type] = fn`, filled by a decorator (`@_draws(_Passage)`)
   - a cell registry for `_Tone` and `_Link` cells
   - a block type with no renderer becomes a note, never a traceback

   After this step the shared part of the renderer is identical in all services. Snapshots identical.
2. Split each `_CSS` into the common rules (identical once `.<root>` is normalized) and `_EXTRA_CSS`. The common
   rules become `base_css(root)`. `KBExplorer`'s `_EXPLORER_CSS` embeds the report CSS once and `_html` strips it
   by string, so keep that string exactly as it is (`s3_explorer` does the same).
3. Move the blocks, the registries, `_prose`, `_call`, `_signature`, `_CALL_RE`, `_MARKS`, `_SELECT`, the findings
   HTML and both renderers into `_kit/blocks.py` and `_kit/render.py`. Service-only blocks stay in their service
   and register themselves when it's imported.
4. **S3 keeps its own, more advanced renderer in this phase** (sort, filter, columns, path columns). It's the one
   listed exception until Phase 6.

- Gates: snapshots identical.

### Phase 4: base View and base Analyzer

- `_kit/view.py`, `_BaseView`:
  - the common part of `__init__` (mode, max_rows, progress), `_show` / display, `_progress` and its helpers,
    and the common part of `_price_basis`
  - `help()` lists commands with `_commands(cls)`, which walks the MRO and leaves out `_BaseView`'s own methods
    except `help`. Today `help()` and the `test_ui_help_groups_every_command` tests read `vars(type(self))`, which
    would miss `help` once it moves to the base. The tests switch to `_commands` too.
  - The base defines no public method other than `help`.
- `_friendly_errors` becomes one decorator with hooks, replacing today's seven variants:
  - `self._explain(code, message, method, args, kwargs)`: S3 maps 404s to "object not found", DynamoDB adds
    `_not_found`'s close names, the base returns the message
  - `_DATA_ERRORS` as a class attribute (S3's extra decoding errors)
  - `self._extra_note(exc, name)` for OpenSearch's `OpenSearchError` and its notes on unexpected response shapes
  - `_Hint` is handled for every service (S3 and DynamoDB gain it, which is harmless)
- `_kit/analyzer.py`, `_BaseAnalyzer`: session, region, profile and prices; `_cached_client(name, make)`;
  `_paginate`; `_map`. Each service keeps its own `__init__` signature and calls `super().__init__`.
- **Shared state audit.** Module-level price tables and caches are now one object reached by several services.
  Check that `prices=` / `model_prices=` never mutate a module dict (copy into the instance), and add a test
  that one analyzer's overrides don't change another's.
- Gates: snapshots identical, and `--apis` unchanged.

### Phase 5: `_bedrock.py`

- Move the 97 names `bedrock_kb` and `bedrock_chat` share whose code matches (`_highlight` differs only in its
  docstring), including the price tables, `DEFAULT_MODEL`,
  the model helpers, `_Markdown`, `_Json` / `_json_html`, the filter helpers, `parse_*`, `search_rank`, `_marked`
  and `_cached_client`.
- Resolve the 6 that differ (`Answer`, `DEFAULT_PROMPT`, `View._answer_blocks`, `_question_text`,
  `answer_findings`, `parse_rag`) by the section 4 rule. Find which copy is newer with `git log -L`.
- Gates: snapshots identical, except any drift fix ported on purpose (listed in the pull request and the
  CHANGELOG).

### Phase 6: S3's renderer becomes everyone's (the one visible change)

- Port S3's `_table_html` (sort, filter, the Columns menu, `path_cols`, `sortable=`), `_split_lead`,
  `_finding_html`, `_lead` and the text renderer's path handling into `_kit`. Delete S3's own copy. Mark tables
  that shouldn't sort `sortable=False`.
- Review every snapshot diff. HTML is expected to change in every service; text should change only in finding and
  note lead-ins. Update HTML assertions one at a time; never regenerate them in bulk.
- Remake the screenshots (`shots.py`, plus the window shot scripts where a window shows reports). Check captions
  and alt text in the guides and in the README's eleven `<picture>`s.
- CHANGELOG "Changed": tables in every report sort, filter and choose columns, as S3's already do.

### Phase 7: window kit (four windows: S3 explorer, chat, KB explorer, Lambda)

- Inventory first: put each primitive's four versions side by side and choose the most complete one.
- Primitives, in order of risk:
  1. small helpers: `_css_height`, `_running_loop`, `_cell_number`, `_class_if`, once-per-cell
     `_ipython_display_`
  2. the callback guard (`_safely` / `_guard`, plus `_window_errors`) and `_set`, which only sends HTML that changed
  3. background work (`_later`, `_workers` / `_threads`, job keys, `_tasks`): every window runs AWS reads on the
     loop's executor and drops stale results
  4. the style widget, icon masks (`_ICON_PATHS`, `_tab_rules`, `_explorer_rules`) and the tab bar
  5. full-row buttons (`_Row`, `_FileRow`), `_renew`, `_CLICK_GRACE`, and Enter handled through the text box's
     `submit` message
  6. the searchable picker with its backdrop (`_Picker` + `_Choice`, `_Chooser`)
- Use one pull request per one or two primitives, each changing no behaviour. Gates: the window tests, which
  click and type through the widgets (including their background paths inside `asyncio.run`), plus before and
  after screenshots from `explorer_shots.py`, `chat_shots.py`, `kb_explorer_shots.py` and the Lambda window's
  shot script.
- Move test patches to the kit (`_cell_number`: 14 places).

### Phase 8: cleanup

- Retire `drift.py` and `/sync-helpers`, and remove `run.py`'s drift step. Guardrail 5 now does that job.
- Update the skills:
  - `/new-analyzer`: subclass `_BaseAnalyzer` / `_BaseView`, register blocks, add the module to `__init__.py`
  - `/add-command`, `/analyzer-review` (the new constraints), `/check`, `/demo` and `/release`
- A final pass over CLAUDE.md: "Hard constraints", "Architecture of an analyzer file" and "How the View layer works".
- Delete this file.

## 7. Risks

| Risk | Where it bites | How it's handled |
|---|---|---|
| A test patches a helper on the service module, but the code now calls the kit's copy, so the patch does nothing | `_cell_number` ×14, `_progress_bar_class` ×2, `_require`, `_in_notebook` | Patch where the name is looked up; grep checklist in every phase |
| `help()` loses `help` when it moves to the base | `vars(type(self))` in `help()` and in the 7 group tests | `_commands(cls)` walks the MRO; tests use it |
| Merging two things that only share a name | `IndexInfo`, `Overview`, `build_filter`, ... | Section 4 rule: identical after normalizing, or a parameter |
| Two copies of a module break `isinstance` | Tests or demo scripts importing both `s3` and `aws_analyzer.s3` | Guardrail 6; every import path goes through the package |
| Shared price tables or caches leak between services | `prices=`, `model_prices=`, module-level caches | Phase 4 audit and test |
| Importing one service loads all of them | A kit module importing a service | Guardrail 4; `_kit` is a leaf |
| `rules.py` stops seeing AWS calls | Moving client creation into the base | Literal `session.client("...")` stays in services; `--apis` gate |
| HTML or CSS shifts, breaking window CSS or old notebook outputs | Root class renames, CSS splits | Roots kept (D5); byte-identical snapshots; Phase 6 is the only visual change |
| `KBExplorer` / `S3Explorer` strip the embedded report CSS by string | Phase 3 CSS split | Keep the string exactly; the window tests cover it |
| Copied-file users break | Phase 2 | **Breaking** note; docs in the same pull request; 0.12.0 files keep working |
| VPC-only notebooks can't reach PyPI | Phase 2 docs | The wheel from S3 (D8) |
| Conflicts with the Lambda window branch | Every service file and the windows | Don't start until it's merged; Phase 0 starts from the new `main` |
| One huge, unreviewable change | Everything | One pull request per phase, one commit per service, each reverts cleanly |

## 8. Files to update (checklist)

- **Code and config:** `pyproject.toml`, `src/aws_analyzer/__init__.py`, `mypy.ini`, `tests/conftest.py`, every
  `tests/test_*.py` import, and `tests/test_package.py`.
- **CI and tooling:** `.github/workflows/ci.yml` (and `release.yml` if the wheel is attached to releases);
  `.claude/skills/check/{run.py,rules.py,SKILL.md}`, `.claude/skills/sync-helpers/*`,
  `.claude/skills/demo/{demo.py,shots.py,explorer_shots.py,chat_shots.py,kb_explorer_shots.py}` and the Lambda
  window's shot script, and `.claude/skills/release/release.py`.
- **Docs:** `README.md` (install, cards, Services table, import lines), `docs/index.md` and every `docs/<service>.md`
  setup section, `CONTRIBUTING.md`, `SECURITY.md` if it mentions files, `.github/pull_request_template.md` and
  the issue templates if they say "file", `CHANGELOG.md`, and `CLAUDE.md`.
- **Skills:** `new-analyzer`, `add-command`, `analyzer-review`, `check`, `demo`, `release`, `sync-helpers`.

## 9. Start trigger: when the Lambda window merges

1. The user says the Lambda window is merged.
2. Restart this branch from the new `main`. It holds only this plan, so carry the plan over.
3. Phase 0: measure again (four windows now), build the snapshots, and confirm D1–D8.
4. Phases 1 to 8 in order, one pull request at a time, each meeting the section 5 definition of done.

A tip for the Lambda window branch (optional): reusing `KBExplorer`'s names and shapes (`_later`, `_safely`,
`_set`, `_renew`, `_Chooser`, `_FileRow`) instead of new variants leaves less to reconcile in Phase 7.
