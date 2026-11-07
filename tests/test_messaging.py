import asyncio
import json
import re
import threading
from html import unescape
from datetime import datetime, timedelta, timezone

import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from aws_analyzer._kit.view import _commands
from aws_analyzer.messaging import (
    MESSAGING_PRICES,
    Consumer,
    MessagingAnalyzer,
    MessagingExplorer,
    MessagingView,
    Overview,
    Queue,
    QueueNumbers,
    QueueReport,
    QueueSample,
    Redrive,
    Rule,
    Subscription,
    Target,
    Topic,
    TopicNumbers,
    allows,
    arn_kind,
    arn_name,
    backlog_trend,
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
from aws_analyzer.messaging.explorer.frame import _window_text
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
    upstream = [(h.name, h.problem) for h in found.hops if h.upstream]
    assert upstream == [("s3-upload", "")]
    audit = core.follow("audit-events")
    assert [h.problem for h in audit.hops if h.upstream] == ["kept out by its policy"] * 2
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
    out = run(capsys, ui.follow, "partner-webhooks")
    assert "-- What sends to it --" in out and "kept out by its policy" in out and "Problems: 1 (!)" in out


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


def plain(html):
    text = re.sub(r"<style>.*?</style>|<[^>]+>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def explorer(core, where=None, **kwargs):
    return MessagingExplorer(where, core=core, mode="widgets", **kwargs)


def test_the_backlog_trend_says_which_way_a_queue_goes():
    start = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    looks = [QueueSample(start, 400), QueueSample(start + timedelta(minutes=2), 320)]
    trend = backlog_trend(looks)
    assert trend.per_minute == -40 and trend.empty_in_s == 480 and trend.words == "draining 40 a minute, empty in about 8 minutes"
    assert backlog_trend([QueueSample(start, 5), QueueSample(start + timedelta(minutes=1), 50)]).words == "growing 45 a minute"
    assert backlog_trend([QueueSample(start, 3), QueueSample(start + timedelta(minutes=5), 3)]).words == "steady"
    assert backlog_trend(looks[:1]).words == "one look so far"


def test_window_wording():
    assert _window_text("queue_info('orders-dlq') shows why.") == "The Overview tab of orders-dlq shows why."
    assert _window_text("See follow('orders').") == "See the Map tab of orders."
    listed = "3 more findings: each one's own are in queue_info(), topic_info() and rule_info()."
    assert _window_text(listed) == "3 more findings: each one's own are in the Overview tab."
    kept = "LambdaView().errors('orders-etl') says why"
    assert _window_text(kept) == kept


def test_explorer_opens_the_failed_messages_first(core):
    x = explorer(core)
    assert repr(x).startswith("MessagingExplorer(orders-dlq)") and x._tab == "everything"
    assert [i.name for i in x.items_shown[:2]] == ["orders-dlq", "orders-events"]
    assert "Waiting 42" in plain(x.stats.value) and "orders-dlq: 1 problem" in plain(x.status.value)
    kinds = [type(b).__name__ for b in x.shown["everything"]]
    assert "_Table" not in kinds and "_Next" not in kinds and "_Text" not in kinds
    assert "orders-dlq holds 42 messages" in plain(x.everything_view.value)
    x.open("orders-events")
    assert "Published · 7d 18,400" in plain(x.stats.value) and "Failed 2,310" in plain(x.stats.value)
    assert "Subscriptions" in plain(x.item_view.value) and "Let them in" in plain(x.item_view.value)


def test_explorer_everything_tab_searches_filters_and_sorts(core):
    x = explorer(core)
    x.item_find.value = "orders-etl"
    assert [i.name for i in x.items_shown] == ["orders"]
    x.item_find.value = ""
    chip = next(c for c in x.item_chips.children if c.description.startswith("Dead-letter"))
    chip.click()
    assert [i.name for i in x.items_shown] == ["orders-dlq", "shipping-dlq"]
    chip.click()
    x.column_buttons["waiting"].click()
    assert x.items_shown[0].name == "email-jobs"
    x.column_buttons["name"].click()
    assert x.items_shown[0].name == "audit-events"
    next(row for row in x._item_rows if row.key.endswith(":orders")).button.click()
    assert x._tab == "overview" and repr(x).startswith("MessagingExplorer(orders)")
    assert "a message not handled by then is deleted" in plain(x.item_view.value)


def test_explorer_map_follows_a_click(core):
    x = explorer(core, "orders-events", tab="map")
    assert x._tab == "map" and [h.name for h in x.hops_shown][:2] == ["orders-events", "shipping-updates"]
    blocked = next(h for h in x.hops_shown if h.name == "partner-webhooks")
    assert blocked.problem == "its policy keeps the topic out"
    next(row for row in x._hop_rows if row.key.endswith(":partner-webhooks")).button.click()
    assert x._tab == "map" and x.found.name == "partner-webhooks" and x.hops_shown[0].upstream
    assert x.hops_shown[0].problem == "kept out by its policy" and "orders-events" in plain(x.map_view.value)
    x.open("orders-events", tab="map")
    next(row for row in x._hop_rows if "fraud-check" in row.key).button.click()
    assert "LambdaView().explore('fraud-check')" in plain(x.status.value)
    x.open("shop", tab="map")
    assert [h.name for h in x.hops_shown][:2] == ["shop", "shop/order-placed"] and "event bus" in plain(x.item_view.value)


def test_explorer_dead_letters_and_live(core):
    x = explorer(core, tab="dead")
    assert [d.queue.name for d in x.dead_shown] == ["orders-dlq", "shipping-dlq"]
    assert "Holding messages 1" in plain(x.dead_view.value) and "orders-dlq holds 42" in plain(x.dead_view.value)
    x._dead_rows[0].button.click()
    assert x._tab == "overview" and repr(x).startswith("MessagingExplorer(orders-dlq)")
    x.watch()
    assert x._tab == "live" and [q.name for q in x.watched] == ["orders-dlq"] and x.watching
    assert len(x.looks[x.watched[0].arn]) == 1 and "42 waiting" in plain(x.live_view.value)
    assert "about" in plain(x.live_note.value) and x.focus.kind == "live"
    x._show_tab("map")
    assert not x.watching and x.focus.kind == "queue" and "queue orders-dlq" in x.focus.chips()


def test_explorer_starts_where_asked_and_refuses_what_it_hasnt(core):
    with pytest.raises(ValueError, match="tab is one of"):
        explorer(core, tab="logs")
    x = explorer(core, "https://sqs.us-east-1.amazonaws.com/123456789012/email-jobs")
    assert repr(x).startswith("MessagingExplorer(email-jobs)")
    x.open("ordrs")
    assert "No queue, topic, rule or bus named 'ordrs'" in plain(x.status.value)
    x.field._clicked(f"rule:arn:aws:events:{REGION}:{ACCOUNT}:rule/shop/refund-requested")
    assert "is disabled" in plain(x.item_view.value)
    x._show_tab("dead")
    x.refresh()
    assert x._tab == "dead" and "refund-requested" in x.item_key


def test_explorer_without_widgets_shows_reports(core, capsys):
    MessagingExplorer("orders-events", core=core, mode="text")
    assert "Where a message goes from orders-events" in capsys.readouterr().out
    html_view = MessagingView(core, mode="html")
    html_view._show = lambda blocks: None
    html_view.explore("orders")
    assert repr(html_view.explorer).startswith("MessagingExplorer(orders)")
    text_view = MessagingView(core, mode="text", progress="off")
    assert "needs Jupyter" in run(capsys, text_view.explore)


def test_explorer_reads_in_the_background(core):
    hold = threading.Event()
    real = core.queue_report

    def held(*args, **kwargs):
        hold.wait(10)
        return real(*args, **kwargs)

    core.queue_report = held

    async def settle(x):
        while x._tasks:
            await asyncio.gather(*list(x._tasks.values()))

    async def main():
        x = explorer(core)
        assert list(x._tasks) == ["list"] and x.overview is None
        await asyncio.gather(x._tasks["list"])
        assert "item" in x._tasks and "Reading orders-dlq" in plain(x.status.value)
        x.open("orders")
        hold.set()
        await settle(x)
        assert repr(x).startswith("MessagingExplorer(orders)") and "orders-dlq holds" in plain(x.item_view.value)

    asyncio.run(main())


def test_live_watches_on_the_loop(core, monkeypatch):
    from aws_analyzer.messaging.explorer import live as livemod

    monkeypatch.setattr(livemod, "_WATCH_SECONDS", 0.01)

    async def main():
        x = explorer(core, "orders")
        while x._tasks:
            await asyncio.gather(*list(x._tasks.values()))
        x.watch()
        assert "watch" in x._tasks
        while len(x.looks.get(x.watched[0].arn, [])) < 3:
            await asyncio.sleep(0.01)
        x._show_tab("everything")
        await asyncio.sleep(0.05)
        assert not x.watching and "watch" not in x._tasks

    asyncio.run(main())
