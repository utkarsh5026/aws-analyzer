"""
Who may deliver where: the SNS subscriptions and EventBridge rules that send to a queue (feeders_of), whether each
receiver's policy lets its sender in (subscriber_allowed, target_allowed), and the commands that would let a blocked
one in (access_fixes). Pure: no AWS calls.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..helpers import arn_service, function_of
from ..models import Feeder, FunctionFacts, Queue, Rule, Subscription, Target, Topic
from .policy import (
    EVENTS_PRINCIPAL, INVOKE_ACTION, SEND_ACTION, SNS_PRINCIPAL, allows, blocks_services, lambda_permission_command,
    queue_policy_command, queue_policy_with, queue_statement,
)
from .words import describe_filter, describe_pattern


@dataclass
class Blocked:
    """
    A sender a receiver's policy keeps out: who sends (sender_arn, through `service`), who receives, and the command
    that lets it in. note says what else keeps it out (a queue's key).
    """

    sender: str
    receiver: str
    receiver_kind: str
    command: str
    note: str = ""


def _receiver_allowed(receiver_arn: str, *, service: str, source_arn: str, queues: dict[str, Queue],
                      functions: dict[str, FunctionFacts]) -> bool | None:
    """
    Whether a queue's or function's policy lets a topic or rule deliver to it; None when it couldn't be told (another
    account's, or a policy that couldn't be read).
    """
    kind = arn_service(receiver_arn)
    if kind == "sqs" and receiver_arn in queues:
        queue = queues[receiver_arn]
        if "policy" in queue.errors:
            return None
        return allows(queue.policy, service=service, action=SEND_ACTION, source_arn=source_arn)
    if kind == "lambda":
        facts = functions.get(function_of(receiver_arn))
        if facts is None or not facts.exists or facts.error:
            return None
        return allows(facts.policy, service=service, action=INVOKE_ACTION, source_arn=source_arn)
    return None


def subscriber_allowed(subscription: Subscription, *, queues: dict[str, Queue],
                       functions: dict[str, FunctionFacts]) -> bool | None:
    """
    Whether a subscription's queue or function lets its topic deliver (None when it couldn't be told or isn't a
    queue or function).
    """
    return _receiver_allowed(subscription.endpoint, service=SNS_PRINCIPAL, source_arn=subscription.topic_arn,
                             queues=queues, functions=functions)


def target_allowed(rule: Rule, target: Target, *, queues: dict[str, Queue],
                   functions: dict[str, FunctionFacts]) -> bool | None:
    """
    Whether a rule's target queue or function lets the rule deliver. A target with a role is reached through the
    role, which this can't judge (None).
    """
    if target.role_arn:
        return None
    return _receiver_allowed(target.arn, service=EVENTS_PRINCIPAL, source_arn=rule.arn, queues=queues,
                             functions=functions)


def feeders_of(queue: Queue, topics: list[Topic], rules: list[Rule]) -> list[Feeder]:
    """
    What sends messages into a queue: the topics with a subscription to it and the rules with it as a target, each
    with what it lets through and whether the queue's policy lets it in.
    """
    queues = {queue.arn: queue}
    feeders = []
    for topic in topics:
        for subscription in topic.subscriptions:
            if subscription.endpoint != queue.arn:
                continue
            feeders.append(Feeder("SNS topic", topic.name, topic.arn,
                                  describe_filter(subscription.filter_policy, subscription.filter_scope),
                                  subscriber_allowed(subscription, queues=queues, functions={})))
    for rule in rules:
        for target in rule.targets:
            if target.arn == queue.arn:
                feeders.append(Feeder("EventBridge rule", rule.label, rule.arn, describe_pattern(rule.pattern),
                                      target_allowed(rule, target, queues=queues, functions={})))
    return feeders


def blocked_finding(sender: str, receiver_kind: str, receiver: str, service: str) -> tuple[str, str]:
    """
    The warning for a sender a receiver's policy keeps out, worded the same from either side, so a report that sees
    it from both shows it once.
    """
    return ("warn", f"{sender} can't deliver to {receiver_kind} {receiver}: its policy doesn't let {service} in from "
                    f"{sender}, so every message fails there. The command under 'Let them in' fixes it.")


@dataclass
class _Refusal:
    sender: str
    source_arn: str
    service: str
    receiver_arn: str
    receiver: str
    receiver_kind: str


def _refusals(topics: list[Topic], rules: list[Rule], queues: dict[str, Queue],
              functions: dict[str, FunctionFacts]) -> list[_Refusal]:
    refusals = []
    for topic in topics:
        for subscription in topic.subscriptions:
            if subscriber_allowed(subscription, queues=queues, functions=functions) is False:
                refusals.append(_Refusal(topic.name, topic.arn, SNS_PRINCIPAL, subscription.endpoint,
                                         subscription.target, subscription.kind))
    for rule in rules:
        for target in rule.targets:
            if target_allowed(rule, target, queues=queues, functions=functions) is False:
                refusals.append(_Refusal(rule.label, rule.arn, EVENTS_PRINCIPAL, target.arn, target.name,
                                         target.kind))
    return refusals


def _queue_fix(queue: Queue, refusals: list[_Refusal]) -> Blocked:
    """
    One command for every sender a queue keeps out: SetQueueAttributes replaces the whole policy, so the statements go
    in together, after the ones already there.
    """
    policy = queue.policy
    for refusal in refusals:
        policy = queue_policy_with(policy, queue_statement(queue.arn, service=refusal.service,
                                                           source_arn=refusal.source_arn))
    senders = " and ".join(dict.fromkeys(r.sender for r in refusals))
    note = "its key alias/aws/sqs keeps them out too: change that as well" if blocks_services(queue.kms_key) else ""
    return Blocked(senders, queue.name, "SQS queue", queue_policy_command(queue.url, policy), note)


def access_fixes(*, topics: list[Topic], rules: list[Rule], queues: dict[str, Queue],
                 functions: dict[str, FunctionFacts]) -> list[Blocked]:
    """
    Every receiver whose policy keeps a subscription's topic or a rule out, with the command that lets them in: one
    per queue (its whole policy, with a statement per sender), one per function and sender (aws lambda
    add-permission).
    """
    by_queue: dict[str, list[_Refusal]] = {}
    blocked = []
    for refusal in _refusals(topics, rules, queues, functions):
        if arn_service(refusal.receiver_arn) == "sqs":
            by_queue.setdefault(refusal.receiver_arn, []).append(refusal)
            continue
        command = lambda_permission_command(function_of(refusal.receiver_arn), service=refusal.service,
                                            source_arn=refusal.source_arn)
        blocked.append(Blocked(refusal.sender, refusal.receiver, refusal.receiver_kind, command))
    return [_queue_fix(queues[arn], refusals) for arn, refusals in by_queue.items()] + blocked


def locked_queues(arns: list[str], queues: dict[str, Queue]) -> list[Queue]:
    """
    The queues among `arns` that SNS and EventBridge can't send to because the AWS managed key encrypts them.
    """
    found = [queues[arn] for arn in dict.fromkeys(arns) if arn in queues]
    return [queue for queue in found if blocks_services(queue.kms_key)]
