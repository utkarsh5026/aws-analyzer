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

- `S3Explorer` finds files: type part of a name, or a file type such as `.csv`, in the search box over the list
  (`.csv .json` finds either, `.csv` also finds `.csv.gz` and `.jpg` finds `.jpeg`), or click one of the chips under
  it, one per file type in the folder with how many files have it. **Folders** and **Files** show only one kind, and
  stay on as you open other folders. **Include subfolders** lists everything below the folder, 10,000 files at a
  time, each under the folder it's in, so the search, the chips and the sort cover all of it: every Parquet file in a
  dataset's partitions, or the biggest files anywhere below. When nothing matches, the note under the list offers the
  fix (**Search the subfolders too**, **Show the 25 files**). `x.filter(".parquet", subfolders=True)` does the same
  from code, and in the text view. For your own code, `S3Navigator.below()` lists everything below a folder, and
  `parse_filter`, `filter_entries(kind=...)` and `count_types` do the matching and counting.

### Changed

- `S3Explorer` has a new look: drawn icons instead of arrow characters, the back, forward, up and refresh buttons
  grouped, softer rows, buttons and chips, the buttons above a report stay in view as it scrolls, and a spinner and
  the outline of a report while one loads. Long bucket names get the room the size column used to take.
- `S3Explorer`'s filter box is now the search box at the top of the list. `*.csv` there also finds `.csv.gz` files,
  and words separated by spaces must each be in the name (`churn train`), not the whole phrase.

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

### Fixed

- Text output (outside Jupyter, or `mode="text"`) keeps its tables' columns lined up when a cell holds an emoji or
  wide characters such as Chinese or Japanese text, in every analyzer. They used to push the rest of their row one
  column right per character.
  ([#26](https://github.com/utkarsh5026/aws-analyzer/pull/26))

## [0.3.0] - 2026-10-04

### Added

- `S3View` tables: click a column's header to sort by it (largest, newest or A to Z first, again for the other way, a
  third time for the original order), pick a value in a column's **Filter** to see only those rows (a storage class,
  a region, a bucket), and untick columns under **Columns** to hide the ones you don't need. It's plain HTML and CSS,
  so it still works after the notebook is saved and reopened.
  ([#21](https://github.com/utkarsh5026/aws-analyzer/pull/21))
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

### Changed

- `S3View`: long keys in tables keep the file name in view. The folder is dimmed and shortened from the left, and the
  whole key shows when you hover over it; in text mode the start of the folder goes, never the file name.
  ([#21](https://github.com/utkarsh5026/aws-analyzer/pull/21))
- `S3View` findings lead with a bold headline, with why it matters and what to do underneath as points, and amounts
  of money stand out. Notes start with their point in bold.
  ([#21](https://github.com/utkarsh5026/aws-analyzer/pull/21))
- `chat()` window: a new look, with rounded corners throughout. The conversation reads like a chat (your questions on
  the right, each answer as a card with the model's name over its time and cost, sources as numbered rows), the box
  you type in sits in one rounded bar with **Send**, each setting is a card, and the tabs and view buttons are
  segmented controls. It follows JupyterLab's light and dark themes. The request's **Text** view is now called
  **JSON**.
  ([#23](https://github.com/utkarsh5026/aws-analyzer/pull/23))

### Fixed

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

[Unreleased]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/utkarsh5026/aws-analyzer/releases/tag/v0.1.0
