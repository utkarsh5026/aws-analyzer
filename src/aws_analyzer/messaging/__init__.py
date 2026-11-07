"""
messaging - Amazon SQS, SNS and EventBridge analysis for SageMaker / Jupyter notebooks: where did my message go?

Part of aws-analyzer: ``pip install aws-analyzer``, then ``from aws_analyzer import MessagingView``.

Requirements: boto3 (required). pandas only for DataFrames, IPython only for rich HTML output, ipywidgets for the
explorer window. All are preinstalled on SageMaker.

The package has two layers:

    MessagingAnalyzer  Pure logic. Talks to SQS, SNS, EventBridge, CloudWatch and Lambda (which functions read a queue,
                       and who may invoke one), and returns plain Python data (dataclasses). Never prints.
    MessagingView      Notebook UI. Calls MessagingAnalyzer and renders readable cards and tables (HTML in Jupyter,
                       plain text in a terminal).

It reads only. It never receives a message: receiving one hides it from its readers for the visibility timeout and
counts as a try towards its dead-letter queue, so even a look changes what happens to it. So it shows how many
messages wait, how old they are, where they go and where they fail, never what they say. Fixes are shown as the
command to run.

Quick start
-----------
    msg = MessagingView()                      # or MessagingView(MessagingAnalyzer(region="eu-west-1"))
    msg.help()                                 # every command
    msg.overview()                             # every queue, topic and rule: what waits, what failed, what it costs
    msg.follow("orders-events")                # where a message goes from a topic, queue, rule or bus, hop by hop
    msg.dead_letters()                         # the messages that failed for good, and how to move them back
    msg.queue_info("orders")                   # one queue: backlog, readers, senders, settings in plain English
    msg.topic_info("orders-events")            # one topic: subscriptions, filters, failed deliveries
    msg.rule_info("shop/order-placed")         # one EventBridge rule: what it matches, its targets, failures
    msg.explore("orders-events")               # a window to click through it all (Jupyter, ipywidgets)

    core = msg.core                            # the same analyzer, raw data
    overview = core.overview(days=7)
    overview.queues_df()                       # every queue and its numbers as a DataFrame
"""

from __future__ import annotations

from .prices import MESSAGING_PRICES
from .helpers import Ref, arn_kind, arn_name, parse_ref, queue_url, seconds_words
from .models import (
    Bus, Consumer, DeadLetter, Feeder, FunctionFacts, Hop, MessageMap, Overview, Queue, QueueNumbers, QueueReport,
    QueueSample, Redrive, Rule, RuleNumbers, RuleReport, Subscription, Target, Topic, TopicNumbers, TopicReport, Trend,
)
from .analysis import (
    Blocked, access_fixes, allows, backlog_trend, dead_letters_from, delivery_logging_command, describe_filter, describe_pattern,
    enable_rule_command, feeders_of, follow_map, lambda_permission_command, move_back_command, overview_findings,
    parse_consumer, parse_queue, parse_rule, parse_subscription, parse_target, parse_topic, queue_attribute_command,
    queue_findings, queue_monthly, queue_policy_command, queue_statement, redrive_command, rule_findings,
    subscription_dlq_command, target_dlq_command, topic_findings, topic_monthly,
)
from .analyzer import MessagingAnalyzer
from .view import MessagingView
from .explorer.window import MessagingExplorer, explore

__all__ = [
    "MESSAGING_PRICES",
    "Blocked",
    "Bus",
    "Consumer",
    "DeadLetter",
    "Feeder",
    "FunctionFacts",
    "Hop",
    "MessageMap",
    "MessagingExplorer",
    "MessagingAnalyzer",
    "MessagingView",
    "Overview",
    "Queue",
    "QueueNumbers",
    "QueueReport",
    "QueueSample",
    "Redrive",
    "Ref",
    "Rule",
    "RuleNumbers",
    "RuleReport",
    "Subscription",
    "Target",
    "Topic",
    "TopicNumbers",
    "TopicReport",
    "Trend",
    "access_fixes",
    "allows",
    "arn_kind",
    "arn_name",
    "backlog_trend",
    "dead_letters_from",
    "delivery_logging_command",
    "describe_filter",
    "describe_pattern",
    "enable_rule_command",
    "explore",
    "feeders_of",
    "follow_map",
    "lambda_permission_command",
    "move_back_command",
    "overview_findings",
    "parse_consumer",
    "parse_queue",
    "parse_ref",
    "parse_rule",
    "parse_subscription",
    "parse_target",
    "parse_topic",
    "queue_attribute_command",
    "queue_findings",
    "queue_monthly",
    "queue_policy_command",
    "queue_statement",
    "queue_url",
    "redrive_command",
    "rule_findings",
    "seconds_words",
    "subscription_dlq_command",
    "target_dlq_command",
    "topic_findings",
    "topic_monthly",
]
