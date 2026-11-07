"""
What a queue or a topic costs a month at the rate CloudWatch saw over the window: a queue's requests, a topic's
publishes and its deliveries by protocol. Estimates: batches make a queue's requests fewer, and filters a topic's
deliveries. Pure: no AWS calls.
"""

from __future__ import annotations

from ..models import Queue, QueueNumbers, Topic, TopicNumbers
from ..prices import DAYS_PER_MONTH, DELIVERY_PRICE_KEYS


def _per_month(count: int, days: int) -> float:
    return count * DAYS_PER_MONTH / max(days, 1)


def queue_monthly(queue: Queue, numbers: QueueNumbers | None, days: int, prices: dict[str, float]) -> float | None:
    """
    A queue's monthly cost in USD: every message sent, received and deleted, and every empty receive, one request each
    (at most: a batch of up to 10 is one request), at its price per request. None without numbers.
    """
    if numbers is None:
        return None
    price = prices["sqs_fifo_request" if queue.fifo else "sqs_request"]
    return _per_month(numbers.requests, days) * price


def empty_receives_monthly(queue: Queue, numbers: QueueNumbers | None, days: int, prices: dict[str, float]) -> float:
    """
    What a queue's empty receives (polls that found nothing) cost a month.
    """
    if numbers is None:
        return 0.0
    price = prices["sqs_fifo_request" if queue.fifo else "sqs_request"]
    return _per_month(numbers.empty_receives, days) * price


def topic_monthly(topic: Topic, numbers: TopicNumbers | None, days: int, prices: dict[str, float]) -> float | None:
    """
    A topic's monthly cost in USD: each message published, plus a delivery to each confirmed subscription at its
    protocol's price (SQS and Lambda are free), as if no filter dropped any. None without numbers.
    """
    if numbers is None:
        return None
    per_message = prices["sns_publish"]
    for subscription in topic.subscriptions:
        key = DELIVERY_PRICE_KEYS.get(subscription.protocol)
        if key and not subscription.pending:
            per_message += prices[key]
    return _per_month(numbers.published, days) * per_message
