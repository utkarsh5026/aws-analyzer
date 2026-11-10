---
title: API Gateway Explorer Guide
description: "How to click through Amazon API Gateway's REST, HTTP and WebSocket APIs in a SageMaker notebook with aws-analyzer's API Gateway explorer window: every API and how it's doing, its routes as a tree, its calls one by one with where each call's time went and the Lambda run behind it."
---

<p class="eyebrow"><img class="aws-icon" src="images/aws/apigateway.svg" alt="" width="32" height="32"> API Gateway explorer <code>apigateway/explorer/</code></p>

# Click from a failing API to the call that broke, and the Lambda run behind it

`explore()` turns a notebook cell into a window on your APIs. The failing ones come first; click one for its routes as a tree and its calls one by one, each with where its time went and, when it reached Lambda, that run's own lines.
{ .lede }

<ul class="pills">
  <li>One install, ipywidgets already on SageMaker</li>
  <li>Read-only: never sends a request to an API</li>
  <li>REST, HTTP and WebSocket APIs</li>
  <li>The Lambda run behind a call</li>
</ul>

The examples use acme's `orders-api`, a REST API whose `POST /orders` runs the `orders-etl` Lambda function, which sometimes takes longer than API Gateway's 29 seconds. For reports you run from code, see the [API Gateway guide](apigateway.md).
{ .muted }

![The API Gateway explorer in a notebook, animated: it starts on the APIs tab, its findings about the six APIs: orders-api returning 5xx on 2.9% of calls and close to its time limit, partner-api's open routes; the pointer opens the Routes tab, the routes as a tree, POST /orders picked with where it sends a request and why it runs out of time; then it opens the Calls tab, clicks the 5xx chip and the newest 504, which shows where its 29 seconds went, almost all in the integration, and the log lines of the orders-etl run behind it](images/apigw-explorer-tour-light.webp#only-light){ width="984" height="865" loading=lazy }
![The API Gateway explorer in a notebook, animated: it starts on the APIs tab, its findings about the six APIs: orders-api returning 5xx on 2.9% of calls and close to its time limit, partner-api's open routes; the pointer opens the Routes tab, the routes as a tree, POST /orders picked with where it sends a request and why it runs out of time; then it opens the Calls tab, clicks the 5xx chip and the newest 504, which shows where its 29 seconds went, almost all in the integration, and the log lines of the orders-etl run behind it](images/apigw-explorer-tour-dark.webp#only-dark){ width="984" height="865" loading=lazy }
/// caption
**`explore()`**: from an API to its routes, then to a failed call, where its time went and the Lambda run that timed out.
///

## Watch it run { #video }

Three steps in a notebook, from every API to a call that timed out and the run behind it: each command typed and run, and the window it shows, with what to notice. It plays as you scroll to it; click it to pause, or pick a chapter to jump to a step.
{ .gist }

<div class="aa-tour" data-tour="apigateway_explorer">
  <div class="tour-screen"></div>
  <div class="tour-controls"></div>
  <div class="tour-chapters"></div>
  <p class="tour-links"><a href="tour.html?service=apigateway_explorer">Full screen</a> · <a href="videos/apigateway_explorer-tour-light.mp4" data-tour-mp4 download>Download as MP4</a>, for Slack or a slide (light or dark, as this page is)</p>
</div>

## Open it { #open }

<div class="steps" markdown>

1. **Install it** in a notebook cell:

    ```bash
    %pip install aws-analyzer
    ```

2. **Open the window.** It uses the notebook's IAM role and region, so there's nothing to configure.

    ```python
    from aws_analyzer import APIGatewayView

    apigw = APIGatewayView()
    x = apigw.explore()              # the API failing most, or the first
    ```

</div>

??? more "Straight to an API, a stage or a tab"

    ```python
    apigw.explore()                               # the API failing most, or the first
    apigw.explore("orders-api", tab="calls")      # straight to its calls
    apigw.explore("orders-api/dev", since="7d")   # another stage, the last 7 days
    ```

    An API is named by its name, its ID, `"name/stage"` or its invoke URL. `tab=` is one of `"overview"`, `"routes"`, `"calls"`, `"performance"` and `"stages"`, and `since=` one of `"1h"`, `"24h"` (the default) and `"7d"`. `region=`, `profile=` and `height=` work as for the reports. The window needs `ipywidgets`, which SageMaker notebooks already have; without it, or outside Jupyter, `explore()` shows the [reports](apigateway.md) instead.

## Start here { #start }

Four things to try in your first minute. Nothing you click sends a request to an API or changes it.
{ .gist }

<div class="quick" markdown>

What's wrong
{ .qgroup }

-   [Which APIs are failing?](#apis) <span class="how">Read the APIs tab, failing ones first</span>
-   [Who can call a route without signing in?](#apis) <span class="how">Click the Open routes chip</span>

One API
{ .qgroup }

-   [Where does a request to this route go?](#routes) <span class="how">Routes, then click the route</span>
-   [Why did this call fail?](#calls) <span class="how">Calls, the 5xx chip, then click it</span>

</div>

## Every API { #apis }

The **APIs** tab lists every API in the region, the failing ones first, under the region's findings.
{ .gist }

![The API Gateway explorer: findings, then a search box, chips and six APIs with their requests, 5xx share, p95, cost and auth under column headers that sort](images/apigw-explorer-light.webp#only-light){ width="984" height="866" loading=lazy }
![The API Gateway explorer: findings, then a search box, chips and six APIs with their requests, 5xx share, p95, cost and auth under column headers that sort](images/apigw-explorer-dark.webp#only-dark){ width="984" height="866" loading=lazy }
/// caption
`apigw.explore()`: every API, the failing ones first; search them, pick a chip (Failing, Open routes, Unused, ...) or click a column to sort.
///

Each line has its requests, its share of 5xx, its p95 latency, its cost and how it's authorized. Search by name, click a chip for only the **Failing**, **REST**, **HTTP** or **WebSocket** ones, those with **Open routes** (anyone can call them) or the **Unused** ones, and click a column's header to sort by it. Click an API to open it: the cards over the tabs say how it's doing, and the stage menu at the top switches stage.

## Its routes { #routes }

The **Routes** tab is the API's paths as a tree. Click a route for where a request goes, box by box, with the step that fails marked.
{ .gist }

The **Routes** tab is a tree of the API's paths. Click a route to see where a request goes, from the caller through the authorizer to the integration, with the step that fails marked, its settings and the command that calls it.

![The Routes tab: orders-api's paths as a tree on the left, and POST /orders open on the right, its path from the caller through Cognito to orders-etl, the integration marked red with p95 29s of 29s, and a warning that it runs out of time](images/apigw-explorer-routes-light.webp#only-light){ width="984" height="866" loading=lazy }
![The Routes tab: orders-api's paths as a tree on the left, and POST /orders open on the right, its path from the caller through Cognito to orders-etl, the integration marked red with p95 29s of 29s, and a warning that it runs out of time](images/apigw-explorer-routes-dark.webp#only-dark){ width="984" height="866" loading=lazy }
/// caption
`POST /orders` in the Routes tab: the integration is where it fails.
///

## Its calls, one by one { #calls }

The **Calls** tab is the stage's access log, a call per line. Click one for where its time went, and the Lambda run behind it.
{ .gist }

The **Calls** tab reads the stage's access log for the time picked: a timeline of calls with the failed ones in red, chips by status, and a search box (typing filters what's read; Enter asks CloudWatch Logs for the text over the whole time). Click a call to see where its time went, and for one that reached Lambda, that run's lines.

- **The time range** is the last hour, 24 hours or 7 days.
- **The chips** show only the **5xx**, **4xx**, **Slow**, **Throttled** or **OK** calls.
- **A call's page** splits its time between the authorizer, the integration and API Gateway itself, and for one that reached Lambda, reads that run by the request ID the log line records.

![The Calls tab: a timeline, chips for 5xx, 4xx, slow and throttled calls, a list of calls, and a 504 on POST /orders open on the right: 29 seconds in the integration and the log lines of the orders-etl run behind it, which went on for 41 seconds](images/apigw-explorer-call-light.webp#only-light){ width="984" height="866" loading=lazy }
![The Calls tab: a timeline, chips for 5xx, 4xx, slow and throttled calls, a list of calls, and a 504 on POST /orders open on the right: 29 seconds in the integration and the log lines of the orders-etl run behind it, which went on for 41 seconds](images/apigw-explorer-call-dark.webp#only-dark){ width="984" height="866" loading=lazy }
/// caption
A call that timed out: API Gateway gave up at 29 seconds, and the `orders-etl` run behind it finished at 41.
///

!!! tip "Worth knowing"

    The Calls tab reads the stage's access log, and API Gateway writes one only when it's turned on: a stage without one has no calls to show, and its note gives the `aws apigateway update-stage` command that turns it on (see [Troubleshooting](apigateway.md#troubleshooting)). Calls after that show up.

## Overview, performance and stages { #more }

The other tabs are the [API Gateway guide](apigateway.md)'s reports, a click away.
{ .gist }

- **Overview** is [`api_info()`](apigateway.md#api-info): how the API is doing, what needs attention, its stages and its routes.
- **Performance** is [`performance()`](apigateway.md#performance): how long calls take, route by route, against the 29 s limit.
- **Stages** is `stages()`: each stage's settings, its throttling and its access log format.

<p class="part"><strong>That's the guide.</strong> Everything below is reference for when you need it: how the window reads, and every command.</p>

## From code { #from-code }

The window is an object you can drive: what a click does, a command does too.
{ .gist }

```python
x = apigw.explore()
x.open("orders-api", tab="calls")    # another API, on a tab
x.route("POST /orders")              # a route in the Routes tab
x.call("c4f1…")                      # a call by its request ID, in the Calls tab
x.refresh()                          # read everything again (the ↻ button)
```

??? more "How the window reads"

    AWS is read off the notebook's event loop, so a click returns at once: the APIs and their CloudWatch numbers first, then the one you pick with its routes and stages, then its access log. A call's Lambda run is read when you click it, by the Lambda request ID its access log line records, through the [Lambda module](lambda_functions.md). Pick another API while one is still being read and the first one's result is dropped when it comes back.

## Command reference { #reference }

<div class="ref" markdown>

| Command | What it does |
|---|---|
| `explore(api=None, tab=None, stage=None, since="24h", region=None, profile=None, height=None)` | Opens the window and returns it; `apigw.explore(...)` on an `APIGatewayView` takes the same |
| `x.open(api, tab=None)` | Shows another API (a name, ID, `"name/stage"` or invoke URL), on `tab` when one is named |
| `x.route(key)` | Opens a route in the Routes tab (`"POST /orders"`) |
| `x.call(request_id)` | Opens a call in the Calls tab: where its time went and the Lambda run behind it |
| `x.refresh()` | Reads everything again (the ↻ button) |

</div>
