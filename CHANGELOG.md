# Changelog

What's new in each release of [aws-analyzer](https://pypi.org/project/aws-analyzer/). The files in
[`analyzers/`](analyzers/) are the same code as the package, so this also tells you when a copy next to your notebook
is worth replacing.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before 1.0, a minor version (0.2.0) adds commands or
changes what one shows, and a patch (0.1.1) only fixes things. A change that could break a notebook starts with
**Breaking** and says what to change. To upgrade: `pip install -U aws-analyzer`.

## [Unreleased]

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
- `s3.py`: `ui.downloads()` shows what you've downloaded: each file, folder and zip in the downloads folder with its
  size and when it was downloaded, how much of the disk they take, and findings for zips a stopped `download_zip()`
  left unfinished, a disk running out of room and downloads over a month old. `ui.clean_downloads()` deletes them to
  free the disk: all of them, the ones named (`ui.clean_downloads("churn.zip")`), or those downloaded before
  `older_than="7d"`; `dry_run=True` shows what would go. It only empties a folder made for downloads, never the
  notebook's own folder or one that held your files first, and nothing in S3 changes.

### Changed

- **Breaking**: `s3.py` and `s3_explorer.py`: `download()`, `download_zip()`, and the explorer's **⬇ Download** and
  zips, save into one folder, `s3-downloads` next to the notebook, when they're given no path, instead of the
  notebook's own folder, so downloads no longer mix with your notebooks and code. The folder gets a `.gitignore` that
  keeps it out of git. A notebook that reads a downloaded file by its bare name (`pd.read_csv("events.csv")`) needs
  `s3-downloads/events.csv`, or `S3View(downloads=".")` for the old place. `S3View(downloads="~/scratch/s3")`,
  `S3Analyzer(downloads=...)` and `S3Explorer(downloads=...)` pick another folder; the explorer's **⚙** edits it as
  **Downloads in**, and `x.downloads` replaces `x.zip_folder` (which still works).
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

[Unreleased]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.8.0...HEAD
[0.8.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/utkarsh5026/aws-analyzer/releases/tag/v0.1.0
