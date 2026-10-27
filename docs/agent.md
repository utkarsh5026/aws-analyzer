---
title: Ask in Plain English (Agent) Guide
description: "How to ask questions about your AWS account in plain English from a SageMaker notebook with aws-analyzer's agent: an agent (a model you pick, on Amazon Bedrock, OpenAI, Azure OpenAI or any LangChain provider, run by LangChain's deepagents) answers questions like which Lambda failed last night, or whether last night's uploads to a bucket were processed, from read-only tools, says what it checked, and stays within a budget per question."
---

<p class="eyebrow"><img class="aws-icon" src="images/aws/lambda.svg" alt="" width="32" height="32"> Ask in plain English <code>agent/</code></p>

# Ask your AWS account a question, and get an answer you can check

Ask "Which Lambda failed last night?" in a notebook cell and get the answer: which functions, how often, why, and the run that shows it. Every answer says what it checked, what it couldn't read and what it cost.
{ .lede }

<ul class="pills">
  <li>Read-only: a guard refuses anything else</li>
  <li>Says what it checked</li>
  <li>A budget per question</li>
  <li>Follow-up questions</li>
  <li>Lambda and S3, joined, and knowledge bases</li>
  <li>Any model: Bedrock, OpenAI, Azure, ...</li>
</ul>

It covers AWS Lambda, Amazon S3 and Amazon Bedrock Knowledge Bases. The model is yours to pick (one on Amazon Bedrock keeps everything in AWS), run by LangChain's [deepagents](https://pypi.org/project/deepagents/). The examples use acme's `orders-etl` from the [Lambda guide](lambda_functions.md), which loads each day's orders from the files that arrive in the `acme-raw` bucket.
{ .muted }

## Set up in SageMaker { #setup }

<div class="steps" markdown>

1. **Install it with the agent's extra** in a notebook cell. It needs Python 3.11 or newer.

    ```bash
    %pip install "aws-analyzer[agent]"
    ```

    Add `sandbox`, `"aws-analyzer[agent,sandbox]"`, for its [Python sandbox](#run-python).

2. **Pick the model it answers with.** On SageMaker the simplest is a model on Amazon Bedrock: it uses the notebook's IAM role, needs no key, and nothing leaves AWS.

    ```python
    from aws_analyzer import AgentView, set_timezone

    set_timezone("Asia/Kolkata")   # so "last night" and "today" mean your night and your day
    agent = AgentView(provider="bedrock")                    # Claude Haiku 4.5; model="sonnet" for another
    # agent = AgentView(model="openai:gpt-4.1")              # OpenAI, with OPENAI_API_KEY set
    # agent = AgentView()                                    # Azure OpenAI, from AZURE_OPENAI_ENDPOINT, _API_KEY, _DEPLOYMENT
    # agent = AgentView(model="anthropic:claude-sonnet-4-5") # any provider LangChain supports
    agent.help()
    ```

    It reads AWS with the notebook's IAM execution role and region, like every other view, and `agent.tools()` shows which model it picked.

</div>

??? more "Every model it can use, and time zones"

    | Model | How | Needs |
    |---|---|---|
    | Amazon Bedrock | `AgentView(provider="bedrock")`, Claude Haiku 4.5; `model=` takes a short name (`"sonnet"`, `"nova-pro"`), a model ID or an inference profile, and finds a short name's ID in the region's model list | `bedrock:InvokeModel` on the model; its cost uses Bedrock's list prices |
    | OpenAI | `AgentView(model="openai:gpt-4.1")` (gpt-4.1 is the default); `endpoint=` for any server that speaks OpenAI's API: LiteLLM, vLLM, OpenRouter | `OPENAI_API_KEY`, or `api_key=` |
    | Azure OpenAI | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY` and `AZURE_OPENAI_DEPLOYMENT` in the environment, or `AgentView(endpoint=..., deployment=..., api_key=...)`; `azure_ad_token_provider=` (from `azure.identity.get_bearer_token_provider`) for Microsoft Entra ID; the API version is `2024-10-21` unless `OPENAI_API_VERSION` or `api_version=` says otherwise | A deployment of a model that supports tool calling |
    | Any LangChain provider | `model="anthropic:claude-sonnet-4-5"`, `"google_genai:gemini-2.5-pro"`, `"ollama:llama3.1"`, ...; or `chat_model=` with a LangChain chat model of your own | Its LangChain package (Anthropic's and Google's come with the extra) and its key |

    Without `provider=`, the agent works it out from the model's name and the environment: `AWS_ANALYZER_AGENT_MODEL` first (`os.environ["AWS_ANALYZER_AGENT_MODEL"] = "bedrock:sonnet"`, good for a lifecycle configuration), then the Azure settings, then `OPENAI_API_KEY`.

    [`set_timezone()`](index.md#timezone) is the package's own: every report then shows its times in your zone too, and the agent's table of what it checked does. `AgentView(timezone="Europe/London")` gives the agent a zone of its own instead. Without either, the agent works in UTC and says so.

    ```python
    agent = AgentView(timezone="kolkata")   # a tz database name, its city, or an offset like "+05:30"
    ```

    The agent needs deepagents, langchain-openai and langchain-aws, which need Python 3.11; the rest of aws-analyzer still runs on 3.10 with boto3 alone.

!!! tip "Worth knowing"

    **What leaves AWS.** Your question and what the tools return (function and bucket names, settings, counts, file names, error messages and log lines, and the lines of a function's code it reads to explain an error, with anything that looks like a key or password hidden) are sent to the model's provider. With a model on Amazon Bedrock it stays in your AWS account; anywhere else, use a provider your organisation allows for that data. Environment variable values and secrets files packed with a function (`.env`, keys) are never read, and what's inside your S3 files isn't either unless you turn on [`read_files=True`](#read-files).

## Start here { #tour }

Ask the way you'd ask a colleague. Every answer says what it checked, so you know how far to trust it.
{ .gist }

```python
agent.chat()                                                   # a chat window: ask, and watch it work
agent.ask("Which Lambda failed last night?")                   # or ask in a cell, and get a report
agent.ask("Why did orders-etl fail?")                          # a follow-up: it remembers the conversation
agent.ask("Show me one of those runs")                         # ...and again
agent.ask("Were last night's uploads to acme-raw processed?", new=True)   # S3 and Lambda together
agent.ask("Which buckets are public, and what do they cost?", new=True)
agent.ask("Which functions run close to their timeout this week?", new=True)   # a new conversation
agent.tools()                                                  # what it can call, its model and its budget
agent.transcript()                                             # every question and answer so far, with their cost
agent.memory()                                                 # with memory=True: what it remembers between conversations
```

With a Python sandbox (`AgentView(python="local")`, [below](#run-python)), it can also read your files and answer with charts: `agent.ask("Chart the orders in s3://acme-raw/sales/ by month")`, then `agent.save_charts()` for the pictures.

Each answer comes with **What it checked**: a line per tool call with the window and regions it covered and what it found. Then what it couldn't read, its cost, and the `LambdaView` and `S3View` commands that show the same as a full report.

## The chat window { #chat }

`agent.chat()` opens a conversation with the agent in the notebook, drawn like a terminal, and shows every call it makes while it works.
{ .gist }

```python
agent = AgentView(provider="bedrock")
agent.chat()
```

Type a question and press Enter, or click one of the questions to start from.

![The agent's chat window, empty, in a monospace font: a header with aws-agent, claude-haiku-4-5 @ bedrock, five small service badges (Lambda's orange λ, CloudWatch Logs' pink lines, CloudWatch's pink chart, S3's green bucket and Bedrock's teal spark), a green read-only, and the ⚙ model and new chat buttons; then a short line on what the agent does, a legend of those badges named lambda, logs, cloudwatch, s3 and bedrock, and under # try five questions, each with the badges of the services it reads; at the bottom a › prompt to type in, a send button, and a line saying enter sends, stop ends a question with what it found, and it's read-only](images/agent-chat-start-light.webp#only-light){ width="984" height="728" loading=lazy }
![The agent's chat window, empty, in a monospace font: a header with aws-agent, claude-haiku-4-5 @ bedrock, five small service badges (Lambda's orange λ, CloudWatch Logs' pink lines, CloudWatch's pink chart, S3's green bucket and Bedrock's teal spark), a green read-only, and the ⚙ model and new chat buttons; then a short line on what the agent does, a legend of those badges named lambda, logs, cloudwatch, s3 and bedrock, and under # try five questions, each with the badges of the services it reads; at the bottom a › prompt to type in, a send button, and a line saying enter sends, stop ends a question with what it found, and it's read-only](images/agent-chat-start-dark.webp#only-dark){ width="984" height="728" loading=lazy }
/// caption
**Start here**: the badges show which AWS services the agent reads; every question to start from shows the ones it needs.
///

### Pick the model in the window { #chat-model }

**⚙ model** in the window's head opens its model settings: pick a provider, type or pick a model, and press **use this model**.
{ .gist }

The list holds Amazon Bedrock, OpenAI, Azure OpenAI, Anthropic, Google Gemini and Ollama, then every other provider LangChain knows. With Bedrock picked, the model box suggests the models your region offers. The panel asks only for what that provider takes: a base URL or Azure endpoint, an API key, Azure's API version. Under them it says where your questions would go and how the tokens are priced.

![The chat window with its model settings open under the header, where the ⚙ model button is highlighted: provider, a list showing Amazon Bedrock; model, a box holding claude-haiku-4-5; then → what the agent sends goes to Amazon Bedrock in your own AWS account (claude-haiku-4-5), so it stays in AWS, and $ priced at Amazon Bedrock's list prices, $1.10 in and $5.50 out per 1M tokens; then the use this model and close buttons, above the empty conversation's greeting and questions to start from](images/agent-chat-model-light.webp#only-light){ width="984" height="894" loading=lazy }
![The chat window with its model settings open under the header, where the ⚙ model button is highlighted: provider, a list showing Amazon Bedrock; model, a box holding claude-haiku-4-5; then → what the agent sends goes to Amazon Bedrock in your own AWS account (claude-haiku-4-5), so it stays in AWS, and $ priced at Amazon Bedrock's list prices, $1.10 in and $5.50 out per 1M tokens; then the use this model and close buttons, above the empty conversation's greeting and questions to start from](images/agent-chat-model-dark.webp#only-dark){ width="984" height="894" loading=lazy }
/// caption
**⚙ model**: pick the provider and model; the panel asks only for what that provider takes, and says where your questions go.
///

!!! tip "Worth knowing"
    Leave the key empty to use the provider's environment variable (`OPENAI_API_KEY`, ...). A key you type is kept for the session only and never shown again. Switching starts a new conversation: the one above stays on screen. If the model can't be made (a key or package missing), the panel says why and the agent keeps the one it had. From a cell, `agent.use_model("openai", "gpt-4.1")` does the same.

### Every call, with the services it reads { #chat-calls }

Each tool call shows as the code it is, with a badge for every AWS service it reads, so you can tell at a glance where the data came from:

<div class="grid cards" markdown>

- **λ orange: AWS Lambda.** The functions, their settings and code.
- **≡ pink: CloudWatch Logs.** The log lines, run by run.
- **↗ pink: CloudWatch.** The numbers: calls, errors, run times.
- **Bucket, green: Amazon S3.** Buckets, folders and files.

</div>

Before each set of calls the agent says what it's about to check and why (`●`), the way a coding assistant does, and when a result changes its plan it says what it found and where it's going next. Point at a badge to see the service's full name. A comment beside the call says what it does in plain English, and the line under it (`⎿`) says what it found.

![The chat window while the agent works: on the right, the question Which Lambda failed in the last 24 hours, and why?, marked with a person emoji; under it its plan, Plan · 2 of 3 done, two steps ticked and Say what to fix first under way; ● I'll check every function's failures over the last 24 hours first; then the Lambda, Logs and CloudWatch badges before function_health() # checked every function's health, ✓ 0.2s, and under it 2 of 5 functions had failed calls: orders-etl (66), report-api (20); ● orders-etl failed the most, let me read its logs; the same badges before function_errors('orders-etl') # read orders-etl's errors, with 13 errors, 3 causes: KeyError ×8, Timeout ×3, ClientError ×2; ✻ Thinking… with the time so far, 2 tool calls, 1 queued, and stop ends it with what it found; under it a follow-up typed meanwhile, How do I fix the KeyError in orders-etl?, dimmed in a dashed box marked queued, with a ✕ beside it; the send button reads stop, and the line under the box says the question is asked once this answer is done](images/agent-chat-working-light.webp#only-light){ width="984" height="728" loading=lazy }
![The chat window while the agent works: on the right, the question Which Lambda failed in the last 24 hours, and why?, marked with a person emoji; under it its plan, Plan · 2 of 3 done, two steps ticked and Say what to fix first under way; ● I'll check every function's failures over the last 24 hours first; then the Lambda, Logs and CloudWatch badges before function_health() # checked every function's health, ✓ 0.2s, and under it 2 of 5 functions had failed calls: orders-etl (66), report-api (20); ● orders-etl failed the most, let me read its logs; the same badges before function_errors('orders-etl') # read orders-etl's errors, with 13 errors, 3 causes: KeyError ×8, Timeout ×3, ClientError ×2; ✻ Thinking… with the time so far, 2 tool calls, 1 queued, and stop ends it with what it found; under it a follow-up typed meanwhile, How do I fix the KeyError in orders-etl?, dimmed in a dashed box marked queued, with a ✕ beside it; the send button reads stop, and the line under the box says the question is asked once this answer is done](images/agent-chat-working-dark.webp#only-dark){ width="984" height="728" loading=lazy }
/// caption
**While it works**: its plan, ticking off, what the model says before it acts (`●`), each call with its services and what it found, a question you typed meanwhile waiting its turn, and **stop** to end it with what it found so far.
///

Once it has answered, the plan stays as it left it, the steps fold into one line (how many calls, how long, and the services they read) and the answer leads. Under it: anything you should know about how it was reached, who answered, what it took and cost, and **details** with every call, the AWS reads and how the cost was worked out.

![The same conversation once the agent has answered: its plan, Plan · 3 of 3 done, every step ticked; a folded line 2 tool calls · 14s with the lambda, logs and cloudwatch badges named, then the answer behind a blue rule, marked with a robot emoji: orders-etl failed 66 of its 2,226 calls in the last 24 hours, with three causes, a KeyError on customer_id in etl.py line 23, timeouts at the 60 s limit, and AccessDeniedException on dynamodb:PutItem; report-api's errors and throttles; the call to look closer; then claude-haiku-4-5 · 2 tool calls · 14s · $0.03 (est.) and a details link](images/agent-chat-light.webp#only-light){ width="984" height="728" loading=lazy }
![The same conversation once the agent has answered: its plan, Plan · 3 of 3 done, every step ticked; a folded line 2 tool calls · 14s with the lambda, logs and cloudwatch badges named, then the answer behind a blue rule, marked with a robot emoji: orders-etl failed 66 of its 2,226 calls in the last 24 hours, with three causes, a KeyError on customer_id in etl.py line 23, timeouts at the 60 s limit, and AccessDeniedException on dynamodb:PutItem; report-api's errors and throttles; the call to look closer; then claude-haiku-4-5 · 2 tool calls · 14s · $0.03 (est.) and a details link](images/agent-chat-dark.webp#only-dark){ width="984" height="728" loading=lazy }
/// caption
**The answer first**, with its plan as it left it and the steps folded above it into one line that names the services they read.
///

### The data behind each step { #chat-data }

Click the folded line to see the steps again, and click a call to see what's behind it: the AWS operations it used, the time window and regions it covered, the `LambdaView` or `S3View` calls that show the same as a full report, and **the data the model read**, the exact result the tool handed it, as JSON you can fold.

![The steps unfolded in the dark theme, with function_health() opened: reads, lambda:ListFunctions, lambda:ListEventSourceMappings, lambda:GetAccountSettings, logs:DescribeLogGroups, cloudwatch:GetMetricData; covered, the 24 hours and us-east-1; next, LambdaView().errors(...) for orders-etl and report-api; and data the model read · 1.2 KB opened as a JSON tree with the window, regions, functions_checked: 5 and the failed functions, orders-etl with 2,226 calls and 66 errors](images/agent-chat-details-light.webp#only-light){ width="984" height="728" loading=lazy }
![The steps unfolded in the dark theme, with function_health() opened: reads, lambda:ListFunctions, lambda:ListEventSourceMappings, lambda:GetAccountSettings, logs:DescribeLogGroups, cloudwatch:GetMetricData; covered, the 24 hours and us-east-1; next, LambdaView().errors(...) for orders-etl and report-api; and data the model read · 1.2 KB opened as a JSON tree with the window, regions, functions_checked: 5 and the failed functions, orders-etl with 2,226 calls and 66 errors](images/agent-chat-details-dark.webp#only-dark){ width="984" height="728" loading=lazy }
/// caption
**Check its work**: each call opens to the operations it used, what it covered and the exact data the model read.
///

- **Follow-ups** continue the conversation. **new chat** starts over (`agent.transcript()` still has every question).
- **stop** (the send button, while it works) ends the question: the agent finishes the step it's on and answers with what it found.
- **Ask the next question while it works**: press Enter and it waits under the conversation, marked *queued*, and is asked as a follow-up once the answer before it is done. Questions you queue are asked in order; **✕** drops one, and **stop** ends the current answer so the next one starts sooner.
- Questions asked with `agent.ask()` in other cells show up in the window too.

??? more "How it works"

    The question runs on a worker thread, so the window keeps answering clicks while the agent works. The agent hands each step to the window as it happens (`AgentAnalyzer.ask(on_step=...)`, below), and the window draws it. Each step comes whole: the model's reply isn't streamed word by word, which on Amazon Bedrock would need another permission (`bedrock:InvokeModelWithResponseStream`). The window lives in the kernel, so reopening the notebook loses it: `agent.transcript()` shows the conversation as a report that stays. `agent.chat(height=800)` sets the conversation's height (pixels, or CSS such as `'70vh'`); it fills the browser window otherwise.

### Its plan, ticked off as it works { #chat-plan }

For a question that takes three or more calls, the agent first writes a short plan, and it shows under your question as a checklist: `○` still to do, `▸` under way, `✓` done.
{ .gist }

![The chat window while the agent works on Which Lambda failed in the last 24 hours, and why?: under the question, a box headed Plan · 2 of 3 done, with Check every function's failures in the last 24 hours and Read the logs of the ones that failed ticked in green, and Say what to fix first marked under way with a blue arrow; below it the model's words, the two tool calls it made, function_health() and function_errors('orders-etl'), each with what it found, and ✻ Thinking… with stop ends it with what it found](images/agent-chat-plan-light.webp#only-light){ width="984" height="728" loading=lazy }
![The chat window while the agent works on Which Lambda failed in the last 24 hours, and why?: under the question, a box headed Plan · 2 of 3 done, with Check every function's failures in the last 24 hours and Read the logs of the ones that failed ticked in green, and Say what to fix first marked under way with a blue arrow; below it the model's words, the two tool calls it made, function_health() and function_errors('orders-etl'), each with what it found, and ✻ Thinking… with stop ends it with what it found](images/agent-chat-plan-dark.webp#only-dark){ width="984" height="728" loading=lazy }
/// caption
**Its plan**: two steps ticked off, the third under way, and the calls that did the first two under it.
///

A plan reads like "check every function's health → read orders-etl's errors → read the code where it fails". It ticks off in place as the agent works, and it changes when a result does: a step added or dropped. Once the question is answered, the plan stays above the folded steps as the agent left it. A step it didn't mark done shows under the answer as a finding, so you can check the answer covers it. A question one or two calls answer gets no plan. The report from `ask()` shows the same checklist above the table of what it checked.

??? more "How it works, and turning it off"

    The plan is LangChain's `TodoListMiddleware`, which deepagents doesn't add by itself: it gives the model a `write_todos` tool, with short instructions written for this agent. Writing a plan reads nothing from AWS, so it isn't counted against the budget's tool calls or listed among them. Its instructions are sent with every model call; `AgentView(plan=False)` leaves them out to save those tokens. `answer.plan` holds the steps (`PlanStep`: `.text`, `.status`).

## How it answers { #how-it-works }

The model picks which read-only tools to call. For "Which Lambda failed last night?" it usually:
{ .gist }

1. Turns "last night" into a window in your time zone (`set_timezone()`'s; 18:00 yesterday to 08:00 today, unless you say otherwise) and converts it to UTC, which CloudWatch uses. Each question is sent with the time it was asked, in your time zone and in UTC.
2. Calls `function_health` for that window: CloudWatch's counts for every function at once, in a few seconds.
3. Calls `function_errors` for the functions that failed, which reads their logs and groups the errors by cause.
4. Answers, and ends with the window and regions it checked.

??? more "How it checks that uploads were processed"

    For "Were last night's uploads to acme-raw processed?" it calls `s3_triggers` for the bucket and the window. That reads the bucket's event notifications (which functions it invokes, on which events, for which keys), lists the files written in the window, checks that the function is there and that its resource policy lets the bucket invoke it, and reads the function's runs and failures from CloudWatch for the same window. The findings name what broke the link: files whose ending is `.CSV` when the filter says `.csv` (S3 compares case-sensitively), files uploaded in parts when the notification fires only on PUT uploads, a function that isn't there, a permission that's missing (with the `aws lambda add-permission` command), or no file arriving at all. Then `function_errors` reads why the runs that did happen failed.

Follow-ups continue the conversation, so "why did it fail?" knows what "it" is. `new=True` or `agent.new_conversation()` starts a fresh one. The kernel's stop button stops a question, and the report shows what it found until then.

## What it can call { #tools }

Eighteen read-only tools over Lambda and S3, and seven for [Bedrock knowledge bases](#knowledge-bases). Each returns a compact summary with what it covered, never raw AWS data.
{ .gist }

`agent.tools()` lists them with the AWS reads each makes. `agent.core.tools()` returns the functions themselves, with type hints and docstrings, so another agent framework can use them too.

??? more "Every tool, and what it returns"

    They're building blocks over `LambdaAnalyzer` and `S3Analyzer` rather than one tool per question: they take a time window, a name glob, regions, a location or a search, so a few of them answer many questions.

    **Lambda**

    | Tool | What it returns |
    |---|---|
    | `find_functions(match, regions)` | The functions: runtime and whether AWS still supports it, memory, timeout, when each last changed |
    | `function_health(since, until, match, regions)` | CloudWatch's counts for every function in a window, sorted into failed, throttled, near their timeout and not called |
    | `function_errors(function, since, until, region)` | One function's errors from its logs, grouped by cause, with counts, an example line, request IDs and the file and line its traceback names |
    | `function_logs(function, since, until, search, region)` | Its runs, newest first: status, run time, memory, cold start and last lines; `search=` finds a request ID, an order ID or an error |
    | `function_performance(function, since, until, region)` | Median, p95, p99 and longest run against the timeout, timeouts, cold starts, and the memory it needs |
    | `function_details(function, region)` | Settings, triggers, aliases, its last 30 days, log retention, the estimated monthly cost by part, and every finding |
    | `function_code(function, region)` | What a Python function's code calls, read with `ast` and never run: AWS operations, environment variables by name, event fields |
    | `function_source(function, file, lines, region)` | The function's code, numbered line by line, like `sed -n`: `lines="42"` for the lines around the one an error's traceback names, `"150-300"` for any range, up to 300 lines a call; with no file, the handler's file and the package's own files. Nothing is run, and anything that looks like a secret is hidden |
    | `search_code(function, text, regex, files, region)` | Every line of the code that contains a text or matches a regular expression, like `grep -rn`, with the lines around each: its own files, or `files=` globs and folders, the libraries packed with it too |
    | `find_symbol(function, name, region)` | Where a Python function, class, method or name is defined (file, lines, signature, docstring) and every line that calls, imports or refers to it, like an editor's go to definition. Read with `ast` and never run |
    | `diff_versions(function, old, new, region)` | What changed between two versions, like `git diff`: settings, environment variable names, libraries, each changed file with the Python functions its changes are in, and the changed lines. `function_errors` says which versions each cause ran on |

    **S3**, which reads what's inside a file only with [`read_files=True`](#read-files):

    | Tool | What it returns |
    |---|---|
    | `find_buckets(match, sizes)` | The buckets and their regions; `sizes=True` adds each one's object count and size from CloudWatch, biggest first |
    | `bucket_details(bucket)` | Public access, encryption, versioning, lifecycle rules in plain words, replication, logging, tags, its event notifications, size and monthly cost, and its findings |
    | `list_folder(location)` | One level of a folder, like `aws s3 ls`: its subfolders, and its files newest first |
    | `find_files(location, match, since, until, min_size, max_size, limit)` | The files under a location that match a name glob, a time window or a size: how many, how big, the newest |
    | `folder_summary(location)` | What a location holds by file type, storage class and folder, the largest files, the monthly cost and findings |
    | `file_info(location)` | One file's headers: size, type, storage class (Glacier needs a restore first), encryption (a KMS key means readers need `kms:Decrypt`), metadata and tags |
    | `preview_file(location, rows)` | Only with `read_files=True`: what's inside one file, read like `S3View().preview()`: a table's columns and first rows, a JSON file's start, a text file's first lines, a PDF's or Word file's text, an archive's files. Secrets are hidden; images, audio, video and binary files give only their type and size |

    **S3 and Lambda together:**

    | Tool | What it returns |
    |---|---|
    | `s3_triggers(bucket, function, since, until, region)` | Which bucket events invoke which functions, on which keys. Give a bucket, a function, or both. For each link: whether the function is there and lets the bucket invoke it, how many files its filter covers arrived in the window and how many of them ran it (and which were missed, by the case of their ending or by how they were uploaded), and the function's runs, failures and throttles in that window |

    `s3_triggers` is there when the agent has both toolkits, as it does by default. From a function, it finds the buckets its resource policy names; when the policy lets any bucket invoke it (or none), it looks through the buckets in the function's region, up to 50.

    **Bedrock knowledge bases**, with [their toolkit](#knowledge-bases):

    | Tool | What it returns |
    |---|---|
    | `find_knowledge_bases(match)` | The knowledge bases in the region: status, vector store, embedding model, each data source's last sync, and how many warnings each has |
    | `kb_health(knowledge_base)` | Its status, settings in plain words, estimated idle cost, each data source's chunking, parsing and last syncs, its documents by status with why they failed, and its findings |
    | `kb_syncs(knowledge_base, data_source, n)` | Its syncs newest first, with what each indexed and why a failed one failed, and the command that syncs each data source again |
    | `kb_files(knowledge_base, state, match, data_source, limit)` | Each file of its S3 data sources next to Bedrock's record: failed (and why), changed since it was indexed, new since the last sync, skipped, deleted from S3, or indexed |
    | `search_kb(question, knowledge_base, n, data_source, search_type)` | The passages a question finds, best first, as an answer would get them, with findings about the result |
    | `file_chunks(file, knowledge_base, question, data_source)` | How one file was cut into chunks and, with `question=`, where its best passage ranks against the whole knowledge base's |
    | `answer_kb(question, knowledge_base, model, n, data_source, search_type)` | The answer the knowledge base gives once a model has read the passages (Bedrock's `RetrieveAndGenerate`): which parts cite which sources, how much of it a citation backs, its estimated cost, and findings |

### Bedrock knowledge bases { #knowledge-bases }

The agent can tell you whether a knowledge base is up to date and why it doesn't answer something.
{ .gist }

```python
agent = AgentView()
agent.ask("Is support-docs up to date? Some answers still quote the old refund policy.")
```

It starts with `kb_health` (status, last syncs, documents that failed), then `kb_files` for the files that changed in S3 since the last sync or never made it in, and `search_kb` with your question to see the passages an answer would be built from. To see what your users actually get, `answer_kb` asks the knowledge base the way your app does, so the agent can put the answer next to the passages: a good passage the answer doesn't cite points at the model or its prompt, not the search. It reads the knowledge bases in the agent's region; with only one there, you don't have to name it.

!!! tip "Worth knowing"
    Nothing here syncs or changes a knowledge base: when a sync would fix it, the answer gives the `aws bedrock-agent start-ingestion-job` command for you to run. `search_kb`, `file_chunks` and `answer_kb` send the passages and answers they get to the model's provider (cut short, with anything that looks like a secret hidden), so a model on Amazon Bedrock keeps them in AWS. Each `answer_kb` call runs a Bedrock model in your account (Claude Haiku 4.5 unless the agent names another), and its estimated cost is in the answer's AWS cost.


### Reading what's in a file { #read-files }

Turn on `read_files=True` and the agent can open a file to answer questions about its contents.
{ .gist }

```python
agent = AgentView(provider="bedrock", read_files=True)
agent.ask("Which columns does s3://acme-raw/incoming/orders-1.csv have, and are any amounts missing?")
```

It adds `preview_file`, which reads a file the way `S3View().preview()` does: a table's first rows, a JSON file's start, a text file's first lines, a document's text. The agent finds the file with `list_folder` or `find_files` first.

!!! tip "Worth knowing"

    What it reads is sent to the model's provider. With a model on Amazon Bedrock it stays in your AWS account; anywhere else, turn it on only for data your organisation allows there. Values that look like secrets, and whole columns or keys named like one (`password`, `api_key`), are hidden first. Images, audio, video and binary files are never sent, and nor is the file's download link.

??? more "How much it reads"

    It reads up to 2 MB of a text or JSON file (`file_bytes`; a table reads only the rows it shows) and sends the model up to 50,000 characters of it (`file_chars`), 50 rows unless the model asks for more, up to 500. Raise both with the budget: `AgentView(read_files=True, budget={"file_bytes": 10_000_000, "file_chars": 200_000})`. More characters means more tokens, so a bigger bill per question.


### Running Python { #run-python }

Give the agent a Python sandbox and it can write code for what no tool does: read a folder of documents, parse a function's code, group a day of logs, join two tools' data, draw a chart.
{ .gist }

```python
%pip install "aws-analyzer[agent,sandbox]"

agent = AgentView(provider="bedrock", python="local")
agent.ask("What do the contracts in s3://acme-legal/2026/ say about termination notice?")
agent.ask("Which of orders-etl's functions call DynamoDB, and with which tables?")
agent.ask("When did orders-etl's errors start yesterday, hour by hour?")
```

It adds six tools. Three save data into the workspace: `save_s3_files` copies files from S3 under `s3/<bucket>/<key>`, with a PDF's, Word file's or PowerPoint file's text beside it as `<key>.txt`; `save_function_code` saves a function's source files under `lambda/<function>/`; and `save_logs` saves a window of its logs as `lines.jsonl` and `runs.jsonl` (each run's status, run time, memory, cold start and error). `run_python` runs the code the model writes there and returns what it printed, `workspace_files` lists what's saved, and `show_chart` puts a chart the code drew in the answer ([Charts](#charts)). deepagents' file tools work on the same files, so the agent writes a longer script with `write_file`, runs it with `run_python(file="analysis.py")`, fixes a line with `edit_file`, and reads or searches saved files with `read_file` and `grep` without spending a run; what they show has secrets hidden, and each call counts against the budget and shows in the report. The window shows each run's code under its call, and the report folds it under the table of calls.

The code runs in [Pyodide](https://pyodide.org) (Python 3.14 compiled to WebAssembly) on [Deno](https://deno.com), which lets it read Pyodide's own files and nothing else: no network, so not the metadata service that hands out the notebook role's credentials, no environment variables, none of the machine's files and no subprocesses. Data gets in only through the tools, which the read-only guard covers.

!!! tip "Worth knowing"

    The first run downloads Pyodide's files once, about 24 MB with pandas, Altair and Vega-Lite, into `~/.cache/aws-analyzer/pyodide/`, and checks each file's SHA-256. Run `LocalSandbox().prepare()` (from `aws_analyzer.agent`) to do it ahead of time. Without internet access, copy the files into that folder from elsewhere, or pass `index_url=` a mirror. What the code prints goes to the model's provider, with anything that looks like a secret hidden.

??? more "Packages, memory and time"

    The code can import the standard library, pandas (with numpy) and Altair. `LocalSandbox(packages=["pandas", "altair", "scikit-learn"])` offers others from [Pyodide's packages](https://pyodide.org/en/stable/usage/packages-in-pyodide.html), and `memory="4GB"` gives a run more than its default 2 GB. Each run starts a fresh interpreter: files stay between runs, variables don't. Starting Pyodide takes about 2 seconds of a run, loading pandas about 3, and Altair about 2 more.

    A question gets 10 runs of up to 30 seconds each, and the tools save at most 50 MB in all: `budget={"python_runs": 20, "python_seconds": 60, "workspace_bytes": 200_000_000}` raises them.

??? more "Your own sandbox"

    To run the code somewhere else, pass a `Workspace` (from `aws_analyzer.agent`) with five methods: `put(path, data)` writes a file, `read(path)` returns its bytes, `delete(path)` removes it (both raise `FileNotFoundError` for a file that isn't there), `files()` lists them as `WorkspaceFile(path, size)`, and `run(code, timeout=...)` runs the code in a fresh interpreter in the workspace's folder and returns a `PythonRun(ok, stdout, stderr, seconds, timed_out, new_files)`. It must give the code no AWS credentials and no network. A plain subprocess on a SageMaker notebook isn't a sandbox: it can still get the notebook role's credentials from the instance's metadata service.

    ```python
    from aws_analyzer.agent import PythonRun, Workspace, WorkspaceFile

    class MySandbox(Workspace):
        title, place = "my sandbox", "(a container with no network)"
        packages = ("pandas",)

        def put(self, path, data): ...
        def read(self, path): ...
        def delete(self, path): ...
        def files(self): ...
        def run(self, code, *, timeout): ...

    agent = AgentView(provider="bedrock", python=MySandbox())
    ```

### Charts { #charts }

Ask for a chart and the answer has one: the agent reads the files it saved with pandas, makes the chart with Altair, and shows it under the answer's text.
{ .gist }

```python
agent = AgentView(provider="bedrock", python="local")
agent.ask("Chart the orders in s3://acme-raw/sales/*.csv by month and region")
agent.ask("Plot orders-etl's run time per hour yesterday, and mark the slow runs")
agent.save_charts()                       # the last answer's charts as SVG files, in agent-charts/
```

Each chart shows under the answer with its title, in the report, the chat window and `transcript()`. The picture is part of the notebook's output, so it stays when the notebook is saved; `save_charts()` writes the charts to files for a slide or a report (`save_charts("slides", answer=0)` for the first answer's, into `slides/`), and never replaces a file that's there.

The chart is made with [Altair](https://altair-viz.github.io), and the sandbox draws it as a crisp SVG with [Vega-Lite](https://vega.github.io/vega-lite/), in the same Deno that runs the code, so drawing it fetches nothing. Every chart gets the same look: one size, quiet axes and grid, and eight colours that stay apart for colour-blind readers, always in the same order. The agent is told how to make a chart read: one message per chart with a title that says it ("Errors doubled after Tuesday's deploy"), axes labelled with units, a line for change over time and sorted bars to compare, no pies and never a second y-axis. It prints the numbers it plots too, so the answer's text says them.

??? more "Charts as data, and their limits"

    `answer.charts` holds them: each `Chart` has its `.title`, the workspace file it came from (`.path`), the SVG's bytes (`.data`), its `.kind` and `.mime`, and its Vega-Lite spec (`.spec`). With Altair installed in the notebook, `alt.Chart.from_dict(answer.charts[0].spec)` shows a chart again, with tooltips; `save_charts()` writes the spec beside each SVG as `.vl.json`, which the [Vega editor](https://vega.github.io/editor/) opens too. A chart that reads data from a URL is refused: its data must be in the chart, as Altair puts it. `show_chart` also takes PNG, SVG, JPEG, GIF and WebP pictures of up to 3 MB, and shows every chart as a picture, so nothing in an SVG runs. A question gets at most 8 charts: `budget={"charts": 12}` raises it. Showing the same file again replaces the chart with the new one. To draw with matplotlib instead, `LocalSandbox(packages=["pandas", "matplotlib"])`: its charts get the same colours.


## Notes it keeps between conversations { #memory }

With `memory=True`, the agent remembers what it learned about your account, so the next conversation doesn't look it up again.
{ .gist }

```python
agent = AgentView(provider="bedrock", memory=True)
agent.ask("Which Lambda failed last night?")   # it finds orders-etl in eu-west-1, and notes it
agent.memory()                                 # what it remembers, and when it learned each note
agent.forget("orders-etl")                     # clear the notes about it
```

When it finds a lasting fact (which region a function runs in, what a bucket is for, which bucket feeds which function, when a job runs, or something you tell it), it writes a dated line in its notes: `- orders-etl runs in eu-west-1 (learned 2026-10-27)`. The next conversation starts with them in its prompt, as facts it found rather than instructions: it uses a note to know where to look first, and when a tool says otherwise, the tool wins and it fixes the note. It never notes what changes from day to day (error counts, costs, sizes), settings' values or log lines. An answer that added a note says so under it.

In the [chat window](#chat), **🧠 memory** opens the notes under its head, each with when it was learned and a ✕ that forgets it (old ones are marked; **forget all** asks for a second click). `memory()` lists the notes with their age, warns about notes over 90 days old (the account may have changed since), and says how full the memory is. `forget("words")` clears the notes holding those words, `forget_all()` every note about this account.

Your own facts can come from a file too: `AgentView(notes="AGENTS.md")` reads it for each new conversation, so editing it changes what the next one knows. The agent never writes to it.

!!! tip "Worth knowing"

    The notes live in `~/.cache/aws-analyzer/agent/memory.sqlite`. On a SageMaker notebook instance only `~/SageMaker` survives a stop, so keep them there: `AgentView(memory="~/SageMaker/agent-memory.sqlite")`.

??? more "Where the notes are kept, and what keeps them safe"

    The notes are one Markdown file the agent's file tools see at `/memories/AGENTS.md`, stored with deepagents' `StoreBackend` in a LangGraph SQLite store (the `langgraph-checkpoint-sqlite` package, in the `[agent]` extra). Each AWS account gets its own set, found with STS `GetCallerIdentity`, so notes about one account never reach a question about another. Before a note is stored, anything that looks like a key, token or password is hidden, an undated line gets the day's date, and the whole file must stay under 8 KB (`budget={"memory_bytes": 16_000}` raises it), because it's sent with every conversation; past it, the write is refused and the agent keeps the notes that matter most. The agent can only write `/memories/AGENTS.md`, and can't delete it. `agent.core.memories()` returns the notes as data (`MemoryState`, with `Note(text, learned)`), and `parse_notes`, `stamp_notes`, `drop_notes` and `memory_findings` work on any text. Memory is off by default; conversations themselves still last as long as the kernel.

## Safe and bounded { #safety }

It can't change your account, it says what it checked, and each question has a budget.
{ .gist }

- **It only reads.** The tools call the analyzers' read methods. A guard on every AWS client they use refuses any operation that isn't a Get, List, Describe, Head or Filter call (or Bedrock's `Retrieve`, which only searches a knowledge base) before its request is built, so even a mistake in a tool can't change your account. The S3 tools read names, sizes, dates and settings, never what's inside a file. There's no shell: deepagents' `execute` tool and its general-purpose subagent are turned off, and its file tools only keep notes in memory, never on disk (with [`memory=True`](#memory), its notes about the account go in one local SQLite file). Code runs only in the [Python sandbox](#run-python), which has no network and no AWS credentials.
- **It says what it checked.** Every tool result carries its window, its regions and whether it was cut short, and the agent is told to say so in the answer. A permission the role lacks becomes part of the answer (`AccessDeniedException; needs logs:FilterLogEvents`), not an error, and the report lists it.
- **Log lines are data.** What the tools read can contain text that looks like instructions ("ignore your instructions..."). The agent is told never to follow it, and the tools hide anything that looks like a key, token or password before the model sees it.

### The budget { #budget }

Past a limit, the tools stop and the model answers with what it has; the report says what was cut short and how to raise the limit.
{ .gist }

| Limit | Default | Past it |
|---|---|---|
| `tool_calls` | 25 | Tools stop; it answers with what it has |
| `seconds` | 300 | The same |
| `usd` | 1.00 | The same, when `model_prices=` says what its tokens cost |
| `steps` | 200 | The agent stops (LangGraph's recursion limit) |
| `log_days` | 3 | A longer log read [asks you first](#big-reads) (all of it, the last 3 days, or none); CloudWatch's counts still cover the window |
| `s3_keys` | 100,000 | An S3 listing stops there and says so: its counts cover the keys it read |
| `ask_keys` | 20,000 | An S3 listing past it [asks you first](#big-reads): go on (up to `s3_keys`), stop there, or skip it |
| `ask_bytes` | 10 MB | With `python=`: a save into the workspace past it [asks you first](#big-reads): all of it, what fits, or none |
| `file_bytes` | 2 MB | With `read_files=True`: `preview_file` reads a text or JSON file's start only |
| `file_chars` | 50,000 | With `read_files=True`: what `preview_file` sends the model is cut there, and it says so |
| `python_runs` | 10 | With `python=`: `run_python` stops; it answers with what it has |
| `python_seconds` | 30 | With `python=`: the workspace stops a run at this many seconds |
| `workspace_bytes` | 50 MB | With `python=`: a save that would go past it is refused |
| `charts` | 8 | With `python=`: `show_chart` puts no more in the answer |
| `kb_searches` | 10 | `search_kb`, `file_chunks` and `answer_kb` stop running |
| `memory_bytes` | 8 KB | With `memory=True`: a note that would take its notes past it isn't kept |

```python
agent = AgentView(budget={"tool_calls": 50, "log_days": 7})
```

??? more "Why log reads and S3 listings are limited"

    Log reads are limited because searching weeks of a busy function's logs is slow. For long windows the agent uses CloudWatch's counts, which cover months in seconds, and reads logs only for a short window around what it found. S3 listings are limited for the same reason: S3 lists 1,000 keys per request, in key order, so the agent is told to give `find_files` and `folder_summary` the narrowest prefix it can (a day's folder rather than the bucket).

### Big reads ask first { #big-reads }

Before a read that would take long or cost money, the agent stops and asks you: go, smaller, or skip.
{ .gist }

Three kinds of read ask first: an S3 listing that goes past 20,000 keys (`find_files`, `folder_summary`, `save_s3_files`), a log read longer than the 3-day limit, and a save of more than 10 MB into the [Python workspace](#run-python). The agent pauses with a question like *"find_files has listed 20,000 keys under s3://acme-raw/ and there are more; the bucket holds about 41,200 files."* and says what each choice reads:

- **Go** reads all of it: the rest of the listing (up to `s3_keys`, with the requests it takes and what they cost), every day of the logs, every file of the save.
- **Smaller** reads the smaller part it names: the keys listed so far, the last 3 days of logs, the files that fit in 10 MB. The answer says what that left out.
- **Skip** answers without it, and says what it didn't read.

In the [chat window](#chat), the call's line says *✋ waiting for you* and three buttons sit under it; Stop skips the read and has the agent answer. With `ask()`, the report shows the question and the three calls to run: `agent.go()`, `agent.smaller()` or `agent.skip()`. The wait doesn't count against the time budget.

!!! note ""

    This is about time and cost, not safety: the agent only reads, whichever you pick. Without asking (`AgentView(interrupt_on=False)`), a listing stops at `s3_keys` and a long log read is refused, as before.

??? more "Choosing which tools ask, and how big is big"

    `interrupt_on=` picks the tools that ask: `True` (the default) for all of them, a list like `interrupt_on=["find_files", "folder_summary"]` for some, `False` for none. `budget={"ask_keys": 50_000, "ask_bytes": 50_000_000}` moves where a read counts as big. The pause is LangGraph's `interrupt`, kept with the conversation, so the question goes on where it stopped: the tool that asked runs again with your choice, and the calls that finished beside it aren't run again. `agent.core.ask()` returns the answer with `.waiting` set (each call's `.waiting` is the `BigRead`: `.what`, `.go`, `.smaller`, `.cost`), and `agent.core.resume("go")` goes on. When the model asked for two big reads side by side, each `resume()` answers the next.

<p class="part"><strong>That's the guide.</strong> Everything below is reference for when you need it: the data in Python and your own tools, cost, permissions, troubleshooting and every command.</p>

## Use the data in Python { #python }

`agent.core` is the `AgentAnalyzer`. Its `ask()` returns an `AgentAnswer` instead of a report:

```python
answer = agent.core.ask("Which Lambda failed last night?")
answer.text                    # the answer, in markdown
answer.charts                  # Chart: title, path, data (the SVG's bytes), kind, mime, spec (its Vega-Lite)
answer.calls                   # ToolCall: name, args, coverage, summary, error, refused, seconds
answer.calls[0].services       # ('lambda', 'logs', 'cloudwatch'): the AWS services it reads (.reads: its operations)
answer.calls[0].result         # the data the tool handed the model
answer.plan                    # PlanStep: text, status ('pending', 'in_progress', 'completed'): the plan it wrote
answer.aws_calls               # {'logs:FilterLogEvents': 12, ...}: every AWS call it made, all reads
answer.provider, answer.model # who served it, and the model or profile that answered
answer.input_tokens, answer.output_tokens, answer.cost   # answer.rates: whose prices the cost is at
answer.stopped                 # None, or why it stopped early: 'tool_calls', 'time', 'cost', 'steps', 'stopped', 'error'
answer.error                   # what went wrong with the model, and what to do
answer.waiting                 # calls paused before a big read: .waiting.what / .go / .smaller say what it asks
agent.core.resume("smaller")   # goes on with your choice: 'go', 'smaller' or 'skip'
agent.core.history             # every AgentAnswer so far
```

To show the agent's work as it happens in a UI of your own, as the chat window does, pass `on_step`. It's told each `AgentStep`: the model's words before it acts (`kind='thinking'`, `.text`), its plan each time it writes or updates one (`'plan'`, `.plan`: the whole list), and each tool call as it starts (`'tool'`), pauses to ask you before a big read (`'asking'`, with `.call.waiting`), and ends (`'tool_done'`, with `.call.summary`, `.call.error` or `.call.refused` filled in). Tools run side by side, so it's called from their threads:

```python
def show(step):
    if step.kind == "plan":
        print("plan:", " → ".join(f"{s.text} ({s.status})" for s in step.plan))
    else:
        print(step.kind, step.text or step.call.call)

agent.core.ask("Which Lambda failed last night?", on_step=show)
```

Settings, all optional:

```python
from aws_analyzer import AgentAnalyzer, AgentView

core = AgentAnalyzer(
    provider="bedrock", model="sonnet",              # or model="openai:gpt-4.1", deployment=..., ...
    model_prices={"claude-sonnet-5": (2.00, 10.00)}, # USD per 1M input and output tokens, over the list prices
    timezone="Asia/Kolkata",                         # for the agent alone; set_timezone() sets it everywhere
    regions=["us-east-1", "eu-west-1"],              # where it looks unless asked otherwise; "all" for every region
    notes="orders-etl runs at 02:00 IST and loads the day's orders",   # facts it's told about your account
    budget={"tool_calls": 25, "log_days": 3, "s3_keys": 100_000},
    toolkits=["lambda", "s3"],                       # the default adds "bedrock_kb"; ["s3"] for S3 alone
    read_files=True,                                 # adds preview_file; off by default
    plan=False,                                      # no plan for questions of several steps; on by default
    interrupt_on=["find_files", "function_logs"],    # only these ask before a big read; True (all) by default
)
agent = AgentView(core)
```

`chat_model=` takes any LangChain chat model instead, made with your own settings. `lambda_core=` and `s3_core=` take a `LambdaAnalyzer` and an `S3Analyzer` of your own (with their own `prices=`). The pure functions work without AWS or a model: `hide_secrets`, `asked_at`, `system_prompt`, `summarize_health`, `summarize_errors`, `summarize_runs`, `summarize_performance`, `summarize_detail`, `summarize_code`, the S3 toolkit's `summarize_bucket`, `summarize_found`, `summarize_prefix`, `summarize_file`, `summarize_preview`, `count_arrivals` (from a listing: which files a notification's filter and event types covered or missed) and `trigger_findings`, `answer_findings`, `pick_provider`, `model_rates` and `explain_model_error`.

### Your own tools { #toolkits }

The agent's tools come in toolkits, one per AWS service: `toolkits=["lambda", "s3", "bedrock_kb"]` is the default. A toolkit of your own adds tools beside them. Give it a name, a sentence for the model on what it covers, and `tools()`: plain functions whose type hints and docstrings tell the model how to call them, each returning a dict.

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

agent = AgentView(toolkits=["lambda", "s3", "bedrock_kb", Runbooks()])
agent.ask("orders-etl failed last night: what should I do?")
```

Its tools get the same budget, error handling and report as the built-in ones, and `tools()` lists them. When it reads AWS, make its clients in the analyzer `make_analyzer()` returns, from the session it's given: the read-only guard covers every client that analyzer makes from its session, and the clients it already holds. A tool that joins your service to another reads that one through the agent's own toolkit for it, `self.peer("lambda")`, which is `None` when the agent hasn't got it.

## Cost { #cost }

Each answer counts the model's input and output tokens. A model on Amazon Bedrock is priced at Bedrock's list prices, so each answer shows its estimated cost and the budget's `usd` limit applies. OpenAI's and Azure OpenAI's prices depend on the plan, the region and the deployment type, so there are none built in: pass `model_prices=` for them (or to override Bedrock's). Without a price the cost shows as unknown, never a guess.

On the AWS side, the CloudWatch metrics it reads cost $0.01 per 1,000, and S3's requests $0.005 per 1,000 listing pages (each lists up to 1,000 keys) and $0.0004 per 1,000 others; the report says how many of each it made and what they cost. Reading logs, Lambda's settings and code costs nothing. `function_health` reads 7 metrics per function, so a question about 100 functions reads about 700: under a cent. A `find_files` over 100,000 keys makes 100 listing requests: $0.0005. A knowledge base search embeds its question, a fraction of a cent; the vector store bills by its own terms (OpenSearch Serverless by the hour, searched or not).

## Permissions { #permissions }

The Lambda tools read through `LambdaAnalyzer`, so the role needs the [Lambda guide's read-only policy](lambda_functions.md#permissions). The S3 tools read through `S3Analyzer`, so they need the [S3 guide's read-only policy](s3.md#permissions) (with `s3:GetBucketNotification`, which `bucket_details` and `s3_triggers` read the bucket's event notifications with). `s3_triggers` needs both. The Bedrock knowledge base tools read through `BedrockKBAnalyzer`, so they need the [knowledge base guide's read-only policy](bedrock_kb.md#permissions): `bedrock:ListKnowledgeBases`, `bedrock:GetKnowledgeBase`, `bedrock:ListDataSources`, `bedrock:GetDataSource`, `bedrock:ListIngestionJobs`, `bedrock:GetIngestionJob`, `bedrock:ListKnowledgeBaseDocuments`, `bedrock:ListTagsForResource`, `bedrock:Retrieve`, and for `answer_kb` `bedrock:RetrieveAndGenerate` with `bedrock:InvokeModel` on the model or inference profile that answers (plus `bedrock:ListFoundationModels` and `bedrock:ListInferenceProfiles` to find it by a short name), and `s3:ListBucket` on each data source's bucket. Nothing more, and no permission to change anything. With a model on Amazon Bedrock, add `bedrock:InvokeModel` on the model and its inference profile (the Converse API, which only generates text), and `bedrock:ListFoundationModels` and `bedrock:ListInferenceProfiles` to find a model by a short name like `"sonnet"`. A permission that's missing shows up in the answer as what couldn't be read, and the rest of the answer still stands. With `memory=True`, the agent reads its account ID with `sts:GetCallerIdentity`, which needs no permission.

On the Azure side, an API key of the Azure OpenAI resource, or a Microsoft Entra ID identity with the **Cognitive Services OpenAI User** role on it.

## Troubleshooting { #troubleshooting }

??? question "“The agent needs Python 3.11 or newer”"

    deepagents needs Python 3.11. On SageMaker, pick a kernel with Python 3.11 or newer, or make one with the SageMaker guide's [`new_env("3.11")`](sagemaker_env.md#envs), then `%pip install "aws-analyzer[agent]"` in it.

??? question "“The agent needs a model to answer with, and none is set up”"

    Nothing says which model to use. Pick one: `AgentView(provider="bedrock")`, `AgentView(model="openai:gpt-4.1")` with `OPENAI_API_KEY` set, or the Azure settings below. `AWS_ANALYZER_AGENT_MODEL` sets one for every notebook.

??? question "“… AZURE_OPENAI_ENDPOINT … are not set”"

    The agent doesn't know which deployment to ask. Set `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY` and `AZURE_OPENAI_DEPLOYMENT` in the notebook's environment, or pass them to `AgentView`.

??? question "“ValidationException … on-demand throughput isn't supported”"

    That Bedrock model is served only through an inference profile. Put `us.` (or `eu.`, `apac.`, `global.`) before its ID, or pass its short name, `AgentView(provider="bedrock", model="haiku")`, and the agent finds the profile.

??? question "“AccessDeniedException” from Bedrock"

    The notebook's role needs `bedrock:InvokeModel` on the model and on its inference profile, and the account needs access to the model: the Bedrock console's Model catalog shows it.

??? question "“NotFoundError … No such deployment on this endpoint”"

    `AZURE_OPENAI_DEPLOYMENT` is the name you gave the deployment in Azure AI Foundry, which can differ from the model's name. An old `api_version` can also give a 404.

??? question "“Azure's content filter blocked the prompt or the answer”"

    Log lines can set off Azure's content filter. Ask about a narrower window, or adjust the deployment's content filter.

??? question "`s3_triggers` says the function doesn't let the bucket invoke it"

    S3 can only invoke a function whose resource-based policy allows `s3.amazonaws.com` for that bucket. The finding gives the `aws lambda add-permission ...` command to run; the agent never runs it. If the permission is there and the finding still says so, the role may lack `lambda:GetPolicy`: the answer then lists it under what couldn't be read.

??? question "It answered, but says it was stopped at the budget"

    The question needed more tool calls, time or log days than the budget allows. Ask a narrower question (one function, a shorter window), or raise the limit it names: `AgentView(budget={"tool_calls": 50})`.

??? question "`ask()` says the agent is waiting for my choice"

    The last question stopped to ask before a [big read](#big-reads). Run `agent.go()`, `agent.smaller()` or `agent.skip()` to let it finish, or `agent.ask("...", new=True)` to drop it and start a new conversation.

## Command reference { #reference }

<div class="ref" markdown>

| Command | What it shows |
|---|---|
| `chat(height=None)` | A chat window: ask, and watch each step as it happens (what the model says, each tool call running and then what it found), then the answer with Details (each tool call, the AWS reads, the cost) folded under it. Stop ends a question with what it found; a question typed while it works is queued and asked next; New chat starts over; 🧠 memory shows the agent's notes, each with a ✕ to forget it; ⚙ model picks the model |
| `ask(question, new=False)` | The answer first, then what it checked: each tool call with its window, regions and what it found, what it couldn't read or was refused, the AWS reads, tokens and cost, and the `LambdaView` and `S3View` commands to look closer |
| `new_conversation()` | Starts a new conversation: the next question won't see the earlier ones |
| `go()` | The agent stopped to ask before a [big read](#big-reads): read all of it, then show the answer |
| `smaller()` | The same, reading only the smaller part it named (the keys listed so far, the last days of logs, the files that fit) |
| `skip()` | The same, answering without it; the answer says what it didn't read |
| `memory()` | With `memory=True`: what the agent remembers about the account, each note and when it learned it, and the notes old enough to check |
| `forget(match)` | Clears the notes holding these words; `forget_all()` clears every note about this account |
| `transcript()` | Every question asked so far and its answer, with the tool calls, tokens and cost of each, and the total |
| `save_charts(folder="agent-charts", answer=-1)` | With `python=`: the last answer's charts (or `answer=` another's) as SVG files, each named after its title, with its Vega-Lite spec beside it; it never replaces a file |
| `tools()` | Each tool and the AWS reads it makes; the model, regions, time zone and budget; and what's sent to the model |
| `use_model(provider=None, model=None, ...)` | Switches the model the agent answers with, and says where what it sends goes; the window's ⚙ model does it by clicking |
| `help(command=None)` | This list, grouped by task; `help("name")` shows one command in full |

</div>
