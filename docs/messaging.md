---
title: SQS, SNS and EventBridge Analyzer Guide
description: "How to find out where a message went: queues that back up, dead-letter queues holding failed messages, SNS deliveries and EventBridge events that fail, and the policies that keep senders out, from a SageMaker notebook with aws-analyzer's messaging module."
---

<p class="eyebrow"><img class="aws-icon" src="images/aws/messaging.svg" alt="" width="32" height="32"> Amazon SQS, SNS and EventBridge <code>messaging</code></p>

# Find out where your message went

See which queues back up and which dead-letter queues hold failed messages, follow a message from a topic or an EventBridge rule hop by hop to the Lambda function that reads it, and find the policy or key that keeps a sender out, without clicking through three consoles.
{ .lede }

<ul class="pills">
  <li>SQS, SNS and EventBridge in one map</li>
  <li>Read-only: never receives a message</li>
  <li>The fix as a command to copy</li>
  <li>A window to click through it</li>
  <li>Plain text outside Jupyter</li>
</ul>

The examples use acme's `orders-events` topic, which delivers to four queues and a Lambda function, and the `shop` event bus, whose `order-placed` rule sends big orders to an audit queue and a fraud check. Some of those deliveries fail, and 42 orders wait in a dead-letter queue.
{ .muted }

![overview(): cards with 9 queues, 3 topics and 4 rules, 135 messages waiting, 42 in dead-letter queues and 2,720 failed deliveries, then the findings, warnings first: orders-dlq holds 42 failed messages, orders-events failed 2,310 deliveries, two queues keep it out, and the tables of queues, topics and rules](images/msg-overview-light.webp#only-light){ width="984" height="2635" loading=lazy }
![overview(): cards with 9 queues, 3 topics and 4 rules, 135 messages waiting, 42 in dead-letter queues and 2,720 failed deliveries, then the findings, warnings first: orders-dlq holds 42 failed messages, orders-events failed 2,310 deliveries, two queues keep it out, and the tables of queues, topics and rules](images/msg-overview-dark.webp#only-dark){ width="984" height="2635" loading=lazy }
/// caption
**`msg.overview()`**: what failed first, then every queue, topic and rule, with the commands that fix what's wrong.
///

![The messaging explorer in a notebook, animated: it opens on the Everything tab, orders-dlq holding 42 failed messages first; the pointer opens orders-events and its Map tab, the subscriptions as a tree with partner-webhooks and audit-events marked because their policy keeps the topic out, clicks partner-webhooks, which the map then follows; it opens the Dead letters tab, then email-jobs and the Live tab, where the backlog drains a few messages at each look](images/msg-explorer-tour-light.webp#only-light){ width="984" height="865" loading=lazy }
![The messaging explorer in a notebook, animated: it opens on the Everything tab, orders-dlq holding 42 failed messages first; the pointer opens orders-events and its Map tab, the subscriptions as a tree with partner-webhooks and audit-events marked because their policy keeps the topic out, clicks partner-webhooks, which the map then follows; it opens the Dead letters tab, then email-jobs and the Live tab, where the backlog drains a few messages at each look](images/msg-explorer-tour-dark.webp#only-dark){ width="984" height="865" loading=lazy }
/// caption
**`msg.explore()`**: from what failed, to where a topic's messages go, to a queue draining as you watch.
///

## Watch it run { #video }

Three calls in a notebook, from every queue, topic and rule to one queue's dead letters: each call typed and run, and the report it draws, with what to notice. It plays as you scroll to it; click it to pause, or pick a chapter to jump to a call.
{ .gist }

<div class="aa-tour" data-tour="messaging">
  <div class="tour-screen"></div>
  <div class="tour-controls"></div>
  <div class="tour-chapters"></div>
  <p class="tour-links"><a href="tour.html?service=messaging">Full screen</a> · <a href="videos/messaging-tour-light.mp4" data-tour-mp4 download>Download as MP4</a>, for Slack or a slide (light or dark, as this page is)</p>
</div>

## Set up in SageMaker { #setup }

<div class="steps" markdown>

1. **Install it** in a notebook cell:

    ```bash
    %pip install aws-analyzer
    ```

2. **Make the view.** It uses the notebook's IAM role and region, so there's nothing to configure.

    ```python
    from aws_analyzer import MessagingView

    msg = MessagingView()
    msg.help()             # every command, grouped by task; msg.help("follow") shows one in full
    ```

</div>

??? more "Another region or profile, plain text"

    Queues, topics and rules are regional, so if `overview()` comes back empty, check the region first. Only boto3 is required.

    ```python
    from aws_analyzer import MessagingAnalyzer, MessagingView

    msg = MessagingView(MessagingAnalyzer(region="eu-west-1", profile="dev"))
    msg = MessagingView(mode="text")    # plain text, also what you get outside Jupyter
    ```

## Start here { #tour }

Five questions people bring to a queue or a topic, and the one line that answers each. None of them touch a message.
{ .gist }

<div class="quick" markdown>

Health
{ .qgroup }

-   [What's backing up or failing?](#overview) `msg.overview()`
-   [What's wrong with this queue?](#one) `msg.queue_info("orders")`

Where messages go
{ .qgroup }

-   [Where does a message go from here?](#follow) `msg.follow("orders-events")`{ .long }
-   [Which messages failed for good?](#dead-letters) `msg.dead_letters()`

Rather click?
{ .qgroup }

-   [Can I click through it instead?](#explorer) `msg.explore()`

</div>

A queue is named by its name, URL, ARN or console link; a topic by its name or ARN; a rule by its name, `"bus/rule"` (`"shop/order-placed"`) or ARN.

??? more "How to read a report"

    Every report puts the answer first: cards with the numbers that matter, then the findings, warnings first, each a bold headline with why it matters and what to do underneath. The tables of detail come after, and **Next** at the bottom lists two or three commands with the arguments filled in. One click on a command selects all of it, ready to copy.

## Everything at once { #overview }

`overview()` lists every queue, topic and EventBridge rule in the region with what waits, what failed and what it costs over the last 7 days, the things that need attention first.
{ .gist }

```python
msg.overview()              # or overview(days=30)
```

The queues table puts dead-letter queues holding messages first, then the fullest queues. **Oldest** is the age of the oldest message waiting, marked when it's past half the queue's retention, because a message is deleted unread when it reaches it. The topics and rules tables put the ones with failed deliveries first, and a rule's pattern reads as a sentence: "OrderPlaced events from shop.orders where detail.total > 100".

When a queue's or function's policy keeps a topic or rule out, **Let them in** shows the command that lets it in: for a queue, its whole policy with a statement added for each sender (SetQueueAttributes replaces the policy, so the command keeps what's there).

!!! tip "Worth knowing"

    SNS and EventBridge can't use the AWS managed key `alias/aws/sqs`. A queue encrypted with it silently drops everything they send it, whatever its policy says. The findings say so; switch the queue to SQS-managed encryption or a key of your own.

## Follow a message { #follow }

`follow()` draws the way a message takes from a topic, queue, rule or bus: what sends to it, then every subscription, target, queue, reader and dead-letter queue downstream, each with its numbers and what's wrong there.
{ .gist }

```python
msg.follow("orders-events")        # a topic
msg.follow("orders")               # a queue: what sends to it, who reads it, where failures go
msg.follow("shop/order-placed")    # a rule, or "shop" for the whole bus
```

![follow('orders-events'): the topic, then a tree of its five subscriptions: shipping-updates with its filter, its reader shipping-notifier and its dead-letter queue, partner-webhooks and audit-events marked because their policy keeps the topic out, an HTTPS endpoint and the fraud-check function](images/msg-follow-light.webp#only-light){ width="984" height="1116" loading=lazy }
![follow('orders-events'): the topic, then a tree of its five subscriptions: shipping-updates with its filter, its reader shipping-notifier and its dead-letter queue, partner-webhooks and audit-events marked because their policy keeps the topic out, an HTTPS endpoint and the fraud-check function](images/msg-follow-dark.webp#only-dark){ width="984" height="1116" loading=lazy }
/// caption
`msg.follow("orders-events")`: two of the five places it delivers to keep it out.
///

**How it gets there** says what carries a message to each hop: a subscription and its filter in plain English, a rule's target and its retries, a Lambda function reading a queue, or a queue's dead-letter queue after so many failed receives. **Problem** marks where messages get lost: a policy or key that keeps the sender out, a subscription nobody confirmed, a disabled rule or trigger, a function that no longer exists, a dead-letter queue holding messages.

## Dead-letter queues { #dead-letters }

`dead_letters()` lists every dead-letter queue, the fullest first: how many failed messages each holds, how old the oldest is, what it catches failures from, and the command that moves them back.
{ .gist }

```python
msg.dead_letters()
```

A dead-letter queue catches what a queue couldn't process after so many receives, what an SNS subscription couldn't deliver, or what an EventBridge target couldn't take. For a queue's failures, `aws sqs start-message-move-task --source-arn <dlq-arn>` moves the messages back to the queue they came from; run it once the cause is fixed, or they fail again.

!!! tip "Worth knowing"

    A message's age counts from when it was first sent, not from when it reached the dead-letter queue. A dead-letter queue that keeps messages no longer than its source can delete a failure before anyone looks. The findings say when, with the command that keeps them 14 days.

??? more "Why it never shows what's in a queue"

    The only way to read a message is `ReceiveMessage`, and receiving changes the message: it's hidden from its real readers for the visibility timeout, and the receive counts as a try towards its dead-letter queue. On a FIFO queue it also holds up every message behind it in its group. So even a look can push a message into the dead-letter queue, and this module never does it. It shows counts, ages and settings, from `GetQueueAttributes` and CloudWatch. Why a message failed is in its reader's logs: for a Lambda function, `LambdaView().errors("orders-etl")`.

## One queue, topic or rule { #one }

`queue_info()`, `topic_info()` and `rule_info()` show one of them in full, with its settings in plain English, a line per day and what to fix.
{ .gist }

```python
msg.queue_info("orders")
msg.topic_info("orders-events")
msg.rule_info("shop/order-placed")
```

![queue_info('orders'): cards with 15 waiting, 10 in flight, 31,000 in and 30,850 done over 7 days and 42 dead letters, findings that orders-dlq holds 42 failed messages, then its settings explained, what sends to it and the orders-etl function that reads it](images/msg-queue-info-light.webp#only-light){ width="984" height="1617" loading=lazy }
![queue_info('orders'): cards with 15 waiting, 10 in flight, 31,000 in and 30,850 done over 7 days and 42 dead letters, findings that orders-dlq holds 42 failed messages, then its settings explained, what sends to it and the orders-etl function that reads it](images/msg-queue-info-dark.webp#only-dark){ width="984" height="1617" loading=lazy }
/// caption
`msg.queue_info("orders")`: its dead-letter queue holds 42 failures, and its visibility timeout is short for its reader.
///

A queue's report explains each setting (the visibility timeout, retention, receive wait, its dead-letter queue), lists the topics and rules that send to it and whether its policy lets them, and the Lambda functions that read it. It warns when the visibility timeout is shorter than six times the function's timeout (AWS's advice: otherwise a message comes back while it's still being worked on), when messages wait and nothing reads them, when the queue falls behind, and when empty receives cost money that long polling would save.

A topic's report lists its subscriptions with their filters, whether each is confirmed, its dead-letter queue and whether the queue or function lets the topic in. A rule's report says which events it matches, and its targets with their retries and dead-letter queues; a rule that matched nothing gets the `aws events test-event-pattern` command to check an event against it.

## Explore by clicking { #explorer }

`explore()` opens a window with every queue, topic and rule, the failed messages first; click one to see it in full, follow its messages on a map, or watch a queue drain.
{ .gist }

```python
msg.explore()                              # a dead-letter queue holding messages first, else the most problems
msg.explore("orders-events", tab="map")    # straight to where a topic's messages go
msg.explore("email-jobs", tab="live")      # watch a queue's backlog
```

![The messaging explorer's Everything tab: the region's findings, then a search box, chips and every queue, topic and rule with what came in, what failed, what waits and its problems, under column headers that sort](images/msg-explorer-light.webp#only-light){ width="984" height="866" loading=lazy }
![The messaging explorer's Everything tab: the region's findings, then a search box, chips and every queue, topic and rule with what came in, what failed, what waits and its problems, under column headers that sort](images/msg-explorer-dark.webp#only-dark){ width="984" height="866" loading=lazy }
/// caption
`msg.explore()`: every queue, topic and rule, the ones that need attention first; search them, pick a chip or click a column to sort.
///

The field at the top switches to another queue, topic or rule, and its cards say how that one is doing. **Overview** is its report, with the commands that fix what's wrong. The **Map** tab is `follow()` as lines you click: a queue, topic or rule on the way opens there, and the map follows it.

![The Map tab: orders-events at the top, then its subscriptions as a tree, shipping-updates with its reader and dead-letter queue, partner-webhooks and audit-events marked in red because their policy keeps the topic out](images/msg-explorer-map-light.webp#only-light){ width="984" height="866" loading=lazy }
![The Map tab: orders-events at the top, then its subscriptions as a tree, shipping-updates with its reader and dead-letter queue, partner-webhooks and audit-events marked in red because their policy keeps the topic out](images/msg-explorer-map-dark.webp#only-dark){ width="984" height="866" loading=lazy }
/// caption
The Map tab: where orders-events' messages go, and the two queues that keep them out.
///

**Dead letters** lists every dead-letter queue, the fullest first, with what it catches failures from; click one to open it.

![The Dead letters tab: cards with two dead-letter queues, one holding 42 messages, the oldest 2.5 days old; the findings that orders-dlq holds 42 failed messages and keeps them no longer than orders, with the commands; then a line per dead-letter queue and what it catches failures from](images/msg-explorer-dead-light.webp#only-light){ width="984" height="866" loading=lazy }
![The Dead letters tab: cards with two dead-letter queues, one holding 42 messages, the oldest 2.5 days old; the findings that orders-dlq holds 42 failed messages and keeps them no longer than orders, with the commands; then a line per dead-letter queue and what it catches failures from](images/msg-explorer-dead-dark.webp#only-dark){ width="984" height="866" loading=lazy }
/// caption
The Dead letters tab: orders-dlq holds 42 failed orders, and the command that moves them back once the cause is fixed.
///

**Live** looks at the queue open (else the fullest few) every 5 seconds while its tab is open: how many messages wait, a bar per look, and which way the backlog is going, with when it would be empty.

![The Live tab: email-jobs draining 40 messages a minute, empty in about 2.4 minutes, a bar per look getting shorter, and 96 waiting now, 116 at the first look 30 seconds before](images/msg-explorer-live-light.webp#only-light){ width="984" height="866" loading=lazy }
![The Live tab: email-jobs draining 40 messages a minute, empty in about 2.4 minutes, a bar per look getting shorter, and 96 waiting now, 116 at the first look 30 seconds before](images/msg-explorer-live-dark.webp#only-dark){ width="984" height="866" loading=lazy }
/// caption
Live: email-jobs draining after its reader was turned back on.
///

!!! tip "Worth knowing"

    Each look Live takes is one `GetQueueAttributes` request per queue it watches, billed like any SQS request ($0.40 per million). The line above the cards says what an hour of watching costs, and Live stops by itself after 10 minutes, or when you leave its tab.

??? more "How the window reads"

    Everything is read once, off the notebook's event loop, so a click returns at once: the queues, topics, rules and their CloudWatch numbers first, then the one you open, in full. The Map and Dead letters tabs are drawn from what's read, with no more requests. Pick another one while one is still being read and the first one's result is dropped when it comes back. Outside Jupyter, `explore()` shows the reports instead.

<p class="part"><strong>That's the guide.</strong> Everything below is reference for when you need it: the data in Python, cost, permissions, troubleshooting and every command.</p>

## Use the data in Python { #python }

`msg.core` is the `MessagingAnalyzer`. Every report has a data method on it.

```python
core = msg.core

ov = core.overview(days=7)                  # Overview: .queues, .topics, .buses, .rules, .queue_numbers, .errors
ov.queues_df()                              # every queue and its numbers as a DataFrame
report = core.queue_report("orders")        # QueueReport: .queue, .numbers, .dlq, .feeders
core.topic_report("orders-events")          # TopicReport: .topic (.subscriptions), .numbers, .queues, .functions
core.rule_report("shop/order-placed")       # RuleReport: .rule (.targets, .pattern), .numbers
core.dead_letters()                         # DeadLetter: .queue, .numbers, .catches
core.follow("orders-events").hops           # Hop: .kind, .name, .via, .numbers, .problem
```

The analysis functions make no AWS calls, so they work on what you already have:

```python
from aws_analyzer.messaging import allows, describe_pattern

describe_pattern({"source": ["shop.orders"], "detail-type": ["OrderPlaced"]})   # 'OrderPlaced events from shop.orders'
allows(queue_policy, service="sns.amazonaws.com", action="sqs:SendMessage", source_arn=topic_arn)
```

## Cost { #cost }

Costs are estimates at us-east-1 list prices (`MESSAGING_PRICES`, the first tier after the free tier): SQS $0.40 per million requests ($0.50 FIFO), SNS $0.50 per million messages published plus $0.60 per million HTTPS and $20 per million email deliveries (deliveries to SQS and Lambda are free). A queue's requests are every message sent, received and deleted plus every empty receive, which is the most it can be: batches of up to 10 make it fewer. Pass your own prices with `MessagingAnalyzer(prices={"sqs_request": 0.00000045})`.

`overview()` reads 6 CloudWatch metrics per queue and per rule and 6 per topic ($0.01 per 1,000) and says how many; reading queues, topics and rules costs nothing.

## Permissions { #permissions }

Everything is read-only. Anything the notebook's role can't read shows up as a note instead of an error. This policy covers every command:

```json title="IAM policy"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadQueuesTopicsRules",
      "Effect": "Allow",
      "Action": [
        "sqs:ListQueues", "sqs:GetQueueAttributes", "sqs:GetQueueUrl", "sqs:ListDeadLetterSourceQueues",
        "sns:ListTopics", "sns:GetTopicAttributes", "sns:ListSubscriptions", "sns:ListSubscriptionsByTopic",
        "sns:GetSubscriptionAttributes",
        "events:ListEventBuses", "events:ListRules", "events:DescribeRule", "events:ListTargetsByRule",
        "events:ListRuleNamesByTarget", "events:ListArchives"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ReadNumbersAndReaders",
      "Effect": "Allow",
      "Action": [
        "cloudwatch:GetMetricData", "lambda:ListEventSourceMappings", "lambda:GetFunctionConfiguration",
        "lambda:GetPolicy"
      ],
      "Resource": "*"
    }
  ]
}
```

There's no `sqs:ReceiveMessage`: nothing here reads, sends, moves or deletes a message.

## Troubleshooting { #troubleshooting }

??? question "“No queue named …”"

    Names are case-sensitive and queues are regional. The note lists the closest names; `overview()` lists them all. Use `MessagingView(MessagingAnalyzer(region="eu-west-1"))` for another region.

??? question "A topic's deliveries fail but every subscriber says “yes”"

    The policy check only reads queues and functions in this account and region, and a condition it can't judge (an account, an organization) counts as met. Turn on delivery status logging (the finding shows the command) and CloudWatch Logs says why each delivery failed.

??? question "The numbers are all “?”"

    CloudWatch couldn't be read: the note says why. `cloudwatch:GetMetricData` is what's needed.

## Command reference { #reference }

Every `MessagingView` command. `msg.help()` prints the same list grouped by task, and `msg.help("follow")` shows one command's full description.

<div class="ref" markdown>

| Command | What it shows |
|---|---|
| `overview(days=7)` | Every queue, topic and rule: what waits, dead letters, failed deliveries, cost, and what needs attention |
| `follow(name, days=7)` | Where a message goes from a topic, queue, rule or bus, hop by hop, and where it gets lost |
| `dead_letters(days=7)` | Every dead-letter queue: its failed messages, what it catches them from, and the command that moves them back |
| `queue_info(name, days=7)` | One queue: backlog, settings in plain English, readers, senders and its dead-letter queue |
| `topic_info(name, days=7)` | One topic: subscriptions, filters, failed deliveries, and which subscribers keep it out |
| `rule_info(name, bus=None, days=7)` | One EventBridge rule: what it matches, its targets, failed deliveries |
| `explore(where=None, tab=None, days=7, height=None)` | The explorer window: everything, one in full, its map, the dead-letter queues and Live |
| `help(command=None)` | This list, grouped by task; `help("name")` shows one command in full |

</div>
