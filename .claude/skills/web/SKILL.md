---
name: web
description: The roadmap for aws-analyzer in a browser tab (`aws-analyzer serve`, src/aws_analyzer/server/) - from the served reports that exist today to a JavaScript-first app organised around the things in an account (search one service at a time, tabs, resource pages, per-service attention, dark mode), with live data, interactive tables and charts and the agent streaming its answers. Holds the vision, the rules the web layer keeps, the phases with what "done" means for each, the open decisions, the list of JavaScript ideas and the progress table. Use for "/web status", "/web phase 3", or before changing anything in src/aws_analyzer/server/.
argument-hint: "<status | phase N | ideas>"
---

# The web app: aws-analyzer in a browser tab

Request: `$ARGUMENTS`. `status` means: read the progress table, check it against the tree (`ls
src/aws_analyzer/server/ src/aws_analyzer/server/static/`) and the open pull requests, and say what's done and what's
next. `phase N` means: do that phase, following its section below (one pull request per bullet group unless it says
otherwise). `ideas` means: pick from "Things JavaScript can do here" with the user.

Read CLAUDE.md first, its "The web page (`server/`)" section above all. Everything there still holds; this plan relaxes
none of it except where it says so.

## Why

In a notebook everything has to fit in a cell. A report is HTML with no JavaScript (Jupyter strips scripts from a saved
notebook), so sorting, filters and zoom are CSS tricks. A window is ipywidgets: every row is about six widgets, every
click goes through the kernel, and the window is boxed inside a cell. A browser tab served by our own process has none
of these limits. It gets a whole screen, runs any JavaScript we ship, and talks to the server directly.

**The direction the user set: JavaScript-first. Use JavaScript as much as it helps.** Anything the browser can do
better than the kernel goes to the browser: drawing, sorting, filtering, searching, laying out graphs, animating,
reading files, keeping history. Python keeps what only it can do: AWS calls through the analyzers, and the pure
analysis that turns AWS data into findings with actions. When a change could go either way, pick the one that puts
more in the browser.

The notebook isn't going away. Reports in cells stay the default, because they survive in a saved notebook, which a
browser tab doesn't. The web app is a second front end on the same core, like the agent is.

```
            ┌──────── core (per service): helpers · models · analysis · analyzer ────────┐
            └──────▲─────────────────────▲─────────────────────▲───────────────▲─────────┘
                   │                     │                     │               │
          View → HTML in a cell   explorer (ipywidgets)   agent toolkit   server/ → JSON + events
                                                                                 │
                                                                     browser: TypeScript app
```

## The design: things first, not commands (decided with the user, November 2026)

The first page was a notebook in a tab: pick a command, fill a form, read the cell's report. The user set a different
direction, worked out on a design canvas (the "aws-analyzer web: prototype UI ideas" artifact): the content stays the
same, but the site is organised around the things in an account (a function, a bucket, a queue), not around the
commands that read them. What was decided, in order of how much it shapes the code:

- **Search is the home page, one service at a time.** A big search box with a service picker inside it ("Lambda ▾"),
  and a "Search in" row of services under it. Only the service picked is read: its names come from
  `GET /api/resources/<service>` (the cheap listing calls the analyzers already make: ListFunctions, ListBuckets,
  ListTables, ...), kept by the server for `NAMES_KEPT` and by the page for the session. Services already read carry a
  green dot and search instantly; matches in them show under the results as "from memory · no new reads". Services
  not read yet are buttons, so reading one is a deliberate click. Pasting an `s3://` path, an ARN or a console link
  picks the service by itself. **Nothing reads every service at once**: no global feed, no global search (the user's
  rule, for speed and cost).
- **Tabs.** What you open stays open as a tab across the top, like a browser's (Home pinned on the left, ✕ on the
  rest, + for a new one), kept in the browser (localStorage) so they come back on reload. Navigating inside a tab
  replaces it; opening from Home, or "↗ New tab", adds one.
- **Picking a result: a quick look, then a tab.** A click (or ↑ ↓) shows a preview beside the results from what the
  search already read; Enter or Open opens it in a tab.
- **Needs attention is per service.** Each service's page opens with "Needs attention in <service>", checked when the
  service is opened ("only Lambda was read", ↻ Check again). Home shows only "Checked so far": the services already
  opened and their counts, and says the others haven't been read.
- **Resource pages.** A thing's page leads with the verdict and a few cards, then the notebook commands as tabs
  (Overview, Errors, Logs, Performance, Code, Settings, Cost for a function; Last run, Runs, Failures, Definition for a
  workflow). Details (a run, an execution, an API call) open in a side panel beside the list, ‹ › stepping through
  them, with "Open as page". Every name that's another resource links to it ("Wired to": the API, queue, table and
  dead-letter queue around a function). A failure across services reads as one story: the workflow's failed step
  shows the Lambda run behind it in place.
- **Ctrl K** jumps to anything already read (no AWS calls), to actions ("Follow orders-etl live") and to the notebook
  commands. Tab moves it to another service.
- **The map** (the account as boxes and moving arrows, red where it breaks) is a later, separate view, once most
  services have pages.
- **The look:** the guides' calm look, polished for reading all day: warm paper (`#f6f1e9` page, `#fffdf9` cards),
  soft ink, a terracotta accent (`#a85a26`); verdicts on the card colour with a thin red border, not red blocks;
  quiet pills with a fine border; tabular numbers, numbers right-aligned in tables; monospace for every identifier,
  ligatures off; a visible focus ring; short hover transitions, none under reduced motion. **Dark mode**: warm charcoal
  (`#24201c` page, `#2d2823` cards), never black, an apricot accent (`#e3a56c`); a Light / Dark / Auto switch in the
  top bar, Auto (the system's) by default, kept in localStorage.
- **The notebook stays one click away:** every page has "Copy as cell", and the command pages (the form and the
  cell's report) stay reachable from each service's page and Ctrl K.

## Rules the web layer keeps

These come on top of CLAUDE.md's product goal and hard constraints, which still apply to every page.

- **Read-only, like the notebook.** No endpoint runs a View's `_CHANGES` command, and no endpoint calls a write
  operation. If a write ever comes to the web (Lambda's `invoke()` is the only candidate), it needs the user's say-so
  first (see "Open decisions"), a confirm dialog showing exactly what will run, and a POST of its own.
- **AWS data is untrusted.** A name, tag, log line or knowledge-base passage can hold HTML or script. Insert it with
  `textContent` or the framework's escaping, never `innerHTML`. The one exception is the server-rendered report HTML
  from Phase 1, which the Views escape. It goes away block by block in Phase 5.
- **The page talks only to its own server.** The CSP stays `script-src 'self'` and `connect-src 'self'`: no CDN, no
  `eval`, no inline scripts. Every library is vendored into the package (pinned, with its license in
  `server/static/THIRD_PARTY.md`). Add `'wasm-unsafe-eval'` only in the phase that ships WebAssembly, and say so in the
  pull request.
- **Every API call carries the token** (`X-AWS-Analyzer-Token`). There's no CORS, and the cookie stays HttpOnly and
  SameSite=Strict. A new transport (Server-Sent Events, WebSocket) checks the token too: a query parameter for an
  EventSource, the first message for a WebSocket.
- **Python's import rules don't move.** Analyzers stay boto3 + stdlib at import time. Only `server/` imports fastapi,
  starlette, uvicorn and pydantic (`SERVER_TOP_LEVEL` in `rules.py`), and `server/__init__.py` imports none of them.
  Nothing the browser needs leaks into the core: the core returns dataclasses, and the server turns them into JSON.
- **Users never need Node.** The built JavaScript ships inside the wheel. Node is only for contributors and CI (see
  Phase 2).
- **A size budget.** The first page load stays under about 300 KB gzipped. Heavy libraries (charts, graph layout,
  DuckDB-WASM, PDF.js, an editor) load with a dynamic `import()` when the page that needs them opens.
- **It works for everyone.** Keyboard first (every action reachable without a mouse), visible focus, ARIA roles on
  custom widgets, `prefers-reduced-motion` honoured, light and dark from `prefers-color-scheme`, and the guides' calm
  look (warm paper, terracotta accent; see CLAUDE.md's docs section).
- **Tested like the rest.** API endpoints with Starlette's `TestClient` on moto. Pure TypeScript with Vitest. Pages
  with Playwright on demo.py's data, in CI. A test checks the committed build is fresh.
- **Documented like the rest.** `docs/browser.md` grows with each phase. A rebuilt window gets a section or its own
  page, with screenshots from a `web_shots.py` (light and dark, from demo data, like `shots.py`). README and CHANGELOG
  follow the usual rules.

### TypeScript style

The Python code style in CLAUDE.md translates directly:
- full-word names, functions as verbs, booleans as questions;
- small functions, early returns, no nesting past three levels;
- no magic numbers (named constants);
- no explanatory comments: a doc comment on each exported function, and names for the rest;
- `strict` TypeScript with no `any`; a type for data with named parts, not a loose object.

One module per concern: `api/` (the client and the event streams), `blocks/` (a renderer per report block),
`windows/<service>/`, `ui/` (shared widgets). Formatting and linting with Biome, checked in CI.

## Phases

Each phase lists what to build and what "done" means. One pull request per bullet group. Update the progress table in
the same pull request.

### Phase 0: proven on real SageMaker (needs the user)

Nobody had opened the page on real SageMaker before Phase 1 shipped, since this container can't reach it. What's
known:
- **The link:** opens on the user's notebook instance (after #298). Studio's (`links.STUDIO_APP_PATHS`) is untried.
- **The check is built:** `server/ping.py`, with `static/ping.js` and `ping.css`. `GET /ping` is a page that opens
  three connections and copies its results as text, holding no token. Each connection sends a line a second for ten
  seconds:
  - `/api/ping-stream` as an EventSource;
  - the same stream as a `fetch`;
  - `/api/ping-socket` as a WebSocket, which also answers the page's messages, to check the way back.

  The stream takes the token as a query parameter or the header. The socket takes it as its first message. On
  SageMaker the terminal prints the page's link.
- **Found locally, with jupyter-server-proxy 4.6.1:** it streams a response only when the request's `Accept` header is
  exactly `text/event-stream` (its `handlers.py`). Anything else, a plain `fetch` included, arrives all at once when
  the response ends. An EventSource sends that header by itself, so Phase 3's fetch-based streams must send it too.
  WebSockets pass both ways, but the proxy drops the close code (the page sees 1005), so a job's end must be a message,
  not a close code. Chrome's `connect-src 'self'` lets the same-origin WebSocket through.
- **uvicorn needs a WebSocket library:** `websockets` comes only through langgraph-sdk and langsmith. Phase 3 should
  depend on it directly. The page names the library the server has, so a missing one isn't blamed on the proxy.

Still to do: the user opens `/ping` on a notebook instance and in a Studio JupyterLab space and sends the results.
SageMaker's proxy version may differ from 4.6.1.

Done when: both machine kinds report results, and the push transport for Phase 3 is chosen from what worked
(WebSocket if both work, since it carries cancel messages back too). Then remove `ping.py`, its static files, its tests
and the terminal's line.

### Phase 1: served reports (done, #296)

`aws-analyzer serve`, the FastAPI app, the token and CSP, the SageMaker link, and a plain ES-module page:
- services and commands from the Views;
- a form per command, plus a call box;
- the server-rendered report HTML;
- ▶ Run on next steps;
- Ctrl K to find a command;
- hash routes, so back, forward, reload and bookmarks work.

### Phase 2: the frontend foundation

- **Toolchain:** TypeScript + Vite + Preact, with `@preact/signals` for state. Preact is React's model in about 4 KB,
  so the bundle budget stays easy. Sources in `web/` at the repo root. `vite build` writes
  `src/aws_analyzer/server/static/dist/` (hashed files plus a manifest), which **is committed**, so the wheel ships it
  and users need no Node. `make web` builds it. `tests/test_server.py` fails when `dist/` doesn't match a fresh build
  (compare the manifest's content hashes), the way `test_tours.py` checks `tours.js`. CI's Node job runs
  `npm ci && npm run build && npm test && npx biome check`.
- **Port the Phase 1 page** to that app with no visible change: router, sidebar, forms, call box, finder, report view.
  Delete `static/app.js` and `app.css` when it's done.
- **Playwright in CI:** a pytest fixture starting the server on demo data (the scratch `serve_demo.py` pattern: seed in
  moto, pass `views=`, run uvicorn on a free port), and page tests for what Phase 1 does.

Done when: the page looks and works as before, built from TypeScript, with page tests in CI.

How the first two are built (one pull request, since a toolchain alone builds nothing to test):
- `web/`: `src/main.tsx` mounts `ui/App` on `#app`. `api/` holds the client and the server's shapes, `lib/` the pure
  logic Vitest tests (`route`, `call`, `find`), `ui/` the components, and `state.ts` the signals (`catalog`, `hash`,
  `finderOpen`) and the reports already run. The stylesheet moved unchanged to `src/styles/app.css`.
- No Babel: Vite's own JSX transform (`oxc.jsx.importSource: "preact"`), so no `@preact/preset-vite`. `web/.npmrc`
  sets `legacy-peer-deps`, since npm 10 crashes on Vitest's optional peers.
- The server reads `dist/manifest.json` (`server/assets.py`'s `page_assets`) and writes the tags into `index.html`'s
  `__ASSETS__`; with no build, the page says to run `make web`.
- Freshness without Node: `web/sources.ts` writes `dist/sources.sha256`, a hash of `web/src` (tests left out) and the
  build's settings, and `tests/test_server.py` hashes the same files the same way. CI's "Web page" job runs
  `npm ci`, `npm run check` (tsc, Biome, Vitest), `npm run build`, then fails when `dist/` differs from the commit.
  The build is byte-identical after a fresh `npm ci`.
- Playwright in CI: `tests/test_web_page.py`, 16 tests on demo.py's S3 and Lambda data (the `served` fixture: moto,
  `views=`, uvicorn in a thread on a free port; the `page` fixture fails a test on a script error or a CSP refusal).
  They skip without Playwright's Chromium and fail without it under `AWS_ANALYZER_PAGE_TESTS=required` (CI's Page
  tests job). Putting the Ctrl K race back makes `test_typing_straight_after_ctrl_k_keeps_every_letter` fail.
- Checked by hand against the Phase 1 page on demo data: 12 of 18 views (light and dark) pixel-identical, the rest
  differing only where the demo's Lambda report says "49m ago" against "51m ago"; the same scripted clicks give the
  same transcript. Two things moved: Ctrl K then typing at once kept every letter (the reset and focus had to be a
  layout effect), and a literal typed in a box keeps its commas in the call box (`['a', 'b']`, which Phase 1 showed
  as `['a' 'b']`).

### Phase 3: data, jobs and live updates

- **Reports as data:**
  - `GET /api/data/<service>/<command>?...` returns the report's `ReportData` as JSON for the services with
    `_DataCommands` (Lambda, API Gateway, Step Functions, S3, ...). Each column carries its meaning, kind (`ms`,
    `share`, `time`, ...), `bar=` and `tones=`.
  - Add `ReportData.to_json()` in `_kit/datasets.py`, pure.
  - Services without it keep the HTML path until they get one.
- **Blocks as JSON:**
  - Add a third drawing to the block registry: `@_draws(_Table, "json")` beside `"html"` and `"text"`.
  - `POST /api/run` returns `blocks: [...]`, each block's JSON, and keeps `html` for any block without a JSON drawing,
    so a page always shows everything.
  - This is the seam Phase 5's pages render through.
- **Jobs instead of one long request:**
  - `POST /api/jobs` starts a command on a worker and returns its ID.
  - The transport Phase 0 picked streams `progress` (from the View's `_progress` ticks: count, total, rate), `report`
    (each `_show`), `note` and `done`.
  - `DELETE /api/jobs/<id>` cancels it (a `threading.Event` the analyzers' long scans check).
  - The page shows a progress bar with the time left, keeps working while jobs run, and lists running jobs.
  - The per-service lock becomes per-View: a second View for a second concurrent job.
- **Live endpoints:** an event stream per live thing the core already reads in a loop:
  - Lambda `activity()` and new log lines (`_filter()` from where it stopped);
  - SQS queue depth (`read_queue`);
  - the SageMaker machine (CPU, memory, GPU from `/proc` and `nvidia-smi`).
  Each read runs on the server's own schedule, at most one per stream however many tabs listen, and stops when no
  one listens.

Done when: a long S3 summary shows a real progress bar and can be cancelled, and a page can subscribe to Lambda
activity and get pushed updates.

How reports as data are built (the first bullet group):
- `ReportData.to_json()` and each part's own (`DataTable`, `DataColumn`, `DataChart`), pure in `_kit/datasets.py`:
  cards as `{label, value, tone}`, findings as `{level, message}`, a column's `tones` as a dict, its kind in words as
  `unit`; `json_value` makes each cell plain JSON (times ISO UTC with `Z`, a naive time read as UTC, NaN null).
- `ReportRunner.data(key, command, boxes)` and `data_call(key, call)` take the same input as `run` / `run_call`,
  call the View's `_read_quietly` under the service's lock, and give a `DataReport` (call, form texts, `data`,
  seconds). `NoData` carries the words of the note a report was instead (its `_Hint`), or says the service's View has
  no `_DataCommands` (`gives_data`, also the catalog's `data` per service).
- `GET /api/data/<service>/<command>?<name>=<text>` and `GET /api/data/<service>?call=...`, with the token: 400 for
  `CantRun`, 422 for `NoData`. The page doesn't use it yet; Phase 5's grid will.

### Phase 4: the frame and search (the design above)

- **The frame, tabs, theme and search home** (one pull request): `GET /api/resources/<service>` (`server/resources.py`:
  a lister per service on its View's analyzer, each `Resource` naming the command and arguments that open it, cached
  for `NAMES_KEPT`), the catalog's `searchable` per service; on the page the tab bar (`lib/tabs.ts`), the theme switch
  (`lib/theme.ts`), the search home with its service picker, from-memory matches and not-read services
  (`lib/search.ts`), and Ctrl K over names already read. Until resource pages exist, a result opens its service's
  main report for it (`function_info`, `bucket_info`, `table_info`, ...) as a command page in a tab.
- **The quick look:** a preview beside the results, from the index entry and the service's cached reads.
- **Per-service attention:** each service page's "Needs attention" block from that service's own findings, and Home's
  "Checked so far".

Done when: the home page is search, everything opens in tabs that survive a reload, and no page reads a service the
user didn't pick.

### Phase 5: resource pages, service by service

One service per pull request, each page built from Phase 3's blocks as JSON and data, drawn by Preact components
(the components list below), in light and dark. Lambda first (the list with attention, a function's page with its
tabs, the run side panel, "Wired to"), then Step Functions (the path a run took, where the time went, the Lambda run
behind a failed step), Messaging, S3 (folders, previews in the browser), API Gateway, DynamoDB, Athena, OpenSearch,
Bedrock and SageMaker. Each keeps what its notebook explorer window does (its tabs, its findings, its `focus`) and
adds what a page can: virtualised logs, brushing a chart to set the time range, live updates once the transport is
chosen.

The components, built as the pages need them:
1. `_Title`, `_Cards`, `_Note`, `_Findings`, `_Verdict`, `_Next`: findings and next steps become actions (open, copy
   as cell).
2. `_Table` as a data grid: virtualised rows, sort, filter and group by any column, sticky header, copy as CSV or
   Markdown, a cell that names a resource links to its page.
3. Charts (`_Bars`, `_Split`, `_Meter`, `_Dots`, `_Treemap`, Lambda's run charts, the error chart) in **Vega-Lite**,
   the grammar the agent's charts use, lazy-loaded; tooltips, brushing a range, PNG / SVG export.
4. `_JsonTree`, code (vendored highlighting), `_Graph` / `_FlowGraph` / `_HopMap` laid out with **ELK.js**, pan and
   zoom.
5. The rest (`_Passage`, `_Answer`, `_Chunks`, `_Pages`, `_Flow`, `_Media`, ...).

Done when: every service has its pages, and `POST /api/run` can drop `html` (the command pages draw blocks too).

### Phase 6: the agent and the chats in the browser

- The agent's chat as a page:
  - **Answers streamed token by token.** LangChain's `astream_events` over a WebSocket. On Bedrock that means
    `ConverseStream`, a generation call like `Converse`, so it carries the `# read-only:` marker for `rules.py`.
  - Each tool step drawn live as it runs, the plan's checklist ticking, citations that scroll to the tool result they
    cite, and charts as interactive Vega-Lite.
- **✦ Ask** in every window page: the same drawer, fed the page's `focus`, with buttons that navigate the page.
- The 🔌 MCP, 🧠 memory, 🗂 chats and ⚙ model panels as pages.
- `bedrock_chat` as a page: the conversation streaming, the settings form from `request_schema()`, the Test tab's
  runs filling a grid live, and sweeps as a heatmap.

Done when: everything `AgentView().chat()` and `chat()` do works in the tab, with streaming.

### Phase 7: a workspace, not just pages

Tabs come in Phase 4; this phase adds the rest:
- **Split panes:** two pages side by side (yesterday's errors next to today's), "Open beside".
- **Pins and dashboards:** pin a report or chart; the dashboard re-runs them on open or on a timer, kept in
  IndexedDB, never on the server.
- **History:** every report kept in IndexedDB, searchable, with a diff between two runs of the same call.
- **Notifications** (asked for once): a Live watch that sees a new error cause or a filling queue while the tab is in
  the background.
- **Export:** a single-file HTML snapshot of a page, CSV and XLSX from a grid, the notebook cell that reproduces it.
- **The map** of the account (the design above), and a region switcher in the top bar.
- **Keyboard everywhere:** `j` / `k` through rows, `/` to search, `?` for the list.

### Phase 8: from a notebook cell too

`aws_analyzer.serve()`: the same app on a thread with its own event loop inside the kernel (a kernel already runs
one, so `uvicorn.run` can't be called there). It shares the notebook's Views, caches and agent conversation, prints
the link and shows it as an `IFrame` in the cell. Stopping the kernel stops it. Same token, same rules.

## Things JavaScript can do here

A list to pick from when planning a phase, or when the user asks for ideas. Each item names the library it would
vendor. Prefer small, dependency-free libraries, and check each against the size budget and the CSP.

- **Grids:** virtualised tables with sort, filter, group and pivot over a whole `ReportData` table in the browser
  (TanStack Table + TanStack Virtual, or a small grid of our own).
- **SQL in the tab:** DuckDB-WASM over a report's tables and S3 previews ("group the runs by cold start and memory").
  Needs `'wasm-unsafe-eval'`.
- **Charts:** Vega-Lite for everything declarative, uPlot for high-rate live series (Live tabs, the machine
  dashboard).
- **Graphs:** ELK.js layout for Step Functions, messaging and how-it-works graphs, with pan and zoom (d3-zoom), a
  minimap and animated execution replay.
- **Code and text:** Shiki highlighting, CodeMirror 6 for the call box and Athena SQL (autocomplete from the catalog
  and the command's parameters), and a diff viewer for `diff()` between Lambda versions.
- **Files:** PDF.js, hyparquet / apache-arrow for Parquet and Arrow, and papaparse for CSV
  previews.
- **Live:** streams with auto-reconnect, a pause button, and a "new lines since you looked" marker. Animations on
  the messaging map and the Live tabs, off under reduced motion.
- **Command palette:** fuzzy search over commands, recent reports, resources seen in any report (functions, buckets,
  tables) and actions ("open orders-etl's logs").
- **Local memory:** IndexedDB for report history, pins and layouts; `localStorage` for small preferences (theme,
  pane widths).
- **Sharing:** a copyable link to any view (the hash already does this), single-file HTML snapshots, and copy as a
  notebook cell.
- **Workers:** Web Workers for heavy client work (sorting 100k rows, parsing a large log page, DuckDB) so the page
  never stutters.

## Open decisions (ask the user)

| Decision | Recommendation | Why it's the user's |
|---|---|---|
| Framework | Preact + signals + TypeScript + Vite | It shapes every later phase |
| Ship the build | Commit `server/static/dist/`, with a freshness test | The alternative, a hatch build hook running Node at release, keeps the repo smaller but makes releases need Node |
| Push transport | Decide from Phase 0 (WebSocket if the proxy passes it) | Depends on real SageMaker |
| `invoke()` on the web | Leave it in the notebook | The only write; a deliberate product choice |
| Bundle budget | 300 KB gzipped first load | Trades start-up speed against features on the first page |
| Vega-Lite size (~250 KB gz) | Lazy-load it with the first chart | Could pick uPlot everywhere instead and lose declarative specs |

## Progress

Update this table in every pull request that moves the web app on. Status is one of: done, in review, in progress,
not started, needs the user.

| Phase | Status | PR | Notes |
|---|---|---|---|
| 0: proven on real SageMaker | needs the user | | link works on a notebook instance; `/ping` built and passes through jupyter-server-proxy 4.6.1 locally; waiting for results from a notebook instance and Studio |
| 1: served reports | done | #296, #298 | `aws-analyzer serve`, FastAPI, token + CSP, plain ES-module page |
| 2: frontend foundation | done | #301 | `web/` (Preact, TypeScript), committed `dist/`, CI's Web page job, and Playwright page tests in CI's Page tests job |
| 3: data, jobs, live | in progress | #306 | reports as data done (`/api/data`, `ReportData.to_json()`); next: block JSON, then jobs and live streams (the transport waits on Phase 0) |
| 4: the frame and search | in progress | #307 | the design above: tabs, theme, search one service at a time (`/api/resources`); next the quick look and per-service attention |
| 5: resource pages | not started | | service by service, Lambda first, from blocks as JSON; grid, Vega-Lite, ELK.js |
| 6: agent and chats | not started | | token streaming, live tool steps, ✦ Ask everywhere |
| 7: workspace | not started | | split panes, pins, history with diffs, notifications, export, the map |
| 8: from a cell | not started | | `aws_analyzer.serve()` on a kernel thread |

Known issues to fix on the way:
- In dark mode, the reports' "🐼 This report as data" fold falls back to a light background
  (`var(--card, #faf7f2)` in `_kit/dataview.py`). Notebooks have the same bug.
- The page's notebook-only hints (`next(1)`, the data fold's `ui.data()`) are hidden or say notebook things. Phase 5's
  components replace them with web actions.
