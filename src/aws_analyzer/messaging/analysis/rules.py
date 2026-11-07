"""
What needs attention about one EventBridge rule: events it couldn't deliver (and whether anything kept them),
targets whose policy keeps it out, a rule that's off, one that matches nothing, and targets without a dead-letter
queue. Each finding says why it matters and what to run. Pure: no AWS calls.
"""

from __future__ import annotations

import json
import shlex

from ..._kit.fmt import _plural
from ..helpers import agree, function_of
from ..models import RuleReport
from .access import blocked_finding, locked_queues, target_allowed
from .commands import enable_rule_command, target_dlq_command
from .words import describe_pattern


Finding = tuple[str, str]


def _disabled(report: RuleReport) -> Finding | None:
    rule = report.rule
    if rule.is_enabled or not rule.targets:
        return None
    return ("warn", f"{rule.label} is disabled, so it sends nothing to its {_plural(len(rule.targets), 'target')}. "
                    f"If that's not on purpose, turn it on: {enable_rule_command(rule.arn)}")


def _failed_events(report: RuleReport) -> Finding | None:
    numbers, rule = report.numbers, report.rule
    if numbers is None or not numbers.failed:
        return None
    lost = [t for t in rule.targets if not t.dlq_arn]
    if lost:
        first = lost[0]
        kept = (f" {_plural(len(lost), 'target')} ({', '.join(t.name for t in lost[:3])}) "
                f"{agree(len(lost), 'has', 'have')} no dead-letter queue, so those events are gone. Give each one: "
                f"{target_dlq_command(rule.arn, first.id, first.arn, '<dlq-arn>')}")
    else:
        kept = f" They went to the targets' dead-letter queues ({numbers.to_dlq:,} sent there): dead_letters()."
    return ("warn", f"{numbers.failed:,} deliveries from {rule.label} failed for good in the last "
                    f"{_plural(report.days, 'day')}, after EventBridge's retries.{kept}")


def _dlq_failed(report: RuleReport) -> Finding | None:
    numbers = report.numbers
    if numbers is None or not numbers.dlq_failed:
        return None
    return ("warn", f"{numbers.dlq_failed:,} events {report.rule.label} couldn't deliver couldn't be put in the "
                    "dead-letter queue either, so they're gone: its policy must let events.amazonaws.com send it "
                    "messages.")


def _blocked_targets(report: RuleReport) -> list[Finding]:
    findings = []
    for target in report.rule.targets:
        if target_allowed(report.rule, target, queues=report.queues, functions=report.functions) is not False:
            continue
        findings.append(blocked_finding(report.rule.label, target.kind, target.name, "events.amazonaws.com"))
    return findings


def _missing_targets(report: RuleReport) -> list[Finding]:
    findings = []
    for target in report.rule.targets:
        facts = report.functions.get(function_of(target.arn))
        if facts is not None and not facts.exists:
            findings.append(("warn", f"{report.rule.label} sends to the Lambda function {target.name}, which doesn't "
                                     "exist any more: every event fails there."))
    return findings


def _locked_targets(report: RuleReport) -> list[Finding]:
    arns = [t.arn for t in report.rule.targets]
    return [("warn", f"{report.rule.label} can't deliver to {queue.name}: the queue is encrypted with the AWS managed "
                     "key alias/aws/sqs, which EventBridge can't use, so every event fails there. Switch the queue to "
                     "SQS-managed encryption, or a key of your own whose policy lets events.amazonaws.com use it.")
            for queue in locked_queues(arns, report.queues)]


def _no_targets(report: RuleReport) -> Finding | None:
    rule = report.rule
    if rule.targets or not rule.is_enabled:
        return None
    return ("warn", f"{rule.label} has no targets: the events it matches go nowhere. Add the queue, function or "
                    "topic that should get them.")


def _never_matched(report: RuleReport) -> Finding | None:
    numbers, rule = report.numbers, report.rule
    if numbers is None or numbers.matched or rule.pattern is None or not rule.is_enabled:
        return None
    sample = shlex.quote(json.dumps(rule.pattern, separators=(",", ":")))
    return ("info", f"{rule.label} matched no event in the last {_plural(report.days, 'day')}. It waits for "
                    f"{describe_pattern(rule.pattern)}: if events should match, check one against it: aws events "
                    f"test-event-pattern --event-pattern {sample} --event file://event.json")


def _throttled(report: RuleReport) -> Finding | None:
    numbers = report.numbers
    if numbers is None or not numbers.throttled:
        return None
    return ("info", f"{report.rule.label} was throttled {numbers.throttled:,} times: EventBridge retries, so events "
                    "arrive late. A Lambda target's reserved concurrency, or the account's invocation quota, is "
                    "the usual limit.")


def _targets_without_dlq(report: RuleReport) -> Finding | None:
    numbers, rule = report.numbers, report.rule
    lost = [t for t in rule.targets if not t.dlq_arn]
    if not lost or not rule.is_enabled or (numbers is not None and numbers.failed):
        return None
    first = lost[0]
    return ("info", f"{_plural(len(lost), 'target')} of {rule.label} {agree(len(lost), 'has', 'have')} no "
                    "dead-letter queue: an event EventBridge "
                    "can't deliver after its retries (24 hours by default) is dropped. Give each one: "
                    f"{target_dlq_command(rule.arn, first.id, first.arn, '<dlq-arn>')}")


def rule_findings(report: RuleReport) -> list[Finding]:
    """
    What needs attention about one EventBridge rule, warnings first: events it couldn't deliver and where they went,
    targets that keep it out, a disabled rule, a rule without targets or that matched nothing.
    """
    candidates = [
        _disabled(report), _failed_events(report), _dlq_failed(report), *_blocked_targets(report), *_missing_targets(report),
        *_locked_targets(report), _no_targets(report), _never_matched(report), _throttled(report), _targets_without_dlq(report),
    ]
    findings = [f for f in candidates if f]
    return [f for f in findings if f[0] == "warn"] + [f for f in findings if f[0] != "warn"]
