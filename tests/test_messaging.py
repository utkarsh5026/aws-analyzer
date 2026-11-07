import json
from datetime import datetime, timezone

import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from aws_analyzer._kit.view import _commands
from aws_analyzer.messaging import (
    MESSAGING_PRICES,
    Consumer,
    MessagingAnalyzer,
    MessagingView,
    Overview,
    Queue,
    QueueNumbers,
    QueueReport,
    Redrive,
    Rule,
    Subscription,
    Target,
    Topic,
    TopicNumbers,
    allows,
    arn_kind,
    arn_name,
    describe_filter,
    describe_pattern,
    follow_map,
    move_back_command,
    parse_queue,
    parse_ref,
    parse_rule,
    parse_subscription,
    queue_findings,
    queue_monthly,
    redrive_command,
    seconds_words,
    target_dlq_command,
    topic_findings,
    topic_monthly,
)
from aws_analyzer.messaging.analysis import blocks_services, dlq_too_short
from aws_analyzer.messaging.style import _render_html

from fake_messaging import ACCOUNT, REGION, seed

QUEUE = f"arn:aws:sqs:{REGION}:{ACCOUNT}:{{}}"
TOPIC = f"arn:aws:sns:{REGION}:{ACCOUNT}:orders-events"
RULE = f"arn:aws:events:{REGION}:{ACCOUNT}:rule/shop/order-placed"


def test_references_name_what_they_point_at():
    assert parse_ref("https://sqs.eu-west-1.amazonaws.com/123456789012/orders") == ("queue", "orders", "", "eu-west-1")
    assert parse_ref(QUEUE.format("orders")).kind == "queue"
    assert parse_ref(TOPIC).name == "orders-events" and parse_ref(f"{TOPIC}:1f2e-uuid").kind == "topic"
    assert parse_ref(RULE)[:3] == ("rule", "order-placed", "shop")
    assert parse_ref(f"arn:aws:events:{REGION}:{ACCOUNT}:rule/nightly").bus == "default"
    assert parse_ref("shop/order-placed")[:3] == ("rule", "order-placed", "shop")
    assert parse_ref("https://console.aws.amazon.com/events/home#/eventbus/shop/rules/order-placed").bus == "shop"
    assert parse_ref("orders").kind == ""
    with pytest.raises(ValueError):
        parse_ref("  ")
    with pytest.raises(ValueError, match="Lambda function"):
        parse_ref(f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:etl")


def test_arns_read_as_names_and_kinds():
    assert arn_name(QUEUE.format("orders")) == "orders" and arn_name(RULE) == "shop/order-placed"
    assert arn_name(f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:etl:live") == "etl"
    assert arn_name("https://hooks.example/x") == "https://hooks.example/x"
    assert arn_kind(QUEUE.format("x")) == "SQS queue" and arn_kind(RULE) == "EventBridge rule"
    assert arn_kind("mailto:x") == "endpoint"


def test_durations_read_as_people_say_them():
    assert seconds_words(345_600) == "4 days" and seconds_words(90) == "1.5 minutes"
    assert seconds_words(1) == "1 second" and seconds_words(3600) == "1 hour" and seconds_words(None) == "-"


def test_event_patterns_in_plain_english():
    pattern = {"source": ["shop.orders"], "detail-type": ["OrderPlaced", "OrderPaid"],
               "detail": {"total": [{"numeric": [">", 100, "<=", 500]}], "country": [{"anything-but": ["us"]}],
                          "coupon": [{"exists": False}]}}
    text = describe_pattern(pattern)
    assert text.startswith("OrderPlaced or OrderPaid events from shop.orders where detail.total > 100 and ≤ 500")
    assert "detail.country anything but us" in text and "detail.coupon is missing" in text
    assert describe_pattern({"$or": [{"a": ["1"]}, {"b": [{"prefix": "x"}]}]}) == \
        "events where either (a is 1) or (b starts with x)"
    assert describe_pattern(None) == "no event pattern"


def test_filter_policies_in_plain_english():
    assert describe_filter({"type": ["order", "refund"]}) == "messages whose attribute type is order or refund"
    assert describe_filter({"total": [{"numeric": [">=", 10]}]}, "MessageBody") == \
        "messages whose body field total ≥ 10"
    assert describe_filter(None) == "every message"


def _policy(*statements):
    return {"Statement": list(statements)}


def test_a_policy_lets_a_service_in_from_one_source():
    topic, other = TOPIC, TOPIC.replace("orders-events", "other")
    allow = {"Effect": "Allow", "Principal": {"Service": "sns.amazonaws.com"}, "Action": "sqs:SendMessage",
             "Condition": {"ArnEquals": {"aws:SourceArn": topic}}}
    assert allows(_policy(allow), service="sns.amazonaws.com", action="sqs:SendMessage", source_arn=topic)
    assert not allows(_policy(allow), service="sns.amazonaws.com", action="sqs:SendMessage", source_arn=other)
    assert not allows(_policy(allow), service="events.amazonaws.com", action="sqs:SendMessage", source_arn=topic)
    assert not allows(None, service="sns.amazonaws.com", action="sqs:SendMessage", source_arn=topic)
    anyone = {"Effect": "Allow", "Principal": "*", "Action": "SQS:*"}
    assert allows(_policy(anyone), service="sns.amazonaws.com", action="sqs:SendMessage", source_arn=topic)
    deny = {"Effect": "Deny", "Principal": "*", "Action": "sqs:SendMessage",
            "Condition": {"ArnLike": {"aws:SourceArn": "arn:aws:sns:*:*:orders-*"}}}
    assert not allows(_policy(anyone, deny), service="sns.amazonaws.com", action="sqs:SendMessage", source_arn=topic)
    account_only = {"Effect": "Allow", "Principal": {"Service": "sns.amazonaws.com"}, "Action": ["sqs:SendMessage"],
                    "Condition": {"StringEquals": {"aws:SourceAccount": ACCOUNT}}}
    assert allows(_policy(account_only), service="sns.amazonaws.com", action="sqs:SendMessage", source_arn=topic)


def test_the_aws_managed_key_keeps_services_out():
    assert blocks_services("alias/aws/sqs") and blocks_services(f"arn:aws:kms:{REGION}:{ACCOUNT}:alias/aws/sqs")
    assert not blocks_services("alias/my-key") and not blocks_services("")


def test_fix_commands_are_ready_to_copy():
    url = f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT}/orders"
    command = redrive_command(url, QUEUE.format("orders-dlq"), 5)
    attributes = json.loads(command.split("--attributes ", 1)[1].strip("'"))
    assert json.loads(attributes["RedrivePolicy"]) == {"deadLetterTargetArn": QUEUE.format("orders-dlq"),
                                                       "maxReceiveCount": "5"}
    assert move_back_command(QUEUE.format("orders-dlq")).endswith("--source-arn " + QUEUE.format("orders-dlq"))
    put = target_dlq_command(RULE, "audit", QUEUE.format("audit"), "<dlq>")
    assert "--rule order-placed --event-bus-name shop" in put
    assert "--event-bus-name" not in target_dlq_command(RULE.replace("shop/", ""), "a", "b", "c")


def test_queue_attributes_become_a_queue():
    queue = parse_queue(f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT}/jobs.fifo", {
        "QueueArn": QUEUE.format("jobs.fifo"), "ApproximateNumberOfMessages": "7",
        "ApproximateNumberOfMessagesNotVisible": "2", "VisibilityTimeout": "60", "KmsMasterKeyId": "alias/k",
        "RedrivePolicy": json.dumps({"deadLetterTargetArn": QUEUE.format("dlq"), "maxReceiveCount": 4}),
        "CreatedTimestamp": "1700000000", "Policy": "not json"})
    assert queue.fifo and queue.messages == 9 and queue.visibility_s == 60 and queue.encryption == "KMS"
    assert queue.redrive == Redrive(QUEUE.format("dlq"), 4) and queue.policy is None
    assert queue.created == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc) and queue.region == REGION


def test_a_subscription_waiting_for_confirmation_has_no_arn():
    pending = parse_subscription({"SubscriptionArn": "PendingConfirmation", "Protocol": "email",
                                  "Endpoint": "a@b.example", "TopicArn": TOPIC})
    assert pending.pending and pending.kind == "email" and pending.target == "a@b.example"
    full = parse_subscription({"SubscriptionArn": f"{TOPIC}:1", "Protocol": "sqs", "Endpoint": QUEUE.format("q")},
                              {"FilterPolicy": '{"a": ["b"]}', "RedrivePolicy": json.dumps(
                                  {"deadLetterTargetArn": QUEUE.format("d")}), "RawMessageDelivery": "true"})
    assert full.filter_policy == {"a": ["b"]} and full.dlq_arn == QUEUE.format("d") and full.raw
    assert full.target == "q"


def test_a_rule_and_its_targets():
    rule = parse_rule({"Name": "r", "EventBusName": "shop", "Arn": RULE, "State": "DISABLED",
                       "EventPattern": '{"source": ["x"]}'},
                      [{"Id": "t", "Arn": QUEUE.format("q"), "DeadLetterConfig": {"Arn": QUEUE.format("d")},
                        "RetryPolicy": {"MaximumRetryAttempts": 0}, "InputPath": "$.detail"}])
    assert not rule.is_enabled and rule.label == "shop/r" and rule.pattern == {"source": ["x"]}
    target = rule.targets[0]
    assert target.dlq_arn == QUEUE.format("d") and target.max_attempts == 0 and target.reshaped
    assert target.kind == "SQS queue" and target.name == "q"


def _queue(name, **kwargs):
    return Queue(name=name, url=f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT}/{name}", arn=QUEUE.format(name),
                 **kwargs)


def test_a_visibility_timeout_shorter_than_the_function_is_a_warning():
    queue = _queue("q", visibility_s=30, consumers=[Consumer(f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:f",
                                                             state="Enabled", batch_size=1, timeout_s=60)])
    findings = queue_findings(QueueReport(queue, days=7, numbers=QueueNumbers()), MESSAGING_PRICES)
    assert ("warn", findings[0][1]) == findings[0] and "VisibilityTimeout=360" in findings[0][1]
    queue.visibility_s = 120
    levels = [level for level, text in queue_findings(QueueReport(queue, 7, QueueNumbers()), MESSAGING_PRICES)
              if "visibility" in text]
    assert levels == ["info"]


def test_a_dead_letter_queue_keeping_messages_no_longer_than_its_source():
    source, dlq = _queue("q", retention_s=345_600), _queue("d", retention_s=345_600)
    assert "MessageRetentionPeriod=1209600" in dlq_too_short(source, dlq)[1]
    dlq.retention_s = 1_209_600
    assert dlq_too_short(source, dlq) is None
    assert dlq_too_short(_queue("q.fifo", fifo=True), _queue("d.fifo", fifo=True)) is None


def test_long_polling_is_suggested_when_empty_receives_cost_money():
    queue = _queue("q")
    numbers = QueueNumbers(empty_receives=1_000_000)
    found = [t for _, t in queue_findings(QueueReport(queue, 7, numbers), MESSAGING_PRICES) if "Long polling" in t]
    assert found and "ReceiveMessageWaitTimeSeconds=20" in found[0]
    queue.wait_s = 20
    assert not [t for _, t in queue_findings(QueueReport(queue, 7, numbers), MESSAGING_PRICES) if "Long" in t]


def test_costs_are_estimated_from_the_numbers():
    numbers = QueueNumbers(sent=1_000_000, received=1_000_000, deleted=1_000_000)
    assert queue_monthly(_queue("q"), numbers, 30, MESSAGING_PRICES) == pytest.approx(1.20)
    assert queue_monthly(_queue("q.fifo", fifo=True), numbers, 30, MESSAGING_PRICES) == pytest.approx(1.50)
    assert queue_monthly(_queue("q"), None, 30, MESSAGING_PRICES) is None
    topic = Topic("t", TOPIC, subscriptions=[Subscription("s", TOPIC, "https", "https://x"),
                                             Subscription("s2", TOPIC, "sqs", QUEUE.format("q"))])
    assert topic_monthly(topic, TopicNumbers(published=1_000_000), 30, MESSAGING_PRICES) == pytest.approx(1.10)


def test_the_map_follows_a_topic_to_its_queues_readers_and_dead_letters():
    dlq = _queue("dlq", visible=3, sources=["q"])
    queue = _queue("q", redrive=Redrive(dlq.arn, 5),
                   consumers=[Consumer(f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:f", state="Disabled")])
    topic = Topic("t", TOPIC, subscriptions=[Subscription("s", TOPIC, "sqs", queue.arn)])
    rule = Rule("r", "default", f"arn:aws:events:{REGION}:{ACCOUNT}:rule/r", targets=[Target("1", TOPIC)])
    overview = Overview(REGION, 7, queues=[queue, dlq], topics=[topic], rules=[rule])
    hops = follow_map(overview, "topic", TOPIC)
    assert [(h.upstream, h.branch, h.name) for h in hops] == [
        (True, "", "r"), (False, "", "t"), (False, "└─ ", "q"), (False, "   ├─ ", "f"), (False, "   └─ ", "dlq")]
    assert hops[2].problem == "its policy keeps the topic out" and hops[3].problem == "trigger disabled"
    assert hops[4].problem == "holds 3 failed messages" and hops[4].via == "after 5 failed receives"


@pytest.fixture
def aws():
    with mock_aws():
        yield seed()


@pytest.fixture
def core(aws):
    analyzer = MessagingAnalyzer(region=REGION)
    analyzer.max_workers = 1
    return analyzer


def test_the_overview_reads_every_queue_topic_and_rule(core):
    overview = core.overview()
    assert len(overview.queues) == 9 and len(overview.topics) == 3 and len(overview.rules) == 4
    dlq = overview.queue(QUEUE.format("orders-dlq"))
    assert dlq.messages == 42 and dlq.sources == ["orders"]
    orders = overview.queue(QUEUE.format("orders"))
    assert orders.in_flight == 10 and orders.consumers[0].function == "orders-etl"
    assert orders.consumers[0].timeout_s == 60
    assert overview.queue_numbers[orders.arn].sent == 31_000
    assert overview.queue_numbers[dlq.arn].oldest_now_s == pytest.approx(2.5 * 86_400)
    assert overview.topic_numbers[TOPIC].failed == 2_310 and overview.rule_numbers[RULE].failed == 410
    assert overview.functions[f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:refunds-handler"].exists is False
    assert overview.metrics_read and not overview.errors


def test_one_queue_with_its_senders_readers_and_dead_letter_queue(core):
    report = core.queue_report("orders")
    assert report.dlq.name == "orders-dlq" and report.dlq.sources == ["orders"]
    assert [(f.kind, f.name, f.allowed) for f in report.feeders] == [("EventBridge rule", "s3-upload", True)]
    assert len(report.numbers.daily) == 7 and report.numbers.daily[-1][1] > report.numbers.daily[0][1]
    texts = " ".join(text for _, text in queue_findings(report, core.prices))
    assert "orders-dlq holds 42 messages" in texts and "orders-etl" in texts
    audit = core.queue_report(f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT}/audit-events")
    assert {f.name: f.allowed for f in audit.feeders} == {"orders-events": False, "shop/order-placed": False}
    assert "alias/aws/sqs" in " ".join(text for _, text in queue_findings(audit, core.prices))


def test_one_topic_with_the_subscribers_that_keep_it_out(core):
    report = core.topic_report("orders-events")
    assert len(report.topic.subscriptions) == 5 and report.numbers.published == 18_400
    texts = " ".join(text for _, text in topic_findings(report, core.prices))
    assert "partner-webhooks" in texts and "SQSFailureFeedbackRoleArn" in texts
    assert core.topic_report(TOPIC).topic.name == "orders-events"
    assert core.topic_report("ops-alarms").topic.pending[0].endpoint == "oncall@acme.example"


def test_one_rule_by_name_bus_or_arn(core):
    assert core.rule_report("shop/order-placed").rule.targets[0].name == "audit-events"
    assert core.rule_report("order-placed").rule.bus == "shop"
    assert core.rule_report("order-placed", bus="shop").numbers.failed == 410
    assert core.rule_report(RULE).rule.description.startswith("Big orders")
    assert core.rule_report("nightly-report").rule.schedule == "cron(0 2 * * ? *)"


def test_a_name_that_isnt_there_suggests_the_closest(core):
    from aws_analyzer._kit.errors import _Hint

    with pytest.raises(_Hint, match="'orders'"):
        core.queue_report("ordrs")
    with pytest.raises(_Hint, match="orders-events"):
        core.topic_report("orders-event")
    with pytest.raises(_Hint, match="is a topic, not a queue"):
        core.queue_report(TOPIC)
    with pytest.raises(_Hint, match="eu-west-1"):
        core.queue_report("https://sqs.eu-west-1.amazonaws.com/123456789012/orders")


def test_dead_letters_say_what_each_catches(core):
    dead = core.dead_letters()
    assert [d.queue.name for d in dead] == ["orders-dlq", "shipping-dlq"]
    assert dead[0].catches == ["queue orders (after 3 receives)", "rule s3-upload → orders"]
    assert "topic orders-events → shipping-updates" in dead[1].catches


def test_follow_starts_at_a_queue_with_what_sends_to_it(core):
    found = core.follow("orders")
    assert found.kind == "queue" and found.name == "orders"
    upstream = [h.name for h in found.hops if h.upstream]
    assert upstream == ["s3-upload"]
    assert [h.name for h in found.hops if not h.upstream][:3] == ["orders", "orders-etl", "orders-dlq"]
    assert core.follow("shop").kind == "bus" and core.follow("shop/order-placed").kind == "rule"


class _Denied:
    """
    A CloudWatch client that refuses every read, as an account without cloudwatch:GetMetricData does.
    """

    def get_paginator(self, name):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "GetMetricData")


def test_numbers_that_cant_be_read_become_a_note(aws, capsys):
    core = MessagingAnalyzer(region=REGION, clients={"cloudwatch": _Denied()})
    overview = core.overview()
    assert "cloudwatch:GetMetricData" in overview.errors["metrics"] and overview.queues
    assert "metrics" in core.queue_report("orders").errors
    ui = MessagingView(core, mode="text", progress="off")
    out = run(capsys, ui.overview)
    assert "The metrics couldn't be read" in out and "Queues: the dead-letter" in out


def run(capsys, fn, *args, **kwargs):
    fn(*args, **kwargs)
    return capsys.readouterr().out


@pytest.fixture
def ui(core):
    return MessagingView(core, mode="text", progress="off")


def test_help_lists_every_command(ui, capsys):
    assert set(_commands(MessagingView)) == {n for names in MessagingView._GROUPS.values() for n in names}
    out = run(capsys, ui.help)
    assert "follow(name" in out and "Every queue, topic and EventBridge rule" in out


def test_the_overview_leads_with_what_failed(ui, capsys):
    out = run(capsys, ui.overview)
    assert "In dead-letter queues: 42 (!)" in out and "Failed deliveries · 7d: 2,720 (!)" in out
    assert out.index("orders-dlq holds 42 messages") < out.index("2,310 deliveries from orders-events failed")
    assert "-- Let them in --" in out and "orders-events and shop/order-placed -> SQS queue audit-events" in out
    assert "dead_letters()" in out.split("Next:")[1]


def test_queue_topic_and_rule_reports(ui, capsys):
    out = run(capsys, ui.queue_info, "orders")
    assert "Dead letters: 42 (!)" in out and "a message not handled by then is deleted" in out
    assert "LambdaView().errors('orders-etl')" in out and "never shows what's in a queue" in out
    out = run(capsys, ui.topic_info, "orders-events")
    assert "messages whose attribute type is order and country starts with eu-" in out
    assert "no: its policy keeps it out" in out and "Failed: 2,310 (!)" in out
    out = run(capsys, ui.rule_info, "shop/refund-requested")
    assert "State: off (!)" in out and "refunds-handler, which doesn't exist" in out
    out = run(capsys, ui.rule_info, "s3-upload")
    assert "matched no event" in out and "test-event-pattern" in out


def test_dead_letters_and_follow(ui, capsys):
    out = run(capsys, ui.dead_letters)
    assert "start-message-move-task --source-arn " + QUEUE.format("orders-dlq") in out
    out = run(capsys, ui.follow, "orders-events")
    assert "├─ shipping-updates" in out and "its policy keeps the topic out" in out
    assert "topic_info('orders-events')" in out


def test_a_missing_queue_is_a_note_not_a_traceback(ui, capsys):
    out = run(capsys, ui.queue_info, "ordrs")
    assert "No queue named 'ordrs'" in out and "Traceback" not in out


def test_an_empty_region(capsys):
    with mock_aws():
        ui = MessagingView(MessagingAnalyzer(region="eu-west-2"), mode="text", progress="off")
        out = run(capsys, ui.overview)
    assert "No queues, topics or rules in eu-west-2" in out


def test_reports_draw_as_html(core):
    ui = MessagingView(core, mode="html", progress="off")
    html = _render_html(ui._overview_blocks(core.overview()), 50)
    assert 'class="msg' in html and "📨 Messaging" in html and "orders-dlq" in html
