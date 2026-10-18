---
title: Ask in Plain English (Agent) Guide
description: "How to ask questions about your AWS account in plain English from a SageMaker notebook with aws-analyzer's agent: an agent (an Azure OpenAI deployment, run by LangChain's deepagents) answers questions like which Lambda failed last night, or whether last night's uploads to a bucket were processed, from read-only tools, says what it checked, and stays within a budget per question."
---

<p class="eyebrow"><img class="aws-icon" src="images/aws/lambda.svg" alt="" width="32" height="32"> Ask in plain English <code>agent/</code></p>

# Ask your AWS account a question, and get an answer you can check

Type "Which Lambda failed last night?" in a notebook cell and get which functions failed, how often, why, and the run that shows it. Ask "Were last night's uploads to acme-raw processed?" and it checks the bucket, the files that arrived and the function they should have run. Every answer says what it checked, in your time zone, what it couldn't read, what it cost, and the commands to look closer.
{ .lede }

<ul class="pills">
  <li>Read-only: a guard refuses anything else</li>
  <li>Says what it checked</li>
  <li>A budget per question</li>
  <li>Follow-up questions</li>
  <li>Lambda and S3, joined</li>
</ul>

It covers AWS Lambda and Amazon S3, and the bucket events that invoke functions. The model is your Azure OpenAI deployment, and the agent loop is LangChain's [deepagents](https://pypi.org/project/deepagents/). The examples use acme's functions from the [Lambda guide](lambda_functions.md): `orders-etl`, which loads each day's orders into the warehouse from the files that arrive in the `acme-raw` bucket, and `report-api`.
{ .muted }

## Set up in SageMaker { #setup }

<div class="steps" markdown>

1. **Install the package with the agent's extra**, in a notebook cell. The agent needs deepagents and langchain-openai, which need Python 3.11 or newer; the rest of aws-analyzer still runs on 3.10 with boto3 alone.

    ```bash
    %pip install "aws-analyzer[agent]"
    ```

2. **Point it at your Azure OpenAI deployment.** It reads the same environment variables as the OpenAI SDK, plus the deployment's name:

    ```python
    import os

    os.environ["AZURE_OPENAI_ENDPOINT"] = "https://acme-ai.openai.azure.com"
    os.environ["AZURE_OPENAI_API_KEY"] = "..."           # better: from a secret store, not typed in the notebook
    os.environ["AZURE_OPENAI_DEPLOYMENT"] = "gpt-4o"     # the deployment's name in Azure AI Foundry
    ```

    Or pass them: `AgentView(endpoint=..., deployment=..., api_key=...)`. For Microsoft Entra ID instead of a key, pass `azure_ad_token_provider=` (from `azure.identity.get_bearer_token_provider`). The API version is `2024-10-21` unless `OPENAI_API_VERSION` or `api_version=` says otherwise.

3. **Create the view.** It reads AWS with the notebook's IAM execution role and region, like every other view.

    ```python
    from aws_analyzer import AgentView, set_timezone

    set_timezone("Asia/Kolkata")   # so "last night" and "today" mean your night and your day
    agent = AgentView()
    agent.help()
    ```

    [`set_timezone()`](index.md#timezone) is the package's own: every report then shows its times in your zone too, and the agent's table of what it checked does. `AgentView(timezone="Europe/London")` gives the agent a zone of its own instead. Without either, the agent works in UTC and says so.

    ```python
    agent = AgentView(timezone="kolkata")   # a tz database name, its city, or an offset like "+05:30"
    ```

</div>

!!! warning ""

    **What leaves AWS.** Your question and what the tools return (function and bucket names, settings, counts, file names, error messages and log lines, with anything that looks like a key or password hidden) are sent to your Azure OpenAI deployment. Environment variable values and what's inside your files are never read. Use a deployment your organisation allows for that data.

## Five-minute tour { #tour }

```python
agent.ask("Which Lambda failed last night?")
agent.ask("Why did orders-etl fail?")                          # a follow-up: it remembers the conversation
agent.ask("Show me one of those runs")                         # ...and again
agent.ask("Were last night's uploads to acme-raw processed?", new=True)   # S3 and Lambda together
agent.ask("Which buckets are public, and what do they cost?", new=True)
agent.ask("Which functions run close to their timeout this week?", new=True)   # a new conversation
agent.tools()                                                  # what it can call, its model and its budget
agent.transcript()                                             # every question and answer so far, with their cost
```

Each `ask()` shows a report: the answer first, then **What it checked**, a line per tool call with the window and regions it covered (and for S3, the keys it read) and what it found, then what it couldn't read or was refused, the AWS reads it made, the tokens and their cost, and the `LambdaView` and `S3View` commands that show the same thing as a full report, arguments filled in.

## How it answers { #how-it-works }

The model gets a set of tools and decides which to call. For "Which Lambda failed last night?" it usually:

1. Turns "last night" into a window in your time zone (`set_timezone()`'s; 18:00 yesterday to 08:00 today, unless you say otherwise) and converts it to UTC, which CloudWatch uses. Each question is sent with the time it was asked, in your time zone and in UTC.
2. Calls `function_health` for that window: CloudWatch's counts for every function at once, in a few seconds.
3. Calls `function_errors` for the functions that failed, which reads their logs and groups the errors by cause.
4. Answers, and ends with the window and regions it checked.

For "Were last night's uploads to acme-raw processed?" it calls `s3_triggers` for the bucket and the window. That reads the bucket's event notifications (which functions it invokes, on which events, for which keys), lists the files written in the window, checks that the function is there and that its resource policy lets the bucket invoke it, and reads the function's runs and failures from CloudWatch for the same window. The findings name what broke the link: files whose ending is `.CSV` when the filter says `.csv` (S3 compares case-sensitively), files uploaded in parts when the notification fires only on PUT uploads, a function that isn't there, a permission that's missing (with the `aws lambda add-permission` command), or no file arriving at all. Then `function_errors` reads why the runs that did happen failed.

Follow-ups continue the conversation, so "why did it fail?" knows what "it" is. `new=True` or `agent.new_conversation()` starts a fresh one. The kernel's stop button stops a question, and the report shows what it found until then.

## What it can call { #tools }

Fourteen tools in two toolkits, each a building block over `LambdaAnalyzer` or `S3Analyzer` rather than one tool per question. They take parameters (a time window, a name glob, regions, a location, a search), so a few of them answer many questions, and each returns a compact summary with what it covered instead of raw AWS data.

**Lambda**

| Tool | What it returns |
|---|---|
| `find_functions(match, regions)` | The functions: runtime and whether AWS still supports it, memory, timeout, when each last changed |
| `function_health(since, until, match, regions)` | CloudWatch's counts for every function in a window, sorted into failed, throttled, near their timeout and not called |
| `function_errors(function, since, until, region)` | One function's errors from its logs, grouped by cause, with counts, an example line and request IDs |
| `function_logs(function, since, until, search, region)` | Its runs, newest first: status, run time, memory, cold start and last lines; `search=` finds a request ID, an order ID or an error |
| `function_performance(function, since, until, region)` | Median, p95, p99 and longest run against the timeout, timeouts, cold starts, and the memory it needs |
| `function_details(function, region)` | Settings, triggers, aliases, its last 30 days, log retention, the estimated monthly cost by part, and every finding |
| `function_code(function, region)` | What a Python function's code calls, read with `ast` and never run: AWS operations, environment variables by name, event fields |

**S3**, which never reads what's inside a file:

| Tool | What it returns |
|---|---|
| `find_buckets(match, sizes)` | The buckets and their regions; `sizes=True` adds each one's object count and size from CloudWatch, biggest first |
| `bucket_details(bucket)` | Public access, encryption, versioning, lifecycle rules in plain words, replication, logging, tags, its event notifications, size and monthly cost, and its findings |
| `list_folder(location)` | One level of a folder, like `aws s3 ls`: its subfolders, and its files newest first |
| `find_files(location, match, since, until, min_size, max_size, limit)` | The files under a location that match a name glob, a time window or a size: how many, how big, the newest |
| `folder_summary(location)` | What a location holds by file type, storage class and folder, the largest files, the monthly cost and findings |
| `file_info(location)` | One file's headers: size, type, storage class (Glacier needs a restore first), encryption (a KMS key means readers need `kms:Decrypt`), metadata and tags |

**S3 and Lambda together:**

| Tool | What it returns |
|---|---|
| `s3_triggers(bucket, function, since, until, region)` | Which bucket events invoke which functions, on which keys. Give a bucket, a function, or both. For each link: whether the function is there and lets the bucket invoke it, how many files its filter covers arrived in the window and how many of them ran it (and which were missed, by the case of their ending or by how they were uploaded), and the function's runs, failures and throttles in that window |

`s3_triggers` is there when the agent has both toolkits, as it does by default. From a function, it finds the buckets its resource policy names; when the policy lets any bucket invoke it (or none), it looks through the buckets in the function's region, up to 50.

`agent.tools()` lists them with the AWS reads each makes. `agent.core.tools()` returns the functions themselves, with type hints and docstrings, so another agent framework can use them too.

## Safe and bounded { #safety }

- **It only reads.** The tools call the analyzers' read methods. A guard on every AWS client they use refuses any operation that isn't a Get, List, Describe, Head or Filter call before its request is built, so even a mistake in a tool can't change your account. The S3 tools read names, sizes, dates and settings, never what's inside a file. There's no shell or code execution: deepagents' `execute` tool and its general-purpose subagent are turned off, and its file tools only keep notes in memory, never on disk.
- **It says what it checked.** Every tool result carries its window, its regions and whether it was cut short, and the agent is told to say so in the answer. A permission the role lacks becomes part of the answer (`AccessDeniedException; needs logs:FilterLogEvents`), not an error, and the report lists it.
- **Log lines are data.** What the tools read can contain text that looks like instructions ("ignore your instructions..."). The agent is told never to follow it, and the tools hide anything that looks like a key, token or password before the model sees it.

### The budget { #budget }

Each question has limits. Past one, the tools stop running and tell the model to answer with what it has; the report says what was cut short and how to raise the limit.

| Limit | Default | Past it |
|---|---|---|
| `tool_calls` | 25 | Tools stop; it answers with what it has |
| `seconds` | 300 | The same |
| `usd` | 1.00 | The same, when `model_prices=` says what its tokens cost |
| `steps` | 80 | The agent stops (LangGraph's recursion limit) |
| `log_days` | 3 | A longer log read is refused; CloudWatch's counts still cover the window |
| `s3_keys` | 100,000 | An S3 listing stops there and says so: its counts cover the keys it read |

```python
agent = AgentView(budget={"tool_calls": 50, "log_days": 7})
```

Log reads are limited because searching weeks of a busy function's logs is slow. For long windows the agent uses CloudWatch's counts, which cover months in seconds, and reads logs only for a short window around what it found. S3 listings are limited for the same reason: S3 lists 1,000 keys per request, in key order, so the agent is told to give `find_files` and `folder_summary` the narrowest prefix it can (a day's folder rather than the bucket).

## Use the data in Python { #python }

`agent.core` is the `AgentAnalyzer`. Its `ask()` returns an `AgentAnswer` instead of a report:

```python
answer = agent.core.ask("Which Lambda failed last night?")
answer.text                    # the answer, in markdown
answer.calls                   # ToolCall: name, args, coverage, summary, error, refused, seconds
answer.aws_calls               # {'logs:FilterLogEvents': 12, ...}: every AWS call it made, all reads
answer.input_tokens, answer.output_tokens, answer.cost
answer.stopped                 # None, or why it stopped early: 'tool_calls', 'time', 'cost', 'steps', 'stopped', 'error'
answer.error                   # what went wrong with the model, and what to do
agent.core.history             # every AgentAnswer so far
```

Settings, all optional:

```python
from aws_analyzer import AgentAnalyzer, AgentView

core = AgentAnalyzer(
    deployment="prod-gpt-4o", endpoint="https://acme-ai.openai.azure.com", api_key="...",
    model_prices={"gpt-4o": (2.50, 10.00)},          # USD per 1M input and output tokens, from your price sheet
    timezone="Asia/Kolkata",                         # for the agent alone; set_timezone() sets it everywhere
    regions=["us-east-1", "eu-west-1"],              # where it looks unless asked otherwise; "all" for every region
    notes="orders-etl runs at 02:00 IST and loads the day's orders",   # facts it's told about your account
    budget={"tool_calls": 25, "log_days": 3, "s3_keys": 100_000},
    toolkits=["lambda", "s3"],                       # the default; ["s3"] for S3 alone
)
agent = AgentView(core)
```

`chat_model=` takes any LangChain chat model instead of Azure OpenAI: for a model on Amazon Bedrock, `ChatBedrockConverse` from langchain-aws. `lambda_core=` and `s3_core=` take a `LambdaAnalyzer` and an `S3Analyzer` of your own (with their own `prices=`). The pure functions work without AWS or a model: `hide_secrets`, `asked_at`, `system_prompt`, `summarize_health`, `summarize_errors`, `summarize_runs`, `summarize_performance`, `summarize_detail`, `summarize_code`, the S3 toolkit's `summarize_bucket`, `summarize_found`, `summarize_prefix`, `summarize_file`, `count_arrivals` (from a listing: which files a notification's filter and event types covered or missed) and `trigger_findings`, `answer_findings` and `explain_model_error`.

### Your own tools { #toolkits }

The agent's tools come in toolkits, one per AWS service: `toolkits=["lambda", "s3"]` is the default. A toolkit of your own adds tools beside them. Give it a name, a sentence for the model on what it covers, and `tools()`: plain functions whose type hints and docstrings tell the model how to call them, each returning a dict.

```python
from aws_analyzer.agent import AgentView, Toolkit

class Runbooks(Toolkit):
    """What the team's runbooks say to do when a function fails."""

    name, title = "runbooks", "Runbooks"
    covers = "the team's runbooks: what to do when a Lambda function fails."

    def make_analyzer(self, session):  # what its tools read through: here a dict, usually an analyzer
        return {"orders-etl": "Re-run it for the day that failed; page #data-oncall if it fails twice."}

    def tools(self):
        def runbook(function: str) -> dict:
            """What the team's runbook says to do when this Lambda function fails."""
            return {"function": function, "steps": self.analyzer.get(function, "There's no runbook for it.")}

        return [runbook]

agent = AgentView(toolkits=["lambda", "s3", Runbooks()])
agent.ask("orders-etl failed last night: what should I do?")
```

Its tools get the same budget, error handling and report as the built-in ones, and `tools()` lists them. When it reads AWS, make its clients in the analyzer `make_analyzer()` returns, from the session it's given: the read-only guard covers every client that analyzer makes from its session, and the clients it already holds. A tool that joins your service to another reads that one through the agent's own toolkit for it, `self.peer("lambda")`, which is `None` when the agent hasn't got it.

## Cost { #cost }

Each answer counts the model's input and output tokens. Azure OpenAI's prices depend on the model, the region and the deployment type, so there are none built in: pass `model_prices=` and each answer shows its estimated cost, and the budget's `usd` limit applies. Without it the cost shows as unknown, never a guess.

On the AWS side, the CloudWatch metrics it reads cost $0.01 per 1,000, and S3's requests $0.005 per 1,000 listing pages (each lists up to 1,000 keys) and $0.0004 per 1,000 others; the report says how many of each it made and what they cost. Reading logs, Lambda's settings and code costs nothing. `function_health` reads 7 metrics per function, so a question about 100 functions reads about 700: under a cent. A `find_files` over 100,000 keys makes 100 listing requests: $0.0005.

## Permissions { #permissions }

The Lambda tools read through `LambdaAnalyzer`, so the role needs the [Lambda guide's read-only policy](lambda_functions.md#permissions). The S3 tools read through `S3Analyzer`, so they need the [S3 guide's read-only policy](s3.md#permissions) (with `s3:GetBucketNotification`, which `bucket_details` and `s3_triggers` read the bucket's event notifications with). `s3_triggers` needs both. Nothing more: no Bedrock and no permission to change anything. A permission that's missing shows up in the answer as what couldn't be read, and the rest of the answer still stands.

On the Azure side, an API key of the Azure OpenAI resource, or a Microsoft Entra ID identity with the **Cognitive Services OpenAI User** role on it.

## Troubleshooting { #troubleshooting }

??? question "“The agent needs Python 3.11 or newer”"

    deepagents needs Python 3.11. On SageMaker, pick a kernel with Python 3.11 or newer, or make one with the SageMaker guide's [`new_env("3.11")`](sagemaker_env.md#envs), then `%pip install "aws-analyzer[agent]"` in it.

??? question "“… AZURE_OPENAI_ENDPOINT … are not set”"

    The agent doesn't know which deployment to ask. Set `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY` and `AZURE_OPENAI_DEPLOYMENT` in the notebook's environment, or pass them to `AgentView`.

??? question "“NotFoundError … No such deployment on this endpoint”"

    `AZURE_OPENAI_DEPLOYMENT` is the name you gave the deployment in Azure AI Foundry, which can differ from the model's name. An old `api_version` can also give a 404.

??? question "“Azure's content filter blocked the prompt or the answer”"

    Log lines can set off Azure's content filter. Ask about a narrower window, or adjust the deployment's content filter.

??? question "`s3_triggers` says the function doesn't let the bucket invoke it"

    S3 can only invoke a function whose resource-based policy allows `s3.amazonaws.com` for that bucket. The finding gives the `aws lambda add-permission ...` command to run; the agent never runs it. If the permission is there and the finding still says so, the role may lack `lambda:GetPolicy`: the answer then lists it under what couldn't be read.

??? question "It answered, but says it was stopped at the budget"

    The question needed more tool calls, time or log days than the budget allows. Ask a narrower question (one function, a shorter window), or raise the limit it names: `AgentView(budget={"tool_calls": 50})`.

## Command reference { #reference }

<div class="ref" markdown>

| Command | What it shows |
|---|---|
| `ask(question, new=False)` | The answer first, then what it checked: each tool call with its window, regions and what it found, what it couldn't read or was refused, the AWS reads, tokens and cost, and the `LambdaView` and `S3View` commands to look closer |
| `new_conversation()` | Starts a new conversation: the next question won't see the earlier ones |
| `transcript()` | Every question asked so far and its answer, with the tool calls, tokens and cost of each, and the total |
| `tools()` | Each tool and the AWS reads it makes; the model, regions, time zone and budget; and what's sent to the model |
| `help(command=None)` | This list, grouped by task; `help("name")` shows one command in full |

</div>
