---
title: API Gateway Analyzer Guide
description: "How to see which API Gateway calls fail and why, which routes anyone can call, and read the Lambda run behind a failed call, from a SageMaker notebook with aws-analyzer's apigateway module."
---

<p class="eyebrow"><img class="aws-icon" src="images/aws/apigateway.svg" alt="" width="32" height="32"> Amazon API Gateway <code>apigateway</code></p>

# See which API calls fail and why, down to the Lambda run behind them

Find the APIs that fail or run out of time, see who can call each route, and follow a failed call from its access log line to the log lines of the Lambda run that served it, without clicking through the console.
{ .lede }

<ul class="pills">
  <li>REST, HTTP and WebSocket APIs</li>
  <li>Read-only: never calls your API</li>
  <li>The Lambda run's own lines, by request ID</li>
  <li>Plain text outside Jupyter</li>
</ul>

The examples use acme's `orders-api`, a REST API whose `POST /orders` route calls the `orders-etl` Lambda function. Some of its calls take longer than API Gateway's 29 seconds and come back as a `504`, while `orders-etl` is still working.
{ .muted }

## Set up in SageMaker { #setup }

<div class="steps" markdown>

1. **Install it** in a notebook cell:

    ```bash
    %pip install aws-analyzer
    ```

2. **Make the view.** It uses the notebook's IAM role and region, so there's nothing to configure.

    ```python
    from aws_analyzer import APIGatewayView

    apigw = APIGatewayView()
    apigw.help()             # every command, grouped by task; apigw.help("errors") shows one in full
    ```

</div>

??? more "Another region or profile, plain text"

    APIs are regional, so if `apis()` comes back empty, check the region first. Only boto3 is required.

    ```python
    from aws_analyzer import APIGatewayAnalyzer, APIGatewayView

    apigw = APIGatewayView(APIGatewayAnalyzer(region="eu-west-1", profile="dev"))
    apigw = APIGatewayView(mode="text")    # plain text, also what you get outside Jupyter
    ```

## Start here { #tour }

Four questions people bring to an API, and the one line that answers each. None of them call your API or change it.
{ .gist }

<div class="quick" markdown>

-   **Which APIs fail, and what do they cost?**

    ```python
    apigw.apis()
    ```

    [Your APIs](#apis)

-   **Which calls fail, and why?**

    ```python
    apigw.errors("orders-api")
    ```

    [What's failing](#errors)

-   **Who can call each route?**

    ```python
    apigw.routes("partner-api")
    ```

    [Routes](#routes)

-   **Where does the time go?**

    ```python
    apigw.explore("orders-api", tab="calls")
    ```

    [Explore by clicking](#explorer)

</div>

An API is named by its name, its ID, `"name/stage"`, its invoke URL or a link copied from the console.

??? more "How to read a report"

    Every report puts the answer first: cards with the numbers that matter, then the findings, warnings first, each a bold headline with why it matters and what to do underneath. The tables of detail come after, and **Next** at the bottom lists two or three commands with the arguments filled in. One click on a command selects all of it, ready to copy.

## Your APIs { #apis }

`apis()` lists every REST, HTTP and WebSocket API in the region with its requests, its share of 4xx and 5xx, how long its slowest calls take and its estimated monthly cost, the ones failing most first.
{ .gist }

```python
apigw.apis()              # or apis(days=30)
```

![apis(): six APIs with their stages, requests, 4xx and 5xx share, p95, auth and cost, and findings about orders-api's server errors, partner-api's open routes and an unused legacy-v1](images/apigw-apis-light.webp#only-light){ width="984" height="1122" loading=lazy }
![apis(): six APIs with their stages, requests, 4xx and 5xx share, p95, auth and cost, and findings about orders-api's server errors, partner-api's open routes and an unused legacy-v1](images/apigw-apis-dark.webp#only-dark){ width="984" height="1122" loading=lazy }
/// caption
`apigw.apis()`: orders-api fails most and runs close to the time limit, partner-api has two routes anyone can call, and legacy-v1 had no calls.
///

The numbers are CloudWatch's (`AWS/ApiGateway`), in whole UTC days. **Auth** says who can call an API's routes: an authorizer, IAM, an API key, or `none ×2` when two routes answer anyone. A health check that answers with a mock doesn't count, since it reaches nothing.

!!! tip "Worth knowing"

    API Gateway gives up on a request after **29 seconds** (30 for an HTTP API) and answers `504`, even when the Lambda function behind it is still working and will finish the job. A route whose slowest calls come close to that is a warning, and `performance()` shows which.

## One API { #api-info }

`api_info()` shows one API's numbers on one stage, its stages and its routes, with where each route goes and who can call it.
{ .gist }

```python
apigw.api_info("orders-api")              # its main stage: prod, $default, or the one deployed last
apigw.api_info("orders-api/dev")          # another stage
```

![api_info('orders-api'): cards with requests, 5xx, p95, routes, stages and cost, warnings that POST /orders runs out of time and fails 9.6% of its calls, then the stages and routes](images/apigw-api-info-light.webp#only-light){ width="984" height="1171" loading=lazy }
![api_info('orders-api'): cards with requests, 5xx, p95, routes, stages and cost, warnings that POST /orders runs out of time and fails 9.6% of its calls, then the stages and routes](images/apigw-api-info-dark.webp#only-dark){ width="984" height="1171" loading=lazy }
/// caption
`apigw.api_info("orders-api")`: `POST /orders` runs out of time, while `orders-etl` may still be working.
///

A route's own numbers need the stage's **detailed metrics**. Without them the routes table has none, and `performance()` works them out from the access log instead.

## What's failing { #errors }

`errors()` groups the calls that failed by status, error and route, the most common first, and says what each status means and what to read next.
{ .gist }

```python
apigw.errors("orders-api")                 # the last 24 hours
apigw.errors("orders-api", since="7d")
apigw.calls("orders-api", status="5xx")    # every server error, call by call
apigw.calls("orders-api", search="c1f08a2e")   # one request ID
```

A `504` is a timeout, a `502` an answer from Lambda that API Gateway can't use (it needs `statusCode` and a body that's a string, and no exception), a `429` throttling, and a `403 Missing Authentication Token` a path or method that doesn't exist. For a call that reached Lambda, the group shows `LambdaView().logs('orders-etl', request_id='…')`, the run that served it.

!!! tip "Worth knowing"

    CloudWatch only counts calls. Which call failed, and why, is only in the stage's **access log**, one line per call, and a stage keeps one only if it's turned on. Without it, `errors()` shows the counts and the `aws apigateway update-stage` command that turns it on, in a format that logs everything these reports read.

??? more "How an access log line is read"

    The owner of a stage chooses its access log's format, made of `$context` variables: JSON like `{"requestId":"$context.requestId","status":"$context.status"}`, or text like the Common Log Format. The format is in the stage's settings, so each line is read by its own format: a JSON key is known by the variable it holds, whatever its name, and a text format becomes a pattern with a place for each variable. To say where a call's time went and which Lambda run served it, the format needs `$context.integration.latency` and `$context.integration.requestId`; a finding says when they're missing, and shows a format that has them.

## Routes { #routes }

`routes()` lists every route with who can call it, where it goes and how long API Gateway waits, with the curl command that calls it.
{ .gist }

```python
apigw.routes("partner-api")
apigw.stages("orders-api")       # each stage's throttling, logs, tracing, cache and WAF
```

A route anyone can call (no authorizer, no IAM, no API key) is a warning, unless it's meant to be public. The curl command leaves credentials as `$TOKEN`, `$API_KEY` or your AWS keys, and a WebSocket API gets `wscat` instead. The command is there to copy: nothing here sends it.

## How long calls take { #performance }

`performance()` shows each route's average, 95th percentile and longest call, how much of the time was the backend's, and where the slowest call's time went.
{ .gist }

```python
apigw.performance("orders-api")
```

The routes' numbers come from the access log when the stage has one, else from detailed metrics. A call's time splits into the authorizer, the integration (the backend) and API Gateway itself.

## Explore by clicking { #explorer }

`explore()` opens a window with every API; click one to read its routes as a tree and its calls one by one, each with where its time went and the Lambda run behind it.
{ .gist }

```python
apigw.explore()                               # the API failing most, or the first
apigw.explore("orders-api", tab="calls")      # straight to its calls
apigw.explore("orders-api/dev", since="7d")   # another stage, the last 7 days
```

![The API Gateway explorer: findings, then a search box, chips and six APIs with their requests, 5xx share, p95, cost and auth under column headers that sort](images/apigw-explorer-light.webp#only-light){ width="984" height="866" loading=lazy }
![The API Gateway explorer: findings, then a search box, chips and six APIs with their requests, 5xx share, p95, cost and auth under column headers that sort](images/apigw-explorer-dark.webp#only-dark){ width="984" height="866" loading=lazy }
/// caption
`apigw.explore()`: every API, the failing ones first; search them, pick a chip (Failing, Open routes, Unused, ...) or click a column to sort.
///

The **Routes** tab is a tree of the API's paths. Click a route to see where a request goes, from the caller through the authorizer to the integration, with the step that fails marked, its settings and the command that calls it.

![The Routes tab: orders-api's paths as a tree on the left, and POST /orders open on the right, its path from the caller through Cognito to orders-etl, the integration marked red with p95 29s of 29s, and a warning that it runs out of time](images/apigw-explorer-routes-light.webp#only-light){ width="984" height="866" loading=lazy }
![The Routes tab: orders-api's paths as a tree on the left, and POST /orders open on the right, its path from the caller through Cognito to orders-etl, the integration marked red with p95 29s of 29s, and a warning that it runs out of time](images/apigw-explorer-routes-dark.webp#only-dark){ width="984" height="866" loading=lazy }
/// caption
`POST /orders` in the Routes tab: the integration is where it fails.
///

The **Calls** tab reads the stage's access log for the time picked: a timeline of calls with the failed ones in red, chips by status, and a search box (typing filters what's read; Enter asks CloudWatch Logs for the text over the whole time). Click a call to see where its time went, and for one that reached Lambda, that run's lines.

![The Calls tab: a timeline, chips for 5xx, 4xx, slow and throttled calls, a list of calls, and a 504 on POST /orders open on the right: 29 seconds in the integration and the log lines of the orders-etl run behind it, which went on for 41 seconds](images/apigw-explorer-call-light.webp#only-light){ width="984" height="866" loading=lazy }
![The Calls tab: a timeline, chips for 5xx, 4xx, slow and throttled calls, a list of calls, and a 504 on POST /orders open on the right: 29 seconds in the integration and the log lines of the orders-etl run behind it, which went on for 41 seconds](images/apigw-explorer-call-dark.webp#only-dark){ width="984" height="866" loading=lazy }
/// caption
A call that timed out: API Gateway gave up at 29 seconds, and the `orders-etl` run behind it finished at 41.
///

**Overview** is `api_info()`, **Performance** is `performance()` and **Stages** is `stages()`; the stage menu at the top switches stage. The window only reads, like the reports, and outside Jupyter `explore()` shows the reports instead.

??? more "How the window reads"

    AWS is read off the notebook's event loop, so a click returns at once: the APIs and their CloudWatch numbers first, then the one you pick with its routes and stages, then its access log. A call's Lambda run is read when you click it, by the Lambda request ID its access log line records, through the [Lambda module](lambda_functions.md). Pick another API while one is still being read and the first one's result is dropped when it comes back.

<p class="part"><strong>That's the guide.</strong> Everything below is reference for when you need it: the data in Python, cost, permissions, troubleshooting and every command.</p>

## Use the data in Python { #python }

`apigw.core` is the `APIGatewayAnalyzer`. Every report has a data method on it.

```python
core = apigw.core

ov = core.overview(days=7)                       # Overview: .apis, .numbers (by API ID), .errors
api = core.api("orders-api")                     # Api: .kind, .stages, .routes, .authorizers, .open_routes
api.route("POST /orders").integration.function   # 'orders-etl'
page = core.calls("orders-api", since="1h")      # CallPage: .calls, .failed, .fields, .truncated
page.to_df()                                     # every call as a DataFrame
run = core.lambda_run(api, page.failed[0])       # LambdaRun: .function, .lines, .duration_ms, .note
```

The analysis functions make no AWS calls, so they also work on log lines you already have:

```python
from datetime import datetime, timezone
from aws_analyzer.apigateway import parse_access_line, read_format, group_errors

fmt = read_format(stage_format)                  # the stage's accessLogSettings format
calls = [parse_access_line(line, datetime.now(timezone.utc), fmt) for line in lines]
group_errors([c for c in calls if c])
```

## Cost { #cost }

Costs are estimates at us-east-1 list prices (`APIGATEWAY_PRICES`, the first tier): REST APIs $3.50 per million requests, HTTP APIs $1.00 per million, WebSocket APIs $1.00 per million messages, plus a REST cache by the hour ($0.02 an hour for 0.5 GB). Requests are what CloudWatch counted over the window, scaled to a month. Pass your own prices with `APIGatewayAnalyzer(prices={"rest_request": 0.0000037})`.

`apis()` reads 6 CloudWatch metrics per API ($0.01 per 1,000) and says how many it read; `api_info()` reads 6 more per route when the stage has detailed metrics. APIs, routes, stages and access logs cost nothing to read.

## Permissions { #permissions }

Everything is read-only. Anything the notebook's role can't read shows up as a note instead of an error. API Gateway authorizes every read, REST, HTTP and WebSocket alike, as `apigateway:GET` on the resource. This policy covers every command:

```json title="IAM policy"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadApis",
      "Effect": "Allow",
      "Action": "apigateway:GET",
      "Resource": "arn:aws:apigateway:*::/*"
    },
    {
      "Sid": "ReadNumbers",
      "Effect": "Allow",
      "Action": "cloudwatch:GetMetricData",
      "Resource": "*"
    },
    {
      "Sid": "ReadAccessLogsAndLambdaRuns",
      "Effect": "Allow",
      "Action": ["logs:FilterLogEvents", "lambda:GetFunction"],
      "Resource": "*"
    }
  ]
}
```

The last statement is for the access logs and the Lambda run behind a call; without it `calls()` and `errors()` say the log couldn't be read, and the counts still show.

## Troubleshooting { #troubleshooting }

??? question "“No API named …”"

    Names are case-sensitive and APIs are regional. The note lists the closest names; `apis()` lists them all. Use `APIGatewayView(APIGatewayAnalyzer(region="eu-west-1"))` for another region.

??? question "“Stage … keeps no access log”"

    API Gateway writes a line per call only when the stage's access log is on. The note shows the `aws apigateway update-stage` (or `aws apigatewayv2 update-stage`) command that turns it on; calls after that show up.

??? question "A call says “-” for Backend and has no Lambda run"

    The stage's access log format doesn't log `$context.integration.latency` or `$context.integration.requestId`. `stages()` shows the format, and a finding shows one that logs them.

??? question "The routes table has no numbers"

    Per-route numbers need the stage's detailed metrics (a setting on the stage, billed as custom metrics). `performance()` works them out from the access log instead.

## Command reference { #reference }

Every `APIGatewayView` command. `apigw.help()` prints the same list grouped by task, and `apigw.help("errors")` shows one command's full description.

<div class="ref" markdown>

| Command | What it shows |
|---|---|
| `apis(days=7)` | Every API: requests, 4xx and 5xx share, p95, who can call it, cost |
| `api_info(name, stage=None, days=7)` | One API: its numbers on a stage, its stages and its routes, and what needs attention |
| `routes(name, stage=None)` | Every route: who can call it, where it goes, its timeout, and the curl command that calls it |
| `stages(name)` | Every stage's throttling, logs, tracing, cache and WAF, and its access log format |
| `calls(name, stage=None, since="24h", status=None, search=None, n=50)` | The access log, call by call, newest first |
| `errors(name, stage=None, since="24h")` | Failed calls grouped by status, error and route, with what each means and the Lambda run to read |
| `performance(name, stage=None, since="24h")` | How long calls take, route by route, and where the slowest call's time went |
| `explore(name=None, tab=None, stage=None, since="24h", height=None)` | The explorer window: every API, its routes as a tree, its calls with their Lambda runs, performance and stages |
| `help(command=None)` | This list, grouped by task; `help("name")` shows one command in full |

</div>
