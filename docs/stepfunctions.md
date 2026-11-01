---
title: Step Functions Analyzer Guide
description: "How to see which step of an AWS Step Functions workflow failed, and read the Lambda run that broke it, from a SageMaker notebook with aws-analyzer's stepfunctions module."
---

<p class="eyebrow"><img class="aws-icon" src="images/aws/stepfunctions.svg" alt="" width="32" height="32"> AWS Step Functions <code>stepfunctions</code></p>

# See which step of a workflow failed, and read the Lambda run that broke it

Find the state machines that fail, see where and why their executions stop, and follow one execution step by step down to the log lines of the Lambda run behind the failure, without clicking through the console.
{ .lede }

<ul class="pills">
  <li>One install, boto3 only</li>
  <li>Read-only: never starts or redrives an execution</li>
  <li>The Lambda run's own lines, by request ID</li>
  <li>Plain text outside Jupyter</li>
</ul>

The examples use acme's `order-pipeline`, a Standard workflow that validates an order, charges the card, loads the order with the `orders-etl` Lambda function and resizes its pictures in a Map. Some of its executions fail at `LoadOrders`, where `orders-etl` raises `KeyError: 'customer_id'`.
{ .muted }

## Set up in SageMaker { #setup }

<div class="steps" markdown>

1. **Install it** in a notebook cell:

    ```bash
    %pip install aws-analyzer
    ```

2. **Make the view.** It uses the notebook's IAM role and region, so there's nothing to configure.

    ```python
    from aws_analyzer import StepFunctionsView

    sfn = StepFunctionsView()
    sfn.help()             # every command, grouped by task; sfn.help("execution") shows one in full
    ```

</div>

??? more "Another region or profile, plain text"

    State machines are regional, so if `overview()` comes back empty, check the region first. Only boto3 is required.

    ```python
    from aws_analyzer import StepFunctionsAnalyzer, StepFunctionsView

    sfn = StepFunctionsView(StepFunctionsAnalyzer(region="eu-west-1", profile="dev"))
    sfn = StepFunctionsView(mode="text")    # plain text, also what you get outside Jupyter
    ```

## Start here { #tour }

Four questions people bring to a workflow, and the one line that answers each. None of them change anything.
{ .gist }

<div class="quick" markdown>

-   **Which workflows fail, and what do they cost?**

    ```python
    sfn.overview()
    ```

    [Your state machines](#overview)

-   **Why does this one fail?**

    ```python
    sfn.failures("order-pipeline")
    ```

    [Why it fails](#failures)

-   **What happened in this execution?**

    ```python
    sfn.execution("order-pipeline/ord-7f3a")
    ```

    [One execution, step by step](#execution)

-   **What does this workflow do, and what could go wrong?**

    ```python
    sfn.state_machine("order-pipeline")
    ```

    [One state machine](#state-machine)

-   **I'd rather click than type.**

    ```python
    sfn.explore()
    ```

    [Explore by clicking](#explorer)

</div>

An execution is named by its ARN, a link copied from the console, or `"state-machine/execution-name"`.

??? more "How to read a report"

    Every report puts the answer first: cards with the numbers that matter, then the findings, warnings first, each a bold headline with why it matters and what to do underneath. The tables of detail come after, and **Next** at the bottom lists two or three commands with the arguments filled in. One click on a command selects all of it, ready to copy.

## Your state machines { #overview }

`overview()` lists every state machine in the region with how many executions it ran in the last 7 days, how many didn't succeed, its average run and its estimated monthly cost, the ones with failures first.
{ .gist }

```python
sfn.overview()            # or overview(days=30)
```

The numbers are CloudWatch's (`AWS/States`), in whole UTC days, so they cover Express workflows too. The findings point at the next command: `failures('order-pipeline')` for a Standard workflow that fails, and the log group for an Express one.

!!! tip "Worth knowing"

    An Express workflow keeps no history. With logging off, its executions leave no record at all, and at `ERROR` only its failures do. The finding shows the `aws stepfunctions update-state-machine` command that turns logging on.

## Why it fails { #failures }

`failures()` groups the executions that failed, timed out or were aborted by the state they stopped at and the error, the most common first, each with the run to read next.
{ .gist }

```python
sfn.failures("order-pipeline")                  # the last 7 days, the newest 50 failures
sfn.failures("order-pipeline", since="30d", limit=200)
```

For each failure it reads only the end of the execution's history (one request, whatever the history's length), which is where the failure is. When the failing state called Lambda, the group names the function and the newest run's request ID, ready for `LambdaView().logs('orders-etl', request_id='c0ffee00-…')`. It also says how many of the failed executions can still be **redriven**, started again from the state that failed, and shows the command; it never runs it.

## One execution, step by step { #execution }

`execution()` shows each state an execution passed through: what it called, when it started, how long it took, how many tries, and where it failed and why.
{ .gist }

```python
sfn.execution("order-pipeline/ord-7f3a")
sfn.execution("arn:aws:states:us-east-1:123456789012:execution:order-pipeline:ord-7f3a")
sfn.execution("ord-7f3a", machine="order-pipeline", logs=False)    # skip the Lambda logs
```

A **waterfall** puts each state on one time axis, with a task's tries, the back-off between them and a Map's iterations in place. Then comes **one log of what happened**: Step Functions' own events and **what each Lambda function logged in that very run**, merged by time. Each run is found by the request ID Step Functions records in the step's result or error, so the lines are that run's and no other's. An older task that names a function's ARN as its resource records no request ID; then the run that started just after the step was scheduled is shown, and the report says it was matched by time.

The states inside a Map or Parallel are summed up under it (`×12`, how many failed, the longest), retries show as tries, and a Catch that moved on is said in a finding.

??? more "How the Lambda lines are read"

    The Step Functions module doesn't read CloudWatch Logs itself: it asks the [Lambda module](lambda_functions.md) for the run, `LambdaAnalyzer.log_events(name, request_id=...)`, the same read `LambdaView().logs()` makes, and draws its lines the same way. The Lambda module loads only when a failed Lambda step needs it, so `sfn.overview()` never imports it.

## One state machine { #state-machine }

`state_machine()` shows a workflow's numbers, every state with what it calls, where it goes next, its retries and where an error sends it, and what its definition risks.
{ .gist }

```python
sfn.state_machine("order-pipeline")
sfn.graph("order-pipeline")                         # the flow drawn as a graph, and Mermaid to paste
sfn.graph("order-pipeline", execution="ord-7f3a")   # coloured by the path that execution took
sfn.graph("order-pipeline", failures="7d")          # shaded by where executions stopped
```

The findings read the definition: a Lambda task with no Retry for Lambda's own errors (`Lambda.ServiceException`, `Lambda.TooManyRequestsException`, ...) fails the whole execution on one throttle, and the finding shows the Retry rule AWS recommends, to paste; a task that waits for a callback (`.waitForTaskToken`, an activity) with no `TimeoutSeconds` or `HeartbeatSeconds` can wait up to a year.

## Explore by clicking { #explorer }

`explore()` opens a window with every state machine; click one to read its executions, each with its waterfall and one log of what happened.
{ .gist }

```python
sfn.explore()                                       # the state machine failing most, or the first
sfn.explore("order-pipeline", execution="ord-7f3a") # with one execution open
sfn.explore("order-pipeline", tab="failures", since="30d")
```

![The Step Functions explorer: four state machines with their executions, failures, average run, cost and logging, and the findings](images/sfn-explorer-light.webp#only-light){ width="984" height="880" loading=lazy }
![The Step Functions explorer: four state machines with their executions, failures, average run, cost and logging, and the findings](images/sfn-explorer-dark.webp#only-dark){ width="984" height="880" loading=lazy }
/// caption
`sfn.explore()`: every state machine, the failing ones first; the cards say how the one picked is doing.
///

The **Executions** tab lists them newest first. Pick a time range, filter by outcome or search by name, and click one. Its page opens with the findings and the waterfall as soon as its history is read, then the log once its Lambda runs are.

![An execution open in the explorer: its findings and a waterfall where ChargeCard's two throttled tries and back-off come before LoadOrders fails with KeyError](images/sfn-explorer-run-light.webp#only-light){ width="984" height="880" loading=lazy }
![An execution open in the explorer: its findings and a waterfall where ChargeCard's two throttled tries and back-off come before LoadOrders fails with KeyError](images/sfn-explorer-run-dark.webp#only-dark){ width="984" height="880" loading=lazy }
/// caption
ord-7f3a: two throttled tries on ChargeCard, then LoadOrders fails with `KeyError`.
///

![The execution's log in the explorer: Step Functions' events and the lines of validate-order, charge-card and orders-etl merged by time, each source in its own colour](images/sfn-explorer-log-light.webp#only-light){ width="984" height="880" loading=lazy }
![The execution's log in the explorer: Step Functions' events and the lines of validate-order, charge-card and orders-etl merged by time, each source in its own colour](images/sfn-explorer-log-dark.webp#only-dark){ width="984" height="880" loading=lazy }
/// caption
Lower on the same page: Step Functions' events and each function's lines, one log in time order.
///

The **Graph** tab draws the state machine from its definition. It is coloured by the path the open execution took, or by where executions stopped in the time range. Pick a state beside the graph to see its tries, its Lambda run's lines, or the failures that stopped there.

![The Graph tab: order-pipeline drawn as boxes and arrows, the path ord-7f3a took in green up to LoadOrders in red, its error arrow to OrderFailed, the states it never reached dashed, and LoadOrders picked on the right](images/sfn-explorer-graph-light.webp#only-light){ width="984" height="880" loading=lazy }
![The Graph tab: order-pipeline drawn as boxes and arrows, the path ord-7f3a took in green up to LoadOrders in red, its error arrow to OrderFailed, the states it never reached dashed, and LoadOrders picked on the right](images/sfn-explorer-graph-dark.webp#only-dark){ width="984" height="880" loading=lazy }
/// caption
The Graph tab with ord-7f3a open: its path in green, the failure in red, and what it never reached dashed.
///

The **Failures** tab is `failures()`, with a button per group that opens its newest execution. **Overview** is `state_machine()` and **Definition** is `graph()`. The window only reads, like the reports, and outside Jupyter `explore()` shows the reports instead.

??? more "How the window reads"

    AWS is read off the notebook's event loop, so a click returns at once: the state machines and their CloudWatch numbers first, then the one you pick, then its executions. An execution's history is one read and its log another, the Lambda runs read side by side (at most 12, the failed ones first). Pick another execution while one is still being read and the first one's result is dropped when it comes back.

<p class="part"><strong>That's the guide.</strong> Everything below is reference for when you need it: the data in Python, cost, permissions, troubleshooting and every command.</p>

## Use the data in Python { #python }

`sfn.core` is the `StepFunctionsAnalyzer`. Every report has a data method on it.

```python
core = sfn.core

ov = core.overview(days=7)                          # Overview: .machines, .numbers (by ARN), .errors
machine = core.state_machine("order-pipeline")      # StateMachine: .type, .states, .functions, .log_level
core.executions("order-pipeline", status="failed").to_df()
detail = core.execution("order-pipeline/ord-7f3a")  # ExecutionDetail: .steps, .failed_step, .lambda_run
detail.failed_step.request_id                       # the Lambda run behind the failure
core.failures("order-pipeline").groups              # FailureGroup: state, error, count, newest, request_id
```

The analysis functions make no AWS calls, so they also work on a definition or a history you already have:

```python
from aws_analyzer.stepfunctions import ExecutionDetail, fill_detail, parse_definition, parse_execution

start, states = parse_definition(open("order-pipeline.asl.json").read())
detail = fill_detail(ExecutionDetail(parse_execution(described)), history_events)
```

## Cost { #cost }

Costs are estimates at us-east-1 list prices (`STEPFUNCTIONS_PRICES`): $0.025 per 1,000 state transitions for Standard workflows, and for Express $1.00 per million executions plus $0.00001667 per GB-second at 64 MB, the least Express bills. A Standard execution is counted as one transition per top-level state; Map iterations add more, and the 4,000 free transitions a month aren't taken off. Pass your own prices with `StepFunctionsAnalyzer(prices={"transition": 0.0000275})`.

`overview()` reads 7 CloudWatch metrics per state machine ($0.01 per 1,000) and says how many it read. Executions, histories and Lambda logs cost nothing to read.

## Permissions { #permissions }

Everything is read-only. Anything the notebook's role can't read shows up as a note instead of an error. This policy covers every command:

```json title="IAM policy"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadStateMachines",
      "Effect": "Allow",
      "Action": [
        "states:ListStateMachines", "states:DescribeStateMachine", "states:ListExecutions",
        "states:DescribeExecution", "states:GetExecutionHistory"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ReadNumbers",
      "Effect": "Allow",
      "Action": "cloudwatch:GetMetricData",
      "Resource": "*"
    },
    {
      "Sid": "ReadLambdaRuns",
      "Effect": "Allow",
      "Action": ["lambda:GetFunction", "logs:FilterLogEvents"],
      "Resource": "*"
    }
  ]
}
```

The last statement is only for the Lambda lines in `execution()`; without it the report says the run couldn't be read and shows the call that reads it.

## Troubleshooting { #troubleshooting }

??? question "“No state machine named …”"

    Names are case-sensitive and state machines are regional. The note lists the closest names; `overview()` lists them all. Use `StepFunctionsView(StepFunctionsAnalyzer(region="eu-west-1"))` for another region.

??? question "“… is an Express workflow”"

    Step Functions keeps no list or history of Express executions, so `executions()`, `execution()` and `failures()` can't read them. They're in the workflow's log group when logging is on; `state_machine()` shows how to turn it on.

??? question "The Lambda run behind a step couldn't be shown"

    The note says why: the function no longer exists, it has no log group, its logs have expired, or the role can't call `logs:FilterLogEvents`. The call it shows reads the run with the Lambda module.

??? question "`ExecutionDoesNotExist`"

    Step Functions keeps an execution for 90 days after it ends. `executions("order-pipeline", since="90d")` lists the ones there are.

## Command reference { #reference }

Every `StepFunctionsView` command. `sfn.help()` prints the same list grouped by task, and `sfn.help("execution")` shows one command's full description.

<div class="ref" markdown>

| Command | What it shows |
|---|---|
| `overview(days=7)` | Every state machine: executions, how many didn't succeed, average run, cost, logging |
| `state_machine(name, days=7)` | One state machine: its numbers, every state, what its definition risks, the definition |
| `graph(name, execution=None, failures=None)` | The flow drawn as a graph, coloured by an execution's path or where executions stop, and Mermaid to paste |
| `executions(name, status=None, since="7d", n=25)` | Its latest executions, newest first |
| `execution(ref, machine=None, logs=True, max_events=5000)` | One execution step by step: its waterfall, and one log with every Lambda run's lines |
| `failures(name, since="7d", limit=50)` | Failures grouped by the state they stopped at and the error |
| `explore(name=None, execution=None, tab=None, since="7d", height=None)` | The explorer window: every state machine, its executions with their waterfall and log, failures and definition |
| `help(command=None)` | This list, grouped by task; `help("name")` shows one command in full |

</div>
