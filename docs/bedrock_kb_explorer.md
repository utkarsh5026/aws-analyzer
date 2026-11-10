---
title: Knowledge Base Explorer Guide
description: "How to click through an Amazon Bedrock knowledge base in a SageMaker notebook with aws-analyzer's knowledge base explorer window: every file next to Bedrock's record of it, how a file was indexed and its chunks, why one failed, whether a question finds it, search, syncs and settings."
---

<p class="eyebrow"><img class="aws-icon" src="images/aws/bedrock.svg" alt="" width="32" height="32"> Knowledge base explorer <code>bedrock_kb/explorer/</code></p>

# Click through a knowledge base: every file, how it was indexed, what a question finds

`explore()` turns a notebook cell into a window on a Bedrock knowledge base. Every file of its S3 data sources sits next to Bedrock's record of it, the problems first. Click one to see each step it took into the vector store and its chunks in order, or ask a question to see where it ranks.
{ .lede }

<ul class="pills">
  <li>One install, ipywidgets already on SageMaker</li>
  <li>Read-only: never starts a sync</li>
  <li>Every file, one state each</li>
  <li>A file's chunks, in document order</li>
</ul>

The examples use `support-docs`, a knowledge base of support policies in the bucket `support-docs-bucket`, some tagged with a `team` and a `year`. The screenshots are the real window in JupyterLab on a simulated Bedrock, so scores and times are illustrative. For reports you run from code (answers with citations, filters, retrieval hit rate), see the [Bedrock Knowledge Bases guide](bedrock_kb.md).
{ .muted }

![The knowledge base explorer in a notebook, animated: it starts on support-docs' Overview, its verdict on the files and a card for each thing to do; the pointer opens the Files tab, types warranty in the search box and clicks warranty.pdf, which shows how it was indexed, step by step; it asks How long is the warranty? about that file and sees its passages and where they rank in the whole knowledge base; then it opens the Search tab and asks What does error E1234 mean?, the passages found shown with their scores, and last the Syncs tab, every sync on a timeline](images/kb-explorer-tour-light.webp#only-light){ width="984" height="845" loading=lazy }
![The knowledge base explorer in a notebook, animated: it starts on support-docs' Overview, its verdict on the files and a card for each thing to do; the pointer opens the Files tab, types warranty in the search box and clicks warranty.pdf, which shows how it was indexed, step by step; it asks How long is the warranty? about that file and sees its passages and where they rank in the whole knowledge base; then it opens the Search tab and asks What does error E1234 mean?, the passages found shown with their scores, and last the Syncs tab, every sync on a timeline](images/kb-explorer-tour-dark.webp#only-dark){ width="984" height="845" loading=lazy }
/// caption
**`explore("support-docs")`**: find a file, ask whether it answers a question, search the whole knowledge base, then read its syncs.
///

## Watch it run { #video }

Five steps in a notebook, from a knowledge base's files to which search settings suit a question: each command typed and run, and the window it shows, with what to notice. It plays as you scroll to it; click it to pause, or pick a chapter to jump to a step.
{ .gist }

<div class="aa-tour" data-tour="bedrock_kb_explorer">
  <div class="tour-screen"></div>
  <div class="tour-controls"></div>
  <div class="tour-chapters"></div>
  <p class="tour-links"><a href="tour.html?service=bedrock_kb_explorer">Full screen</a> · <a href="videos/bedrock_kb_explorer-tour-light.mp4" data-tour-mp4 download>Download as MP4</a>, for Slack or a slide (light or dark, as this page is)</p>
</div>

## Open it { #open }

<div class="steps" markdown>

1. **Install it** in a notebook cell:

    ```bash
    %pip install aws-analyzer
    ```

2. **Open the window.** It uses the notebook's IAM role and region, so there's nothing to configure.

    ```python
    from aws_analyzer import BedrockKBView

    ui = BedrockKBView()
    x = ui.explore("support-docs")   # a name, ID or ARN; explore() picks the only one, or the first active one
    ```

</div>

??? more "Straight to a file, another region or a fixed height"

    ```python
    from aws_analyzer.bedrock_kb import explore

    explore()                             # the only knowledge base here, or the first active one
    explore("support-docs")               # a name, ID or ARN
    explore("support-docs", file="warranty.pdf")   # straight to one file's page
    explore(region="us-west-2")           # another region (or profile="dev")
    explore("support-docs", height=800)   # 800px pages (else the browser's height)
    ui.explore()                          # from a BedrockKBView, on its knowledge base
    ```

??? more "The top of the window, and without ipywidgets"

    The window needs `ipywidgets`, which SageMaker notebooks already have (elsewhere, `%pip install ipywidgets`, then reload the browser tab). Without it, or outside Jupyter, `explore()` shows the same as reports: [`files()`, `file()` and `search_file()`](bedrock_kb.md#files).

    **The top of the window** is the knowledge base field and its cards. Click the field for the region's knowledge bases, with a search box that finds one by its name, part of its ID (`k7qj` finds `K7QJ2M4XNA`) or its description; Enter picks the first, and a whole ID or ARN works too. The cards say how it's doing: how many files it has and how many are searchable, how many failed or are waiting for a sync, when the last sync ran, and what its vector store costs while idle. The tabs below hold the rest, and the line at the bottom says what's going on.

## Start here { #start }

Four things to try in your first minute. Nothing you click starts a sync or changes a file.
{ .gist }

<div class="quick" markdown>

What's wrong
{ .qgroup }

-   [What needs fixing, and does a sync fix it?](#explorer-overview) <span class="how">Read the Overview tab</span>
-   [Which files failed, and why?](#explorer-files) <span class="how">Files, then the Failed chip</span>

One file
{ .qgroup }

-   [How was this file indexed?](#explorer-file) <span class="how">Click it in the Files tab</span>
-   [Does a question find this file?](#explorer-ask) <span class="how">Type the question on its page</span>

</div>

## What needs you { #explorer-overview }

The **Overview** tab says how the files are doing in a sentence, then what to do about them, a card for each fix.
{ .gist }

![The Overview tab of the explorer: the knowledge base field showing support-docs, cards for 44 files, 39 searchable, 2 failed, 2 to sync, the last sync done 1d ago and an idle cost of $350.40 a month, the tabs, and the Overview: its verdict, 37 of 44 files are indexed and up to date, 6 need you, and one sync of docs-s3 fixes 3 of them; a bar of the 44 files by state with its legend; then three cards of what to do: Sync docs-s3, for a changed file, a new one and one deleted from S3, with the command to copy; Fix 2 files, then sync, for an encrypted PDF and a scanned one; and Convert or remove 2 files, a .pptx and an .mp4 Bedrock doesn't read; and below them the findings left, as notes](images/kb-explorer-light.webp#only-light){ width="984" height="846" loading=lazy }
![The Overview tab of the explorer: the knowledge base field showing support-docs, cards for 44 files, 39 searchable, 2 failed, 2 to sync, the last sync done 1d ago and an idle cost of $350.40 a month, the tabs, and the Overview: its verdict, 37 of 44 files are indexed and up to date, 6 need you, and one sync of docs-s3 fixes 3 of them; a bar of the 44 files by state with its legend; then three cards of what to do: Sync docs-s3, for a changed file, a new one and one deleted from S3, with the command to copy; Fix 2 files, then sync, for an encrypted PDF and a scanned one; and Convert or remove 2 files, a .pptx and an .mp4 Bedrock doesn't read; and below them the findings left, as notes](images/kb-explorer-dark.webp#only-dark){ width="984" height="846" loading=lazy }
/// caption
**Overview**: a sentence on the files, then what to do about them, a card for each fix: a sync per data source (with its command), the files to fix, and the files to convert. Below, out of the picture: how each data source becomes vectors, with each problem pinned under the step where it happens, and the recent syncs.
///

## Every file, next to Bedrock's record of it { #explorer-files }

The **Files** tab gives every file one state, problems first: failed, changed since the sync, not synced yet, or indexed and searchable.
{ .gist }

Search by name, folder or Bedrock's reason, click a state's chip for only those files, and **⬇ List** saves the list's `s3://` paths as a `.txt` file to download.

??? more "Every state, what it means and what to do"

    | State | What it means | What to do |
    |---|---|---|
    | **Failed** | Bedrock couldn't index it, for the reason shown: an encrypted PDF, a scan with no text layer, a file too big | The file's page says what usually fixes that reason, then sync |
    | **Changed since sync** | It (or its `.metadata.json`) changed in S3 after it was indexed, so searches still find the old version | Sync |
    | **Not synced yet** | Added after the last sync, so nothing in it is searchable | Sync |
    | **Skipped by the sync** | In S3 before the last sync, yet Bedrock has no record of it: usually a type Bedrock doesn't read, a file over 50 MB, or one in GLACIER | Convert, split or restore it, then sync |
    | **Deleted from S3** | Gone from S3, but still in the index, so answers can cite it | Sync, and the next sync removes it |
    | **Partly indexed** | Only part of it, or of its metadata, was indexed | Its page says what's missing |
    | **Ignored** | Bedrock skipped it on purpose, usually because of its type | Nothing, unless you meant it to be searchable |
    | **Indexing** | Being indexed or removed right now | Wait for the sync to finish |
    | **Not checked** | Past the first 10,000 documents of Bedrock's list | Open it: its own record is looked up |
    | **Indexed** | Indexed, up to date and searchable | |

## How a file was indexed { #explorer-file }

Click a file to see each step it took from S3 into the vector store, the one that failed in red, and its chunks in document order.
{ .gist }

![The Files tab: on the left, warranty typed in the search box, the state chips with their counts and warranty.pdf in the list; on the right, warranty.pdf's page with cards for its state (Indexed), 5 chunks, 3.9 KB, changed 20 days ago, indexed 3 days ago and 2 metadata attributes, a link that opens the file, no issues found, and How it was indexed as a line of steps: Stored in S3, Read by the parser, Cut into chunks (5 chunks, about 294 tokens each, from page 1 to 8, fixed size: 300 tokens per chunk, 20% overlap), Embedded, Stored as vectors and Now: Indexed](images/kb-explorer-file-light.webp#only-light){ width="984" height="846" loading=lazy }
![The Files tab: on the left, warranty typed in the search box, the state chips with their counts and warranty.pdf in the list; on the right, warranty.pdf's page with cards for its state (Indexed), 5 chunks, 3.9 KB, changed 20 days ago, indexed 3 days ago and 2 metadata attributes, a link that opens the file, no issues found, and How it was indexed as a line of steps: Stored in S3, Read by the parser, Cut into chunks (5 chunks, about 294 tokens each, from page 1 to 8, fixed size: 300 tokens per chunk, 20% overlap), Embedded, Stored as vectors and Now: Indexed](images/kb-explorer-file-dark.webp#only-dark){ width="984" height="846" loading=lazy }
/// caption
A file's page: each step from S3 into the vector store, here all of them green.
///

![warranty.pdf's five chunks: a bar of five blocks of about the same width, then a line per chunk with its page, about 300 tokens and 210 words and its first words; chunk 2 is open, starting with four lines highlighted in green, the text it repeats from chunk 1 (270 characters shared), then the rest of its text and its metadata team=legal, year=2024](images/kb-explorer-chunks-light.webp#only-light){ width="984" height="846" loading=lazy }
![warranty.pdf's five chunks: a bar of five blocks of about the same width, then a line per chunk with its page, about 300 tokens and 210 words and its first words; chunk 2 is open, starting with four lines highlighted in green, the text it repeats from chunk 1 (270 characters shared), then the rest of its text and its metadata team=legal, year=2024](images/kb-explorer-chunks-dark.webp#only-dark){ width="984" height="846" loading=lazy }
/// caption
Its chunks in document order, one of them open: the green start is the text it shares with the chunk before it.
///

??? more "The steps, the chunks and the metadata on a file's page"

    The steps: **Stored in S3** (its size, type and when it last changed), **Read by the parser** (the default parser reads the text only; a foundation model or Data Automation parser also reads tables, charts and scans), **Cut into chunks** (how many, how big, from which pages, and the data source's chunking), **Embedded** (the model and its dimensions), **Stored as vectors** (the vector store and its index) and where it stands **Now**.

    Below the steps come **its chunks**, read from the vector store itself (a search limited to the file), in the order they come in the document. The bar shows one block per chunk, as wide as its text, with tiny ones in amber; for a `.txt` or `.md` file, a second bar shows where each chunk sits in the file, and what no chunk holds. Each chunk is a line with its page, its size in tokens and words, and its first words; click it for its full text and its metadata. With fixed-size or hierarchical chunking, each chunk starts with the end of the one before it: that overlap is marked in green, and the line says how many characters it repeats.

    Last comes **its metadata**: the attributes in its `.metadata.json` next to the ones its chunks carry (what `where=` filters match), with what's wrong with the file when there is something: JSON that doesn't parse, a name Bedrock keeps for itself, an attribute its chunks don't have yet because the file changed after the last sync. The JSON as written, and Bedrock's record and the S3 object, are folded underneath.

A file that **failed** shows the step that failed in red, and the finding at the top says what usually fixes Bedrock's reason.

![The Failed chip picked, with catalogue-2019.pdf and scanned-invoice.pdf listed, and scanned-invoice.pdf's page: its state Failed, no chunks, a warning that Bedrock couldn't index it because it's a scanned image with no text layer, that a data source with a foundation model or Data Automation parser reads it, and the sync command; How it was indexed with Read by the parser in red](images/kb-explorer-failed-light.webp#only-light){ width="984" height="846" loading=lazy }
![The Failed chip picked, with catalogue-2019.pdf and scanned-invoice.pdf listed, and scanned-invoice.pdf's page: its state Failed, no chunks, a warning that Bedrock couldn't index it because it's a scanned image with no text layer, that a data source with a foundation model or Data Automation parser reads it, and the sync command; How it was indexed with Read by the parser in red](images/kb-explorer-failed-dark.webp#only-dark){ width="984" height="846" loading=lazy }
/// caption
A failed file: Bedrock's reason, what usually fixes it, and the step that failed.
///

## Does a question find this file? { #explorer-ask }

Type a question on a file's page to see where the file ranks for it. A file that holds the answer but ranks #6 is never used, because an answer only sees the top 5.
{ .gist }

![warranty.pdf asked How long is the warranty?: cards for its 5 passages, a best score of 0.84, rank #6 in the knowledge base in amber, the time and the estimated cost of the two searches; a warning that its best passage ranks #6, so an answer that gets 5 passages won't see it because refund-policy.pdf and shipping-times.pdf rank higher, and to ask for 6 passages or narrow the search; then the file's passages with the word warranty highlighted](images/kb-explorer-ask-light.webp#only-light){ width="984" height="846" loading=lazy }
![warranty.pdf asked How long is the warranty?: cards for its 5 passages, a best score of 0.84, rank #6 in the knowledge base in amber, the time and the estimated cost of the two searches; a warning that its best passage ranks #6, so an answer that gets 5 passages won't see it because refund-policy.pdf and shipping-times.pdf rank higher, and to ask for 6 passages or narrow the search; then the file's passages with the word warranty highlighted](images/kb-explorer-ask-dark.webp#only-dark){ width="984" height="846" loading=lazy }
/// caption
The file holds the answer, but five passages of other files rank above it.
///

## Search { #explorer-search }

The **Search** tab asks the whole knowledge base a question: its passages, best first, with their scores and the question's words highlighted.
{ .gist }

Pick how many passages, the search type (**Semantic** matches meaning, **Hybrid** also exact words such as error codes), a data source and a reranker. Type a metadata filter under them, like `team = billing; year >= 2024`, to search only the files it matches (see [Filter by metadata](bedrock_kb.md#filters)). **How payment-errors.md was indexed ›** under a passage opens its file's page in the Files tab.

![The Search tab: What does error E1234 mean? in the search box beside Search and Compare, the options (5 passages, Default, Semantic or Hybrid search, every data source, no reranker), an empty metadata filter box, cards for 5 passages, the top score, 4 files, the time and the cost, a note that scores are relative, and the passages: payment-errors.md twice with Error, E1234 and means highlighted, then digital-goods.pdf, each with a How it was indexed button](images/kb-explorer-search-light.webp#only-light){ width="984" height="846" loading=lazy }
![The Search tab: What does error E1234 mean? in the search box beside Search and Compare, the options (5 passages, Default, Semantic or Hybrid search, every data source, no reranker), an empty metadata filter box, cards for 5 passages, the top score, 4 files, the time and the cost, a note that scores are relative, and the passages: payment-errors.md twice with Error, E1234 and means highlighted, then digital-goods.pdf, each with a How it was indexed button](images/kb-explorer-search-dark.webp#only-dark){ width="984" height="846" loading=lazy }
/// caption
**Search**: each passage's **How … was indexed ›** opens its file's page.
///

![The Search tab with the metadata filter team = billing; year >= 2024: How long do refunds take? finds 5 passages, the subtitle saying where team = 'billing', year >= 2024, and each passage shows its metadata, team=billing and year=2024 or 2025](images/kb-explorer-filter-light.webp#only-light){ width="984" height="846" loading=lazy }
![The Search tab with the metadata filter team = billing; year >= 2024: How long do refunds take? finds 5 passages, the subtitle saying where team = 'billing', year >= 2024, and each passage shows its metadata, team=billing and year=2024 or 2025](images/kb-explorer-filter-dark.webp#only-dark){ width="984" height="846" loading=lazy }
/// caption
A **metadata filter** under the options: only the files whose `.metadata.json` matches.
///

**Compare**, beside **Search**, runs the question as Semantic and as Hybrid, with the passages picked and twice as many, and shows each passage's rank under each: the report [`compare()`](bedrock_kb.md#decide) draws, with the window's data source and filter.

![Compare for How long do refunds take? with the filter team = billing, scrolled to its findings (10 passages add gift-cards.md; Semantic and Hybrid return the same passages in the same order) and the table of each passage's rank under Semantic and Hybrid at 5 and 10 passages: refund-policy.pdf first under every setting, gift-cards.md found only at 10](images/kb-explorer-compare-light.webp#only-light){ width="984" height="846" loading=lazy }
![Compare for How long do refunds take? with the filter team = billing, scrolled to its findings (10 passages add gift-cards.md; Semantic and Hybrid return the same passages in the same order) and the table of each passage's rank under Semantic and Hybrid at 5 and 10 passages: refund-policy.pdf first under every setting, gift-cards.md found only at 10](images/kb-explorer-compare-dark.webp#only-dark){ width="984" height="846" loading=lazy }
/// caption
**Compare**: where each passage ranks with each search type and number of passages.
///

## Syncs and settings { #explorer-syncs }

The **Syncs** tab is the sync history as a timeline; **Settings** is every setting in plain English.
{ .gist }

- **Syncs** is the sync history as a timeline, newest first: which data source, when it ran and how long it took, what it read, added, changed, deleted and couldn't index, and why a sync failed.
- **Settings** has every setting in plain English: the embedding model and its dimensions, the vector store and which of its fields holds each part of a chunk, each data source's location, chunking, parser and deletion policy, and the tags. GetKnowledgeBase and GetDataSource as AWS returns them are folded at the end, with the AWS CLI commands that read them.

![The Syncs tab: cards for 6 syncs, 1 failed, the last successful one a day ago and documents failed; a warning that the last sync of docs-s3 couldn't index 2 documents and that the Files tab's Failed filter shows them; then a timeline of the six syncs, green for done, amber for the one that couldn't parse 2 documents, red for the one that failed because the role wasn't allowed s3:GetObject; and the sync commands to copy](images/kb-explorer-syncs-light.webp#only-light){ width="984" height="846" loading=lazy }
![The Syncs tab: cards for 6 syncs, 1 failed, the last successful one a day ago and documents failed; a warning that the last sync of docs-s3 couldn't index 2 documents and that the Files tab's Failed filter shows them; then a timeline of the six syncs, green for done, amber for the one that couldn't parse 2 documents, red for the one that failed because the role wasn't allowed s3:GetObject; and the sync commands to copy](images/kb-explorer-syncs-dark.webp#only-dark){ width="984" height="846" loading=lazy }
/// caption
**Syncs**: the history as a timeline, with why a sync failed.
///

<p class="part"><strong>That's the guide.</strong> Everything below is reference for when you need it: driving the window from code, and every command.</p>

## From code { #from-code }

The window is an object you can drive: what a click does, a command does too, and what's shown is data you can read.
{ .gist }

```python
x = ui.explore("support-docs")
x.file("warranty.pdf")                                   # a file's page: its name, path in the bucket or s3:// path
x.search("How long do refunds take?", where="team = billing")   # the Search tab, with a metadata filter
x.compare("How long do refunds take?")                   # Semantic against Hybrid, as its Compare button does
x.open("another-kb")                                     # another knowledge base
x.refresh()                                              # read it again (the ↻ button)
```

Everything is read in the background: a click shows what's known at once and fills in the rest as it arrives, and the window keeps answering meanwhile. `explore()` returns the window: `x.inventory`, `x.info` and `x.chunks` hold the data behind what's shown, `x.focus` is what you're looking at (the knowledge base, the tab and the file or search open), `x.file("faq.md")`, `x.search("...")` and `x.open("another-kb")` do what the clicks do, and `x.ui` is a `BedrockKBView` for reports in other cells. With `explore(..., assistant=AgentView(...))` (or `x.attach(agent)`), **✦ Ask** at the end of the tab bar opens the agent's chat beside the tabs, asking about the file you have open ([the agent's guide](agent.md#ask-drawer)).

## Command reference { #reference }

<div class="ref" markdown>

| Command | What it does |
|---|---|
| `explore(kb=None, file=None, region=None, profile=None, height=None, assistant=None)` | Opens the window and returns it; `ui.explore(...)` on a `BedrockKBView` opens it on its knowledge base |
| `x.open(kb)` | Shows another knowledge base (a name, ID or ARN) |
| `x.file(path)` | Opens a file in the Files tab: how it was indexed, its chunks and its metadata |
| `x.search(question, where=None)` | Searches the knowledge base in the Search tab; `where=` types a metadata filter (`"team = billing; year >= 2024"`) |
| `x.compare(question, where=None)` | Compares Semantic and Hybrid search, with the passages picked and twice as many |
| `x.refresh()` | Reads the knowledge base again: settings, files, syncs (the ↻ button) |

</div>
