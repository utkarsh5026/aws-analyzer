# Changelog

What's new in each release of [aws-analyzer](https://pypi.org/project/aws-analyzer/).

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before 1.0, a minor version (0.2.0) adds commands or
changes what one shows, and a patch (0.1.1) only fixes things. A change that could break a notebook starts with
**Breaking** and says what to change. To upgrade: `pip install -U aws-analyzer`.

## [Unreleased]

### Added

- 🤖 **Agent: asks before going past a limit** ([#156](https://github.com/utkarsh5026/aws-analyzer/pull/156))
  - A question that uses up a limit (tool calls, time, cost, Python runs, searches, charts, workspace size) asks to raise it for that question.
  - `save_s3_files` asks to copy every matching file when it finds more than the agent asked for (up to `budget={"save_files": 500}`).
  - Go, smaller or skip, as for big reads; `AgentView(ask_more=False)` never asks.

- 🤖 **Agent: remembers your account between conversations** ([#160](https://github.com/utkarsh5026/aws-analyzer/pull/160))
  - `AgentView(memory=True)`: it keeps dated notes like "orders-etl runs in eu-west-1" and starts from them next time.
  - `memory()` or the chat window's 🧠 memory shows them; `forget('orders-etl')`, a note's ✕ or `forget_all()` clears them.
  - `notes=` also takes a file (`notes="AGENTS.md"`), read again for each new conversation.

- 🤖 **Agent: findings and next calls in every answer** ([#159](https://github.com/utkarsh5026/aws-analyzer/pull/159))
  - The answer ends with a findings panel, warnings first: what's wrong, why it matters and what to do.
  - Each finding names a `LambdaView`, `S3View` or `BedrockKBView` call, checked before it's shown; they lead "Look closer".
  - `answer.findings` holds them as data; `AgentView(findings=False)` answers in plain text as before.

## [0.34.0] - 2026-10-26

### Added

- 🤖 **Agent: tests what a knowledge base answers** ([#151](https://github.com/utkarsh5026/aws-analyzer/pull/151))
  - New `answer_kb` tool: the answer Bedrock's `RetrieveAndGenerate` gives, what it cites, and how grounded it is.
  - The agent compares it with `search_kb`'s passages to tell a search problem from a model problem.
  - Counts against `kb_searches`; its estimated cost is in the answer's AWS cost. Needs `bedrock:RetrieveAndGenerate`.

- 🧰 **Copy buttons on code** ([#152](https://github.com/utkarsh5026/aws-analyzer/pull/152))
  - Code in answers (the agent's, Bedrock chat's, `ask()`'s) and code to copy in any report gets a ⧉ Copy button.
  - One click selects it all; press Ctrl+C (⌘C on a Mac). It works in saved notebooks too.
  - The agent's chat window has one on the code each `run_python` call ran.

### Changed

- 🤖 **Agent: knowledge base tools on by default** ([#153](https://github.com/utkarsh5026/aws-analyzer/pull/153))
  - `AgentView()` now has the Bedrock knowledge base tools beside Lambda's and S3's; no `toolkits=` needed.
  - Passages they find go to the model's provider, as S3 and Lambda results do; `toolkits=["lambda", "s3"]` leaves them out.
  - The chat window suggests "Are my knowledge bases up to date?" to start from.

## [0.33.0] - 2026-10-26

### Added

- Agent: for a question that takes several steps, the agent now writes a short plan first ("check every function's
  health → read orders-etl's errors → read the code where it fails") and ticks it off as it works. `chat()` shows it
  as a checklist under your question that updates in place, and `ask()`'s report shows it above the tool calls; a
  step it didn't mark done shows as a finding. `answer.plan` holds the steps, and `ask(on_step=...)` is told each
  version (`kind='plan'`). Writing a plan reads nothing and doesn't count against the budget; `AgentView(plan=False)`
  turns it off to save its instructions' tokens.
  ([#146](https://github.com/utkarsh5026/aws-analyzer/pull/146))
- Agent: before a big read, the agent now stops and asks you instead of hitting the budget: an S3 listing past 20,000
  keys ("find_files has listed 20,000 keys under s3://acme-raw/ and there are more; the bucket holds about 41,200
  files"), a log read longer than the 3-day limit, or a save of more than 10 MB into the Python workspace. Pick
  **go** (all of it, with the requests it takes and what they cost), **smaller** (the keys listed so far, the last 3
  days, the files that fit) or **skip**: `chat()` shows three buttons under the call, and after `ask()` you run
  `go()`, `smaller()` or `skip()`. The answer says what a smaller read or a skip left out, and the wait doesn't count
  against the time budget. `budget={"ask_keys": ..., "ask_bytes": ...}` moves the thresholds, and
  `AgentView(interrupt_on=False)` turns it off (or `interrupt_on=["find_files"]` for some tools). In Python,
  `core.ask()` returns the answer with `.waiting` set and `core.resume("go")` goes on.
  ([#148](https://github.com/utkarsh5026/aws-analyzer/pull/148))

## [0.32.0] - 2026-10-25

### Added

- Agent: `AgentView(toolkits=["lambda", "s3", "bedrock_kb"])` lets the agent answer questions about your Bedrock
  knowledge bases: "is support-docs up to date?", "why didn't last night's sync index the new PDFs?", "why doesn't it
  answer the refund question?". Six new read-only tools: `find_knowledge_bases`, `kb_health` (status, data sources,
  last syncs, documents by status, idle cost and findings), `kb_syncs` (why syncs failed), `kb_files` (each file next
  to Bedrock's record: failed, changed since the last sync, never synced, skipped, deleted), `search_kb` (the passages
  a question finds, as an answer would) and `file_chunks` (how a file was chunked, and with `question=` where it ranks
  against the whole knowledge base). Nothing syncs: the findings give the `start-ingestion-job` command to run. The
  read-only guard now lets Bedrock's `Retrieve` through by name, since it only searches, and a new budget limit,
  `kb_searches` (10), caps `search_kb` and `file_chunks` per question. The passages they return go to the model's
  provider, with anything that looks like a secret hidden. `kb_core=` passes your own `BedrockKBAnalyzer`.
  ([#144](https://github.com/utkarsh5026/aws-analyzer/pull/144))
- Agent: with a Python sandbox (`AgentView(python="local")`), the agent answers with charts. Ask "Chart the orders in
  s3://acme-raw/sales/ by month" and it reads the files it saved with pandas, makes the chart with Altair and puts it
  under the answer with the new `show_chart` tool, in the report, the chat window and `transcript()`. The sandbox draws
  it as a crisp SVG with Vega-Lite, with no network; every chart gets the same look (quiet axes, colours that stay
  apart for colour-blind readers), and the agent is told how to make one read. `save_charts()` writes an answer's
  charts to SVG files in `agent-charts/` with their Vega-Lite specs beside them (never replacing a file),
  `answer.charts` holds them as data, and a new budget limit, `charts` (8), caps them. The local sandbox now offers
  Altair by default (its first download grows from about 21 MB to 24 MB), and `LocalSandbox(vega_url=)` points at a
  mirror of Vega's files. `LocalSandbox(packages=["pandas", "matplotlib"])` draws with matplotlib instead.
  ([#140](https://github.com/utkarsh5026/aws-analyzer/pull/140))

### Fixed

- Agent: `LocalSandbox(packages=[...])` now offers packages whose dependencies Pyodide names with an underscore or a
  dot (Altair's `jsonschema_specifications`), which it used to refuse as unknown.
  ([#140](https://github.com/utkarsh5026/aws-analyzer/pull/140))

## [0.31.0] - 2026-10-23

### Added

- Agent: `AgentView(python="local")` gives the agent a Python sandbox for questions no single tool answers: what a
  folder of documents says, every place a function's code calls DynamoDB, errors by hour across a day of logs, a count
  across a hundred CSVs. Install it with `pip install "aws-analyzer[agent,sandbox]"` (the new `sandbox` extra brings
  Deno). The code runs in Pyodide on Deno with no network, no AWS credentials, no environment variables and none of
  the machine's files; the first run downloads about 21 MB of Pyodide once, checked against pinned hashes
  (`LocalSandbox().prepare()` does it ahead of time, `index_url=` points at a mirror). New tools copy data into its
  workspace and run code there: `save_s3_files`, `save_function_code`, `save_logs`, `run_python` and
  `workspace_files`, and deepagents' file tools (`read_file`, `write_file`, `edit_file`, `grep`, ...) work on the same
  files. What the code prints goes to the model's provider. New budget limits cap it: `python_runs` (10),
  `python_seconds` (30) and `workspace_bytes` (50 MB). `python=` also takes a `Workspace` of your own, to run the
  code in another sandbox. ([#137](https://github.com/utkarsh5026/aws-analyzer/pull/137))
- Lambda: `core.sources("orders-etl")` returns the text of a function's own source files, and `read_sources()` does
  the same for a .zip you already have. ([#137](https://github.com/utkarsh5026/aws-analyzer/pull/137))

### Changed

- Agent: the default `steps` budget (the agent loop's limit) goes from 80 to 200, so a question that uses most of
  its 25 tool calls isn't stopped by the step limit first.
  ([#137](https://github.com/utkarsh5026/aws-analyzer/pull/137))

## [0.30.0] - 2026-10-23

### Added

- Lambda: `diff("orders-etl")` shows what changed between two versions of a function: each of its own files line by
  line, with the Python functions the changes are in, the libraries packed with it that were added, removed or
  upgraded, and its settings and environment variable names (never their values). By default it compares `$LATEST`
  with the newest published version whose code is different; `diff("orders-etl", 3, 5)` compares two versions and
  `diff("orders-etl:prod")` what an alias runs. Its findings name the AWS calls the new code makes that the old code
  didn't, with the IAM permission each needs, and flag a Python file that no longer reads, an environment variable
  taken out that the code still reads, and a shorter timeout or less memory. Lambda keeps the code of
  published versions only, so for a function that was never published the report says there's nothing to compare
  and how to keep a copy of each deploy. `core.diff()` and `core.versions()` return the same as data, and
  `diff_packages()` compares two .zip files you already have.
  ([#135](https://github.com/utkarsh5026/aws-analyzer/pull/135))
- Agent: a new tool, `diff_versions`, lets the agent answer "what changed when it started failing?", and each error
  cause from `function_errors` now says which versions it ran on.
  ([#135](https://github.com/utkarsh5026/aws-analyzer/pull/135))
- Agent: `AgentView(python="local")` lets the agent write and run Python for questions no tool answers on its own
  (every place a function's code calls DynamoDB, a count across many files). It adds `run_python`, which runs the
  model's code and hands back what it printed, `workspace_files`, and Lambda's `save_function_code`, which saves a
  function's source files where the code can read them. The code runs on the notebook's machine in Pyodide (Python
  3.14 compiled to WebAssembly) on Deno, with no network, no environment variables, none of the machine's files and no
  subprocesses, so it can't reach AWS and the agent stays read-only; data gets in only through the tools. Install
  Deno with `pip install "aws-analyzer[agent,sandbox]"`; the first run downloads Pyodide's files once (about 21 MB with
  pandas, checked against their SHA-256) into `~/.cache/aws-analyzer/pyodide/`, and `LocalSandbox().prepare()` does it
  ahead of time. `LocalSandbox(packages=[...], memory=...)` offers more of Pyodide's packages, and `python=` takes a
  `Workspace` of your own for another sandbox. The chat window and the report show each run's code, and the budget
  caps runs (`python_runs`, `python_seconds`) and what's saved (`workspace_bytes`).
  ([#137](https://github.com/utkarsh5026/aws-analyzer/pull/137))
- Agent: with `python=`, the agent can also save S3 files and Lambda logs where its code reads them, so it can
  answer "what do the contracts in this folder say?" or "when did the errors start, hour by hour?". `save_s3_files`
  copies one file or a folder's matching files (50 by default, up to 500), with a PDF's, Word file's or PowerPoint
  file's text saved beside it, and skips files in Glacier until they're restored. `save_logs` saves a window of a
  function's logs as one line per log line and one per run (status, run time, memory, cold start, error), with
  anything that looks like a secret hidden.
  ([#137](https://github.com/utkarsh5026/aws-analyzer/pull/137))
- Agent: with `python=`, deepagents' file tools work on the workspace, so the agent writes a longer script with
  `write_file`, runs it with `run_python(file="analysis.py")`, fixes a line with `edit_file` instead of sending the
  whole script again, and reads or searches saved files with `read_file` and `grep` without spending a run (secrets
  hidden in what they show). Each of those calls counts against the question's tool calls and shows in the chat window
  and the report.
  ([#137](https://github.com/utkarsh5026/aws-analyzer/pull/137))
- Lambda: `core.sources("orders-etl")` returns the text of a function's own source files by path, and
  `read_sources()` does the same for a .zip you already have.
  ([#137](https://github.com/utkarsh5026/aws-analyzer/pull/137))

### Changed

- Agent: a question may now take 200 steps of the agent's loop (`budget={"steps": ...}`), up from 80, so a question
  that uses most of its 25 tool calls isn't stopped by the step limit first.
  ([#137](https://github.com/utkarsh5026/aws-analyzer/pull/137))

## [0.29.0] - 2026-10-23

### Added

- Agent: `AgentView(read_files=True)` lets the agent read what's inside an S3 file. A new tool, `preview_file`, reads
  a file the way `S3View().preview()` does (a table's columns and first rows, a JSON file's start, a text file's first
  lines, a PDF's or Word file's text, an archive's files), so you can ask "which columns does orders.parquet have?" or
  "why does this CSV fail to load?". It's off by default, because what it reads is sent to the model's provider.
  Anything that looks like a secret is hidden, and images, audio, video, binary files and download links are never
  sent. Two new budget limits cap it: `file_bytes` (2 MB read) and `file_chars` (50,000 characters sent).
  ([#133](https://github.com/utkarsh5026/aws-analyzer/pull/133))

## [0.28.0] - 2026-10-22

### Changed

- Agent: the agent is friendlier and shows its work. Before each set of tool calls it says what it's about to
  check and why, and when a result changes its plan it says what it found and what it'll look at next, so
  `chat()` reads like a coding assistant at work. It explains AWS terms in plain words, picks the likeliest
  meaning of a vague question and says so instead of asking, and ends an answer by offering the obvious next
  step. A greeting or "what can you do?" gets a short answer with questions to try.
  ([#131](https://github.com/utkarsh5026/aws-analyzer/pull/131))

## [0.27.0] - 2026-10-22

### Added

- Agent: the agent follows an error from the logs into the code. `function_errors` and `function_logs` now give each
  error's `in_code`, the file and line its traceback names (Python, Node.js and Ruby, from text or JSON logs). Three
  new tools then read the function's code the way coding tools do: `function_source` reads any lines of a file, like
  `sed -n`; `search_code` finds a text or a regular expression in every file, like `grep -rn`; and `find_symbol`
  finds where a Python function or class is defined and every line that uses it, like an editor's go to definition.
  Ask "why does orders-etl fail?" and the answer quotes the line that raised and says what to change. Nothing in the
  package is run, anything that looks like a secret is hidden, and secrets files (`.env`, keys) are never read.
  ([#128](https://github.com/utkarsh5026/aws-analyzer/pull/128))
- Lambda: `LambdaAnalyzer.search_code("orders-etl", "customer_id")` returns every line of a function's code that has
  a text, and `find_symbol("orders-etl", "load_orders")` returns where a Python name is defined and used. Their
  pure versions, `search_package` and `find_symbol_in`, take a .zip you already have.
  ([#128](https://github.com/utkarsh5026/aws-analyzer/pull/128))

### Changed

- Lambda: `code()`, `how_it_works()` and the agent keep the last two packages they downloaded. Reading another file
  of the same code no longer downloads it again. New code has a new hash, so it's always downloaded fresh.
  ([#128](https://github.com/utkarsh5026/aws-analyzer/pull/128))

## [0.26.0] - 2026-10-20

### Added

- Agent: the chat window (`AgentView.chat()`) takes your next question while it's still answering. Press Enter and
  the question waits under the conversation, marked queued, and is asked as a follow-up once the current answer is
  done, in the order you typed them. **✕** drops a queued question, and **stop** ends the current answer so the
  next one starts sooner. Before, the window refused a question until the answer was finished.
  ([#124](https://github.com/utkarsh5026/aws-analyzer/pull/124))

### Changed

- Agent: **stop** in the chat window (and `agent.core.ask(..., stop=event)`) now ends with a written answer. The
  tools stop, and the model answers from what it had read and says what it didn't get to check. Before, a stopped
  question usually ended with only its steps and a note that no answer was written.
  ([#124](https://github.com/utkarsh5026/aws-analyzer/pull/124))
- Lambda: `how_it_works("orders-etl")` shows its graph and its steps in two tabs, **Graph** and **Step by step**,
  and the steps are now a walk you click through like a debugger: the code on the left, and on the right the step
  you're on, with the functions it's in, the loops and branches around it and what the line does. **Next** goes
  into the functions a line calls, **Over** past them and **Out** back to the caller, and a click on a line jumps to
  it. Before, each function was folded under the line that calls it, to open one at a time. The tabs come first in
  the report, with the numbers, sentences and findings under them. The same in the explorer's **How it works** and in
  `how_it_works_page()`, where the arrow keys step too.
  ([#125](https://github.com/utkarsh5026/aws-analyzer/pull/125))
- Lambda: code in Lambda's reports and explorer is drawn in Cascadia Mono when it's installed, else the system's
  monospace font as before.
  ([#125](https://github.com/utkarsh5026/aws-analyzer/pull/125))

### Removed

- Lambda: `how_it_works()` no longer ends with tables of its AWS calls and their IAM policy, the environment
  variables it reads, the event's fields and a test event, its own functions, its imports and its HTTP requests. The
  graph, the steps and the findings show what matters in them; `core.code_map(name)` still returns all of it as data
  (`.aws`, `.env`, `.event`, `.functions`, `.imports`).
  ([#125](https://github.com/utkarsh5026/aws-analyzer/pull/125))

## [0.25.0] - 2026-10-19

### Added

- Lambda: `how_it_works_page("orders-etl")` saves `how_it_works()` as a page of its own,
  `lambda-downloads/orders-etl-how-it-works.html` (or at `path=`), and shows a link that opens it, so a big package's
  graph and steps get a whole tab instead of a notebook cell. In JupyterLab a click opens it in a JupyterLab tab, and
  a middle-click (or right-click, **Open link in new tab**) in a browser tab. The explorer's Code tab has the same as
  **↗ Open in a tab**, beside **How it works**.
  ([#120](https://github.com/utkarsh5026/aws-analyzer/pull/120))
- Lambda: `how_it_works_page("orders-etl")` (and the explorer's **↗ Open in a tab**) draws the whole graph: every
  function the steps follow and every operation, variable and event field in each box, where `how_it_works()` in the
  notebook stops at 24 functions, 5 calls deep, and 5 lines a box. When the notebook's graph leaves something out, a
  note under it says what and points to the page. `code_graph(cmap, max_functions=None, max_depth=None,
  max_lines=None)` gives the whole graph in Python.
  ([#121](https://github.com/utkarsh5026/aws-analyzer/pull/121))
- **`set_theme()` picks how reports look**: `from aws_analyzer import set_theme; set_theme("soft")`. There are five
  themes, each made for a light or a dark notebook: `console` (boxed panels, WARN and INFO tags, numbers in a
  monospace font), `soft` (rounded tiles on a light fill), `editorial` (a serif title, large figures, almost no
  boxes), `brand` (the guide site's navy, indigo and amber highlighter) and `classic` (the look until now). Every
  report from then on uses it, and the ones already in the notebook keep theirs. The `AWS_ANALYZER_THEME` environment
  variable picks it for every notebook, and `set_theme(None)` goes back to it. The windows keep their own look, and
  text output is the same in every theme ([#122](https://github.com/utkarsh5026/aws-analyzer/pull/122)).

### Changed

- **Reports have a new look**: they're drawn in the `console` theme, with the title and findings in boxed panels,
  each card's tone as a coloured top edge and numbers in a monospace font. `set_theme("classic")` (or
  `AWS_ANALYZER_THEME=classic`) brings back the look until now ([#122](https://github.com/utkarsh5026/aws-analyzer/pull/122)).

## [0.24.0] - 2026-10-19

### Added

- **The agent's chat window picks its model by clicking**: **⚙ model** in its head opens the model settings, a list of
  providers (Amazon Bedrock, OpenAI, Azure OpenAI, Anthropic, Google Gemini, Ollama and every other provider
  LangChain knows) and a model box that suggests names (the region's Bedrock models, read when Bedrock is picked). It
  asks only for what that provider takes: a base URL or Azure endpoint, an API key (in a password box, never shown
  back; leave it empty to use the environment variable) and Azure's API version. Under them it says where what the
  agent sends would go (in AWS, or outside it) and how its tokens are priced. **Use this model** switches and starts
  a new conversation; a missing key, setting or package is said in the panel, and the agent keeps the model it had.
  When no model is set up, the window opens on it with Amazon Bedrock picked, which needs no key. From code,
  `AgentView().use_model('openai', 'gpt-4.1', api_key=...)` and `AgentAnalyzer.use_model(...)` do the same
  ([#118](https://github.com/utkarsh5026/aws-analyzer/pull/118)).

## [0.23.0] - 2026-10-19

### Added

- **`AgentView().chat()` opens a chat window for the agent**, drawn like a terminal: type a question (or click one to
  start from) and watch it work. What the model says before it acts, then each tool call as the code it is, with a
  coloured badge for every AWS service it reads (Lambda, CloudWatch Logs, CloudWatch, S3, ...), a comment saying what
  it does and what it found under it. A click on a call shows the AWS operations it used, the window it covered, the
  `LambdaView` / `S3View` call to look closer, and the exact data the model read, as JSON you can fold. Once it
  answers, the steps fold into one line naming the services they read, and the answer leads, with any findings, the
  model, time and cost, and **details** under it. Follow-ups continue the conversation, **stop** ends a question with
  what it found so far, and **new chat** starts over. `AgentAnalyzer.ask(on_step=...)` hands the same steps to your
  own code (`AgentStep`: the model's words, and each tool call as it starts and ends), and each `ToolCall` now keeps
  the AWS operations it reads (`.reads`, `.services`) and what it handed the model (`.result`)
  ([#115](https://github.com/utkarsh5026/aws-analyzer/pull/115)).

### Changed

- **`chat()`'s window looks like the agent's chat**: a monospace font on your notebook editor's colours, light or
  dark; your questions on the right, marked 🧑, and the answers on the left, marked 🤖, behind a thin rule; flat tabs,
  fields and buttons; and a header with just the knowledge base's name. Everything it does is the same. The agent's
  chat (`AgentView().chat()`) marks questions and answers the same way
  ([#116](https://github.com/utkarsh5026/aws-analyzer/pull/116)).
- **Searching a Lambda function's logs is several times faster**: the Lambda explorer's Logs search (Enter),
  `logs()`, `errors()`, `performance()` and `log_runs(search=...)` now ask CloudWatch Logs for up to four slices of
  the time range at once, newest first, instead of one after another, so a search over a day or a week comes back in
  a fraction of the time. They read the same lines, need the same permissions and cost nothing more
  ([#113](https://github.com/utkarsh5026/aws-analyzer/pull/113)).

## [0.22.0] - 2026-10-19

### Changed

- **`chat()`'s sources show each chunk's metadata**: open a source under an answer, or a passage of a Retrieve only
  search, and it lists the file's own metadata (its `<file>.metadata.json`, what `where=` filters on) and Bedrock's
  (chunk ID, data source ID, page), or says the file has none of its own. `ask()` and `retrieve()` reports add a
  Metadata column when a passage has some, and `Passage.bedrock_metadata` keeps Bedrock's keys
  ([#110](https://github.com/utkarsh5026/aws-analyzer/pull/110)).
- **`chat()`'s Answer / Retrieve only switch is at the top right**, beside **+ New chat**, instead of in the question
  box, and two small buttons there, **Chat** and **Settings**, hide or show the conversation and the side tabs, so
  either can have the window's whole width ([#111](https://github.com/utkarsh5026/aws-analyzer/pull/111)).

## [0.21.0] - 2026-10-19

### Added

- **The agent answers with the model you pick, not only Azure OpenAI**: `AgentView(provider="bedrock")` asks Claude
  Haiku 4.5 on Amazon Bedrock with the notebook's own AWS credentials, so nothing leaves AWS (`model="sonnet"`, a
  model ID or an inference profile for another; short names are found in the region's model list), and its answers
  show their cost at Bedrock's list prices without `model_prices=`. `AgentView(model="openai:gpt-4.1")` uses OpenAI
  (`endpoint=` points it at any server that speaks OpenAI's API: LiteLLM, vLLM, OpenRouter), and any provider
  LangChain supports works as `model="anthropic:claude-sonnet-4-5"`, `"google_genai:gemini-2.5-pro"` or
  `"ollama:llama3.1"`. Without `provider=`, the agent works it out from the model's name and the environment
  (`AWS_ANALYZER_AGENT_MODEL` sets one for every notebook), and Azure OpenAI set up as before keeps working
  unchanged. A failed model call says what it means for that provider (a Bedrock model that needs an inference
  profile, a missing key), and `tools()` shows the provider and where what the agent sends goes. The `[agent]` extra
  now installs langchain-aws too ([#102](https://github.com/utkarsh5026/aws-analyzer/pull/102)).

## [0.20.0] - 2026-10-18

### Added

- **Ask about your account in plain English**: `AgentView().ask("Which Lambda failed last night?")` answers from the
  package's own read-only tools, run by an agent (LangChain's deepagents) on your Azure OpenAI deployment. It finds
  which functions failed in your night (in `set_timezone()`'s zone, or `AgentView(timezone=...)`'s), why, from their
  logs, and the run that shows it,
  and every answer says the window and regions it checked, what it couldn't read, the AWS reads, tokens and cost, and
  the `LambdaView` commands to look closer. Follow-up questions continue the conversation; `transcript()` and
  `tools()` show the questions so far and what it can call. Nothing can change your account: a guard refuses any AWS
  call that isn't a read, and each question has a budget (tool calls, time, cost, a 3-day log window) that it stops
  at and says so. Version 1 covers Lambda. Install it with `pip install "aws-analyzer[agent]"` (Python 3.11 or newer;
  everything else still runs on 3.10 with boto3 alone) ([#87](https://github.com/utkarsh5026/aws-analyzer/pull/87)).

- The agent's tools come in **toolkits**, one per AWS service: `AgentView(toolkits=["lambda", Runbooks()])` adds a
  `Toolkit` of your own (from `aws_analyzer.agent`) beside the Lambda one, and its tools get the same budget, error
  handling, read-only guard and report. The agent guide's "Your own tools" has an example ([#88](https://github.com/utkarsh5026/aws-analyzer/pull/88)).

- **The agent covers S3 too, and the bucket events that invoke Lambda functions**:
  `AgentView().ask("Were last night's uploads to acme-raw processed?")` checks the bucket's event notifications, the
  files that arrived in the window and the function they should have run, and says what broke the link: files ending
  in `.CSV` when the filter says `.csv`, files uploaded in parts when the notification fires only on PUT uploads, a
  function that isn't there, a missing permission (with the `aws lambda add-permission` command), or no file arriving
  at all, next to the function's runs and failures in that window. It also answers about buckets on their own (their
  setup, size and cost, what's in a folder, which files arrived when, one file's headers), and never reads what's
  inside a file. `AgentView(toolkits=["s3"])` gives S3 alone, and `budget={"s3_keys": 500_000}` lets a listing read
  more than its default 100,000 keys ([#89](https://github.com/utkarsh5026/aws-analyzer/pull/89)).

- S3: `S3Analyzer().notifications("my-bucket")` lists a bucket's **event notifications**: which Lambda functions, SQS
  queues and SNS topics it sends which events to, for which keys, and whether EventBridge gets them all
  (`parse_notifications` does the same on a `GetBucketNotificationConfiguration` response). It needs
  `s3:GetBucketNotification` ([#89](https://github.com/utkarsh5026/aws-analyzer/pull/89)).

- `set_timezone("Asia/Kolkata")` (from `aws_analyzer`) shows **times in your own time zone** instead of UTC, in every
  report and window of every service, and their columns and sentences say which zone (`Last modified (IST)`,
  `since 2024-05-01 14:02 IST`). It takes a tz database name, its city alone (`"kolkata"`, `"new york"`), `"local"`
  for the machine's own zone, an offset (`"+05:30"`) or `"UTC"` to go back, and dates you type in
  (`since="2024-05-01 10:00"`) are read in the same zone. The `AWS_ANALYZER_TZ` environment variable picks it for every
  notebook, and `help()` says which zone times are in. CloudWatch's daily numbers stay in UTC days, as AWS adds them up ([#86](https://github.com/utkarsh5026/aws-analyzer/pull/86)).

## [0.19.0] - 2026-10-16

### Added

- Lambda: `how_it_works()`, and **How it works** in the explorer's Code tab, also draw **a graph of how the code fits
  together**, between the findings and the step-by-step outline. The event and environment variables come in on the
  left, the handler and its own functions are in the middle with an arrow for each call, and the AWS services and the
  web it reaches are on the right. Each arrow is coloured by what the call does and lands on the operation it calls, and
  the variable, list call or request a warning is about is marked on its line (not set, first page only, no timeout),
  with its arrow glowing amber. Pointing at a box leaves only what it's joined to in sight. The same graph comes as
  Mermaid to paste in a Markdown file, and `code_graph(cmap).mermaid()` gives it in Python
  ([#83](https://github.com/utkarsh5026/aws-analyzer/pull/83)).

## [0.18.0] - 2026-10-16

### Added

- SageMaker: `new_env("3.11")` makes a Python environment with the version you ask for and adds it to Jupyter's kernel
  menu as **Python 3.11 (py311)**, in a folder that's kept when the notebook stops (`~/SageMaker/envs` on a notebook
  instance, `~/.conda/envs` in Studio). It uses the first of mamba, conda, micromamba and uv on the machine (conda
  ones with conda-forge), and `packages="pandas boto3"` or `packages="-r requirements.txt"` installs what you list.
  Run it again after a notebook instance restarts and it only adds the kernel back; the report also has the lifecycle
  configuration that does that at every start. `dry_run=True` shows the commands instead, and a failed step says why
  (no internet access, a full disk, a version that doesn't exist) with the end of its output
  ([#81](https://github.com/utkarsh5026/aws-analyzer/pull/81)).
- SageMaker: `envs()` lists the Python environments and Jupyter kernels on the machine, with each one's Python version
  and whether it's kept when the notebook stops. It warns about environments that will be gone after the next stop,
  kernels whose Python isn't there any more, and environments that lost their kernel in a restart
  ([#81](https://github.com/utkarsh5026/aws-analyzer/pull/81)).
- Lambda: `how_it_works("orders-etl")` explains how a Python function's code works, read from its package with
  Python's `ast` module and never run. A few sentences sum it up. Then come each step of a call from the handler on,
  with the functions it calls in its own files folded under the lines that call them, and each line tagged with what
  it does: AWS calls, environment variables, event fields, HTTP requests, logs. After that come its AWS calls with the
  IAM permissions they need, the environment variables it reads and whether they're set, a test event with the fields
  it reads, and its imports and where Lambda finds them. Findings catch a variable it needs that isn't set, an import
  Lambda can't find, a handler that isn't there, HTTP requests without a timeout, list calls that read only their first
  page, errors it drops, files written outside `/tmp`, secrets written in the code, and AWS calls in a loop that a
  batch call would make in fewer requests. `map_code()` does the same on code you have on disk
  ([#82](https://github.com/utkarsh5026/aws-analyzer/pull/82)).
- Lambda explorer: **How it works** in the **Code** tab shows the same for a Python function, in place of the source
  ([#82](https://github.com/utkarsh5026/aws-analyzer/pull/82)).


## [0.17.0] - 2026-10-15

### Added

- Bedrock Knowledge Bases: `save_file_list()` saves the knowledge base's files as a `.txt` file, one per line and
  sorted by path, with a **⬇ Download** link that saves it on your computer. Each line is a file's `s3://` path, ready
  for a script, `aws s3 cp` or a spreadsheet; `link="https"` writes a link that opens each file in a browser instead
  (presigned, for an hour unless `expires=` says otherwise) and `link="key"` its path in the bucket. `status=`,
  `match=` and `data_source=` keep only the files `files()` lists with them, so `save_file_list(status="failed")` is
  the list of failed files
  ([#77](https://github.com/utkarsh5026/aws-analyzer/pull/77)).
- Bedrock Knowledge Bases explorer: **⬇ List** in the **Files** tab saves the `s3://` path of every file the list
  shows (with its search, state chip and data source, in its order), one per line, in `<knowledge base>-files.txt`
  beside the notebook, and puts a **⬇ Download** link for it under the list
  ([#77](https://github.com/utkarsh5026/aws-analyzer/pull/77)).
- Bedrock chat: `save_file_list()` does the same for the chat's knowledge base (`match=` keeps the files whose path
  contains it, as `files()` does), and **⬇ List** beside the **Files** list's search box saves the `s3://` paths of
  the files the list shows, with a **⬇ Download** link under the list. `BedrockChatAnalyzer.file_url()` makes the
  same presigned links as the knowledge base analyzer's
  ([#77](https://github.com/utkarsh5026/aws-analyzer/pull/77)).

## [0.16.1] - 2026-10-13

### Changed

- Lambda's `function_info()` and the explorer's **Settings** tab: the configuration as Lambda returns it is JSON in
  colour that folds, like S3's preview of a `.json` file, instead of plain text. Click an object or a list to fold or
  unfold it, and hover over a line to see its path in Python (`raw['Environment']['Variables']`)
  ([#74](https://github.com/utkarsh5026/aws-analyzer/pull/74)).
- Lambda's tables of run times (`overview()`'s Avg run, `metrics()`'s days, `performance()` and its runs) keep the
  exact milliseconds and say a second or more the way people read it too: `30,000 ms (30s)`, `185,000 ms (3m 05s)`
  ([#75](https://github.com/utkarsh5026/aws-analyzer/pull/75)).

### Fixed

- Lambda's tables of run times sort by the number when you click the column's header, so `1,530 ms` comes after
  `999 ms` instead of before it ([#75](https://github.com/utkarsh5026/aws-analyzer/pull/75)).

## [0.16.0] - 2026-10-13

### Changed

- Every service's reports: tables sort, filter and choose their columns, as S3's already did. Click a column's header
  to sort by it (largest, newest or A to Z first; again for the other way; a third time for the original order), pick
  a value in a column's **Filter** to see only those rows (a status, a runtime, a data source), and untick the columns
  you don't need under **Columns**. It's plain HTML and CSS, so it still works when you reopen the notebook, and the
  tables in the knowledge base and Lambda explorers and in the chat's Test and Runs tabs work the same way
  ([#70](https://github.com/utkarsh5026/aws-analyzer/pull/70)).
- Every service's reports: each finding leads with a bold headline that says what's wrong, with why it matters and
  what to do underneath as points, and a note starts with its point in bold, as S3's already did. So their headlines
  read right, a few findings say it in a new order: DynamoDB's idle capacity ("Most of the provisioned capacity goes
  unused: reads peaked at 4% ..."), Lambda's most common error ("The most common error happened 8 times (the last 44m
  ago): KeyError: 'customer_id'"), OpenSearch's k-NN memory ("The vector graphs don't fit in the k-NN plugin's
  memory: ...", "The vector graphs have little room to grow: ...") and the chat's sweeps ("3 questions didn't work
  with any setup: nothing tried here fixes them (...)")
  ([#70](https://github.com/utkarsh5026/aws-analyzer/pull/70)).

### Fixed

- `help(aws_analyzer.s3)` and the other modules' help: the quick start says to `pip install aws-analyzer` and import
  from `aws_analyzer`, where it still said to copy the file into a notebook cell
  ([#72](https://github.com/utkarsh5026/aws-analyzer/pull/72)).

## [0.15.0] - 2026-10-12

### Added

- `lambda_functions.py`: `download("orders-etl")` saves a function's deployment package, the .zip as Lambda stores it,
  in `lambda-downloads/` next to the notebook (or at `path=`, a file or a folder; `"name:alias"` saves that version, as
  `orders-etl-v3.zip`), and says where, its size and files, and whether its SHA-256 matches the one Lambda reports. In
  JupyterLab, right-click it in the file browser and pick Download to get it on your computer. A downloads folder it
  makes gets a `.gitignore`, since a package can hold secrets. The explorer's Code tab has the same as a
  **Download .zip** button, which saves the package the tab already read. ([#66](https://github.com/utkarsh5026/aws-analyzer/pull/66))

### Changed

- `lambda_functions.py`: the explorer's Code tab shows the package's files as a tree of folders instead of a flat
  list: folders first, with their sizes, the handler's folder open, and a folder that holds only a folder on one line
  (`botocore/data/`). Click a folder to open or fold it; **Find a file** shows the files that match inside their
  folders. Beside the open file's name, **Copy** selects all of its source, so Ctrl+C (⌘C on a Mac) copies the file.
  ([#66](https://github.com/utkarsh5026/aws-analyzer/pull/66))
- `lambda_functions.py`: the explorer's Code tab has a **Hide files** button that folds the file tree away, so the source
  has the whole width; **Show files** brings it back. It stays hidden for the next function you open.
  ([#67](https://github.com/utkarsh5026/aws-analyzer/pull/67))

## [0.14.0] - 2026-10-12

### Changed

- **Breaking**: the analyzers no longer work as single files copied next to a notebook, since they now share code in
  `aws_analyzer._kit`. Install the package instead, `%pip install aws-analyzer` (a notebook without internet can
  install it from a bucket: [Get started](https://github.com/utkarsh5026/aws-analyzer#get-started) shows how), and
  import from it: `from aws_analyzer import S3View` instead of `from s3 import S3View`, and
  `from aws_analyzer.s3 import explain_policy` for anything else a file had. Notebooks that already use the package
  need no change, and a file you copied before keeps working as it is. The files also moved in the repository, from
  `analyzers/` to `src/aws_analyzer/`. ([#61](https://github.com/utkarsh5026/aws-analyzer/pull/61),
  [#62](https://github.com/utkarsh5026/aws-analyzer/pull/62))

## [0.13.0] - 2026-10-11

### Added

- `lambda_functions.py`: `explore()` (or `ui.explore()`; installed with pip, `LambdaExplorer`) opens a window on your
  Lambda functions that you use by clicking, with nothing to type but a search. Its Functions tab lists every function
  in the region (or every region) with its runtime, calls, error rate, run time, cost and warnings, the ones that need
  attention first, to search, filter and sort. Click one for its findings, a diagram of what calls it and where its
  results, failed events and logs go, and its calls and run time a day. Its Logs tab reads the logs **run by run**:
  each call on one line with its status, run time against the timeout, memory and cold start, the failed ones in red,
  and every line it logged a click away (JSON logs as their message, an error as a traceback). Pick the time range,
  find runs with some text as you type, press Enter to have CloudWatch search the whole range and bring each run back
  whole, or turn on Live to see new runs as they come while you test. The Errors, Performance, Code and Settings tabs
  are `errors()`, `performance()`, `code()` and `function_info()`'s settings, by clicking: a failed or slow run opens in
  the Logs tab, and a package's files open with their source in colour. Like the rest of the file it only reads, with
  the permissions the commands already use. ([#59](https://github.com/utkarsh5026/aws-analyzer/pull/59))
- `lambda_functions.py`: `ui.core.log_runs("orders-etl", since="24h")` returns what a function logged as runs: `.runs`
  is a `LogRun` per call with its lines, status, run time, memory and cold start. `search=` keeps the runs with a line
  that matches (an order ID, a request ID or a filter pattern), each with all of its lines. `split_runs()` does the
  same for log lines you already have, `line_level()` says what kind of line one is (error, warning, Lambda's own...),
  and `read_package()` reads a deployment package .zip you already have, without AWS.
  ([#59](https://github.com/utkarsh5026/aws-analyzer/pull/59))

## [0.12.0] - 2026-10-09

### Added

- `bedrock_kb.py`: every file name in `search`, `ask`, `follow_up`, `chunk`, `compare`, `documents` and `unsynced` is
  now a link (↗) that opens the file in a new browser tab, a PDF at the passage's page, so you can check a passage
  in its document with one click. S3 files get a presigned link, signed in the notebook with no AWS call, that works
  for an hour and needs `s3:GetObject` on the file; web, Confluence, SharePoint and Salesforce sources link to their
  page. `ui.link(2)` gives the link on its own (also in text mode), and takes a file name, an `s3://` path and
  `expires=` (up to 7 days). ([#55](https://github.com/utkarsh5026/aws-analyzer/pull/55))
- `bedrock_kb.py`: `explore()` (or `ui.explore()`; installed with pip, `KBExplorer`) opens a window on a knowledge
  base that you use by clicking, with nothing to type but a question. Its Files tab puts every file of the knowledge
  base's S3 data sources next to Bedrock's record of it: failed and why, changed in S3 since it was indexed, added
  after the last sync, skipped by the sync (a type Bedrock doesn't read, over 50 MB, archived), gone from S3 but still
  in answers. Click one to see how it was indexed: each step from S3 into the vector store, its chunks in document
  order with the text each shares with the one before, its `.metadata.json` next to what its chunks carry, and what to
  fix; ask it a question to see whether it holds the answer, and whether it ranks high enough for an answer to see it.
  The Overview, Syncs (a timeline), Search and Settings tabs are a click away, and like the rest of the file it only
  reads: where a sync would help, it shows the command. Besides what `documents()` and `unsynced()` use, it needs
  `s3:GetObject` to read metadata files, and `bedrock:GetKnowledgeBaseDocuments` for files past the first 10,000.
  ([#57](https://github.com/utkarsh5026/aws-analyzer/pull/57))
- `bedrock_kb.py`: `ui.files()`, `ui.file("refund-policy.pdf")` and `ui.search_file("refund-policy.pdf", "a
  question")` show the same as reports: every file and its state (`status="failed"` or `match="refund"` narrows the
  list), how one file was indexed, and where a question ranks it. `ui.core.file_inventory()`, `document_chunks()`,
  `metadata_file()` and `probe_file()` return the data behind them.
  ([#57](https://github.com/utkarsh5026/aws-analyzer/pull/57))

### Changed

- `bedrock_chat.py`: the chat window now opens with a two-line greeting instead of a page of instructions that
  filled the conversation before you'd asked anything. The tour of the window is still there, shorter, folded under
  **How this window works**. ([#56](https://github.com/utkarsh5026/aws-analyzer/pull/56))

## [0.11.0] - 2026-10-09

### Added

- `bedrock_chat.py`: **Try variations**, in the chat window's Test tab, and `ui.sweep(...)`, ask your test questions
  with every combination of the settings, models, data sources or files you're unsure of, instead of changing one and
  running the list again: `ui.sweep(n=[5, 10], search_type=["SEMANTIC", "HYBRID"], model=["haiku", "sonnet"])`, or a
  line per setting in the window (`n = 5, 10`), with one-click lines for the common ones. The setups come back ranked
  (expected files cited, then answers, then grounded share, the cheaper first when as good), with findings that say
  which setup beats yours and for how much, what each setting changed ("temperature made no difference"), when a lead
  is small enough to be chance, and which questions no setup could answer, plus how each question did with each setup.
  `ui.use_run()` (or **Use this setup**) switches to the best one. A sweep is estimated before it's sent and isn't sent
  over $2 (`max_cost=`; in the window, a second click) or 16 setups (`max_setups=`); with `retrieve_only=True` it costs
  only the questions' embeddings. ([#51](https://github.com/utkarsh5026/aws-analyzer/pull/51))
- `bedrock_chat.py`: **Runs**, a new tab in the chat window, and `ui.runs()` list every test run, newest first, each
  ranked against the other runs of the same questions, so you can see what each change got you; `ui.compare_runs(2, 5)`
  puts runs side by side, question by question, and `ui.use_run(5)` switches to a run's setup. **Save** (or
  `ui.save_runs()`) keeps the runs in `kb-test-runs.jsonl` beside the notebook, a JSON line per run with each answer and
  its sources, and adds every later run as it finishes; **Load** (`ui.load_runs()`) reads them back after a kernel
  restart, or from a teammate. `chat(..., log="kb-test-runs.jsonl")` saves from the start, and `ask_all(label=...)`
  names a run. ([#51](https://github.com/utkarsh5026/aws-analyzer/pull/51))

### Changed

- **Breaking**: `bedrock_chat.py`: `ui.results(n)` counts test runs from 1, the way the Runs tab, `ui.runs()`,
  `ui.use_run()` and `ui.compare_runs()` number them, so `ui.results(1)` is now the first run (it was
  `ui.results(0)`, which now says how the runs are numbered). `ui.results()` still shows the last run.
  ([#51](https://github.com/utkarsh5026/aws-analyzer/pull/51))
- `bedrock_chat.py`: the chat window's side tabs stay on one row at any width instead of wrapping onto a second
  line. The emoji before their names are now small line icons that follow the theme (shown when there's room),
  **Request JSON** and **Last response** are now **Request** and **Response**, and the open tab stands out in dark
  themes too. ([#51](https://github.com/utkarsh5026/aws-analyzer/pull/51))

## [0.10.0] - 2026-10-09

### Added

- `s3.py`: `ui.downloads()` shows what you've downloaded: each file, folder and zip in the downloads folder with its
  size and when it was downloaded, how much of the disk they take, and findings for zips a stopped `download_zip()`
  left unfinished, a disk running out of room and downloads over a month old. `ui.clean_downloads()` deletes them to
  free the disk: all of them, the ones named (`ui.clean_downloads("churn.zip")`), or those downloaded before
  `older_than="7d"`; `dry_run=True` shows what would go. It only empties a folder made for downloads, never the
  notebook's own folder or one that held your files first, and nothing in S3 changes. ([#50](https://github.com/utkarsh5026/aws-analyzer/pull/50))

### Changed

- **Breaking**: `s3.py` and `s3_explorer.py`: `download()`, `download_zip()`, and the explorer's **⬇ Download** and
  zips, save into one folder, `s3-downloads` next to the notebook, when they're given no path, instead of the
  notebook's own folder, so downloads no longer mix with your notebooks and code. The folder gets a `.gitignore` that
  keeps it out of git. A notebook that reads a downloaded file by its bare name (`pd.read_csv("events.csv")`) needs
  `s3-downloads/events.csv`, or `S3View(downloads=".")` for the old place. `S3View(downloads="~/scratch/s3")`,
  `S3Analyzer(downloads=...)` and `S3Explorer(downloads=...)` pick another folder; the explorer's **⚙** edits it as
  **Downloads in**, and `x.downloads` replaces `x.zip_folder` (which still works). ([#50](https://github.com/utkarsh5026/aws-analyzer/pull/50))

## [0.9.0] - 2026-10-09

### Added

- `bedrock_chat.py`: **Answer / Retrieve only**, beside the chat window's question box. On **Retrieve only**, a
  question only searches: the same search an answer makes (Retrieve, with the same passages, search type, filter,
  reranker, data source and files) and no model, showing every passage found, best first, with its score. Switching
  puts your last question back in the box, so you can ask it both ways: the search tags the passages the answer
  cited, and a finding says when the answer was "unable to assist" although the search found passages, so you can
  tell a retrieval problem from an answer problem. From code: `ui.retrieve("How long do refunds take?")`,
  `chat(..., retrieve_only=True)` and `ui.request(retrieve_only=True)`. It needs only `bedrock:Retrieve`. ([#41](https://github.com/utkarsh5026/aws-analyzer/pull/41))
- `bedrock_chat.py`: `ui.kbs("K7QJ")` finds knowledge bases by name, ID (or part of one), ARN or description, best
  match first, and suggests the name you may have meant when nothing matches. ([#42](https://github.com/utkarsh5026/aws-analyzer/pull/42))
- `s3_explorer.py`: **📄 Text** on a PDF, beside **📖 Read all**: the PDF's words, 50 pages at a time, laid out to read
  like a web page (headings from the font sizes, paragraphs joined back up from the PDF's lines, bullet and numbered
  lists, a thin line where each page starts), without the running headers, footers and page numbers repeated on every
  page. **📖 Read all** still shows the pages as they look. ([#43](https://github.com/utkarsh5026/aws-analyzer/pull/43))
- `s3.py`: `pdf_flow(doc)` turns a PDF read with `read_pdf(uri, layout=True)` (or `parse_pdf(..., layout=True)`) into
  headings, paragraphs and list items, ready to read or to send to a model, and `pdf_furniture(doc)` lists the running
  headers and footers it leaves out. `doc.layout` holds each page's lines with where they sit, their size and weight. ([#43](https://github.com/utkarsh5026/aws-analyzer/pull/43))
- `bedrock_chat.py`: **🧪 Test** in the chat window, and `ui.ask_all(questions)`, ask a whole list of test questions
  with the setup you've built (knowledge base, model, data source, files and settings), each on its own rather than as
  a follow-up, a few at a time while the window stays usable (**Stop** sends no more). Each question gets a line
  saying how it did (answered, "unable to assist", no citations, partly grounded, failed) with its grounded share,
  sources and time, and opens to the full answer; `How long do refunds take? | refund-policy.pdf` also checks that
  the answer cites that file. Findings sum up the run and say which setting to try, and after a change, running the
  list again (`ui.ask_all()`) says which questions did better or worse. The estimated cost shows before you run.
  `ui.results()` shows a run again as a report, `ui.batches[-1].to_df()` gives one row per question, and
  `chat(..., questions=[...])` opens the window with the list ready. It needs no new permissions. ([#45](https://github.com/utkarsh5026/aws-analyzer/pull/45))
- `bedrock_chat.py`: **📋 Code** in the chat window, and `ui.code()`, give the setup as it is now, to run anywhere: a
  Python script that needs only boto3 and asks your test questions, printing each answer with the files it cites; the
  config as JSON (the request without the question, which `client.retrieve_and_generate(input=..., **config)` or the
  AWS CLI's `--cli-input-json` sends); and the AWS CLI command for one question. It follows every change, and warns
  when Bedrock would refuse the setup. ([#45](https://github.com/utkarsh5026/aws-analyzer/pull/45))

### Changed

- `s3_explorer.py` and `bedrock_chat.py`: the S3 explorer and the chat window fill the browser window's height
  instead of a fixed 560 and 540 pixels, so a big screen shows more files, more of a report and more of the
  conversation. They stay at least that tall, and keep those heights in VS Code. `S3Explorer(height=720)` and the new
  `chat(height=800)` set a height of your own, in pixels or as CSS (`"80vh"`). ([#48](https://github.com/utkarsh5026/aws-analyzer/pull/48))
- `s3_explorer.py`: **↗ Open in new tab** replaces **🔗 Link** above a file. One click opens the file in a new browser
  tab, where PDFs, pictures, sound, video and text files show instead of downloading, even when they were stored as a
  generic type (a CSV or log file shows as text). Other files download to your computer. Right-click it to copy the
  link (it works for an hour); `x.ui.link(path)` still shows a download link. `S3Analyzer.presigned_url(uri,
  inline=True)` makes the same kind of link from code. ([#46](https://github.com/utkarsh5026/aws-analyzer/pull/46))
- `bedrock_chat.py` and `bedrock_kb.py`: `chat()`, `ask()` and `generate()` use Claude Haiku 4.5 when you don't
  pick a model, instead of Claude Opus 5: answers come faster and cost a fifth as much ($1.10 / $5.50 per 1M tokens
  in us-east-1). To keep Opus, pass `model="opus"`, or set it once with `BedrockChatAnalyzer(default_model="opus")`
  / `BedrockKBAnalyzer(default_model="opus")`. ([#44](https://github.com/utkarsh5026/aws-analyzer/pull/44))
- `bedrock_chat.py`: the chat window's knowledge base, model, data source and files pickers are searchable lists
  instead of drop-downs. Click a field to open its list: each knowledge base shows its ID, a status dot, its
  description and when it changed, and each model its ID, provider and price. Type part of a name, an ID, a
  description or a provider to narrow the list (what matched is highlighted), and Enter picks the first. A knowledge
  base ID or ARN pasted whole works even when it isn't listed, or when the role can't list knowledge bases. Files are
  ticked in the same kind of list. A click anywhere else in the window closes an open list. ([#42](https://github.com/utkarsh5026/aws-analyzer/pull/42),
  [#47](https://github.com/utkarsh5026/aws-analyzer/pull/47))
- `s3.py`: `document()` shows a PDF's text laid out to read, the way **📄 Text** does, instead of each page's lines as
  `pypdf` reads them. A note says which running headers and footers it left out, and with `pictures=False` another
  gives the call that draws the pages without text. `ui.core.read_document(uri).text` still has every line. ([#43](https://github.com/utkarsh5026/aws-analyzer/pull/43))

## [0.8.0] - 2026-10-08

### Added

- `bedrock_kb.py`: `ask()`, `search()`, `compare()` and `evaluate()` take `data_source=`, so a question can be answered
  from one of a knowledge base's data sources only: `ui.ask("How long do refunds take?", data_source="faq")`, by
  name (any case) or ID, or a list of them. `follow_up()` keeps it, and `follow_up(..., data_source="policies")` moves
  the conversation to another one (`"all"` back to every one). It filters on the data source ID Bedrock gives every
  chunk, so no metadata files are needed, and it works with `where=`. `kb_info()` shows the `data_source=` for each
  data source, answers and searches whose passages come from several data sources say which each came from, and
  a finding says when a vector store returned passages from outside the one asked for. ([#39](https://github.com/utkarsh5026/aws-analyzer/pull/39))
- `bedrock_chat.py`: a **Data source** picker beside the knowledge base, shown when it has more than one, points the
  next questions at one data source without ending the conversation. From code: `chat("support-docs",
  data_source="faq")` or `ui.use(data_source="faq")`. **Edit JSON** reads it back from the request's filter. It needs
  `bedrock:ListDataSources` (by ID it works without). ([#39](https://github.com/utkarsh5026/aws-analyzer/pull/39))
- `bedrock_chat.py`: **📄 Pick files** in the chat window lists the knowledge base's indexed files; type part of a
  name and pick one or several, and the next questions search only those files (click a chip to drop one, **All
  files** to search everything again). `ui.files()` lists them as a report, with failed ones marked, and
  `ui.use(files=["refund-policy.pdf", "faq/returns.md"])` or `chat(..., files=[...])` picks them from code, by name,
  path or `s3://` path. It needs `bedrock:ListKnowledgeBaseDocuments` (by `s3://` path it works without). ([#39](https://github.com/utkarsh5026/aws-analyzer/pull/39))

## [0.7.0] - 2026-10-06

### Added

- `lambda_functions.py` (`LambdaView`): AWS Lambda functions, in one region or every region your account has turned
  on (`functions(regions="all")`). `functions()` lists each function with its runtime and when that loses support,
  memory, timeout, what triggers it, its calls, error rate and run time over the last 30 days, and its estimated
  monthly cost, with warnings for runtimes AWS no longer patches, functions anyone can call, provisioned concurrency
  that sits idle, errors that are frequent or rising, throttles and runs close to the timeout.
  `function_info("orders-etl")` explains one function in plain English: what triggers it and who may call it, what
  happens to failed events, what it can reach (environment variable names; their values are never shown), versions,
  aliases and provisioned concurrency, and its last 30 days day by day. `errors()` groups the errors in its logs by
  cause (timeouts, running out of memory, code that can't load, permissions its role lacks) with the run to look at,
  `logs()` shows the newest lines or one run from start to end, `performance()` reads run times, memory used and cold
  starts and suggests the memory size that would do, and `code()` lists the files in its deployment package and shows
  the handler's source. Nothing is invoked or changed: where a change would help, the report shows the AWS CLI
  command. With the package: `from aws_analyzer import LambdaView`. Guide:
  [Lambda functions](https://utkarsh5026.github.io/aws-analyzer/lambda_functions.html).
  ([#35](https://github.com/utkarsh5026/aws-analyzer/pull/35))
- S3 explorer: **▾ Expand all**, above a JSON file's preview (beside **✕**), opens every object and array in the tree
  at once, so you no longer click each one open. It stays on for the next JSON files you open; click it again to
  collapse them back to the first levels. ([#34](https://github.com/utkarsh5026/aws-analyzer/pull/34))

## [0.6.0] - 2026-10-05

### Added

- `opensearch.py` (`OpenSearchView`): OpenSearch vector (k-NN) indexes, in OpenSearch Service domains, Serverless
  collections, or any OpenSearch by URL. `overview()` lists every domain and collection with what it runs on, the
  memory its nodes have for vector graphs, its estimated monthly cost and what Serverless bills even when idle.
  `indexes("vectors-prod")` shows each index's vector fields (dimensions, engine, similarity), how many documents have
  a vector, and whether the graphs fit in the memory the nodes have. `index_info("vectors-prod/docs")` explains one
  index in plain English, with what a score means, what on-disk mode or fp16 would save, the fields you can filter on
  and the k-NN query to copy. `sample()` checks the vectors for zeros, repeats and odd lengths, and `search()` finds
  the documents nearest a question (embedded with a Bedrock model, or your own function), a vector, or an existing
  document (`like="doc-id"`), with `where=` filters. `use("vectors-prod/docs")` sets the index later commands use.
  No OpenSearch client library is needed: requests are signed with your AWS credentials, and only reads and searches
  are sent. With the package: `from aws_analyzer import OpenSearchView`. Guide:
  [OpenSearch vector indexes](https://utkarsh5026.github.io/aws-analyzer/opensearch.html).
  ([#32](https://github.com/utkarsh5026/aws-analyzer/pull/32))

## [0.5.0] - 2026-10-05

### Added

- `S3Navigator.list_rest()` lists the rest of a folder from your own code, one request per 1,000 entries, up to
  `list_limit` of them (10,000, set with `S3Navigator(list_limit=...)` or `x.nav.list_limit`); `more()` goes on from
  there. ([#30](https://github.com/utkarsh5026/aws-analyzer/pull/30))

### Changed

- `chat()` window: the Settings tab shows what's sent and little else. Each setting is one line: its name, its value
  (beside the name for numbers, lists and the slider), and what the value means; hover the name for what it does,
  what it takes and where it goes in the request, which used to be printed under every value. **Add a setting** is
  folded behind a **+ Add a setting** button, with the same one-click chips, search and Browse all inside, and ✕
  folds it away again. **Open this setup again** is folded at the bottom. The side uses smaller type throughout, and
  the Request JSON and Last response tabs do too. ([#29](https://github.com/utkarsh5026/aws-analyzer/pull/29))
- `S3Explorer` searches the whole folder, not only the first 1,000 entries S3 returns. A big folder shows its first
  page at once and lists the rest in the background, up to 10,000 entries, while you click, sort and search; each
  page updates the list, the counts, the type chips and your search, and the bar at the bottom says **Listing…**. So
  the 2,500th file is found by typing part of its name, and **Size** sorts the whole folder. Past 10,000 entries,
  **Load more from S3** lists the next 10,000 (`x.nav.list_limit` changes how many), and **Look up** asks S3 for a
  name you type the start of; `x.filter("name")` from code does that by itself, and the text view says how to list
  more. Opening a file by its path finds it however far down its folder it is.
  ([#30](https://github.com/utkarsh5026/aws-analyzer/pull/30))
- `S3Explorer`'s list shows 100 rows a page with « ‹ › » under it, which say which rows these are
  (`2,401–2,500 of 3,000`), in place of **Show more**, which added 100 rows a click and slowed the list down. Typing
  in the search box is quicker in big folders too: the list is sorted once, and each key only filters it.
  ([#30](https://github.com/utkarsh5026/aws-analyzer/pull/30))

## [0.4.0] - 2026-10-04

### Added

- `S3Explorer` finds files: type part of a name, or a file type such as `.csv`, in the search box over the list
  (`.csv .json` finds either, `.csv` also finds `.csv.gz` and `.jpg` finds `.jpeg`), or click one of the chips under
  it, one per file type in the folder with how many files have it. **Folders** and **Files** show only one kind, and
  stay on as you open other folders. **Include subfolders** lists everything below the folder, 10,000 files at a
  time, each under the folder it's in, so the search, the chips and the sort cover all of it: every Parquet file in a
  dataset's partitions, or the biggest files anywhere below. When nothing matches, the note under the list offers the
  fix (**Search the subfolders too**, **Show the 25 files**). `x.filter(".parquet", subfolders=True)` does the same
  from code, and in the text view. For your own code, `S3Navigator.below()` lists everything below a folder, and
  `parse_filter`, `filter_entries(kind=...)` and `count_types` do the matching and counting.
  ([#27](https://github.com/utkarsh5026/aws-analyzer/pull/27))
- `S3Explorer`: tick files to download them together. A checkbox shows when you point at a row (and on every row
  once something is ticked); the one in the header ticks everything listed, such as every `.csv` a search found. The
  bar under the list says how many are ticked and how big they are, and **⬇ Download selected** shows what goes in
  the zip, checks it's within the limits, and suggests a name from the folder and the count (`churn-12-files.zip`),
  which you can change. It never replaces a file already there. `x.picked` lists what's ticked.
  ([#27](https://github.com/utkarsh5026/aws-analyzer/pull/27))
- `S3View.download_zip()` and `S3Analyzer.download_zip()` / `plan_zip()` take a list of files and folders from one
  bucket too: `ui.download_zip(["s3://b/raw/a.csv", "s3://b/raw/2024/"])` zips them together, laid out as they are
  under the folder they share, after the same checks, and names the zip after that folder (`raw-2-items.zip`).
  ([#27](https://github.com/utkarsh5026/aws-analyzer/pull/27))
- `chat()` window, and `ask()` / `transcript()` in `BedrockChatView` and `BedrockKBView`: answers written in markdown
  are laid out, with headings, bullet and numbered lists, bold and italic, tables, code blocks and links, and each
  cited span still shaded and numbered. Nothing in an answer runs as HTML, and its links open only web pages and email
  addresses. In a terminal the markdown is printed as written, with code blocks and tables left unwrapped.
  ([#23](https://github.com/utkarsh5026/aws-analyzer/pull/23))
- `chat()` window: **Add a setting** searches every RetrieveAndGenerate field by its name, its path or what it does
  (`rerank`, `latency`, `encrypts`), lists the matches with what each one takes and does, and adds one with **+ Add**
  (Enter adds the best match). A misspelt name lists the closest ones, and **Browse all** lists every field by group.
  ([#23](https://github.com/utkarsh5026/aws-analyzer/pull/23))
- `chat()` window: the **Python** view of the request, and **Open this setup again**, are highlighted like code, and
  so is "The same call in Python" in `request()` and `last()`. The **JSON** view is in colour too.
  `python_call(params, region, width=)` breaks lines at `width`, counting each key.
  ([#23](https://github.com/utkarsh5026/aws-analyzer/pull/23))
- A guide to the S3 explorer, [S3 file explorer](https://utkarsh5026.github.io/aws-analyzer/s3_explorer.html):
  putting it next to a notebook, browsing, previewing, downloading a folder as a `.zip`, using it from code or
  without widgets, and the permissions it needs.
  ([#25](https://github.com/utkarsh5026/aws-analyzer/pull/25))

### Changed

- `S3Explorer` has a new look: drawn icons instead of arrow characters, the back, forward, up and refresh buttons
  grouped, softer rows, buttons and chips, the buttons above a report stay in view as it scrolls, and a spinner and
  the outline of a report while one loads. Long bucket names get the room the size column used to take.
  ([#27](https://github.com/utkarsh5026/aws-analyzer/pull/27))
- `S3Explorer`'s filter box is now the search box at the top of the list. `*.csv` there also finds `.csv.gz` files,
  and words separated by spaces must each be in the name (`churn train`), not the whole phrase.
  ([#27](https://github.com/utkarsh5026/aws-analyzer/pull/27))
- `S3View.preview()` (and the S3 explorer) shows a JSON file as a tree coloured like code instead of a wall of text.
  Click a line to fold or unfold an object, array or long string, and hover over a line for the Python that reaches
  it (`data['Records'][0]`). It starts with as much open as fits on a screen, with the first of a long list of
  records open as a sample. Short arrays sit on one line, and long ones (an embedding) wrap like words. A string that
  holds JSON (an SNS message, SageMaker hyperparameters) shows as the JSON inside it. Cards give the number of keys or
  items and how deep the file nests. A `.json` file bigger than the 512 KB preview window now shows its start as a
  tree (or a table of its first records) instead of raw text.
  ([#24](https://github.com/utkarsh5026/aws-analyzer/pull/24))
- Every report's title chip starts with an icon for its service (🪣 S3, 🗄️ DynamoDB, 📚 Bedrock KB, 💬 Bedrock chat,
  🧪 SageMaker), so in a notebook that mixes them you can tell at a glance where each report came from. `help()`
  gives each group of commands an icon too (💰 Cut cost, 🔎 Search and answer, 💻 This notebook, ...).
  ([#26](https://github.com/utkarsh5026/aws-analyzer/pull/26))
- `S3View`'s tables of keys (`ls`, `find`, `largest`, `duplicates`, `compare`, `versions`, `deleted`, `uploads`,
  ...) show each file's type as an icon in front of its key, the same icons the S3 explorer uses: 📊 tables,
  📕 PDFs, 🖼️ pictures, 🧠 models, 📁 folders. It's only drawn in the notebook: the cell still sorts and copies as
  the key, and text output is unchanged.
  ([#26](https://github.com/utkarsh5026/aws-analyzer/pull/26))
- `S3Explorer`: every button above the right pane has an icon, as ⬇ Download and 🔗 Link already did: 👁️ Preview,
  🏷️ Details, 📖 Read all, 📊 What's in here, 🛡️ Bucket settings and 🪣 Every bucket.
  ([#26](https://github.com/utkarsh5026/aws-analyzer/pull/26))
- `SageMakerView.running()` starts each row with an icon for what's billing (📓 notebook instance, 🧪 Studio app,
  🚀 endpoint, 🏋️ training job, ⚙️ processing job), and marks the notebook you're in with 📍 instead of
  "(this notebook)".
  ([#26](https://github.com/utkarsh5026/aws-analyzer/pull/26))
- `chat()`: the window's tabs are ⚙️ Settings, 🧾 Request JSON and 📨 Last response, each answer's cited passages
  sit under 📎 Sources, and your own questions get a 🧑 beside them, as the model's answers have their ✦.
  ([#26](https://github.com/utkarsh5026/aws-analyzer/pull/26))
- `chat()` window: a new look, with rounded corners throughout. The conversation reads like a chat (your questions on
  the right, each answer as a card with the model's name over its time and cost, sources as numbered rows), the box
  you type in sits in one rounded bar with **Send**, each setting is a card, and the tabs and view buttons are
  segmented controls. It follows JupyterLab's light and dark themes. The request's **Text** view is now called
  **JSON**.
  ([#23](https://github.com/utkarsh5026/aws-analyzer/pull/23))

### Fixed

- Text output (outside Jupyter, or `mode="text"`) keeps its tables' columns lined up when a cell holds an emoji or
  wide characters such as Chinese or Japanese text, in every analyzer. They used to push the rest of their row one
  column right per character.
  ([#26](https://github.com/utkarsh5026/aws-analyzer/pull/26))
- `chat()` window, **Request JSON**: with **Edit JSON** open, changing a setting in the Settings tab (or from another
  cell) and then pressing **Apply** silently undid that change. Now an editor you haven't touched takes the new
  request, and one you have keeps your edits and says what Apply would undo, with **Start over** to load the request
  as it is now.
  ([#23](https://github.com/utkarsh5026/aws-analyzer/pull/23))
- `chat()` window, **Request JSON**: while **Edit JSON** was open, **Tree**, **Text** and **Python** did nothing when
  clicked. They now wait, greyed out, until you Apply or Cancel.
  ([#23](https://github.com/utkarsh5026/aws-analyzer/pull/23))
- `chat()` window: the three tabs shared one scroll position, so after scrolling down the settings, **Request JSON**
  opened scrolled past its buttons. Each tab now scrolls on its own.
  ([#23](https://github.com/utkarsh5026/aws-analyzer/pull/23))
- `chat()` window: the JSON text and Python views wrapped long lines in the middle of a word; they now scroll sideways,
  and the Python breaks its lines to fit the tab.
  ([#23](https://github.com/utkarsh5026/aws-analyzer/pull/23))

## [0.3.0] - 2026-10-04

### Added

- `S3View` tables: click a column's header to sort by it (largest, newest or A to Z first, again for the other way, a
  third time for the original order), pick a value in a column's **Filter** to see only those rows (a storage class,
  a region, a bucket), and untick columns under **Columns** to hide the ones you don't need. It's plain HTML and CSS,
  so it still works after the notebook is saved and reopened.
  ([#21](https://github.com/utkarsh5026/aws-analyzer/pull/21))

### Changed

- `S3View`: long keys in tables keep the file name in view. The folder is dimmed and shortened from the left, and the
  whole key shows when you hover over it; in text mode the start of the folder goes, never the file name.
  ([#21](https://github.com/utkarsh5026/aws-analyzer/pull/21))
- `S3View` findings lead with a bold headline, with why it matters and what to do underneath as points, and amounts
  of money stand out. Notes start with their point in bold.
  ([#21](https://github.com/utkarsh5026/aws-analyzer/pull/21))

## [0.2.0] - 2026-10-04

### Added

- `S3Explorer`: **Read all** on a PDF shows its pages as they look, 20 at a time, with buttons under the report for
  the pages before and after, and each page's text folded underneath.
  ([#18](https://github.com/utkarsh5026/aws-analyzer/pull/18))
- A click on a drawn PDF page shows it as big as the notebook, with **‹** **›** to step through the pages and **✕**
  to go back: in `S3Explorer`, and in `S3View.preview()` and `document()`. It's plain HTML and CSS, so it still
  works after the notebook is saved and reopened. ([#18](https://github.com/utkarsh5026/aws-analyzer/pull/18))
- `S3Explorer`: **⬇ Download .zip** saves a folder as one `.zip` next to the notebook, after the same disk-space and
  read-access checks as `S3View.download_zip()`, up to 100 MB and 10,000 files. The **⚙** settings panel changes
  those limits and the folder the zips go to, and so does `S3Explorer(zip_max_size="2GB")`.
  ([#18](https://github.com/utkarsh5026/aws-analyzer/pull/18))

### Changed

- `S3Explorer`: clicking through files no longer waits for each one to load. In a notebook, previews load in the
  background, the file you clicked is highlighted at once, and files you clicked past are skipped.
  ([#18](https://github.com/utkarsh5026/aws-analyzer/pull/18))

## [0.1.0] - 2026-10-04

The first release on PyPI: `pip install aws-analyzer` (or `"aws-analyzer[all]"` for pandas, the file readers and the
notebook extras), then `from aws_analyzer import S3View`. Each analyzer still works on its own, as one file next to a
notebook with only boto3.

### Added

- **Amazon S3** (`s3.py`, `S3View`): every bucket's size, estimated monthly cost and security settings (`overview`,
  `bucket_info`, and `policy` in plain English); folders added up and searched (`ls`, `tree`, `summary`, `find`,
  `largest`, `compare`); savings from `duplicates`, lifecycle rules (`what_if`) and unfinished `uploads`;
  `versions`, `history` and `deleted` files with the call that restores them; and a look inside files without
  downloading them (`preview`, `document`, `file_details`): tables, archives, notebooks, images, PDFs, Word and
  PowerPoint. `download`, `download_zip` and `link` get a copy.
- **S3 explorer** (`s3_explorer.py`, `S3Explorer`): click through buckets and folders in the notebook, with a file's
  preview and details on the right.
- **Amazon DynamoDB** (`dynamodb.py`, `DynamoDBView`): `tables` and `table_info`, with the cost, CloudWatch usage
  and the `query(...)` call for each index; items as plain tables with `sample`, `scan`, `query`, `get`, PartiQL
  `sql` and `more`; and what the items hold with `schema`, `value_counts`, `largest` and `count`.
- **Amazon Bedrock Knowledge Bases** (`bedrock_kb.py`, `BedrockKBView`): every knowledge base and its settings in
  plain English (`kbs`, `kb_info`), sync health (`syncs`, `documents`, `unsynced`), `search` with highlighted
  passages, `ask` and `follow_up` with each claim linked to its source, and retrieval measured with `compare` and
  `evaluate`.
- **Bedrock knowledge base chat** (`bedrock_chat.py`, `chat()`): a chat window on a knowledge base with every
  RetrieveAndGenerate setting in reach, the request as JSON you can edit, and a `transcript()` that stays in the
  saved notebook.
- **Amazon SageMaker** (`sagemaker_env.py`, `SageMakerView`): the notebook you're in (`instance`: type, cost so far,
  CPU, memory and GPU use, idle shutdown), what fills its `disk` and what's safe to clear, and everything `running`
  and billing in the region.
- Every report starts with the numbers that matter, explains its findings in plain English with the command to run
  next, and shows a short note instead of a traceback. Nothing writes to AWS.

[Unreleased]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.34.0...HEAD
[0.34.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.33.0...v0.34.0
[0.33.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.32.0...v0.33.0
[0.32.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.31.0...v0.32.0
[0.31.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.30.0...v0.31.0
[0.30.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.29.0...v0.30.0
[0.29.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.28.0...v0.29.0
[0.28.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.27.0...v0.28.0
[0.27.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.26.0...v0.27.0
[0.26.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.25.0...v0.26.0
[0.25.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.24.0...v0.25.0
[0.24.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.23.0...v0.24.0
[0.23.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.22.0...v0.23.0
[0.22.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.21.0...v0.22.0
[0.21.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.20.0...v0.21.0
[0.20.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.19.0...v0.20.0
[0.19.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.18.0...v0.19.0
[0.18.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.17.0...v0.18.0
[0.17.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.16.1...v0.17.0
[0.16.1]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.16.0...v0.16.1
[0.16.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.15.0...v0.16.0
[0.15.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/utkarsh5026/aws-analyzer/releases/tag/v0.1.0
