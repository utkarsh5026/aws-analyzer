---
name: new-analyzer
description: Scaffold a new AWS service analyzer - analyzers/<service>.py with the five-section layout and the duplicated helpers, a first set of useful commands, moto tests, a README section, a docs guide page and a home-page card. Use when starting a new service such as the planned bedrock_kb (Bedrock Knowledge Bases).
argument-hint: "<module name, e.g. bedrock_kb> [what users should be able to see first]"
---

# New analyzer

Request: `$ARGUMENTS`. The first word is the module name (snake_case, and a valid Python identifier, because
users will `import` it). The rest, if given, says what users need from it.

A new analyzer is a new copy-paste file with the same shape as `analyzers/s3.py` and `analyzers/dynamodb.py`.
Read CLAUDE.md's "Hard constraints" and "Architecture of an analyzer file" first. Everything below follows them.
`dynamodb.py` is the newest and smallest analyzer, so model the new one on it.

## 1. Research the service (before writing code)

- **Clients and read-only operations.** List the operations of each boto3 client the analyzer will use. For
  Knowledge Bases these are `bedrock-agent` (configuration) and `bedrock-agent-runtime` (retrieval):

  ```bash
  .venv/bin/python -c "import botocore.session as s; m = s.get_session().get_service_model('bedrock-agent'); print(sorted(m.operation_names))"
  ```

  Only Get / List / Describe / Head / Search / Retrieve style calls. Nothing that writes, starts a job or ingests
  data.
- **moto support.** Look for the client in
  `.venv/bin/python -c "from moto.backend_index import backend_url_patterns as b; print([n for n, _ in b])"`.
  Where moto doesn't cover a client or an operation, the tests use `botocore.stub.Stubber` or
  `monkeypatch` instead. Say which in the plan.
- **Costs.** Work out what the service bills that users would want estimated. That becomes a
  `<SERVICE>_PRICES` table of us-east-1 list prices, and callers can override it with `prices=`.
- **IAM.** List the permission each operation needs.

## 2. Agree on the first commands

Propose three to five View commands and confirm them with the user before building. The one-argument call of
the first command has to be useful. A typical set:

- an overview of everything in the region, with the warnings, like `tables()` / `overview()`
- a detail report for one resource, with findings and the call to copy for the next step, like `table_info()` /
  `bucket_info()`
- one or two commands that look at the data itself (for Knowledge Bases: data sources and sync status, and a
  test retrieval that shows the chunks returned with their scores and sources)

For each command, give its name, what the user decides from it, its cards, and its findings.

## 3. Create `analyzers/<service>.py`

Pick the names first:

- the classes: `<Service>Analyzer` / `<Service>View` (for example `BedrockKBAnalyzer` / `BedrockKBView`)
- a short CSS root class for `_render_html` (`s3a` and `ddb` are taken)
- the price table: `<SERVICE>_PRICES`

Then build the file:

1. **Module docstring.** Same shape as the others: what it is, "Copy this one file...", the requirements, the
   two layers, and a quick start that shows the commands.
2. **Imports.** `from __future__ import annotations`, then only the stdlib, `boto3` and `botocore` at the top.
   Anything optional is imported inside functions through `_require`.
3. **The five banners**, exactly in this form: `# 1. Helpers: ...`, `# 2. Data models (what <Service>Analyzer
   returns)`, `# 3. Pure analysis (no AWS calls - ...)`, `# 4. <Service>Analyzer - pure logic layer ...`,
   `# 5. <Service>View - notebook UI layer ...`, each between `# ===...` lines.
4. **Shared helpers.** Copy them verbatim from `dynamodb.py`: every name in CLAUDE.md's duplicated list, plus
   the small helpers they use (`_utcnow`, `_plural`, `_clip`, `_error_code`, `_units`, `_visible_rows`,
   `_NUMERIC_RE`, `_text_bar`, `_fmt_dt`, `_share`, `_CSS`). Change only the service names and the CSS root
   class. Then run `.venv/bin/python .claude/skills/sync-helpers/drift.py`: every copied helper should be
   reported as identical, or as differing only in docstrings.
5. **The analyzer.** The constructor matches the others:
   `(session=None, *, region=None, profile=None, client=None, prices=None)`. Clients are made lazily, and a
   missing region turns into a readable `ValueError` (see `DynamoDBAnalyzer.client`). Use
   `Config(retries={"max_attempts": 10, "mode": "adaptive"})`. Record config sections the caller can't read in
   `errors`. Long reads take `limit=` and `progress=`.
6. **The View.** `__init__(core=None, *, mode="auto", max_rows=50)`, plus `_show`, `_progress`, `help` and
   `_price_basis` copied from the other Views. Every command is decorated with `@_friendly_errors`, and its
   docstring's first line is its help() text. `_friendly_errors` gets a service-specific not-found message.

Build each command the way `/add-command` does: the data model, then the pure function and its findings, then
the analyzer method, then the View method.

## 4. Tests: `tests/test_<service>.py`

Use the same layout as `tests/test_dynamodb.py`: `# ---- helpers` (pure functions), `# ---- AWS (moto)` with
an `aws` fixture around `mock_aws()` plus seeded fixtures, and `# ---- UI` with `ui` in `mode="text"` and the
`run(capsys, fn, ...)` helper. Cover at least one failure path that shows a note instead of a traceback.

`tests/conftest.py` already puts `analyzers/` on `sys.path` and sets fake credentials. If moto supports the
service, add its extra to the `moto[...]` line in `requirements-dev.txt` and `pip install -r
requirements-dev.txt`.

CI needs no changes: it lints, imports and tests every `analyzers/*.py`. Check that the loop in
`.github/workflows/ci.yml` still globs.

## 5. Docs

- `README.md`:
  - In the service table at the top, set the status to ✅ and link the file.
  - Add a `## <Service> quick start` section with the same parts as the DynamoDB one: quick start, the commands
    table, "Getting the data", cost notes, and IAM permissions.
  - Update the Development paragraph if it names the guides.
- `docs/<service>.html`: a new guide. Copy `docs/dynamodb.html`'s structure, CSS and theme handling, and cover
  set up in SageMaker, a five-minute tour, a section per area, using the data in Python, cost, permissions,
  troubleshooting and a command reference. Leave out screenshots you can't make yet (see `/demo --html`).
- `docs/index.html`: add a card for the new guide next to the existing ones.
- `CLAUDE.md`: update the list of analyzers (`bedrock_kb.py` is planned), the price tables, the moto extras
  and anything service-specific a future session needs.

## 6. Demo data and verification

1. Add `seed_<service>()` and an entry in `SEEDERS` in `.claude/skills/demo/demo.py`, if moto supports the
   service.
2. Run `/check`. `rules.py` must report 0 errors, and every IAM warning is fixed in the README.
3. Run `/demo <service>` on each command and read the output against the product rules.

Report the files created, the commands with their help() lines, a demo excerpt, the IAM permissions, and what
is still missing (screenshots, commands left for later). Don't commit unless asked.
