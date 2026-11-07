"""
The pure analysis of queues, topics and rules: parsing what AWS returns, reading policies and event patterns, the
findings for each, what things cost, and follow()'s map. Nothing here calls AWS.
"""

from __future__ import annotations

from .parsing import parse_consumer, parse_json, parse_queue, parse_rule, parse_subscription, parse_target, parse_topic
from .policy import (
    allows, blocks_services, lambda_permission_command, queue_policy_command, queue_policy_with, queue_statement,
)
from .words import describe_filter, describe_pattern
from .commands import (
    delivery_logging_command, enable_rule_command, move_back_command, queue_attribute_command, redrive_command,
    subscription_dlq_command, target_dlq_command,
)
from .cost import empty_receives_monthly, queue_monthly, topic_monthly
from .access import Blocked, access_fixes, feeders_of, subscriber_allowed, target_allowed
from .queues import dlq_holding, dlq_too_short, queue_findings
from .topics import topic_findings
from .rules import rule_findings
from .world import (
    dead_letter_names, dead_letters_from, overview_findings, queue_report_from, rule_report_from, rules_on,
    topic_report_from,
)
from .flow import follow_map

__all__ = [
    "Blocked", "access_fixes", "allows", "blocks_services", "dead_letter_names", "dead_letters_from",
    "delivery_logging_command", "describe_filter", "describe_pattern", "dlq_holding", "dlq_too_short",
    "empty_receives_monthly", "enable_rule_command", "feeders_of", "follow_map", "lambda_permission_command",
    "move_back_command", "overview_findings", "parse_consumer", "parse_json", "parse_queue", "parse_rule",
    "parse_subscription", "parse_target", "parse_topic", "queue_attribute_command", "queue_findings", "queue_monthly",
    "queue_policy_command", "queue_policy_with", "queue_report_from", "queue_statement", "redrive_command",
    "rule_findings", "rule_report_from", "rules_on", "subscriber_allowed", "subscription_dlq_command",
    "target_allowed", "target_dlq_command", "topic_findings", "topic_monthly", "topic_report_from",
]
