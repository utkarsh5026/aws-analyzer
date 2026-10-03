---
title: Bedrock Knowledge Bases Analyzer Guide
description: "How to check, search and ask Amazon Bedrock Knowledge Bases from a SageMaker notebook with aws-analyzer's bedrock_kb.py, with examples."
---

<p class="eyebrow">aws-analyzer · bedrock_kb.py</p>

# Check, search and ask your Bedrock knowledge bases from a SageMaker notebook

One Python file. Drop it next to your notebook to see whether your knowledge bases are healthy, what a question retrieves, and how well an answer is backed by its sources, with each claim linked to the passage behind it.
{ .lede }

<ul class="pills">
  <li>One file, boto3 only</li>
  <li>Read-only: never starts a sync</li>
  <li>Any Bedrock model</li>
  <li>Plain text outside Jupyter</li>
</ul>

Every example uses a knowledge base called `support-docs`: support policies (PDFs and Markdown files) in the S3 bucket `support-docs-bucket`, some with a `.metadata.json` file that tags them with a `team` and a `year`. Use your own names. The screenshots are real output from the tool, run against demo knowledge bases with synthetic documents and a simulated Bedrock, so the scores, answers and times are illustrative.
{ .muted }

## Set up in SageMaker { #setup }

<div class="steps" markdown>

1. **Get `bedrock_kb.py` next to your notebook.** Pick whichever works in your environment:

    - **Upload it.** Download [bedrock_kb.py](https://raw.githubusercontent.com/utkarsh5026/aws-analyzer/main/analyzers/bedrock_kb.py), then drag it into JupyterLab's file browser, in the same folder as your notebook.

    - **Fetch it from a cell**, if the notebook can reach the internet:

        ```bash
        !curl -sO https://raw.githubusercontent.com/utkarsh5026/aws-analyzer/main/analyzers/bedrock_kb.py
        ```

    - **Copy it from S3**, for a notebook with no internet access (VPC-only mode). Upload it to a bucket once, then:

        ```bash
        !aws s3 cp s3://acme-ml-data/tools/bedrock_kb.py .
        ```

    - Or paste the whole file into a notebook cell and run it.

2. **Import it and create the view.** It uses the notebook's IAM execution role and region, so there's nothing to configure.

    ```python
    from bedrock_kb import BedrockKBView

    ui = BedrockKBView()   # uses the notebook's IAM role and region
    ui.help()              # every command, grouped by task; ui.help("ask") shows one in full
    ```

3. **Optional:** another region or AWS profile, a default knowledge base, or plain-text output. Knowledge bases are regional, so if `kbs()` comes back empty, check the region first.

    ```python
    from bedrock_kb import BedrockKBAnalyzer, BedrockKBView

    ui = BedrockKBView(BedrockKBAnalyzer(region="us-west-2", profile="dev"))
    ui = BedrockKBView(kb="support-docs")    # commands use this knowledge base unless given another
    ui = BedrockKBView(mode="text")          # plain text, e.g. in a terminal or a script
    ui = BedrockKBView(progress="plain")     # a plain progress line instead of tqdm bars ("off": none)
    ```

</div>

!!! note ""

    **Only boto3 is required.** Answers are generated through Bedrock itself, so no model SDK is needed and any model enabled in your account works. pandas (preinstalled on SageMaker) is used only when you ask for a DataFrame.

## Five-minute tour { #tour }

The commands you'll use most. Each one prints a report under the cell; none of them change anything.

```python
ui.kbs()                                     # every knowledge base: store, model, last sync, warnings
ui.use("support-docs")                       # later commands use this one (a name, ID or ARN)
ui.kb_info()                                 # its settings in plain English, data sources, syncs, findings
ui.search("How long do refunds take?")       # the passages a question retrieves, best first
ui.chunk(1)                                  # the full text and metadata of result #1
ui.ask("How long do refunds take?")          # an answer with [1][2] citations
ui.follow_up("And for digital goods?")       # the next question in the same conversation
ui.evaluate([("refund window?", "refund-policy.pdf")])   # does retrieval find the right file?
```

**Reading a report.** Every report puts the answer first: cards with the numbers that matter (a card turns amber or red when a finding is about it), then the findings, warnings first, each ending in what to do. The tables of detail come after, with status cells such as `FAILED` in colour, and at the bottom a **Next** row of two or three commands with the arguments filled in from this report, such as `chunk(1)`. One click on a command anywhere in a report, or on a code block, selects all of it, ready to copy. `ui.help()` lists every command by task; `ui.help("ask")` shows one in full.

Commands take `kb=`: a name in any case, the 10-character ID, or the ARN. Without it they use the one set by `use()`, else the only knowledge base in the region, else they list the ones there and say how to pick.

## Your knowledge bases { #kbs }

### Every knowledge base at a glance

`kbs()` describes every knowledge base in the region, in parallel: status, type, vector store, embedding model, how many data sources it has, how many documents the last successful sync read, when the last sync ran and how it went, and the vector store's estimated idle cost. Below the table, the warnings for each one, such as a data source that has never been synced or a sync that failed.

```python
ui.kbs()
```

![kbs(): four knowledge bases with status, type, vector store, embedding model, data sources, documents, last sync and estimated idle cost, then one warning each: a data source that doesn't chunk, a failed knowledge base and its failed sync, a data source never synced, and a sync that left 2 documents unindexed](images/bedrock-kbs-light.webp#only-light){ width="984" height="885" loading=lazy }
![kbs(): four knowledge bases with status, type, vector store, embedding model, data sources, documents, last sync and estimated idle cost, then one warning each: a data source that doesn't chunk, a failed knowledge base and its failed sync, a data source never synced, and a sync that left 2 documents unindexed](images/bedrock-kbs-dark.webp#only-dark){ width="984" height="885" loading=lazy }
/// caption
`ui.kbs()`: every knowledge base here has something to look at. The idle cost is estimated for OpenSearch Serverless only; the other vector stores show “-”.
///

### One knowledge base in detail

`kb_info()` puts the numbers that matter in cards (status, vector store, embedding model and its dimensions, data sources, last sync, idle cost), then the findings, each with what to do about it. The settings follow in plain English:

- **Vector store:** for example “OpenSearch Serverless collection abc123, index kb-index”.
- **Chunking:** “Fixed size: 300 tokens per chunk, 20% overlap”, “Hierarchical: 1,500-token parents, 300-token children, 60-token overlap”, “Semantic: up to 300 tokens, split where the topic changes” or “None: each file is one chunk”.
- **Parsing:** the default parser reads the text only; a foundation model or Bedrock Data Automation also reads tables, charts and images.
- **When deleted:** whether a data source's chunks are removed with it (`DELETE`) or stay in the vector store and keep turning up in answers (`RETAIN`).

```python
ui.kb_info("support-docs")
ui.kb_info()                                 # the default knowledge base
```

![kb_info(): cards for status, type, vector store, embedding model, data sources, last sync, idle cost and creation date; findings about 2 documents that failed to index, a data source that keeps its chunks when deleted, and the idle cost of OpenSearch Serverless; then the settings, two data sources with their chunking, parsing and deletion policy in plain English, the recent syncs and the tags](images/bedrock-kb-info-light.webp#only-light){ width="984" height="1116" loading=lazy }
![kb_info(): cards for status, type, vector store, embedding model, data sources, last sync, idle cost and creation date; findings about 2 documents that failed to index, a data source that keeps its chunks when deleted, and the idle cost of OpenSearch Serverless; then the settings, two data sources with their chunking, parsing and deletion policy in plain English, the recent syncs and the tags](images/bedrock-kb-info-dark.webp#only-dark){ width="984" height="1116" loading=lazy }
/// caption
`ui.kb_info("support-docs")`: the findings come first, each with what to do next. Below them, each data source's chunking, parsing and deletion policy in plain English.
///

Findings cover a failed knowledge base, data sources that were never synced, the last sync failing or leaving documents unindexed, no metadata files (so `where=` filters match nothing), chunking that is off or has no overlap, the `RETAIN` deletion policy, and what an OpenSearch Serverless store costs while idle. A section the notebook's role can't read shows as `? (AccessDeniedException)` and a note; the rest of the report still renders.

## Syncs and documents { #syncs }

A knowledge base only knows what its last sync indexed. Three commands show how far that is from the files you have. None of them starts a sync: where one is needed, they show the command to run and the boto3 call to copy.

```python
ui.syncs()                                   # sync history: counts, how long, why it failed
ui.syncs(data_source="docs-s3", n=30)        # one data source, further back
ui.documents(status="FAILED")                # documents that failed to index, with the reason
ui.unsynced()                                # S3 files added or changed since the last sync
```

- `syncs()` lists each sync with when it started, how long it took, its status, and how many documents it scanned, added, re-indexed, removed and failed on, with the reason a sync failed. Findings group repeated failures and their reasons: when the last three syncs failed the same way, syncing again won't help until the cause is fixed.
- `documents()` counts documents by status (indexed, failed, pending ...) and lists the ones that aren't indexed with Bedrock's reason, such as an encrypted PDF or a file that's too large. Bedrock keeps document status for S3 and custom data sources only; for the others, `syncs()` has the failed counts.
- `unsynced()` lists the S3 bucket (and the data source's inclusion prefixes) and compares each file's modification time with the start of the last successful sync. Metadata files are counted apart: a changed `.metadata.json` needs a sync too, before filters see the new values.

```bash title="What a sync looks like"
aws bedrock-agent start-ingestion-job --knowledge-base-id KBID123456 --data-source-id DSID123456 --region us-east-1
```

![syncs(): six syncs with when each started, how long it took, its status and document counts; one failed because the knowledge base's role wasn't allowed s3:GetObject, and one couldn't parse 2 documents; then the start-ingestion-job command and boto3 call for each data source](images/bedrock-syncs-light.webp#only-light){ width="984" height="647" loading=lazy }
![syncs(): six syncs with when each started, how long it took, its status and document counts; one failed because the knowledge base's role wasn't allowed s3:GetObject, and one couldn't parse 2 documents; then the start-ingestion-job command and boto3 call for each data source](images/bedrock-syncs-dark.webp#only-dark){ width="984" height="647" loading=lazy }
/// caption
`ui.syncs("support-docs")`: why a sync failed, in the same row, and the command that starts the next one. You run it; the tool never does.
///

![documents(): 42 documents read, of which 39 indexed, 2 failed and 1 ignored; a note that the web data source keeps no document status, a warning with the most common reason and the sync command, and a table of the three documents that aren't indexed with Bedrock's reason: an encrypted PDF, a scanned image with no text layer and an unsupported .mp4](images/bedrock-documents-light.webp#only-light){ width="984" height="476" loading=lazy }
![documents(): 42 documents read, of which 39 indexed, 2 failed and 1 ignored; a note that the web data source keeps no document status, a warning with the most common reason and the sync command, and a table of the three documents that aren't indexed with Bedrock's reason: an encrypted PDF, a scanned image with no text layer and an unsupported .mp4](images/bedrock-documents-dark.webp#only-dark){ width="984" height="476" loading=lazy }
/// caption
`ui.documents("support-docs")`: without `status=` it lists only the documents that aren't indexed, with Bedrock's reason for each.
///

![unsynced(): 12 files checked, 2 changed since the last sync: refund-policy.pdf changed 5 hours ago and holiday-shipping.md a day ago, a note that the web data source can't be listed, and the sync command](images/bedrock-unsynced-light.webp#only-light){ width="984" height="480" loading=lazy }
![unsynced(): 12 files checked, 2 changed since the last sync: refund-policy.pdf changed 5 hours ago and holiday-shipping.md a day ago, a note that the web data source can't be listed, and the sync command](images/bedrock-unsynced-dark.webp#only-dark){ width="984" height="480" loading=lazy }
/// caption
`ui.unsynced("support-docs")`: two files changed after the last sync, so searches and answers don't see those changes yet.
///

## Search { #search }

`search()` runs Bedrock's Retrieve for a question and shows the passages it returns, best first: a score bar relative to the top result, the file and page, the part of the passage with the most of the question's words (highlighted in Jupyter), and the passage's own metadata.

```python
ui.search("How long do refunds take?")
ui.search("How long do refunds take?", n=10)              # up to 100 passages
ui.search("error E1234", search_type="HYBRID")            # meaning and keywords, where the store supports it
ui.search("refund window", rerank=True)                   # re-order with a reranking model (Cohere Rerank 3.5)
ui.chunk(2)                                               # result #2 in full, with its metadata and IDs
```

![search(): five passages for How long do refunds take?, each with its file, page, score bar, metadata such as team=billing and year=2024, and the question's words highlighted; cards for passages, top score, files, time and the estimated cost of the question embedding](images/bedrock-search-light.webp#only-light){ width="984" height="677" loading=lazy }
![search(): five passages for How long do refunds take?, each with its file, page, score bar, metadata such as team=billing and year=2024, and the question's words highlighted; cards for passages, top score, files, time and the estimated cost of the question embedding](images/bedrock-search-dark.webp#only-dark){ width="984" height="677" loading=lazy }
/// caption
`ui.search("How long do refunds take?")`: two passages from the refund policy, then three weaker ones. The words from the question are highlighted.
///

Scores are relative: compare them with each other, not against a fixed cutoff, since they depend on the vector store and the embedding model. The findings say what to try next:

- **Nothing came back:** check the filter, whether the data sources are synced, or try a larger `n=`.
- **A code in the question is in no passage** (an error code, a SKU): semantic search matches meaning, not exact strings; try `search_type="HYBRID"`.
- **Every passage comes from one file**, **passages repeat each other word for word** (duplicate files), or **most passages are very short** (chunking too small).

`chunk(n)` shows result *n* of the last search (or source *n* of the last answer) in full, with its metadata, chunk ID and data source, and the `S3View().preview("s3://…")` call that opens the whole file with [s3.py](s3.md).

## Ask { #ask }

`ask()` answers a question from the knowledge base and shows the answer with `[1][2]` markers after each claim, linked to the sources below it. The cards say how much of the answer the citations cover (“grounded”), how many sources it used, the model, tokens, cost and time.

```python
ui.ask("How long do refunds take?")
ui.follow_up("And for digital goods?")                     # same session
ui.ask("How long do refunds take?", model="sonnet", n=10)
ui.ask("How long do refunds take?", engine="converse")     # exact tokens and cost
ui.chunk(1)                                                # source [1] in full
```

![ask(): an answer about refund times with a \[1\] and a \[2\] marker, 77% grounded because its last sentence cites nothing, cards for sources used, model, estimated tokens, cost and time, and the two cited passages from refund-policy.pdf](images/bedrock-ask-light.webp#only-light){ width="984" height="405" loading=lazy }
![ask(): an answer about refund times with a \[1\] and a \[2\] marker, 77% grounded because its last sentence cites nothing, cards for sources used, model, estimated tokens, cost and time, and the two cited passages from refund-policy.pdf](images/bedrock-ask-dark.webp#only-dark){ width="984" height="405" loading=lazy }
/// caption
`ui.ask("How long do refunds take?")`: cited spans are shaded and link to their source. The last sentence cites nothing, so the answer is 77% grounded. Tokens are an estimate here, because RetrieveAndGenerate doesn't report them.
///

### Two ways to generate

<div class="grid cards" markdown>

- **`engine="kb"` (default)**

    Bedrock's managed RetrieveAndGenerate: it retrieves, prompts the model and returns the citations, and `follow_up()` keeps its session. It doesn't report tokens, so tokens and cost are estimated from characters. A custom `prompt=` must contain `$search_results$`.

- **`engine="converse"`**

    Retrieve, then the model through Bedrock Converse: exact token counts and cost, any model, and your own `prompt=` with `{sources}` and `{question}`. The passages go in as numbered sources the model must cite as `[n]`.

</div>

![ask() with engine=converse and model=sonnet: the same answer, 100% grounded, with exact input and output tokens, and all five retrieved passages listed with a Cited column showing the model used the first two](images/bedrock-ask-converse-light.webp#only-light){ width="984" height="419" loading=lazy }
![ask() with engine=converse and model=sonnet: the same answer, 100% grounded, with exact input and output tokens, and all five retrieved passages listed with a Cited column showing the model used the first two](images/bedrock-ask-converse-dark.webp#only-dark){ width="984" height="419" loading=lazy }
/// caption
`ui.ask("How long do refunds take?", engine="converse", model="sonnet")`: exact token counts, and the Cited column shows which of the passages the model used.
///

With `engine="converse"`, the sources are sent as data, never as instructions (a document that says “ignore your instructions” is just text), and the model is told to say plainly when they don't hold the answer. `bedrock_kb.DEFAULT_PROMPT` is the template it uses; copy it to write your own.

```python
import bedrock_kb
print(bedrock_kb.DEFAULT_PROMPT)

MY_PROMPT = """Sources:
{sources}

Answer in two sentences for a customer, citing sources as [n]: {question}"""
ui.ask("How long do refunds take?", engine="converse", prompt=MY_PROMPT)
```

### Choosing a model

`model=` takes a model ID or ARN, an inference profile ID, or a short name: `"opus"`, `"sonnet"`, `"haiku"`, `"claude-opus-5"`, `"nova-pro"`. The default is Claude Opus 5, called through the region's inference profile when it can't be called on demand. `models()` lists the text models you can use in the region, the ID to pass, how each is called and its price per million tokens.

```python
ui.models()
ui.models("claude")
ui = BedrockKBView(BedrockKBAnalyzer(default_model="sonnet"))   # a different default
```

![models(): nine text models with the ID to pass as model=, name, provider, whether it's called on demand or through an inference profile, and the price per million input and output tokens; the default for ask() is us.anthropic.claude-opus-5](images/bedrock-models-light.webp#only-light){ width="984" height="477" loading=lazy }
![models(): nine text models with the ID to pass as model=, name, provider, whether it's called on demand or through an inference profile, and the price per million input and output tokens; the default for ask() is us.anthropic.claude-opus-5](images/bedrock-models-dark.webp#only-dark){ width="984" height="477" loading=lazy }
/// caption
`ui.models()`: the ID to pass as `model=`, how each model is called, and what it costs per million tokens.
///

The answer's findings flag an answer that cites nothing (it may be the model's own knowledge), one where less than half is backed by a citation, Bedrock's default “unable to assist” reply (the passages didn't hold the answer: run `search()` on the question), a guardrail stepping in, and an answer cut off at `max_tokens`.

## Filter by metadata { #filters }

`where=` filters on the documents' own metadata, which comes from a `<file>.metadata.json` next to each file in S3, then a sync. It works on `search`, `ask`, `compare` and `evaluate`, and every condition must match:

```json title="refund-policy.pdf.metadata.json"
{"metadataAttributes": {"team": "billing", "year": 2024}}
```

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

Values are typed: `2024` and `"2024"` are different. It's the same vocabulary as the DynamoDB analyzer's filters. For `OR`, pass a Bedrock `RetrievalFilter` instead, and it's sent unchanged:

```python
ui.search("error E1234", where={"team": "billing", "year": (">=", 2024)})
ui.search("refund window", where={"orAll": [{"equals": {"key": "team", "value": "billing"}},
                                            {"equals": {"key": "team", "value": "support"}}]})
```

## Compare and evaluate { #decide }

### Which search settings suit a question

`compare()` runs the same question with each search type and each `n` (one Retrieve each), and shows one row per passage with its rank under every setting, or “-” where a setting missed it. The cards say how much each pair of settings overlaps, and the findings say what changed, for example “HYBRID found 2 passages SEMANTIC missed at n=5, including its top result”. A setting the vector store doesn't support becomes a note.

```python
ui.compare("refund window for EU orders")                       # SEMANTIC and HYBRID, n=5 and n=10
ui.compare("error E1234", n=10, search_types=("SEMANTIC", "HYBRID"))
```

![compare(): four settings, SEMANTIC and HYBRID at n=2 and n=5, with their overlap in cards; findings that n=5 adds three passages and that HYBRID ranks the passage about error E1234 first where SEMANTIC ranks it second; a table with each passage's rank under each setting](images/bedrock-compare-light.webp#only-light){ width="984" height="722" loading=lazy }
![compare(): four settings, SEMANTIC and HYBRID at n=2 and n=5, with their overlap in cards; findings that n=5 adds three passages and that HYBRID ranks the passage about error E1234 first where SEMANTIC ranks it second; a table with each passage's rank under each setting](images/bedrock-compare-dark.webp#only-dark){ width="984" height="722" loading=lazy }
/// caption
`ui.compare("what does error E1234 mean?", n=(2, 5))`: both search types find the same passages, but only HYBRID puts the one with the exact error code first.
///

### How often retrieval finds the right file

`evaluate()` takes test questions with the source each one should find (part of its file name, URI or text) and reports the hit rate (the share of questions whose source came up in the top `n`) and the MRR (mean reciprocal rank: 1.00 means always first, 0.50 second on average). Each question shows where its source ranked, or “missed”, and what came up first instead. It only retrieves, so it costs a question embedding per question.

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

![evaluate(): six questions, hit rate 83% and MRR 0.72; a warning that When do holiday orders ship? missed holiday-shipping, a note that one question found its source third, and a table of each question, its expected source, its rank and what came up first](images/bedrock-evaluate-light.webp#only-light){ width="984" height="571" loading=lazy }
![evaluate(): six questions, hit rate 83% and MRR 0.72; a warning that When do holiday orders ship? missed holiday-shipping, a note that one question found its source third, and a table of each question, its expected source, its rank and what came up first](images/bedrock-evaluate-dark.webp#only-dark){ width="984" height="571" loading=lazy }
/// caption
`ui.evaluate(cases)`: the miss is `holiday-shipping.md`, which was added after the last sync. The `unsynced()` screenshot above shows it.
///

Findings list the questions that missed, a file that keeps coming up first instead, and the usual fixes: `search_type="HYBRID"` for codes and names, a larger `n=`, smaller chunks (a new data source), or `where=` filters.

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

a = kb.ask("support-docs", "How long do refunds take?")   # Answer
a.text, a.citations, a.sources, a.grounded_share

# Generate from your own passages, with your own prompt, on any model (Converse, exact tokens)
a = kb.generate("refund window?", r.passages, model="opus", prompt=MY_PROMPT)
a = kb.generate("refund window?", ["a chunk of text", "another chunk"])
a.input_tokens, a.output_tokens

report = kb.evaluate("support-docs", cases)       # EvalReport
report.hit_rate, report.mrr, report.to_df()
```

The analysis functions don't call AWS, so they work on responses and passages you already have: `parse_retrieve`, `parse_rag`, `parse_converse`, `parse_knowledge_base`, `parse_data_source`, `describe_chunking`, `describe_parsing`, `describe_vector_store`, `build_filter`, `build_prompt`, `parse_citation_markers`, `best_snippet`, `question_terms`, `retrieval_metrics`, `match_expected`, `compare_retrievals`, `changed_since`, `generation_cost`, `vector_store_monthly_cost`, `query_cost`, and the findings (`kb_findings`, `sync_findings`, `retrieval_findings`, `answer_findings`, `eval_findings`).

## Cost { #cost }

<div class="grid cards" markdown>

- **The idle vector store**

    A classic OpenSearch Serverless vector collection bills at least 2 OCUs around the clock, about $350/month at us-east-1 list prices, even with no searches. Collections sharing a KMS key share those OCUs; dev-test collections bill half, and NextGen collections scale to zero.

- **Answers**

    Priced per million input and output tokens by model (`MODEL_PRICES`, and `GLOBAL_MODEL_PRICES` for the cheaper `global.` profiles). With the default engine the tokens are an estimate; `engine="converse"` reports exact counts. A model not in the table shows its cost as unknown.

- **Searches**

    A search embeds the question (a fraction of a cent) and, with `rerank=`, pays for reranking (about $2 per 1,000 searches for Cohere Rerank 3.5, $1 for Amazon Rerank). `compare` and `evaluate` only search.

- **Listing stops early**

    `documents()` reads at most 10,000 documents and `unsynced()` lists at most 100,000 objects, and they say when they stopped.

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
        "bedrock:ListKnowledgeBaseDocuments", "bedrock:ListTagsForResource",
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
    }
  ]
}
```

| Permission | Used by |
|---|---|
| `bedrock:ListKnowledgeBases`, `bedrock:GetKnowledgeBase` | `kbs`, `kb_info`, and finding a knowledge base by name |
| `bedrock:ListDataSources`, `bedrock:GetDataSource` | `kb_info`, `syncs`, `documents`, `unsynced` |
| `bedrock:ListIngestionJobs`, `bedrock:GetIngestionJob` | `kbs`, `kb_info`, `syncs`, `unsynced` |
| `bedrock:ListKnowledgeBaseDocuments` | `documents` |
| `bedrock:ListTagsForResource` | `kb_info` |
| `bedrock:Retrieve` | `search`, `compare`, `evaluate`, `ask(engine="converse")` |
| `bedrock:RetrieveAndGenerate` and `bedrock:InvokeModel` | `ask`, `follow_up` |
| `bedrock:InvokeModel` | `ask(engine="converse")`, `core.generate` |
| `bedrock:ListFoundationModels`, `bedrock:ListInferenceProfiles` | `models`, and turning `model="sonnet"` into an ID |
| `s3:ListBucket` on the data source's bucket | `unsynced` |

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

??? question "The report lost its formatting after I reopened the notebook"

    JupyterLab strips the report's styles from saved output when a notebook is reopened. Run the cell again to get the formatted report back.

## Command reference { #reference }

Every `BedrockKBView` command. `ui.help()` prints the same list grouped by task, and `ui.help("ask")` shows one command's full description.

<div class="ref" markdown>

| Command | What it shows |
|---|---|
| `use(kb)` | Sets the knowledge base later commands use |
| `kbs()` | Every knowledge base in the region: status, type, vector store, embedding model, sources, documents, last sync, idle cost, warnings |
| `kb_info(kb=None)` | Settings in plain English, data sources, recent syncs, findings, cost and tags |
| `syncs(kb=None, data_source=None, n=10)` | Sync history with counts and why syncs failed, and the command to sync again |
| `documents(kb=None, data_source=None, status=None, n=50)` | Documents by status, the ones that aren't indexed with the reason |
| `unsynced(kb=None, data_source=None)` | S3 files added or changed since the last successful sync |
| `search(question, n=5, kb=None, where=None, search_type=None, rerank=None)` | Ranked passages with scores, source and page, highlighted words, metadata and findings |
| `chunk(rank=1)` | The full text and metadata of a result from the last search or ask |
| `ask(question, kb=None, n=5, where=None, search_type=None, model=None, engine="kb", prompt=None, temperature=None, max_tokens=None)` | The answer with \[1\]\[2\] citations, grounded share, sources, model, tokens, cost and findings |
| `follow_up(question)` | The next question in the same session or conversation |
| `compare(question, kb=None, n=(5, 10), search_types=("SEMANTIC", "HYBRID"), where=None)` | Each passage's rank under each search setting, overlap and findings |
| `evaluate(cases, kb=None, n=5, search_type=None, where=None)` | Retrieval hit rate and MRR on test questions |
| `models(match=None)` | Models for `ask()`: the ID to pass, how it's called, and its price |
| `help(command=None)` | This list, grouped by task; `help("name")` shows one command in full |

</div>
