"""
The agent's CloudWatch toolkit: what normal looks like for a number. The maths (baselines.py) on its own, the tools on
fake_cloudwatch's night (churn-endpoint slow since 02:10, orders-etl failing with its alarm firing), the reads on
moto's CloudWatch through the read-only guard, and a question answered end to end with a scripted model.
"""

import json
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from fake_cloudwatch import NOW, FakeCloudWatch, scene
from moto import mock_aws

from aws_analyzer.agent import AgentAnalyzer, AgentView, ReadOnlyError
from aws_analyzer.agent.toolkits import DEFAULT_TOOLKITS
from aws_analyzer.agent.toolkits.baselines import (
    HIGHER, RESOURCE_KINDS, LOWER, NO_BASELINE, NO_DATA, USUAL, Comparison, Series, alarm_command, alarm_rule, compare, judge,
    make_window, parse_alarm, parse_resource, pick_metrics, ratio, resource_of, rounded_value, says,
)
from aws_analyzer.agent.toolkits.cloudwatch_reads import MetricReader
from aws_analyzer.agent.window.chat import _SERVICES, _tool_words
from aws_analyzer.agent.window.tool_words import _TOOL_WORDS
from aws_analyzer.agent.answers import ToolCall

REGION = "us-east-1"
AT = "2026-11-09T03:00Z"


def tool(core, name):
    return next(t for t in core.tools() if t.__name__ == name)


@pytest.fixture
def cloudwatch():
    return FakeCloudWatch(scene(NOW))


@pytest.fixture
def agent(cloudwatch):
    reader = MetricReader(client=cloudwatch, region=REGION)
    reader.max_workers = 1
    return AgentAnalyzer(region=REGION, toolkits=["cloudwatch"], cores={"cloudwatch": reader})




def test_a_resource_is_read_from_kind_name_an_arn_a_queue_url_or_a_raw_metric():
    assert parse_resource("lambda:orders-etl") == parse_resource("function/orders-etl")
    assert parse_resource("Endpoint: churn-endpoint").kind == "sagemaker"
    arn = parse_resource("arn:aws:lambda:eu-west-1:123456789012:function:orders-etl")
    assert (arn.kind, arn.name, arn.region) == ("lambda", "orders-etl", "eu-west-1")
    machine = parse_resource("arn:aws:states:us-east-1:123456789012:stateMachine:order-pipeline")
    assert machine.kind == "states" and machine.name.endswith(":stateMachine:order-pipeline")
    queue = parse_resource("https://sqs.us-east-2.amazonaws.com/123456789012/email-jobs")
    assert (queue.kind, queue.name, queue.region) == ("sqs", "email-jobs", "us-east-2")
    assert parse_resource("arn:aws:dynamodb:us-east-1:123456789012:table/orders/stream/x").name == "orders"
    raw = parse_resource("AWS/EC2:InstanceId=i-0abc,AutoScalingGroupName=web")
    assert raw.kind == "" and raw.namespace == "AWS/EC2" and raw.dimensions == (("InstanceId", "i-0abc"),
                                                                                  ("AutoScalingGroupName", "web"))
    assert raw.text == "AWS/EC2:InstanceId=i-0abc,AutoScalingGroupName=web"
    with pytest.raises(ValueError, match="write it as kind:name"):
        parse_resource("orders-etl")


def test_metric_words_pick_the_number_and_its_statistic():
    lam = RESOURCE_KINDS["lambda"]
    assert [s.key for s in pick_metrics(lam, "")] == ["invocations", "errors", "duration", "throttles"]
    slow = pick_metrics(lam, "p99 latency")[0]
    assert (slow.name, slow.stat, slow.label, slow.unit) == ("Duration", "p99", "p99 duration", "ms")
    assert pick_metrics(lam, "failures")[0].name == "Errors"
    peak = pick_metrics(RESOURCE_KINDS["sqs"], "max backlog")[0]
    assert (peak.name, peak.stat) == ("ApproximateNumberOfMessagesVisible", "Maximum")
    other = pick_metrics(lam, "ConcurrentExecutions")[0]
    assert (other.name, other.stat) == ("ConcurrentExecutions", "Average")
    latency = pick_metrics(RESOURCE_KINDS["sagemaker"], "latency")[0]
    assert (latency.name, latency.scale, latency.unit) == ("ModelLatency", 0.001, "ms")
    with pytest.raises(ValueError, match="needs metric="):
        pick_metrics(None, "")


def test_judging_needs_a_real_change_against_both_baselines():
    errors = RESOURCE_KINDS["lambda"].metrics[1]
    calls = RESOURCE_KINDS["lambda"].metrics[0]
    assert judge(errors, 35, [2, 3]) == HIGHER
    assert judge(errors, 4, [2, 1]) == USUAL
    assert judge(errors, 5, [0, 0]) == HIGHER
    assert judge(errors, 0, [40, 50]) == USUAL
    assert judge(calls, 100, [1200, 1100]) == LOWER
    assert judge(calls, 1600, [1200, 1100]) == USUAL
    assert judge(errors, None, [1, 2]) == NO_DATA and judge(errors, 3, [None, None]) == NO_BASELINE
    assert ratio(1840, 410) == 4.5 and ratio(71, 2) == 36.0 and ratio(5, 0) is None
    assert rounded_value(1839.6) == 1840 and rounded_value(4.46) == 4.5 and rounded_value(0.01234) == 0.0123


def test_off_since_is_where_the_run_of_off_points_starts():
    spec = RESOURCE_KINDS["sagemaker"].metrics[1]
    window = make_window(NOW + timedelta(minutes=2), 60)
    assert (window.end, window.start, window.trend_start) == (NOW, NOW - timedelta(hours=1), NOW - timedelta(hours=6))
    started = NOW - timedelta(minutes=50)
    points = {t: (1840.0 if t >= started else 400.0) for t in window.point_starts()}
    points[NOW - timedelta(minutes=25)] = 420.0
    now = Series({t: (1840.0 if t >= window.start else 400.0) for t in window.period_starts()}, points)

    def back(shift):
        return Series({t - shift: 400.0 for t in window.period_starts()},
                      {t - shift: 400.0 for t in window.point_starts()})

    found = compare(spec, window, (now, back(timedelta(days=1)), back(timedelta(days=7))))
    assert found.looks == HIGHER and found.off_since == started and found.off_before is None
    assert [row["now"] for row in found.trend] == [400.0] * 5

    always = Series(now.period_values, {t: 1840.0 for t in window.point_starts()})
    early = compare(spec, window, (always, back(timedelta(days=1)), back(timedelta(days=7))))
    assert early.off_since is None and early.off_before == window.trend_start
    assert "since before 2026-11-08T21:00Z, at least" in says(early, "churn-endpoint", window)


def test_alarms_are_said_in_words_and_in_the_comparisons_unit():
    raw = scene(NOW).alarms[0]
    assert alarm_rule(raw) == "Sum > 10 for 1 of 1 periods of 5 minutes"
    latency = {"AlarmName": "slow", "StateValue": "OK", "Namespace": "AWS/SageMaker", "MetricName": "ModelLatency",
               "ExtendedStatistic": "p95", "Period": 60, "EvaluationPeriods": 5, "DatapointsToAlarm": 3,
               "Threshold": 2_000_000.0, "ComparisonOperator": "GreaterThanOrEqualToThreshold",
               "Dimensions": [{"Name": "EndpointName", "Value": "churn-endpoint"}]}
    alarm = parse_alarm(latency, unit="ms", scale=0.001)
    assert alarm.rule == "p95 >= 2,000 ms for 3 of 5 periods of a minute" and alarm.threshold == 2000
    assert alarm.notifies is False and resource_of(alarm) == "sagemaker:churn-endpoint"
    ec2 = parse_alarm(scene(NOW).alarms[3])
    assert resource_of(ec2) == "AWS/EC2:InstanceId=i-0old"
    composite = parse_alarm({"AlarmName": "all", "StateValue": "ALARM", "AlarmRule": "ALARM(a) OR ALARM(b)"})
    assert composite.rule == "composite: ALARM(a) OR ALARM(b)" and resource_of(composite) == ""


def test_the_alarm_command_would_have_caught_it():
    spec = RESOURCE_KINDS["sagemaker"].metrics[1]
    found = Comparison(spec, spec.label, (("EndpointName", "churn-endpoint"), ("VariantName", "AllTraffic")), 1840,
                       395, 410, HIGHER)
    command = alarm_command(found, namespace="AWS/SageMaker", name="churn-endpoint", region=REGION)
    assert command.startswith("aws cloudwatch put-metric-alarm --alarm-name churn-endpoint-latency ")
    assert "--dimensions Name=EndpointName,Value=churn-endpoint Name=VariantName,Value=AllTraffic" in command
    assert "--extended-statistic p95" in command and "--threshold 820000 " in command
    assert "--comparison-operator GreaterThanThreshold" in command and command.endswith("--region us-east-1")
    calls = RESOURCE_KINDS["lambda"].metrics[0]
    dropped = Comparison(calls, calls.label, (("FunctionName", "f"),), 100, 1200, 1100, LOWER)
    assert "--statistic Sum" in alarm_command(dropped, namespace="AWS/Lambda", name="f")
    assert "--threshold 550 --comparison-operator LessThanThreshold" in alarm_command(dropped, namespace="AWS/Lambda",
                                                                                      name="f")




def test_compare_metric_says_how_far_off_since_when_and_that_no_alarm_watches_it(agent, cloudwatch):
    out = tool(agent, "compare_metric")("sagemaker:churn-endpoint", at=AT)
    assert out["complete"] and out["off"] == ["p95 latency"] and out["region"] == REGION
    assert out["window"] == {"from": "2026-11-09T02:00Z", "to": "2026-11-09T03:00Z", "hours": 1.0}
    assert out["compared_with"]["last_week"]["from"] == "2026-11-02T02:00Z"
    latency = out["metrics"][0]
    assert (latency["now"], latency["last_week"], latency["yesterday"]) == (1840, 410, 395)
    assert (latency["vs_last_week_x"], latency["vs_yesterday_x"]) == (4.5, 4.7)
    assert latency["off_since"] == "2026-11-09T02:10Z" and latency["alarms"] == []
    assert latency["says"] == ("p95 latency for churn-endpoint in the hour to 2026-11-09T03:00Z: 1,840 ms against "
                               "410 ms last week (4.5×) and 395 ms yesterday (4.7×), higher than usual since "
                               "2026-11-09T02:10Z. No alarm is set on it.")
    assert len(latency["earlier_periods"]) == 5 and latency["dimensions"]["VariantName"] == "AllTraffic"
    usual = out["metrics"][1]
    assert usual["looks"] == "usual" and "earlier_periods" not in usual
    finding = out["findings"][0]
    assert finding["level"] == "warn" and "with no alarm on it" in finding["message"]
    assert "aws cloudwatch put-metric-alarm" in finding["message"] and "--threshold 820000" in finding["message"]
    first, *_ = cloudwatch.called("get_metric_data")
    assert {q["MetricStat"]["Period"] for q in first["MetricDataQueries"]} == {300, 3600}
    assert {q["MetricStat"]["Stat"] for q in first["MetricDataQueries"]} == {"Sum", "p95"}
    assert len(cloudwatch.called("get_metric_data")) == 3
    json.dumps(out)


def test_an_alarm_already_firing_is_said_and_isnt_a_finding(agent):
    out = tool(agent, "compare_metric")("lambda:orders-etl", metric="errors", at=AT)
    errors = out["metrics"][0]
    assert (errors["now"], errors["last_week"], errors["vs_last_week_x"]) == (71, 2, 36.0)
    assert errors["alarms"][0]["name"] == "orders-etl-errors" and errors["alarms"][0]["state"] == "ALARM"
    assert errors["says"].endswith("Alarm 'orders-etl-errors' (Sum > 10 for 1 of 1 periods of 5 minutes) is in "
                                   "ALARM since 2026-11-09T02:15Z.")
    assert out["findings"] == []


def test_a_usual_night_says_so(agent):
    out = tool(agent, "compare_metric")("sqs:email-jobs", at=AT)
    assert out["off"] == [] and {m["looks"] for m in out["metrics"]} == {"usual"}
    assert out["findings"][0]["level"] == "info" and "within its usual range" in out["findings"][0]["message"]
    backlog = out["metrics"][0]
    assert "is set on it and is OK" in backlog["says"] and backlog["alarms"][0]["rule"].startswith("Maximum > 1,000")


def test_a_name_cloudwatch_doesnt_have_gets_the_close_ones(agent):
    out = tool(agent, "compare_metric")("lambda:orders-et", at=AT)
    assert out["complete"] is False and out["close_names"] == ["orders-etl", "orders-api"]
    assert "no numbers for Lambda function 'orders-et'" in out["error"]
    endpoint = tool(agent, "compare_metric")("endpoint:churn-endpont", at=AT)
    assert endpoint["close_names"] == ["churn-endpoint"]
    raw = tool(agent, "compare_metric")("AWS/EC2:InstanceId=i-0old", metric="CPUUtilization", at=AT)
    assert raw["metrics"][0]["looks"] == "no data" and "is set on it and is INSUFFICIENT_DATA" in \
        raw["metrics"][0]["says"]


def test_find_alarms_puts_the_firing_ones_first_with_what_they_watch(agent):
    firing = tool(agent, "find_alarms")()
    assert [a["name"] for a in firing["alarms"]] == ["orders-etl-errors", "checkout-5xx"]
    assert firing["alarms"][0]["watches"] == "lambda:orders-etl" and firing["alarms"][1]["notifies"] is False
    messages = [f["message"] for f in firing["findings"]]
    assert "compare_metric('lambda:orders-etl')" in messages[0]
    assert any("nobody hears" in m for m in messages)
    every = tool(agent, "find_alarms")(state="all")
    assert every["states"] == {"ALARM": 2, "OK": 1, "INSUFFICIENT_DATA": 1} and every["found"] == 4
    assert "delete-alarms --alarm-names legacy-batch-cpu" in every["findings"][-1]["message"]
    wrong = tool(agent, "find_alarms")(state="red")
    assert wrong["complete"] is False and "use 'ALARM', 'OK'" in json.dumps(wrong)


def test_every_agent_gets_the_toolkit_with_its_words_and_badge():
    assert "cloudwatch" in DEFAULT_TOOLKITS
    core = AgentAnalyzer(region=REGION)
    kit = core.toolkit("cloudwatch")
    assert [fn.__name__ for fn in kit.tools()] == ["compare_metric", "find_alarms"]
    assert all(fn.__name__ in _TOOL_WORDS for fn in kit.tools())
    assert {read.split(":", 1)[0] for reads in kit.reads.values() for read in reads} <= set(_SERVICES)
    assert core.reads["compare_metric"] == ("cloudwatch:GetMetricData", "cloudwatch:ListMetrics",
                                            "cloudwatch:DescribeAlarmsForMetric")
    call = ToolCall("compare_metric", {"resource": "sagemaker:churn-endpoint"}, first="resource")
    assert _tool_words(call, running=True) == "comparing sagemaker:churn-endpoint with yesterday and last week"
    assert "call compare_metric" in kit.instructions(core.budget)




def test_the_reads_work_on_cloudwatch_itself_and_the_guard_refuses_a_write():
    with mock_aws():
        client = boto3.client("cloudwatch", region_name=REGION)
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        for days in (0, 1, 7):
            for minutes in range(5, 60, 5):
                value = 30.0 if days == 0 else 2.0
                client.put_metric_data(Namespace="AWS/Lambda", MetricData=[{
                    "MetricName": "Errors", "Dimensions": [{"Name": "FunctionName", "Value": "etl"}],
                    "Timestamp": now - timedelta(days=days, minutes=minutes), "Value": value}])
        core = AgentAnalyzer(region=REGION, toolkits=["cloudwatch"])
        out = tool(core, "compare_metric")("lambda:etl", metric="errors")
        errors = out["metrics"][0]
        assert out["complete"] and errors["looks"] == "higher than usual" and errors["now"] > errors["last_week"]
        assert errors["says"].startswith("errors for etl in the hour to ") and "No alarm is set on it." in errors["says"]
        assert tool(core, "find_alarms")()["found"] == 0
        reader = core.toolkit("cloudwatch").analyzer
        elsewhere = tool(core, "compare_metric")("arn:aws:lambda:eu-west-1:123456789012:function:etl", metric="errors")
        assert elsewhere["regions"] == ["eu-west-1"] and "no numbers for Lambda function 'etl' in eu-west-1" in \
            elsewhere["error"]
        with pytest.raises(ReadOnlyError, match="cloudwatch:PutMetricAlarm isn't a read"):
            reader.cloudwatch().put_metric_alarm(AlarmName="x", EvaluationPeriods=1, ComparisonOperator="LessThanThreshold")
        with pytest.raises(ReadOnlyError):
            reader.cloudwatch("eu-west-1").delete_alarms(AlarmNames=["x"])
        assert core._guard.counts["cloudwatch:GetMetricData"] == 6




def test_the_agent_copies_the_comparison_and_the_report_shows_it(cloudwatch, capsys):
    from test_agent import calls, says, scripted

    from aws_analyzer.agent.grounding import check_grounding

    sentence = ("p95 latency of churn-endpoint is 1,840 ms against 410 ms last week (4.5×), since 02:10 UTC. "
                "No alarm is set on it [1].")
    model = scripted(calls(("compare_metric", {"resource": "sagemaker:churn-endpoint", "at": AT})), says(sentence))
    reader = MetricReader(client=cloudwatch, region=REGION)
    agent = AgentAnalyzer(region=REGION, toolkits=["cloudwatch"], cores={"cloudwatch": reader}, chat_model=model,
                          findings=False)
    answer = agent.ask("Is churn-endpoint slow?", now=NOW)
    assert answer.error is None and [c.name for c in answer.calls] == ["compare_metric"]
    assert answer.calls[0].summary == "churn-endpoint: p95 latency higher than usual"
    assert answer.calls[0].call == "compare_metric('sagemaker:churn-endpoint', at='2026-11-09T03:00Z')"
    assert "compare_metric" in model.bound[0] and "find_alarms" in model.bound[0]
    assert check_grounding(answer, []).unknown_numbers == []
    view = AgentView(agent, mode="text", progress="off")
    view._show(view._answer_blocks(answer))
    out = capsys.readouterr().out
    assert "1,840 ms" in out and "compare_metric" in out
