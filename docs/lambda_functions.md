---
title: Lambda Functions Guide
description: "How to see every AWS Lambda function, in one region or all of them, from a SageMaker notebook with aws-analyzer's lambda_functions.py: runtimes and their end of support, triggers and public access, calls, errors and cost, errors grouped by cause, logs run by run, memory and cold starts, and the code, in reports or in an explorer window you click through, with examples."
---

<p class="eyebrow"><img class="aws-icon" src="images/aws/lambda.svg" alt="" width="32" height="32"> AWS Lambda <code>lambda_functions.py</code></p>

# Find out which Lambda failed, and why, without the console

Every function, in one region or all of them: what's failing and why, read from its own logs, what it costs, which runtimes lose support and who else can call it. Or click through it all in the explorer window.
{ .lede }

<ul class="pills">
  <li>One install, nothing to set up</li>
  <li>Read-only: never invokes or changes a function</li>
  <li>Every region at once</li>
  <li>An explorer window: click, don't type</li>
  <li>Plain text outside Jupyter</li>
</ul>

The examples use acme's functions: `orders-etl` loads each day's orders from S3 into the warehouse, `churn-scoring` scores customers with a SageMaker model, and `report-api` serves a sales report over a function URL. The screenshots are the tool's real output on [moto](https://github.com/getmoto/moto).
{ .muted }

## Watch it run { #video }

Seven calls in a notebook, from every function to the code it runs: each call typed and run, and the report it draws, with what to notice. It plays as you scroll to it; click it to pause, or pick a chapter to jump to a call.
{ .gist }

<div class="aa-tour" data-tour="lambda_functions">
  <div class="tour-screen"></div>
  <div class="tour-controls"></div>
  <div class="tour-chapters"></div>
  <p class="tour-links"><a href="tour.html?service=lambda_functions">Full screen</a> · <a href="videos/lambda_functions-tour-light.mp4" data-tour-mp4 download>Download as MP4</a>, for Slack or a slide (light or dark, as this page is)</p>
</div>

## Set up in SageMaker { #setup }

<div class="steps" markdown>

1. **Install it** in a notebook cell:

    ```bash
    %pip install aws-analyzer
    ```

2. **Make the view.** It uses the notebook's IAM role and region, so there's nothing to configure.

    ```python
    from aws_analyzer import LambdaView

    ui = LambdaView()
    ui.help()           # every command, grouped by task; ui.help("errors") shows one in full
    ```

</div>

??? more "No internet in the notebook (VPC-only mode)?"

    On a computer that has internet, download the package and copy it to a bucket the notebook can read (`pip download aws-analyzer --no-deps -d wheels`, then `aws s3 cp --recursive wheels/ s3://acme-ml-data/tools/wheels/`), and install it from there:

    ```bash
    !aws s3 cp --recursive s3://acme-ml-data/tools/wheels/ wheels/
    %pip install --no-index --find-links wheels aws-analyzer
    ```

??? more "Another region or profile, plain text"

    ```python
    from aws_analyzer import LambdaAnalyzer, LambdaView

    ui = LambdaView(LambdaAnalyzer(region="eu-west-1", profile="dev"))
    ui = LambdaView(mode="text")    # plain text, e.g. in a terminal or a script
    ```

    Only boto3 is required. The module is `lambda_functions`, not `lambda`, because `lambda` is a Python keyword.

## Start here { #tour }

Eight questions people bring to Lambda, and the one line that answers each. Nothing is invoked, changed or deleted.
{ .gist }

<div class="quick" markdown>

What's wrong
{ .qgroup }

-   [What do I have, and what's failing or costing money?](#functions) `ui.functions(regions="all")`{ .long }
-   [Why did this function fail?](#errors) `ui.errors("orders-etl")`
-   [Which runs took 15 seconds or more today?](#runs) `ui.runs("orders-etl", since="today", where="duration > 15s")`{ .long }
-   [What did it log?](#logs) `ui.logs("orders-etl", search="KeyError")`{ .long }

Cost and code
{ .qgroup }

-   [Did it get slower, or is it paying for memory it never uses?](#performance) `ui.performance("orders-etl")`{ .long }
-   [What does its code actually do?](#how-it-works) `ui.how_it_works("orders-etl")`{ .long }
-   [It worked last week. What changed?](#diff) `ui.diff("orders-etl")`

Try it
{ .qgroup }

-   [Run it now and see what it logged.](#invoke) `ui.invoke("orders-etl", {"order_id": 42})`{ .long }

Rather click?
{ .qgroup }

-   [I'd rather click through it all.](#explorer) `ui.explore()`

</div>

Name a function by its name, `"name:alias"`, its ARN or a console link: paste what you have. Environment variable values never appear in a report, only their names.

??? more "How to read a report, and functions in other regions"

    Every report puts the answer first: cards with the numbers that matter (a card turns amber or red when a finding is about it), then the findings, warnings first, each a bold headline with why it matters and what to do underneath. The tables of detail come after.

    - **Sort** by clicking a column's header (again for the other way, a third time for the original order).
    - **Filter** a column with a few repeated values (a runtime, a status) to see only those rows.
    - **Columns** lets you untick the columns you don't need.
    - **Next** at the bottom lists two or three commands with the arguments filled in, such as `errors('orders-etl')`.

    One click on a command anywhere in a report, or on a code block, selects all of it, ready to copy. Where a change would help, a report shows the AWS CLI command instead of running it.

    Functions are regional: pass `region="eu-west-1"` for one outside the view's region (an ARN or a link already says where it is). After `functions(regions="all")`, a name found in only one other region is looked for there.

## The explorer window { #explorer }

Rather click than type? `explore()` opens a window on your functions: click one to see what's wrong with it, and its logs run by run.
{ .gist }

```python
ui.explore()                        # every function in the notebook's region
ui.explore("orders-etl", tab="logs")   # straight to one function's logs
```

Every function is listed with how it's doing, the ones that need attention first. A click away are its logs run by run, its errors grouped by cause, its run times, the code in its package and every setting, and the Live tab shows what's running right now. The [Lambda explorer guide](lambda_explorer.md) covers every tab, with its own video tour.

## Every function { #functions }

Start with `functions()`: it says which functions need you and why, what to fix first, and where the bill goes.
{ .gist }

```python
ui.functions(regions="all")
```

![functions(regions="all"): "4 of 6 functions need you", the $93.93 monthly bill as one bar (three-quarters of it provisioned concurrency), the three problems to fix first, each with its command, then the functions under Failing, Paying for nothing and Needs a fix, each with its calls a day, failed share, longest run against its timeout and cost, the healthy ones folded, and a table by region](images/lambda-functions-light.webp#only-light){ width="984" height="1976" loading=lazy }
![functions(regions="all"): "4 of 6 functions need you", the $93.93 monthly bill as one bar (three-quarters of it provisioned concurrency), the three problems to fix first, each with its command, then the functions under Failing, Paying for nothing and Needs a fix, each with its calls a day, failed share, longest run against its timeout and cost, the healthy ones folded, and a table by region](images/lambda-functions-dark.webp#only-dark){ width="984" height="1976" loading=lazy }
/// caption
`ui.functions(regions="all")`: orders-etl started failing after a change three days ago, report-api is open to anyone, and $70 of the $94 a month is provisioned concurrency churn-scoring never uses.
///

It reads from the top down, so you can stop as soon as you have your answer:

- **The verdict**: how many functions need you, and the first few problems in a sentence.
- **Where the money goes**: the estimated monthly bill as one bar of its parts (provisioned concurrency, logs written and stored, run time, requests), with who pays the largest. [Cost](#cost) has the details.
- **Do this first**: the three problems to fix first, each kind once with every function that has it and the command for each. One problem shared by many functions, such as three runtimes past their end of support, is one job. The rest are folded under it.
- **The functions**, under **Failing**, **Paying for nothing**, **Needs a fix**, **Not called** and **Healthy** (folded). Each line shows its runtime (marked when AWS has ended or is ending its support; [Runtime support](#runtimes)), memory and triggers, its problems as small labels, its **calls a day** over the window with failed calls in red below, its **failed share** with an ↑ when the last 3 days are well above it, its **longest run against its timeout**, and its **estimated cost**. Click a line for its findings and the calls to run next.

**URL (public)** or **Anyone** in a function's triggers means anyone on the internet can run it. `function_info()` shows every finding for one function.

`match="etl-*"` keeps the functions whose names match. `metrics=False` skips CloudWatch, and `details=False` skips the reads made for each function (its resource policy and provisioned concurrency), which makes accounts with hundreds of functions quicker.

### Across regions { #regions }

`functions(regions="all")` reads every region your account has turned on, with a **By region** table of each region's functions, cost and limits.
{ .gist }

??? more "Which regions it reads, and what the By region table shows"

    It reads every region your account has turned on (from EC2's `DescribeRegions`; without that permission, every region Lambda is offered in, skipping the ones that answer that they aren't turned on). `regions=["us-east-1", "eu-west-1"]` or `regions="us-east-1, eu-west-1"` reads just those. The table gains a Region column, and a **By region** table shows each region's functions, cost, code storage against its limit, and the most runs that happened at once against the region's concurrency limit.

## One function { #function-info }

`function_info()` shows how one function is behaving and how it's wired, then explains its setup in plain English; every finding ends in the command that fixes it.
{ .gist }

Under the findings come the pictures: calls a day with the failed part in red, failed calls a day on their own scale (so a jump after a change shows even when failures are a small share), run time a day against the timeout, each with a dashed line on the day it was last changed; a diagram of what calls it and where its results, failed events and logs go; and the monthly cost as one bar of its parts. Then the settings, triggers, versions and the numbers day by day.

The `update-function-configuration` call that moves it to a supported runtime or raises its timeout, the `put-retention-policy` call that stops its logs being kept forever, the `put-function-event-invoke-config` call that catches failed events. `days=7` reads a shorter window.

```python
ui.function_info("orders-etl")
```

![function_info("orders-etl"): cards for its runtime, memory, timeout, architecture, calls, error rate, average and longest run, throttles, estimated cost and triggers; findings about its runtime, rising errors, calls that began to fail after its last change, a run close to the timeout, logs costing more than its compute, failed S3 events being dropped, a log group kept forever and a password in an environment variable; charts of its calls a day, its failed calls a day jumping after the change, and its run time a day against the timeout; a diagram of what calls it and where its failed events and logs go; and a bar of where its monthly cost goes](images/lambda-function-info-light.webp#only-light){ width="984" height="2150" loading=lazy }
![function_info("orders-etl"): cards for its runtime, memory, timeout, architecture, calls, error rate, average and longest run, throttles, estimated cost and triggers; findings about its runtime, rising errors, calls that began to fail after its last change, a run close to the timeout, logs costing more than its compute, failed S3 events being dropped, a log group kept forever and a password in an environment variable; charts of its calls a day, its failed calls a day jumping after the change, and its run time a day against the timeout; a diagram of what calls it and where its failed events and logs go; and a bar of where its monthly cost goes](images/lambda-function-info-dark.webp#only-dark){ width="984" height="2150" loading=lazy }
/// caption
`ui.function_info("orders-etl")`: its logs cost six times its compute, and failed S3 events are dropped because nothing catches them.
///

??? more "What the report's tables hold"

    - **What it runs**: the runtime and its support dates, the handler (which file and function Lambda calls), the package's size and layers, memory and the CPU it buys (a full vCPU at 1,769 MB), timeout, `/tmp` storage, architecture, reserved concurrency, tracing, where its logs go and how long they're kept, and whether Lambda patches its runtime itself.
    - **What triggers it**: each event source mapping with its batch size, state and last result, each service or account its resource policy allows, and its function URL with its auth type.
    - **When an asynchronous call fails**: for functions that S3, SNS or EventBridge call asynchronously, how many times Lambda retries a failed event, how old an event may get, and where it goes when it fails for good. With no on-failure destination or dead-letter queue, it's dropped.
    - **What it can reach**: its execution role (what its code may do in AWS), its network (a VPC reaches the internet only through a NAT gateway or VPC endpoints), and the names of its environment variables.
    - **Versions, aliases and provisioned concurrency**, with what each provisioned copy costs.
    - **The last 30 days**, one row per day: calls, errors, throttles, average and longest run.
    - **The estimated monthly cost**, as one bar of its parts (logs sent, logs kept, compute, requests...), and the configuration as Lambda returns it, folded away, with environment values hidden: JSON in colour, where a click on an object or a list folds it, as in S3's preview of a `.json` file.


### Runtime support { #runtimes }

A runtime that's **ended** gets no more security patches. The report warns 90 days before, and names the runtime to move to.
{ .gist }

AWS supports each managed runtime (`python3.12`, `nodejs22.x`, `java21`...) for a while after the language version is released, then:

| Date | What happens |
|---|---|
| End of support | AWS stops applying security patches. Functions keep running |
| Block function create | No new functions on that runtime |
| Block function update | Existing functions on it can't be updated |

The report marks a runtime **ended** after its end of support, and warns 90 days before it. It names the newest runtime for the same language, and the `update-function-configuration` command that moves the function to it: test the code on the new runtime first. A container image has no managed runtime: rebuild it on a current base image now and then.

### Who can call it { #triggers }

**Anyone** or **URL (public)** in the triggers means anyone on the internet can run the function, and you pay for every call.
{ .gist }

??? more "The three ways a function gets called, and what the report can't see"

    Lambda functions get called three ways, and the report reads all three:

    | How | Where it's set | Examples |
    |---|---|---|
    | Lambda reads a queue or stream for it | Event source mappings | SQS, Kinesis, DynamoDB streams, Kafka |
    | A service or account calls it | The function's resource policy | S3, EventBridge, API Gateway, SNS, a Bedrock agent, another account |
    | Anyone calls its HTTPS address | Its function URL | With auth type `NONE`, anyone who has the URL |

    **Anyone** in the triggers means its resource policy lets any AWS account invoke it, and **URL (public)** means its function URL needs no sign-in: both are warnings, since anyone can run it and you pay for every call. A service allowed without a source (an S3 permission with no `SourceArn`) is a note: that service could call it for other accounts' resources too. Functions called directly (an SDK, Step Functions, a service using its own role) leave no trace here, since their permission is on the caller's role.

## When something goes wrong { #errors }

### Errors, grouped by cause

`errors()` groups a function's errors by cause, says what to do about each, and points at the run that shows it.
{ .gist }

It reads the function's own logs, next to CloudWatch's count of failed and throttled calls.

```python
ui.errors("orders-etl")
ui.errors("orders-etl", since="7d")            # further back
```

![errors("orders-etl"): the verdict that 8 events were lost, 2 causes are still failing and 1 stopped; cards for 61 failed calls of 2,067, 8 events lost and 3 causes; a chart of calls and failed calls per hour over the 24 hours with a row of dots per cause under it; cards for what happened to the failed events, 8 lost and 2 kept; and a card per cause, KeyError, the timeout and AccessDenied (stopped), each saying what it means, where it fails and what to do, with the command or call to copy](images/lambda-errors-light.webp#only-light){ width="984" height="2238" loading=lazy }
![errors("orders-etl"): the verdict that 8 events were lost, 2 causes are still failing and 1 stopped; cards for 61 failed calls of 2,067, 8 events lost and 3 causes; a chart of calls and failed calls per hour over the 24 hours with a row of dots per cause under it; cards for what happened to the failed events, 8 lost and 2 kept; and a card per cause, KeyError, the timeout and AccessDenied (stopped), each saying what it means, where it fails and what to do, with the command or call to copy](images/lambda-errors-dark.webp#only-dark){ width="984" height="2238" loading=lazy }
/// caption
`ui.errors("orders-etl")`: the answer first, when it failed hour by hour, where the failed events went, and a card per cause with what to do about it.
///

It reads in four parts:

- **The verdict**: one sentence that says what matters, such as "8 events were lost, 2 causes are still failing and 1 stopped", with the numbers behind it.
- **When it failed**: calls and failed calls per hour (per day for a longer window), with a row of dots per cause under them on the same columns, and a sentence that reads it: one spike, rising, falling or spread out.
- **What happened to the failed events**: [below](#failed-events).
- **A card per cause**, still failing ones first. Its tag says whether it's new, still failing or stopped (stopped means no line for a while; the card says how many calls ran since). It says **what it means** in plain words, **where it fails** (the line of the function's own code its traceback names), and **what to do**, with the command or call to copy.

What each kind of cause gets:

- **Timeouts**: the command that raises the timeout, or `performance()` to find what's slow. At 900 seconds there's no more room: split the work, or run it somewhere without the limit.
- **Out of memory**: the command that gives it more memory.
- **Code that can't load** (`Runtime.ImportModuleError`): a package missing from the deployment package or a layer, or a handler setting that doesn't match a file. `code()` shows what's in the package.
- **Access denied**: the role, the action and the resource, and the `aws iam put-role-policy` command that allows it.
- **Throttled or unreachable services**, and the common Python errors (`KeyError`, `TypeError`, `ValueError`, ...) in plain words.

Each card ends with the `logs("orders-etl", request_id="...")` call for its newest run, start to end. The raw error lines are folded at the bottom.

### Did we lose data? { #failed-events }

The report also says what happened to the failed events: lost, kept in a queue, or still waiting to be tried again.
{ .gist }

A failed call isn't lost data yet. Where its event goes depends on what called the function:

- **S3, SNS, EventBridge** call it asynchronously. Lambda tries an event 3 times (1 + its retries), then gives up and sends it to the function's on-failure destination or dead-letter queue, or drops it when there's neither. The report counts the events dropped with CloudWatch's `AsyncEventsDropped`, Lambda's own number. For S3, the file is still in the bucket: it was never processed.
- **An SQS queue** gets a failed message back. After `maxReceiveCount` reads the queue moves it to its dead-letter queue; without one, the message is deleted when the queue's retention runs out. The report reads how many messages wait in each queue and how old the oldest is, and never receives one: that would hide it from the function.
- **A function URL or API Gateway** waits for the answer, so the caller got the error and Lambda keeps nothing. Whether it tried again is up to the caller.

Each number says where it came from ("Lambda's count", "the queue, now"), and a dead-letter queue's count is labelled as from any cause, since several queues can share one. The findings show the command to keep the next dropped events (`put-function-event-invoke-config`), to give a queue a dead-letter queue, or to send kept messages back once it's fixed (`start-message-move-task`).

### Logs { #logs }

`logs()` shows the newest runs a function logged, each a line that opens to its log lines, and `request_id=` one whole run.
{ .gist }

The runs that hold the newest 50 lines of the last hour, newest first, like a log viewer: each run's status, start, run time against the timeout, memory used, cold start and what failed. Click one for its lines, oldest first, with their time, level and text (a JSON line's other fields a click away, a traceback laid out). Chips keep the failed runs, the ones that logged an error or the cold starts, and switches keep only errors and warnings, only the lines a search found, or open every run. In Jupyter with ipywidgets, a search box sits over the report: typing keeps the runs already read that hold the text (no AWS call, any case, `KeyError OR Timeout` works too), and Enter searches every line of the time range in CloudWatch and draws the report again. `search=` does the same from code. The browser's Find (Ctrl+F) also opens the run it finds text in.

```python
ui.logs("orders-etl")
ui.logs("orders-etl", search="KeyError")                  # the runs with that text (case-sensitive), whole
ui.logs("orders-etl", search="KeyError OR Timeout")       # words joined with AND, OR and NOT, line by line
ui.logs("orders-etl", search='?ERROR ?WARN')              # or any CloudWatch Logs filter pattern
ui.logs("orders-etl", request_id="c0ffee00-1d2e-4f3a-9b8c-7d6e5f4a3b2c")   # one run, start to end
ui.logs("orders-etl", since="24h", n=200)
```

![logs("orders-etl", since="24h"): the newest 50 lines of the last 24 hours grouped into 17 runs, 4 of them failed; cards for runs, failed runs, lines shown, error lines and the newest line; a note that reading stopped at 300 lines; then the runs, newest first, each with its status, start, run time against the 60 s timeout, memory used, cold start and first line; a failed run's KeyError: 'customer_id' with its traceback, and a run that timed out after 60.0 s](images/lambda-logs-light.webp#only-light){ width="984" height="1316" loading=lazy }
![logs("orders-etl", since="24h"): the newest 50 lines of the last 24 hours grouped into 17 runs, 4 of them failed; cards for runs, failed runs, lines shown, error lines and the newest line; a note that reading stopped at 300 lines; then the runs, newest first, each with its status, start, run time against the 60 s timeout, memory used, cold start and first line; a failed run's KeyError: 'customer_id' with its traceback, and a run that timed out after 60.0 s](images/lambda-logs-dark.webp#only-dark){ width="984" height="1316" loading=lazy }
/// caption
`ui.logs("orders-etl", since="24h")`: a day of runs, newest first, the failed one and the timeout among them.
///

`request_id=` shows every line of one run, including what the code printed without the ID: everything its log stream recorded between the run's first and last line. When the window has no lines, the report says when the newest one was, and the call that reaches back that far.

### Every run, as rows to ask about { #runs }

`runs()` lists a function's runs one row each, and keeps the ones you ask for: `where="duration > 15s"`.
{ .gist }

Each run has fields: what Lambda says about it (status, start, run time, memory, cold start, the error it hit), every field its JSON log lines carry, and the event, when the code logs it. Ask about any of them, sort by them, pick them as columns, or sum the runs up by one.

```python
ui.runs("orders-etl", since="today", where="duration > 15s")       # today's runs that took 15 s or more
ui.runs("orders-etl", where="duration > 15s and cold", sort="-duration")
ui.runs("orders-etl", where="status in (failed, timeout)", group_by="hour")   # when do they fail?
ui.runs("orders-etl", where="size > 30MB", columns=["started", "duration", "event.Records.s3.object.key"])
ui.runs("orders-etl", fields={"written": r"wrote (\d+) orders"}, where="written > 40000")  # from text lines
```

![runs("orders-etl", since="24h", where="status in (failed, timeout)"): 13 of 70 runs match, all failed, from 3 causes; cards for the runs that match, failed runs, median and longest run time, memory and cold starts; a finding that 3 runs reached the 60 s timeout; a dot per run by when it started and how long it took, the timeouts in a lane above; a card per cause, KeyError: 'customer_id' and the timeout still failing, a denied DynamoDB PutItem stopped; then the runs as a table, and the fields to query](images/lambda-runs-light.webp#only-light){ width="984" height="2714" loading=lazy }
![runs("orders-etl", since="24h", where="status in (failed, timeout)"): 13 of 70 runs match, all failed, from 3 causes; cards for the runs that match, failed runs, median and longest run time, memory and cold starts; a finding that 3 runs reached the 60 s timeout; a dot per run by when it started and how long it took, the timeouts in a lane above; a card per cause, KeyError: 'customer_id' and the timeout still failing, a denied DynamoDB PutItem stopped; then the runs as a table, and the fields to query](images/lambda-runs-dark.webp#only-dark){ width="984" height="2714" loading=lazy }
/// caption
`ui.runs("orders-etl", since="24h", where="status in (failed, timeout)")`: 13 of the day's 70 runs failed, from three causes, and two of them are still failing.
///

The report answers first: how many runs failed and from how many causes, which one is still failing, and how long the runs that worked took. Then a dot per run by when it started and how long it took, drawn as `performance()` draws them (failed ones red, the runs that timed out in a lane above so the rest stay to scale), and a card per cause of the failed runs: still failing or stopped, what it means, where its code fails, what to do and the call that reads its newest run. Findings say when a run reached the timeout or used 90% of the memory (with the command to fix it), and when a field you named isn't one any run has, with the one you likely meant. The runs follow as a table, and the fields there are to ask about are listed, folded, at the end.

??? more "What a query can say"

    - **Compare**: `>`, `<`, `>=`, `<=`, `=`, `!=`, `~` (contains, any case), `!~`, and `in (a, b)`. Join with `and`, `or`, `not`, and brackets.
    - **Units**: run times as `15s`, `500ms`, `2m` (a bare number is in ms, as REPORT lines say), memory as `512MB` or `1GB`, shares as `80%`, sizes in a field your code logs as `30MB` (in bytes). Times as `14:00` (today), `2h` (2 hours ago), `today` or `2026-10-31T09:00`.
    - **Lambda's fields**: `status` (ok, failed, timeout, logged, running), `failed` and `cold` (yes or no, so `cold` alone works), `started`, `day`, `hour`, `duration`, `billed`, `init`, `memory`, `memory_size`, `memory_share`, `timeout_share`, `version`, `error_type`, `error`, `summary`, `text`, `lines`, `request_id`, `stream`.
    - **Your fields**: a JSON log line's fields by their path (`customer.id`), the event's as `event.` and its path (a list is read through its first item: `event.Records.s3.object.size`). A short name stands for the one field that ends in it: `size`.
    - **The data**: `ui.core.runs(...)` returns a `RunTable`: `.where()`, `.sorted_by()`, `.group()`, `.fields()` and `.to_df()`, with no other read from AWS.

### Run times, memory and cold starts { #performance }

`performance()` says how long a typical run takes, whether runs got slower, and the memory they use against what they have.
{ .gist }

It reads the REPORT line Lambda logs at the end of every run, for the last 24 hours and the 24 hours before. It opens with a verdict, then shows every run over time, with the timeouts in a lane of their own so the other runs stay to scale.

```python
ui.performance("orders-etl")
```

![performance("orders-etl"): a verdict that 3 runs hung until the 60 s timeout while every other run finished within 8.66 s, and that runs are slower than the day before; cards for runs, the typical run, timeouts, memory, cold starts and cost; findings about the hung runs, the slowdown, 256 MB being enough, memory climbing in one container and cold starts; every run over the day with the timeouts in a red lane above; a table comparing the day with the one before; a histogram of run times with a gap up to the timeout; cold starts against warm runs; a memory bar marked at 256 MB; memory per container, one climbing; and the slowest runs that finished](images/lambda-performance-light.webp#only-light){ width="984" height="2796" loading=lazy }
![performance("orders-etl"): a verdict that 3 runs hung until the 60 s timeout while every other run finished within 8.66 s, and that runs are slower than the day before; cards for runs, the typical run, timeouts, memory, cold starts and cost; findings about the hung runs, the slowdown, 256 MB being enough, memory climbing in one container and cold starts; every run over the day with the timeouts in a red lane above; a table comparing the day with the one before; a histogram of run times with a gap up to the timeout; cold starts against warm runs; a memory bar marked at 256 MB; memory per container, one climbing; and the slowest runs that finished](images/lambda-performance-dark.webp#only-dark){ width="984" height="2796" loading=lazy }
/// caption
`ui.performance("orders-etl")`: three runs hung until the 60 s timeout while every other run finished within 9 s, and runs are slower than the day before.
///

??? more "What else the report shows"

    - **Compared with the 24 hours before**: runs, the typical and 1-in-10 run time, timeouts, cold starts, memory and cost, each with its change. `compare=False` skips that read.
    - **By version**, when runs came from more than one (a function called through a version or alias), or **before and since its last change** when it changed inside the window. A version that's a fifth slower is a finding, with the `diff()` to run.
    - **How long the runs that finished take**, with the stretch up to the timeout that no run reached: timeouts with nothing near them are stuck runs, and a longer timeout won't help.
    - **Cold starts against warm runs**: the start-up a cold one spends first, and its share of all run time.
    - **Memory per container**: the memory each run used, run after run, in each log stream. Memory that keeps climbing means something kept between runs grows, and the finding says about how many runs are left before Lambda kills one.
    - The slowest runs that finished, against the typical one, and the percentiles, folded.

!!! tip "Worth knowing"

    **Try a smaller size, then check again.** Lambda bills memory × run time, and memory also sets the CPU (a full vCPU at 1,769 MB). When runs use far less memory than the function has, the report suggests a smaller size that still leaves 30% room, never less than a quarter of what it has in one step, and what that would save if the run time stays the same. A function held back by its CPU may run slower with less memory, which can cost more, so try it, and run `performance()` again a day later. When runs come close to the memory or the timeout, it says so and gives the command that raises them.

## Run it and see its logs { #invoke }

`invoke()` runs a function once with an event and shows what it returned and every line it logged, in one report.
{ .gist }

It's the console's Test button and its log tab together: no switching tabs, and no waiting for CloudWatch to catch up. Lambda hands back the last 4 KB of a run's log with its answer, so the lines show at once; when the log is longer, the whole run is read from CloudWatch Logs as soon as it gets there (up to `wait_logs=10` seconds).

```python
ui.invoke("orders-etl", {"order_id": 42})        # a dict, a list, JSON text, or nothing for {}
ui.invoke("orders-etl:prod", '{"order_id": 42}')  # an alias, with the event as JSON text
ui.invoke("orders-etl", dry_run=True)             # only check that you may invoke it
```

The report opens with whether it ran or failed, then cards for its run time against the timeout, the memory it used, a cold start and what the call cost (and what a million like it would). Findings say what to change: a failure with the error that made it, a timeout with the command that raises it, memory close to running out with the command that adds more. Then what it returned (a failure as its traceback), its log lines as one run, opened, and the event it was sent, folded. Next steps run it again or open the run in `logs()`.

!!! tip "Worth knowing"

    **This really runs your function**, with whatever its code writes, sends or deletes: use it on functions that are safe to call with the event you give. It's called once and never retried, even when the answer is slow, and it's the only command that does more than read. It needs `lambda:InvokeFunction`, the agent can't use it, and `ui.next()` puts it in a cell for you to check rather than running it.

## Look at the code { #code }

`code()` shows what's in the deployment package and the handler's source, and catches secrets and dead weight packed into it.
{ .gist }

Nothing in it is run, and a secrets file (`.env`, keys, credentials) is listed but its text isn't shown, because a notebook's output is easy to share.

```python
ui.code("orders-etl")
ui.code("orders-etl", "db.py")           # another file: a path, a name, or a glob like "*.py"
ui.code("orders-etl", max_size="200MB")  # packages up to 50 MB are read by default
```

![code("orders-etl"): cards for the package's size zipped and unzipped, its files, layers and handler file; a warning that the package holds a .env file and a note that it carries its own boto3 and botocore; the folders that take the space; and the source of etl.py](images/lambda-code-light.webp#only-light){ width="984" height="1150" loading=lazy }
![code("orders-etl"): cards for the package's size zipped and unzipped, its files, layers and handler file; a warning that the package holds a .env file and a note that it carries its own boto3 and botocore; the folders that take the space; and the source of etl.py](images/lambda-code-dark.webp#only-dark){ width="984" height="1150" loading=lazy }
/// caption
`ui.code("orders-etl")`: a .env file anyone allowed `lambda:GetFunction` can read, and 13 MB of boto3 the runtime already has.
///

The findings: a handler setting no file in the package matches, secrets files packed in it (`.env`, keys, credentials), code and layers near Lambda's 250 MB limit, boto3 bundled although the runtime has it, and caches or tests that never run. A container image isn't pulled: the report names the image instead.

### How the code works { #how-it-works }

`how_it_works()` explains a Python function's code without running it: what each call does, what it reads, what AWS services it reaches, and what would break.
{ .gist }

It reads the package's own `.py` files with Python's `ast` module, the way you'd read the code yourself, keeping track of what each name holds (a boto3 client, part of the event, an environment variable) so it knows what each line calls.

```python
ui.how_it_works("support-agent-actions")
ui.how_it_works("orders-etl", max_size="200MB")   # packages up to 50 MB are read by default
```

The report opens with two tabs. **Graph** shows how it fits together: what comes in on the left, the handler and its functions in the middle, the AWS services it reaches on the right.

![The graph how_it_works("support-agent-actions") draws: the Bedrock agent's event and five environment variables on the left, handler() calling find_order(), open_ticket() and tracking() in the middle, and DynamoDB, the web and SNS on the right; TEAM_TOPIC is marked not set, Query first page only and the web request no timeout](images/lambda-how-it-works-light.webp#only-light){ width="984" height="441" loading=lazy }
![The graph how_it_works("support-agent-actions") draws: the Bedrock agent's event and five environment variables on the left, handler() calling find_order(), open_ticket() and tracking() in the middle, and DynamoDB, the web and SNS on the right; TEAM_TOPIC is marked not set, Query first page only and the web request no timeout](images/lambda-how-it-works-dark.webp#only-dark){ width="984" height="441" loading=lazy }
/// caption
The graph in `ui.how_it_works("support-agent-actions")`: `open_ticket()` reads `TEAM_TOPIC`, which isn't set, and `tracking()` calls the shipping API with no timeout.
///

**Step by step** lets you walk through a call the way a debugger would, without running anything. The code is on the left, and the step you're on is on the right: the functions it's in, the loops and branches around it, and what the line does. **Next** goes to the next line that runs, into the functions a line calls; **Over** goes past them, and **Out** back to the line after the one that called the function you're in. Click any line to jump to it, or a function in the path above the code to go back to where it starts.

![The Step by step tab of how_it_works("support-agent-actions"): the code from the import at start-up through handler(), find_order(), tracking() and open_ticket() on the left, each line tagged with what it does, and step 1 of 35 on the right, the import of support/orders.py, with the Back, Into support/orders.py, Over and Out buttons](images/lambda-how-it-works-steps-light.webp#only-light){ width="984" height="620" loading=lazy }
![The Step by step tab of how_it_works("support-agent-actions"): the code from the import at start-up through handler(), find_order(), tracking() and open_ticket() on the left, each line tagged with what it does, and step 1 of 35 on the right, the import of support/orders.py, with the Back, Into support/orders.py, Over and Out buttons](images/lambda-how-it-works-steps-dark.webp#only-dark){ width="984" height="620" loading=lazy }
/// caption
The **Step by step** tab: it starts with what runs when Lambda loads the code, then follows the handler line by line, into each function it calls.
///

Under the tabs are a few numbers (where it starts, the event it expects, its AWS calls and environment variables), a few sentences on what the code does, and the findings, what would break or cost it:

- An environment variable it reads with `os.environ[...]` that isn't set on the function: a `KeyError`, on every call when it's read at start-up.
- A module it imports that isn't in the package, the runtime or the standard library (`Runtime.ImportModuleError`), with the `pip install ... -t .` line, or one that a newer Python removed (`imp`, `distutils`, `cgi`), which breaks it when you move it to a newer runtime.
- A handler that isn't in its file, that's `async def` (the runtime doesn't await it), or that can't take `(event, context)`.
- An HTTP request without a timeout, which can wait until Lambda stops the run, a list call that reads only its first page (S3's first 1,000 keys, DynamoDB's first MB), and an `except` that drops every error, so a failed run reports success and its event is lost.
- A file written outside `/tmp`, where Lambda's disk is read-only, and a secret written in the code (its name is shown, never its value).
- An AWS call in a loop that a batch call would make in fewer requests (`batch_writer()`, `send_message_batch`), a client made on every call instead of once, and `time.sleep`, which the function is billed for.

??? more "How to read the graph"

    - On the left, what comes in: the event, with the fields the code reads and the source that sends events like it, and the environment variables, with an arrow to each function that reads them.
    - In the middle, the handler and its own functions, a column for each step down the calls, an arrow for each call, and **At start-up** for what runs when Lambda loads the code.
    - On the right, what it reaches: each AWS service with the operations it calls, and the web, for its HTTP requests. An arrow's colour says what the call does (blue reads, orange writes, pink sends, purple runs), and it lands on the operation it calls; an environment variable's arrow leaves from the variable's line. ↻ marks a call made inside a loop, once per item.
    - A box a warning is about has an amber edge and a **!**, and the line it's about says why: a variable that's **not set**, a list call that reads its **first page only**, a request with **no timeout**. An arrow a warning is about keeps its colour under an amber glow, with a **!** on it.
    - A dotted arrow from **At start-up** is a client made there. A dotted arrow in a call's colour is one known only by the method's name, because its client couldn't be followed, and a dash-dot one is a call made in functions further down that the graph leaves out.
    - Point at a box to leave only it and what it's joined to in sight, and at an arrow for what goes along it, in a sentence.

??? more "Mermaid, and the steps in detail"

    The graph and the steps are plain HTML, SVG and CSS, with no JavaScript, so it's still drawn when you reopen your notebook. JupyterLab doesn't keep a report's drawings and styles in a notebook it doesn't trust, such as one someone else saved, so run the cell again there. The same graph is under it in [Mermaid](https://mermaid.js.org/), folded, to paste in a Markdown file, a wiki or a pull request that draws it. In the notebook it draws at most 24 functions, 5 calls deep, and 5 lines a box, and says what it left out; the steps below have them all, and [`how_it_works_page()`](#how-it-works-page) draws the whole graph.

    The **Step by step** tab walks through two parts:

    - **When Lambda starts it**: the lines that run when Lambda loads the code, before the first call, such as the boto3 clients made at the top of a file and the environment variables read there, with the modules it imports that do something themselves.
    - **What each call does, step by step**: the handler's lines that do something worth knowing, in order, with the loops, `if`s, `with`s and `try`s around them, each tagged with what it does: an AWS call (blue when it reads, orange when it writes, pink when it sends a message, purple when it runs something), an environment variable, a field of the event, an HTTP request, a log line, a file. The plain lines between them are left out. A call to one of its own functions, in the same file or another, is followed by that function's lines, indented under it, and so on down, so **Next** follows a call through the whole package. A function called again shows its lines only the first time, and its step says where.

    The steps follow the code as it's written, not one run: both sides of an `if` are there, one after the other, and a loop's lines once. The code side scrolls to the step you're on. In the page [`how_it_works_page()`](#how-it-works-page) saves, opened in a browser tab, <kbd>↑</kbd> and <kbd>↓</kbd> step too once you've clicked a line (JupyterLab keeps the arrow keys for its cells).

    The code in the report is in [Cascadia Mono](https://github.com/microsoft/cascadia-code) when it's installed (Windows Terminal and many editors bring it), else your system's monospace font.

    For the detail as data, `core.code_map("support-agent-actions")` returns it: its AWS calls with the IAM permission each needs (`.aws`, and `.to_df()`), the environment variables it reads (`.env`), the event's fields (`.event`), its own functions (`.functions`) and what it imports (`.imports`).

#### Read it in a tab of its own { #how-it-works-page }

A big package makes a long report, and a notebook cell is narrow. `how_it_works_page()` saves the same report as a page of its own and shows a link that opens it.
{ .gist }

```python
ui.how_it_works_page("support-agent-actions")    # lambda-downloads/support-agent-actions-how-it-works.html
ui.how_it_works_page("orders-etl", "notes/etl.html")
```

In JupyterLab, a click on the link opens the page in a JupyterLab tab you can drag beside the notebook. Middle-click it, or right-click it and pick **Open link in new tab**, for a browser tab with the whole screen. The page is plain HTML and CSS, light or dark like your browser, and the steps and graph get all the room they need instead of scrolling in a box. The graph there is whole: every function the steps follow, and every operation, variable and event field in each box, where the notebook stops at 24 functions, 5 calls deep. In the explorer, **↗ Open in a tab** beside **How it works** does the same.

!!! tip "Worth knowing"
    The page is a snapshot of the code when you saved it. After a deploy, run it again: it replaces the page of the same name.

??? more "What it can't follow, and running it on code you have on disk"

    It follows the clients and resources the code makes with boto3, and the calls into its own functions and classes. A client passed in from somewhere it can't see, or made inside a library, isn't followed, so there may be AWS calls the report doesn't list. Libraries packed in the package (found by their `.dist-info` folders) aren't read as its code. Other runtimes and container images aren't read: `code()` still shows their files.

    `map_code()` does the same on code you have on disk, with nothing to download:

    ```python
    from pathlib import Path
    from aws_analyzer.lambda_functions import code_graph, code_map_findings, map_code

    files = {str(p): p.read_text() for p in Path(".").rglob("*.py")}
    cmap = map_code(files, "app.handler")
    cmap.aws                                          # AwsCall: service, operation, permission, file, line, loop
    code_map_findings(cmap)
    code_graph(cmap).mermaid()                        # the graph, for a Markdown file
    ```

### What changed between versions { #diff }

`diff()` shows what changed between two versions of a function: its code line by line, the libraries packed with it, and its settings.
{ .gist }

Each changed file shows its lines added and removed, and the Python functions the changes are in. Libraries are compared by version ("requests 2.31.0 → 2.32.3"), and environment variables by name only.

```python
ui.diff("orders-etl")              # $LATEST against the newest published version with different code
ui.diff("orders-etl", 3)           # $LATEST against version 3
ui.diff("orders-etl", 3, 5)        # version 3 against version 5
ui.diff("orders-etl:prod")         # what the alias runs, against the version before it
```

![diff("orders-etl"): cards comparing version 1 with $LATEST, 2 of 7 files changed, +3 −1 lines, the same libraries and 1 setting changed; a note that $LATEST makes a DynamoDB PutItem call the code didn't make before, at etl.py:22, so its role needs dynamodb:PutItem; the new ORDERS_TABLE environment variable; the new call with its permission and code; the files that changed; and etl.py's diff, with the added table = boto3.resource("dynamodb") line and the added table.put_item line that reads row["customer_id"]](images/lambda-diff-light.webp#only-light){ width="984" height="1237" loading=lazy }
![diff("orders-etl"): cards comparing version 1 with $LATEST, 2 of 7 files changed, +3 −1 lines, the same libraries and 1 setting changed; a note that $LATEST makes a DynamoDB PutItem call the code didn't make before, at etl.py:22, so its role needs dynamodb:PutItem; the new ORDERS_TABLE environment variable; the new call with its permission and code; the files that changed; and etl.py's diff, with the added table = boto3.resource("dynamodb") line and the added table.put_item line that reads row["customer_id"]](images/lambda-diff-dark.webp#only-dark){ width="984" height="1237" loading=lazy }
/// caption
`ui.diff("orders-etl")`: the deploy that added a DynamoDB copy of each order, the `put_item` line behind the `KeyError: 'customer_id'` in its logs, and the permission it needs.
///

For Python code, the findings name each AWS call the new version makes that the old one didn't, with the IAM permission its role needs, since a new call the role can't make fails every run that reaches it. They also flag a Python file that no longer reads (a syntax error fails every call that imports it), an environment variable taken out that the code still reads, a shorter timeout, less memory and a new runtime. Versions published without a code change are skipped, and the subtitle says which.

!!! tip "Worth knowing"

    Lambda keeps the code of **published versions** only. Code deployed over `$LATEST` without publishing a version is gone, so there's nothing to compare it with, and the report says so. To keep a copy of each deploy, publish a version after it: `aws lambda publish-version --function-name orders-etl` (SAM's `AutoPublishAlias` and CDK's `currentVersion` do it for you).

??? more "How files are compared"

    Unchanged files are told apart by the .zip's index (each file's CRC-32 and size), so they aren't unpacked. Files of the libraries packed with it are counted, not shown line by line. Secrets files (`.env`, keys) are compared by size and never read, and so are binaries, minified code and files over 1 MB. A package built again with the same files has a new SHA-256: the report says the code didn't change. A container image's files aren't compared, only which image each version runs. Nothing in either package is run.

### Download the package { #download }

`download()` saves the deployment package as a .zip next to the notebook, and checks it's the code the function runs.
{ .gist }

It says where it went, how big it is, how many files it holds, and whether its SHA-256 matches the one Lambda reports. Nothing in it is unzipped or run.

```python
ui.download("orders-etl")                     # lambda-downloads/orders-etl.zip, next to the notebook
ui.download("churn-scoring:live")             # the version an alias points to: churn-scoring-v<version>.zip
ui.download("orders-etl", "~/packages/")      # into a folder of your own (or a file name: "~/etl.zip")
```

??? more "Where it goes, getting it onto your computer, and unzipping it"

    Without a path it goes in `lambda-downloads/`, next to the notebook, and replaces an earlier download of the same function. When that folder is new, it gets a `.gitignore` that keeps it out of git, because a package can hold secrets. To get the file on your computer, right-click it in JupyterLab's file browser and pick **Download**. To unzip it next to the notebook, the report's **Next** row has the line to run: `import shutil; shutil.unpack_archive('lambda-downloads/orders-etl.zip', 'lambda-downloads/orders-etl')`. The explorer's Code tab has the same as a **Download .zip** button, which saves the package the tab already read.

<p class="part"><strong>That's the guide.</strong> Everything below is reference for when you need it: the data in Python, cost, permissions, troubleshooting and every command.</p>

## Use the data in Python { #python }

Every report has a data version on `ui.core` (a `LambdaAnalyzer`) that returns dataclasses and DataFrames instead of printing.

```python
lam = ui.core

ov = lam.overview(regions="all")                  # Overview: .functions, .metrics, .triggers, .accounts
df = ov.to_df()                                   # one row per function: settings, calls, errors, cost
detail = lam.describe("orders-etl")               # FunctionDetail: .function, .triggers, .aliases, .metrics, ...
detail.metrics.daily                              # DailyUsage per day: invocations, errors, throttles, run time

lam.errors("orders-etl", since="7d").groups       # ErrorGroup: kind, message, count, first, last, request_ids
lam.performance("orders-etl").to_df()             # one row per run: duration, billed, memory used, cold start
lam.log_events("orders-etl", since="1h", pattern='"KeyError"').to_df()
runs = lam.log_runs("orders-etl", since="24h").runs   # LogRun per call: .status, .duration, .cold, .events
lam.log_runs("orders-etl", search="ORD-1042").runs     # the runs with a line that has it, each one whole
lam.code("orders-etl").files                      # CodeFile: path, size, compressed
lam.search_code("orders-etl", "customer_id")      # CodeSearch: every line that has it, like grep -rn
lam.find_symbol("orders-etl", "load_orders")      # SymbolReport: where it's defined and every line that uses it
cmap = lam.code_map("orders-etl")                 # CodeMap: .steps, .aws, .env, .event, .imports, .functions
cmap.to_df()                                      # one row per AWS call: service, operation, permission, where
lam.download("orders-etl").path                   # PackageDownload: where it was saved, size, files, sha256_matches
lam.versions("orders-etl")                        # Version per published version: number, code SHA-256, when, description
diff = lam.diff("orders-etl", 3)                  # VersionDiff: .files (FileChange: lines, definitions), .libraries, .settings
```

The analysis functions don't call AWS, so they also work on data you already have: `parse_function`, `parse_policy`, `parse_event_source_mapping`, `runtime_status`, `function_monthly_cost`, `provisioned_monthly_cost`, `classify_error`, `group_errors`, `change_effect` (its calls and errors before and after its last change), `cause_search` (what to search the logs for to find a cause's runs), `code_frames` and `failing_frame` (the lines of the package a traceback names), `parse_report`, `split_runs` (log lines grouped into runs), `run_timeline` (runs counted by slice of time), `slow_cutoff` (the run time of the slowest 10%), `run_console_url` and `run_insights_query` (a run in the AWS console and in Logs Insights), `line_level`, `percentile`, `suggest_memory`, `run_stats`, `performance_verdict`, `run_groups` and `slowdown` (runs by version or around the last change), `time_slices`, `run_time_bins`, `cold_vs_warm`, `memory_by_container` (memory run after run per container), `handler_file`, `read_package` (a .zip you already have), `package_file_name`, `page_file_name`, `secret_like`, `source_files` (a package's own source files), `read_sources` (`sources()`: the text of a .zip's own source files), `search_package` and `find_symbol_in` (`search_code()` and `find_symbol()` on a .zip's bytes), `map_code` (Python code read with `ast`, [above](#how-it-works)), `map_package` (the same from a .zip's bytes), `code_graph` (a CodeMap as boxes and arrows, with `.mermaid()`), `diff_packages` (`diff()` on two .zip files you already have), `diff_settings`, `version_before` (the version `diff()` compares with), `stream_version` (the version a log stream's name says ran), and the findings: `function_findings`, `account_findings`, `error_findings`, `performance_findings`, `package_findings`, `code_map_findings`, `diff_findings`.

```python
from aws_analyzer.lambda_functions import parse_report, runtime_status, suggest_memory

runtime_status("python3.9")                       # RuntimeStatus: state 'deprecated', upgrade 'python3.14'
parse_report(report_line).max_memory              # a REPORT line from a log export
suggest_memory(max_used=180, memory=1024)         # 256
```

## Cost { #cost }

<div class="grid cards" markdown>

- **Calls and compute**

    $0.20 per million requests, and $0.0000166667 per GB-second of memory × run time on x86_64 ($0.0000133334 on arm64). `/tmp` above 512 MB is $0.0000000309 per GB-second.

- **Provisioned concurrency**

    $0.0000041667 per GB-second for every copy kept ready, used or not ($0.0000033334 on arm64): 4 copies of a 2 GB function cost about $70 a month on arm64.

- **Logs**

    $0.50 per GB a function logs to CloudWatch Logs, and $0.03 per GB-month kept. Chatty functions often pay more for logs than for compute.

- **The tool itself**

    `functions()` reads 7 CloudWatch metrics per function, plus one per region ($0.01 per 1,000 metrics), and says how many. Reading logs (`FilterLogEvents`) and downloading code are free.

</div>

Costs are estimates at us-east-1 list prices from the AWS Price List API (`LAMBDA_PRICES`), before the free tier (1 million requests and 400,000 GB-seconds a month for the whole account), at the first pricing tier, and with compute that runs on provisioned concurrency billed at the on-demand rate (it's a little cheaper). Usage is what CloudWatch counted over the window, scaled to a month. API Gateway, SQS and data transfer aren't included. For another region or a discount, pass your own:

```python
ui = LambdaView(LambdaAnalyzer(prices={"gb_second": 0.0000183, "request": 0.22}))
```

## Permissions { #permissions }

Everything is read-only but `invoke()`, which needs `lambda:InvokeFunction` as well (leave it out to keep the role read-only). Anything the notebook's role can't read shows up as a note instead of an error, so you can start with less and add what you need. This IAM policy covers every command:

```json title="IAM policy"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "Functions",
      "Effect": "Allow",
      "Action": [
        "lambda:ListFunctions", "lambda:GetFunction", "lambda:GetPolicy", "lambda:ListEventSourceMappings",
        "lambda:GetFunctionUrlConfig", "lambda:GetFunctionEventInvokeConfig", "lambda:ListVersionsByFunction",
        "lambda:ListAliases", "lambda:ListProvisionedConcurrencyConfigs", "lambda:GetRuntimeManagementConfig",
        "lambda:GetAccountSettings"
      ],
      "Resource": "*"
    },
    {
      "Sid": "FailedMessages",
      "Effect": "Allow",
      "Action": ["sqs:GetQueueUrl", "sqs:GetQueueAttributes"],
      "Resource": "*"
    },
    {
      "Sid": "UsageAndLogs",
      "Effect": "Allow",
      "Action": [
        "cloudwatch:GetMetricData", "logs:DescribeLogGroups", "logs:DescribeLogStreams", "logs:FilterLogEvents"
      ],
      "Resource": "*"
    },
    {
      "Sid": "EveryRegion",
      "Effect": "Allow",
      "Action": "ec2:DescribeRegions",
      "Resource": "*"
    }
  ]
}
```

`lambda:GetFunction` also returns the link `code()`, `how_it_works()`, `diff()` and `download()` download the package from, and the environment variables' values, which the reports never show. Leave it out and `function_info()`, `errors()`, `logs()`, `performance()`, `code()` and `how_it_works()` can't start; give it to roles that may read the code. `diff()` lists the versions with `lambda:ListVersionsByFunction`. `ec2:DescribeRegions` is only for `regions="all"`. `sqs:GetQueueUrl` and `sqs:GetQueueAttributes` are only for `errors()`' failed messages in an SQS trigger's queue. The explorer window reads with the same permissions as the commands: a tab whose permission is missing says which one, and the other tabs still work.

## Troubleshooting { #troubleshooting }

??? question "“No Lambda functions in us-east-1”"

    Functions are regional, and the view reads the notebook's region unless told otherwise. `functions(regions="all")` looks in every region your account has turned on, and `LambdaView(LambdaAnalyzer(region="eu-west-1"))` makes another region the default.

??? question "“Couldn't read … (AccessDeniedException; needs lambda:…)”"

    The notebook's role is missing that permission. The note names it; add it from [Permissions](#permissions). The rest of the report still shows what could be read.

??? question "“… has no log group yet”"

    The function hasn't logged anything: it hasn't run, or its role can't write logs (`logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents`, in the `AWSLambdaBasicExecutionRole` managed policy). A function set to log to its own log group is read from there.

??? question "CloudWatch counts failed calls, but `errors()` finds no error lines"

    The code fails without logging the error (an unhandled promise rejection, a process exit), or its logging is turned down: with JSON logs, a log level above ERROR drops them. `logs()` shows what it did log.

??? question "“Couldn't download the code”"

    The package comes from S3 over HTTPS, so a notebook in a VPC without internet access needs an S3 gateway endpoint. The link Lambda gives expires after 10 minutes: run `code()` again.

??? question "The cost doesn't match my bill"

    The estimate is before the free tier, which covers small accounts entirely, and leaves out what other services charge for the same calls (API Gateway, SQS, data transfer). It scales the last 30 days to a month, so a function whose traffic changed lately is estimated from its old traffic too.

??? question "explore() shows a report instead of the window"

    The window needs Jupyter and `ipywidgets`. SageMaker has both; elsewhere, `%pip install ipywidgets`, then restart the kernel and reload the browser tab. In a terminal or a script, `explore()` shows the same as reports: `functions()`, or `function_info()` and the other reports for one function.

??? question "A run in the Logs tab says “no REPORT line yet”"

    Lambda writes a run's REPORT line when the run ends, so the run is still going, or it ended after the time range read. Click ↻ to read the range again, or turn on Live. A run whose START was before the range shows the lines the range holds.

??? question "The explorer window went blank after I reopened the notebook"

    Widgets live in the running kernel, so a saved notebook doesn't keep the window. Run the cell again.

??? question "The report lost its formatting after I reopened the notebook"

    JupyterLab strips the report's styles from saved output when a notebook is reopened. Run the cell again to get the formatted report back.

??? question "“Nothing to compare $LATEST with”"

    The function has no published versions, and Lambda doesn't keep code it replaced. Publish a version after each deploy from now on (`aws lambda publish-version --function-name orders-etl`, or SAM's `AutoPublishAlias`), and `diff()` can compare the next deploy with it. If the code came from an S3 bucket with versioning, the older .zip may still be there.

## Command reference { #reference }

Every `LambdaView` command. `ui.help()` prints the same list grouped by task, and `ui.help("logs")` shows one command's full description.

<div class="ref" markdown>

| Command | What it shows |
|---|---|
| `explore(name=None, tab=None, region=None, height=None, history=True)` | [The explorer window](#explorer): every function, and for the one you click its health, logs run by run, errors, run times, code and settings, by clicking |
| `functions(match=None, regions=None, days=30, metrics=True, details=True)` | Every function in the region (or regions, `"all"`), the ones that need you first: a verdict, where the bill goes, what to fix first with the commands, then each function by group with its calls a day, failed share, longest run against its timeout and cost |
| `function_info(name, region=None, days=30)` | One function: charts of its calls, failures and run time a day, how it's wired, where its cost goes, then what it runs, what triggers it and who may call it, failed asynchronous events, what it can reach, versions and aliases, its last 30 days, cost, findings |
| `errors(name, since="24h", region=None, limit=10000)` | Its errors grouped by cause, with what to do about each and the run to look at |
| `logs(name, since=None, search=None, request_id=None, n=50, region=None)` | The newest lines it logged, or one run from start to end |
| `runs(name, since="24h", where=None, sort=None, columns=None, group_by=None, fields=None, n=50, region=None, limit=10000)` | [Its runs as rows](#runs): the ones a query keeps (`duration > 15s and cold`), sorted, in the columns you pick, or summed up by hour, error or any field |
| `performance(name, since="24h", region=None, limit=5000, compare=True)` | A verdict, every run over time, how it compares with the 24 hours before and between versions, cold starts against warm runs, memory per container, the slowest runs, and the memory size that would do |
| `invoke(name, event=None, region=None, wait_logs=10, dry_run=False)` | [Runs it once](#invoke) and shows what it returned, its run time, memory used, cold start and cost, and every line it logged during that run |
| `code(name, file=None, region=None, max_size="50MB")` | The files in its deployment package, findings about them, and the handler's source |
| `diff(name, old=None, new=None, region=None, max_size="50MB")` | What changed between two versions: its files line by line with the functions each change is in, the libraries packed with it, its settings and environment variable names |
| `how_it_works(name, region=None, max_size="50MB")` | How its Python code works, as a graph and as steps to click through from the handler, then what to fix |
| `how_it_works_page(name, path=None, region=None, max_size="50MB")` | The same report as a page of its own, saved in `lambda-downloads/` (or at `path=`), with a link that opens it in a tab |
| `download(name, path=None, region=None)` | Its deployment package saved on the notebook's disk, in `lambda-downloads/` (or at `path=`), checked against Lambda's SHA-256 |
| `help(command=None)` | This list, grouped by task; `help("name")` shows one command in full |

</div>
