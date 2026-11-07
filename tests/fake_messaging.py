"""
acme's queues, topics and EventBridge rules, for the tests, the demo and the guide's figures, made in moto with
their CloudWatch numbers (moto keeps AWS/SQS, AWS/SNS and AWS/Events metrics put with PutMetricData) and the Lambda
functions that read the queues or get the events.

    orders            read by orders-etl (60 s timeout, visibility 90 s), its failures go to orders-dlq after 3 tries
    orders-dlq        42 failed messages, kept 4 days, no longer than orders
    email-jobs        120 messages wait, nothing reads it, the oldest 3 days old of 4
    shipping-updates  fine: subscribed to orders-events with a filter, long polling, its own dead-letter queue
    partner-webhooks  subscribed to orders-events, but its policy keeps SNS out
    audit-events      subscribed and targeted, but encrypted with alias/aws/sqs, which SNS and EventBridge can't use
    reports-queue     polled without long polling: 900,000 empty receives a week
    invoices.fifo     a FIFO queue read by invoice-writer

    orders-events     SNS: five subscriptions, 2,310 failed deliveries, no delivery logging
    price-alerts      SNS: published to, no subscriptions
    ops-alarms        SNS: an email subscription nobody confirmed

    shop/order-placed      matches big OrderPlaced events, 410 failed deliveries, no dead-letter queue
    shop/refund-requested  disabled, its target function deleted
    nightly-report         a schedule on the default bus
    s3-upload              matches nothing in a week
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3


REGION = "us-east-1"

ACCOUNT = "123456789012"

DAYS = 7

DAY = 86_400

QUEUE_ARN = f"arn:aws:sqs:{REGION}:{ACCOUNT}:{{}}"

FUNCTION_ARN = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{{}}"

FUNCTIONS = {"orders-etl": 60, "invoice-writer": 30, "shipping-notifier": 15, "fraud-check": 10,
             "report-builder": 300}

QUEUE_TRAFFIC = {
    "orders": {"NumberOfMessagesSent": 31_000, "NumberOfMessagesReceived": 33_400, "NumberOfMessagesDeleted": 30_850,
               "NumberOfEmptyReceives": 12_000, "age": 40},
    "orders-dlq": {"NumberOfMessagesSent": 42, "age": 2.5 * DAY},
    "email-jobs": {"NumberOfMessagesSent": 120, "age": 3.1 * DAY},
    "shipping-updates": {"NumberOfMessagesSent": 9_100, "NumberOfMessagesReceived": 9_100,
                         "NumberOfMessagesDeleted": 9_100, "NumberOfEmptyReceives": 2_000, "age": 3},
    "reports-queue": {"NumberOfMessagesSent": 700, "NumberOfMessagesReceived": 700, "NumberOfMessagesDeleted": 700,
                      "NumberOfEmptyReceives": 900_000, "age": 1},
    "invoices.fifo": {"NumberOfMessagesSent": 2_400, "NumberOfMessagesReceived": 2_400,
                      "NumberOfMessagesDeleted": 2_400, "age": 2},
}

TOPIC_TRAFFIC = {
    "orders-events": {"NumberOfMessagesPublished": 18_400, "NumberOfNotificationsDelivered": 52_000,
                      "NumberOfNotificationsFailed": 2_310, "NumberOfNotificationsFilteredOut": 6_100},
    "price-alerts": {"NumberOfMessagesPublished": 3_200},
    "ops-alarms": {"NumberOfMessagesPublished": 12},
}

RULE_TRAFFIC = {
    ("shop", "order-placed"): {"TriggeredRules": 9_800, "Invocations": 19_600, "FailedInvocations": 410},
    ("default", "nightly-report"): {"TriggeredRules": 7, "Invocations": 7},
    ("default", "s3-upload"): {"TriggeredRules": 0},
}


def _zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("app.py", "def handler(event, context):\n    return event\n")
    return buffer.getvalue()


def _functions() -> None:
    iam = boto3.client("iam", region_name=REGION)
    role = iam.create_role(RoleName="acme-lambda", AssumeRolePolicyDocument="{}")["Role"]["Arn"]
    client = boto3.client("lambda", region_name=REGION)
    for name, timeout in FUNCTIONS.items():
        client.create_function(FunctionName=name, Runtime="python3.12", Role=role, Handler="app.handler",
                               Code={"ZipFile": _zip()}, Timeout=timeout)


def _allow_sns(queue: str, topic_arn: str) -> str:
    return json.dumps({"Version": "2012-10-17", "Statement": [{
        "Sid": "orders-events", "Effect": "Allow", "Principal": {"Service": "sns.amazonaws.com"},
        "Action": "sqs:SendMessage", "Resource": QUEUE_ARN.format(queue),
        "Condition": {"ArnEquals": {"aws:SourceArn": topic_arn}}}]})


def _queues(topic_arn: str) -> dict[str, str]:
    sqs = boto3.client("sqs", region_name=REGION)
    four_days, fourteen_days = str(4 * DAY), str(14 * DAY)
    redrive = json.dumps({"deadLetterTargetArn": QUEUE_ARN.format("orders-dlq"), "maxReceiveCount": "3"})
    shipping_redrive = json.dumps({"deadLetterTargetArn": QUEUE_ARN.format("shipping-dlq"), "maxReceiveCount": "5"})
    settings = {
        "orders-dlq": {"MessageRetentionPeriod": four_days},
        "orders": {"RedrivePolicy": redrive, "VisibilityTimeout": "90", "MessageRetentionPeriod": four_days},
        "email-jobs": {"MessageRetentionPeriod": four_days},
        "shipping-dlq": {"MessageRetentionPeriod": fourteen_days},
        "shipping-updates": {"RedrivePolicy": shipping_redrive, "ReceiveMessageWaitTimeSeconds": "20",
                             "VisibilityTimeout": "120", "Policy": _allow_sns("shipping-updates", topic_arn)},
        "partner-webhooks": {},
        "audit-events": {"KmsMasterKeyId": "alias/aws/sqs"},
        "reports-queue": {},
        "invoices.fifo": {"FifoQueue": "true", "VisibilityTimeout": "180"},
    }
    return {name: sqs.create_queue(QueueName=name, Attributes=attributes)["QueueUrl"]
            for name, attributes in settings.items()}


def _messages(urls: dict[str, str]) -> None:
    sqs = boto3.client("sqs", region_name=REGION)
    for name, count in (("orders-dlq", 42), ("email-jobs", 120), ("orders", 25)):
        for first in range(0, count, 10):
            entries = [{"Id": str(i), "MessageBody": json.dumps({"order": f"ord-{first + i:04d}"})}
                       for i in range(min(10, count - first))]
            sqs.send_message_batch(QueueUrl=urls[name], Entries=entries)
    sqs.receive_message(QueueUrl=urls["orders"], MaxNumberOfMessages=10, VisibilityTimeout=600)


def _mappings() -> None:
    client = boto3.client("lambda", region_name=REGION)
    for queue, function, batch in (("orders", "orders-etl", 10), ("shipping-updates", "shipping-notifier", 1),
                                   ("invoices.fifo", "invoice-writer", 10)):
        client.create_event_source_mapping(EventSourceArn=QUEUE_ARN.format(queue), FunctionName=function,
                                           BatchSize=batch)


def _mark_pending(topic_arn: str, endpoint: str) -> None:
    """
    Makes a subscription wait for confirmation, as an email one does until its link is clicked (moto confirms every
    subscription at once): AWS lists it with the ARN 'PendingConfirmation'.
    """
    from moto.sns.models import sns_backends

    for subscription in sns_backends[ACCOUNT][REGION].subscriptions.values():
        if subscription.topic.arn == topic_arn and subscription.endpoint == endpoint:
            subscription.arn = "PendingConfirmation"


def _topics() -> dict[str, str]:
    sns = boto3.client("sns", region_name=REGION)
    return {name: sns.create_topic(Name=name)["TopicArn"] for name in ("orders-events", "price-alerts", "ops-alarms")}


def _subscriptions(topics: dict[str, str]) -> None:
    sns = boto3.client("sns", region_name=REGION)
    lambda_client = boto3.client("lambda", region_name=REGION)
    orders = topics["orders-events"]
    sns.subscribe(TopicArn=orders, Protocol="sqs", Endpoint=QUEUE_ARN.format("shipping-updates"),
                  Attributes={"FilterPolicy": json.dumps({"type": ["order"], "country": [{"prefix": "eu-"}]}),
                              "RedrivePolicy": json.dumps({"deadLetterTargetArn": QUEUE_ARN.format("shipping-dlq")})})
    sns.subscribe(TopicArn=orders, Protocol="sqs", Endpoint=QUEUE_ARN.format("partner-webhooks"))
    sns.subscribe(TopicArn=orders, Protocol="sqs", Endpoint=QUEUE_ARN.format("audit-events"))
    sns.subscribe(TopicArn=orders, Protocol="https", Endpoint="https://hooks.partner.example/orders")
    sns.subscribe(TopicArn=orders, Protocol="lambda", Endpoint=FUNCTION_ARN.format("fraud-check"))
    lambda_client.add_permission(FunctionName="fraud-check", StatementId="sns-orders-events",
                                 Action="lambda:InvokeFunction", Principal="sns.amazonaws.com", SourceArn=orders)
    sns.subscribe(TopicArn=topics["ops-alarms"], Protocol="email", Endpoint="oncall@acme.example")
    _mark_pending(topics["ops-alarms"], "oncall@acme.example")


def _rules() -> None:
    events = boto3.client("events", region_name=REGION)
    lambda_client = boto3.client("lambda", region_name=REGION)
    events.create_event_bus(Name="shop")
    placed = events.put_rule(Name="order-placed", EventBusName="shop", EventPattern=json.dumps({
        "source": ["shop.orders"], "detail-type": ["OrderPlaced"], "detail": {"total": [{"numeric": [">", 100]}]}}),
        Description="Big orders go to the fraud check and the audit trail.")["RuleArn"]
    events.put_targets(Rule="order-placed", EventBusName="shop", Targets=[
        {"Id": "audit", "Arn": QUEUE_ARN.format("audit-events")},
        {"Id": "fraud", "Arn": FUNCTION_ARN.format("fraud-check"), "RetryPolicy": {"MaximumRetryAttempts": 2}}])
    lambda_client.add_permission(FunctionName="fraud-check", StatementId="events-order-placed",
                                 Action="lambda:InvokeFunction", Principal="events.amazonaws.com", SourceArn=placed)
    events.put_rule(Name="refund-requested", EventBusName="shop", State="DISABLED", EventPattern=json.dumps({
        "source": ["shop.orders"], "detail-type": ["RefundRequested"]}))
    events.put_targets(Rule="refund-requested", EventBusName="shop", Targets=[
        {"Id": "refunds", "Arn": FUNCTION_ARN.format("refunds-handler")}])
    nightly = events.put_rule(Name="nightly-report", ScheduleExpression="cron(0 2 * * ? *)")["RuleArn"]
    events.put_targets(Rule="nightly-report", Targets=[{"Id": "build", "Arn": FUNCTION_ARN.format("report-builder")}])
    lambda_client.add_permission(FunctionName="report-builder", StatementId="events-nightly",
                                 Action="lambda:InvokeFunction", Principal="events.amazonaws.com", SourceArn=nightly)
    upload = events.put_rule(Name="s3-upload", EventPattern=json.dumps({
        "source": ["aws.s3"], "detail-type": ["Object Created"],
        "detail": {"bucket": {"name": [{"prefix": "acme-raw"}]}}}))["RuleArn"]
    events.put_targets(Rule="s3-upload", Targets=[{"Id": "orders", "Arn": QUEUE_ARN.format("orders"),
                                                   "DeadLetterConfig": {"Arn": QUEUE_ARN.format("orders-dlq")}}])
    sqs = boto3.client("sqs", region_name=REGION)
    orders_url = sqs.get_queue_url(QueueName="orders")["QueueUrl"]
    statement = {"Effect": "Allow", "Principal": {"Service": "events.amazonaws.com"}, "Action": "sqs:SendMessage",
                 "Resource": QUEUE_ARN.format("orders"), "Condition": {"ArnEquals": {"aws:SourceArn": upload}}}
    sqs.set_queue_attributes(QueueUrl=orders_url, Attributes={"Policy": json.dumps({"Statement": [statement]})})


def _spread(total: float, days: int) -> list[float]:
    """
    A week's total over its days, rising towards today, so the line per day shows a trend.
    """
    weights = [1 + i / days for i in range(days)]
    return [total * w / sum(weights) for w in weights]


def _put(cloudwatch: Any, namespace: str, dims: list[dict[str, str]], counts: dict[str, float], now: datetime) -> None:
    data = []
    for metric, total in counts.items():
        if metric == "age":
            continue
        for back, value in enumerate(reversed(_spread(total, DAYS))):
            data.append({"MetricName": metric, "Dimensions": dims, "Value": round(value), "Unit": "Count",
                         "Timestamp": now - timedelta(days=back, minutes=5)})
    if "age" in counts:
        data += [{"MetricName": "ApproximateAgeOfOldestMessage", "Dimensions": dims, "Value": counts["age"],
                  "Unit": "Seconds", "Timestamp": now - timedelta(minutes=minutes)} for minutes in (2, 30)]
    for first in range(0, len(data), 500):
        cloudwatch.put_metric_data(Namespace=namespace, MetricData=data[first:first + 500])


def _numbers(now: datetime) -> None:
    cloudwatch = boto3.client("cloudwatch", region_name=REGION)
    for queue, counts in QUEUE_TRAFFIC.items():
        _put(cloudwatch, "AWS/SQS", [{"Name": "QueueName", "Value": queue}], counts, now)
    for topic, counts in TOPIC_TRAFFIC.items():
        _put(cloudwatch, "AWS/SNS", [{"Name": "TopicName", "Value": topic}], counts, now)
    for (bus, rule), counts in RULE_TRAFFIC.items():
        dims = [{"Name": "RuleName", "Value": rule}]
        if bus != "default":
            dims = [{"Name": "EventBusName", "Value": bus}, *dims]
        _put(cloudwatch, "AWS/Events", dims, counts, now)


def seed(now: datetime | None = None) -> dict[str, str]:
    """
    Makes acme's queues, topics, rules and functions in moto (inside mock_aws), with a week of CloudWatch numbers
    ending at `now`. Returns the topics' ARNs by name.
    """
    now = now or datetime.now(timezone.utc)
    _functions()
    topics = _topics()
    urls = _queues(topics["orders-events"])
    _messages(urls)
    _mappings()
    _subscriptions(topics)
    _rules()
    _numbers(now)
    return topics
