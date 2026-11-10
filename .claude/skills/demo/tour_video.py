"""
Make each service's video tour: its calls typed into a notebook cell and run, and the reports they draw, with captions.

    .venv/bin/python .claude/skills/demo/tour_video.py                      # tours.js, then every MP4, light and dark
    .venv/bin/python .claude/skills/demo/tour_video.py lambda_functions     # just these services' MP4s
    .venv/bin/python .claude/skills/demo/tour_video.py --data               # only tours.js and clips.js
    .venv/bin/python .claude/skills/demo/tour_video.py athena --theme dark --fps 24
    .venv/bin/python .claude/skills/demo/tour_video.py --list

A tour is a few notebook cells. Each names a figure in shots.py, so the code it types is that figure's own call (with
the variable the service's guide uses, `ui`, `sfn`, ...), and its output is that figure's screenshot,
docs/images/<figure>-{light,dark}.webp. The player, docs/javascripts/tour.js, types the code, runs the cell, fades the
report in and scrolls through it under the captions, between a title card and an end card listing the calls.

What the player shows is written here and saved as data, docs/javascripts/tours.js: the code, the pictures' sizes,
each scene's length and when each caption comes in. Run --data after changing a tour or remaking a figure
(tests/test_tours.py fails when tours.js is out of date). The guides embed the player in their "Watch it run"
section, and docs/tour.html shows it full window (tour.html?service=lambda_functions).

Every report screenshot in the guides also gets a short clip, docs/javascripts/clips.js: the same player, one cell
long, typing that figure's call and scrolling its report under the rest of its guide caption (the call it starts with
left out, since the cell shows it). A ▶ Watch it run button on the screenshot plays it in the picture's place. Clips
need nothing written for them: a new figure in shots.py that a guide shows gets one at the next --data.

The MP4s, docs/videos/<service>-tour-{light,dark}.mp4, are for Slack, email and slides. The tour is a pure function of
time, so instead of recording in real time (which drops frames) this opens tour.html?record in headless Chromium at
1280 x 720, draws every frame with aaTour.renderAt(t), screenshots it and pipes the frames into ffmpeg, which must be
on PATH. 720p at 24 fps keeps each video to a few MB, and the screenshots' text stays sharp (--scale 1.5 makes 1080p,
about twice the size). Remake them after tours.js changes.

Needs playwright (pip install playwright) and Chromium: $CHROME, or the one Playwright installs.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import shutil
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE)]

import shots  # noqa: E402

ROOT = HERE.parents[2]
DOCS = ROOT / "docs"
DATA = DOCS / "javascripts" / "tours.js"
CLIPS = DOCS / "javascripts" / "clips.js"
PAGE = DOCS / "tour.html"
VIDEOS = DOCS / "videos"
SITE = "utkarsh5026.github.io/aws-analyzer"
THEMES = ("light", "dark")

STAGE_HEIGHT = 720
OUTPUT_WIDTH = 1150
CODE_TOP = 64
CODE_LINE_HEIGHT = 27.2
CODE_BOX_PADDING = 22
CODE_CHARS_PER_LINE = 108
OUTPUT_GAP = 18
CAPTION_ROOM = 96

TYPING_STARTS = 0.5
TYPED_PER_SECOND = 32
SHORTEST_TYPING = 0.5
LONGEST_TYPING = 2.4
RUN_PAUSE = 0.35
RUNNING_FOR = 0.75
PAN_SPEED = 115
LONGEST_PAN = 1100
STILL_BEFORE_PAN = 1.6
STILL_AFTER_PAN = 1.4
CAPTION_SECONDS = 3.6
FIRST_CAPTION_AFTER_OUTPUT = 0.6

TITLE_SECONDS = 3.2
INSTALL_RUNS = 1.2
INSTALL_HOLD = 1.0
END_SECONDS = 6.5
VIDEO_WIDTH, VIDEO_HEIGHT = 1280, 720
FPS = 24
QUALITY = 28


@dataclass
class Cell:
    """
    One notebook cell of a tour: the figure whose call it types and whose screenshot is its output, the chapter it
    is in the player's list, and its captions: the first while the call is typed, the rest spread over the report as
    it scrolls by. `**words**` in a caption are stressed and `` `code` `` is code. `code` replaces the figure's call
    when the cell should type something else, and `pan_max` stops the scroll that many pixels down the report.
    """

    chapter: str
    figure: str
    captions: list[str]
    code: str = ""
    pan_max: int | None = None


@dataclass
class Tour:
    """
    One service's tour: its View and the variable its guide keeps it in, the notebook's name in the tab, the title
    card's words (the guide's headline), what a screen reader says it shows, and its cells.
    """

    service: str
    view: str
    variable: str
    notebook: str
    eyebrow: str
    icon: str
    heading: str
    text: str
    summary: str
    cells: list[Cell] = field(default_factory=list)


LAKE = "s3://acme-ml-data"
TOURS = [
    Tour("s3", "S3View", "ui", "acme-ml-data.ipynb", "Amazon S3", "s3",
         "See what's in your buckets, and what it's costing you",
         "Every bucket, one bucket in plain English, what fills it, and a file read without downloading it.",
         "A notebook tour of aws-analyzer's S3 view: every bucket with its size and cost, one bucket's settings and "
         "findings, a summary of a prefix, a search for large model files, a Parquet preview and a lifecycle what-if.",
         [Cell("Every bucket", "overview",
               ["**Every bucket at once**, with the notebook's own role. Nothing to set up.",
                "Four buckets, 4.3 TB, about **$86.50 a month**, and three with something to fix.",
                "Sizes come from CloudWatch's daily storage metrics, so no bucket is listed."]),
          Cell("One bucket", "bucket-info",
               ["**One bucket in plain English**: who can read it and how it's set up.",
                "Encryption, versioning, public access and lifecycle rules as cards, not JSON.",
                "Each finding says what's wrong and **what to do about it**."], pan_max=700),
          Cell("What fills it", "summary",
               ["**What's in there?** A summary of everything under a prefix.",
                "91% of the objects are under 1 MB, and 500 are in Glacier: **each finding is priced**.",
                "Then where the space goes, folder by folder."]),
          Cell("Find files", "find",
               ["**Find** model files over 4 GB from the last 90 days, by pattern, size and age.",
                "Five matches, 34.7 GB, with their storage class and age.",
                "And the next call to run, **filled in** for the first one."]),
          Cell("Preview", "preview-parquet",
               ["**Look inside a file** without downloading it.",
                "A Parquet file: only its footer and first row group are read.",
                "Rows, columns, row groups and the schema, **in the notebook**."]),
          Cell("What if", "what-if",
               ["**What would a lifecycle rule save?** Try it before you write it.",
                "Moving training outputs to cheaper storage saves **$11.95 a month**, and pays for itself in under a month.",
                "Then the rule's JSON to copy. **The tool never changes the bucket.**"])]),
    Tour("dynamodb", "DynamoDBView", "ui", "acme-app.ipynb", "Amazon DynamoDB", "dynamodb",
         "Read your DynamoDB tables as plain tables",
         "No more {\"S\": ...}: items as rows, the shape of a table, and what each read cost.",
         "A notebook tour of aws-analyzer's DynamoDB view: every table with its cost, one table's keys and indexes, a "
         "query on one customer's orders, a filtered scan with what it read, and the attributes the items really hold.",
         [Cell("Every table", "dynamodb-tables",
               ["**Every table in the region**: items, size, billing and cost.",
                "Four tables, about **$264 a month**, and three with warnings.",
                "Each warning comes with the command that fixes it."], pan_max=650),
          Cell("One table", "dynamodb-table-info",
               ["**One table**: its keys, indexes, capacity and what it costs.",
                "For an on-demand table, the cost includes reads and writes at the last day's rate.",
                "Each index comes with the **`query(...)` call** that uses it."]),
          Cell("Query", "dynamodb-query",
               ["**Query** one customer's orders: the key's value as you'd say it.",
                "Items as rows, not `{\"S\": ...}`, with the **read units it cost**.",
                "Filters on each column, and `more()` for the next page."],
           code='ui.query("acme-app", "CUSTOMER#1042", sort=("begins_with", "ORDER#"), n=8,\n'
                '         attributes=["status", "created_at", "total", "currency", "coupon"])'),
          Cell("Scan", "dynamodb-scan-filter",
               ["**Scan with a filter**: failed orders over $300.",
                "It read 1,619 items to return 10, and **says so**: a filter runs after the read.",
                "So you know what a query on a key or index would save."],
           code='ui.scan("acme-app", 10, where={"status": "failed", "total": (">", 300)},\n'
                '        attributes=["status", "created_at", "total", "failure_reason"])'),
          Cell("Schema", "dynamodb-schema",
               ["**What do the items really hold?** Every attribute, its types and how often it's set.",
                "An old import stored some zip codes as numbers, so a filter on them **silently misses** those.",
                "Examples of each attribute, without reading the whole table."])]),
    Tour("bedrock_kb", "BedrockKBView", "ui", "support-docs.ipynb", "Bedrock Knowledge Bases", "bedrock",
         "Find out why your knowledge base answered that",
         "What failed to index, what a question really retrieves, and how well an answer is backed by its sources.",
         "A notebook tour of aws-analyzer's Bedrock Knowledge Bases view: every knowledge base, one in plain English, "
         "its syncs and why one failed, files changed since the last sync, a search and an answer with its sources.",
         [Cell("Every KB", "bedrock-kbs",
               ["**Every knowledge base in the region**, with its status and vector store.",
                "One is FAILED, one data source never synced, and idle Serverless costs **$350 a month**.",
                "Every warning, one knowledge base at a time."], pan_max=650),
          Cell("One KB", "bedrock-kb-info",
               ["**One knowledge base in plain English.**",
                "The findings first: two documents **failed to index**, and the call that lists them.",
                "Then each data source's chunking, parsing and deletion policy as sentences."], pan_max=800),
          Cell("Syncs", "bedrock-syncs",
               ["**Its syncs**: when each ran and what it did.",
                "Why a sync failed, **in the same row**.",
                "And the command that starts the next one. **You run it; the tool never does.**"]),
          Cell("Unsynced", "bedrock-unsynced",
               ["**Which files changed since the last sync?**",
                "Two files changed after it, so searches and answers **don't see those changes yet**.",
                "So you know when to sync, and what it would pick up."]),
          Cell("Search", "bedrock-search",
               ["**Search it** the way your app does, and see what comes back.",
                "Two passages from the refund policy, then three weaker ones, each with its score.",
                "The question's words are **highlighted** in each passage."]),
          Cell("Ask", "bedrock-ask",
               ["**Ask a question** and read the answer with its sources.",
                "Each sentence is marked with **the passage it came from**.",
                "And what the answer cost, with the next call to try."])]),
    Tour("sagemaker_env", "SageMakerView", "ui", "churn-analysis.ipynb", "Amazon SageMaker", "sagemaker",
         "Know what this notebook costs, and stop paying for idle",
         "This notebook, its disk, what else is still running, and why a training job or a pipeline broke.",
         "A notebook tour of aws-analyzer's SageMaker view: this notebook's cost and idle GPU, what fills its disk, "
         "everything still running and billing, why a training job failed, idle endpoints and a broken pipeline run.",
         [Cell("This notebook", "sagemaker-instance",
               ["**What is this notebook costing?** Ask it from inside.",
                "An ml.g5.2xlarge at **$1.52 an hour**, its GPU idle, and nothing will stop it tonight.",
                "Each warning has the command or setting that fixes it."], pan_max=700),
          Cell("Disk", "sagemaker-disk",
               ["**What's filling the disk?**",
                "88% full, and **3.8 GB is a file deleted weeks ago**, still in the trash.",
                "What's safe to clear, folder by folder. **The tool deletes nothing.**"]),
          Cell("Still running", "sagemaker-running",
               ["**What else is still running and billing?**",
                "A forgotten notebook, a forgotten app and an unused endpoint:",
                "about **$590 a month**, with the command to stop each."]),
          Cell("Training job", "sagemaker-training-job",
               ["**Why did a training job fail?** Read from its own logs.",
                "`ExecuteUserScriptError` becomes **\"add lightgbm to requirements.txt\"**.",
                "With the traceback it came from, and what it cost."]),
          Cell("Endpoints", "sagemaker-endpoints",
               ["**Which endpoints does nobody call?**",
                "churn-v1 hasn't been called in a month and costs **$83.95 a month**.",
                "Its traffic and errors, day by day."]),
          Cell("Pipeline", "sagemaker-pipeline-run",
               ["**Where did a pipeline run break?**",
                "The training step broke, and **the four steps after it never ran**.",
                "Each step's time, drawn as a graph and a waterfall."])]),
    Tour("opensearch", "OpenSearchView", "ui", "vectors.ipynb", "Amazon OpenSearch", "opensearch",
         "See what your vector indexes really hold",
         "Every domain and collection, whether the graphs fit in memory, and what a question finds.",
         "A notebook tour of aws-analyzer's OpenSearch view: every domain and collection with its cost, a domain's "
         "vector indexes, one index's memory, a sample of its documents and a vector search.",
         [Cell("Everything", "opensearch-overview",
               ["**Every domain and Serverless collection**, with what each costs.",
                "What each domain runs on, and **how much memory** its nodes have for vector graphs.",
                "And what Serverless bills even when it's idle."]),
          Cell("Indexes", "opensearch-indexes",
               ["**A domain's vector indexes**, each in plain English.",
                "The graphs need more memory than the nodes have, so **searches read them from disk**.",
                "One index uses a deprecated engine; another has documents search can't find."]),
          Cell("One index", "opensearch-index-info",
               ["**One index**: its vector field, engine and memory.",
                "32-bit vectors that need **15.7 GB** with replicas,",
                "and what on-disk mode would need instead."]),
          Cell("Sample", "opensearch-sample",
               ["**What do the documents look like?** A random sample.",
                "Unit-length vectors, none all zeros, and **a few passages repeated**.",
                "Each document's text, source and vector, side by side."]),
          Cell("Search", "opensearch-search",
               ["**Search it with a question**, the way your app does.",
                "Refund articles at cosine **0.74 to 0.75**,",
                "the question embedded with Titan Text Embeddings V2 for less than a cent."])]),
    Tour("lambda_functions", "LambdaView", "ui", "lambda.ipynb", "AWS Lambda", "lambda",
         "Find out which Lambda failed, and why, without the console",
         "Every function in every region, its errors read from its own logs, its runs and its code.",
         "A notebook tour of aws-analyzer's Lambda view: every function in every region with its findings, one "
         "function in full, its errors grouped by cause, how close its runs come to the limits, and its code.",
         [Cell("Every function", "lambda-functions",
               ["**Every function, in every region**, from one line.",
                "Two runtimes AWS no longer patches, and **3% of orders-etl's calls failing** in the last three days.",
                "Every warning names the function, says why it matters and **gives the fix**."], pan_max=900),
          Cell("One function", "lambda-function-info",
               ["**One function in full**: its triggers, settings, calls and cost.",
                "Its findings first: an ended runtime, and calls failing **since its last change**.",
                "Further down: its logs cost **six times its compute**."]),
          Cell("Errors", "lambda-errors",
               ["**Why does it fail?** Its errors, read from its own logs.",
                "Three different reasons, **grouped by cause**,",
                "each with what to do and the run to look at."]),
          Cell("Performance", "lambda-performance",
               ["**How close do its runs come to the limits?**",
                "They use at most 182 MB of the 1,024 MB it has,",
                "and **three ran into the 60 s timeout**."]),
          Cell("Code", "lambda-code",
               ["**What's in its package?** Read without running it.",
                "A **.env file** anyone allowed lambda:GetFunction can read,",
                "and 13 MB of boto3 the runtime already has."])]),
    Tour("apigateway", "APIGatewayView", "apigw", "apis.ipynb", "Amazon API Gateway", "apigateway",
         "See which API calls fail and why",
         "Every API with its errors and open routes, then one API route by route.",
         "A notebook tour of aws-analyzer's API Gateway view: every REST, HTTP and WebSocket API with its findings, "
         "then one API's routes, where each sends a request, and why one runs out of time.",
         [Cell("Every API", "apigw-apis",
               ["**Every REST, HTTP and WebSocket API** in the region.",
                "orders-api fails most and runs **close to its time limit**,",
                "partner-api has **two routes anyone can call**, and legacy-v1 had no calls."], pan_max=800),
          Cell("One API", "apigw-api-info",
               ["**One API**, route by route.",
                "Where each route sends a request, and who's allowed to call it.",
                "`POST /orders` **runs out of time** while orders-etl may still be working."])]),
    Tour("messaging", "MessagingView", "msg", "messaging.ipynb", "SQS, SNS and EventBridge", "messaging",
         "Find out where your message went",
         "Every queue, topic and rule as one map, a message followed hop by hop, and one queue in full.",
         "A notebook tour of aws-analyzer's messaging view: every SQS queue, SNS topic and EventBridge rule with what "
         "failed first, a topic followed to everything it delivers to, and one queue with its dead-letter queue.",
         [Cell("Everything", "msg-overview",
               ["**Every queue, topic and rule** in the region, read as one map.",
                "What failed first: **42 messages** in a dead-letter queue, deliveries that fail.",
                "Each finding ends in the **command that fixes it**."], pan_max=900),
          Cell("Follow it", "msg-follow",
               ["**Where does a message go?** Follow a topic hop by hop.",
                "Five places it delivers to, down to the Lambda that reads it,",
                "and **two of them keep it out**, with the policy that does."]),
          Cell("One queue", "msg-queue-info",
               ["**One queue in full**: its backlog, readers and settings.",
                "Its dead-letter queue holds **42 failures**,",
                "and its visibility timeout is short for its reader."])]),
    Tour("stepfunctions", "StepFunctionsView", "sfn", "workflows.ipynb", "AWS Step Functions", "stepfunctions",
         "See which step of a workflow failed, and read the Lambda run that broke it",
         "Every state machine, its failures by cause, one execution step by step, and its graph.",
         "A notebook tour of aws-analyzer's Step Functions view: every state machine with its failure rate, "
         "order-pipeline's failures grouped by cause, one execution's waterfall and log, its definition and its graph.",
         [Cell("Every workflow", "sfn-overview",
               ["**Every state machine** with its executions, failures and cost.",
                "order-pipeline's **4.0% failure rate** is the one warning,",
                "and the failing state machines come first."]),
          Cell("Failures", "sfn-failures",
               ["**Why do its executions fail?** Grouped by where and why.",
                "Most stop at LoadOrders with **`KeyError: 'customer_id'`**,",
                "and the newest run's request ID is filled in."]),
          Cell("One execution", "sfn-execution",
               ["**One execution, step by step.**",
                "A waterfall of each step's time, then one log:",
                "**orders-etl's traceback** between the step that called it and the Catch that ended it."],
           pan_max=1000),
          Cell("Definition", "sfn-state-machine",
               ["**Its definition, in plain English.**",
                "LoadOrders has **no Retry** for Lambda's own errors,",
                "and the finding gives the rule to paste."]),
          Cell("Graph", "sfn-graph",
               ["**The workflow as a graph**, with an execution drawn on it.",
                "The path ord-7f3a took, **where it failed**,",
                "and the states it never reached."])]),
    Tour("athena", "AthenaView", "athena", "athena.ipynb", "Amazon Athena", "athena",
         "See what your SQL over S3 costs, why it fails, and which tables to fix",
         "What queries cost, why they failed, and which Glue tables point at nothing.",
         "A notebook tour of aws-analyzer's Athena view: a week of queries and what they cost, the most expensive "
         "ones, why queries failed, the Glue catalog's broken tables, one table in full and the code for a query.",
         [Cell("Overview", "athena-overview",
               ["**A week of queries** and the catalog, in one report.",
                "One dashboard query is **90% of the bill**, two tables point at nothing,",
                "and two crawlers stopped."], pan_max=900),
          Cell("Expensive", "athena-expensive",
               ["**Which queries cost the most?**",
                "The dashboard's `SELECT *` ran 21 times and cost **$22.56**,",
                "grouped by query shape, whatever the literal values."]),
          Cell("Failures", "athena-failures",
               ["**Why did queries fail?** In plain words.",
                "A typo'd table name, a bad value in a CSV file,",
                "and a query the scan limit stopped, **each with its fix**."]),
          Cell("Catalog", "athena-catalog",
               ["**The Glue Data Catalog**: every table checked against S3.",
                "`marketing.campaigns` points at a **bucket that's gone**,",
                "and `sales.clicks` has files but no partitions."], pan_max=900),
          Cell("One table", "athena-table-info",
               ["**One table in full.**",
                "Every query reads all of it,",
                "so **partitioning it by date** is the fix, with the SQL to run."]),
          Cell("Query code", "athena-query-code",
               ["**The code to run a query**, and what it would read.",
                "It doesn't filter on `dt`, so it **reads every partition**.",
                "The tool never starts a query: it shows the code to copy."])]),
]


def figure_named(name: str) -> shots.Figure:
    """
    The figure in shots.py with this image name, or an error saying which one is missing.
    """
    for figure in shots.FIGURES:
        if figure.name == name:
            return figure
    raise KeyError(f"no figure {name!r} in shots.py (shots.py --list shows them)")


def picture_size(name: str) -> tuple[int, int]:
    """
    The light screenshot's width and height in pixels, read from the WebP's header (the dark one is the same size).
    """
    data = (shots.IMAGES / f"{name}-light.webp").read_bytes()[:30]
    if data[12:16] == b"VP8X":
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    if data[12:16] == b"VP8L":
        bits = int.from_bytes(data[21:25], "little")
        return 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)
    width = int.from_bytes(data[26:28], "little") & 0x3FFF
    height = int.from_bytes(data[28:30], "little") & 0x3FFF
    return width, height


def with_variable(code: str, variable: str) -> str:
    """
    A figure's call with the guide's variable for the View in place of `ui`.
    """
    return re.sub(r"\bui\.", f"{variable}.", code)


def cell_code(tour: Tour, cell: Cell, *, first: bool) -> str:
    """
    What a cell types: the figure's setup and call (or the cell's own code), after the import and the View in the
    tour's first cell.
    """
    figure = figure_named(cell.figure)
    code = cell.code or "\n".join(part for part in (figure.setup, figure.code) if part)
    code = with_variable(code, tour.variable)
    if first:
        return f"from aws_analyzer import {tour.view}\n\n{tour.variable} = {tour.view}()\n{code}"
    return code


def caption_html(text: str) -> str:
    """
    A caption's words as HTML: escaped, with `**stressed**` as <b> and `` `code` `` as <code>.
    """
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", escaped)


def output_appears(code: str, *, runs: float = RUNNING_FOR) -> float:
    """
    Seconds into a cell when its output is in: typing, a pause, then the cell running for `runs` seconds (tour.js's
    timing()).
    """
    typing = min(LONGEST_TYPING, max(SHORTEST_TYPING, len(code) / TYPED_PER_SECOND))
    return TYPING_STARTS + typing + RUN_PAUSE + runs


def code_lines(code: str) -> int:
    """
    How many lines the code takes in the cell, long lines wrapping as they do on the stage.
    """
    return sum(max(1, math.ceil(len(line) / CODE_CHARS_PER_LINE)) for line in code.split("\n"))


def scroll_distance(code: str, picture: tuple[int, int], pan_max: int | None) -> float:
    """
    How far the report scrolls, in stage pixels: its height on the stage less the room under the cell.
    """
    width, height = picture
    shown_height = height * OUTPUT_WIDTH / width
    output_top = CODE_TOP + code_lines(code) * CODE_LINE_HEIGHT + CODE_BOX_PADDING + OUTPUT_GAP
    room = STAGE_HEIGHT - output_top - CAPTION_ROOM
    return min(pan_max if pan_max is not None else LONGEST_PAN, max(0.0, shown_height - room))


def cell_seconds(code: str, travel: float, caption_count: int) -> float:
    """
    How long a cell lasts: until the report has scrolled by, and long enough to read each caption.
    """
    appears = output_appears(code)
    scrolled = appears + STILL_BEFORE_PAN + travel / PAN_SPEED + STILL_AFTER_PAN
    read = appears + FIRST_CAPTION_AFTER_OUTPUT + max(1, caption_count - 1) * CAPTION_SECONDS
    return round(max(scrolled, read), 1)


def caption_times(captions: list[str], *, appears: float, seconds: float) -> list[list]:
    """
    When each caption comes in: the first at the start, while the call is typed, the rest spread evenly from just
    after the output appears to a caption's length before the end.
    """
    first, *rest = captions
    timed = [[0, caption_html(first)]]
    start = appears + FIRST_CAPTION_AFTER_OUTPUT
    step = (seconds - CAPTION_SECONDS - start) / max(1, len(rest) - 1)
    for index, caption in enumerate(rest):
        timed.append([round(start + index * step, 2), caption_html(caption)])
    return timed


def cell_scene(tour: Tour, cell: Cell, *, number: int) -> dict:
    """
    One cell as the player's scene: the code, the screenshot, the scene's length and its timed captions.
    """
    code = cell_code(tour, cell, first=number == 1)
    picture = picture_size(cell.figure)
    travel = scroll_distance(code, picture, cell.pan_max)
    seconds = cell_seconds(code, travel, len(cell.captions))
    output = {"img": cell.figure, "w": picture[0], "h": picture[1]}
    if cell.pan_max is not None:
        output["panMax"] = cell.pan_max
    return {"kind": "cell", "dur": seconds, "chapter": cell.chapter, "step": number, "steps": len(tour.cells),
            "label": f"{number} of {len(tour.cells)} · {cell.chapter}", "code": code, "outputs": [output],
            "captions": caption_times(cell.captions, appears=output_appears(code), seconds=seconds)}


def install_lines(version: str) -> list[dict]:
    """
    What pip prints while it installs, each line with the seconds after the cell starts running that it shows; the
    line with a bar fills it until the next line comes.
    """
    wheel = f"aws_analyzer-{version}-py3-none-any.whl"
    return [{"at": 0.05, "html": "Collecting aws-analyzer"},
            {"at": 0.2, "html": f"&nbsp;&nbsp;Downloading {wheel}"},
            {"at": 0.3, "html": '&nbsp;&nbsp;<span class="bar"></span>', "bar": True},
            {"at": 0.8, "html": "Installing collected packages: aws-analyzer"},
            {"at": 1.05, "html": f'<span class="ok">✓</span> Successfully installed aws-analyzer-{version}'}]


def install_scene() -> dict:
    """
    The tour's first cell: installing the package, pip's lines streaming in under it.
    """
    source = shots.ROOT.joinpath("src", "aws_analyzer", "__init__.py").read_text(encoding="utf-8")
    version = re.search(r'__version__ = "([^"]+)"', source).group(1)
    code = "%pip install aws-analyzer"
    seconds = round(output_appears(code, runs=INSTALL_RUNS) + INSTALL_HOLD, 1)
    return {"kind": "cell", "dur": seconds, "chapter": "Install", "label": "Install", "runs": INSTALL_RUNS,
            "code": code, "outputs": [{"lines": install_lines(version)}],
            "captions": [[0, caption_html("**Install it once**, in any SageMaker or Jupyter notebook. "
                                          "Read-only: it never changes anything in AWS.")]]}


def short_call(code: str, variable: str) -> str:
    """
    A cell's call for the end card: the last command it runs on the View, with `(…)` when it took arguments.
    """
    method, opening = re.findall(rf"^{re.escape(variable)}\.(\w+)\((.?)", code, re.MULTILINE)[-1]
    arguments = "" if opening == ")" else "…"
    return f"{variable}.{method}({arguments})"


def end_scene(tour: Tour, calls: list[str]) -> dict:
    """
    The end card: the calls the tour made, help(), and where the guide is.
    """
    return {"kind": "end", "dur": END_SECONDS, "chapter": "Your turn", "eyebrow": tour.eyebrow,
            "icon": f"images/aws/{tour.icon}.svg", "heading": "Your turn: one line each.",
            "calls": [*calls, f"{tour.variable}.help()"], "url": f"{SITE}/{tour.service}.html"}


def tour_data(tour: Tour) -> dict:
    """
    One tour as the player reads it: the title card, the install cell, a scene per cell and the end card.
    """
    title = {"kind": "title", "dur": TITLE_SECONDS, "chapter": "Intro", "eyebrow": tour.eyebrow,
             "icon": f"images/aws/{tour.icon}.svg", "heading": html.escape(tour.heading, quote=False),
             "text": html.escape(tour.text, quote=False), "ahead": [cell.chapter for cell in tour.cells]}
    cells = [cell_scene(tour, cell, number=number) for number, cell in enumerate(tour.cells, 1)]
    calls = [short_call(scene["code"], tour.variable) for scene in cells]
    return {"eyebrow": tour.eyebrow, "notebook": tour.notebook, "summary": tour.summary,
            "guide": f"{tour.service}.html", "video": f"videos/{tour.service}-tour-light.mp4",
            "scenes": [title, install_scene(), *cells, end_scene(tour, calls)]}


def tours_text() -> str:
    """
    docs/javascripts/tours.js as it should be: every tour's data for the player.
    """
    data = {tour.service: tour_data(tour) for tour in TOURS}
    body = json.dumps(data, indent=1, ensure_ascii=False)
    return ("/* The video tours' scenes, for tour.js. Written by .claude/skills/demo/tour_video.py --data: "
            "edit the tours there, not here. */\n"
            f"window.AA_TOURS = {body};\n")


FIGURE_CAPTION = re.compile(
    r"images/([\w-]+)-light\.webp[^\n]*\n(?:[^\n]*\.webp[^\n]*\n)*[ \t]*/// caption\n(.*?)\n[ \t]*///", re.S)
MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z`*])")
CAPTION_CHARS = 150
ANIMATION_FLAG = 0x02
AFTER_CLICKS = "**Opens the window**, shown here a few clicks in."


def guide_captions() -> dict[str, dict[str, str]]:
    """
    Every figure's caption in each guide it's in: {figure: {guide file name: caption Markdown}}, on one line (a figure
    in a fold has its caption indented).
    """
    captions: dict[str, dict[str, str]] = {}
    for guide in sorted(DOCS.glob("*.md")):
        for match in FIGURE_CAPTION.finditer(guide.read_text(encoding="utf-8")):
            caption = " ".join(match.group(2).split())
            captions.setdefault(match.group(1), {}).setdefault(guide.name, caption)
    return captions


def without_leading_call(caption: str) -> str:
    """
    A caption without the call it opens with, since the clip's cell already shows it, and what follows the call kept:
    "`ui.disk()`: 3.8 GB is ..." -> "3.8 GB is ...", "`athena.explore()`, the Costs tab: ..." -> "The Costs tab: ...".
    A caption that is only the call leaves nothing, and one that doesn't open with a call stays as it is ("`.tar`
    with ..." is what was typed in a search box).
    """
    opening = re.match(r"`[^`]*\([^`]*`[\s,:]*", caption)
    if not opening:
        return caption
    rest = caption[opening.end():]
    return rest[:1].upper() + rest[1:]


def caption_parts(caption: str) -> list[str]:
    """
    A caption cut into what fits on the stage at once: its sentences, short ones kept together up to CAPTION_CHARS.
    """
    parts: list[str] = []
    if not caption:
        return parts
    for sentence in SENTENCE_END.split(MARKDOWN_LINK.sub(r"\1", caption)):
        joined = f"{parts[-1]} {sentence}" if parts else sentence
        if parts and len(joined) <= CAPTION_CHARS:
            parts[-1] = joined
        else:
            parts.append(sentence)
    return parts


def plain_text(markdown: str) -> str:
    """
    A caption as a screen reader says it: without backticks, stars or links.
    """
    return MARKDOWN_LINK.sub(r"\1", markdown).replace("`", "").replace("**", "")


def clip_captions(parts: list[str], *, appears: float) -> list[list]:
    """
    When each part of the caption comes in: the first just after the report appears, the rest a reading time apart.
    """
    start = appears + FIRST_CAPTION_AFTER_OUTPUT
    return [[round(start + index * CAPTION_SECONDS, 2), caption_html(part)] for index, part in enumerate(parts)]


@dataclass
class ClipSource:
    """
    What one screenshot's clip is made of: the screenshot, the call its cell types, its guide caption, and the
    notebook tab and toolbar label it shows. `clicked` is a window screenshot taken after some clicks, not as the call
    opens it.
    """

    name: str
    code: str
    caption: str
    eyebrow: str
    notebook: str
    clicked: bool = False


@dataclass
class Window:
    """
    A notebook window whose screenshots get clips: the start of their names, the call its guide opens it with, its
    service's tour (for the notebook tab, the toolbar label and the variable) or its own eyebrow and notebook, and the
    screenshots that show it as the call opens it.
    """

    prefix: str
    code: str
    service: str = ""
    eyebrow: str = ""
    notebook: str = ""
    as_opened: tuple[str, ...] = ()


WINDOWS = [
    Window("lambda-explorer", "ui.explore()", "lambda_functions", as_opened=("lambda-explorer",)),
    Window("kb-explorer", 'ui.explore("support-docs")', "bedrock_kb", as_opened=("kb-explorer",)),
    Window("sfn-explorer", "sfn.explore()", "stepfunctions", as_opened=("sfn-explorer",)),
    Window("apigw-explorer", "apigw.explore()", "apigateway", as_opened=("apigw-explorer",)),
    Window("os-explorer", "ui.explore()", "opensearch", as_opened=("os-explorer",)),
    Window("athena-explorer", "athena.explore()", "athena"),
    Window("msg-explorer", "msg.explore()", "messaging", as_opened=("msg-explorer",)),
    Window("explorer", 'x = S3Explorer("s3://acme-ml-data/")', "s3", eyebrow="S3 file explorer"),
    Window("chat", 'chat("support-docs")', eyebrow="Bedrock chat", notebook="support-docs.ipynb"),
    Window("agent", "agent.chat()", eyebrow="Ask in plain English", notebook="agent.ipynb",
           as_opened=("agent-chat-start",)),
]


def clip_data(source: ClipSource) -> dict:
    """
    One screenshot's clip as the player reads it: a single cell typing its call and showing the screenshot under
    its caption. A window screenshot taken after some clicks says so while its call is typed (AFTER_CLICKS), so the
    call isn't taken to show that view by itself.
    """
    parts = caption_parts(without_leading_call(source.caption))
    picture = picture_size(source.name)
    travel = scroll_distance(source.code, picture, None)
    seconds = cell_seconds(source.code, travel, len(parts))
    captions = clip_captions(parts, appears=output_appears(source.code))
    if source.clicked:
        captions.insert(0, [0, caption_html(AFTER_CLICKS)])
    scene = {"kind": "cell", "dur": seconds, "chapter": source.eyebrow, "label": source.eyebrow, "code": source.code,
             "outputs": [{"img": source.name, "w": picture[0], "h": picture[1]}], "captions": captions}
    summary = f"{plain_text(source.code)}: {plain_text(source.caption)}"
    return {"notebook": source.notebook, "summary": summary, "video": "", "scenes": [scene]}


def clip_caption(captions: dict[str, str], *guides: str) -> str:
    """
    The caption a clip reads: the one in the first of these guides that shows the screenshot, else the first there is.
    """
    for guide in guides:
        if guide in captions:
            return captions[guide]
    return next(iter(captions.values()))


def report_clips(captions: dict[str, dict[str, str]]) -> dict[str, dict]:
    """
    A clip for every report screenshot a guide shows and captions: each figure of a service with a tour, in the look a
    user gets (a figure drawn in another theme shows that theme, not a call to run).
    """
    tours = {tour.service: tour for tour in TOURS}
    made = {}
    for figure in shots.FIGURES:
        is_shown = figure.name in captions and figure.service in tours
        if not is_shown or figure.theme:
            continue
        tour = tours[figure.service]
        code = with_variable("\n".join(part for part in (figure.setup, figure.code) if part), tour.variable)
        caption = clip_caption(captions[figure.name], figure.guide, f"{figure.service}.md")
        made[figure.name] = clip_data(ClipSource(figure.name, code, caption, tour.eyebrow, tour.notebook))
    return made


def is_animated(name: str) -> bool:
    """
    Whether a screenshot is an animated WebP (a window's tour, which moves already), from its VP8X header's flags.
    """
    data = (shots.IMAGES / f"{name}-light.webp").read_bytes()[:21]
    return data[12:16] == b"VP8X" and bool(data[20] & ANIMATION_FLAG)


def window_of(name: str) -> Window | None:
    """
    The window a screenshot's name says it shows ("kb-explorer-file" is kb-explorer's), or None.
    """
    for window in WINDOWS:
        if name == window.prefix or name.startswith(f"{window.prefix}-"):
            return window
    return None


def window_source(window: Window, name: str, captions: dict[str, str]) -> ClipSource:
    """
    A window screenshot's clip: its window's call, in its service's tour's notebook when it has one.
    """
    tour = next((tour for tour in TOURS if tour.service == window.service), None)
    eyebrow = window.eyebrow or tour.eyebrow
    notebook = window.notebook or tour.notebook
    caption = clip_caption(captions, f"{window.service}.md")
    return ClipSource(name, window.code, caption, eyebrow, notebook, clicked=name not in window.as_opened)


def window_clips(captions: dict[str, dict[str, str]], reports: set[str]) -> dict[str, dict]:
    """
    A clip for every other screenshot a guide shows and captions: the notebook windows, typing the call that opens
    the window, and none for the animated tours.
    """
    made = {}
    for name in sorted(set(captions) - reports):
        window = window_of(name)
        if window is None or is_animated(name):
            continue
        made[name] = clip_data(window_source(window, name, captions[name]))
    return made


def clips() -> dict[str, dict]:
    """
    Every screenshot's clip, by its name: the reports', then the windows'.
    """
    captions = guide_captions()
    reports = {figure.name for figure in shots.FIGURES}
    return {**report_clips(captions), **window_clips(captions, reports)}


def clips_text() -> str:
    """
    docs/javascripts/clips.js as it should be: every screenshot's clip for the player.
    """
    body = json.dumps(clips(), indent=1, ensure_ascii=False)
    return ("/* The screenshots' short clips, for tour.js. Written by .claude/skills/demo/tour_video.py --data "
            "from shots.py's figures and the guides' captions: change those, not this. */\n"
            f"window.AA_CLIPS = {body};\n")


def write_file(path: Path, text: str, about: str) -> None:
    """
    Write one of the player's data files, saying whether it changed.
    """
    changed = not path.exists() or path.read_text(encoding="utf-8") != text
    path.write_text(text, encoding="utf-8")
    print(f"{'Wrote' if changed else 'Unchanged:'} {path.relative_to(ROOT)} ({about})")


def write_data() -> None:
    """
    Write docs/javascripts/tours.js and clips.js, saying whether each changed.
    """
    seconds = {tour.service: sum(scene["dur"] for scene in tour_data(tour)["scenes"]) for tour in TOURS}
    lengths = ", ".join(f"{service} {value:.0f}s" for service, value in seconds.items())
    write_file(DATA, tours_text(), lengths)
    made = clips()
    shortest = min(clip["scenes"][0]["dur"] for clip in made.values())
    longest = max(clip["scenes"][0]["dur"] for clip in made.values())
    write_file(CLIPS, clips_text(), f"{len(made)} clips, {shortest:.0f} to {longest:.0f}s")


def ffmpeg_command(fps: int, out: Path) -> list[str]:
    """
    The ffmpeg call that turns JPEG frames on stdin into an H.264 MP4 every browser, phone and slide deck plays
    (level 4.0, 4 reference frames, yuv420p, its index first so it starts before it's all downloaded).
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        sys.exit("ffmpeg isn't installed: apt install ffmpeg, or brew install ffmpeg")
    return [ffmpeg, "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg",
            "-i", "-", "-c:v", "libx264", "-preset", "slow", "-crf", str(QUALITY), "-tune", "stillimage",
            "-profile:v", "high", "-level", "4.0", "-refs", "4", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(out)]


def record(browser: Any, service: str, *, theme: str, fps: int, scale: float) -> Path:
    """
    Draw one tour frame by frame in one theme and encode it to docs/videos/<service>-tour-<theme>.mp4.
    """
    out = VIDEOS / f"{service}-tour-{theme}.mp4"
    page = browser.new_page(viewport={"width": VIDEO_WIDTH, "height": VIDEO_HEIGHT}, device_scale_factor=scale)
    page.goto(f"{PAGE.as_uri()}?service={service}&record&theme={theme}")
    page.wait_for_function("window.aaTour !== undefined")
    page.evaluate("async () => { aaTour.pause(); await aaTour.ready(); await document.fonts.ready; }")
    duration = page.evaluate("aaTour.duration")
    frames = int(duration * fps)
    screen = page.locator(".tour-screen")
    print(f"{service} ({theme}): {frames} frames, {duration:.0f}s at {fps} fps")
    encoder = subprocess.Popen(ffmpeg_command(fps, out), stdin=subprocess.PIPE)
    try:
        for frame in range(frames):
            page.evaluate("t => aaTour.renderAt(t)", frame / fps)
            encoder.stdin.write(screen.screenshot(type="jpeg", quality=90, animations="disabled"))
    finally:
        encoder.stdin.close()
        encoder.wait()
        page.close()
    if encoder.returncode != 0:
        sys.exit("ffmpeg failed: see its message above")
    print(f"  saved {out.relative_to(ROOT)} ({out.stat().st_size / 1_000_000:.1f} MB)")
    return out


def chromium() -> str | None:
    """
    The Chromium to draw with: $CHROME, else the one a cloud session has in /opt/pw-browsers, else Playwright's own.
    """
    installed = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux*/chrome"))
    found = os.environ.get("CHROME") or next(iter(installed), None)
    return str(found) if found else None


def record_all(services: list[str], *, themes: list[str], fps: int, scale: float) -> None:
    """
    Export the MP4s of these services in these themes, in one headless Chromium.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("playwright isn't installed: pip install playwright")
    VIDEOS.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chromium())
        try:
            for service in services:
                for theme in themes:
                    record(browser, service, theme=theme, fps=fps, scale=scale)
        finally:
            browser.close()


def main() -> int:
    """
    Read the command line, write tours.js and export the MP4s it asks for.
    """
    intro, _, rest = (__doc__ or "").strip().partition("\n\n")
    parser = argparse.ArgumentParser(description=intro, epilog=rest, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("services", nargs="*", help="services to export (default: all)")
    parser.add_argument("--data", action="store_true", help="only write docs/javascripts/tours.js")
    parser.add_argument("--list", action="store_true", help="list the tours and their cells")
    parser.add_argument("--theme", choices=[*THEMES, "both"], default="both")
    parser.add_argument("--fps", type=int, default=FPS)
    parser.add_argument("--scale", type=float, default=1, help="1 gives 1280 x 720 (default), 1.5 gives 1920 x 1080")
    args = parser.parse_args()
    if args.list:
        for tour in TOURS:
            print(f"{tour.service:17} {', '.join(cell.figure for cell in tour.cells)}")
        return 0
    known = [tour.service for tour in TOURS]
    unknown = sorted(set(args.services) - set(known))
    if unknown:
        parser.error(f"no tour for {', '.join(unknown)} (--list shows them)")
    write_data()
    if args.data:
        return 0
    themes = list(THEMES) if args.theme == "both" else [args.theme]
    record_all(args.services or known, themes=themes, fps=args.fps, scale=args.scale)
    return 0


if __name__ == "__main__":
    sys.exit(main())
