---
title: Step Functions Explorer Guide
description: "How to click through AWS Step Functions state machines in a SageMaker notebook with aws-analyzer's Step Functions explorer window: every state machine and how it's doing, its executions, each one's waterfall and one log with its Lambda runs' lines, its failures by cause, and its graph."
---

<p class="eyebrow"><img class="aws-icon" src="images/aws/stepfunctions.svg" alt="" width="32" height="32"> Step Functions explorer <code>stepfunctions/explorer/</code></p>

# Click from a failing workflow to the step that broke it

`explore()` turns a notebook cell into a window on your state machines. The failing ones come first; click one for its executions, and an execution for its waterfall, one log of what happened (with the lines of every Lambda run it called) and its path on the graph.
{ .lede }

<ul class="pills">
  <li>One install, ipywidgets already on SageMaker</li>
  <li>Read-only: never starts or redrives an execution</li>
  <li>The Lambda runs' own lines, merged in</li>
  <li>Standard and Express workflows</li>
</ul>

The examples use acme's `order-pipeline`, a Standard workflow that validates an order, charges the card, loads the order with the `orders-etl` Lambda function and resizes its pictures in a Map. Some of its executions fail at `LoadOrders`, where `orders-etl` raises `KeyError: 'customer_id'`. For reports you run from code, see the [Step Functions guide](stepfunctions.md).
{ .muted }

![The Step Functions explorer in a notebook, animated: it starts on the State machines tab, four state machines with their failures, run times and cost; the pointer opens the Executions tab and clicks the failed execution ord-7f3a, whose waterfall shows each step's time and the one that failed; then it opens the Graph tab and clicks the LoadOrders state, the workflow drawn as boxes and arrows with that state's definition and tries beside it](images/sfn-explorer-tour-light.webp#only-light){ width="984" height="865" loading=lazy }
![The Step Functions explorer in a notebook, animated: it starts on the State machines tab, four state machines with their failures, run times and cost; the pointer opens the Executions tab and clicks the failed execution ord-7f3a, whose waterfall shows each step's time and the one that failed; then it opens the Graph tab and clicks the LoadOrders state, the workflow drawn as boxes and arrows with that state's definition and tries beside it](images/sfn-explorer-tour-dark.webp#only-dark){ width="984" height="865" loading=lazy }
/// caption
**`explore()`**: open a failed execution to see which step broke, then find that step on the workflow's graph.
///

## Watch it run { #video }

Four steps in a notebook, from every state machine to the path a failed execution took: each command typed and run, and the window it shows, with what to notice. It plays as you scroll to it; click it to pause, or pick a chapter to jump to a step.
{ .gist }

<div class="aa-tour" data-tour="stepfunctions_explorer">
  <div class="tour-screen"></div>
  <div class="tour-controls"></div>
  <div class="tour-chapters"></div>
  <p class="tour-links"><a href="tour.html?service=stepfunctions_explorer">Full screen</a> · <a href="videos/stepfunctions_explorer-tour-light.mp4" data-tour-mp4 download>Download as MP4</a>, for Slack or a slide (light or dark, as this page is)</p>
</div>

## Open it { #open }

<div class="steps" markdown>

1. **Install it** in a notebook cell:

    ```bash
    %pip install aws-analyzer
    ```

2. **Open the window.** It uses the notebook's IAM role and region, so there's nothing to configure.

    ```python
    from aws_analyzer import StepFunctionsView

    sfn = StepFunctionsView()
    x = sfn.explore()                # the state machine failing most, or the first
    ```

</div>

??? more "Straight to a state machine, an execution or a tab"

    ```python
    sfn.explore()                                       # the state machine failing most, or the first
    sfn.explore("order-pipeline", execution="ord-7f3a") # with one execution open
    sfn.explore("order-pipeline", tab="failures", since="30d")
    ```

    `tab=` is one of `"overview"`, `"executions"`, `"graph"`, `"failures"` and `"definition"`, and `since=` one of `"24h"`, `"7d"` (the default), `"30d"` and `"90d"`. `region=`, `profile=` and `height=` work as for the reports. The window needs `ipywidgets`, which SageMaker notebooks already have; without it, or outside Jupyter, `explore()` shows the [reports](stepfunctions.md) instead.

## Start here { #start }

Four things to try in your first minute. Nothing you click starts, stops or redrives an execution.
{ .gist }

<div class="quick" markdown>

What's wrong
{ .qgroup }

-   [Which workflows are failing?](#machines) <span class="how">Read the State machines tab, failing ones first</span>
-   [Where do its executions stop, and why?](#failures) <span class="how">Open the Failures tab</span>

One execution
{ .qgroup }

-   [Which step broke this execution?](#executions) <span class="how">Executions, then click a failed one</span>
-   [Where is that step in the workflow?](#graph) <span class="how">Open the Graph tab</span>

</div>

## Every state machine { #machines }

The **State machines** tab lists every state machine in the region, the failing ones first, under the region's findings.
{ .gist }

![The Step Functions explorer: the findings, then a search box, chips and four state machines with their executions, failures, average run, cost and logging under column headers that sort](images/sfn-explorer-light.webp#only-light){ width="984" height="866" loading=lazy }
![The Step Functions explorer: the findings, then a search box, chips and four state machines with their executions, failures, average run, cost and logging under column headers that sort](images/sfn-explorer-dark.webp#only-dark){ width="984" height="866" loading=lazy }
/// caption
`sfn.explore()`: every state machine, the failing ones first; search them, pick a chip (Failing, Express, Logging off, ...) or click a column to sort. The cards say how the one picked is doing.
///

Each line has its executions, the share that didn't succeed, its average run, its cost and whether it logs. Search by name, click a chip for only the **Failing**, **Express**, **Standard**, **Logging off** or **Not run** ones, and click a column's header to sort by it. Click a state machine to open it: the cards over the tabs say how it's doing, and its **Overview** is [`state_machine()`](stepfunctions.md#state-machine), its findings first.

## Its executions { #executions }

The **Executions** tab lists them newest first. Click one for its waterfall: each state on one time axis, its tries and back-off in place.
{ .gist }

The **Executions** tab lists them newest first. Pick a time range, filter by outcome or search by name, and click one. Its page opens with the findings and the waterfall as soon as its history is read, then the log once its Lambda runs are.

- **The time range** is the last 24 hours, 7 days, 30 days or 90 days.
- **The chips** show only the executions that **Failed**, **Timed out**, were **Aborted**, are **Running** or **Succeeded**.
- **The search box** finds an execution by its name.
- **↗ Console**, above the open execution, opens it in the Step Functions console, where you can redrive it or start it again. The one beside ↻ Refresh opens the state machine there.

![An execution open in the explorer: its findings and a waterfall where ChargeCard's two throttled tries and back-off come before LoadOrders fails with KeyError](images/sfn-explorer-run-light.webp#only-light){ width="984" height="866" loading=lazy }
![An execution open in the explorer: its findings and a waterfall where ChargeCard's two throttled tries and back-off come before LoadOrders fails with KeyError](images/sfn-explorer-run-dark.webp#only-dark){ width="984" height="866" loading=lazy }
/// caption
ord-7f3a: two throttled tries on ChargeCard, then LoadOrders fails with `KeyError`.
///

Lower on the same page is **one log of what happened**: Step Functions' events and the lines each Lambda run logged, merged by time, so `orders-etl`'s traceback sits between the step that called it and the Catch that ended the execution.

![The execution's log in the explorer: Step Functions' events and the lines of validate-order, charge-card and orders-etl merged by time, each source in its own colour](images/sfn-explorer-log-light.webp#only-light){ width="984" height="866" loading=lazy }
![The execution's log in the explorer: Step Functions' events and the lines of validate-order, charge-card and orders-etl merged by time, each source in its own colour](images/sfn-explorer-log-dark.webp#only-dark){ width="984" height="866" loading=lazy }
/// caption
Lower on the same page: Step Functions' events and each function's lines, one log in time order.
///

!!! note ""

    Express workflows keep no history in Step Functions, so the window reads their executions from the log group their logging names. An Express state machine with logging off keeps no record of its executions anywhere, so turn its logging on to see them here.

## The graph { #graph }

The **Graph** tab draws the state machine from its definition, coloured by the path the open execution took, or by where executions stopped.
{ .gist }

![The Graph tab: order-pipeline drawn as boxes and arrows, the path ord-7f3a took in green up to LoadOrders in red, its error arrow to OrderFailed, the states it never reached dashed, and LoadOrders picked on the right](images/sfn-explorer-graph-light.webp#only-light){ width="984" height="866" loading=lazy }
![The Graph tab: order-pipeline drawn as boxes and arrows, the path ord-7f3a took in green up to LoadOrders in red, its error arrow to OrderFailed, the states it never reached dashed, and LoadOrders picked on the right](images/sfn-explorer-graph-dark.webp#only-dark){ width="984" height="866" loading=lazy }
/// caption
The Graph tab with ord-7f3a open: its path in green, the failure in red, and what it never reached dashed.
///

The toggle over the graph picks what colours it: **This execution** (its path, the failure in red, the states it never reached dashed), **Where failures stop** (the failures in the time range, by the state they stopped at) or **Just the flow**. Pick a state in the list beside the graph to see its definition, its tries, its Lambda run's lines, or the failures that stopped there.

## Failures, overview and definition { #failures }

The other tabs are the [Step Functions guide](stepfunctions.md)'s reports, a click away.
{ .gist }

- **Failures** is [`failures()`](stepfunctions.md#failures): the failed executions grouped by the state they stopped at and why, with a button per group that opens its newest execution.
- **Overview** is [`state_machine()`](stepfunctions.md#state-machine): how it's doing, what its definition risks, and every state.
- **Definition** is [`graph()`](stepfunctions.md#state-machine): the flow drawn as a graph, with Mermaid to paste.

<p class="part"><strong>That's the guide.</strong> Everything below is reference for when you need it: how the window reads, and every command.</p>

## From code { #from-code }

The window is an object you can drive: what a click does, a command does too.
{ .gist }

```python
x = sfn.explore()
x.open("order-pipeline")          # another state machine, a name or ARN
x.execution("ord-7f3a")           # an execution: its ARN, 'machine/name', or a name of the one shown
x.refresh()                       # read everything again (the ↻ button)
```

??? more "How the window reads"

    AWS is read off the notebook's event loop, so a click returns at once: the state machines and their CloudWatch numbers first, then the one you pick, then its executions. An execution's history is one read and its log another, the Lambda runs read side by side (at most 12, the failed ones first). Pick another execution while one is still being read and the first one's result is dropped when it comes back.

## Command reference { #reference }

<div class="ref" markdown>

| Command | What it does |
|---|---|
| `explore(machine=None, execution=None, tab=None, since="7d", region=None, profile=None, height=None)` | Opens the window and returns it; `sfn.explore(...)` on a `StepFunctionsView` takes the same |
| `x.open(machine)` | Shows another state machine (a name or ARN) |
| `x.execution(ref)` | Opens an execution in the Executions tab: its waterfall and one log of what happened |
| `x.refresh()` | Reads everything again (the ↻ button) |

</div>
