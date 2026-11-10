---
title: Messaging Explorer Guide
description: "How to click through Amazon SQS queues, SNS topics and EventBridge rules in a SageMaker notebook with aws-analyzer's messaging explorer window: every queue, topic and rule, where a message goes hop by hop, the dead-letter queues, and a queue's backlog live."
---

<p class="eyebrow"><img class="aws-icon" src="images/aws/messaging.svg" alt="" width="32" height="32"> Messaging explorer <code>messaging/explorer/</code></p>

# Click through your queues, topics and rules, and follow a message

`explore()` turns a notebook cell into a window on SQS, SNS and EventBridge. The failed messages come first; click a queue, topic or rule for its report, follow its messages on a map whose hops you click, or watch a queue drain.
{ .lede }

<ul class="pills">
  <li>One install, ipywidgets already on SageMaker</li>
  <li>Read-only: never receives a message</li>
  <li>SQS, SNS and EventBridge in one map</li>
  <li>A queue's backlog, live</li>
</ul>

The examples use acme's `orders-events` topic, which delivers to four queues and a Lambda function, and the `shop` event bus, whose `order-placed` rule sends big orders to an audit queue and a fraud check. Some of those deliveries fail, and 42 orders wait in a dead-letter queue. For reports you run from code, see the [messaging guide](messaging.md).
{ .muted }

![The messaging explorer in a notebook, animated: it opens on the Everything tab, orders-dlq holding 42 failed messages first; the pointer opens orders-events and its Map tab, the subscriptions as a tree with partner-webhooks and audit-events marked because their policy keeps the topic out, clicks partner-webhooks, which the map then follows; it opens the Dead letters tab, then email-jobs and the Live tab, where the backlog drains a few messages at each look](images/msg-explorer-tour-light.webp#only-light){ width="984" height="865" loading=lazy }
![The messaging explorer in a notebook, animated: it opens on the Everything tab, orders-dlq holding 42 failed messages first; the pointer opens orders-events and its Map tab, the subscriptions as a tree with partner-webhooks and audit-events marked because their policy keeps the topic out, clicks partner-webhooks, which the map then follows; it opens the Dead letters tab, then email-jobs and the Live tab, where the backlog drains a few messages at each look](images/msg-explorer-tour-dark.webp#only-dark){ width="984" height="865" loading=lazy }
/// caption
**`msg.explore()`**: from what failed, to where a topic's messages go, to a queue draining as you watch.
///

## Watch it run { #video }

Four steps in a notebook, from everything in the region to a queue draining: each command typed and run, and the window it shows, with what to notice. It plays as you scroll to it; click it to pause, or pick a chapter to jump to a step.
{ .gist }

<div class="aa-tour" data-tour="messaging_explorer">
  <div class="tour-screen"></div>
  <div class="tour-controls"></div>
  <div class="tour-chapters"></div>
  <p class="tour-links"><a href="tour.html?service=messaging_explorer">Full screen</a> · <a href="videos/messaging_explorer-tour-light.mp4" data-tour-mp4 download>Download as MP4</a>, for Slack or a slide (light or dark, as this page is)</p>
</div>

## Open it { #open }

<div class="steps" markdown>

1. **Install it** in a notebook cell:

    ```bash
    %pip install aws-analyzer
    ```

2. **Open the window.** It uses the notebook's IAM role and region, so there's nothing to configure.

    ```python
    from aws_analyzer import MessagingView

    msg = MessagingView()
    x = msg.explore()                # a dead-letter queue holding messages first, else the most problems
    ```

</div>

??? more "Straight to a queue, topic or rule, or a tab"

    ```python
    msg.explore()                              # a dead-letter queue holding messages first, else the most problems
    msg.explore("orders-events", tab="map")    # straight to where a topic's messages go
    msg.explore("email-jobs", tab="live")      # watch a queue's backlog
    ```

    A queue is named by its name, URL or ARN, a rule as `"bus/rule"`. `tab=` is one of `"overview"`, `"map"`, `"dead"` and `"live"`, and `days=` the window of the numbers (7 by default). `region=`, `profile=` and `height=` work as for the reports. The window needs `ipywidgets`, which SageMaker notebooks already have; without it, or outside Jupyter, `explore()` shows the [reports](messaging.md) instead.

## Start here { #start }

Four things to try in your first minute. Nothing you click receives, sends or deletes a message.
{ .gist }

<div class="quick" markdown>

What's wrong
{ .qgroup }

-   [What needs attention?](#everything) <span class="how">Read the Everything tab, problems first</span>
-   [Which failed messages are waiting?](#dead) <span class="how">Open the Dead letters tab</span>

Follow it
{ .qgroup }

-   [Where did my message go?](#map) <span class="how">Click the topic or rule, then Map</span>
-   [Is this queue draining?](#live) <span class="how">Click the queue, then Live</span>

</div>

## Everything at once { #everything }

The **Everything** tab is every queue, topic and EventBridge rule in the region, a line each, the ones that need attention first.
{ .gist }

![The messaging explorer's Everything tab: the region's findings, then a search box, chips and every queue, topic and rule with what came in, what failed, what waits and its problems, under column headers that sort](images/msg-explorer-light.webp#only-light){ width="984" height="866" loading=lazy }
![The messaging explorer's Everything tab: the region's findings, then a search box, chips and every queue, topic and rule with what came in, what failed, what waits and its problems, under column headers that sort](images/msg-explorer-dark.webp#only-dark){ width="984" height="866" loading=lazy }
/// caption
`msg.explore()`: every queue, topic and rule, the ones that need attention first; search them, pick a chip or click a column to sort.
///

Each line has what came in, what failed, what waits and its problems. Search by name, click a chip for only the ones with **Problems**, the **Queues**, the **Dead-letter queues**, the **Topics** or the **Rules**, and click a column's header to sort by it. A dead-letter queue holding messages sorts first.

The field at the top switches to another queue, topic or rule, and its cards say how that one is doing. **Overview** is its report, with the commands that fix what's wrong. The **Map** tab is `follow()` as lines you click: a queue, topic or rule on the way opens there, and the map follows it.

## Where its messages go { #map }

The **Map** tab follows a queue's, topic's or rule's messages hop by hop, to the function that reads them; click a hop to open it.
{ .gist }

![The Map tab: orders-events at the top, then its subscriptions as a tree, shipping-updates with its reader and dead-letter queue, partner-webhooks and audit-events marked in red because their policy keeps the topic out](images/msg-explorer-map-light.webp#only-light){ width="984" height="866" loading=lazy }
![The Map tab: orders-events at the top, then its subscriptions as a tree, shipping-updates with its reader and dead-letter queue, partner-webhooks and audit-events marked in red because their policy keeps the topic out](images/msg-explorer-map-dark.webp#only-dark){ width="984" height="866" loading=lazy }
/// caption
The Map tab: where orders-events' messages go, and the two queues that keep them out.
///

It's [`follow()`](messaging.md#follow) as lines you click: a hop marked red is a delivery that fails, or a queue whose policy keeps the sender out.

## Dead-letter queues { #dead }

The **Dead letters** tab lists every dead-letter queue, the fullest first, with what it catches failures from; click one to open it.
{ .gist }

![The Dead letters tab: cards with two dead-letter queues, one holding 42 messages, the oldest 2.5 days old; the findings that orders-dlq holds 42 failed messages and keeps them no longer than orders, with the commands; then a line per dead-letter queue and what it catches failures from](images/msg-explorer-dead-light.webp#only-light){ width="984" height="866" loading=lazy }
![The Dead letters tab: cards with two dead-letter queues, one holding 42 messages, the oldest 2.5 days old; the findings that orders-dlq holds 42 failed messages and keeps them no longer than orders, with the commands; then a line per dead-letter queue and what it catches failures from](images/msg-explorer-dead-dark.webp#only-dark){ width="984" height="866" loading=lazy }
/// caption
The Dead letters tab: orders-dlq holds 42 failed orders, and the command that moves them back once the cause is fixed.
///

## Live { #live }

The **Live** tab looks at a queue every 5 seconds while it's open: how many messages wait, and when the backlog would be empty.
{ .gist }

**Live** looks at the queue open (else the fullest few) every 5 seconds while its tab is open: how many messages wait, a bar per look, and which way the backlog is going, with when it would be empty.

![The Live tab: email-jobs draining 40 messages a minute, empty in about 2.4 minutes, a bar per look getting shorter, and 96 waiting now, 116 at the first look 30 seconds before](images/msg-explorer-live-light.webp#only-light){ width="984" height="866" loading=lazy }
![The Live tab: email-jobs draining 40 messages a minute, empty in about 2.4 minutes, a bar per look getting shorter, and 96 waiting now, 116 at the first look 30 seconds before](images/msg-explorer-live-dark.webp#only-dark){ width="984" height="866" loading=lazy }
/// caption
Live: email-jobs draining after its reader was turned back on.
///

!!! tip "Worth knowing"

    Each look Live takes is one `GetQueueAttributes` request per queue it watches, billed like any SQS request ($0.40 per million). The line above the cards says what an hour of watching costs, and Live stops by itself after 10 minutes, or when you leave its tab.

<p class="part"><strong>That's the guide.</strong> Everything below is reference for when you need it: how the window reads, and every command.</p>

## From code { #from-code }

The window is an object you can drive: what a click does, a command does too.
{ .gist }

```python
x = msg.explore()
x.open("orders-events", tab="map")   # another queue, topic, rule ('bus/rule') or bus, on a tab
x.watch()                            # the Live tab, on the queue open (else the fullest few)
x.refresh()                          # read everything again (the ↻ button)
```

??? more "How the window reads"

    Everything is read once, off the notebook's event loop, so a click returns at once: the queues, topics, rules and their CloudWatch numbers first, then the one you open, in full. The Map and Dead letters tabs are drawn from what's read, with no more requests. Pick another one while one is still being read and the first one's result is dropped when it comes back. Outside Jupyter, `explore()` shows the reports instead.

## Command reference { #reference }

<div class="ref" markdown>

| Command | What it does |
|---|---|
| `explore(where=None, tab=None, region=None, profile=None, days=7, height=None)` | Opens the window and returns it; `msg.explore(...)` on a `MessagingView` takes the same |
| `x.open(where, tab=None)` | Shows another queue (name, URL or ARN), topic, rule (`"bus/rule"`) or bus, on `tab` when one is named |
| `x.watch()` | Opens the Live tab and watches the queue open, else the fullest few |
| `x.refresh()` | Reads everything again (the ↻ button) |

</div>
