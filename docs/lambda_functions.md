---
title: Lambda Functions Guide
description: "How to see every AWS Lambda function, in one region or all of them, from a SageMaker notebook with aws-analyzer's lambda_functions.py: runtimes and their end of support, triggers and public access, calls, errors and cost, errors grouped by cause, logs run by run, memory and cold starts, and the code, in reports or in an explorer window you click through, with examples."
---

<p class="eyebrow"><img class="aws-icon" src="images/aws/lambda.svg" alt="" width="32" height="32"> AWS Lambda <code>lambda_functions.py</code></p>

# Find out which Lambda failed, and why, without the console

Every function, in one region or all of them: what's failing and why, read from its own logs, what it costs, which runtimes lose support and who else can call it. Or click through it all in the explorer window.
{ .lede }

<ul class="pills">
  <li>One install, boto3 only</li>
  <li>Read-only: never invokes or changes a function</li>
  <li>Every region at once</li>
  <li>An explorer window: click, don't type</li>
  <li>Plain text outside Jupyter</li>
</ul>

The examples use acme's functions: `orders-etl` loads each day's orders from S3 into the warehouse, `churn-scoring` scores customers with a SageMaker model, and `report-api` serves a sales report over a function URL. The screenshots are the tool's real output on [moto](https://github.com/getmoto/moto).
{ .muted }

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

Seven questions people bring to Lambda, and the one line that answers each. Nothing is invoked, changed or deleted.
{ .gist }

<div class="quick" markdown>

-   **What do I have, and what's failing or costing money?**

    ```python
    ui.functions(regions="all")
    ```

    [Every function](#functions)

-   **Why did this function fail?**

    ```python
    ui.errors("orders-etl")
    ```

    [When something goes wrong](#errors)

-   **I'd rather click through it all.**

    ```python
    ui.explore()
    ```

    [The explorer window](#explorer)

-   **What did it log?**

    ```python
    ui.logs("orders-etl", search="KeyError")
    ```

    [Logs](#logs)

-   **Is it paying for memory it never uses?**

    ```python
    ui.performance("orders-etl")
    ```

    [Run times and memory](#performance)

-   **What does its code actually do?**

    ```python
    ui.how_it_works("orders-etl")
    ```

    [How the code works](#how-it-works)

-   **It worked last week. What changed?**

    ```python
    ui.diff("orders-etl")
    ```

    [What changed between versions](#diff)

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

Every function in the region is listed with how it's doing. A click away are its errors grouped by cause, its run times, the code in its package and every setting.

```python
from aws_analyzer.lambda_functions import explore

explore()                                # every function in the notebook's region
explore("orders-etl")                    # straight to one function: a name, an ARN or a console link
explore("orders-etl", tab="logs")        # ...on its logs (or "errors", "performance", "code", "settings")
explore(region="all")                    # every region your account has turned on
explore(region="eu-west-1", profile="dev")   # another region or AWS profile
explore(height=800)                      # 800px pages (else the browser's height)
ui.explore("orders-etl")                 # from a LambdaView
```

??? more "The top of the window, and without ipywidgets"

    **The top of the window** is the region field, the function field and their cards. The region field switches to another region, or to every region at once. Click the function field for every function, with a search box that finds one by its name, description, runtime or region; Enter picks the first, and a function's ARN or console link works too, even one in another region. With no function picked, the cards sum up the region: its functions, their calls and error rate over 30 days, the estimated monthly cost, and how many need attention. With one picked, they're its own. The line at the bottom of the window says what's going on.

    The window needs `ipywidgets`, which SageMaker notebooks already have (elsewhere, `%pip install ipywidgets`, then reload the browser tab). Without it, or outside Jupyter, `explore()` shows the same as reports: [`functions()`](#functions), or for one function [`function_info()`](#function-info) or the report of the tab you asked for.

![The Functions tab of the explorer: the region field, the function field and cards for 5 functions, 192,496 calls in 30 days, a 0.3% error rate, $93.68 a month and 4 that need attention; chips for the functions that need attention, have errors, run an old runtime, are public or weren't called; and the list, problems first: orders-etl on python3.9 (ended) with a 0.6% error rate in red and 3 warnings, report-api on nodejs20.x (ended) and public, churn-scoring, feature-backfill not called in 30 days, and support-agent-actions, each with its calls, error rate, average run time, monthly cost and when it was last called](images/lambda-explorer-light.webp#only-light){ width="984" height="861" loading=lazy }
![The Functions tab of the explorer: the region field, the function field and cards for 5 functions, 192,496 calls in 30 days, a 0.3% error rate, $93.68 a month and 4 that need attention; chips for the functions that need attention, have errors, run an old runtime, are public or weren't called; and the list, problems first: orders-etl on python3.9 (ended) with a 0.6% error rate in red and 3 warnings, report-api on nodejs20.x (ended) and public, churn-scoring, feature-backfill not called in 30 days, and support-agent-actions, each with its calls, error rate, average run time, monthly cost and when it was last called](images/lambda-explorer-dark.webp#only-dark){ width="984" height="861" loading=lazy }
/// caption
**Functions**: every function with its runtime, calls, error rate, run time, cost and warnings, the ones that need attention first.
///

### Every function { #explorer-functions }

The **Functions** tab puts the functions that need attention first. A dot says how each is doing: red when its calls fail often enough to act on, amber when it has a warning, green when all is well, hollow when nothing called it in 30 days.
{ .gist }

Search by name, description, runtime, region or trigger (`sqs`), click a chip for only the failing, old or public ones, and click **Logs** on a line to go straight to its logs.

**Recent** shows only the functions you've opened before, the ones you open most and most lately first, and the function field lists them first, each saying when you last opened it. The window remembers them between sessions, per AWS account: only their ARNs and when, in `~/.cache/aws-analyzer/history/places.sqlite`, which `clean_up()` keeps. `x.recent` lists them, `x.forget_recent("orders-etl")` forgets one and `x.forget_recent()` every one. `explore(history=False)` remembers nothing, and `AWS_ANALYZER_HISTORY=off` turns it off for every explorer.

![The Functions tab with the Recent chip picked: All 5, Recent 2, and only orders-etl and report-api in the list, the two functions opened before](images/lambda-explorer-recent-light.webp#only-light){ width="984" height="861" loading=lazy }
![The Functions tab with the Recent chip picked: All 5, Recent 2, and only orders-etl and report-api in the list, the two functions opened before](images/lambda-explorer-recent-dark.webp#only-dark){ width="984" height="861" loading=lazy }
/// caption
**Recent**: the functions you've opened, in this session or an earlier one.
///

### Logs, run by run { #explorer-logs }

The **Logs** tab is CloudWatch grouped the way you think about it: one line per run, ✓ or ✕, newest first. Click a run to read every line it logged.
{ .gist }

Each run shows how long it ran against the timeout, the memory it used, a cold start, and what made it fail. Times are in UTC, or in the zone [`set_timezone()`](index.md#timezone) picked.

![The Logs tab of orders-etl over the last 24 hours: the time range, the search box, Live and ↻; chips for all 70 runs, 13 failed, 3 timed out and 8 cold starts; a line saying 70 runs, 13 failed (18.6%), a median of 4.87 s, the slowest 60.0 s of 60 s, memory up to 182 of 1,024 MB and 8 cold starts; then the runs, newest first, each with its time, a ✓ or ✕, a bar of its run time against the timeout, its memory and its first line; a failed run is open, showing reading s3://acme-uploads/orders/2026-10-06/batch-002.json at +0.050s, then KeyError: 'customer_id' and its traceback in red, then Ran 1.73 s, billed 1.73 s, used 151 of 1,024 MB and its request ID; under it a run that timed out after 60.0 s with a full red bar](images/lambda-explorer-logs-light.webp#only-light){ width="984" height="861" loading=lazy }
![The Logs tab of orders-etl over the last 24 hours: the time range, the search box, Live and ↻; chips for all 70 runs, 13 failed, 3 timed out and 8 cold starts; a line saying 70 runs, 13 failed (18.6%), a median of 4.87 s, the slowest 60.0 s of 60 s, memory up to 182 of 1,024 MB and 8 cold starts; then the runs, newest first, each with its time, a ✓ or ✕, a bar of its run time against the timeout, its memory and its first line; a failed run is open, showing reading s3://acme-uploads/orders/2026-10-06/batch-002.json at +0.050s, then KeyError: 'customer_id' and its traceback in red, then Ran 1.73 s, billed 1.73 s, used 151 of 1,024 MB and its request ID; under it a run that timed out after 60.0 s with a full red bar](images/lambda-explorer-logs-dark.webp#only-dark){ width="984" height="861" loading=lazy }
/// caption
**Logs**: orders-etl's last 24 hours, run by run, with a failed run open: its traceback, and its REPORT line summed up.
///

??? more "The time range, searching CloudWatch, the chips and Live"

    - **The time range**, from the last 15 minutes to the last 30 days (the last hour at first), or **Custom range…**: pick **From** and **To** on a calendar (in the reports' time zone; leave To empty for up to now) and click **Show runs**, to read last Tuesday's 09:00 to 17:30. While a range is read, a bar fills as it goes, with how far back it has read and the lines found so far. The newest 3,000 lines are read; when the range holds more, **Older runs** under the list reads further back. A range with nothing in it says when the function last logged, and a button picks a range that reaches it.
    - **The search box** finds runs as you type: an order ID, `KeyError`, a request ID, in any case, among the runs read. **Enter** asks CloudWatch to search the whole time range, and brings back each run with a matching line whole, the matching lines marked. A CloudWatch Logs filter pattern (`?ERROR ?WARN`, `{ $.level = "ERROR" }`) works too, with Enter. ✕ shows every run again.
    - **The chips** show only the runs that failed, timed out, logged an error (and may have handled it) or were cold starts.
    - **Live** looks for new lines every 5 seconds and puts the new runs at the top, to watch a function while you test it. It stops when you leave the tab, and after 15 minutes.

    The line over the runs sums up the ones shown: how many, how many failed, the median and slowest run time against the timeout, the most memory used, and the cold starts.

![Searching orders-etl's last 24 hours for AccessDenied: 2 runs, both failed, both open; each shows its lines with AccessDenied highlighted in the ClientError that says the role acme-lambda-role isn't authorized to perform dynamodb:PutItem on the orders table, the line marked with a blue bar at its left; the first run was a cold start and starts with its INIT_START line](images/lambda-explorer-search-light.webp#only-light){ width="984" height="861" loading=lazy }
![Searching orders-etl's last 24 hours for AccessDenied: 2 runs, both failed, both open; each shows its lines with AccessDenied highlighted in the ClientError that says the role acme-lambda-role isn't authorized to perform dynamodb:PutItem on the orders table, the line marked with a blue bar at its left; the first run was a cold start and starts with its INIT_START line](images/lambda-explorer-search-dark.webp#only-dark){ width="984" height="861" loading=lazy }
/// caption
**AccessDenied**, searched in CloudWatch: two runs in the last 24 hours, each brought back whole, the matching line marked.
///

Logs in JSON (a function whose log format is JSON) show each line's message, with its other fields a click away. An error the runtime logged in JSON reads like a traceback: its type, its message, then its stack.

![report-api's Logs tab over the last 3 hours: 45 runs, 3 failed; a good run open, its lines GET /reports/weekly?region=AMER and 200 in 99 ms marked INFO, each with {…} fields to open; a failed run open, its ERROR line reading TypeError: Cannot read properties of undefined (reading 'total') then the stack, and its summary saying status error (TypeError)](images/lambda-explorer-json-light.webp#only-light){ width="984" height="861" loading=lazy }
![report-api's Logs tab over the last 3 hours: 45 runs, 3 failed; a good run open, its lines GET /reports/weekly?region=AMER and 200 in 99 ms marked INFO, each with {…} fields to open; a failed run open, its ERROR line reading TypeError: Cannot read properties of undefined (reading 'total') then the stack, and its summary saying status error (TypeError)](images/lambda-explorer-json-dark.webp#only-dark){ width="984" height="861" loading=lazy }
/// caption
report-api logs JSON: each line's message, its level, and its other fields a click away.
///

### How it's doing { #explorer-overview }

The **Overview** tab has what to fix first, then **how it's wired**: what calls the function and where its results, failed events and logs go.
{ .gist }

A failure destination it doesn't have shows in amber. Then its calls a day over 30 days, the failed part in red, its run time a day against the timeout, and what it costs.

![orders-etl's Overview below its findings: How it's wired, with its SQS queue orders-queue (batches of up to 10) and S3 bucket acme-uploads on the left, the function in the middle (python3.9, 1,024 MB, 60 s timeout, x86_64), and on the right On failure: nowhere: dropped in amber, its log group kept forever with 24.5 GB stored, and the role it may use; then a column per day of the last 30 days for its calls, a thin red top where calls failed, and its run time a day, the average under the longest, against a dashed line at its 60 s timeout](images/lambda-explorer-overview-light.webp#only-light){ width="984" height="861" loading=lazy }
![orders-etl's Overview below its findings: How it's wired, with its SQS queue orders-queue (batches of up to 10) and S3 bucket acme-uploads on the left, the function in the middle (python3.9, 1,024 MB, 60 s timeout, x86_64), and on the right On failure: nowhere: dropped in amber, its log group kept forever with 24.5 GB stored, and the role it may use; then a column per day of the last 30 days for its calls, a thin red top where calls failed, and its run time a day, the average under the longest, against a dashed line at its 60 s timeout](images/lambda-explorer-overview-dark.webp#only-dark){ width="984" height="861" loading=lazy }
/// caption
**Overview**, under the findings: failed S3 events go nowhere, its logs are kept forever, and its longest runs come close to the 60 s timeout.
///

### Errors, run times, code and settings { #explorer-more }

The other tabs are the reports below, a click away: **Errors** is [`errors()`](#errors), **Performance** is [`performance()`](#performance), **Code** is the package as a tree you click through, and **Settings** is every setting in plain English.
{ .gist }

??? more "What each of those tabs does"

    - **Errors** is [`errors()`](#errors): the errors in its logs grouped by cause, with what to do about each, then the newest failed runs. Click one to read every line of it in the Logs tab.
    - **Performance** is [`performance()`](#performance): run times against the timeout, the memory used against what it has and the size that would do, cold starts, and the slowest runs, each a click from its lines.
    - **Code** downloads the deployment package once and shows its files as a tree of folders, folders first, with the handler's folder open (a folder that holds only a folder shares its line, as `botocore/data/` does). Click a folder to open or fold it, and a file to see its source, with line numbers, and Python and JSON in colour; **Copy** beside its name selects all of it, so <kbd>Ctrl</kbd>+<kbd>C</kbd> (<kbd>⌘</kbd>+<kbd>C</kbd> on a Mac) copies the file. Typing in **Find a file** shows only the files that match, in their folders. **Hide files** folds the tree away so the source has the whole width, and **Show files** brings it back (it stays hidden for the next function you open). For a Python function, **How it works** shows [how its code works](#how-it-works) in place of the source: the graph of what calls what, the steps of a call from the handler on, its AWS calls, the environment variables and event fields it reads, and what to fix; click it again, or a file in the tree, for the source. While it's shown, **↗ Open in a tab** saves it as a page of its own, like [`how_it_works_page()`](#how-it-works-page), with the link that opens it under the buttons. **Download .zip** saves the package, the .zip as Lambda stores it, in `lambda-downloads/` next to the notebook, like [`download()`](#download). A secrets file (`.env`, keys) is listed but its text isn't shown, and a package over 50 MB is downloaded only when you click **Download it anyway**.
    - **Settings** has every setting in plain English, then the configuration as Lambda returns it, folded (open it for the JSON in colour, where a click on an object or a list folds it, and hovering over a line shows its path in `raw`), and the AWS CLI commands that read it. Environment variable values never appear.

    The Errors and Performance tabs read the last 24 hours, and have their own time range.

![orders-etl's Errors tab over the last 24 hours: cards for 35 failed calls, 1,177 calls, a 3.0% error rate, 13 error lines, 3 causes and 3 timeouts; warnings that 3 runs timed out at the 60 s limit, that its role acme-lambda-role isn't allowed dynamodb:PutItem, and that KeyError: 'customer_id' happened 8 times, each naming the run to read in the Logs tab; then the errors by cause with their share, and the newest failed runs](images/lambda-explorer-errors-light.webp#only-light){ width="984" height="861" loading=lazy }
![orders-etl's Errors tab over the last 24 hours: cards for 35 failed calls, 1,177 calls, a 3.0% error rate, 13 error lines, 3 causes and 3 timeouts; warnings that 3 runs timed out at the 60 s limit, that its role acme-lambda-role isn't allowed dynamodb:PutItem, and that KeyError: 'customer_id' happened 8 times, each naming the run to read in the Logs tab; then the errors by cause with their share, and the newest failed runs](images/lambda-explorer-errors-dark.webp#only-dark){ width="984" height="861" loading=lazy }
/// caption
**Errors**: three causes, each with what to do; the failed runs under them open in the Logs tab.
///

![orders-etl's Code tab: cards for a 17.2 KB package, 16.0 MB unzipped, 7 files, no layers and the handler file etl.py; a warning that the package holds .env and a note that it carries its own boto3 and botocore; a Hide files button, then a Download .zip button that saves it in lambda-downloads/; on the left its files as a tree, the folders boto3/, botocore/data/ (open, holding endpoints.json) and psycopg2/ with their sizes, then .env marked KEY, db.py, etl.py marked HANDLER and requirements.txt; on the right etl.py with line numbers, its keywords, strings and calls in colour, and a Copy button](images/lambda-explorer-code-light.webp#only-light){ width="984" height="861" loading=lazy }
![orders-etl's Code tab: cards for a 17.2 KB package, 16.0 MB unzipped, 7 files, no layers and the handler file etl.py; a warning that the package holds .env and a note that it carries its own boto3 and botocore; a Hide files button, then a Download .zip button that saves it in lambda-downloads/; on the left its files as a tree, the folders boto3/, botocore/data/ (open, holding endpoints.json) and psycopg2/ with their sizes, then .env marked KEY, db.py, etl.py marked HANDLER and requirements.txt; on the right etl.py with line numbers, its keywords, strings and calls in colour, and a Copy button](images/lambda-explorer-code-dark.webp#only-dark){ width="984" height="861" loading=lazy }
/// caption
**Code**: the package as a tree of folders, the handler's source to copy, and a `.env` file that shouldn't be in the package.
///

??? more "Driving the window from code"

    Everything is read in the background: a click shows what's known at once and fills in the rest as it arrives, and the window keeps answering meanwhile. `explore()` returns the window: `x.overview`, `x.function`, `x.detail`, `x.logs_page`, `x.package`, `x.code_map` (what **How it works** read) and `x.saved` (what **Download .zip** saved) hold the data behind what's shown; `x.open("report-api")`, `x.logs(search="KeyError", since="24h")` (or `x.logs(since="2026-10-27 09:00", until="2026-10-27 17:30")` for a custom range), `x.run("c0ffee00-1d2e-4f3a-9b8c-7d6e5f4a3b2c")` and `x.refresh()` do what the clicks do; and `x.ui` is a `LambdaView` for reports in other cells.

## Every function { #functions }

Start with `functions()`: one table answers what you have, what's failing, what costs money and what needs attention.
{ .gist }

```python
ui.functions(regions="all")
```

![functions(regions="all"): six functions in two of 24 regions with runtime, memory, timeout, triggers, calls, error rate, average run time, when each was last called, estimated monthly cost and warnings; a table by region with code storage and the most runs at once; and warnings about idle provisioned concurrency, runtimes past or near the end of support, rising errors, runs close to the timeout, throttled calls and a public function URL](images/lambda-functions-light.webp#only-light){ width="984" height="1491" loading=lazy }
![functions(regions="all"): six functions in two of 24 regions with runtime, memory, timeout, triggers, calls, error rate, average run time, when each was last called, estimated monthly cost and warnings; a table by region with code storage and the most runs at once; and warnings about idle provisioned concurrency, runtimes past or near the end of support, rising errors, runs close to the timeout, throttled calls and a public function URL](images/lambda-functions-dark.webp#only-dark){ width="984" height="1491" loading=lazy }
/// caption
`ui.functions(regions="all")`: two runtimes AWS no longer patches, $70 a month of provisioned concurrency for a function that never runs two at once, and 3% of calls failing in the last three days, against 0.6% over the month.
///

- **Runtime**, marked when AWS has ended its support (no more security patches) or ends it within 90 days. The dates come from AWS's published schedule (`RUNTIMES` in the file); [Runtime support](#runtimes) explains them.
- **Triggers**: the queues and streams Lambda reads for it (event source mappings), the services and accounts its resource policy lets call it (S3, EventBridge, API Gateway, a Bedrock agent...), and its function URL. **Anyone** or **URL (public)**, in amber, means anyone on the internet can run it.
- **Calls, error rate, average run time** over the last 30 days, and when it was **last called**, from CloudWatch.
- **Est. $/month**: calls and run time scaled to a month, plus provisioned concurrency and what its logs cost. [Cost](#cost) has the details.
- **Warnings** (listed under the table) cover runtimes past or near the end of support, functions anyone can invoke, provisioned concurrency that sits idle, errors that are frequent or rising, throttled calls, runs close to the timeout, and failed updates. `function_info()` shows every finding for one function.

`match="etl-*"` keeps the functions whose names match. `metrics=False` skips CloudWatch, and `details=False` skips the reads made for each function (its resource policy and provisioned concurrency), which makes accounts with hundreds of functions quicker.

### Across regions { #regions }

`functions(regions="all")` reads every region your account has turned on, with a **By region** table of each region's functions, cost and limits.
{ .gist }

??? more "Which regions it reads, and what the By region table shows"

    It reads every region your account has turned on (from EC2's `DescribeRegions`; without that permission, every region Lambda is offered in, skipping the ones that answer that they aren't turned on). `regions=["us-east-1", "eu-west-1"]` or `regions="us-east-1, eu-west-1"` reads just those. The table gains a Region column, and a **By region** table shows each region's functions, cost, code storage against its limit, and the most runs that happened at once against the region's concurrency limit.

## One function { #function-info }

`function_info()` explains one function in plain English, and every finding ends in the command that fixes it.
{ .gist }

The `update-function-configuration` call that moves it to a supported runtime or raises its timeout, the `put-retention-policy` call that stops its logs being kept forever, the `put-function-event-invoke-config` call that catches failed events. `days=7` reads a shorter window.

```python
ui.function_info("orders-etl")
```

![function_info("orders-etl"): cards for its runtime, memory, timeout, architecture, calls, error rate, average and longest run, estimated cost and triggers; findings about its runtime, rising errors, a run close to the timeout, logs costing more than its compute, failed S3 events being dropped, a log group kept forever and a password in an environment variable; and tables of what it runs, what triggers it, what happens to events that fail and what it can reach](images/lambda-function-info-light.webp#only-light){ width="984" height="1400" loading=lazy }
![function_info("orders-etl"): cards for its runtime, memory, timeout, architecture, calls, error rate, average and longest run, estimated cost and triggers; findings about its runtime, rising errors, a run close to the timeout, logs costing more than its compute, failed S3 events being dropped, a log group kept forever and a password in an environment variable; and tables of what it runs, what triggers it, what happens to events that fail and what it can reach](images/lambda-function-info-dark.webp#only-dark){ width="984" height="1400" loading=lazy }
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
    - **The estimated monthly cost**, by part, and the configuration as Lambda returns it, folded away, with environment values hidden: JSON in colour, where a click on an object or a list folds it, as in S3's preview of a `.json` file.


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

![errors("orders-etl"): cards for 66 failed calls of 2,226, error lines, causes, 3 timeouts and the last error; findings that runs timed out at the 60 s limit, that the role isn't allowed dynamodb:PutItem, and that KeyError: 'customer_id' happened 8 times; and tables of errors by cause and the newest error lines with their request IDs](images/lambda-errors-light.webp#only-light){ width="984" height="1472" loading=lazy }
![errors("orders-etl"): cards for 66 failed calls of 2,226, error lines, causes, 3 timeouts and the last error; findings that runs timed out at the 60 s limit, that the role isn't allowed dynamodb:PutItem, and that KeyError: 'customer_id' happened 8 times; and tables of errors by cause and the newest error lines with their request IDs](images/lambda-errors-dark.webp#only-dark){ width="984" height="1472" loading=lazy }
/// caption
`ui.errors("orders-etl")`: three different reasons to fail, each with what to do and the run to look at.
///

Each cause gets its own advice:

- **Timeouts**: raise the timeout, or find what's slow with `performance()`. At 900 seconds there's no more room: split the work, or run it somewhere without the limit.
- **Out of memory**: Lambda killed the run. Give it more memory.
- **Code that can't load** (`Runtime.ImportModuleError`): a package missing from the deployment package or a layer, or a handler setting that doesn't match a file. `code()` shows what's in the package.
- **Access denied**: the function's role isn't allowed something its code does, named with the action and the resource.
- **Throttled or unreachable services**, and otherwise the most common error, with an example.

Each error is matched to its run, so `logs("orders-etl", request_id="...")` shows that whole run, start to end.

### Logs { #logs }

`logs()` shows the newest lines a function logged, and `request_id=` one whole run.
{ .gist }

50 lines from the last hour by default, each run's REPORT line summed up: run time, memory used and cold start.

```python
ui.logs("orders-etl")
ui.logs("orders-etl", search="KeyError")                  # lines with that text (case-sensitive)
ui.logs("orders-etl", search='?ERROR ?WARN')              # or any CloudWatch Logs filter pattern
ui.logs("orders-etl", request_id="c0ffee00-1d2e-4f3a-9b8c-7d6e5f4a3b2c")   # one run, start to end
ui.logs("orders-etl", since="24h", n=200)
```

`request_id=` shows every line of one run, including what the code printed without the ID: everything its log stream recorded between the run's first and last line. When the window has no lines, the report says when the newest one was, and the call that reaches back that far.

### Run times, memory and cold starts { #performance }

`performance()` shows how long runs take against the timeout, the memory they use against what they have, and the size that would do.
{ .gist }

It reads the REPORT line Lambda logs at the end of every run: how long it ran and was billed for, the memory it used, and how long a cold start spent starting up.

```python
ui.performance("orders-etl")
```

![performance("orders-etl"): cards for 70 runs, the median and slowest run times against the 60 s timeout, memory used of 1,024 MB, cold starts and timeouts; findings that 3 runs timed out, that 256 MB would do instead of 1,024 MB, and that 11% of runs were cold starts; and tables of run time and memory percentiles, how long runs take and the slowest runs](images/lambda-performance-light.webp#only-light){ width="984" height="1498" loading=lazy }
![performance("orders-etl"): cards for 70 runs, the median and slowest run times against the 60 s timeout, memory used of 1,024 MB, cold starts and timeouts; findings that 3 runs timed out, that 256 MB would do instead of 1,024 MB, and that 11% of runs were cold starts; and tables of run time and memory percentiles, how long runs take and the slowest runs](images/lambda-performance-dark.webp#only-dark){ width="984" height="1498" loading=lazy }
/// caption
`ui.performance("orders-etl")`: runs use at most 182 MB of the 1,024 MB it has, and three ran into the 60 s timeout.
///

!!! tip "Worth knowing"

    **Try a smaller size, then check again.** Lambda bills memory × run time, and memory also sets the CPU (a full vCPU at 1,769 MB). When runs use far less memory than the function has, the report suggests a smaller size that still leaves 30% room, never less than a quarter of what it has in one step, and what that would save if the run time stays the same. A function held back by its CPU may run slower with less memory, which can cost more, so try it, and run `performance()` again a day later. When runs come close to the memory or the timeout, it says so and gives the command that raises them.

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

![diff("orders-etl"): cards comparing version 1 with $LATEST, 2 of 7 files changed, +3 −1 lines, the same libraries and 1 setting changed; a note that $LATEST makes a DynamoDB PutItem call the code didn't make before, at etl.py:22, so its role needs dynamodb:PutItem; the new ORDERS_TABLE environment variable; the new call with its permission and code; the files that changed; and etl.py's diff, with the added table = boto3.resource("dynamodb") line and the added table.put_item line that reads row["customer_id"]](images/lambda-diff-light.webp#only-light){ width="984" height="1164" loading=lazy }
![diff("orders-etl"): cards comparing version 1 with $LATEST, 2 of 7 files changed, +3 −1 lines, the same libraries and 1 setting changed; a note that $LATEST makes a DynamoDB PutItem call the code didn't make before, at etl.py:22, so its role needs dynamodb:PutItem; the new ORDERS_TABLE environment variable; the new call with its permission and code; the files that changed; and etl.py's diff, with the added table = boto3.resource("dynamodb") line and the added table.put_item line that reads row["customer_id"]](images/lambda-diff-dark.webp#only-dark){ width="984" height="1164" loading=lazy }
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

The analysis functions don't call AWS, so they also work on data you already have: `parse_function`, `parse_policy`, `parse_event_source_mapping`, `runtime_status`, `function_monthly_cost`, `provisioned_monthly_cost`, `classify_error`, `group_errors`, `parse_report`, `split_runs` (log lines grouped into runs), `line_level`, `percentile`, `suggest_memory`, `handler_file`, `read_package` (a .zip you already have), `package_file_name`, `page_file_name`, `secret_like`, `source_files` (a package's own source files), `read_sources` (`sources()`: the text of a .zip's own source files), `search_package` and `find_symbol_in` (`search_code()` and `find_symbol()` on a .zip's bytes), `map_code` (Python code read with `ast`, [above](#how-it-works)), `map_package` (the same from a .zip's bytes), `code_graph` (a CodeMap as boxes and arrows, with `.mermaid()`), `diff_packages` (`diff()` on two .zip files you already have), `diff_settings`, `version_before` (the version `diff()` compares with), `stream_version` (the version a log stream's name says ran), and the findings: `function_findings`, `account_findings`, `error_findings`, `performance_findings`, `package_findings`, `code_map_findings`, `diff_findings`.

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

Everything is read-only. Anything the notebook's role can't read shows up as a note instead of an error, so you can start with less and add what you need. This IAM policy covers every command:

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

`lambda:GetFunction` also returns the link `code()`, `how_it_works()`, `diff()` and `download()` download the package from, and the environment variables' values, which the reports never show. Leave it out and `function_info()`, `errors()`, `logs()`, `performance()`, `code()` and `how_it_works()` can't start; give it to roles that may read the code. `diff()` lists the versions with `lambda:ListVersionsByFunction`. `ec2:DescribeRegions` is only for `regions="all"`. The explorer window reads with the same permissions as the commands: a tab whose permission is missing says which one, and the other tabs still work.

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
| `functions(match=None, regions=None, days=30, metrics=True, details=True)` | Every function in the region (or regions, `"all"`): runtime and its support, memory, timeout, triggers, calls, error rate, run time, last called, estimated cost, warnings |
| `function_info(name, region=None, days=30)` | One function: what it runs, what triggers it and who may call it, failed asynchronous events, what it can reach, versions and aliases, its last 30 days, cost, findings |
| `errors(name, since="24h", region=None, limit=10000)` | Its errors grouped by cause, with what to do about each and the run to look at |
| `logs(name, since=None, search=None, request_id=None, n=50, region=None)` | The newest lines it logged, or one run from start to end |
| `performance(name, since="24h", region=None, limit=5000)` | Run times, memory used, cold starts, timeouts, the slowest runs, and the memory size that would do |
| `code(name, file=None, region=None, max_size="50MB")` | The files in its deployment package, findings about them, and the handler's source |
| `diff(name, old=None, new=None, region=None, max_size="50MB")` | What changed between two versions: its files line by line with the functions each change is in, the libraries packed with it, its settings and environment variable names |
| `how_it_works(name, region=None, max_size="50MB")` | How its Python code works, as a graph and as steps to click through from the handler, then what to fix |
| `how_it_works_page(name, path=None, region=None, max_size="50MB")` | The same report as a page of its own, saved in `lambda-downloads/` (or at `path=`), with a link that opens it in a tab |
| `download(name, path=None, region=None)` | Its deployment package saved on the notebook's disk, in `lambda-downloads/` (or at `path=`), checked against Lambda's SHA-256 |
| `help(command=None)` | This list, grouped by task; `help("name")` shows one command in full |

</div>
