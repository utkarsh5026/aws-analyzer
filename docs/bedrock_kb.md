---
title: Bedrock Knowledge Bases Analyzer Guide
description: "How to check, search and ask Amazon Bedrock Knowledge Bases from a SageMaker notebook with aws-analyzer's bedrock_kb.py, with examples."
---

<p class="eyebrow"><img class="aws-icon" src="images/aws/bedrock.svg" alt="" width="32" height="32"> Amazon Bedrock Knowledge Bases <code>bedrock_kb.py</code></p>

# Find out why your knowledge base answered that

See which files your knowledge base failed to index, what a question really retrieves, and how well each answer is backed by its sources. Or open the explorer window and see it all by clicking.
{ .lede }

<ul class="pills">
  <li>One install, nothing to set up</li>
  <li>Read-only: never starts a sync</li>
  <li>Any Bedrock model</li>
  <li>An explorer window: click, don't type</li>
  <li>Plain text outside Jupyter</li>
</ul>

The examples use `support-docs`, a knowledge base of support policies in the bucket `support-docs-bucket`, some tagged with a `team` and a `year`. The screenshots are the tool's real output on a simulated Bedrock, so scores, answers and times are illustrative.
{ .muted }

## Watch it run { #video }

Six calls in a notebook, from every knowledge base to an answer and its sources: each call typed and run, and the report it draws, with what to notice. It plays as you scroll to it; click it to pause, or pick a chapter to jump to a call.
{ .gist }

<div class="aa-tour" data-tour="bedrock_kb">
  <div class="tour-screen"></div>
  <div class="tour-controls"></div>
  <div class="tour-chapters"></div>
  <p class="tour-links"><a href="tour.html?service=bedrock_kb">Full screen</a> · <a href="videos/bedrock_kb-tour-light.mp4" data-tour-mp4 download>Download as MP4</a>, for Slack or a slide (light or dark, as this page is)</p>
</div>

## Set up in SageMaker { #setup }

<div class="steps" markdown>

1. **Install it** in a notebook cell:

    ```bash
    %pip install aws-analyzer
    ```

2. **Make the view.** It uses the notebook's IAM role and region, so there's nothing to configure.

    ```python
    from aws_analyzer import BedrockKBView

    ui = BedrockKBView()
    ui.help()              # every command, grouped by task; ui.help("ask") shows one in full
    ```

</div>

??? more "No internet in the notebook (VPC-only mode)?"

    On a computer that has internet, download the package and copy it to a bucket the notebook can read (`pip download aws-analyzer --no-deps -d wheels`, then `aws s3 cp --recursive wheels/ s3://acme-ml-data/tools/wheels/`), and install it from there:

    ```bash
    !aws s3 cp --recursive s3://acme-ml-data/tools/wheels/ wheels/
    %pip install --no-index --find-links wheels aws-analyzer
    ```

??? more "Another region or profile, a default knowledge base, plain text"

    Knowledge bases are regional, so if `kbs()` comes back empty, check the region first. Only boto3 is required: answers are generated through Bedrock itself, so any model enabled in your account works.

    ```python
    from aws_analyzer import BedrockKBAnalyzer, BedrockKBView

    ui = BedrockKBView(BedrockKBAnalyzer(region="us-west-2", profile="dev"))
    ui = BedrockKBView(kb="support-docs")    # commands use this knowledge base unless given another
    ui = BedrockKBView(mode="text")          # plain text, e.g. in a terminal or a script
    ui = BedrockKBView(progress="plain")     # a plain progress line instead of tqdm bars ("off": none)
    ```

## Start here { #tour }

Six questions people bring to a knowledge base, and the one line that answers each. None of them change anything.
{ .gist }

<div class="quick" markdown>

Is it healthy?
{ .qgroup }

-   [Are my knowledge bases healthy?](#kbs) `ui.kbs()`
-   [Which files failed or aren't synced yet?](#files) `ui.files(status="failed")`

Does it answer well?
{ .qgroup }

-   [What does a question retrieve?](#search) `ui.search("How long do refunds take?")`{ .long }
-   [What's the answer, and what backs it up?](#ask) `ui.ask("How long do refunds take?")`{ .long }
-   [Does retrieval find the right file?](#decide) `ui.evaluate([("refund window?", "refund-policy.pdf")])`{ .long }

Rather click?
{ .qgroup }

-   [I'd rather click through it all.](#explorer) `ui.explore("support-docs")`

</div>

After `ui.use("support-docs")`, commands use that knowledge base: a name in any case, its ID or its ARN.

??? more "How to read a report"

    Every report puts the answer first: cards with the numbers that matter (a card turns amber or red when a finding is about it), then the findings, warnings first, each a bold headline with why it matters and what to do underneath. The tables of detail come after.

    - **Sort** by clicking a column's header (again for the other way, a third time for the original order).
    - **Filter** a column with a few repeated values (a status, a data source) to see only those rows.
    - **Columns** lets you untick the columns you don't need.
    - **Next** at the bottom lists two or three commands with the arguments filled in, such as `chunk(1)`.

    One click on a command anywhere in a report, or on a code block, selects all of it, ready to copy. Without `use()` or `kb=`, commands use the only knowledge base in the region, else list the ones there and say how to pick.

## The explorer window { #explorer }

Rather click than type? `explore()` opens a window on a knowledge base: every file, whether it's searchable, and how it was indexed.
{ .gist }

```python
ui.explore("support-docs")                         # a name, ID or ARN
ui.explore("support-docs", file="warranty.pdf")    # straight to one file's page
```

It puts every file of the S3 data sources next to Bedrock's record of it, with each file's chunks, the syncs, a search box and every setting a click away. The [knowledge base explorer guide](bedrock_kb_explorer.md) covers every tab, with its own video tour.

## Your knowledge bases { #kbs }

### Every knowledge base at a glance

`kbs()` lists every knowledge base in the region with its last sync, what its vector store costs while idle, and what's wrong.
{ .gist }

```python
ui.kbs()
```

![kbs(): four knowledge bases with status, type, vector store, embedding model, data sources, documents, last sync and estimated idle cost, then one warning each: a data source that doesn't chunk, a failed knowledge base and its failed sync, a data source never synced, and a sync that left 2 documents unindexed](images/bedrock-kbs-light.webp#only-light){ width="984" height="1070" loading=lazy }
![kbs(): four knowledge bases with status, type, vector store, embedding model, data sources, documents, last sync and estimated idle cost, then one warning each: a data source that doesn't chunk, a failed knowledge base and its failed sync, a data source never synced, and a sync that left 2 documents unindexed](images/bedrock-kbs-dark.webp#only-dark){ width="984" height="1070" loading=lazy }
/// caption
`ui.kbs()`: every knowledge base here has something to look at. The idle cost is estimated for OpenSearch Serverless only; the other vector stores show “-”.
///

### One knowledge base in detail

`kb_info()` explains one knowledge base's settings in plain English, after the findings that say what to fix.
{ .gist }

??? more "How the settings read"

    - **Vector store:** for example “OpenSearch Serverless collection abc123, index kb-index”.
    - **Chunking:** “Fixed size: 300 tokens per chunk, 20% overlap”, “Hierarchical: 1,500-token parents, 300-token children, 60-token overlap”, “Semantic: up to 300 tokens, split where the topic changes” or “None: each file is one chunk”.
    - **Parsing:** the default parser reads the text only; a foundation model or Bedrock Data Automation also reads tables, charts and images.
    - **When deleted:** whether a data source's chunks are removed with it (`DELETE`) or stay in the vector store and keep turning up in answers (`RETAIN`).

```python
ui.kb_info("support-docs")
ui.kb_info()                                 # the default knowledge base
```

![kb_info(): cards for status, type, vector store, embedding model, data sources, last sync, idle cost and creation date; findings about 2 documents that failed to index, a data source that keeps its chunks when deleted, and the idle cost of OpenSearch Serverless; then the settings, two data sources with their chunking, parsing and deletion policy in plain English and the data_source= that asks each, the recent syncs and the tags](images/bedrock-kb-info-light.webp#only-light){ width="984" height="1547" loading=lazy }
![kb_info(): cards for status, type, vector store, embedding model, data sources, last sync, idle cost and creation date; findings about 2 documents that failed to index, a data source that keeps its chunks when deleted, and the idle cost of OpenSearch Serverless; then the settings, two data sources with their chunking, parsing and deletion policy in plain English and the data_source= that asks each, the recent syncs and the tags](images/bedrock-kb-info-dark.webp#only-dark){ width="984" height="1547" loading=lazy }
/// caption
`ui.kb_info("support-docs")`: the findings come first, each with what to do next. Below them, each data source's chunking, parsing and deletion policy in plain English.
///

Its findings catch data sources never synced, a sync that failed or left documents out, no metadata files (so `where=` filters match nothing), chunking that's off, and what an OpenSearch Serverless store costs while idle.

## Syncs and documents { #syncs }

A knowledge base only knows what its last sync indexed. These commands show how far that is from the files you have.
{ .gist }

None of them starts a sync: where one is needed, they show the command to run.

```python
ui.syncs()                                   # sync history: counts, how long, why it failed
ui.syncs(data_source="docs-s3", n=30)        # one data source, further back
ui.documents(status="FAILED")                # documents that failed to index, with the reason
ui.unsynced()                                # S3 files added or changed since the last sync
```

??? more "What each command shows"

    - `syncs()` lists each sync with when it started, how long it took, its status, and how many documents it scanned, added, re-indexed, removed and failed on, with the reason a sync failed. Findings group repeated failures and their reasons: when the last three syncs failed the same way, syncing again won't help until the cause is fixed.
    - `documents()` counts documents by status (indexed, failed, pending ...) and lists the ones that aren't indexed with Bedrock's reason, such as an encrypted PDF or a file that's too large. Bedrock keeps document status for S3 and custom data sources only; for the others, `syncs()` has the failed counts.
    - `unsynced()` lists the S3 bucket (and the data source's inclusion prefixes) and compares each file's modification time with the start of the last successful sync. Metadata files are counted apart: a changed `.metadata.json` needs a sync too, before filters see the new values.

    ```bash title="What a sync looks like"
    aws bedrock-agent start-ingestion-job --knowledge-base-id KBID123456 --data-source-id DSID123456 --region us-east-1
    ```

![syncs(): six syncs with when each started, how long it took, its status and document counts; one failed because the knowledge base's role wasn't allowed s3:GetObject, and one couldn't parse 2 documents; then the start-ingestion-job command and boto3 call for each data source](images/bedrock-syncs-light.webp#only-light){ width="984" height="836" loading=lazy }
![syncs(): six syncs with when each started, how long it took, its status and document counts; one failed because the knowledge base's role wasn't allowed s3:GetObject, and one couldn't parse 2 documents; then the start-ingestion-job command and boto3 call for each data source](images/bedrock-syncs-dark.webp#only-dark){ width="984" height="836" loading=lazy }
/// caption
`ui.syncs("support-docs")`: why a sync failed, in the same row, and the command that starts the next one. You run it; the tool never does.
///

![documents(): 42 documents read, of which 39 indexed, 2 failed and 1 ignored; a note that the web data source keeps no document status, a warning with the most common reason and the sync command, and a table of the three documents that aren't indexed with Bedrock's reason: an encrypted PDF, a scanned image with no text layer and an unsupported .mp4](images/bedrock-documents-light.webp#only-light){ width="984" height="651" loading=lazy }
![documents(): 42 documents read, of which 39 indexed, 2 failed and 1 ignored; a note that the web data source keeps no document status, a warning with the most common reason and the sync command, and a table of the three documents that aren't indexed with Bedrock's reason: an encrypted PDF, a scanned image with no text layer and an unsupported .mp4](images/bedrock-documents-dark.webp#only-dark){ width="984" height="651" loading=lazy }
/// caption
`ui.documents("support-docs")`: without `status=` it lists only the documents that aren't indexed, with Bedrock's reason for each.
///

![unsynced(): 43 files checked, 2 changed since the last sync: refund-policy.pdf changed 5 hours ago and holiday-shipping.md a day ago, a note that the web data source can't be listed, and the sync command](images/bedrock-unsynced-light.webp#only-light){ width="984" height="647" loading=lazy }
![unsynced(): 43 files checked, 2 changed since the last sync: refund-policy.pdf changed 5 hours ago and holiday-shipping.md a day ago, a note that the web data source can't be listed, and the sync command](images/bedrock-unsynced-dark.webp#only-dark){ width="984" height="647" loading=lazy }
/// caption
`ui.unsynced("support-docs")`: two files changed after the last sync, so searches and answers don't see those changes yet.
///

### Every file, as reports { #files }

`files()` catches what Bedrock's own document list can't: files added after the last sync, files the sync skipped without a record, and deleted files answers can still cite.
{ .gist }

The explorer's Files tab is these three commands underneath:

```python
ui.files()                                   # every file next to Bedrock's record of it, problems first
ui.files(status="failed")                    # only these: failed, changed, new (not synced yet), skipped, deleted...
ui.files(match="refund")                     # files whose name, path or reason holds this
ui.file("refund-policy.pdf")                 # how one file was indexed: steps, chunks in order, metadata, what to fix
ui.file("s3://support-docs-bucket/policies/refund-policy.pdf")   # a name, its path in the bucket, or its s3:// path
ui.search_file("refund-policy.pdf", "How long do refunds take?")  # does the question find it, and where it ranks
```

It ends with the command that syncs each data source a sync would change. A name that several files share asks which one you mean, with their paths.

### The list of files, as a text file { #file-list }

`save_file_list()` saves the files' paths in a `.txt` file, one per line, with a **⬇ Download** link to save it on your computer.
{ .gist }

It takes the same filters as `files()`, so it can be just the failed files, or the files of one folder or data source:

```python
ui.save_file_list()                          # support-docs-files.txt: every file's s3:// path, one per line
ui.save_file_list(status="failed")           # support-docs-failed-files.txt: only the failed ones
ui.save_file_list(match="policies/")         # files whose name or path holds this
ui.save_file_list(path="lists/docs.txt")     # somewhere else (a folder puts support-docs-files.txt in it)
ui.save_file_list(link="key")                # each file's path in the bucket: policies/refund-policy.pdf
ui.save_file_list(link="https")              # a link that opens each file in a browser, for an hour
ui.save_file_list(link="https", expires=86400)   # ... for a day (7 days at most)
```

```text title="support-docs-files.txt"
s3://support-docs-bucket/faq/account-faq.md
s3://support-docs-bucket/faq/answer-00.md
s3://support-docs-bucket/faq/answer-01.md
...
```

!!! tip "Worth knowing"

    The `https` links are presigned in the notebook with your credentials (no AWS call), so anyone holding the file can open every file in it until they expire: share it with care. A file of the same name is replaced. Nothing in AWS changes: the list is the only thing these commands write.

## Search { #search }

`search()` shows the passages a question retrieves, best first, with the question's words highlighted. Check this before you blame the model for an answer.
{ .gist }

```python
ui.search("How long do refunds take?")
ui.search("How long do refunds take?", n=10)              # up to 100 passages
ui.search("error E1234", search_type="HYBRID")            # meaning and keywords, where the store supports it
ui.search("refund window", rerank=True)                   # re-order with a reranking model (Cohere Rerank 3.5)
ui.search("refund window", data_source="faq")             # one data source only (see below)
ui.chunk(2)                                               # result #2 in full, with its metadata and IDs
ui.link(2)                                                # a link that opens result #2's file in a new tab
```

![search(): five passages for How long do refunds take?, each with its file, page, score bar, metadata such as team=billing and year=2024, and the question's words highlighted; cards for passages, top score, files, time and the estimated cost of the question embedding](images/bedrock-search-light.webp#only-light){ width="984" height="838" loading=lazy }
![search(): five passages for How long do refunds take?, each with its file, page, score bar, metadata such as team=billing and year=2024, and the question's words highlighted; cards for passages, top score, files, time and the estimated cost of the question embedding](images/bedrock-search-dark.webp#only-dark){ width="984" height="838" loading=lazy }
/// caption
`ui.search("How long do refunds take?")`: two passages from the refund policy, then three weaker ones. The words from the question are highlighted.
///

Scores are relative: compare them with each other, not against a fixed cutoff. The findings say what to try next:

- **Nothing came back:** check the filter, whether the data sources are synced, or try a larger `n=`.
- **A code in the question is in no passage** (an error code, a SKU): semantic search matches meaning, not exact strings; try `search_type="HYBRID"`.
- **Every passage comes from one file**, **passages repeat each other word for word** (duplicate files), or **most passages are very short** (chunking too small).

??? more "A passage in full, and opening its file"

    `chunk(n)` shows result *n* of the last search (or source *n* of the last answer) in full, with its metadata, chunk ID and data source, a link that opens its file, and the `S3View().preview("s3://…")` call that previews the whole file in the notebook with [s3.py](s3.md).

    **Open a source's file.** Each file name in a search, an answer's sources, `compare()`, `documents()` and `unsynced()` is a link (↗) that opens the file in a new browser tab, a PDF at the passage's page, so you can check what a passage says in context. For a file in S3 it's a presigned link: signed in the notebook with your credentials (no AWS call), it works for an hour and only if those credentials may read the file (`s3:GetObject`). The browser shows PDFs, pictures, text and HTML; Word and Excel files download. Web, Confluence, SharePoint and Salesforce sources link to their page. `link(n)` gives the link on its own: in text mode, where reports don't show links, or for longer than an hour (`link(2, expires=86400)`, up to 7 days). `link("refund-policy.pdf")` and `link("s3://bucket/key")` work too. Anyone you send a link to can open the file until it expires.

## Ask { #ask }

`ask()` answers from the knowledge base with `[1][2]` after each claim, and says how much of the answer its sources back up ("grounded").
{ .gist }

```python
ui.ask("How long do refunds take?")
ui.follow_up("And for digital goods?")                     # same session
ui.ask("How long do refunds take?", model="sonnet", n=10)
ui.ask("How long do refunds take?", engine="converse")     # exact tokens and cost
ui.ask("How long do refunds take?", data_source="faq")     # from one data source only
ui.chunk(1)                                                # source [1] in full
```

![ask(): an answer about refund times with a \[1\] and a \[2\] marker, 77% grounded because its last sentence cites nothing, cards for sources used, model, estimated tokens, cost and time, and the two cited passages from refund-policy.pdf](images/bedrock-ask-light.webp#only-light){ width="984" height="605" loading=lazy }
![ask(): an answer about refund times with a \[1\] and a \[2\] marker, 77% grounded because its last sentence cites nothing, cards for sources used, model, estimated tokens, cost and time, and the two cited passages from refund-policy.pdf](images/bedrock-ask-dark.webp#only-dark){ width="984" height="605" loading=lazy }
/// caption
`ui.ask("How long do refunds take?")`: cited spans are shaded and link to their source. The last sentence cites nothing, so the answer is 77% grounded. Tokens are an estimate here, because RetrieveAndGenerate doesn't report them.
///

Its findings flag an answer that cites nothing (it may be the model's own knowledge), one that's less than half backed by citations, and Bedrock's "unable to assist" reply (the passages didn't hold the answer: run `search()` on the question).

### Two ways to generate

The default lets Bedrock do it all; `engine="converse"` gives you exact tokens, any model and your own prompt.
{ .gist }

<div class="grid cards" markdown>

- **`engine="kb"` (default)**

    Bedrock's managed RetrieveAndGenerate: it retrieves, prompts the model and returns the citations, and `follow_up()` keeps its session. It doesn't report tokens, so tokens and cost are estimated from characters. A custom `prompt=` must contain `$search_results$`.

- **`engine="converse"`**

    Retrieve, then the model through Bedrock Converse: exact token counts and cost, any model, and your own `prompt=` with `{sources}` and `{question}`. The passages go in as numbered sources the model must cite as `[n]`.

</div>

![ask() with engine=converse and model=sonnet: the same answer, 100% grounded, with exact input and output tokens, and all five retrieved passages listed with a Cited column showing the model used the first two](images/bedrock-ask-converse-light.webp#only-light){ width="984" height="617" loading=lazy }
![ask() with engine=converse and model=sonnet: the same answer, 100% grounded, with exact input and output tokens, and all five retrieved passages listed with a Cited column showing the model used the first two](images/bedrock-ask-converse-dark.webp#only-dark){ width="984" height="617" loading=lazy }
/// caption
`ui.ask("How long do refunds take?", engine="converse", model="sonnet")`: exact token counts, and the Cited column shows which of the passages the model used.
///

??? more "Your own prompt, and how sources are sent"

    With `engine="converse"`, the sources are sent as data, never as instructions (a document that says “ignore your instructions” is just text), and the model is told to say plainly when they don't hold the answer. `bedrock_kb.DEFAULT_PROMPT` is the template it uses; copy it to write your own.

    ```python
    from aws_analyzer import bedrock_kb
    print(bedrock_kb.DEFAULT_PROMPT)

    MY_PROMPT = """Sources:
    {sources}

    Answer in two sentences for a customer, citing sources as [n]: {question}"""
    ui.ask("How long do refunds take?", engine="converse", prompt=MY_PROMPT)
    ```

    An answer the model wrote in markdown is laid out as such: lists, **bold**, tables and code blocks, with the cited spans still shaded inside them. Nothing in it runs as HTML, and its links open only web pages and email addresses.

### Choosing a model

`model=` takes a short name like `"sonnet"` or `"nova-pro"`, and `models()` lists every model you can use here with its price.
{ .gist }

It also takes a model ID or ARN, or an inference profile ID. The default is Claude Haiku 4.5, called through the region's inference profile when it can't be called on demand.

```python
ui.models()
ui.models("claude")
ui = BedrockKBView(BedrockKBAnalyzer(default_model="sonnet"))   # a different default
```

![models(): nine text models with the ID to pass as model=, name, provider, whether it's called on demand or through an inference profile, and the price per million input and output tokens; the default for ask() is us.anthropic.claude-haiku-4-5-20251001-v1:0](images/bedrock-models-light.webp#only-light){ width="984" height="635" loading=lazy }
![models(): nine text models with the ID to pass as model=, name, provider, whether it's called on demand or through an inference profile, and the price per million input and output tokens; the default for ask() is us.anthropic.claude-haiku-4-5-20251001-v1:0](images/bedrock-models-dark.webp#only-dark){ width="984" height="635" loading=lazy }
/// caption
`ui.models()`: the ID to pass as `model=`, how each model is called, and what it costs per million tokens.
///

## Ask one data source { #data-source }

`data_source=` points a question at one of a knowledge base's data sources, with no metadata files needed.
{ .gist }

`search`, `ask`, `compare` and `evaluate` take its name (in any case) or its ID, or a list of them, and `follow_up()` keeps it for the rest of the conversation:

```python
ui.search("refund window", data_source="faq")                   # passages from the faq data source only
ui.ask("How long do refunds take?", data_source="policies")     # an answer from the policies only
ui.follow_up("And on the help site?", data_source="help-site")  # the next question searches another one
ui.follow_up("Anything else?", data_source="all")               # back to every data source
```

??? more "How it works, and when passages come from several data sources"

    Bedrock tags every chunk with its data source's ID (`x-amz-bedrock-kb-data-source-id`), so this needs no metadata files, and it works together with `where=`: both must match. `kb_info()` lists the data sources, with the `data_source=` for each when there are several. A name that isn't one of them is answered with the names that are.

    When the passages of a search or an answer come from several data sources, the report says which each came from and suggests asking the one most of them came from. If a vector store returns passages from another data source anyway, a finding says so: tag the files with your own metadata then, and filter with `where=`.

## Filter by metadata { #filters }

`where=` filters on the documents' own metadata, from a `<file>.metadata.json` next to each file in S3, with a plain dict.
{ .gist }

```json title="refund-policy.pdf.metadata.json"
{"metadataAttributes": {"team": "billing", "year": 2024}}
```

```python
ui.search("error E1234", where={"team": "billing", "year": (">=", 2024)})
```

??? more "Every condition `where=` takes, and OR"

    It works on `search`, `ask`, `compare` and `evaluate`, and every condition must match:

    | `where=` | Means |
    |---|---|
    | `{"team": "billing"}` | `team` equals `"billing"` |
    | `{"team": ["billing", "support"]}` | Any of these values |
    | `{"year": (">=", 2024)}` | Also `"="`, `"!="`, `">"`, `"<"` and `"<="` |
    | `{"year": ("between", 2020, 2024)}` | Both ends included |
    | `{"region": ("in", ["eu", "uk"])}` | Also `("not_in", [...])` |
    | `{"doc_id": ("begins_with", "POL-")}` | Text starting with this |
    | `{"title": ("contains", "refund")}` | Text containing this, or a list with an element containing it |
    | `{"tags": ("list_contains", "gdpr")}` | A list attribute holding exactly this element |

    The same can be typed as text, as in the explorer's Search tab: `where="team = billing; year >= 2024"`, `"region in eu, uk"`, `"year between 2020 and 2024"`, `"doc_id begins with POL-"`, `"title contains refund"` or `"tags has gdpr"`, with `;`, `,` or `and` between conditions and quotes around a value that holds them. `parse_where` gives the dict.

    Values are typed: `2024` and `"2024"` are different. It's the same vocabulary as the DynamoDB analyzer's filters. For `OR`, pass a Bedrock `RetrievalFilter` instead, and it's sent unchanged:

    ```python
    ui.search("refund window", where={"orAll": [{"equals": {"key": "team", "value": "billing"}},
                                                {"equals": {"key": "team", "value": "support"}}]})
    ```

## Compare and evaluate { #decide }

### Which search settings suit a question

`compare()` runs one question with several search settings and shows what each found that the others missed.
{ .gist }

One row per passage, with its rank under every setting. The findings say what changed, for example “HYBRID found 2 passages SEMANTIC missed at n=5, including its top result”.

```python
ui.compare("refund window for EU orders")                       # SEMANTIC and HYBRID, n=5 and n=10
ui.compare("error E1234", n=10, search_types=("SEMANTIC", "HYBRID"))
```

![compare(): four settings, SEMANTIC and HYBRID at n=2 and n=5, with their overlap in cards; findings that n=5 adds three passages and that HYBRID ranks the passage about error E1234 first where SEMANTIC ranks it second; a table with each passage's rank under each setting](images/bedrock-compare-light.webp#only-light){ width="984" height="855" loading=lazy }
![compare(): four settings, SEMANTIC and HYBRID at n=2 and n=5, with their overlap in cards; findings that n=5 adds three passages and that HYBRID ranks the passage about error E1234 first where SEMANTIC ranks it second; a table with each passage's rank under each setting](images/bedrock-compare-dark.webp#only-dark){ width="984" height="855" loading=lazy }
/// caption
`ui.compare("what does error E1234 mean?", n=(2, 5))`: both search types find the same passages, but only HYBRID puts the one with the exact error code first.
///

### How often retrieval finds the right file

`evaluate()` scores retrieval on your own test questions: how often the right file comes up, and how high.
{ .gist }

Give each question the source it should find (part of its file name). The hit rate is the share whose source came up in the top `n`; the MRR is 1.00 when it's always first, 0.50 when it's second on average. It only retrieves, so it costs a question embedding per question.

```python
cases = [
    ("How long do refunds take?", "refund-policy.pdf"),
    ("How do I reset my password?", "account-faq"),
    ("Can EU customers return an order?", "eu-returns.pdf"),
]
ui.evaluate(cases)
ui.evaluate(cases, n=10, search_type="HYBRID")          # did that help?
ui.evaluate(df)                                        # or a DataFrame with question and expected columns
```

![evaluate(): six questions, hit rate 83% and MRR 0.72; a warning that When do holiday orders ship? missed holiday-shipping, a note that one question found its source third, and a table of each question, its expected source, its rank and what came up first](images/bedrock-evaluate-light.webp#only-light){ width="984" height="821" loading=lazy }
![evaluate(): six questions, hit rate 83% and MRR 0.72; a warning that When do holiday orders ship? missed holiday-shipping, a note that one question found its source third, and a table of each question, its expected source, its rank and what came up first](images/bedrock-evaluate-dark.webp#only-dark){ width="984" height="821" loading=lazy }
/// caption
`ui.evaluate(cases)`: the miss is `holiday-shipping.md`, which was added after the last sync. The `unsynced()` screenshot above shows it.
///

Findings list the questions that missed, a file that keeps coming up first instead, and the usual fixes: `search_type="HYBRID"` for codes and names, a larger `n=`, smaller chunks (a new data source), or `where=` filters.

<p class="part"><strong>That's the guide.</strong> Everything below is reference for when you need it: the data in Python, cost, permissions, troubleshooting and every command.</p>

## Use the data in Python { #python }

Every report has a data version on `ui.core` (a `BedrockKBAnalyzer`) that returns dataclasses and DataFrames instead of printing. Its methods take the knowledge base first.

```python
kb = ui.core

info = kb.describe("support-docs")                # KnowledgeBaseInfo: settings, data sources, last syncs, tags
info.data_sources[0].chunking                     # chunkingConfiguration, as AWS returns it
kb.ingestion_jobs("support-docs", n=20)           # [IngestionJob], newest first, with failure reasons
docs, summary = kb.documents("support-docs", status="FAILED")

r = kb.retrieve("support-docs", "refund window", n=10, where={"team": "billing"})   # Retrieval
r.passages[0].text, r.passages[0].source, r.passages[0].metadata
df = r.to_df()                                    # one row per passage
r = kb.retrieve("support-docs", "refund window", data_source="faq")   # r.data_sources: {ID: name}
kb.data_sources("support-docs")                   # [DataSourceInfo]: ID, name, status

a = kb.ask("support-docs", "How long do refunds take?")   # Answer
a.text, a.citations, a.sources, a.grounded_share

# Generate from your own passages, with your own prompt, on any model (Converse, exact tokens)
a = kb.generate("refund window?", r.passages, model="opus", prompt=MY_PROMPT)
a = kb.generate("refund window?", ["a chunk of text", "another chunk"])
a.input_tokens, a.output_tokens

report = kb.evaluate("support-docs", cases)       # EvalReport
report.hit_rate, report.mrr, report.to_df()

inv = kb.file_inventory("support-docs")           # FileInventory: every file, KBFile.state and why (KBFile.note)
inv.counts(), inv.to_df()                         # files per state; one row per file
kb.save_file_list(inv, "files.txt")               # SavedFileList: a line per file (link="https" or "key" too)
f = "s3://support-docs-bucket/policies/refund-policy.pdf"
chunks = kb.document_chunks("support-docs", f)    # DocumentChunks: its chunks in document order
chunks.chunks, chunks.stats.overlaps, chunks.stats.coverage
kb.metadata_file(f)                               # MetadataFile: the .metadata.json's attributes and problems
kb.probe_file("support-docs", f, "refund window")   # FileProbe: its passages, and .rank across the knowledge base
```

The analysis functions don't call AWS, so they work on responses and passages you already have: `parse_retrieve`, `parse_rag`, `parse_converse`, `parse_knowledge_base`, `parse_data_source`, `describe_chunking`, `describe_parsing`, `describe_vector_store`, `build_filter`, `parse_where`, `data_source_filter`, `with_data_sources`, `describe_sources`, `build_prompt`, `parse_citation_markers`, `best_snippet`, `question_terms`, `retrieval_metrics`, `match_expected`, `compare_retrievals`, `changed_since`, `generation_cost`, `vector_store_monthly_cost`, `query_cost`, the file functions (`inventory_files`, `file_state`, `skip_reason`, `sync_needed`, `parse_metadata_file`, `chunk_overlap`, `place_chunks`, `order_chunks`, `chunk_stats`, `file_steps`, `find_files`, `sort_files`, `parse_file_state`, `pick_files`, `file_list_lines`, `file_actions`, `files_verdict`, `stage_problems`), and the findings (`kb_findings`, `sync_findings`, `retrieval_findings`, `answer_findings`, `eval_findings`, `inventory_findings`, `state_findings`, `listing_findings`, `other_findings`, `file_findings`, `probe_findings`).

## Cost { #cost }

<div class="grid cards" markdown>

- **The idle vector store**

    A classic OpenSearch Serverless vector collection bills at least 2 OCUs around the clock, about $350/month at us-east-1 list prices, even with no searches. Collections sharing a KMS key share those OCUs; dev-test collections bill half, and NextGen collections scale to zero.

- **Answers**

    Priced per million input and output tokens by model (`MODEL_PRICES`, and `GLOBAL_MODEL_PRICES` for the cheaper `global.` profiles). With the default engine the tokens are an estimate; `engine="converse"` reports exact counts. A model not in the table shows its cost as unknown.

- **Searches**

    A search embeds the question (a fraction of a cent) and, with `rerank=`, pays for reranking (about $2 per 1,000 searches for Cohere Rerank 3.5, $1 for Amazon Rerank). `compare` and `evaluate` only search. Opening a file in the explorer (or `file()`) is one search, asking it a question two.

- **Listing stops early**

    `documents()` reads at most 10,000 documents and `unsynced()` lists at most 100,000 objects; `files()`, `save_file_list()` and the explorer read 10,000 documents and 10,000 S3 files per data source. A file shows at most 100 chunks, the most one search returns. They all say when they stopped.

</div>

Costs are estimates at us-east-1 list prices, read from the Bedrock and OpenSearch pricing pages on 2026-09-25 and checked against the AWS Price List API on 2026-09-27 (`BEDROCK_PRICES`, `MODEL_PRICES` and `GLOBAL_MODEL_PRICES`), and every report says whether it used list prices or yours. For another region or a negotiated price, pass your own:

```python
ui = BedrockKBView(BedrockKBAnalyzer(
    prices={"opensearch_ocu_hour": 0.26, "opensearch_min_ocus": 1},
    model_prices={"claude-sonnet-5": (2.00, 10.00)},    # $ per 1M input and output tokens
))
```

## Permissions { #permissions }

Everything is read-only. Anything the notebook's role can't read shows up as a note (or a `?` on a card) instead of an error, so you can start with less and add what you need. This policy covers every command:

```json title="IAM policy"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadKnowledgeBases",
      "Effect": "Allow",
      "Action": [
        "bedrock:ListKnowledgeBases", "bedrock:GetKnowledgeBase", "bedrock:ListDataSources",
        "bedrock:GetDataSource", "bedrock:ListIngestionJobs", "bedrock:GetIngestionJob",
        "bedrock:ListKnowledgeBaseDocuments", "bedrock:GetKnowledgeBaseDocuments", "bedrock:ListTagsForResource",
        "bedrock:Retrieve", "bedrock:RetrieveAndGenerate"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ListModels",
      "Effect": "Allow",
      "Action": ["bedrock:ListFoundationModels", "bedrock:ListInferenceProfiles"],
      "Resource": "*"
    },
    {
      "Sid": "GenerateAnswers",
      "Effect": "Allow",
      "Action": "bedrock:InvokeModel",
      "Resource": ["arn:aws:bedrock:*::foundation-model/*", "arn:aws:bedrock:*:*:inference-profile/*"]
    },
    {
      "Sid": "ListSourceBuckets",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::support-docs-bucket"
    },
    {
      "Sid": "ReadSourceFiles",
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::support-docs-bucket/*"
    }
  ]
}
```

| Permission | Used by |
|---|---|
| `bedrock:ListKnowledgeBases`, `bedrock:GetKnowledgeBase` | `kbs`, `kb_info`, and finding a knowledge base by name |
| `bedrock:ListDataSources`, `bedrock:GetDataSource` | `kb_info`, `syncs`, `documents`, `unsynced`, `files`, `save_file_list`, `file`, `explore`; `ListDataSources` also for `data_source=` by name, and to name the data sources in `search` and `ask` |
| `bedrock:ListIngestionJobs`, `bedrock:GetIngestionJob` | `kbs`, `kb_info`, `syncs`, `unsynced`, `files`, `save_file_list`, `file`, `explore` |
| `bedrock:ListKnowledgeBaseDocuments` | `documents`, `files`, `save_file_list`, `file`, `search_file`, `explore` |
| `bedrock:GetKnowledgeBaseDocuments` | `file` and `explore`, for a file past the first 10,000 of Bedrock's list |
| `bedrock:ListTagsForResource` | `kb_info` |
| `bedrock:Retrieve` | `search`, `compare`, `evaluate`, `ask(engine="converse")`, and a file's chunks and questions in `file`, `search_file` and `explore` |
| `bedrock:RetrieveAndGenerate` and `bedrock:InvokeModel` | `ask`, `follow_up` |
| `bedrock:InvokeModel` | `ask(engine="converse")`, `core.generate` |
| `bedrock:ListFoundationModels`, `bedrock:ListInferenceProfiles` | `models`, and turning `model="sonnet"` into an ID |
| `s3:ListBucket` on the data source's bucket | `unsynced`, `files`, `save_file_list`, `file`, `search_file`, `explore` |
| `s3:GetObject` on the data source's files | `file` and `explore` read a file's `.metadata.json`, and a `.txt` or `.md` file's text to place its chunks in it. Opening a file from its link (`link`, `save_file_list(link="https")`, and the file names in reports) uses it too, in your browser |

A model also has to be enabled for the account under **Model access** in the Bedrock console. To scope the policy down, replace `*` in the first statement with your knowledge bases' ARNs (`ListKnowledgeBases` itself needs `*`).

## Troubleshooting { #troubleshooting }

??? question "“No knowledge base 'x' in us-east-1”, or “knowledge base not found”"

    Knowledge bases are regional: the notebook's region may not be the knowledge base's. Use `BedrockKBView(BedrockKBAnalyzer(region="us-west-2"))`. The note lists the names it did find; names match in any case.

??? question "“Which knowledge base? There are 3 in us-east-1 …”"

    There's more than one and no default. Pass `kb="support-docs"` to the command, or set it once with `ui.use("support-docs")`.

??? question "“AccessDeniedException: You don't have access to the model …”"

    The model isn't enabled for the account. Enable it in the Bedrock console under Model access, or pick another from `ui.models()`. If the message says “not authorized to perform: bedrock:InvokeModel”, the notebook's role needs that permission instead (see [Permissions](#permissions)).

??? question "“This model needs an inference profile”"

    Some models can only be called through a cross-region inference profile. The note names the one to pass, such as `model="us.anthropic.claude-opus-5"`; `ui.models()` shows it for every model.

??? question "“This vector store only supports SEMANTIC search”"

    Hybrid search (meaning and keywords) needs a store that keeps the text searchable, such as OpenSearch, Aurora PostgreSQL or MongoDB Atlas. Drop `search_type="HYBRID"`.

??? question "A search returns nothing"

    Check `ui.syncs()` (was the data source ever synced?) and `ui.documents()` (did the files index?). With `where=`, remember that metadata values are typed and that filtering needs a `.metadata.json` file next to each file, followed by a sync.

??? question "The answer is “Sorry, I am unable to assist you with this request.”"

    That's RetrieveAndGenerate's reply when the passages it retrieved don't answer the question. Run `ui.search()` on the same question to see what came back, then try a larger `n=`, `search_type="HYBRID"`, or a different wording.

??? question "Tokens and cost say “estimate”"

    RetrieveAndGenerate doesn't report token counts, so they're estimated from characters. `engine="converse"` reports exact counts.

??? question "A follow-up says the session expired"

    Bedrock ends RetrieveAndGenerate sessions after a while. The tool starts a new one and says so; that answer doesn't know the earlier questions, so ask the full question again if it needs them.

??? question "A file's link opens “AccessDenied” or “Request has expired”"

    The link is signed with the notebook's credentials, so it opens only if they may read the file (`s3:GetObject` on the bucket, and `kms:Decrypt` when the bucket is encrypted with a KMS key), and only for an hour or until those credentials expire, whichever comes first. Run the cell again, or `link(n)`, for a fresh link; `link(n, expires=86400)` lasts a day.

??? question "explore() shows a report instead of the window"

    The window needs Jupyter and `ipywidgets`. SageMaker has both; elsewhere, `%pip install ipywidgets`, then restart the kernel and reload the browser tab. In a terminal or a script, `explore()` shows the same as reports: `files()`, `file()` and `search_file()`.

??? question "A file says “Not checked”"

    Bedrock's document list was read up to 10,000 documents per data source, and the file came after. Open it: its own record is looked up then (`bedrock:GetKnowledgeBaseDocuments`). To read every record at once, `ui.core.file_inventory("support-docs", limit=None)`.

??? question "A file's page says “passages of other files came back”"

    Its chunks are read with a search limited to the file (a filter on `x-amz-bedrock-kb-source-uri`). A vector store that ignores the filter returns other files' passages too: they're counted and left out, but the chunks shown may not be all of the file's.

??? question "The explorer window went blank after I reopened the notebook"

    Widgets live in the running kernel, so a saved notebook doesn't keep the window. Run the cell again.

??? question "The report lost its formatting after I reopened the notebook"

    JupyterLab strips the report's styles from saved output when a notebook is reopened. Run the cell again to get the formatted report back.

## Command reference { #reference }

Every `BedrockKBView` command. `ui.help()` prints the same list grouped by task, and `ui.help("ask")` shows one command's full description.

<div class="ref" markdown>

| Command | What it shows |
|---|---|
| `explore(kb=None, file=None, height=None)` | [The explorer window](#explorer): every file and how it was indexed, syncs, search and settings, by clicking |
| `use(kb)` | Sets the knowledge base later commands use |
| `kbs()` | Every knowledge base in the region: status, type, vector store, embedding model, sources, documents, last sync, idle cost, warnings |
| `kb_info(kb=None)` | Settings in plain English, data sources, recent syncs, findings, cost and tags |
| `syncs(kb=None, data_source=None, n=10)` | Sync history with counts and why syncs failed, and the command to sync again |
| `documents(kb=None, data_source=None, status=None, n=50)` | Documents by status, the ones that aren't indexed with the reason |
| `unsynced(kb=None, data_source=None)` | S3 files added or changed since the last successful sync |
| `files(kb=None, data_source=None, status=None, match=None, n=50)` | Every file next to Bedrock's record of it: failed, changed, not synced, skipped, deleted; problems first, with the sync commands |
| `save_file_list(kb=None, path=None, data_source=None, status=None, match=None, link="s3", expires=3600)` | [The files as a text file](#file-list), one per line (`s3://` paths, links or keys), with a link that downloads it |
| `file(path, kb=None)` | How one file was indexed: each step, its chunks in document order, its metadata file, and what to fix |
| `search_file(path, question, kb=None, n=10)` | Whether a question finds a file, and where it ranks among the whole knowledge base's passages |
| `search(question, n=5, kb=None, data_source=None, where=None, search_type=None, rerank=None)` | Ranked passages with scores, source and page, highlighted words, metadata and findings |
| `chunk(rank=1)` | The full text and metadata of a result from the last search or ask, and a link to its file |
| `link(source=1, expires=3600)` | A link that opens a source's file (or any `s3://` path) in a new browser tab |
| `ask(question, kb=None, data_source=None, n=5, where=None, search_type=None, model=None, engine="kb", prompt=None, temperature=None, max_tokens=None)` | The answer with \[1\]\[2\] citations, grounded share, sources, model, tokens, cost and findings |
| `follow_up(question, data_source=None)` | The next question in the same session or conversation; `data_source=` moves it to another data source |
| `compare(question, kb=None, n=(5, 10), search_types=("SEMANTIC", "HYBRID"), where=None, data_source=None)` | Each passage's rank under each search setting, overlap and findings |
| `evaluate(cases, kb=None, n=5, search_type=None, where=None, data_source=None)` | Retrieval hit rate and MRR on test questions |
| `models(match=None)` | Models for `ask()`: the ID to pass, how it's called, and its price |
| `help(command=None)` | This list, grouped by task; `help("name")` shows one command in full |

</div>
