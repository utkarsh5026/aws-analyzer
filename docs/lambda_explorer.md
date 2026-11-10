---
title: Lambda Explorer Guide
description: "How to click through your AWS Lambda functions in a SageMaker notebook with aws-analyzer's Lambda explorer window: every function and how it's doing, what's running now, logs run by run with search and a time range, errors by cause, run times, the code in the package and every setting."
---

<p class="eyebrow"><img class="aws-icon" src="images/aws/lambda.svg" alt="" width="32" height="32"> Lambda explorer <code>lambda_functions/explorer/</code></p>

# Click through your Lambda functions: what failed, what's running, and why

`explore()` turns a notebook cell into a window on your functions. Every function is listed with how it's doing, the ones that need attention first. Click one for its logs run by run, its errors by cause, its code and every setting. Nothing to type, nothing to remember.
{ .lede }

<ul class="pills">
  <li>One install, ipywidgets already on SageMaker</li>
  <li>Read-only: never invokes or changes a function</li>
  <li>Logs run by run, searched in CloudWatch</li>
  <li>What's running now, every few seconds</li>
</ul>

The examples use acme's functions: `orders-etl` loads each day's orders from S3 into the warehouse, and `report-api` serves a sales report over a function URL. The screenshots are the real window in JupyterLab on [moto](https://github.com/getmoto/moto). For reports you run from code (every region, runtimes, how the code works, what changed between versions), see the [Lambda guide](lambda_functions.md).
{ .muted }

![The Lambda explorer in a notebook, animated: it starts on the Functions tab, five functions with the ones that need attention first; the pointer clicks orders-etl, whose Overview shows its findings; it opens the Logs tab, picks Last 24 hours and opens a failed run, whose lines end in KeyError: 'customer_id'; it types AccessDenied in the search box and presses Enter, and the two runs that logged it come back with the word marked; then it opens the Errors tab, the failed calls grouped by cause, and the Code tab, the function's files](images/lambda-explorer-tour-light.webp#only-light){ width="984" height="809" loading=lazy }
![The Lambda explorer in a notebook, animated: it starts on the Functions tab, five functions with the ones that need attention first; the pointer clicks orders-etl, whose Overview shows its findings; it opens the Logs tab, picks Last 24 hours and opens a failed run, whose lines end in KeyError: 'customer_id'; it types AccessDenied in the search box and presses Enter, and the two runs that logged it come back with the word marked; then it opens the Errors tab, the failed calls grouped by cause, and the Code tab, the function's files](images/lambda-explorer-tour-dark.webp#only-dark){ width="984" height="809" loading=lazy }
/// caption
**`explore()`**: pick a function, read its failed run, search its logs, then see its errors by cause and its code.
///

## Watch it run { #video }

Seven steps in a notebook, from every function to what's running right now: each command typed and run, and the window it shows, with what to notice. It plays as you scroll to it; click it to pause, or pick a chapter to jump to a step.
{ .gist }

<div class="aa-tour" data-tour="lambda_explorer">
  <div class="tour-screen"></div>
  <div class="tour-controls"></div>
  <div class="tour-chapters"></div>
  <p class="tour-links"><a href="tour.html?service=lambda_explorer">Full screen</a> · <a href="videos/lambda_explorer-tour-light.mp4" data-tour-mp4 download>Download as MP4</a>, for Slack or a slide (light or dark, as this page is)</p>
</div>

## Open it { #open }

<div class="steps" markdown>

1. **Install it** in a notebook cell:

    ```bash
    %pip install aws-analyzer
    ```

2. **Open the window.** It uses the notebook's IAM role and region, so there's nothing to configure.

    ```python
    from aws_analyzer import LambdaView

    ui = LambdaView()
    x = ui.explore()                 # every function in the notebook's region
    ```

</div>

??? more "Straight to a function, a tab, a time range or another region"

    ```python
    from aws_analyzer.lambda_functions import explore

    explore()                                # every function in the notebook's region
    explore("orders-etl")                    # straight to one function: a name, an ARN or a console link
    explore("orders-etl", tab="logs")        # ...on its logs (or "errors", "performance", "code", "settings")
    explore(tab="live")                      # what's running now, looked at every few seconds
    explore(region="all")                    # every region your account has turned on
    explore(region="eu-west-1", profile="dev")   # another region or AWS profile
    explore(height=800)                      # 800px pages (else the browser's height)
    ui.explore("orders-etl")                 # from a LambdaView
    ```

??? more "The top of the window, and without ipywidgets"

    **The top of the window** is the region field, the function field and their cards. **⧉ Copy as code** beside the region field holds the one call that opens the window as it is now, such as `explore('orders-etl', tab='logs', since='3h', search='KeyError')`: click it, press <kbd>Ctrl</kbd>+<kbd>C</kbd>, and paste it in another cell or a message. Once a function is picked, the header shrinks to one line of its numbers over 30 days, so the tabs get the height. When the list loads, the window reads the function at the top of its problems in the background, so the one you're most likely to click opens at once; a function you open again within 5 minutes shows at once too, as the window read it then. **↻ Refresh** reads everything again. The region field switches to another region, or to every region at once. Click the function field for every function, with a search box that finds one by its name, description, runtime or region; Enter picks the first, and a function's ARN or console link works too, even one in another region. With no function picked, the cards sum up the region: its functions, their calls and error rate over 30 days (each card names its window), the estimated monthly cost, and how many need attention. With one picked, they're its own. The line at the bottom of the window says what's going on.

    The window needs `ipywidgets`, which SageMaker notebooks already have (elsewhere, `%pip install ipywidgets`, then reload the browser tab). Without it, or outside Jupyter, `explore()` shows the same as reports: [`functions()`](lambda_functions.md#functions), or for one function [`function_info()`](lambda_functions.md#function-info) or the report of the tab you asked for.

## Start here { #start }

Six things to try in your first minute. Nothing you click invokes, changes or deletes a function.
{ .gist }

<div class="quick" markdown>

What's wrong
{ .qgroup }

-   [Which functions need attention?](#explorer-functions) <span class="how">Read the Functions tab, problems first</span>
-   [Why did this run fail?](#explorer-logs) <span class="how">Click a function, then Logs, then a ✕ run</span>
-   [Which runs logged this error?](#explorer-logs) <span class="how">Type it in the Logs search box, press Enter</span>
-   [What are its errors, by cause?](#explorer-more) <span class="how">Open the Errors tab</span>

Right now, and since last time
{ .qgroup }

-   [What's running at this moment?](#explorer-live) <span class="how">Open the Live tab</span>
-   [What changed since I last looked?](#explorer-functions) <span class="how">Click the Changed chip</span>

</div>

## Every function { #explorer-functions }

The **Functions** tab puts the functions that need attention first. A dot says how each is doing: red when its calls fail often enough to act on, amber when it has a warning, green when all is well, hollow when nothing called it in 30 days.
{ .gist }

![The Functions tab of the explorer: the region field, the function field and cards for 5 functions, 192,496 calls in 30 days, a 0.3% error rate over 30 days, $93.59 a month and 4 that need attention; chips for the functions that need attention, have errors, run an old runtime, are public or weren't called; and the list, problems first, each with a bar a day for its last 14 days of calls: orders-etl on python3.9 (ended), its line saying in red that 224 of 35,130 calls failed (0.6%) in the last 30 days, report-api on nodejs20.x (ended), its line saying its runtime reached end of support, churn-scoring, feature-backfill not called in 30 days, and support-agent-actions with its description, each with its calls, error rate, average run time, monthly cost and when it was last called](images/lambda-explorer-light.webp#only-light){ width="984" height="863" loading=lazy }
![The Functions tab of the explorer: the region field, the function field and cards for 5 functions, 192,496 calls in 30 days, a 0.3% error rate over 30 days, $93.59 a month and 4 that need attention; chips for the functions that need attention, have errors, run an old runtime, are public or weren't called; and the list, problems first, each with a bar a day for its last 14 days of calls: orders-etl on python3.9 (ended), its line saying in red that 224 of 35,130 calls failed (0.6%) in the last 30 days, report-api on nodejs20.x (ended), its line saying its runtime reached end of support, churn-scoring, feature-backfill not called in 30 days, and support-agent-actions with its description, each with its calls, error rate, average run time, monthly cost and when it was last called](images/lambda-explorer-dark.webp#only-dark){ width="984" height="863" loading=lazy }
/// caption
**Functions**: every function with its last 14 days, what's wrong with it, its calls, error rate, run time and cost, the ones that need attention first.
///

Under a function that needs attention, its line says what's wrong ("3 of 120 calls failed (2.5%) in the last 30 days") in place of its description, and **Last 14 days** draws its calls a day, the failed part in red, so a function that started failing this week stands out from one that always fails a little. Click that column's header to put the ones failing most in the last 3 days first.

Search by name, description, runtime, region or trigger (`sqs`), click a chip for only the failing, old or public ones, and click **Logs** on a line to go straight to its logs.

**Recent** shows only the functions you've opened before, the ones you open most and most lately first, and the function field lists them first, each saying when you last opened it. The window remembers them between sessions, per AWS account: only their ARNs and when, in `~/.cache/aws-analyzer/history/places.sqlite`, which `clean_up()` keeps. `x.recent` lists them, `x.forget_recent("orders-etl")` forgets one and `x.forget_recent()` every one. `explore(history=False)` remembers nothing, and `AWS_ANALYZER_HISTORY=off` turns it off for every explorer.

![The Functions tab with the Recent chip picked: All 5, Recent 2, and only orders-etl and report-api in the list, the two functions opened before](images/lambda-explorer-recent-light.webp#only-light){ width="984" height="811" loading=lazy }
![The Functions tab with the Recent chip picked: All 5, Recent 2, and only orders-etl and report-api in the list, the two functions opened before](images/lambda-explorer-recent-dark.webp#only-dark){ width="984" height="811" loading=lazy }
/// caption
**Recent**: the functions you've opened, in this session or an earlier one.
///

**Changed** shows the functions you've opened before whose code or settings changed since, or whose errors went up, each tagged *changed* in the list. Open one, and its Overview says what: when the new code was deployed, which settings moved, and its failed calls a day against the days before your last look. Its Errors tab names the errors it hadn't seen when you last opened it.

![The Functions tab with the Changed chip picked: only orders-etl, tagged changed beside its runtime](images/lambda-explorer-changed-light.webp#only-light){ width="984" height="863" loading=lazy }
![The Functions tab with the Changed chip picked: only orders-etl, tagged changed beside its runtime](images/lambda-explorer-changed-dark.webp#only-dark){ width="984" height="863" loading=lazy }
/// caption
**Changed**: the functions that are different from when you last looked.
///

![orders-etl's Overview, its findings led by: Errors went up since your last look 3d ago, 96 failed calls since 28 Oct (UTC days), about 32 a day, against about 4.9 a day before; further down, the code changed since your last look 3d ago and its settings changed, memory 512 → 1,024 MB](images/lambda-explorer-changes-light.webp#only-light){ width="984" height="811" loading=lazy }
![orders-etl's Overview, its findings led by: Errors went up since your last look 3d ago, 96 failed calls since 28 Oct (UTC days), about 32 a day, against about 4.9 a day before; further down, the code changed since your last look 3d ago and its settings changed, memory 512 → 1,024 MB](images/lambda-explorer-changes-dark.webp#only-dark){ width="984" height="811" loading=lazy }
/// caption
What changed in orders-etl since three days ago, among its findings.
///

??? more "How it decides what changed"

    When you open a function, the window notes its code's SHA-256, when it was deployed, its runtime, handler, memory and timeout, and, once you open its Errors tab, a hash of each error's cause (never the message). Next time, it compares. Looks within an hour of each other are one visit. Failed calls come from CloudWatch, which counts whole UTC days, so they're counted from the day after your last look, and not until a whole day has passed; they've gone up when there are at least 3 and twice as many a day as before.

## What's running now { #explorer-live }

The **Live** tab shows what's running right now: every function that ran in the last 10 minutes, the ones with runs in progress first, looked at again every few seconds.
{ .gist }

Each line says how many runs are going and what the newest one is doing, its calls a minute (the failed part in red), and when it last logged. A run turns amber as it nears its timeout. Click a function to follow it: its runs, newest first, the ones still going open at the top with their lines added as they come. **‹ All functions** goes back, and **Its Logs tab ›** opens the function's Logs tab for a longer time range or a search.

```python
explore(tab="live")                     # what's running now
explore("orders-etl", tab="live")       # ...following one function
x.live("orders-etl")                    # the same, in a window that's open
```

![The Live tab of the explorer: the Live button on, and a line saying 3 of 5 functions ran, 3 running now and 1,166 calls, 3 failed in the last 10 minutes; then the functions that ran, running ones first: report-api, its newest run 2.6 s in on GET /reports/weekly?region=APAC, 2 running, its calls a minute as bars, 1,143 calls and 2 failed, seen just now; orders-etl, 47 s into a run that wrote 14,000 of 18,400 orders, in amber as it nears its timeout, 1 running, 10 calls and 1 failed; support-agent-actions idle, 13 calls, 2 min ago; and under the list, the functions not called in the last 10 minutes](images/lambda-explorer-live-light.webp#only-light){ width="984" height="828" loading=lazy }
![The Live tab of the explorer: the Live button on, and a line saying 3 of 5 functions ran, 3 running now and 1,166 calls, 3 failed in the last 10 minutes; then the functions that ran, running ones first: report-api, its newest run 2.6 s in on GET /reports/weekly?region=APAC, 2 running, its calls a minute as bars, 1,143 calls and 2 failed, seen just now; orders-etl, 47 s into a run that wrote 14,000 of 18,400 orders, in amber as it nears its timeout, 1 running, 10 calls and 1 failed; support-agent-actions idle, 13 calls, 2 min ago; and under the list, the functions not called in the last 10 minutes](images/lambda-explorer-live-dark.webp#only-dark){ width="984" height="828" loading=lazy }
/// caption
`explore(tab="live")`: three functions ran in the last 10 minutes, two of them running now.
///

![orders-etl followed on the Live tab: a back button, a line saying 1 running now and 10 calls, 1 failed in the last 10 minutes, and a button to its Logs tab; its run going is open at the top, 48 s of its 60 s timeout with an amber bar and a cold start, a warning that Lambda stops it at 60 s with the command that gives it longer, its lines so far (reading the batch, parsed 18,400 orders, a WARN about ProvisionedThroughputExceededException, wrote 14,000 of 18,400 orders), Still running: no REPORT line yet, and waiting for more lines; under it the runs that ended, a good one, a failed KeyError and another good one](images/lambda-explorer-live-follow-light.webp#only-light){ width="984" height="811" loading=lazy }
![orders-etl followed on the Live tab: a back button, a line saying 1 running now and 10 calls, 1 failed in the last 10 minutes, and a button to its Logs tab; its run going is open at the top, 48 s of its 60 s timeout with an amber bar and a cold start, a warning that Lambda stops it at 60 s with the command that gives it longer, its lines so far (reading the batch, parsed 18,400 orders, a WARN about ProvisionedThroughputExceededException, wrote 14,000 of 18,400 orders), Still running: no REPORT line yet, and waiting for more lines; under it the runs that ended, a good one, a failed KeyError and another good one](images/lambda-explorer-live-follow-dark.webp#only-dark){ width="984" height="811" loading=lazy }
/// caption
Following orders-etl: its run going, near its timeout, its lines as they come.
///

??? more "How it knows what's running"

    Lambda has no call that lists the runs in progress, so the tab puts two reads together. Once a minute, CloudWatch's numbers (GetMetricData: calls, failed calls and runs at once, a value a minute) say which functions ran; they come a minute or two late. Every 5 seconds, the newest lines of the five busiest (FilterLogEvents) say which runs are going: Lambda writes a `START` line when a run begins and a `REPORT` line when it ends, so a run with a `START` and no `REPORT` yet, younger than its timeout, is running. A function you follow has its lines read whether CloudWatch counted it or not.

    It watches while the tab is open, for 15 minutes at most; click **Live** to stop or start again. The status line says what the numbers cost: three metrics a function a minute, at $0.01 per 1,000 (a hundred functions for 15 minutes is about $0.05). Nothing is invoked or changed.

## Logs, run by run { #explorer-logs }

The **Logs** tab is CloudWatch grouped the way you think about it: one line per run, ✓ or ✕, newest first. Click a run to read every line it logged.
{ .gist }

Each run shows how long it ran against the timeout, the memory it used, a cold start, and what made it fail. Times are in UTC, or in the zone [`set_timezone()`](index.md#timezone) picked.

![The Logs tab of orders-etl over the last 24 hours: the time range, the search box, Live and ↻; chips for all 70 runs, 13 failed, 3 timed out, 8 cold starts and the slowest 10%; a timeline of the 24 hours, a bar for each slice, red where runs failed; a line saying 70 runs, 13 failed (18.6%), a median of 4.87 s, the slowest 60.0 s of 60 s, memory up to 182 of 1,024 MB and 8 cold starts; then the runs, newest first, each with its time, a ✓ or ✕, a bar of its run time against the timeout, its memory and its first line; a failed run is open, showing reading s3://acme-uploads/orders/2026-10-06/batch-002.json at +0.050s, then KeyError: 'customer_id' and its traceback in red, then Ran 1.73 s, billed 1.73 s, used 151 of 1,024 MB and its request ID, and a button that opens etl.py, line 22, in the Code tab; under it a run that timed out after 60.0 s with a full red bar](images/lambda-explorer-logs-light.webp#only-light){ width="984" height="811" loading=lazy }
![The Logs tab of orders-etl over the last 24 hours: the time range, the search box, Live and ↻; chips for all 70 runs, 13 failed, 3 timed out, 8 cold starts and the slowest 10%; a timeline of the 24 hours, a bar for each slice, red where runs failed; a line saying 70 runs, 13 failed (18.6%), a median of 4.87 s, the slowest 60.0 s of 60 s, memory up to 182 of 1,024 MB and 8 cold starts; then the runs, newest first, each with its time, a ✓ or ✕, a bar of its run time against the timeout, its memory and its first line; a failed run is open, showing reading s3://acme-uploads/orders/2026-10-06/batch-002.json at +0.050s, then KeyError: 'customer_id' and its traceback in red, then Ran 1.73 s, billed 1.73 s, used 151 of 1,024 MB and its request ID, and a button that opens etl.py, line 22, in the Code tab; under it a run that timed out after 60.0 s with a full red bar](images/lambda-explorer-logs-dark.webp#only-dark){ width="984" height="811" loading=lazy }
/// caption
**Logs**: orders-etl's last 24 hours, run by run, with a failed run open: its traceback, and its REPORT line summed up.
///

??? more "The time range, searching CloudWatch, the chips and Live"

    - **The time range**, from the last 15 minutes to the last 30 days (the last hour at first), or **Custom range…**: pick **From** and **To** on a calendar (in the reports' time zone; leave To empty for up to now) and click **Show runs**, to read last Tuesday's 09:00 to 17:30. Errors and Performance start at the last 24 hours; once you pick a range in any of the three tabs, the others follow it, a custom range too. While a range is read, a bar fills as it goes, with how far back it has read and the lines found so far. The newest 3,000 lines are read; when the range holds more, the line over the runs says these are the newest runs, and **Older runs** under the list reads further back. A range with nothing in it says when the function last logged, and a button picks a range that reaches it.
    - **The time range** also has **Today**: from midnight, in the reports' time zone.
    - **The search box** finds runs as you type: an order ID, `KeyError`, a request ID, in any case, among the runs read. **Enter** asks CloudWatch to search the whole time range, and brings back each run with a matching line whole, the matching lines marked. A CloudWatch Logs filter pattern (`?ERROR ?WARN`, `{ $.level = "ERROR" }`) works too, with Enter. ✕ shows every run again.
    - **Join words with AND, OR and NOT**, in capitals: `KeyError OR Timeout`, `PutItem AND NOT retrying` (or `PutItem -retrying`), `(AccessDenied OR Throttling) AND order-42`. A run matches when its lines hold them, on one line or several, so an order ID logged on one line and the error on the next still find the run. The words show as chips under the box: click the **OR** between two of them to make it **AND** (and back), click a word to leave out the runs that have it, and its ✕ takes it away; each click rewrites the box. Lowercase *and* and *or* are words, so a pasted message such as `No such file or directory` is still one phrase, and quotes keep a phrase together: `"not authorized" OR Throttling`. Enter asks CloudWatch for the lines with any of the words, reads each of their runs whole, then keeps the runs that match the whole search; it keeps the newest 500 lines that matched, and the line at the bottom says when a search reached that.
    - **The same box asks for runs by their fields**: type `duration > 15s`, `duration > 15s and cold`, `memory_share > 80%`, `error ~ KeyError` or `started > 14:00`, and the list keeps the runs read that match, as [`runs(where=...)`](lambda_functions.md#runs) does. Fields your JSON logs carry work too, and so does the event when the function logs it (`size > 30MB`). The line under the tabs says how many match, or the field you likely meant when no run has the one typed. Put words in quotes to search for them instead. With an agent (`explore(..., assistant=AgentView(...))`), **✦** beside the box takes plain English instead: "slow runs that started cold", then Enter, and the agent writes the query for you to use ([the agent's guide](agent.md#ask-drawer)).
    - **The sort menu** beside the chips lists the runs newest first, oldest first, longest first or most memory first.
    - **A run that logged its event** shows it at the top of its lines, as JSON to fold. Lambda never logs the event itself, so it's there only when the code does (Powertools: `@logger.inject_lambda_context(log_event=True)`).
    - **The chips** show only the runs that failed, timed out, logged an error (and may have handled it) or were cold starts.
    - **A dashed line across the runs** marks when the function was last changed (its code or settings): the runs under it ran it as it was before.
    - **The timeline** over the runs is a bar for each thirtieth of the time read, as tall as the runs that started in it and red where some failed. Click a bar for only those runs, and again for every run.
    - **Slowest 10%** shows the runs that took the longest (with 10 runs or more read).
    - **An open run** has its request ID and a CloudWatch Logs Insights query for it to copy (click, then <kbd>Ctrl</kbd>+<kbd>C</kbd>), and **↗ Open in the AWS console**, its log stream filtered to that run.
    - **A failed run's traceback** (Python's `File "/var/task/etl.py", line 22`, or a Node.js stack) gets a button under its lines that opens that file in the Code tab, the lines around the failing one at the top and that line shaded in the whole file below. Frames in libraries the package bundles are skipped for the function's own code.
    - **Live** looks for new lines every 5 seconds and puts the new runs at the top, to watch a function while you test it. It stops when you leave the tab, and after 15 minutes.

    The line over the runs sums up the ones shown: how many, how many failed, the median and slowest run time against the timeout, the most memory used, and the cold starts.

![Searching orders-etl's last 24 hours for AccessDenied: 2 runs, both failed, both open; each shows its lines with AccessDenied highlighted in the ClientError that says the role acme-lambda-role isn't authorized to perform dynamodb:PutItem on the orders table, the line marked with a blue bar at its left; the first run was a cold start and starts with its INIT_START line](images/lambda-explorer-search-light.webp#only-light){ width="984" height="811" loading=lazy }
![Searching orders-etl's last 24 hours for AccessDenied: 2 runs, both failed, both open; each shows its lines with AccessDenied highlighted in the ClientError that says the role acme-lambda-role isn't authorized to perform dynamodb:PutItem on the orders table, the line marked with a blue bar at its left; the first run was a cold start and starts with its INIT_START line](images/lambda-explorer-search-dark.webp#only-dark){ width="984" height="811" loading=lazy }
/// caption
**AccessDenied**, searched in CloudWatch: two runs in the last 24 hours, each brought back whole, the matching line marked.
///

Logs in JSON (a function whose log format is JSON) show each line's message, with its other fields a click away. An error the runtime logged in JSON reads like a traceback: its type, its message, then its stack.

![report-api's Logs tab over the last 3 hours: 45 runs, 3 failed; a good run open, its lines GET /reports/weekly?region=AMER and 200 in 99 ms marked INFO, each with {…} fields to open; a failed run open, its ERROR line reading TypeError: Cannot read properties of undefined (reading 'total') then the stack, and its summary saying status error (TypeError)](images/lambda-explorer-json-light.webp#only-light){ width="984" height="811" loading=lazy }
![report-api's Logs tab over the last 3 hours: 45 runs, 3 failed; a good run open, its lines GET /reports/weekly?region=AMER and 200 in 99 ms marked INFO, each with {…} fields to open; a failed run open, its ERROR line reading TypeError: Cannot read properties of undefined (reading 'total') then the stack, and its summary saying status error (TypeError)](images/lambda-explorer-json-dark.webp#only-dark){ width="984" height="811" loading=lazy }
/// caption
report-api logs JSON: each line's message, its level, and its other fields a click away.
///

## How it's doing { #explorer-overview }

The **Overview** tab has what to fix first, then **how it's wired**: what calls the function and where its results, failed events and logs go.
{ .gist }

A failure destination it doesn't have shows in amber. Then its calls a day over 30 days, the failed part in red, its run time a day against the timeout, and what it costs. A dashed line on both charts marks the day it was last changed (its code or settings), and when its calls began to fail after that, a warning says so, with how many failed before and since.

![orders-etl's Overview below its findings: How it's wired, with its SQS queue orders-queue (batches of up to 10) and S3 bucket acme-uploads on the left, the function in the middle (python3.9, 1,024 MB, 60 s timeout, x86_64), and on the right On failure: nowhere: dropped in amber, its log group kept forever with 24.5 GB stored, and the role it may use; then a column per day of the last 30 days for its calls, a thin red top where calls failed, and its run time a day, the average under the longest, against a dashed line at its 60 s timeout; on both, a dashed purple line through Oct 27, labelled changed Oct 27, after which more calls fail](images/lambda-explorer-overview-light.webp#only-light){ width="984" height="811" loading=lazy }
![orders-etl's Overview below its findings: How it's wired, with its SQS queue orders-queue (batches of up to 10) and S3 bucket acme-uploads on the left, the function in the middle (python3.9, 1,024 MB, 60 s timeout, x86_64), and on the right On failure: nowhere: dropped in amber, its log group kept forever with 24.5 GB stored, and the role it may use; then a column per day of the last 30 days for its calls, a thin red top where calls failed, and its run time a day, the average under the longest, against a dashed line at its 60 s timeout; on both, a dashed purple line through Oct 27, labelled changed Oct 27, after which more calls fail](images/lambda-explorer-overview-dark.webp#only-dark){ width="984" height="811" loading=lazy }
/// caption
**Overview**, under the findings: failed S3 events go nowhere, its logs are kept forever, and its longest runs come close to the 60 s timeout.
///

## Errors, run times, code and settings { #explorer-more }

The other tabs are the [Lambda guide](lambda_functions.md)'s reports, a click away: **Errors** is [`errors()`](lambda_functions.md#errors), **Performance** is [`performance()`](lambda_functions.md#performance), **Code** is the package as a tree you click through, and **Settings** is every setting in plain English.
{ .gist }

??? more "What each of those tabs does"

    - **Errors** is [`errors()`](lambda_functions.md#errors): the errors in its logs grouped by cause, each a line to click for the runs it failed in (the Logs tab, searched for it over the same time range), then what to do about each, then the newest failed runs. Click one to read every line of it in the Logs tab. **Code ›** on a cause opens, in the Code tab, the line of the function's own code its traceback names.
    - **Performance** is [`performance()`](lambda_functions.md#performance): run times against the timeout, the memory used against what it has and the size that would do, cold starts, and the slowest runs, each a click from its lines.
    - **Code** downloads the deployment package once and shows its files as a tree of folders, folders first, with the handler's folder open (a folder that holds only a folder shares its line, as `botocore/data/` does). Click a folder to open or fold it, and a file to see its source, with line numbers, and Python and JSON in colour; **Copy** beside its name selects all of it, so <kbd>Ctrl</kbd>+<kbd>C</kbd> (<kbd>⌘</kbd>+<kbd>C</kbd> on a Mac) copies the file. Typing in **Find a file** shows only the files that match, in their folders. **Hide files** folds the tree away so the source has the whole width, and **Show files** brings it back (it stays hidden for the next function you open). For a Python function, **How it works** shows [how its code works](lambda_functions.md#how-it-works) in place of the source: the graph of what calls what, the steps of a call from the handler on, its AWS calls, the environment variables and event fields it reads, and what to fix; click it again, or a file in the tree, for the source. While it's shown, **↗ Open in a tab** saves it as a page of its own, like [`how_it_works_page()`](lambda_functions.md#how-it-works-page), with the link that opens it under the buttons. **Download .zip** saves the package, the .zip as Lambda stores it, in `lambda-downloads/` next to the notebook, like [`download()`](lambda_functions.md#download). A secrets file (`.env`, keys) is listed but its text isn't shown, and a package over 50 MB is downloaded only when you click **Download it anyway**.
    - **Settings** has every setting in plain English, then the configuration as Lambda returns it, folded (open it for the JSON in colour, where a click on an object or a list folds it, and hovering over a line shows its path in `raw`), and the AWS CLI commands that read it. Environment variable values never appear.

    The Errors and Performance tabs read the last 24 hours at first. Once you pick a time range in the Logs, Errors or Performance tab, all three read that range.

![orders-etl's Errors tab over the last 24 hours: cards for 35 failed calls, 1,177 calls, a 3.0% error rate, 13 error lines, 3 causes and 3 timeouts; the errors by cause as lines to click, KeyError 8 times, a timeout 3 times and ClientError twice, each with when it last happened and its share; warnings that 3 runs timed out at the 60 s limit, that its role acme-lambda-role isn't allowed dynamodb:PutItem, and that KeyError: 'customer_id' happened 8 times, each naming the run to read in the Logs tab, and the newest failed runs](images/lambda-explorer-errors-light.webp#only-light){ width="984" height="811" loading=lazy }
![orders-etl's Errors tab over the last 24 hours: cards for 35 failed calls, 1,177 calls, a 3.0% error rate, 13 error lines, 3 causes and 3 timeouts; the errors by cause as lines to click, KeyError 8 times, a timeout 3 times and ClientError twice, each with when it last happened and its share; warnings that 3 runs timed out at the 60 s limit, that its role acme-lambda-role isn't allowed dynamodb:PutItem, and that KeyError: 'customer_id' happened 8 times, each naming the run to read in the Logs tab, and the newest failed runs](images/lambda-explorer-errors-dark.webp#only-dark){ width="984" height="811" loading=lazy }
/// caption
**Errors**: three causes, each a click from the runs it failed in, then what to do about each.
///

![orders-etl's Code tab opened from the KeyError on its Errors tab: etl.py picked in the tree, then, in red, Line 22, in handler(): where the run failed, over the lines around it with line 22 shaded, then the whole file with the same line shaded](images/lambda-explorer-failed-line-light.webp#only-light){ width="984" height="811" loading=lazy }
![orders-etl's Code tab opened from the KeyError on its Errors tab: etl.py picked in the tree, then, in red, Line 22, in handler(): where the run failed, over the lines around it with line 22 shaded, then the whole file with the same line shaded](images/lambda-explorer-failed-line-dark.webp#only-dark){ width="984" height="811" loading=lazy }
/// caption
**Code ›** on a cause, or the button under a failed run: the line its traceback names, in the package's own code.
///

![orders-etl's Code tab: cards for a 17.2 KB package, 16.0 MB unzipped, 7 files, no layers and the handler file etl.py; a warning that the package holds .env and a note that it carries its own boto3 and botocore; a Hide files button, then a Download .zip button that saves it in lambda-downloads/; on the left its files as a tree, the folders boto3/, botocore/data/ (open, holding endpoints.json) and psycopg2/ with their sizes, then .env marked KEY, db.py, etl.py marked HANDLER and requirements.txt; on the right etl.py with line numbers, its keywords, strings and calls in colour, and a Copy button](images/lambda-explorer-code-light.webp#only-light){ width="984" height="811" loading=lazy }
![orders-etl's Code tab: cards for a 17.2 KB package, 16.0 MB unzipped, 7 files, no layers and the handler file etl.py; a warning that the package holds .env and a note that it carries its own boto3 and botocore; a Hide files button, then a Download .zip button that saves it in lambda-downloads/; on the left its files as a tree, the folders boto3/, botocore/data/ (open, holding endpoints.json) and psycopg2/ with their sizes, then .env marked KEY, db.py, etl.py marked HANDLER and requirements.txt; on the right etl.py with line numbers, its keywords, strings and calls in colour, and a Copy button](images/lambda-explorer-code-dark.webp#only-dark){ width="984" height="811" loading=lazy }
/// caption
**Code**: the package as a tree of folders, the handler's source to copy, and a `.env` file that shouldn't be in the package.
///

<p class="part"><strong>That's the guide.</strong> Everything below is reference for when you need it: driving the window from code, and every command.</p>

## From code { #from-code }

The window is an object you can drive: what a click does, a command does too, and what's shown is data you can read.
{ .gist }

Everything is read in the background: a click shows what's known at once and fills in the rest as it arrives, and the window keeps answering meanwhile. `explore()` returns the window: `x.overview`, `x.function`, `x.detail`, `x.logs_page`, `x.package`, `x.code_map` (what **How it works** read) and `x.saved` (what **Download .zip** saved) hold the data behind what's shown, and `x.focus` is what you're looking at (the function, the tab, its time range and the run or file opened; `x.focus.chips()` reads it as a row of chips); `x.open("report-api")`, `x.logs(search="KeyError", since="24h")` (or `x.logs(since="2026-10-27 09:00", until="2026-10-27 17:30")` for a custom range), `x.run("c0ffee00-1d2e-4f3a-9b8c-7d6e5f4a3b2c")` and `x.refresh()` do what the clicks do; and `x.ui` is a `LambdaView` for reports in other cells. With `explore(..., assistant=AgentView(...))` (or `x.attach(agent)`), **✦ Ask** at the end of the tab bar opens the agent's chat beside the tabs, asking about the function, run or file you have open ([the agent's guide](agent.md#ask-drawer)).

## Command reference { #reference }

<div class="ref" markdown>

| Command | What it does |
|---|---|
| `explore(name=None, tab=None, since=None, until=None, search=None, region=None, profile=None, height=None, history=True, assistant=None)` | Opens the window and returns it; `ui.explore(...)` on a `LambdaView` takes the same |
| `x.open(name, tab=None)` | Opens a function (a name, `"name:alias"`, an ARN or a console link), on its Overview or `tab=` (`"logs"`, `"errors"`, `"performance"`, `"code"`, `"settings"`) |
| `x.logs(name=None, search=None, since=None, until=None)` | Its Logs tab, for a time range (`"15m"`, `"24h"`, `"7d"`, a date) and a search, as typing it and pressing Enter does |
| `x.run(request_id)` | One run, every line of it, by its request ID |
| `x.live(name=None)` | The Live tab: what ran in the last 10 minutes, or one function followed |
| `x.recent` / `x.forget_recent(name="")` | The functions you've opened before; forget one, or all of them |
| `x.refresh()` | Reads everything again (the ↻ button) |

</div>
