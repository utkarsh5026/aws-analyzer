import asyncio
import html
import io
import json
import os
import re
import sys
import threading
import urllib.request
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import boto3
import pytest
from botocore import xform_name
from botocore.exceptions import ClientError
from botocore.validate import ParamValidator
from moto import mock_aws

from aws_analyzer import lambda_functions as lfmod
from aws_analyzer.lambda_functions import analyzer as analyzer_mod, helpers, style as stylemod, view as viewmod
from aws_analyzer.lambda_functions.analysis import text as textmod
from aws_analyzer.lambda_functions.analysis.codemap import aws_model, reader
from aws_analyzer.lambda_functions.analysis.codemap import findings as codemap_findings
from aws_analyzer.lambda_functions.analysis.codemap import graph as codemap_graph
from aws_analyzer.lambda_functions.blocks import diff as diff_block, graph as graph_block, wiring as wiring_block
from aws_analyzer.lambda_functions.blocks.walk import _Walk, _WalkHtml
from aws_analyzer.lambda_functions.explorer import code as code_tab, frame, logs as logs_tab
from aws_analyzer import set_timezone
from aws_analyzer._kit import window as windowmod
from aws_analyzer._kit.blocks import _Next, _Report
from aws_analyzer._kit.errors import _Hint
from aws_analyzer._kit.render import _render_text
from aws_analyzer._kit.view import _commands
from aws_analyzer.lambda_functions import (
    GB,
    LAMBDA_PRICES,
    MB,
    RUNTIMES,
    AccountLimits,
    AsyncConfig,
    CodeFile,
    CodePackage,
    DailyUsage,
    ErrorGroup,
    ErrorReport,
    Function,
    FunctionDetail,
    FunctionMetrics,
    Invocation,
    LambdaAnalyzer,
    LambdaExplorer,
    LambdaView,
    Layer,
    LogEvent,
    LogGroup,
    LogRun,
    Performance,
    ProvisionedConcurrency,
    Trigger,
    Version,
    account_findings,
    classify_error,
    code_graph,
    code_map_findings,
    diff_findings,
    diff_packages,
    error_findings,
    find_symbol_in,
    function_findings,
    function_monthly_cost,
    group_errors,
    handler_file,
    human_ms,
    line_level,
    map_code,
    map_package,
    masked_config,
    package_file_name,
    package_findings,
    parse_event_source_mapping,
    parse_function,
    parse_function_ref,
    parse_policy,
    parse_report,
    percentile,
    performance_findings,
    provisioned_monthly_cost,
    read_package,
    request_id_of,
    runtime_status,
    search_package,
    search_rank,
    secret_like,
    source_files,
    split_runs,
    suggest_memory,
    version_before,
)

REGION, OTHER = "us-east-1", "eu-west-1"
ACCOUNT = "123456789012"
RID = "8f5ce35b-3c2b-4b4e-9d7c-1f2e3d4c5b6a"
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def zipped(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


CODE = zipped({
    "app.py": "def handler(event, context):\n    return event['customer_id']\n",
    "utils.py": "def helper():\n    return 42\n",
    ".env": "DB_PASSWORD=hunter2\n",
})


SOURCE = zipped({
    "app.py": 'import os\nfrom store import Orders, load_orders\n\nTABLE = os.environ["TABLE"]\n\n\n'
              'def handler(event, context):\n    orders = load_orders(event)\n    return Orders(TABLE).save(orders)\n',
    "store.py": 'class Orders:\n    """Orders in DynamoDB."""\n\n    def __init__(self, table):\n        self.table = table\n\n'
                '    @staticmethod\n    def save(orders, *, batch=25) -> int:\n        return len(orders)\n\n\n'
                'def load_orders(event):\n    """The orders in an S3 event."""\n    return event["customer_id"]\n',
    "requests/api.py": "def load_orders():\n    return None\n",
    "requests-2.32.3.dist-info/RECORD": "requests/api.py,,\n",
    "broken.py": "def oops(:\n",
    "logo.png": b"\x89PNG\x00\x00",
    ".env": "DB_PASSWORD=hunter2\n",
    "long.py": "\n".join(f"x{i} = {i}" for i in range(1, 701)) + "\n",
})


def fn(**kwargs):
    defaults = dict(name="etl", arn=f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:etl", region=REGION,
                    runtime="python3.12", handler="app.handler", memory=1024, timeout=30)
    return Function(**{**defaults, **kwargs})


def metrics(invocations=1000.0, errors=0.0, throttles=0.0, avg=100.0, longest=500.0, days=30, last=NOW, **kwargs):
    daily = [DailyUsage(last.replace(hour=0, minute=0), invocations, errors, throttles, avg * invocations, longest)]
    return FunctionMetrics("etl", days, 86400, invocations, errors, throttles, avg * invocations, longest,
                           daily=daily, **kwargs)


def run(capsys, fn, *args, **kwargs):
    fn(*args, **kwargs)
    return capsys.readouterr().out


# ---- helpers (pure functions)


@pytest.mark.parametrize("text, expected", [
    ("etl", ("etl", None, None)),
    (" etl-nightly:live ", ("etl-nightly", "live", None)),
    ("etl:7", ("etl", "7", None)),
    (f"arn:aws:lambda:{OTHER}:{ACCOUNT}:function:etl", ("etl", None, OTHER)),
    (f"arn:aws:lambda:{OTHER}:{ACCOUNT}:function:etl:$LATEST", ("etl", "$LATEST", OTHER)),
    (f"{ACCOUNT}:function:etl:prod", ("etl", "prod", None)),
    (f"https://{OTHER}.console.aws.amazon.com/lambda/home?region={OTHER}#/functions/etl?tab=code",
     ("etl", None, OTHER)),
])
def test_parse_function_ref(text, expected):
    assert parse_function_ref(text) == expected


def test_parse_function_ref_explains_what_it_takes():
    with pytest.raises(ValueError, match="isn't a Lambda function name, ARN or console link"):
        parse_function_ref("not a/function")


def test_runtime_status_follows_the_support_schedule():
    today = date(2026, 10, 6)
    ended = runtime_status("python3.9", today=today)
    assert (ended.state, ended.label, ended.tone, ended.upgrade) == ("deprecated", "python3.9 (ended)", "bad",
                                                                     "python3.14")
    assert ended.deprecated == date(2025, 12, 15) and ended.block_update == date(2027, 3, 3)
    ending = runtime_status("python3.10", today=today)
    assert (ending.state, ending.days_left, ending.tone) == ("ending", 25, "warn")
    assert ending.label == "python3.10 (ends in 25 days)"
    assert runtime_status("python3.12", today=today).state == "supported"
    assert runtime_status("nodejs12.x", today=today).state == "blocked"
    assert runtime_status("python3.14", today=today).upgrade is None  # already the newest
    assert runtime_status(None, package_type="Image").label == "container image"
    unknown = runtime_status("python3.15", today=today)
    assert unknown.state == "unknown" and unknown.upgrade == "python3.14"
    assert runtime_status("dotnetcore3.1", today=today).upgrade == "dotnet10"
    assert runtime_status("go1.x", today=today).upgrade == "provided.al2023"


def test_runtime_table_is_well_formed():
    for runtime, dates in RUNTIMES.items():
        assert len(dates) == 3
        for value in dates:
            assert value is None or date.fromisoformat(value)
        assert lfmod.runtime_family(runtime) in lfmod.LATEST_RUNTIMES
    for newest in lfmod.LATEST_RUNTIMES.values():
        assert newest in RUNTIMES


def test_times_and_sizes_read_the_way_people_write_them():
    assert helpers._lambda_time("2024-05-01T10:00:00.000+0000") == datetime(2024, 5, 1, 10, tzinfo=timezone.utc)
    assert helpers._lambda_time("2026-10-05T19:34:24.781359000Z").microsecond == 781359
    assert helpers._lambda_time("2026-10-05T19:34:24Z").second == 24
    assert helpers._lambda_time(None) is None and helpers._lambda_time("yesterday") is None
    assert [human_ms(v) for v in (0.42, 102.4, 1530, 15300, 75000, 185000)] == [
        "0.42 ms", "102 ms", "1.53 s", "15.3 s", "75.0 s", "3m 05s"]
    assert helpers._ms(1530) == "1,530 ms (1.5s)" and helpers._ms(None) == "-"
    assert [helpers._ms(v) for v in (102.4, 30000, 185000, 3_725_000)] == [
        "102 ms", "30,000 ms (30s)", "185,000 ms (3m 05s)", "3,725,000 ms (1h 02m)"]
    assert [helpers._pct(v) for v in (0, 0.0004, 0.024, None)] == ["0%", "<0.1%", "2.4%", "-"]
    assert textmod._window(900) == "15 minutes" and textmod._window(3600) == "hour"
    assert textmod._window(86400) == "24 hours" and textmod._window(30 * 86400) == "30 days"
    assert lfmod.parse_size("50MB") == 50 * MB
    assert lfmod.parse_time("24h", now=NOW) == NOW - timedelta(hours=24)


def test_parse_function_keeps_names_not_values():
    config = {
        "FunctionName": "etl", "FunctionArn": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:etl:live",
        "Runtime": "python3.12", "Handler": "app.handler", "MemorySize": 2048, "Timeout": 60, "CodeSize": 1234,
        "Architectures": ["arm64"], "LastModified": "2026-10-01T08:00:00.000+0000",
        "Environment": {"Variables": {"DB_PASSWORD": "hunter2", "MODE": "prod"}},
        "Layers": [{"Arn": f"arn:aws:lambda:{REGION}:{ACCOUNT}:layer:pandas:7", "CodeSize": 40 * MB}],
        "VpcConfig": {"VpcId": "vpc-1", "SubnetIds": ["subnet-a", "subnet-b"], "SecurityGroupIds": ["sg-1"]},
        "LoggingConfig": {"LogFormat": "JSON", "LogGroup": "/custom/etl", "ApplicationLogLevel": "INFO"},
        "EphemeralStorage": {"Size": 2048}, "SnapStart": {"ApplyOn": "PublishedVersions"},
        "TracingConfig": {"Mode": "Active"}, "DeadLetterConfig": {"TargetArn": "arn:aws:sqs:us-east-1:1:dlq"},
    }
    f = parse_function(config)
    assert (f.name, f.arn, f.region) == ("etl", f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:etl", REGION)
    assert (f.memory, f.timeout, f.architecture, f.arm, f.gb) == (2048, 60, "arm64", True, 2.0)
    assert f.env_names == ["DB_PASSWORD", "MODE"] and "hunter2" not in json.dumps(f.raw)
    assert f.raw["Environment"]["Variables"] == {"DB_PASSWORD": "(hidden)", "MODE": "(hidden)"}
    assert f.layers[0].name == "pandas:7" and f.layer_size == 40 * MB
    assert (f.vpc_id, f.subnets, f.log_group, f.log_format, f.ephemeral_storage) == (
        "vpc-1", ["subnet-a", "subnet-b"], "/custom/etl", "JSON", 2048)
    assert f.snapstart and f.tracing == "Active" and f.dead_letter.endswith(":dlq")
    assert f.last_modified == datetime(2026, 10, 1, 8, tzinfo=timezone.utc)
    plain = parse_function({"FunctionName": "x", "VpcConfig": {"VpcId": "", "SubnetIds": []}}, "eu-west-1")
    assert plain.vpc_id is None and plain.log_group == "/aws/lambda/x" and plain.region == "eu-west-1"
    assert masked_config({"Environment": {"Variables": {"A": "1"}}})["Environment"]["Variables"] == {"A": "(hidden)"}


def test_parse_policy_names_who_may_call():
    def statement(**kwargs):
        return {"Effect": "Allow", "Action": "lambda:InvokeFunction", **kwargs}

    policy = {"Statement": [
        statement(Sid="s3", Principal={"Service": "s3.amazonaws.com"}, Condition={
            "ArnLike": {"AWS:SourceArn": "arn:aws:s3:::acme-uploads"}, "StringEquals": {"AWS:SourceAccount": ACCOUNT}}),
        statement(Sid="api", Principal={"Service": "apigateway.amazonaws.com"}, Condition={
            "ArnLike": {"AWS:SourceArn": f"arn:aws:execute-api:{REGION}:{ACCOUNT}:a1b2c3/prod/GET/orders"}}),
        statement(Sid="rule", Principal={"Service": "events.amazonaws.com"}, Condition={
            "ArnLike": {"AWS:SourceArn": f"arn:aws:events:{REGION}:{ACCOUNT}:rule/nightly"}}),
        statement(Sid="open", Principal={"Service": "sns.amazonaws.com"}),
        statement(Sid="alexa", Principal={"Service": "alexa-appkit.amazon.com"},
                  Condition={"StringEquals": {"lambda:EventSourceToken": "amzn1.ask.skill.1"}}),
        statement(Sid="partner", Principal={"AWS": "arn:aws:iam::222222222222:role/caller"}),
        statement(Sid="public", Principal="*"),
        statement(Sid="logs", Action="logs:Get*", Principal="*"),
    ]}
    found = {t.statement: t for t in parse_policy(json.dumps(policy))}
    assert (found["s3"].kind, found["s3"].source, found["s3"].unscoped) == ("S3 bucket", "acme-uploads", False)
    assert (found["api"].kind, found["api"].source, found["api"].detail) == (
        "API Gateway", "a1b2c3", "stage prod, GET /orders")
    assert (found["rule"].kind, found["rule"].source, found["rule"].short) == ("EventBridge rule", "nightly",
                                                                               "EventBridge")
    assert found["open"].unscoped and not found["alexa"].unscoped
    assert (found["partner"].kind, found["partner"].source, found["partner"].detail) == (
        "AWS account", "222222222222", "only caller")
    assert found["public"].public and found["public"].kind == "Anyone" and found["public"].short == "Anyone (public)"
    assert "logs" not in found  # not an invoke permission
    vpce = parse_policy({"Statement": [statement(Sid="vpc", Principal="*",
                                                 Condition={"StringEquals": {"aws:SourceVpce": "vpce-1"}})]})
    assert (vpce[0].kind, vpce[0].public, vpce[0].short, vpce[0].detail) == (
        "Any caller meeting its conditions", False, "Conditional", "when aws:SourceVpce")
    assert found["s3"].asynchronous and not found["api"].asynchronous
    assert parse_policy(None) == []


def test_parse_policy_shows_a_public_url_once():
    url = {"Effect": "Allow", "Principal": "*", "Action": "lambda:InvokeFunctionUrl", "Sid": "url",
           "Condition": {"StringEquals": {"lambda:FunctionUrlAuthType": "NONE"}}}
    invoke = {"Effect": "Allow", "Principal": "*", "Action": "lambda:InvokeFunction", "Sid": "url-invoke",
              "Condition": {"Bool": {"lambda:InvokedViaFunctionUrl": "true"}}}
    found = parse_policy({"Statement": [url, invoke]})
    assert len(found) == 1 and found[0].kind == "Function URL" and found[0].public
    signed = parse_policy({"Statement": [{**url, "Condition": {"StringEquals": {"lambda:FunctionUrlAuthType": "AWS_IAM"}}}]})
    assert not signed[0].public and "AWS_IAM" in signed[0].detail


def test_parse_event_source_mapping():
    sqs = parse_event_source_mapping({
        "UUID": "u1", "EventSourceArn": f"arn:aws:sqs:{REGION}:{ACCOUNT}:orders", "State": "Enabled", "BatchSize": 10,
        "MaximumBatchingWindowInSeconds": 5, "FilterCriteria": {"Filters": [{"Pattern": "{}"}]},
        "ScalingConfig": {"MaximumConcurrency": 4}})
    assert (sqs.kind, sqs.source, sqs.short, sqs.uuid) == ("SQS queue", "orders", "SQS", "u1")
    assert sqs.detail == "batches of up to 10, waits up to 5 s to fill one, 1 filter, at most 4 at once"
    stream = parse_event_source_mapping({
        "EventSourceArn": f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/orders/stream/2026-01-01T00:00:00.000",
        "State": "Disabled", "LastProcessingResult": "PROBLEM: Function call failed"})
    assert (stream.kind, stream.source, stream.state) == ("DynamoDB stream", "orders", "Disabled")
    kafka = parse_event_source_mapping({"SelfManagedEventSource": {"Endpoints": {
        "KAFKA_BOOTSTRAP_SERVERS": ["broker:9092"]}}, "Topics": ["clicks"]})
    assert (kafka.kind, kafka.source) == ("Kafka (self-managed)", "broker:9092 (clicks)")


def test_monthly_cost_scales_usage_to_a_month():
    m = metrics(invocations=1_000_000, avg=100.0, days=30, log_bytes=2 * GB)
    cost = function_monthly_cost(fn(memory=1024), m)
    month = lfmod.DAYS_PER_MONTH / 30
    assert cost["requests"] == pytest.approx(0.20 * month)
    assert cost["compute"] == pytest.approx(1_000_000 * 0.1 * 1.0 * 0.0000166667 * month)
    assert cost["logs"] == pytest.approx(2 * 0.50 * month)
    arm = function_monthly_cost(fn(memory=1024, architecture="arm64"), m)
    assert arm["compute"] == pytest.approx(cost["compute"] * 0.0000133334 / 0.0000166667)
    big_tmp = function_monthly_cost(fn(ephemeral_storage=10240), m)
    assert big_tmp["storage"] == pytest.approx(1_000_000 * 0.1 * 9.5 * 0.0000000309 * month)
    held = function_monthly_cost(fn(memory=1024), None, [ProvisionedConcurrency("live", 10, 10, 10, "READY")],
                                 LogGroup("/aws/lambda/etl", None, 10 * GB))
    assert held == pytest.approx({"provisioned": 10 * 730 * 3600 * 0.0000041667, "log_storage": 0.30})
    assert provisioned_monthly_cost(fn(memory=2048), 1) == pytest.approx(2 * 730 * 3600 * 0.0000041667)
    assert function_monthly_cost(fn(), None) == {}
    assert function_monthly_cost(fn(), m, prices={"request": 0.4})["requests"] == pytest.approx(0.40 * month)


def test_findings_on_the_configuration_alone():
    found = function_findings(fn(runtime="python3.9", reserved_concurrency=0, env_names=["DB_PASSWORD", "MODE"],
                                 state="Failed", state_reason="EniLimitExceeded"), now=NOW)
    text = " ".join(message for _, message in found)
    assert ("warn", next(m for m in (message for _, message in found) if m.startswith("python3.9"))) in found
    assert "aws lambda update-function-configuration --function-name etl --region us-east-1 --runtime python3.14" in text
    assert "aws lambda delete-function-concurrency --function-name etl --region us-east-1" in text
    assert "Failed state (EniLimitExceeded)" in text
    assert "Environment variable DB_PASSWORD looks like a secret" in text
    assert function_findings(fn(), now=NOW) == []
    ending = function_findings(fn(runtime="python3.10"), now=NOW)
    assert ending[0][0] == "warn" and "in 25 days" in ending[0][1]


def test_findings_from_cloudwatch_numbers():
    busy = metrics(invocations=10_000, errors=400, throttles=12, longest=29_000, last=NOW - timedelta(days=1))
    found = function_findings(fn(timeout=30), busy, now=NOW)
    levels = {message.split(" ")[0]: level for level, message in found}
    text = " ".join(message for _, message in found)
    assert "400 of 10,000 calls failed (4.0%) in the last 30 days, the last yesterday: errors('etl')" in text
    assert "12 calls were throttled" in text and "function_info('etl') shows its concurrency" in text
    assert "longest run took 29.0 s of its 30 s timeout" in text and "--timeout 60" in text
    assert levels["400"] == "warn"
    old = metrics(invocations=10_000, errors=400, last=NOW - timedelta(days=20))
    assert ("info", next(m for _, m in function_findings(fn(), old, now=NOW) if "calls failed" in m)) in (
        function_findings(fn(), old, now=NOW))
    at_max = function_findings(fn(timeout=900), metrics(longest=899_000), now=NOW)
    assert any("already at the most Lambda allows (900 s)" in message for _, message in at_max)
    slack = function_findings(fn(timeout=900), metrics(longest=2_000), now=NOW)
    assert any("--timeout 10" in message for _, message in slack)
    unused = function_findings(fn(), metrics(invocations=0, longest=None), FunctionDetail(fn()), now=NOW)
    assert unused[0][0] == "info" and "nothing triggers it" in unused[0][1] and "code('etl')" in unused[0][1]
    assert "aws lambda delete-function --function-name etl --region us-east-1" in unused[0][1]
    unread = FunctionDetail(fn(), errors={"policy": "not read"})  # can't tell that nothing triggers it
    assert function_findings(fn(), metrics(invocations=0), unread, now=NOW)[0][1] == (
        "It wasn't called in the last 30 days.")
    triggered = FunctionDetail(fn(), triggers=[Trigger("SQS queue", "orders")])
    assert function_findings(fn(), metrics(invocations=0), triggered, now=NOW)[0][1] == (
        "It wasn't called in the last 30 days.")


def test_cost_findings():
    heavy = metrics(invocations=10_000_000, avg=500.0, log_bytes=500 * GB)
    found = function_findings(fn(memory=2048), heavy, now=NOW)
    text = " ".join(message for _, message in found)
    assert "on arm64 (Graviton) its compute would cost about" in text and "--architectures arm64" in text
    assert "It logs 500.0 GB every 30 days" in text
    slow_small = function_findings(fn(memory=128), metrics(avg=2_000.0), now=NOW)
    assert any("It has 128 MB" in message for _, message in slow_small)


def test_findings_from_the_rest_of_describe():
    detail = FunctionDetail(
        fn(),
        triggers=[Trigger("Anyone", "any AWS account", via="resource policy", public=True, statement="open"),
                  Trigger("Function URL", "https://x.lambda-url.on.aws", via="function URL", public=True),
                  Trigger("SNS topic", "", via="resource policy", principal="sns.amazonaws.com", unscoped=True,
                          statement="sns"),
                  Trigger("SQS queue", "orders", state="Disabled", uuid="u-1"),
                  Trigger("Kinesis stream", "clicks", last_result="PROBLEM: Function call failed")],
        async_config=AsyncConfig(),
        provisioned=[ProvisionedConcurrency("live", 10, 10, 10, "READY"),
                     ProvisionedConcurrency("beta", 2, 0, 0, "FAILED", "Not enough capacity")],
        log_group=LogGroup("/aws/lambda/etl", None, 2 * GB),
        runtime_updates="Manual",
        runtime_version="arn:aws:lambda:us-east-1::runtime:0123456789abcdef",
        versions=[Version(str(i), 30 * MB) for i in range(1, 61)],
    )
    found = function_findings(fn(), metrics(concurrency_max=2.0), detail, now=NOW)
    text = " ".join(message for _, message in found)
    assert "remove-permission --function-name etl --region us-east-1 --statement-id open" in text
    assert "update-function-url-config --function-name etl --region us-east-1 --auth-type AWS_IAM" in text
    assert "lets sns.amazonaws.com invoke it without naming the SNS topic" in text
    assert "update-event-source-mapping --uuid u-1 --enabled --region us-east-1" in text
    assert "Kinesis stream clicks reports 'PROBLEM: Function call failed'" in text
    assert "live keeps 10 copies ready" in text and "at most 2 ran at once" in text
    assert "--provisioned-concurrent-executions 3" in text
    assert "Provisioned concurrency on beta failed (Not enough capacity)" in text
    assert "Events from SNS topic that still fail after 2 retries are dropped" in text
    assert "keeps logs forever: 2.0 GB so far, $0.06/month" in text
    assert "--retention-in-days 30 --region us-east-1" in text
    assert "Runtime updates are Manual" in text and "60 published versions" in text
    idle = function_findings(fn(), metrics(invocations=0), FunctionDetail(fn(), provisioned=[
        ProvisionedConcurrency("live", 5, 5, 5, "READY")]), now=NOW)
    assert idle[0][0] == "warn" and "wasn't called in the last 30 days" in idle[0][1]
    assert "delete-provisioned-concurrency-config --function-name etl --region us-east-1 --qualifier live" in idle[0][1]
    small = FunctionDetail(fn(), log_group=LogGroup("/aws/lambda/etl", None, 500))
    assert function_findings(fn(), detail=small, now=NOW) == []  # a few bytes kept forever isn't worth a note


def test_account_findings():
    tight = AccountLimits(REGION, 1000, 50, 70 * GB, 75 * GB, 40, peak_concurrency=950)
    text = " ".join(message for _, message in account_findings(tight, 30))
    assert "takes 70.0 GB of its 75.0 GB limit (93.3%)" in text
    assert "Up to 950 runs happened at once in us-east-1" in text
    assert "leaves 50 of us-east-1's 1,000 concurrent runs" in text
    assert account_findings(AccountLimits(REGION, 1000, 900, GB, 75 * GB, peak_concurrency=10)) == []


@pytest.mark.parametrize("line, expected", [
    ("[ERROR] KeyError: 'customer_id'\nTraceback (most recent call last):\n  File \"/var/task/app.py\", line 2",
     ("KeyError", "KeyError: 'customer_id'")),
    (f"[ERROR]\t2026-10-05T12:00:00.123Z\t{RID}\tCouldn't save the order", ("Logged error", "Couldn't save the order")),
    (f"2026-10-05T12:00:00.123Z\t{RID}\tERROR\tInvoke Error \t{{\"errorType\":\"TypeError\",\"errorMessage\":"
     "\"Cannot read properties of undefined (reading 'id')\"}",
     ("TypeError", "TypeError: Cannot read properties of undefined (reading 'id')")),
    (f"2026-10-05T12:00:03.003Z {RID} Task timed out after 3.00 seconds", ("Timeout", "Task timed out after 3.00 seconds")),
    (f"RequestId: {RID} Error: Runtime exited with error: signal: killed\nRuntime.ExitError",
     ("Out of memory", "Runtime exited: out of memory (signal: killed)")),
    ("[ERROR] Runtime.ImportModuleError: Unable to import module 'app': No module named 'pandas'",
     ("Runtime.ImportModuleError", "Runtime.ImportModuleError: Unable to import module 'app': No module named 'pandas'")),
    ('{"timestamp":"2026-10-05T12:00:00Z","level":"ERROR","message":{"errorMessage":"\'id\'","errorType":"KeyError",'
     f'"requestId":"{RID}"}}}}', ("KeyError", "KeyError: 'id'")),
    (f'{{"time":"2026-10-05T12:00:00Z","type":"platform.runtimeDone","record":{{"requestId":"{RID}","status":"timeout"}}}}',
     ("Timeout", "Task timed out")),
    ("Traceback (most recent call last):\n  File \"x.py\"\nValueError: bad value", ("ValueError", "ValueError: bad value")),
    ("panic: runtime error: index out of range", ("panic", "panic: runtime error: index out of range")),
    ("processed 0 errors in 12 files", None),
    (f"REPORT RequestId: {RID}\tDuration: 3000.00 ms\tStatus: timeout", None),
    ('{"level":"INFO","message":"ok"}', None),
])
def test_classify_error(line, expected):
    assert classify_error(line) == expected


def test_request_ids_and_report_lines():
    assert request_id_of(f"START RequestId: {RID} Version: $LATEST") == RID
    assert request_id_of('{"requestId": "abc"}') == "abc" and request_id_of("hello") is None
    run_ = parse_report(f"REPORT RequestId: {RID}\tDuration: 102.12 ms\tBilled Duration: 103 ms\tMemory Size: 1024 MB"
                        "\tMax Memory Used: 88 MB\tInit Duration: 300.11 ms\tStatus: timeout")
    assert (run_.request_id, run_.duration, run_.billed, run_.memory, run_.max_memory, run_.init, run_.status) == (
        RID, 102.12, 103.0, 1024, 88, 300.11, "timeout")
    warm = parse_report(f"REPORT RequestId: {RID}\tDuration: 5.0 ms\tBilled Duration: 5 ms\tMemory Size: 128 MB\t"
                        "Max Memory Used: 64 MB\tStatus: error\tError Type: Runtime.OutOfMemory")
    assert warm.init is None and warm.status == "error" and warm.error_type == "Runtime.OutOfMemory"
    record = parse_report(json.dumps({"time": "2026-10-05T12:00:00.000Z", "type": "platform.report", "record": {
        "requestId": RID, "status": "success", "metrics": {"durationMs": 12.5, "billedDurationMs": 13,
                                                           "memorySizeMB": 512, "maxMemoryUsedMB": 70}}}))
    assert (record.duration, record.billed, record.max_memory, record.status) == (12.5, 13.0, 70, None)
    assert record.time == datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    assert parse_report("hello") is None and parse_report('{"type": "platform.start"}') is None


def test_log_lines_drop_what_the_columns_show():
    assert viewmod._log_line(f"2026-10-05T23:28:28.000Z {RID} Task timed out after 60.00 seconds") == (
        "Task timed out after 60.00 seconds")
    assert viewmod._log_line(f"[ERROR]\t2026-10-05T12:00:00.123Z\t{RID}\tCouldn't save") == "[ERROR]\tCouldn't save"
    assert viewmod._log_line(f"2026-10-05T12:00:00.123Z\t{RID}\tERROR\tInvoke Error") == "ERROR\tInvoke Error"
    assert viewmod._log_line("[ERROR] KeyError: 'x'") == "[ERROR] KeyError: 'x'"
    assert viewmod._log_line('{"level": "WARN", "message": "slow"}') == "[WARN] slow"
    assert viewmod._log_line(f"REPORT RequestId: {RID}\tDuration: 5.00 ms\tBilled Duration: 5 ms\tMemory Size: 128 MB"
                           "\tMax Memory Used: 64 MB") == "REPORT: run 5 ms (billed 5 ms), 64 of 128 MB used"
    assert viewmod._lifecycle(f"START RequestId: {RID} Version: $LATEST") and viewmod._lifecycle("INIT_START Runtime")
    assert viewmod._lifecycle('{"time":"2026-10-05T12:00:00Z","type":"platform.start","record":{}}')
    assert not viewmod._lifecycle("hello")


def test_group_errors_by_cause():
    events = [
        LogEvent(NOW - timedelta(minutes=30), "[ERROR] KeyError: 'customer_id'", "s", None),
        LogEvent(NOW - timedelta(minutes=20), f"2026 {RID} Task timed out after 3.00 seconds", "s", RID),
        LogEvent(NOW - timedelta(minutes=10), "[ERROR] KeyError: 'customer_id'", "s", "b" * 8 + RID[8:]),
        LogEvent(NOW - timedelta(minutes=5), "[ERROR] KeyError: 'order_id'", "s", None),
        LogEvent(NOW - timedelta(minutes=1), "all good", "s", None),
    ]
    groups = group_errors(events)
    assert [(g.kind, g.message, g.count) for g in groups] == [
        ("KeyError", "KeyError: 'customer_id'", 2), ("KeyError", "KeyError: 'order_id'", 1), ("Timeout",
                                                                                               "Task timed out after 3.00 seconds", 1)]
    assert groups[0].first == NOW - timedelta(minutes=30) and groups[0].last == NOW - timedelta(minutes=10)
    assert groups[0].request_ids == ["b" * 8 + RID[8:]]
    numbered = group_errors([LogEvent(NOW, "[ERROR] Exception: retry 1 of 3"), LogEvent(NOW, "[ERROR] Exception: retry 2 of 3")])
    assert len(numbered) == 1 and numbered[0].count == 2


def report(groups, function=None, **kwargs):
    return ErrorReport(function or fn(), "/aws/lambda/etl", NOW - timedelta(hours=24), NOW, groups=groups, **kwargs)


def group(kind, message, count=3, example=None, ids=(RID,)):
    return lfmod.ErrorGroup(kind, message, count, NOW - timedelta(hours=5), NOW - timedelta(hours=1),
                            example or message, list(ids))


def test_error_findings_say_what_to_do():
    found = error_findings(report([
        group("Timeout", "Task timed out after 30.00 seconds"),
        group("Out of memory", "Runtime exited: out of memory (signal: killed)", 1),
        group("Runtime.ImportModuleError", "Runtime.ImportModuleError: No module named 'pandas'"),
        group("ClientError", "ClientError: An error occurred (AccessDeniedException)",
              example="botocore.exceptions.ClientError: User: arn:aws:sts::1:assumed-role/etl-role/etl is not "
                      "authorized to perform: dynamodb:PutItem on resource: orders"),
        group("KeyError", "KeyError: 'customer_id'", 7),
    ], function=fn(role=f"arn:aws:iam::{ACCOUNT}:role/etl-role")))
    text = " ".join(message for _, message in found)
    assert "3 runs timed out at the 30 s limit (the last 1h ago): Lambda stopped them part way" in text
    assert "--timeout 60" in text and f"logs('etl', request_id='{RID}') shows that whole run" in text
    assert "1 run ran out of memory at 1,024 MB" in text and "Lambda killed it" in text and "--memory-size 1536" in text
    assert "Its code can't be loaded" in text and "code('etl') shows what's in the package" in text
    assert "its role etl-role isn't allowed dynamodb:PutItem on orders. Add the permission" in text
    assert "The most common other error happened 7 times (the last" in text and "): KeyError: 'customer_id'." in text
    quiet = error_findings(report([], metrics=metrics(invocations=50, errors=4, throttles=2)))
    assert "CloudWatch counted 4 failed calls" in quiet[0][1] and "2 calls were throttled" in quiet[1][1]
    assert error_findings(report([])) == []
    first = error_findings(report([group("Logged error", "Couldn't save", 2)]))
    assert first == [("info", f"The most common error happened twice (the last 1h ago): Couldn't save. "
                              f"logs('etl', request_id='{RID}') shows the whole run.")]


def runs(*specs, memory=1024):
    return [Invocation(f"{i:08d}-0000-0000-0000-000000000000", NOW - timedelta(minutes=i), duration, duration + 1,
                       memory, used, init, status=status)
            for i, (duration, used, init, status) in enumerate(specs)]


def test_percentile_and_memory_suggestions():
    assert percentile([5, 1, 3, 2, 4], 50) == 3 and percentile([5, 1, 3, 2, 4], 99) == 5
    assert percentile([], 50) is None
    assert suggest_memory(88, 1024) == 128 and suggest_memory(700, 1024) is None
    assert suggest_memory(300, 2048) == 512


def test_performance_findings():
    perf = Performance(fn(memory=2048), "/aws/lambda/etl", NOW - timedelta(days=1), NOW,
                       runs(*[(200.0, 100, 1500.0 if i < 5 else None, None) for i in range(40)], memory=2048))
    found = performance_findings(perf)
    text = " ".join(message for _, message in found)
    assert "used at most 100 of the 2,048 MB" in text and "--memory-size 512" in text  # a quarter at most per step
    assert "cold starts" in text and "SnapStart" in text
    tight = Performance(fn(timeout=3, memory=128), "/aws/lambda/etl", NOW - timedelta(days=1), NOW,
                        runs((3000.0, 120, None, "timeout"), (2900.0, 126, None, None), memory=128))
    text = " ".join(message for _, message in performance_findings(tight))
    assert "1 run of 2 timed out at the 3 s limit" in text and "--timeout 6" in text
    assert "A run used 126 of its 128 MB" in text and "--memory-size 256" in text
    killed = Performance(fn(memory=128), "/aws/lambda/etl", NOW - timedelta(days=1), NOW, runs((50.0, 128, None, "error"), memory=128))
    killed.invocations[0].error_type = "Runtime.OutOfMemory"
    assert "1 run ran out of memory at 128 MB: Lambda killed it" in performance_findings(killed)[0][1]
    assert performance_findings(Performance(fn(), "/x", NOW, NOW)) == []


def test_handler_file_and_package_findings():
    names = ["app.py", "pkg/module.py", "src/index.mjs", "nested/app.py"]
    assert handler_file("app.handler", "python3.12", names) == "app.py"
    assert handler_file("pkg.module.handler", "python3.12", names) == "pkg/module.py"
    assert handler_file("src/index.handler", "nodejs22.x", names) == "src/index.mjs"
    assert handler_file("missing.handler", "python3.12", names) is None
    assert handler_file("com.acme.Handler::handleRequest", "java21", names) is None
    assert handler_file("app.handler", "python3.12", ["nested/app.py"]) == "nested/app.py"
    package = CodePackage(fn(), files=[CodeFile("app2.py", 10, 5), CodeFile(".env", 10, 5),
                                       CodeFile("boto3/__init__.py", 3 * MB, MB),
                                       CodeFile("botocore/data/x.json", 200 * MB, 9 * MB),
                                       CodeFile("tests/test_app.py", 2 * MB, MB)])
    text = " ".join(message for _, message in package_findings(package))
    assert "No file in the package matches its handler app.handler (Lambda looks for app.py)" in text
    assert "The package holds .env" in text and "read it" in text
    assert "take 205.0 MB unzipped" in text and "boto3 and botocore (203.0 MB)" in text
    assert "caches, tests or version-control files" in text
    assert package_findings(CodePackage(fn(), files=[CodeFile("app.py", 10, 5)], handler_file="app.py")) == []


def test_file_tree_folds_folders_and_finds_files():
    files = [CodeFile(path, size, 1) for path, size in [
        ("app.py", 10), ("README.md", 5), ("boto3/__init__.py", 100), ("botocore/data/endpoints.json", 1000),
        ("botocore/data/ec2.json", 500), ("pkg/sub/mod.py", 20), ("pkg/util.py", 30), ("Zeta/x.txt", 1)]]
    tree = code_tab._file_tree(files, set())
    assert [(t.label, t.depth, t.folder, t.files, t.size) for t in tree] == [
        ("boto3/", 0, True, 1, 100), ("botocore/data/", 0, True, 2, 1500), ("pkg/", 0, True, 2, 50),
        ("Zeta/", 0, True, 1, 1), ("app.py", 0, False, 1, 10), ("README.md", 0, False, 1, 5)]
    assert tree[1].path == "botocore/data/"  # a folder that holds only a folder shares its line
    opened = [(t.label, t.depth) for t in code_tab._file_tree(files, {"pkg/", "pkg/sub/", "botocore/data/"})]
    assert opened[1:7] == [("botocore/data/", 0), ("ec2.json", 1), ("endpoints.json", 1), ("pkg/", 0), ("sub/", 1),
                           ("mod.py", 2)]
    assert opened[7] == ("util.py", 1)
    found = code_tab._file_tree(files, set(), "json")
    assert [(t.label, t.open) for t in found] == [("botocore/data/", True), ("ec2.json", False),
                                                  ("endpoints.json", False)]
    assert [t.label for t in code_tab._file_tree(files, set(), "json", shut={"botocore/data/"})] == ["botocore/data/"]
    assert code_tab._file_tree(files, set(), "nothing-like-it") == []
    assert code_tab._folders_of("a/b/c.py") == ["a/", "a/b/"] and code_tab._folders_of("c.py") == []
    assert plain(code_tab._tree_face(found[1], [], None)) == "JSON ec2.json 500 B"
    assert "ec2.<mark>json</mark>" in code_tab._tree_face(found[1], ["json"], None)
    assert 'style="--d:1"' in code_tab._tree_face(found[1], [], None)
    assert "HANDLER" in code_tab._tree_face(tree[4], [], "app.py") and " d open" in code_tab._tree_face(found[0], [], None)
    assert code_tab._file_badge(".env") == ("KEY", "key") and code_tab._file_badge("bin/tool") == ("FILE", "")


def test_package_file_name():
    assert package_file_name(fn()) == "etl.zip" and package_file_name(fn(version="7")) == "etl-v7.zip"


def test_secret_like():
    assert secret_like(["DB_PASSWORD", "API_KEY", "STRIPE_SECRET", "DB_SECRET_ARN", "TOKEN_PARAMETER_NAME",
                        "AWS_ACCESS_KEY_ID", "MODE", "GITHUB_TOKEN"]) == ["DB_PASSWORD", "API_KEY", "STRIPE_SECRET",
                                                                          "GITHUB_TOKEN"]


def test_layer_names():
    assert Layer(f"arn:aws:lambda:{REGION}:{ACCOUNT}:layer:pandas:7").name == "pandas:7"
    assert Layer("odd").name == "odd"


RID2 = "1a2b3c4d-0000-4000-8000-000000000002"
RID3 = "1a2b3c4d-0000-4000-8000-000000000003"


def line(seconds, message, stream="s1"):
    return LogEvent(NOW + timedelta(seconds=seconds), message, stream, request_id_of(message))


def report_line(rid, ms=120.0, used=88, init=None, status=None):
    text = (f"REPORT RequestId: {rid}\tDuration: {ms:.2f} ms\tBilled Duration: {int(ms) + 1} ms\tMemory Size: 1024 MB"
            f"\tMax Memory Used: {used} MB")
    return text + (f"\tInit Duration: {init} ms" if init else "") + (f"\tStatus: {status}" if status else "")


def test_split_runs_groups_each_streams_lines_by_run():
    events = [
        line(-30, "still busy from before the window", "s2"),  # a run whose START came before the window
        line(-29, f"END RequestId: {RID3}", "s2"),
        line(-29, report_line(RID3), "s2"),
        line(0, "INIT_START Runtime Version: python:3.12.v40"),  # a cold start's start-up, before its START
        line(1, f"START RequestId: {RID} Version: $LATEST"),
        line(1.1, "processing order 9a8b7c6d-1111-4222-8333-444455556666"),  # a UUID that names no run
        line(1.2, "[ERROR] KeyError: 'customer_id'\nTraceback (most recent call last):"),
        line(1.3, f"END RequestId: {RID}"),
        line(1.3, report_line(RID, init=310.5)),
        line(5, f"START RequestId: {RID2} Version: $LATEST"),
        line(5.5, "halfway"),  # no REPORT yet: still running at the end of the window
        line(9, "an extension's line, in no run", "s3"),
    ]
    runs = split_runs(events)
    by_id = {run.request_id: run for run in runs}
    assert [run.request_id for run in runs] == [None, RID2, RID, RID3]  # newest first
    shuffled = split_runs(reversed(events))  # END after REPORT in the same millisecond still joins its run
    assert [(r.request_id, len(r.events)) for r in shuffled] == [(r.request_id, len(r.events)) for r in runs]
    cold = by_id[RID]
    assert [e.message.split()[0] for e in cold.events] == ["INIT_START", "START", "processing", "[ERROR]", "END",
                                                           "REPORT"]
    assert cold.cold and cold.report.init == 310.5 and cold.report.stream == "s1" and cold.duration == 120.0
    assert (cold.status, cold.error) == ("failed", ("KeyError", "KeyError: 'customer_id'"))
    assert cold.start == NOW and cold.end == NOW + timedelta(seconds=1.3) and cold.key == f"s1|{RID}"
    assert by_id[RID2].status == "running" and by_id[RID2].report is None
    assert [e.message for e in by_id[RID3].events][0] == "still busy from before the window"
    assert by_id[RID3].status == "ok" and not by_id[RID3].cold
    assert by_id[None].status == "outside" and by_id[None].stream == "s3"


def test_split_runs_reads_json_logs_and_concurrent_runs():
    def platform(seconds, kind, rid, **record):
        return line(seconds, json.dumps({"time": "2026-10-06T12:00:00Z", "type": kind,
                                         "record": {"requestId": rid, **record}}))

    def app(seconds, rid, level, message):
        return line(seconds, json.dumps({"level": level, "requestId": rid, "message": message}))

    events = [  # two calls at once in one execution environment: lines carry the request ID they belong to
        platform(0, "platform.start", RID),
        platform(0.1, "platform.start", RID2),
        app(0.2, RID, "INFO", "first"),
        app(0.3, RID2, "WARN", "second"),
        app(0.4, RID, "ERROR", {"errorType": "TypeError", "errorMessage": "x is undefined"}),
        platform(0.5, "platform.report", RID, status="error", errorType="TypeError",
                 metrics={"durationMs": 500, "billedDurationMs": 501, "memorySizeMB": 512, "maxMemoryUsedMB": 70}),
        platform(0.6, "platform.report", RID2, status="success",
                 metrics={"durationMs": 600, "billedDurationMs": 601, "memorySizeMB": 512, "maxMemoryUsedMB": 71,
                          "initDurationMs": 200}),
    ]
    first, second = sorted(split_runs(events), key=lambda r: r.start)
    assert [json.loads(e.message).get("message") for e in first.events][1:3] == ["first", {
        "errorType": "TypeError", "errorMessage": "x is undefined"}]
    assert (first.status, first.duration, first.error[0]) == ("failed", 500.0, "TypeError")
    assert (second.status, second.cold, second.report.max_memory) == ("ok", True, 71)


def test_run_status_tells_failures_from_logged_errors():
    def run(*messages, report=None):
        events = [line(0, f"START RequestId: {RID} Version: $LATEST")]
        events += [line(i + 1, message) for i, message in enumerate(messages)]
        if report is not None:
            events.append(line(9, report))
        return split_runs(events)[0]

    assert run("ok", report=report_line(RID)).status == "ok"
    assert run(f"2026-10-06T12:00:03.003Z {RID} Task timed out after 3.00 seconds",
               report=report_line(RID, status="timeout")).status == "timeout"
    assert run(report=report_line(RID, 3000.0, status="timeout")).status == "timeout"
    logged = run(f"[ERROR]\t2026-10-06T12:00:00.123Z\t{RID}\tCouldn't save, retrying", report=report_line(RID))
    assert (logged.status, logged.error) == ("logged", ("Logged error", "Couldn't save, retrying"))
    oom = run(report=report_line(RID, status="error") + "\tError Type: Runtime.OutOfMemory")
    assert oom.status == "failed" and oom.error == ("Runtime.OutOfMemory", "Runtime.OutOfMemory (error)")
    assert LogRun(None, "s", [line(0, "x")]).status == "outside"


@pytest.mark.parametrize("message, level", [
    (f"START RequestId: {RID} Version: $LATEST", "platform"),
    ("INIT_START Runtime Version: python:3.12.v40", "platform"),
    (report_line(RID), "report"),
    ('{"type": "platform.report", "record": {"requestId": "x"}}', "report"),
    ("[ERROR] KeyError: 'x'", "error"),
    (f"[WARNING]\t2026-10-06T12:00:00.123Z\t{RID}\tslow", "warn"),
    ("[INFO] loaded", "info"),
    (f"2026-10-06T12:00:00.123Z\t{RID}\tDEBUG\tdetails", "debug"),
    ('{"level": "WARN", "message": "slow"}', "warn"),
    ('{"levelname": "INFO", "message": "ok"}', "info"),
    ("WARNING: disk almost full", "warn"),
    ("loaded 12 orders", ""),
])
def test_line_level(message, level):
    assert line_level(message) == level


def test_read_package_without_a_download():
    assert "container image" in read_package(fn(package_type="Image", runtime=None), None).note
    assert read_package(fn(), None).note == "Lambda gave no link to download its code."
    package = read_package(fn(handler="utils.helper"), CODE, file="*.py")
    assert package.shown_file == "app.py" and package.handler_file == "utils.py" and package.size == len(CODE)


# how_it_works(): a package of three files that does most of what map_code() follows
APP = {
    "app/__init__.py": "",
    "app/main.py": '''"""Handles uploads."""
import json
import logging
import os

import boto3
import requests

from .store import Orders
from . import notify

logger = logging.getLogger()
API_KEY = "sk-live-9f8a7b6c5d4e"
_s3 = None


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def handler(event, context):
    """Handles an S3 upload."""
    orders = Orders()
    for i, record in enumerate(event["Records"]):
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
        listing = _client().list_objects_v2(Bucket=bucket, Prefix=key)
        try:
            orders.save({"key": key, "size": len(listing.get("Contents", []))})
        except Exception:
            pass
        answer = requests.post(os.environ.get("HOOK_URL", "https://example.com"), json={"key": key})
        logger.info("posted %s", answer.status_code)
    notify.tell(event.get("detail", {}).get("user"))
    with open("report.csv", "w") as out:
        out.write("done")
    return {"statusCode": 200, "body": json.dumps({"ok": True})}


def unused():
    return 1


if __name__ == "__main__":
    handler({}, None)
''',
    "app/store.py": '''import os
import boto3
import pandas as pd


class Orders:
    """Saves orders."""

    def __init__(self):
        self.table = boto3.resource("dynamodb").Table(os.environ["TABLE"])
        self.sqs = boto3.client("sqs")

    def save(self, item):
        self.table.put_item(Item=item)
        self._queue(item)

    def _queue(self, item):
        self.sqs.send_message(QueueUrl=os.environ["QUEUE_URL"], MessageBody=str(item))
        paginator = self.sqs.get_paginator("list_queues")
        for page in paginator.paginate():
            pass
''',
    "app/notify.py": '''import time
import boto3
import yaml
sns = boto3.client("sns")


def tell(user):
    if user:
        sns.publish(TopicArn="arn:aws:sns:us-east-1:123456789012:team", Message=user)
        time.sleep(2)
''',
    "scripts/backfill.py": "print('one-off')\n",
}
APP_NAMES = list(APP) + ["requests/__init__.py", "requests-2.31.0.dist-info/RECORD"]


def outline(steps, depth=0):
    """
    An outline as lines of text: each step's code and tags, a function it calls under it.
    """
    lines = []
    for step in steps:
        tags = ", ".join(text for _, text in step.notes)
        lines.append("  " * depth + step.code + (f"  [{tags}]" if tags else "") + (" (seen)" if step.seen else ""))
        for call in step.calls:
            lines += outline([call], depth + 2)
        lines += outline(step.steps, depth + 1)
    return lines


def test_map_code_follows_the_handler():
    cmap = map_code(APP, "app.main.handler", names=APP_NAMES,
                    function=fn(name="uploads", handler="app.main.handler", env_names=["TABLE"], timeout=30))
    assert cmap.handler_file == "app/main.py" and cmap.entry == "app.main.handler" and cmap.entry_line == 24
    assert cmap.params == ["event", "context"] and not cmap.is_async and cmap.note is None
    assert cmap.files == 4 and cmap.other_files == 1 and cmap.unreached == 1  # scripts/backfill.py, unused()
    assert outline(cmap.startup) == [
        "from . import notify",
        "    app/notify.py, run when it's imported",
        "      sns = boto3.client('sns')  [makes an SNS client]",
    ]
    steps = outline(cmap.steps)
    assert steps[0] == "def handler(event, context):"
    assert "  orders = Orders()" in steps and "      def __init__(self):" in steps
    assert "  for i, record in enumerate(event['Records']):  [Records]" in steps
    assert "    bucket = record['s3']['bucket']['name']  [Records[].s3.bucket.name]" in steps
    assert "    listing = _client().list_objects_v2(Bucket=bucket, Prefix=key)  [S3 ListObjectsV2]" in steps
    assert "    except Exception:  [drops the error]" in steps
    assert ("    answer = requests.post(os.environ.get('HOOK_URL', 'https://example.com'), json={'key': key})  "
            "[HOOK_URL, HTTP POST]") in steps
    assert "    logger.info('posted %s', answer.status_code)  [logs]" in steps
    assert "  with open('report.csv', 'w') as out:  [writes report.csv]" in steps
    assert steps[-1] == "  return {'statusCode': 200, 'body': json.dumps({'ok': True})}"
    assert not any("__main__" in line or "unused" in line for line in steps)
    # the method a method calls is opened under it, and what a class's __init__ made is followed
    assert ("                  self.sqs.send_message(QueueUrl=os.environ['QUEUE_URL'], MessageBody=str(item))  "
            "[QUEUE_URL, SQS SendMessage]") in steps
    calls = {(c.service, c.operation): c for c in cmap.aws}
    assert list(calls) == [("s3", "ListObjectsV2"), ("dynamodb", "PutItem"), ("sqs", "SendMessage"),
                           ("sqs", "ListQueues"), ("sns", "Publish")]
    assert calls["s3", "ListObjectsV2"].permission == "s3:ListBucket" and calls["s3", "ListObjectsV2"].pages == "one"
    assert calls["sqs", "ListQueues"].pages == "all" and calls["sqs", "SendMessage"].access == "sends"
    assert calls["dynamodb", "PutItem"].loop == "for i, record in enumerate(event['Records']):"
    assert calls["dynamodb", "PutItem"].function == "app.store.Orders.save" and not calls["sns", "Publish"].loop
    assert [(e.name, e.required, e.default) for e in cmap.env] == [
        ("TABLE", True, ""), ("QUEUE_URL", True, ""), ("HOOK_URL", False, "'https://example.com'")]
    assert [(f.label, f.required) for f in cmap.event] == [
        ("Records", True), ("Records[].s3", True), ("Records[].s3.bucket", True), ("Records[].s3.bucket.name", True),
        ("Records[].s3.object", True), ("Records[].s3.object.key", True), ("detail", False), ("detail.user", False)]
    sources = {i.module: i.source for i in cmap.imports}
    assert sources["requests"] == "package" and sources["pandas"] == sources["yaml"] == "missing"
    assert sources["boto3"] == "runtime" and sources["json"] == "stdlib" and sources["app.store"] == "own"
    assert [(f.name, f.depth, f.called_from) for f in cmap.functions] == [
        ("app.main.handler", 0, []), ("app.store.Orders.__init__", 1, ["app.main.handler line 26"]),
        ("app.main._client", 1, ["app.main.handler line 30"]), ("app.store.Orders.save", 1, ["app.main.handler line 32"]),
        ("app.store.Orders._queue", 2, ["app.store.Orders.save line 15"]),
        ("app.notify.tell", 1, ["app.main.handler line 37"])]
    assert cmap.functions[0].doc == "Handles an S3 upload." and cmap.functions[0].lines == 17
    spots = {(s.kind, s.detail): s for s in cmap.spots}
    assert spots["client", "s3"].ok  # made once: behind `if _s3 is None`, into a global
    assert not spots["client", "dynamodb"].ok and not spots["client", "sqs"].ok  # Orders() runs on every call
    assert spots["client", "sns"].ok and spots["client", "sns"].startup
    assert not spots["http", "POST os.environ.get('HOOK_URL', 'https://example.com')"].ok
    assert not spots["write", "report.csv"].ok and spots["sleep", "2"].function == "app.notify.tell"
    assert spots["secret", "API_KEY"].line == 13 and "sk-live" not in repr(cmap)  # the value is never kept
    assert cmap.returns == [("{'statusCode': 200, 'body': json.dumps({'ok': True})}", "app/main.py", 40)]
    df = cmap.to_df()
    assert df["label"].tolist()[0] == "S3 ListObjectsV2" and df["permission"].tolist()[-1] == "sns:Publish"


def test_code_map_findings():
    cmap = map_code(APP, "app.main.handler", names=APP_NAMES,
                    function=fn(name="uploads", handler="app.main.handler", env_names=["TABLE"], timeout=30))
    found = code_map_findings(cmap)
    text = "\n".join(message for _, message in found)
    assert [level for level, _ in found].count("warn") == 7
    assert ("QUEUE_URL isn't set on uploads, and its code reads it with os.environ[...] (app/store.py line 18): that "
            "line fails with KeyError: 'QUEUE_URL'. Add it") in text
    assert "It imports pandas and yaml, which aren't in the package" in text
    assert "every call fails with Runtime.ImportModuleError: No module named 'pandas'" in text
    assert "pip install pandas pyyaml -t ." in text
    assert "1 HTTP request has no timeout (POST os.environ.get" in text and "at its 30 s timeout" in text
    assert ("S3 ListObjectsV2 at app/main.py line 30 reads only its first page: past 1,000 keys the rest is left out"
            in text and "get_paginator('list_objects_v2')" in text)
    assert "Errors are dropped at app/main.py line 33 in handler() (except Exception: pass)" in text
    assert "It writes report.csv at app/main.py line 38, outside /tmp" in text and "(/tmp/report.csv)" in text
    assert "Secret-looking values are written in the code (API_KEY at app/main.py line 13;" in text
    assert "sk-live" not in text
    assert "DynamoDB PutItem runs once per item in for i, record in enumerate(event['Records'])" in text
    assert "table.batch_writer() (BatchWriteItem) sends up to 25 at a time" in text
    assert "It makes a new DynamoDB client on every call, at app/store.py line 10 in Orders()" in text
    assert "It sleeps at app/notify.py line 10 in tell() (time.sleep(2))" in text
    assert "S3 client" not in text  # _client() makes it once
    summary = " ".join(codemap_findings._code_summary(cmap))
    assert summary.startswith("When Lambda starts it (once per cold start), the code makes an SNS client. Each call "
                              "runs handler() in app/main.py. It reads Records[].s3.bucket.name, ")
    assert "which looks like an S3 event" in summary
    assert ("It reads from S3 (ListObjectsV2) and SQS (ListQueues); writes to DynamoDB (PutItem); sends to SQS "
            "(SendMessage) and SNS (Publish); and sends 1 HTTP request.") in summary
    assert "It calls Orders() from app/store.py, _client(), Orders.save() from app/store.py and tell()" in summary
    assert summary.endswith("from app/notify.py and returns a dict with statusCode and body.")
    assert aws_model._sample_event(cmap.event) == {"Records": [{"s3": {"bucket": {"name": "..."},
                                                                    "object": {"key": "..."}}}],
                                               "detail": {"user": "..."}}


def test_map_code_handler_problems():
    def findings(source, handler="app.handler", **kwargs):
        cmap = map_code({"app.py": source}, handler, function=fn(handler=handler, **kwargs))
        return cmap, "\n".join(message for _, message in code_map_findings(cmap))

    cmap, text = findings("def main(event, context):\n    return 1\n\ndef helper(x):\n    return x\n")
    assert cmap.entry is None and cmap.handlers == ["main()"] and cmap.steps == []
    assert ("No function handler() in app.py: every call fails with Runtime.HandlerNotFound before any of its code "
            "runs. Point the handler setting at one of its functions (main() take event and context)") in text
    _, text = findings("async def handler(event, context):\n    return 1\n")
    assert "handler() is async def: the Python runtime calls it without awaiting it" in text
    _, text = findings("def handler(event):\n    return 1\n")
    assert "handler() takes 1 argument, and Lambda passes two (event and context): every call fails with TypeError" in text
    assert "TypeError" not in findings("def handler(*args):\n    return 1\n")[1]
    assert "TypeError" not in findings("def handler(event, context, extra=None):\n    return 1\n")[1]
    web = "from mangum import Mangum\nfrom fastapi import FastAPI\napp = FastAPI()\nhandler = Mangum(app)\n"
    assert "TypeError" not in findings(web)[1]  # an object (a web app's adapter) takes (event, context) itself
    cmap, text = findings("def handler(event, context)\n    return 1\n", runtime="python3.10")  # no newer than here
    assert cmap.skipped == [("app.py", "line 1: expected ':'")] and cmap.entry is None
    assert "app.py doesn't parse (line 1: expected ':'): Lambda can't load it either" in text
    assert "HandlerNotFound" not in text
    cmap, text = findings("def handler(event, context):\n    return 1\n", runtime="python3.99")
    assert text == ""
    cmap, text = findings("print 'hello'\ndef handler(event, context):\n    return 1\n", runtime="python3.99")
    assert "doesn't parse with this notebook's Python" in text and "syntax only Python 3.99 knows" in text
    assert map_code({"app.py": "x = 1\n"}, "nodot").note.startswith("Its handler setting (nodot) doesn't name")
    assert "Lambda looks for main.py" in map_code({"app.py": "x = 1\n"}, "main.handler").note


def test_map_code_imports_and_layers():
    source = ("import imp\nimport requests\ntry:\n    import ujson as json\nexcept ImportError:\n    import json\n"
              "import tomllib\n\ndef handler(event, context):\n    return requests.get('https://x', timeout=5).json()\n")
    old = map_code({"app.py": source}, "app.handler", function=fn(runtime="python3.10"))
    sources = {(i.module, i.optional): i.source for i in old.imports}
    assert sources == {("imp", False): "stdlib", ("requests", False): "missing", ("ujson", True): "missing",
                       ("json", False): "stdlib", ("tomllib", False): "missing"}  # the fallback is needed
    text = "\n".join(message for _, message in code_map_findings(old))
    assert "It imports requests and tomllib, which aren't in the package" in text and "ujson" not in text
    assert "It imports imp, which Python 3.12 removed: moving it to python3.14 breaks it" in text
    assert "(imp by importlib)" in text and "timeout" not in text
    new = map_code({"app.py": source}, "app.handler",
                   function=fn(runtime="python3.13", layers=[Layer(f"arn:aws:lambda:{REGION}:{ACCOUNT}:layer:http:3")]))
    text = "\n".join(message for _, message in code_map_findings(new))
    assert "It imports imp (app.py line 1), which Python 3.12 removed: on python3.13 that fails" in text
    assert "Use importlib instead" in text and "aren't in the package" not in text
    assert "requests isn't in the package, so it has to come from its layers (http:3)" in text


def test_map_code_follows_routes_and_more():
    source = '''
import boto3
from aws_lambda_powertools.event_handler import APIGatewayRestResolver

app = APIGatewayRestResolver()
table = boto3.resource("dynamodb").Table("orders")
bucket = boto3.resource("s3").Bucket("acme")


@app.get("/orders/<id>")
def get_order(id):
    return table.get_item(Key={"id": id})["Item"]


@app.route("/orders", methods=["POST"])
def add_order():
    with table.batch_writer() as batch:
        batch.put_item(Item=app.current_event.json_body)
    for obj in bucket.objects.filter(Prefix="in/"):
        obj.delete()
    return {"ok": True}


def handler(event, context):
    return app.resolve(event, context)


def countdown(n):
    if n:
        countdown(n - 1)
'''
    cmap = map_code({"api.py": source}, "api.handler")
    assert cmap.routes == [("GET", "/orders/<id>", "api.get_order"), ("POST", "/orders", "api.add_order")]
    assert [(c.operation, c.pages) for c in cmap.aws] == [
        ("GetItem", ""), ("BatchWriteItem", ""), ("ListObjects", "all"), ("DeleteObject", "")]
    steps = outline(cmap.steps)
    assert "      def get_order(id):  [GET /orders/<id>]" in steps
    assert "        with table.batch_writer() as batch:" in steps
    summary = " ".join(codemap_findings._code_summary(cmap))
    assert "answers 2 web routes (GET /orders/<id> and POST /orders)" in summary
    assert "It reads from DynamoDB (GetItem) and S3 (ListObjects); writes to DynamoDB (BatchWriteItem) and S3" in summary
    loop = map_code({"app.py": source.replace("return app.resolve(event, context)", "countdown(3)")}, "app.handler")
    assert outline(loop.steps)[-1] == "              def countdown(n):  [calls itself: not followed again] (seen)"


def test_map_code_never_shows_a_secret():
    source = ("import boto3\ns3 = boto3.client('s3', aws_access_key_id='AKIAABCDEFGHIJKLMNOP', "
              "aws_secret_access_key='wJalrXUtnFEMI/K7MDENG')\nDB = {'password': 'hunter2-prod'}\n\n"
              "def handler(event, context):\n    connect(password='pa55word!')\n"
              "    return s3.get_object(Bucket='b', Key=event['k'])\n")
    cmap = map_code({"app.py": source}, "app.handler")
    assert cmap.startup[0].code == "s3 = boto3.client('s3', aws_access_key_id='…', aws_secret_access_key='…')"
    assert [(s.detail, s.line) for s in cmap.spots if s.kind == "secret"] == [
        ("aws_secret_access_key", 2), ("password", 3), ("password", 6), ("an AWS access key", 2)]
    shown = repr(cmap) + "".join(m for _, m in code_map_findings(cmap))
    assert not any(secret in shown for secret in ("AKIAABCD", "wJalr", "hunter2", "pa55word"))
    broken = reader._CodeReader._call
    try:
        reader._CodeReader._call = lambda self, node, frame: 1 / 0  # a bug in following some code
        partial = map_code({"app.py": source}, "app.handler")
    finally:
        reader._CodeReader._call = broken
    assert partial.limited == "stopped partway, at code it couldn't follow (ZeroDivisionError: division by zero)"
    guessed = map_code({"app.py": "def handler(event, context):\n    for x in event['xs']:\n"
                                  "        store.put_item(Item=x)\n"}, "app.handler")
    assert [(c.label, c.guessed) for c in guessed.aws] == [("DynamoDB PutItem", True)]
    assert code_map_findings(guessed) == []  # a call guessed from its name alone sets off nothing


def test_code_graph():
    cmap = map_code(APP, "app.main.handler", names=APP_NAMES,
                    function=fn(name="uploads", handler="app.main.handler", env_names=["TABLE"], timeout=30))
    graph = code_graph(cmap)
    nodes = {n.id: (n.kind, n.column, n.label, n.tone) for n in graph.nodes}
    assert nodes == {
        "event": ("event", 0, "An S3 event", ""), "env": ("env", 0, "Environment", "warn"),
        "start": ("start", 1, "At start-up", ""), "f0": ("handler", 1, "handler()", "warn"),
        "f1": ("function", 2, "Orders()", ""), "f2": ("function", 2, "_client()", ""),
        "f3": ("function", 2, "Orders.save()", ""), "f4": ("function", 2, "tell()", ""),
        "f5": ("function", 3, "Orders._queue()", ""), "aws-s3": ("aws", 4, "S3", "warn"),
        "aws-dynamodb": ("aws", 4, "DynamoDB", ""), "aws-sqs": ("aws", 4, "SQS", ""),
        "aws-sns": ("aws", 4, "SNS", ""), "http": ("http", 4, "The web", "warn")}
    assert graph.node("event").lines == ["Records[].s3.bucket.name", "Records[].s3.object.key", "detail.user"]
    assert graph.node("aws-sqs").sub == "sends · reads" and graph.node("aws-sqs").lines == ["SendMessage", "ListQueues"]
    assert graph.node("f0").sub == "app/main.py line 24" and graph.node("f0").lines == ["Handles an S3 upload."]
    edges = {(e.source, e.target, e.kind): (e.label, e.tone, e.loop) for e in graph.edges}
    assert edges == {
        ("event", "f0", "event"): ("the event", "", False), ("f0", "f1", "call"): ("line 26", "", False),
        ("f0", "f2", "call"): ("line 30", "", False), ("f0", "f3", "call"): ("line 32", "", False),
        ("f0", "f4", "call"): ("line 37", "", False), ("f3", "f5", "call"): ("line 15", "", False),
        ("env", "f1", "env"): ("TABLE", "", False), ("env", "f5", "env"): ("QUEUE_URL", "warn", False),
        ("env", "f0", "env"): ("HOOK_URL", "", False), ("f0", "aws-s3", "reads"): ("ListObjectsV2", "warn", True),
        ("f3", "aws-dynamodb", "writes"): ("PutItem", "", True), ("f5", "aws-sqs", "sends"): ("SendMessage", "", True),
        ("f5", "aws-sqs", "reads"): ("ListQueues", "", True), ("f4", "aws-sns", "sends"): ("Publish", "", False),
        ("start", "aws-sns", "client"): ("makes its SNS client", "", False),
        ("f0", "http", "http"): ("POST os.environ.get('HOOK_URL', 'https://example.com')", "warn", True)}
    assert graph.hidden == 0
    mermaid = graph.mermaid()
    assert mermaid.startswith("flowchart LR\n  event([\"An S3 event<br/><small>Lambda calls it with</small>\"])")
    assert '  aws_s3[("S3<br/><small>reads</small>")]' in mermaid and "  f0 --> f1" in mermaid
    assert '  f0 -->|"ListObjectsV2"| aws_s3' in mermaid and '  env -.->|"QUEUE_URL"| f5' in mermaid
    assert ('  f0 -->|"POST os.environ.get(\'HOOK_URL\', \'https://example.com\')"| http' in mermaid
            and mermaid.endswith("  class env,f0,aws_s3,http warn"))
    assert code_graph(map_code({"app.py": "x = 1\n"}, "app.handler")).nodes == []  # no handler: nothing to draw

    # a chain deeper than the graph draws: the rest are counted, not drawn
    chain = "".join(f"def f{i}():\n    return f{i + 1}()\n\n" for i in range(8)) + "def f8():\n    return 1\n"
    deep = code_graph(map_code({"app.py": chain + "def handler(event, context):\n    return f0()\n"}, "app.handler"))
    assert deep.hidden == 4 and max(n.column for n in deep.nodes if n.kind == "function") == 1 + codemap_graph._GRAPH_DEPTH
    assert {e.target for e in deep.edges} <= {n.id for n in deep.nodes}  # no arrow to a box that isn't drawn
    whole = code_graph(map_code({"app.py": chain + "def handler(event, context):\n    return f0()\n"}, "app.handler"),
                       max_functions=None, max_depth=None, max_lines=None)
    assert whole.hidden == 0 and len([n for n in whole.nodes if n.kind == "function"]) == 9
    assert not any(e.indirect for e in whole.edges)
    variables = "".join(f"    os.environ['V{i}']\n" for i in range(8))
    many = map_code({"app.py": f"import os\n\ndef handler(event, context):\n{variables}"}, "app.handler")
    assert code_graph(many).node("env").lines[-1] == "+4 more" and code_graph(many).shortened == 1
    every = code_graph(many, max_lines=None)
    assert every.node("env").lines == [f"V{i}" for i in range(8)] and every.shortened == 0
    assert code_graph(many).what_was_cut() == "shortens 1 box's list" and every.what_was_cut() == ""
    assert deep.what_was_cut() == "leaves out 4 functions (too deep or too many)"

    # a handler that's an object (a web app's adapter): its routes hang off it
    app = ("from mangum import Mangum\nfrom fastapi import FastAPI\nimport boto3\napp = FastAPI()\n"
           "s3 = boto3.client('s3')\n\n@app.get('/files')\ndef files():\n    return s3.list_objects_v2(Bucket='b')\n\n"
           "handler = Mangum(app)\n")
    web = code_graph(map_code({"main.py": app}, "main.handler"))
    assert web.node("f-entry").label == "handler" and web.node("f-entry").lines == ["an object, not a function"]
    assert ("f-entry", "f0", "call", "GET /files") in {(e.source, e.target, e.kind, e.label) for e in web.edges}


def test_graph_layout_and_drawing():
    cmap = map_code(APP, "app.main.handler", names=APP_NAMES,
                    function=fn(name="uploads", handler="app.main.handler", env_names=["TABLE"], timeout=30))
    graph = code_graph(cmap)
    cards = graph_block._g_cards(graph)
    boxes, routes, width, tall, xs, widths, column_of = graph_block._graph_layout(graph, cards)
    assert set(boxes) == {n.id for n in graph.nodes} and sorted(xs) == list(range(5))
    assert all(xs[k + 1] == xs[k] + widths[k] + graph_block._GRAPH_GAP for k in range(4))  # columns left to right
    assert all(graph_block._GRAPH_MIN_W <= w <= graph_block._GRAPH_MAX_FLAGGED for w in widths.values())
    assert widths[4] > graph_block._GRAPH_MAX_W  # the S3 box's ListObjectsV2 is whole beside its flag
    assert width == xs[4] + widths[4] + graph_block._GRAPH_PAD_X  # no arrow goes back, so no room kept to curl round
    by_column = defaultdict(list)
    for n in graph.nodes:
        assert boxes[n.id][0] == xs[column_of[n.id]] and boxes[n.id][2] == widths[column_of[n.id]]
        by_column[column_of[n.id]].append(boxes[n.id])
    for column in by_column.values():  # no two boxes in a column overlap
        column.sort(key=lambda b: b[1])
        assert all(a[1] + a[3] + graph_block._GRAPH_ROW_GAP - 0.1 <= b[1] for a, b in zip(column, column[1:]))
    assert all(graph_block._GRAPH_BAND <= y and y + h <= tall for _, y, _, h in boxes.values())  # under the captions
    skipping = {i for i, e in enumerate(graph.edges) if abs(column_of[e.target] - column_of[e.source]) > 1}
    assert set(routes) == skipping and skipping  # an arrow that skips columns has a waypoint in each
    for i, ys in routes.items():
        e = graph.edges[i]
        for k, y in enumerate(ys):  # ...which runs between the boxes of the column it crosses, not behind one
            crossed = column_of[e.source] + k + 1
            assert not any(by <= y <= by + bh for bx, by, _, bh in boxes.values() if bx == xs[crossed])
    ends = graph_block._g_anchors(graph, cards, boxes)
    writes = next(i for i, e in enumerate(graph.edges) if e.target == "aws-dynamodb")
    assert ends[writes][1] == [boxes["aws-dynamodb"][1] + cards["aws-dynamodb"].row_y("PutItem")]  # lands on its row
    reads = next(i for i, e in enumerate(graph.edges) if e.kind == "env" and e.target == "f5")
    assert ends[reads][0] == [boxes["env"][1] + cards["env"].row_y("QUEUE_URL")]  # leaves from the variable's row

    page = stylemod._render_html([graph_block._Graph(graph, "How it fits together")], 50)
    assert page.count('<g class="cn ') == len(graph.nodes) and page.count('<g class="ce ek-') == 2 * len(graph.edges)
    assert '<g class="cn k-handler n-f0 a-event a-f1' in page and '<g class="cn k-env n-env a-f1 a-f5 a-f0 warn"' in page
    assert ('<g class="ce ek-reads s-f0 t-aws-s3 warn"><title>handler() reads from S3: ListObjectsV2; in a loop, once '
            'per item; a warning is about it</title>') in page and '<path class="halo" d="M' in page
    assert ">QUEUE_URL</text>" in page and ">not set</text>" in page and ">first page only</text>" in page
    assert ">ListObjectsV2</text>" in page and '<path class="cs" d="M' in page
    assert ">no timeout</text>" in page and '<circle class="bu flagged"' in page  # what a warning is about, marked
    assert ">COMES IN</text>" in page and ">HANDLER</text>" in page and ">REACHES</text>" in page
    assert ".lmb .cg:has(.n-f0:hover) .ce:not(.s-f0):not(.t-f0){opacity:.06}" in page  # pointing at a box
    assert ".lmb .cg:has(.n-f0:hover) .cn:not(.n-f0):not(.a-f0){opacity:.28}" in page
    assert '<svg class="sw ek-reads"' in page and "</svg>reads</span>" in page and "in a loop: once per item" in page
    assert "a warning is about it</span>" in page and "guessed" not in page and "not drawn" not in page
    assert "<script" not in page and f'viewBox="0 0 {width:.0f} {tall:.0f}"' in page
    text = _render_text([graph_block._Graph(graph, "How it fits together")], 50)
    assert "-- How it fits together --" in text
    assert "  An S3 event: Records[].s3.bucket.name, Records[].s3.object.key, detail.user\n      → handler()" in text
    assert ("  Environment: TABLE, QUEUE_URL (not set), HOOK_URL (!)\n      → Orders() reads TABLE\n"
            "      → Orders._queue() reads QUEUE_URL (!)") in text
    assert "  handler()  app/main.py line 24 (!)\n      calls Orders() (line 26)" in text
    assert "      reads from S3: ListObjectsV2, in a loop (!)" in text and "      makes the SNS client" in text
    assert "  Orders()  app/store.py line 9\n      (does nothing this follows: no AWS calls, own functions or HTTP)" in text
    assert "S3  reads" not in text  # what it reaches shows as the arrows that reach it
    empty = graph_block._Graph(lfmod.CodeGraph(), "How it fits together")
    assert "(nothing to draw)" in stylemod._render_html([empty], 50) and "(nothing to draw)" in _render_text([empty], 50)

    # a call back to an earlier column, one within a column, a guessed call and one through functions not drawn
    nodes = [lfmod.GraphNode("f0", "handler", "handler()", "app.py line 1", column=1),
             lfmod.GraphNode("f1", "function", "a()", "app.py line 5", column=2),
             lfmod.GraphNode("f2", "function", "b()", "app.py line 9", column=3),
             lfmod.GraphNode("f3", "function", "c()", "app.py line 13", column=3),
             lfmod.GraphNode("aws-sqs", "aws", "SQS", "sends", ["SendMessage"], column=4)]
    edges = [lfmod.GraphEdge("f0", "f1", "call", "line 2"), lfmod.GraphEdge("f1", "f2", "call", "line 6"),
             lfmod.GraphEdge("f2", "f0", "call", "line 10"), lfmod.GraphEdge("f2", "f3", "call", "line 11"),
             lfmod.GraphEdge("f3", "aws-sqs", "sends", "SendMessage", guessed=True),
             lfmod.GraphEdge("f1", "aws-sqs", "sends", "SendMessage", indirect=True)]
    odd = lfmod.CodeGraph(nodes, edges)
    cards = graph_block._g_cards(odd)
    boxes, routes, width, tall, xs, widths, column_of = graph_block._graph_layout(odd, cards)
    assert set(routes) == {2, 5} and len(routes[2]) == 1  # the call back to handler() runs between b()'s neighbours
    assert width == xs[3] + widths[3] + graph_block._GRAPH_PAD_X  # the loop from b() round to c() curls in a gap
    page = stylemod._render_html([graph_block._Graph(odd, "")], 50)
    assert '<g class="ce ek-sends s-f3 t-aws-sqs guess">' in page and '<g class="ce ek-sends s-f1 t-aws-sqs via">' in page
    assert "guessed from a method's name</span>" in page and "through functions not drawn</span>" in page

    # a warned arrow that carries two variables glows only on the line from the one the warning is about
    env = lfmod.GraphNode("env", "env", "Environment", "variables it reads", ["TABLE", "TOPIC"], "warn", column=0,
                          flags={"TOPIC": "not set"})
    two = lfmod.CodeGraph([env, nodes[0]], [lfmod.GraphEdge("env", "f0", "env", "TABLE, TOPIC", "warn")])
    page = stylemod._render_html([graph_block._Graph(two, "")], 50)
    lines = re.search(r'<path class="ln" d="([^"]*)"', page).group(1)
    halo = re.search(r'<path class="halo" d="([^"]*)"', page).group(1)
    assert lines.count("M") == 2 and halo.count("M") == 1 and halo in lines and ">not set</text>" in page

    # a service reached in four ways: the words under its name are cut to fit, never past the card
    sfn = lfmod.GraphNode("aws-states", "aws", "Step Functions", "runs · reads · sends · writes", ["StartExecution"],
                          column=2)
    page = stylemod._render_html([graph_block._Graph(lfmod.CodeGraph([nodes[0], sfn], [
        lfmod.GraphEdge("f0", "aws-states", "runs", "StartExecution")]), "")], 50)
    assert '<tspan class="ac ek-runs">runs</tspan>' in page and '<tspan class="sep"> · </tspan>…</text>' in page

    # two requests, one whose code has ', ' in it: each arrow lands on its own row
    two = map_code({"app.py": "import os, requests\n\ndef handler(event, context):\n"
                              "    requests.post(os.environ.get('HOOK', 'https://a.example'), json={})\n"
                              "    return requests.get('https://b.example', timeout=2)\n"}, "app.handler")
    graph = code_graph(two)
    cards = graph_block._g_cards(graph)
    boxes = graph_block._graph_layout(graph, cards)[0]
    ends = graph_block._g_anchors(graph, cards, boxes)
    sent = next(i for i, e in enumerate(graph.edges) if e.kind == "http")
    assert sorted(ends[sent][1]) == sorted(boxes["http"][1] + cards["http"].row_y(r) for r in cards["http"].rows)

    # a function that answers two routes keeps both
    web = map_code({"main.py": "from mangum import Mangum\nfrom fastapi import FastAPI\napp = FastAPI()\n\n"
                               "@app.get('/items')\n@app.head('/items')\ndef items():\n    return []\n\n"
                               "handler = Mangum(app)\n"}, "main.handler")
    graph = code_graph(web)
    assert [e.label for e in graph.edges if e.kind == "call"] == ["GET /items; HEAD /items"]
    page = stylemod._render_html([graph_block._Graph(graph, "")], 50)
    assert '<tspan class="mt">GET</tspan> /items +1</text>' in page


def test_map_package_reads_its_own_files():
    data = zipped({"app.py": APP["app/main.py"].replace("from .store", "from store").replace("from . import", "import"),
                   "store.py": APP["app/store.py"], "notify.py": APP["app/notify.py"],
                   "requests/__init__.py": "def get(url):\n    return boto3.client('s3').get_object()\n",
                   "requests-2.31.0.dist-info/RECORD": "requests/__init__.py,,\n",
                   "yaml/__init__.py": "", "tests/test_app.py": "def test(): pass\n"})
    cmap = map_package(fn(handler="app.handler", env_names=["TABLE", "QUEUE_URL"]), data)
    assert cmap.files == 3 and cmap.entry == "app.handler" and cmap.other_files == 0
    assert {i.module: i.source for i in cmap.imports}["yaml"] == "package"
    assert not any(c.file.startswith("requests/") for c in cmap.aws)  # a library isn't read as its code
    assert "container image" in map_package(fn(package_type="Image", runtime=None), None).note
    assert "It runs nodejs20.x: how_it_works() reads Python code" in map_package(fn(runtime="nodejs20.x"), b"").note
    assert map_package(fn(), None).note == "Lambda gave no link to download its code."
    with pytest.raises(ValueError, match="isn't a readable .zip"):
        map_package(fn(), b"not a zip")


def test_search_rank_finds_by_any_field():
    assert search_rank("etl", ["etl", "x"]) == 0 and search_rank("orders", ["orders-etl"]) == 1
    assert search_rank("ETL", ["orders-etl"]) == 2 and search_rank("orders nightly", ["orders-etl", "nightly"]) == 3
    assert search_rank("nope", ["orders-etl"]) is None and search_rank("", []) == 3


def test_window_lines_drop_what_the_columns_show():
    parts = logs_tab._line_parts
    assert parts(f"[ERROR]\t2026-10-05T12:00:00.123Z\t{RID}\tCouldn't save") == ("Couldn't save", "")
    assert parts(f"2026-10-05T12:00:00.123Z\t{RID}\tINFO\tready") == ("ready", "")
    assert parts(f"2026-10-05T23:28:28.000Z {RID} Task timed out after 60.00 seconds") == (
        "Task timed out after 60.00 seconds", "")
    text, fields = parts('{"level": "INFO", "message": "200 in 84 ms", "statusCode": 200, "requestId": "x"}')
    assert text == "200 in 84 ms" and json.loads(fields) == {"statusCode": 200}
    error = parts(json.dumps({"level": "ERROR", "message": {
        "errorType": "TypeError", "errorMessage": "x is undefined",
        "stack": ["TypeError: x is undefined", "    at handler (index.mjs:4:2)"]}}))
    assert error == ("TypeError: x is undefined\n    at handler (index.mjs:4:2)", "")
    logged = parts(json.dumps({"level": "ERROR", "message": "failed", "exception": "Traceback ...\nKeyError: 'x'",
                               "service": "orders"}))
    assert logged[0] == "failed\nTraceback ...\nKeyError: 'x'" and json.loads(logged[1]) == {"service": "orders"}
    assert parts('{"time": "t", "type": "platform.start", "record": {"requestId": "r"}}')[0].startswith(
        "platform.start {")


def test_window_text_points_at_the_tabs():
    text = frame._window_text
    assert text("errors('etl') groups them by cause.") == "The Errors tab groups them by cause."
    assert text(f"It failed. logs('etl', request_id='{RID}') shows that whole run.") == (
        f"It failed. Run {RID[:8]} in the Logs tab shows that whole run.")
    assert text("retried later. function_info('etl') shows its concurrency.") == (
        "retried later. The Settings tab shows its concurrency.")
    assert text("functions() lists them, and functions(regions='all') looks in every region.") == (
        "The Functions tab lists them, and All regions in the region field looks in every region.")
    assert "aws lambda update-function-configuration" in text(
        "Raise it: aws lambda update-function-configuration --function-name etl --timeout 60.")


def test_wiring_and_columns_render_as_html_and_text():
    wiring = wiring_block._Wiring([("SQS queue", "orders", "batches of up to 10", ""), ("Function URL", "anyone", "", "warn")],
                           ("etl", "python3.12 · 1,024 MB"), [("On failure", "nowhere: dropped", "", "warn")],
                           title="How it's wired")
    page = stylemod._render_html([wiring], 50)
    assert '<div class="wire">' in page and "orders" in page and 'class="wn warn"' in page and "λ" in page
    out = _render_text([wiring], 50)
    assert "Called by: SQS queue orders; Function URL anyone (!)" in out and "Then:      On failure: nowhere" in out
    assert "Nothing calls it on its own" in stylemod._render_html([wiring_block._Wiring([], ("etl", ""), [])], 50)
    columns = wiring_block._Columns([("Oct 1", 10.0, 2.0, "10 calls"), ("Oct 2", 0.0, 0.0, "none"), ("Oct 3", 5.0, 0.0, "")],
                             title="Calls a day")
    page = stylemod._render_html([columns], 50)
    assert page.count("<i ") == 3 and 'title="10 calls"' in page and "height:20.0%" in page
    assert "Oct 1 █ ▄ Oct 3   (the most: 10, Oct 1)" in _render_text([columns], 50)
    timed = wiring_block._Columns([("a", 900.0, 100.0, "")], part="dim", limit=1000.0, limit_label="timeout 1 s", unit="ms")
    assert 'class="lim"' in stylemod._render_html([timed], 50) and "the most: 900 ms" in _render_text([timed], 50)


OLD_CODE = zipped({
    "app.py": 'import os\nMODE = os.environ["MODE"]\n\n\ndef handler(event, context):\n    return load(event)\n\n\n'
              '@cached\ndef load(event):\n    return event["id"]\n',
    "store.py": "def save(rows):\n    return len(rows)\n",
    "gone.py": "x = 1\n",
    "requests-2.31.0.dist-info/RECORD": "requests/api.py,,\n",
    "requests/api.py": "def get():\n    return 1\n",
    "node_modules/left-pad/package.json": '{"name": "left-pad", "version": "1.2.0"}',
    "logo.png": b"\x89PNG\x00\x00",
    ".env": "DB_PASSWORD=hunter2\n",
})

NEW_CODE = zipped({
    "app.py": 'import os\nMODE = os.environ["MODE"]\n\n\ndef handler(event, context):\n    return load(event)\n\n\n'
              '@cached\ndef load(event):\n    return event["customer_id"]\n',
    "store.py": "def save(rows:\n    return len(rows)\n",
    "added.py": "class Orders:\n    def add(self):\n        pass\n",
    "requests-2.32.3.dist-info/RECORD": "requests/api.py,,\n",
    "requests/api.py": "def get():\n    return 2\n",
    "node_modules/left-pad/package.json": '{"name": "left-pad", "version": "1.3.0"}',
    "logo.png": b"\x89PNG\x00\x00\x01",
    ".env": "DB_PASSWORD=swordfish\n",
    "bundle.js": "x" * 3000,
})


def versioned(version, sha, **kwargs):
    return fn(version=version, raw={"CodeSha256": sha}, **kwargs)


def test_stream_version_and_qualifiers():
    assert helpers.stream_version("2026/10/22/[3]8f5ce35b") == "3"
    assert helpers.stream_version("2026/10/22/[$LATEST]8f5c") == "$LATEST"
    assert helpers.stream_version("no version here") is None
    assert [helpers.parse_qualifier(v) for v in (3, "3", "v3", "latest", "$LATEST", "prod")] == [
        "3", "3", "3", "$LATEST", "$LATEST", "prod"]
    with pytest.raises(ValueError, match="isn't a version or an alias"):
        helpers.parse_qualifier("not ok!")
    events = [LogEvent(NOW - timedelta(minutes=m), "[ERROR] KeyError: 'id'", stream=f"2026/10/06/[{v}]ab")
              for m, v in ((30, "2"), (10, "3"), (5, "3"))]
    assert group_errors(events)[0].versions == ["3", "2"]


def test_diff_packages_compares_files_lines_and_libraries():
    old = versioned("2", "old", env_names=["MODE", "TABLE"], timeout=900, memory=1024, runtime="python3.11")
    new = versioned("$LATEST", "new", env_names=["TABLE", "BUCKET"], timeout=30, memory=1024, runtime="python3.12")
    diff = diff_packages(old, OLD_CODE, new, NEW_CODE)
    files = {change.path: change for change in diff.files}
    assert [change.path for change in diff.files][0] == "app.py"
    assert {path: change.status for path, change in files.items()} == {
        "app.py": "changed", ".env": "changed", "added.py": "added", "bundle.js": "added", "gone.py": "removed",
        "logo.png": "changed", "store.py": "changed"}
    app = files["app.py"]
    assert (app.added, app.removed, app.definitions) == (1, 1, ["load"])
    assert app.lines == ['@@ -8,4 +8,4 @@', ' ', ' @cached', ' def load(event):', '-    return event["id"]',
                         '+    return event["customer_id"]']
    assert files["added.py"].definitions == ["Orders", "Orders.add"] and files["added.py"].added == 3
    assert files["gone.py"].definitions == ["(top level)"] and files["gone.py"].removed == 1
    assert "secrets file" in files[".env"].note and not files[".env"].lines
    assert "swordfish" not in json.dumps([change.lines for change in diff.files])
    assert files["logo.png"].note == "not a source file: compared by size"
    assert files["bundle.js"].note.startswith("minified")
    assert diff.broken == {"store.py": "SyntaxError at line 1"}
    assert [(c.name, c.old, c.new) for c in diff.libraries] == [("left-pad", "1.2.0", "1.3.0"),
                                                                ("requests", "2.31.0", "2.32.3")]
    assert diff.library_files == 4 and diff.unchanged_files == 0
    assert [(c.setting, c.old, c.new) for c in diff.settings] == [("Runtime", "python3.11", "python3.12"),
                                                                  ("Timeout", "900 s", "30 s")]
    assert (diff.env_added, diff.env_removed, diff.still_read) == (["BUCKET"], ["MODE"], {"MODE": "app.py:2"})

    found = diff_findings(diff)
    text = " ".join(message for _, message in found)
    assert [level for level, _ in found] == ["warn", "warn", "info", "info", "info"]
    assert "store.py has a SyntaxError at line 1 in $LATEST that version 2 didn't have" in text
    assert "code('etl', 'store.py')" in text
    assert "MODE was taken out of its environment variables in $LATEST, but app.py:2 still reads it" in text
    assert "2 libraries packed with it changed in $LATEST: left-pad 1.2.0 → 1.3.0, requests 2.31.0 → 2.32.3" in text
    assert "timeout went down from 900 s to 30 s" in text and "moved from python3.11 to python3.12" in text


def test_diff_packages_when_there_are_no_lines_to_compare():
    same = diff_packages(versioned("2", "abc"), None, versioned("3", "abc", memory=512), None)
    assert same.same_code and not same.files and same.settings[0].setting == "Memory"
    assert "only the settings below changed" in diff_findings(same)[0][1]
    nothing = diff_packages(versioned("2", "abc"), None, versioned("3", "abc"), None)
    assert "Nothing changed between version 2 and version 3" in diff_findings(nothing)[0][1]
    image = diff_packages(versioned("2", "a", package_type="Image", image_uri="repo@sha256:1"), None,
                          versioned("3", "b", package_type="Image", image_uri="repo@sha256:2"), None)
    assert "container image" in image.note and image.settings[-1].setting == "Image" and not diff_findings(image)
    assert "no link" in diff_packages(versioned("2", "a"), CODE, versioned("3", "b"), None).note
    with pytest.raises(ValueError, match="isn't a readable .zip"):
        diff_packages(versioned("2", "a"), b"not a zip", versioned("3", "b"), CODE)
    big = zipped({"app.py": "".join(f"line {i}\n" for i in range(5000))})
    cut = diff_packages(versioned("2", "a"), zipped({"app.py": ""}), versioned("3", "b"), big).files[0]
    assert cut.added == 5000 and cut.truncated and len(cut.lines) == 400




def test_diff_packages_names_the_aws_calls_new_code_makes():
    before = zipped({"app.py": 'import boto3\n\ns3 = boto3.client("s3")\n\n\ndef handler(event, context):\n'
                               '    return s3.get_object(Bucket="b", Key="k")\n'})
    after = zipped({"app.py": 'import boto3\n\ns3 = boto3.client("s3")\ntable = boto3.resource("dynamodb").Table("t")\n'
                              '\n\ndef handler(event, context):\n    table.put_item(Item=event)\n'
                              '    return s3.get_object(Bucket="b", Key="k")\n'})
    diff = diff_packages(versioned("1", "a"), before, versioned("$LATEST", "b", handler="app.handler"), after)
    assert [(c.label, c.permission, c.file, c.line) for c in diff.new_calls] == [
        ("DynamoDB PutItem", "dynamodb:PutItem", "app.py", 8)]
    finding = [message for _, message in diff_findings(diff) if "AWS call" in message][0]
    assert "$LATEST makes an AWS call the code didn't make before: DynamoDB PutItem at app.py:8" in finding
    assert "Its role needs dynamodb:PutItem" in finding and "errors('etl')" in finding
    node = diff_packages(versioned("1", "a", runtime="nodejs20.x"), before,
                         versioned("2", "b", runtime="nodejs20.x"), after)
    assert not node.new_calls and node.files
    page = stylemod._render_html(diff_block._diff_blocks(diff), 50)
    assert "AWS calls new in $LATEST" in page and "dynamodb:PutItem" in page

def test_version_before_skips_versions_with_the_same_code():
    versions = [Version("1", code_sha="a"), Version("2", code_sha="b"), Version("3", code_sha="b")]
    assert version_before(versions, versioned("$LATEST", "b")) == (versions[0], ["3", "2"])
    assert version_before(versions, versioned("$LATEST", "c")) == (versions[2], [])
    assert version_before(versions, versioned("3", "b")) == (versions[0], ["2"])
    assert version_before(versions, versioned("$LATEST", "a"))[0] is versions[2]
    assert version_before(versions[:1], versioned("$LATEST", "a")) == (versions[0], [])
    assert version_before(versions, versioned("1", "a")) == (None, [])
    assert version_before([], versioned("$LATEST", "a")) == (None, [])


def test_diff_renders_as_html_and_text():
    diff = diff_packages(versioned("2", "old"), OLD_CODE, versioned("$LATEST", "new"), NEW_CODE)
    page = stylemod._render_html([diff_block._Diff(diff.files[0], open=True)], 50)
    assert '<details class="dif" open>' in page and '<span class="dfa">+    return event[&quot;customer_id&quot;]' in page
    assert '<span class="dfh">@@ -8,4 +8,4 @@</span>' in page and "in load" in page
    out = _render_text([diff_block._Diff(diff.files[0])], 50)
    assert "-- app.py (changed, +1 −1, in load) --" in out and '+    return event["customer_id"]' in out


# ---- AWS (moto)


def checked(client, handlers):
    """
    Handlers for the Lambda reads moto doesn't have, with each request and answer checked against the service
    model like a real client's.
    """
    model = client.meta.service_model
    operations = {xform_name(op): model.operation_model(op) for op in model.operation_names}

    def wrap(name, handler):
        def call(**params):
            report_ = ParamValidator().validate(params, operations[name].input_shape)
            assert not report_.has_errors(), report_.generate_report()
            answer = handler(**params)
            report_ = ParamValidator().validate(answer, operations[name].output_shape)
            assert not report_.has_errors(), report_.generate_report()
            return answer
        return call

    return {name: wrap(name, handler) for name, handler in handlers.items()}


class Patched:
    """
    A moto client, with some operations answered by functions instead.
    """

    def __init__(self, client, handlers):
        self._client, self._handlers = client, checked(client, handlers)

    def __getattr__(self, name):
        return self._handlers.get(name) or getattr(self._client, name)


def lambda_clients(provisioned=None, reserved=None, policies=None, runtime_updates="Auto", denied=(), **handlers):
    """
    A function that makes a patched Lambda client per region: moto has no GetAccountSettings,
    ListProvisionedConcurrencyConfigs or GetRuntimeManagementConfig, leaves Concurrency out of GetFunction and
    writes a function URL's auth type outside the policy's Condition.
    """
    provisioned, reserved, policies = provisioned or {}, reserved or {}, policies or {}

    def make(region):
        client = boto3.client("lambda", region_name=region or REGION)

        def get_function(FunctionName, **params):
            answer = client.get_function(FunctionName=FunctionName, **params)
            answer.pop("ResponseMetadata", None)
            if FunctionName in reserved:
                answer["Concurrency"] = {"ReservedConcurrentExecutions": reserved[FunctionName]}
            return answer

        def get_policy(FunctionName, **params):
            if FunctionName in policies:
                return {"Policy": json.dumps(policies[FunctionName]), "RevisionId": "1"}
            answer = client.get_policy(FunctionName=FunctionName, **params)
            answer.pop("ResponseMetadata", None)
            return answer

        def denied_call(name):
            def call(**params):
                raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "not authorized"}}, name)
            return call

        answers = {
            "get_function": get_function,
            "get_policy": get_policy,
            "get_account_settings": lambda: {
                "AccountLimit": {"TotalCodeSize": 75 * GB, "CodeSizeUnzipped": 250 * MB, "CodeSizeZipped": 50 * MB,
                                 "ConcurrentExecutions": 1000, "UnreservedConcurrentExecutions": 900},
                "AccountUsage": {"TotalCodeSize": 3 * GB, "FunctionCount": 3}},
            "list_provisioned_concurrency_configs": lambda FunctionName, **_: {"ProvisionedConcurrencyConfigs": [
                {"FunctionArn": f"arn:aws:lambda:{region}:{ACCOUNT}:function:{FunctionName}:{q}",
                 "RequestedProvisionedConcurrentExecutions": n, "AllocatedProvisionedConcurrentExecutions": n,
                 "AvailableProvisionedConcurrentExecutions": n, "Status": "READY"}
                for q, n in provisioned.get(FunctionName, {}).items()]},
            "get_runtime_management_config": lambda FunctionName, **_: {"UpdateRuntimeOn": runtime_updates},
            **handlers,
        }
        for name in denied:
            answers[name] = denied_call(name)
        return Patched(client, answers)

    return make


@pytest.fixture
def aws():
    with mock_aws():
        yield


@pytest.fixture
def role(aws):
    return boto3.client("iam").create_role(RoleName="etl-role", AssumeRolePolicyDocument="{}")["Role"]["Arn"]


def create(name, role, region=REGION, **kwargs):
    params = dict(FunctionName=name, Runtime="python3.12", Role=role, Handler="app.handler", Code={"ZipFile": CODE})
    return boto3.client("lambda", region_name=region).create_function(**{**params, **kwargs})


def put_logs(group, events, stream="2026/10/06/[$LATEST]abc", region=REGION):
    logs = boto3.client("logs", region_name=region)
    try:
        logs.create_log_group(logGroupName=group)
    except logs.exceptions.ResourceAlreadyExistsException:
        pass
    logs.create_log_stream(logGroupName=group, logStreamName=stream)
    logs.put_log_events(logGroupName=group, logStreamName=stream, logEvents=[
        {"timestamp": int(moment.timestamp() * 1000), "message": message} for moment, message in events])


@pytest.fixture
def seeded(role):
    """
    etl-nightly: python3.9, 1 GB, 900 s timeout, environment variables with a password, an SQS queue and an S3
    bucket that trigger it, CloudWatch numbers with errors and throttles, and a run in its logs. api-handler: anyone
    may invoke it. eu-fn: nodejs18.x in eu-west-1.
    """
    lam = boto3.client("lambda", region_name=REGION)
    create("etl-nightly", role, Runtime="python3.9", MemorySize=1024, Timeout=900, Description="Nightly orders load",
           Environment={"Variables": {"DB_PASSWORD": "hunter2", "MODE": "prod"}}, Tags={"team": "ml"})
    create("api-handler", role, Architectures=["arm64"])
    create("eu-fn", role, region=OTHER, Runtime="nodejs18.x", Handler="index.handler")
    lam.add_permission(FunctionName="etl-nightly", StatementId="s3", Action="lambda:InvokeFunction",
                       Principal="s3.amazonaws.com", SourceArn="arn:aws:s3:::acme-uploads", SourceAccount=ACCOUNT)
    lam.add_permission(FunctionName="api-handler", StatementId="open", Action="lambda:InvokeFunction", Principal="*")
    sqs = boto3.client("sqs", region_name=REGION)
    url = sqs.create_queue(QueueName="orders")["QueueUrl"]
    queue = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    lam.create_event_source_mapping(EventSourceArn=queue, FunctionName="etl-nightly", BatchSize=10)
    now = datetime.now(timezone.utc)
    dims = [{"Name": "FunctionName", "Value": "etl-nightly"}]
    boto3.client("cloudwatch", region_name=REGION).put_metric_data(Namespace="AWS/Lambda", MetricData=[
        {"MetricName": "Invocations", "Dimensions": dims, "Timestamp": now - timedelta(minutes=30), "Value": 1000.0},
        {"MetricName": "Errors", "Dimensions": dims, "Timestamp": now - timedelta(minutes=30), "Value": 40.0},
        {"MetricName": "Throttles", "Dimensions": dims, "Timestamp": now - timedelta(minutes=30), "Value": 3.0},
        {"MetricName": "Duration", "Dimensions": dims, "Timestamp": now - timedelta(minutes=30), "Value": 250_000.0},
    ])
    started = now - timedelta(minutes=10)
    put_logs("/aws/lambda/etl-nightly", [
        (started, f"START RequestId: {RID} Version: $LATEST"),
        (started + timedelta(milliseconds=5), "loading 1,204 orders"),
        (started + timedelta(milliseconds=10), "[ERROR] KeyError: 'customer_id'\nTraceback (most recent call last):"),
        (started + timedelta(milliseconds=20), f"END RequestId: {RID}"),
        (started + timedelta(milliseconds=21), f"REPORT RequestId: {RID}\tDuration: 102.12 ms\tBilled Duration: 103 ms"
                                               "\tMemory Size: 1024 MB\tMax Memory Used: 88 MB\tInit Duration: 300.11 ms"),
    ])
    return {"queue": queue}


@pytest.fixture
def core(seeded):
    core = LambdaAnalyzer(region=REGION, clients={"lambda": lambda_clients()})
    core._download = lambda url, most: CODE
    return core


def test_list_functions_and_locate(core):
    names = sorted(f.name for f in core.list_functions())
    assert names == ["api-handler", "etl-nightly"] and [f.name for f in core.list_functions(OTHER)] == ["eu-fn"]
    assert [f.name for f in core.list_functions(match="etl-*")] == ["etl-nightly"]
    assert core.locate("etl-nightly") == ("etl-nightly", None, REGION)
    assert core.locate("etl-nightly", OTHER)[2] == OTHER
    assert core.locate(f"arn:aws:lambda:{OTHER}:{ACCOUNT}:function:eu-fn")[2] == OTHER
    core.overview(regions=[REGION, OTHER], metrics=False, details=False)
    assert core.locate("eu-fn") == ("eu-fn", None, OTHER)  # overview() saw it there


def test_overview_reads_settings_triggers_and_cloudwatch(core):
    ov = core.overview()
    by_name = {f.name: f for f in ov.functions}
    etl, api = by_name["etl-nightly"], by_name["api-handler"]
    assert ov.regions == [REGION] and set(by_name) == {"api-handler", "etl-nightly"}
    assert {t.kind for t in ov.triggers[etl.arn]} == {"SQS queue", "S3 bucket"}
    assert [t.kind for t in ov.triggers[api.arn]] == ["Anyone"] and ov.triggers[api.arn][0].public
    m = ov.metrics[etl.arn]
    assert (m.invocations, m.errors, m.throttles, m.duration_sum) == (1000, 40, 3, 250_000)
    assert m.avg_duration == 250 and m.last_invoked is not None
    assert ov.metrics[api.arn].invocations == 0
    assert ov.accounts[REGION].concurrency == 1000 and ov.accounts[REGION].code_storage == 3 * GB
    assert ov.log_groups[etl.arn].name == "/aws/lambda/etl-nightly" and not ov.errors
    assert ov.metrics_read == 2 * 7 + 1  # 6 Lambda metrics and the log volume per function, and the region's peak
    df = ov.to_df()
    assert set(df["function"]) == {"etl-nightly", "api-handler"}
    assert set(df.columns) >= {"region", "runtime_status", "invocations", "est_monthly_usd", "triggers"}


def test_overview_across_regions(core):
    ov = core.overview(regions=f"{REGION}, {OTHER}")
    assert ov.regions == [REGION, OTHER]
    assert sorted(f.region for f in ov.functions) == [OTHER, REGION, REGION]
    assert OTHER in ov.accounts


def test_overview_skips_regions_not_turned_on(seeded):
    def make(region):
        if region == "ap-east-1":
            client = boto3.client("lambda", region_name=region)
            return Patched(client, {"list_functions": lambda **_: (_ for _ in ()).throw(ClientError(
                {"Error": {"Code": "UnrecognizedClientException", "Message": "invalid token"}}, "ListFunctions"))})
        return lambda_clients()(region)

    ov = LambdaAnalyzer(region=REGION, clients={"lambda": make}).overview(regions=[REGION, "ap-east-1"])
    assert ov.skipped == {"ap-east-1": "UnrecognizedClientException"} and len(ov.functions) == 2


def test_regions(core, monkeypatch):
    class EC2:
        meta = type("Meta", (), {"region_name": REGION})()

        def __init__(self, fail=False):
            self.fail = fail

        def describe_regions(self):
            if self.fail:
                raise ClientError({"Error": {"Code": "UnauthorizedOperation", "Message": "no"}}, "DescribeRegions")
            return {"Regions": [{"RegionName": "us-east-1", "OptInStatus": "opt-in-not-required"},
                                {"RegionName": "af-south-1", "OptInStatus": "not-opted-in"},
                                {"RegionName": "eu-west-1", "OptInStatus": "opt-in-not-required"}]}

    core._given["ec2"] = EC2()
    assert core.regions("all") == ["eu-west-1", "us-east-1"]
    core._given["ec2"] = EC2(fail=True)
    core._clients.pop(("ec2", None))
    every = core.regions("ALL")
    assert "us-east-1" in every and len(every) > 10
    assert core.regions(None) == [REGION] and core.regions(["eu-west-1", "eu-west-1"]) == ["eu-west-1"]
    with pytest.raises(ValueError, match="isn't a region name"):
        core.regions("europe")


def test_describe_reads_every_section(seeded, role):
    lam = boto3.client("lambda", region_name=REGION)
    version = lam.publish_version(FunctionName="etl-nightly")["Version"]
    lam.create_alias(FunctionName="etl-nightly", Name="live", FunctionVersion=version)
    lam.create_function_url_config(FunctionName="etl-nightly", AuthType="NONE")
    lam.put_function_event_invoke_config(FunctionName="etl-nightly", MaximumRetryAttempts=1)
    public_url = {"Statement": [
        {"Sid": "s3", "Effect": "Allow", "Principal": {"Service": "s3.amazonaws.com"}, "Action": "lambda:InvokeFunction",
         "Condition": {"ArnLike": {"AWS:SourceArn": "arn:aws:s3:::acme-uploads"}}},
        {"Sid": "url", "Effect": "Allow", "Principal": "*", "Action": "lambda:InvokeFunctionUrl",
         "Condition": {"StringEquals": {"lambda:FunctionUrlAuthType": "NONE"}}}]}
    core = LambdaAnalyzer(region=REGION, clients={"lambda": lambda_clients(
        provisioned={"etl-nightly": {"live": 5}}, reserved={"etl-nightly": 20}, policies={"etl-nightly": public_url},
        runtime_updates="Manual")})
    ticks = []
    detail = core.describe("etl-nightly", progress=lambda done, total: ticks.append((done, total)))
    fn_ = detail.function
    assert (fn_.name, fn_.runtime, fn_.reserved_concurrency, fn_.tags) == ("etl-nightly", "python3.9", 20, {"team": "ml"})
    kinds = {t.kind: t for t in detail.triggers}
    assert set(kinds) == {"SQS queue", "S3 bucket", "Function URL"}
    assert kinds["Function URL"].public and kinds["Function URL"].source.startswith("https://")
    assert detail.async_config.retries == 1 and detail.async_config.on_failure is None
    assert [v.version for v in detail.versions] == [version] and detail.aliases[0].name == "live"
    assert detail.provisioned[0].qualifier == "live" and detail.provisioned[0].billed == 5
    assert detail.runtime_updates == "Manual" and detail.log_group.name == "/aws/lambda/etl-nightly"
    assert detail.metrics.invocations == 1000 and detail.metrics.concurrency_max is None
    assert not detail.errors and ticks[-1][0] == ticks[-1][1]


def test_describe_drops_url_statements_without_a_url(seeded):
    stale = {"Statement": [{"Sid": "url", "Effect": "Allow", "Principal": "*", "Action": "lambda:InvokeFunctionUrl",
                            "Condition": {"StringEquals": {"lambda:FunctionUrlAuthType": "NONE"}}}]}
    core = LambdaAnalyzer(region=REGION, clients={"lambda": lambda_clients(policies={"api-handler": stale})})
    assert [t.kind for t in core.describe("api-handler", metrics=False).triggers] == []
    boto3.client("lambda", region_name=REGION).create_function_url_config(FunctionName="api-handler",
                                                                          AuthType="NONE")
    url = core.describe("api-handler", metrics=False).triggers[0]
    assert url.kind == "Function URL" and url.public
    closed = LambdaAnalyzer(region=REGION, clients={"lambda": lambda_clients()})
    refused = closed.describe("api-handler", metrics=False).triggers
    assert [t.kind for t in refused] == ["Anyone", "Function URL"]
    assert not refused[1].public and "calls are refused" in refused[1].detail


def test_describe_records_sections_it_cannot_read(seeded):
    core = LambdaAnalyzer(region=REGION, clients={"lambda": lambda_clients(denied=("get_policy", "list_aliases"))})
    detail = core.describe("etl-nightly", metrics=False)
    assert detail.errors == {"policy": "AccessDeniedException", "aliases": "AccessDeniedException"}
    assert [t.kind for t in detail.triggers] == ["SQS queue"] and detail.metrics is None


def test_describe_a_missing_function_raises(core):
    with pytest.raises(ClientError, match="Function not found"):
        core.describe("nope")


def test_log_events_read_the_newest_lines(core):
    page = core.log_events("etl-nightly", since="1h")
    assert [e.message.split(" ")[0] for e in page.events] == ["START", "loading", "[ERROR]", "END", "REPORT"]
    assert not page.truncated and page.events[0].request_id == RID
    keyed = core.log_events("etl-nightly", since="1h", pattern='"KeyError"')
    assert [e.message[:7] for e in keyed.events] == ["[ERROR]"]
    one = core.log_events("etl-nightly", request_id=RID)
    assert len(one.events) == 5 and one.events[1].message == "loading 1,204 orders"  # printed lines carry no ID
    nothing = core.log_events("etl-nightly", since=datetime.now(timezone.utc) - timedelta(minutes=2))
    assert nothing.events == [] and nothing.latest is not None
    missing = core.log_events("api-handler")
    assert missing.errors == {"logs": "ResourceNotFoundException"}


def test_filter_keeps_the_newest_and_says_where_it_stopped(core):
    now = datetime.now(timezone.utc)
    put_logs("/aws/lambda/api-handler", [(now - timedelta(minutes=50 - i), f"line {i}") for i in range(30)])
    events, truncated, covered = core._filter("/aws/lambda/api-handler", None, now - timedelta(hours=1), now,
                                              None, 10)
    assert [e.message for e in events] == [f"line {i}" for i in range(20, 30)]
    assert truncated and covered == events[0].time
    every, truncated, _ = core._filter("/aws/lambda/api-handler", None, now - timedelta(hours=1), now, None, 100)
    assert len(every) == 30 and not truncated


def test_log_slices_cover_the_range_newest_first():
    minute = 60_000
    low, high = 0, 7 * 24 * 60 * minute - 1
    for readers in (1, analyzer_mod._LOG_READERS):
        slices = analyzer_mod._log_slices(low, high, readers)
        assert slices[0] == (high - minute + 1, high) and slices[-1][0] == low
        assert all(newer[0] == older[1] + 1 for newer, older in zip(slices, slices[1:]))
    side_by_side = analyzer_mod._log_slices(low, high, 4)
    assert max(last - first + 1 for first, last in side_by_side) == (high + 1) // 4
    assert analyzer_mod._log_slices(low, minute // 2, 4) == [(low, minute // 2)]


def test_a_search_reads_its_slices_side_by_side_and_keeps_the_newest(seeded):
    now = datetime.now(timezone.utc)
    put_logs("/aws/lambda/api-handler", [(now - timedelta(hours=150 - 5 * i), f"KeyError {i}") for i in range(30)])
    together = threading.Barrier(analyzer_mod._LOG_READERS, timeout=10)
    calls = []

    def logs_client(region):
        client = boto3.client("logs", region_name=region or REGION)

        def filter_log_events(**params):
            calls.append(params)
            if len(calls) <= together.parties:
                together.wait()
            answer = client.filter_log_events(**params)
            answer.pop("ResponseMetadata", None)
            return answer

        return Patched(client, {"filter_log_events": filter_log_events})

    core = LambdaAnalyzer(region=REGION, clients={"lambda": lambda_clients(), "logs": logs_client})
    args = ("/aws/lambda/api-handler", None, now - timedelta(days=7), now, '"KeyError"')
    events, truncated, covered = core._filter(*args, 12)
    assert not together.broken
    assert [e.message for e in events] == [f"KeyError {i}" for i in range(18, 30)]
    assert truncated and covered == events[0].time
    core.max_workers = 1
    assert core._filter(*args, 12) == (events, truncated, covered)
    assert len(core._filter(*args, None)[0]) == 30


def test_errors_and_performance(core):
    report_ = core.errors("etl-nightly")
    assert report_.errors_found == 1 and [g.kind for g in report_.groups] == ["KeyError"]
    assert report_.metrics.errors == 40 and report_.metrics.period == 3600
    perf = core.performance("etl-nightly")
    assert len(perf.invocations) == 1 and perf.invocations[0].max_memory == 88
    assert perf.cold_starts and perf.window_days == pytest.approx(1, abs=0.01)
    assert core.performance("api-handler").errors == {"logs": "ResourceNotFoundException"}
    with pytest.raises(ValueError, match="since= and until= take a time like"):
        core.errors("etl-nightly", since="last tuesday")
    with pytest.raises(ValueError, match="must be before until="):
        core.errors("etl-nightly", since="2h", until="3h")


def test_code_reads_the_package(core):
    package = core.code("etl-nightly")
    assert sorted(f.path for f in package.files) == [".env", "app.py", "utils.py"]
    assert package.handler_file == package.shown_file == "app.py" and "customer_id" in package.source
    assert core.code("etl-nightly", file="utils.py").source.startswith("def helper")
    assert core.code("etl-nightly", file="*.py").shown_file == "app.py"
    secret = core.code("etl-nightly", file=".env")
    assert secret.shown_file == ".env" and secret.source is None and "secrets file" in secret.note
    with pytest.raises(ValueError, match="Did you mean 'utils.py'"):
        core.code("etl-nightly", file="util.py")
    core._download = lambda url, most: b"not a zip"
    core._packages.clear()
    with pytest.raises(ValueError, match="isn't a readable .zip"):
        core.code("etl-nightly")


def test_code_downloads_a_package_once_and_new_code_again(core):
    downloads = []
    core._download = lambda url, most: downloads.append(url) or CODE
    core.code("etl-nightly")
    core.code("etl-nightly", file="utils.py")
    core.code_map("etl-nightly")
    assert len(downloads) == 1
    core._packages.clear()
    core._download = lambda url, most: downloads.append(url) or zipped({"app.py": "not what Lambda reports"})
    core.code("etl-nightly")
    core.code("etl-nightly")
    assert len(downloads) == 3


def test_source_files_are_its_own_text_files_the_handlers_first():
    package = read_package(fn(handler="app.handler"), SOURCE)
    assert source_files(package) == ["app.py", "broken.py", "long.py", "store.py"]


def test_search_package_finds_lines_like_grep():
    found = search_package(fn(), SOURCE, "load_orders")
    assert [(m.file, m.line) for m in found.matches] == [("app.py", 2), ("app.py", 8), ("store.py", 12)]
    assert found.total == 3 and found.files_matched == 2 and found.files_searched == 4 and not found.truncated
    assert found.matches[1].context == [(6, ""), (7, "def handler(event, context):"),
                                        (8, "    orders = load_orders(event)"), (9, "    return Orders(TABLE).save(orders)")]
    libraries = search_package(fn(), SOURCE, "load_orders", files="requests/")
    assert [(m.file, m.line) for m in libraries.matches] == [("requests/api.py", 1)]
    everything = search_package(fn(), SOURCE, "hunter2", files="*")
    assert not everything.matches and "logo.png: a binary file" in everything.skipped
    regex = search_package(fn(), SOURCE, r'(?i)EVENT\["\w+"\]', regex=True)
    assert [m.text.strip() for m in regex.matches] == ['return event["customer_id"]']
    assert len(search_package(fn(), SOURCE, "x", files="long.py").matches) == 500
    with pytest.raises(ValueError, match="repeats a group that repeats itself"):
        search_package(fn(), SOURCE, "(a+)+$", regex=True)
    with pytest.raises(ValueError, match="isn't a regular expression"):
        search_package(fn(), SOURCE, "(", regex=True)
    assert "container image" in search_package(fn(package_type="Image", runtime=None), None, "x").note


def test_find_symbol_in_finds_definitions_and_uses():
    report = find_symbol_in(fn(), SOURCE, "load_orders")
    assert [(d.name, d.kind, d.file, d.line, d.end_line) for d in report.definitions] == [
        ("load_orders", "function", "store.py", 12, 14)]
    assert report.definitions[0].signature == "def load_orders(event)"
    assert report.definitions[0].doc == "The orders in an S3 event."
    assert [(u.file, u.line, u.kind, u.within) for u in report.uses] == [
        ("app.py", 2, "import", "(module)"), ("app.py", 8, "call", "handler")]
    assert report.files_read == 3 and report.skipped == ["broken.py: Python can't read it (SyntaxError at line 1)"]

    save = find_symbol_in(fn(), SOURCE, "Orders.save").definitions
    assert [(d.kind, d.signature, d.decorators) for d in save] == [
        ("method", "def save(orders, *, batch=25) -> int", ["@staticmethod"])]
    assert find_symbol_in(fn(), SOURCE, "store.Orders").definitions[0].doc == "Orders in DynamoDB."
    assert find_symbol_in(fn(), SOURCE, "TABLE").definitions[0].kind == "variable"
    assert not find_symbol_in(fn(), SOURCE, "Other.save").definitions
    assert "search the code" in find_symbol_in(fn(runtime="nodejs20.x"), SOURCE, "x").note
    with pytest.raises(ValueError, match="isn't a Python name"):
        find_symbol_in(fn(), SOURCE, "load orders")


def test_search_code_and_find_symbol_read_the_package(core):
    core._download = lambda url, most: SOURCE
    search = core.search_code("etl-nightly", "load_orders", limit=2)
    assert len(search.matches) == 2 and search.total == 3 and search.truncated
    assert core.find_symbol("etl-nightly", "load_orders").definitions[0].file == "store.py"



def versioned_clients(**settings):
    """
    lambda_clients whose GetFunction tags the code's link with the version, since moto gives every version the
    same one, so a test's _download can serve each version its own package.
    """
    def get_function(FunctionName, **params):
        answer = boto3.client("lambda", region_name=REGION).get_function(FunctionName=FunctionName, **params)
        answer.pop("ResponseMetadata", None)
        answer["Code"]["Location"] += "#" + answer["Configuration"]["Version"]
        return answer
    return lambda_clients(get_function=get_function, **settings)


@pytest.fixture
def released(role):
    """
    orders-etl, published as version 1 (OLD_CODE, 900 s, MODE set), version 2 (the same code, 60 s) and version 3
    (NEW_CODE), then $LATEST with MODE taken out; the alias prod is version 2. core serves each version its package.
    """
    lam = boto3.client("lambda", region_name=REGION)
    create("orders-etl", role, Code={"ZipFile": OLD_CODE}, Timeout=900,
           Environment={"Variables": {"MODE": "prod", "TABLE": "orders"}})
    lam.publish_version(FunctionName="orders-etl")
    lam.update_function_configuration(FunctionName="orders-etl", Timeout=60)
    lam.publish_version(FunctionName="orders-etl")
    lam.update_function_code(FunctionName="orders-etl", ZipFile=NEW_CODE)
    lam.publish_version(FunctionName="orders-etl")
    lam.update_function_configuration(FunctionName="orders-etl", Environment={"Variables": {"TABLE": "orders"}})
    lam.create_alias(FunctionName="orders-etl", Name="prod", FunctionVersion="2")
    create("never-published", role)
    core = LambdaAnalyzer(region=REGION, clients={"lambda": versioned_clients()})
    packages = {"1": OLD_CODE, "2": OLD_CODE, "3": NEW_CODE, "$LATEST": NEW_CODE}
    core._download = lambda url, most: packages[url.rsplit("#", 1)[1]] if "#" in url else CODE
    return core


def test_versions_and_diff_compare_published_versions(released):
    core = released
    assert [(v.version, bool(v.code_sha)) for v in core.versions("orders-etl")] == [("1", True), ("2", True),
                                                                                    ("3", True)]
    diff = core.diff("orders-etl")
    assert (diff.old.version, diff.function.version, diff.skipped) == ("2", "$LATEST", ["3"])
    assert diff.broken == {"store.py": "SyntaxError at line 1"} and diff.still_read == {"MODE": "app.py:2"}
    assert not diff.settings and diff.env_removed == ["MODE"]
    pair = core.diff("orders-etl", 1, "v2")
    assert pair.same_code and [(c.setting, c.old, c.new) for c in pair.settings] == [("Timeout", "900 s", "60 s")]
    alias = core.diff("orders-etl:prod")
    assert (alias.old.version, alias.function.version, alias.same_code) == ("1", "2", True)
    first = core.diff("orders-etl", new=1)
    assert first.old is None and "first version" in first.note
    nothing = core.diff("never-published")
    assert nothing.old is None and "no published versions" in nothing.note and "publish-version" in nothing.note


def test_download_checks_size_and_network(core, monkeypatch):
    class Answer:
        def __init__(self, body, length=None):
            self.body, self.headers = body, {"Content-Length": str(length or len(body))}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, size=-1):
            return self.body if size < 0 else self.body[:size]

    download = LambdaAnalyzer._download.__get__(core)
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout: Answer(b"x" * 100))
    assert download("https://s3.example/pkg.zip", 1000) == b"x" * 100
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout: Answer(b"x" * 10, length=80 * MB))
    with pytest.raises(ValueError, match="The package is 80.0 MB, more than max_size"):
        download("https://s3.example/pkg.zip", 50 * MB)

    def offline(url, timeout):
        raise OSError("Network is unreachable")

    monkeypatch.setattr(urllib.request, "urlopen", offline)
    with pytest.raises(ValueError, match="S3 gateway endpoint"):
        download("https://s3.example/pkg.zip", 50 * MB)
    with pytest.raises(ValueError, match="isn't an https"):
        download("file:///etc/passwd", 50 * MB)


def test_download_saves_the_package(core, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    saved = core.download("etl-nightly")
    assert saved.path == str(tmp_path / "lambda-downloads" / "etl-nightly.zip") and saved.size == len(CODE)
    assert (tmp_path / "lambda-downloads" / "etl-nightly.zip").read_bytes() == CODE
    assert saved.files == 3 and saved.sha256_matches is True and saved.marked and not saved.replaced
    assert (tmp_path / "lambda-downloads" / ".gitignore").read_text().splitlines()[-1] == "*"
    again = core.download("etl-nightly")
    assert again.replaced and not again.marked
    assert sorted(os.listdir(tmp_path / "lambda-downloads")) == [".gitignore", "etl-nightly.zip"]  # no .part left
    (tmp_path / "mine").mkdir()
    assert core.download("etl-nightly", "mine").path == str(tmp_path / "mine" / "etl-nightly.zip")
    assert core.download("etl-nightly", "new/pkg.zip").path == str(tmp_path / "new" / "pkg.zip")
    assert not (tmp_path / "new" / ".gitignore").exists()  # only a downloads folder it makes gets one
    core.downloads = "elsewhere"
    assert core.download("etl-nightly").path == str(tmp_path / "elsewhere" / "etl-nightly.zip")
    core._download = lambda url, most: zipped({"app.py": "changed"})
    core._packages.clear()
    changed = core.download("etl-nightly")
    assert changed.sha256_matches is False and changed.files == 1
    (tmp_path / "blocked").write_text("a file, not a folder")
    with pytest.raises(ValueError, match="Couldn't save etl-nightly.zip in .*blocked"):
        core.download("etl-nightly", "blocked/pkg.zip")


def test_code_of_a_container_image(core, monkeypatch):
    image = fn(package_type="Image", runtime=None, image_uri=f"{ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/etl:1")
    monkeypatch.setattr(core, "function", lambda ref, region=None: image)
    package = core.code("etl")
    assert package.files == [] and "container image" in package.note and "etl:1" in package.note
    with pytest.raises(_Hint, match="etl is a container image .*etl:1.*docker pull"):
        core.download("etl")


HOW = zipped({  # etl-nightly's code for how_it_works(): an S3 event, a variable that isn't set, a client per call
    "app.py": ("import os\nimport boto3\n\nfrom store import save\n\ns3 = boto3.client('s3')\n\n\n"
               "def handler(event, context):\n    for record in event['Records']:\n"
               "        body = s3.get_object(Bucket=os.environ['BUCKET'], Key=record['s3']['object']['key'])['Body'].read()\n"
               "        save(body)\n    return {'saved': len(event['Records'])}\n"),
    "store.py": ("import boto3\n\n\ndef save(body):\n    \"\"\"Saves one order.\"\"\"\n"
                 "    boto3.client('dynamodb').put_item(TableName='orders', Item={'body': {'B': body}})\n"),
    ".env": "DB_PASSWORD=hunter2\n",
})


def test_code_map_reads_the_package(core):
    cmap = core.code_map("etl-nightly")
    assert cmap.entry == "app.handler" and cmap.files == 1 and cmap.other_files == 1  # utils.py isn't imported
    assert [f.label for f in cmap.event] == ["customer_id"] and cmap.returns == [("event['customer_id']", "app.py", 2)]
    core._download = lambda url, most: pytest.fail("a function that isn't Python isn't downloaded")
    assert "It runs nodejs18.x: how_it_works() reads Python code" in core.code_map("eu-fn", region=OTHER).note


def seed_runs(now, group="/aws/lambda/etl-nightly", stream="2026/10/06/[$LATEST]def", count=4):
    """
    count more runs of etl-nightly, a minute apart in a stream of their own; the second one logs an order ID.
    """
    events, ids = [], []
    for i in range(count):
        rid = f"{i:08x}-1111-4222-8333-444455556666"
        start = now - timedelta(minutes=count - i)
        ids.append(rid)
        events += [(start, f"START RequestId: {rid} Version: $LATEST"),
                   (start + timedelta(milliseconds=5), f"handling order {'ORD-77' if i == 1 else 'ORD-1'}"),
                   (start + timedelta(milliseconds=9), f"END RequestId: {rid}"),
                   (start + timedelta(milliseconds=10), report_line(rid, 50.0 + i))]
    put_logs(group, events, stream=stream)
    return ids


def test_log_runs_and_searches_that_bring_back_whole_runs(core):
    now = datetime.now(timezone.utc)
    ids = seed_runs(now)
    page = core.log_runs("etl-nightly", since="1h")
    assert [run.request_id for run in page.runs] == [*reversed(ids), RID]  # newest first
    assert page.runs[-1].status == "failed" and page.runs[0].status == "ok" and not page.truncated
    found = core.log_runs("etl-nightly", since="1h", search="ORD-77")
    assert [run.request_id for run in found.runs] == [ids[1]] and found.pattern == '"ORD-77"'
    run = found.runs[0]
    assert len(run.events) == 4 and [e.matched for e in run.events] == [False, True, False, False]
    assert run.report.duration == 51.0  # the whole run came back, not just the line that matched
    by_id = core.log_runs("etl-nightly", since="1h", search=RID).runs
    assert [r.request_id for r in by_id] == [RID] and len(by_id[0].events) == 5
    assert core.log_runs("etl-nightly", since="1h", search="nothing like this").runs == []
    assert core.log_runs("api-handler").errors == {"logs": "ResourceNotFoundException"}


def test_run_around_reads_one_whole_run(core):
    now = datetime.now(timezone.utc)
    ids = seed_runs(now)
    etl = core.function("etl-nightly")
    found = core._run_around(etl, now - timedelta(minutes=2), stream="2026/10/06/[$LATEST]def", request_id=ids[2])
    assert found.request_id == ids[2] and found.status == "ok" and len(found.events) == 4
    near = core._run_around(etl, now - timedelta(minutes=3) + timedelta(milliseconds=5),
                            stream="2026/10/06/[$LATEST]def")
    assert near.request_id == ids[1]  # without the ID: the run that logged a line at that moment
    without_stream = core._run_around(etl, now - timedelta(minutes=10), request_id=RID)
    assert without_stream.request_id == RID and without_stream.status == "failed"
    assert core._run_around(etl, now) is None


def test_overview_in_parts_adds_up_to_overview(core):
    whole = core.overview()
    ov = core.overview(metrics=False, details=False)
    assert not ov.metrics and not ov.details and ov.metrics_read == 0
    until = datetime.now(timezone.utc)
    since = helpers._midnight(until) - timedelta(days=29)
    ov.add_numbers(*core.overview_numbers(ov.functions, since=since, until=until, limits=set(ov.accounts)))
    ov.add_extras(core._all_extras(ov.functions))
    assert ov.details and ov.metrics_read == whole.metrics_read
    assert {arn: m.invocations for arn, m in ov.metrics.items()} == {
        arn: m.invocations for arn, m in whole.metrics.items()}
    assert {arn: sorted(t.kind for t in ts) for arn, ts in ov.triggers.items()} == {
        arn: sorted(t.kind for t in ts) for arn, ts in whole.triggers.items()}
    assert ov.accounts[REGION].peak_concurrency == whole.accounts[REGION].peak_concurrency


def test_missing_region_is_a_readable_error(monkeypatch):
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", "/nonexistent")
    with pytest.raises(ValueError, match="No AWS region is set"):
        LambdaAnalyzer().client


# ---- UI


@pytest.fixture
def ui(core):
    return LambdaView(core, mode="text")


def test_functions_report(ui, capsys):
    out = run(capsys, ui.functions)
    assert "Lambda functions in us-east-1" in out and "Functions: 2" in out
    assert "Calls (30d): 1,000" in out and "Error rate: 4.0% (!)" in out and "Unsupported runtimes: 1 (!)" in out
    assert "Code storage: 3.0 GB of 75.0 GB" in out
    assert "python3.9 (ended)" in out and "Anyone (public)" in out and "SQS, S3" in out
    assert "Its resource policy lets anyone invoke it (statement open" in out
    assert "40 of 1,000 calls failed (4.0%)" in out
    assert "This report read 15 CloudWatch metrics" in out
    assert "function_info('etl-nightly')" in out and "errors('etl-nightly')" in out
    assert "functions(regions='all')" in out


def test_functions_report_across_regions(ui, capsys):
    out = run(capsys, ui.functions, regions=[REGION, OTHER])
    assert "Lambda functions in 2 regions" in out and "Regions with functions: 2 of 2" in out
    assert "-- By region --" in out and "eu-fn (eu-west-1)" in out and "nodejs18.x (ended)" in out
    assert "functions(regions='all')" not in out
    assert "function_info('eu-fn', region='eu-west-1')" in run(capsys, ui.functions, match="eu-*", regions=[OTHER])


def test_overview_says_what_it_did_not_read(core):
    ov = core.overview(details=False)
    detail = ov.detail(next(f for f in ov.functions if f.name == "api-handler"))
    assert detail.errors == {"policy": "not read", "provisioned": "not read"} and detail.triggers == []
    assert not any("nothing triggers it" in message for _, message in function_findings(
        detail.function, detail.metrics, detail))


def test_functions_report_without_metrics_or_details(ui, capsys):
    out = run(capsys, ui.functions, metrics=False, details=False)
    assert "Calls (30d)" not in out and "details=False: resource policies" in out and "Anyone" not in out
    assert "No Lambda functions matching 'zzz*' in us-east-1" in run(capsys, ui.functions, "zzz*")


def test_function_info_report(ui, capsys):
    out = run(capsys, ui.function_info, "etl-nightly")
    assert "Function etl-nightly" in out and "Nightly orders load" in out
    assert "Runtime: python3.9 (ended) (!)" in out and "Calls (30d): 1,000" in out and "Triggers: 2" in out
    assert "python3.9 reached end of support on 2025-12-15" in out
    assert "Environment variable DB_PASSWORD looks like a secret" in out
    assert "Events from S3 bucket acme-uploads that still fail after 2 retries are dropped" in out
    assert "1,024 MB (about 0.58 vCPU)" in out and "900 seconds" in out and "Lambda calls handler() in app.py" in out
    assert "SQS queue  orders" in out and "Lambda reads it" in out and "allowed to invoke it" in out
    assert "DB_PASSWORD, MODE" in out and "hunter2" not in out and '"DB_PASSWORD": "(hidden)"' in out
    assert "-- The last 30 days (CloudWatch" in out and "-- Estimated monthly cost:" in out
    assert "errors('etl-nightly')" in out and "performance('etl-nightly')" in out and "code('etl-nightly')" in out


def test_function_info_of_a_missing_function_suggests_names(ui, capsys):
    out = run(capsys, ui.function_info, "etl-nightlyy")
    assert out.startswith("[i] No function 'etl-nightlyy' in us-east-1. Did you mean 'etl-nightly'?")
    assert "has no alias or version 'beta'" in run(capsys, ui.function_info, "etl-nightly:beta")
    assert "isn't a Lambda function name" in run(capsys, ui.function_info, "a b")
    assert "days= must be between 1 and 455" in run(capsys, ui.function_info, "etl-nightly", days=0)


def test_errors_report(ui, capsys):
    out = run(capsys, ui.errors, "etl-nightly")
    assert "Errors in etl-nightly" in out and "Failed calls (CloudWatch): 40" in out
    assert "Throttled calls: 3 (!)" in out and "Causes: 1" in out
    assert "The most common error happened once (the last" in out and "): KeyError: 'customer_id'." in out
    assert "-- Errors by cause" in out and "-- The newest error lines --" in out
    assert "function_info('etl-nightly')" in out
    none = run(capsys, ui.errors, "api-handler")
    assert "api-handler has no log group yet (/aws/lambda/api-handler)" in none


def test_error_causes_share_all_error_lines(ui, capsys, monkeypatch):
    read = ui.core.errors

    def errors(*args, **kwargs):
        found = read(*args, **kwargs)
        timeout = ErrorGroup("Timeout", "Task timed out after 60.00 seconds", 3, found.groups[0].first,
                             found.groups[0].last, "Task timed out after 60.00 seconds")
        found.groups.insert(0, timeout)
        return found

    monkeypatch.setattr(ui.core, "errors", errors)
    out = run(capsys, ui.errors, "etl-nightly")
    assert "75.0%" in out and "25.0%" in out and "100.0%" not in out


def test_logs_report(ui, capsys):
    out = run(capsys, ui.logs, "etl-nightly")
    assert "Logs of etl-nightly" in out and "START and END lines left out" in out
    assert "loading 1,204 orders" in out and "START RequestId" not in out
    assert "REPORT: run 102 ms (billed 103 ms), 88 of 1,024 MB used, cold start 300 ms" in out
    assert "Runs (REPORT lines): 1" in out and "Error lines: 1" in out and "KeyError" in out
    assert "errors('etl-nightly')" in out and "logs('etl-nightly', since='24h')" in out
    found = run(capsys, ui.logs, "etl-nightly", search="orders")
    assert "lines containing 'orders'" in found and "Lines shown: 1" in found
    whole = run(capsys, ui.logs, "etl-nightly", request_id=RID)
    assert f"Run {RID} of etl-nightly" in whole and "START RequestId" in whole and "Lines shown: 5" in whole
    quiet = run(capsys, ui.logs, "etl-nightly", since="2m")
    assert "No lines in the last 2 minutes. The newest line in its log group is from" in quiet
    assert "n= is how many lines" in run(capsys, ui.logs, "etl-nightly", n=0)


def test_performance_report(ui, capsys):
    out = run(capsys, ui.performance, "etl-nightly")
    assert "Performance of etl-nightly" in out and "Runs: 1" in out and "Memory used: 88 of 1,024 MB" in out
    assert "Cold starts: 100.0% · 300 ms" in out and "-- The slowest runs --" in out
    assert "256 MB still leaves room" in out
    assert f"logs('etl-nightly', request_id='{RID}')" in out
    assert "No REPORT lines" not in out
    assert "has no log group yet" in run(capsys, ui.performance, "api-handler")


def test_reports_show_times_in_the_zone_picked(ui, capsys):
    utc = run(capsys, ui.logs, "etl-nightly")
    assert "Time (UTC)" in utc
    first = datetime.strptime(re.search(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", utc).group(0), "%Y-%m-%d %H:%M:%S")
    assert set_timezone("Asia/Kolkata") == "IST"
    out = run(capsys, ui.logs, "etl-nightly")
    shown = first.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Asia/Kolkata"))
    assert "Time (IST)" in out and f"{shown:%Y-%m-%d %H:%M:%S}" in out and "UTC" not in out
    errors = run(capsys, ui.errors, "etl-nightly")
    assert re.search(r"the last 24 hours \(since \d{4}-\d\d-\d\d \d\d:\d\d IST\)", errors)
    assert "Time (IST)" in errors
    assert "Time (IST)" in run(capsys, ui.performance, "etl-nightly")
    info = run(capsys, ui.function_info, "etl-nightly")
    assert re.search(r"Last change +\d{4}-\d\d-\d\d \d\d:\d\d IST \(", info)
    assert "Day (UTC)" in info  # CloudWatch adds up its daily numbers by UTC day, so they stay in UTC


def test_code_report(ui, capsys):
    out = run(capsys, ui.code, "etl-nightly")
    assert "Code of etl-nightly" in out and "Handler file: app.py" in out and "Files: 3" in out
    assert "The package holds .env" in out and "return event['customer_id']" in out
    assert "code('etl-nightly', file='utils.py')" in out
    assert "No file 'nope.py' in the package" in run(capsys, ui.code, "etl-nightly", "nope.py")
    secret = run(capsys, ui.code, "etl-nightly", ".env")
    assert ".env looks like a secrets file, so its text isn't shown" in secret and "hunter2" not in secret
    assert "aws lambda get-function --function-name etl-nightly --region us-east-1 --query Code.Location" in secret


def test_diff_report(released, capsys):
    ui = LambdaView(released, mode="text", progress="off")
    out = run(capsys, ui.diff, "orders-etl")
    assert "What changed in orders-etl" in out and "skipped 3: the same code" in out
    assert "Compared: version 2 → $LATEST" in out and "Files changed: 7 of 7 (!)" in out
    assert "store.py has a SyntaxError at line 1 in $LATEST" in out and "app.py:2 still reads it" in out
    assert "-- app.py (changed, +1 −1, in load) --" in out and '+    return event["customer_id"]' in out
    assert "requests  2.31.0     2.32.3" in out and "Environment variable MODE  set        removed" in out
    assert "code('orders-etl', 'app.py')" in out and "errors('orders-etl')" in out
    assert "diff('orders-etl', 1, 2)" in out
    same = run(capsys, ui.diff, "orders-etl", 1, 2)
    assert "The code is the same in version 1 and version 2" in same and "Timeout      900 s       60 s" in same
    none = run(capsys, ui.diff, "never-published")
    assert "Nothing to compare $LATEST with" in none and "aws lambda publish-version" in none
    assert "isn't a version or an alias" in run(capsys, ui.diff, "orders-etl", "no way")
    assert "No function 'orders-etc'" in run(capsys, ui.diff, "orders-etc") and "Traceback" not in out


def test_how_it_works_report(ui, capsys):
    ui.core._download = lambda url, most: HOW
    out = run(capsys, ui.how_it_works, "etl-nightly")
    assert "How etl-nightly works" in out and "read from 2 Python files (19 lines) with Python's ast, nothing run" in out
    assert "Starts at: handler() in app.py" in out and "Expects: an S3 event" in out
    assert "AWS calls: 2 · S3, DynamoDB" in out and "Environment: 1 variable read · 1 not set (!)" in out
    assert ("When Lambda starts it (once per cold start), the code makes an S3 client. Each call runs handler() in "
            "app.py. It reads Records[].s3.object.key from the event, which looks like an S3 event.") in out
    assert "[!] BUCKET isn't set on etl-nightly, and its code reads it with os.environ[...] (app.py line 11)" in out
    assert "[i] It makes a new DynamoDB client on every call, at store.py line 6 in save()" in out
    assert "-- When Lambda starts it (once per cold start, before the first call) --" in out
    assert "    6  s3 = boto3.client('s3')   # makes an S3 client" in out
    assert "-- What each call does, step by step from handler() --" in out
    assert "    9  def handler(event, context):   # app.py" in out
    assert "   10    for record in event['Records']:   # event Records" in out
    assert "   12      save(body)" in out and "    4          ↳ def save(body):   # store.py" in out
    assert ("    6              boto3.client('dynamodb').put_item(TableName='orders', Item={'body': {'B': body}})   # "
            "makes a DynamoDB client · DynamoDB PutItem (writes)") in out
    assert "The AWS calls it makes" not in out and "A test event" not in out and "hunter2" not in out
    assert out.index("How it fits together") < out.index("step by step from handler()") < out.index("Starts at:")
    assert out.index("Starts at:") < out.index("BUCKET isn't set on etl-nightly")
    assert "code('etl-nightly', file='app.py')" in out and "code('etl-nightly', file='store.py')" in out
    assert "-- How it fits together: what comes in, who calls whom, and what it reaches --" in out
    assert "  handler()  app.py line 9\n      calls save() (line 12)\n      reads from S3: GetObject, in a loop" in out
    assert "flowchart LR" in out and '  f1 -->|"PutItem"| aws_dynamodb' in out
    note = run(capsys, ui.how_it_works, "eu-fn", region=OTHER)
    assert "It runs nodejs18.x: how_it_works() reads Python code" in note and "code('eu-fn', region='eu-west-1')" in note
    assert "No function 'etl-nightlx' in us-east-1. Did you mean 'etl-nightly'?" in run(capsys, ui.how_it_works,
                                                                                       "etl-nightlx")
    page = stylemod._render_html(ui._how_blocks(ui.core.code_map("etl-nightly")), 50)
    assert page.index('<div class="tabs">') < page.index("Starts at") and '<details class="od">' not in page
    assert re.search(r'<label><input type="radio" name="t\w+" class="tr0" checked hidden="hidden">Graph</label>', page)
    assert '<div class="tp tp1"><div class="wk">' in page and '<span class="nt n-aws-reads"' in page
    assert '<code class="ocd"><span class="pk">def</span> <span class="pf">handler</span>' in page
    assert "save()</b>" in page and "store.py line 4" in page and "Step 5 of 8" in page
    assert ".lmb .wk:has(.w7:checked) .wp7{display:block}" in page and "Cascadia Mono" in page


def test_how_it_works_steps_walk_like_a_debugger(ui):
    ui.core._download = lambda url, most: HOW
    cmap = ui.core.code_map("etl-nightly")
    walk = _Walk(cmap.startup, cmap.steps, "At start-up", "Each call")
    drawn = _WalkHtml(walk, _Report(0, root="lmb"))
    stops = drawn.stops
    assert [(s.part, s.step.line, s.depth) for s in stops] == [
        ("start", 6, 0), ("call", 9, 0), ("call", 10, 1), ("call", 11, 2), ("call", 12, 2), ("call", 4, 3),
        ("call", 6, 4), ("call", 13, 1)]
    assert stops[5].caller == 4 and stops[6].frames == [1, 5] and stops[3].around == [2] and stops[2].end == 7

    def buttons(i):
        found = re.findall(r'<(label|span) class="wbt[^"]*"(?: for="w\w+-(\d+)")?[^>]*>([^<]+)<', drawn._buttons(i, stops[i]))
        return [(text, int(to) if to else None) for _, to, text in found]

    assert buttons(0) == [("‹ Back", None), ("Next ›", 1), ("Over ⤼", None), ("Out ↑", None)]
    assert buttons(4) == [("‹ Back", 3), ("Into save() ›", 5), ("Over ⤼", 7), ("Out ↑", None)]
    assert buttons(6) == [("‹ Back", 5), ("Next ›", 7), ("Over ⤼", None), ("Out ↑", 7)]
    assert buttons(2)[2] == ("Over ⤼", 7) and buttons(7)[1] == ("From the top ⟲", 0)
    assert "Goes into save(), in store.py line 4, called from line 12." in drawn._does(5, stops[5])
    assert "Returns: what Lambda hands back" in drawn._does(7, stops[7])
    assert "A loop: the lines under it run once for each pass." in drawn._does(2, stops[2])
    page = drawn.html()
    assert page.count('type="radio"') == 8 and page.count(" checked") == 1 and page.count('class="wp wp') == 8
    assert "handler()</label> › <b>save()</b>" in drawn._crumbs(5, stops[5])
    assert '-5" title="Go to its first line">save()</label>' in drawn._crumbs(6, stops[6])
    empty = stylemod._render_html([_Walk([], [], title="Each call", empty="Lambda calls handler(), which isn't in app.py.")], 0)
    assert "Lambda calls handler(), which isn&#x27;t in app.py." in empty


def test_how_it_works_page_saves_a_page_to_open_in_a_tab(ui, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ui.core._download = lambda url, most: HOW
    out = run(capsys, ui.how_it_works_page, "etl-nightly")
    page_path = tmp_path / "lambda-downloads" / "etl-nightly-how-it-works.html"
    assert "Saved how etl-nightly works as a page" in out and "lambda-downloads/etl-nightly-how-it-works.html" in out
    assert "middle-click it, or right-click it and pick Open link in new tab, for a browser tab of its own." in out
    assert "lambda-downloads was made for downloads, with a .gitignore" in out and "how_it_works('etl-nightly')" in out
    page = page_path.read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>") and "<title>How etl-nightly works</title>" in page
    assert "color-scheme:light dark" in page and ".lmb .flow,.lmb .wk{max-width:none;max-height:none}" in page
    assert "BUCKET isn&#x27;t set on etl-nightly" in page
    assert '<div class="wk">' in page and "<script" not in page and "hunter2" not in page
    again = run(capsys, ui.how_it_works_page, "etl-nightly", "notes/etl.html")
    assert (tmp_path / "notes" / "etl.html").exists() and "notes/etl.html" in again
    saved = ui._save_how_page(ui.core.code_map("etl-nightly"))
    assert saved.replaced and not saved.marked and saved.size == len(page.encode("utf-8"))
    link = stylemod._render_html(ui._page_blocks(saved), 50)
    assert ('<a class="pgo" href="lambda-downloads/etl-nightly-how-it-works.html" target="_blank" rel="noopener"'
            in link)
    outside = ui._save_how_page(ui.core.code_map("etl-nightly"), str(tmp_path.parent / "elsewhere.html"))
    assert '<a class="pgo"' not in stylemod._render_html(ui._page_blocks(outside), 50)


def test_how_it_works_page_draws_the_whole_graph(ui, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    chain = "".join(f"def step{i}():\n    return step{i + 1}()\n\n" for i in range(8)) + "def step8():\n    return 1\n"
    ui.core._download = lambda url, most: zipped({"app.py": chain + "def handler(event, context):\n    return step0()\n"})
    out = run(capsys, ui.how_it_works, "etl-nightly")
    assert ("The graph is cut to fit here: it leaves out 4 functions (too deep or too many). "
            "how_it_works_page('etl-nightly') saves a page with the whole graph.") in out
    run(capsys, ui.how_it_works_page, "etl-nightly")
    page = (tmp_path / "lambda-downloads" / "etl-nightly-how-it-works.html").read_text(encoding="utf-8")
    assert "step8()" in page and "not drawn" not in page and "The graph is cut" not in page


def test_download_report(ui, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = run(capsys, ui.download, "etl-nightly")
    assert "Downloaded etl-nightly's code" in out and "Saved as: lambda-downloads/etl-nightly.zip" in out
    assert "Files: 3" in out and "SHA-256: matches Lambda's" in out
    assert "right-click it in the file browser and pick Download" in out
    assert "lambda-downloads was made for downloads, with a .gitignore" in out
    assert "code('etl-nightly')" in out
    assert ("import shutil; shutil.unpack_archive('lambda-downloads/etl-nightly.zip', 'lambda-downloads/etl-nightly')"
            in out)
    again = run(capsys, ui.download, "etl-nightly")
    assert "where it replaced the one downloaded before" in again and "was made for downloads" not in again
    ui.core._download = lambda url, most: b"not a zip"
    ui.core._packages.clear()
    odd = run(capsys, ui.download, "etl-nightly", "odd.zip")
    assert "SHA-256: differs from Lambda's (!)" in odd and "Files: not a .zip (!)" in odd
    assert "download('etl-nightly', " in odd and "odd.zip')" in odd
    assert "No function 'etl-nightly2' in us-east-1" in run(capsys, ui.download, "etl-nightly2")
    version = lfmod.PackageDownload(fn(name="etl", version="7", region=OTHER), str(tmp_path / "etl-v7.zip"), 10,
                                    files=1, sha256_matches=False)
    text = _render_text(ui._download_blocks(version), 50)
    assert "version 7" in text and f"download('etl:7', '{tmp_path / 'etl-v7.zip'}', region='{OTHER}')" in text
    assert f"code('etl:7', region='{OTHER}')" in text


def test_access_denied_becomes_a_note(seeded, capsys):
    core = LambdaAnalyzer(region=REGION, clients={"lambda": lambda_clients(denied=("list_functions", "get_function"))})
    ui = LambdaView(core, mode="text")
    out = run(capsys, ui.functions)
    assert "Couldn't list the functions in us-east-1 (AccessDeniedException; needs lambda:ListFunctions)" in out
    assert "Traceback" not in out
    info = run(capsys, ui.function_info, "etl-nightly")
    assert info.startswith("[!] AccessDeniedException: not authorized. README lists the read-only IAM permissions")


def test_html_report(core):
    shown = []
    ui = LambdaView(core, mode="html")
    ui._show = shown.append
    ui.functions()
    page = stylemod._render_html(shown[0], 50)
    assert page.startswith("<style>") and 'class="lmb"' in page and "⚡ Lambda" in page
    assert '<span class="pill bad">python3.9 (ended)</span>' in page
    ui.function_info("etl-nightly")
    page = stylemod._render_html(shown[1], 50)
    # the configuration is a JSON tree to fold, coloured like code, with each line's path from fn.raw
    assert '<details class="sec"><summary>Configuration, as Lambda returns it' in page
    assert '<div class="jt">' in page and ".lmb .jt .jk{" in page
    assert '<span class="jk">&quot;Timeout&quot;</span>' in page and "title=\"raw[&#x27;Timeout&#x27;]\"" in page
    assert "hunter2" not in page and "(hidden)" in page


def test_help_lists_every_command(ui, capsys):
    out = run(capsys, ui.help)
    assert set(_commands(LambdaView)) == {name for names in LambdaView._GROUPS.values() for name in names}
    assert "Start here:" in out and "Other" not in out and "functions(regions='all')" in out
    assert "request_id=" in run(capsys, ui.help, "logs")
    assert "Did you mean 'errors'" in run(capsys, ui.help, "eror")


def test_view_validates_its_options(core):
    with pytest.raises(ValueError, match="mode must be"):
        LambdaView(core, mode="pdf")
    with pytest.raises(ValueError, match="progress must be"):
        LambdaView(core, progress="loud")


def test_prices_are_list_prices():
    assert LAMBDA_PRICES["request"] == 0.20 and LAMBDA_PRICES["gb_second_arm"] < LAMBDA_PRICES["gb_second"]
    assert LambdaView(LambdaAnalyzer(region=REGION), mode="text")._price_basis() == "us-east-1 list prices"
    assert LambdaView(LambdaAnalyzer(region=REGION, prices={"request": 0.3}), mode="text")._price_basis() == (
        "your prices")


def test_ui_explore_needs_jupyter(ui, capsys):
    out = run(capsys, ui.explore)
    assert "The explorer window needs Jupyter" in out and "logs('name') shows what one logged" in out


# ---- the explorer window


def plain(value):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())


def enter(text_box):
    text_box._handle_custom_msg({"event": "submit"}, [])


@pytest.fixture
def explorer(core):
    def make(*args, **kwargs):
        return LambdaExplorer(*args, core=core, mode="widgets", **kwargs)
    return make


def row_of(x, name):
    return next(r for r in x._rows[:len(x._visible)] if r.item.name == name)


def faces(rows, count):
    return [plain(r.face.value) for r in rows[:count]]


def test_explorer_lists_the_functions_and_opens_one(explorer):
    x = explorer()
    assert x.region == REGION and sorted(f.name for f in x.overview.functions) == ["api-handler", "etl-nightly"]
    assert plain(x.title.value) == ("Lambda explorer us-east-1 2 functions · every function, its logs run by run, its "
                                    "errors and code · read-only")
    assert plain(x.stats.value) == ("Functions 2 Calls · 30d 1,000 Error rate 4.0% Est. $ / month <$0.01 "
                                    "Need attention 2")
    assert [c.description for c in x.chips.children] == ["All 2", "Needs attention 2", "With errors 1",
                                                         "Old runtime 1", "Public 1", "Not called 1"]
    assert [f.fn.name for f in x._visible] == ["etl-nightly", "api-handler"]  # problems first
    assert faces(x._rows, 2) == [
        "etl-nightly python3.9 (ended) Nightly orders load · SQS, S3 · 1,024 MB · 900 s 1,000 4.0% 250 ms <$0.01 today 3",
        "api-handler python3.12 Anyone (public) · 128 MB · 3 s 0 - - $0.00 not in 30d 1"]
    status = plain(x.status.value)
    assert status.startswith("2 functions in us-east-1, read in") and "2 need attention" in status
    assert "(15 CloudWatch metrics, about <$0.01)" in status  # what reading the list cost
    assert plain(x.field.face.value) == "Function Pick a function" and "lmx-dim" in x.tab_buttons["logs"]._dom_classes
    x.tab_buttons["logs"].click()
    assert "Pick a function first" in plain(x.run_head.value)
    row_of(x, "etl-nightly").button.click()
    assert x.function.name == "etl-nightly" and x._tab == "overview" and x.detail.function.tags == {"team": "ml"}
    assert "etl-nightly python3.9" in plain(x.field.face.value) and "lmx-on" in row_of(x, "etl-nightly").box._dom_classes
    assert plain(x.stats.value) == ("Calls · 30d 1,000 Error rate 4.0% Avg / longest run 250 ms / 4m 10s "
                                    "Est. $ / month <$0.01 Warnings 3")
    overview = plain(x.overview_view.value)
    assert "Runtime python3.9 (ended) Memory 1,024 MB Timeout 900 s Architecture x86_64 Triggers 2" in overview
    assert "Calls (30d)" not in overview  # the header shows the numbers
    assert "the last today The Errors tab groups them by cause" in overview  # a headline, then its detail
    assert "errors('etl-nightly')" not in overview
    assert "How it's wired" in overview and "SQS queue orders batches of up to 10" in overview
    assert "On failure nowhere: dropped events from S3 bucket that still fail after 2 retries are lost" in overview
    assert "Calls a day, the failed part in red" in overview and "Estimated monthly cost" in overview
    assert not any(isinstance(b, _Next) for b in x.shown["overview"])
    assert "lmx-alert" in x.tab_buttons["overview"]._dom_classes and "lmx-alarm" in x.tab_buttons["errors"]._dom_classes
    assert plain(x.status.value) == "etl-nightly: 3 warnings · the Logs tab shows what it logged, run by run"
    x.tab_buttons["settings"].click()
    settings = plain(x.settings_view.value)
    assert "Settings of etl-nightly in plain English" in settings and "DB_PASSWORD, MODE" in settings
    assert "hunter2" not in settings and "aws lambda get-function-configuration --function-name etl-nightly" in settings
    assert repr(x) == "LambdaExplorer(etl-nightly) · help(LambdaExplorer) says what it shows"


def html_widgets(widget):
    """
    Every widget in a window that shows HTML.
    """
    found, todo = [], [widget]
    while todo:
        item = todo.pop()
        if type(item).__name__ == "HTML":
            found.append(item)
        todo += getattr(item, "children", ())
    return found


def test_explorer_holds_the_report_css_once(explorer):
    # Each page is a report drawn without the stylesheet _render_html starts with, which the window's hidden style
    # widget holds once. _html drops it with str.replace, which does nothing when the string doesn't match. A page
    # keeps only the rules its own tables add (their filters and columns), which ship with them.
    x = explorer()
    row_of(x, "etl-nightly").button.click()
    x.tab_buttons["settings"].click()
    panes = html_widgets(x.root)
    [style] = [pane for pane in panes if stylemod._CSS in pane.value]
    assert style.layout.display == "none"
    pages = [pane for pane in panes if '<div class="lmb">' in pane.value]
    rules = [rule for page in pages for style in page.value.split("<style>")[1:]
             for rule in style.split("</style>")[0].split("\n")]
    assert len(pages) >= 2 and all(rule.startswith(".lmb form.tbl:has(") for rule in rules)


def test_explorer_searches_filters_sorts_and_pages_the_functions(role, core, explorer):
    for i in range(43):
        create(f"batch-{i:02d}", role, Description="a nightly batch" if i % 2 else "")
    x = explorer()
    assert len(x._visible) == 45 and len(x.rows_box.children) == 41  # the header, then 40 rows
    assert plain(x._pagers["functions"][0].value) == "1–40 of 45 functions"
    x._pagers["functions"][1]["next"].click()
    assert len(x.rows_box.children) == 6 and plain(x._pagers["functions"][0].value) == "41–45 of 45 functions"
    x.find.value = "nightly"
    assert [f.fn.name for f in x._visible][0] == "etl-nightly" and len(x._visible) == 22  # best match first
    assert "<mark>nightly</mark>" in x._rows[0].face.value
    x.find.value = "sqs"
    assert [f.fn.name for f in x._visible] == ["etl-nightly"]  # its trigger
    x.find.value = "zzz"
    assert "No function matches. Clear the search, or pick All above." in plain(x.rows_box.children[1].value)
    x.find.value = ""
    next(c for c in x.chips.children if c.description.startswith("Public")).click()
    assert [f.fn.name for f in x._visible] == ["api-handler"]
    assert plain(x._pagers["functions"][0].value) == "1–1 of 1 functions (of 45)"
    next(c for c in x.chips.children if c.description.startswith("Public")).click()
    x.column_buttons["calls"].click()
    assert x._visible[0].fn.name == "etl-nightly" and x.column_buttons["calls"].description == "Calls · 30d ▾"
    x.column_buttons["name"].click()
    assert [f.fn.name for f in x._visible][:2] == ["api-handler", "batch-00"]
    x.column_buttons["name"].click()  # again: the other way
    assert x._visible[0].fn.name == "etl-nightly" and x.column_buttons["name"].description == "Function ▾"


def test_explorer_logs_tab_shows_runs(core, explorer):
    now = datetime.now(timezone.utc)
    ids = seed_runs(now)
    x = explorer("etl-nightly", tab="logs")
    assert x.function.name == "etl-nightly" and x._tab == "logs"
    assert [run.request_id for run in x._runs] == [*reversed(ids), RID]
    assert [c.description for c in x.run_chips.children] == ["All runs 5", "Failed 1", "Cold starts 1"]
    head = plain(x.run_head.value)
    assert "5 runs 1 failed (20.0%) median 52 ms slowest 102 ms of 900 s memory up to 88 of 1,024 MB" in head
    assert "Newest first, the last hour; times in UTC" in head
    assert plain(x.status.value) == "5 runs in the last hour, 1 failed · click a run to see its lines"
    failed = x._run_rows[4]
    assert "✕" in plain(failed.face.value) and "KeyError: 'customer_id' 8f5ce35b" in plain(failed.face.value)
    assert "cold 300 ms" in plain(failed.face.value) and failed.body.layout.display == "none"
    failed.button.click()
    body = plain(failed.body.value)
    assert failed.body.layout.display == "" and "lmx-open" in failed.box._dom_classes
    assert "+0.005s loading 1,204 orders +0.010s ERROR KeyError: 'customer_id'" in body
    assert f"used 88 of 1,024 MB cold start 300 ms request {RID}" in body and "START RequestId" not in body
    assert failed.button.tooltip.startswith("Failed · started") and "click to fold its lines" in failed.button.tooltip
    failed.button.click()
    assert failed.body.layout.display == "none"
    next(c for c in x.run_chips.children if c.description.startswith("Failed")).click()
    assert [run.request_id for run in x._visible_runs()] == [RID] and len(x.runs_box.children) == 1
    next(c for c in x.run_chips.children if c.description.startswith("Failed")).click()
    x.log_find.value = "ord-77"  # finds runs as you type, in any case
    assert [run.request_id for run in x._visible_runs()] == [ids[1]] and "<mark>ORD-77</mark>" in (
        x._run_rows[0].face.value)
    assert plain(x.status.value) == "1 of 5 runs read have 'ord-77' · Enter searches CloudWatch too"
    x.log_find.value = "ORD-77"
    enter(x.log_find)  # CloudWatch searches the whole range, and brings back whole runs
    assert x.found is not None and [run.request_id for run in x._found_runs] == [ids[1]]
    assert x._run_rows[0].body.layout.display == "" and " hit" in x._run_rows[0].body.value  # opened, line marked
    assert plain(x.status.value) == "1 run with 'ORD-77' in the last hour · ✕ shows every run again"
    x.log_find.value = ""
    assert x.found is None and len(x._visible_runs()) == 5
    x.log_find.value = "?ERROR ?WARN"
    assert "That's a CloudWatch Logs filter pattern: press Enter" in plain(x.status.value)


def test_explorer_shows_times_in_the_zone_picked(core, explorer):
    seed_runs(datetime.now(timezone.utc))
    set_timezone("Asia/Kolkata")
    x = explorer("etl-nightly", tab="logs")
    assert "Newest first, the last hour; times in IST" in plain(x.run_head.value)
    row = x._run_rows[0]
    clock = row.run.start.astimezone(ZoneInfo("Asia/Kolkata"))
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    face = f"{clock:%H:%M:%S}" if clock.date() == today else f"{clock:%b} {clock.day} {clock:%H:%M}"
    assert face in plain(row.face.value)
    assert f"started {clock:%Y-%m-%d %H:%M:%S} IST" in row.button.tooltip


def test_explorer_logs_tab_reaches_further_back(core, explorer, monkeypatch):
    x = explorer("etl-nightly", tab="logs")
    x.range_pick.value = "15m"
    assert [run.request_id for run in x._runs] == [RID]
    x.range_pick.value = "3h"
    assert x._range == "3h" and "the last 3 hours" in plain(x.run_head.value)
    quiet = explorer("api-handler", tab="logs")
    assert "api-handler has no log group yet" in plain(quiet.run_head.value)
    put_logs("/aws/lambda/api-handler", [(datetime.now(timezone.utc) - timedelta(hours=5), "an old line")])
    quiet.range_pick.value = "1h"
    quiet._reload_logs()
    assert "Nothing logged in the last hour. Its newest line is from 5h ago." in plain(quiet.runs_box.children[0].value)
    assert quiet.jump_button.layout.display == "" and quiet.jump_button.description == "Show last 12 hours ›"
    quiet.jump_button.click()
    assert quiet._range == "12h" and quiet.range_pick.value == "12h" and len(quiet._runs) == 1
    monkeypatch.setattr(logs_tab, "_RUN_LINES", 6)  # reading stops at 6 lines: Older runs reads the rest
    ids = seed_runs(datetime.now(timezone.utc))
    x._reload_logs()
    assert x.logs_page.truncated and x.older_button.layout.display == ""
    assert "These are the newest 6 lines" in plain(x.run_head.value)
    for _ in range(5):
        if not x.logs_page.truncated:
            break
        x.older_button.click()
    assert {run.request_id for run in x._runs} == {RID, *ids} and x.older_button.layout.display == "none"
    x.run(RID)
    assert x.found is not None and x._found_runs[0].request_id == RID and x.log_find.value == RID
    assert x._range == "24h"  # run() looks back a day at least


def test_explorer_live_adds_new_runs(core, explorer):
    x = explorer("etl-nightly", tab="logs")
    assert [run.request_id for run in x._runs] == [RID]
    chips = list(x.run_chips.children)
    ids = seed_runs(datetime.now(timezone.utc), count=2)
    x.live_button.click()  # no event loop here, so it looks once
    assert x._live and "lmx-on" in x.live_button._dom_classes
    assert [run.request_id for run in x._runs] == [*reversed(ids), RID]
    assert x.run_chips.children[0] is chips[0] and chips[0].description == "All runs 3"  # the same widgets, relabelled
    assert plain(x.status.value).startswith("Live · 2 new runs since it started · checked")
    x.tab_buttons["errors"].click()
    assert not x._live and "lmx-on" not in x.live_button._dom_classes  # Live runs while the Logs tab is open


def test_explorer_errors_and_performance_tabs_open_their_runs(core, explorer):
    x = explorer("etl-nightly", tab="errors")
    errors = plain(x.errors_view.value)
    assert "Errors in etl-nightly" in errors and "The most common error happened once" in errors
    assert "KeyError: 'customer_id'" in errors
    assert f"Run {RID[:8]} in the Logs tab shows the whole run" in errors and "The newest error lines" not in errors
    assert faces(x._error_rows, 1) == [f"✕ {plain(x._error_rows[0].face.value).split()[1]} KeyError KeyError: "
                                       "'customer_id' Read the run ›"]
    assert "The newest failed runs" in plain(x.error_head.value)
    x._error_rows[0].button.click()
    assert x._tab == "logs" and x._found_runs[0].request_id == RID and x.log_find.value == RID
    assert x._run_rows[0].body.layout.display == ""
    assert plain(x.status.value) == f"Run {RID}: failed in 102 ms · ✕ shows every run again"
    x.tab_buttons["performance"].click()
    perf = plain(x.perf_view.value)
    assert "Performance of etl-nightly" in perf and "256 MB still leaves room" in perf and "The slowest runs" not in perf
    assert "102 ms 88 of 1,024 MB · cold start 300 ms" in plain(x._slow_rows[0].face.value)
    x.errors_pick.value = "3d"
    assert x.error_report.since < datetime.now(timezone.utc) - timedelta(days=2)
    x._slow_rows[0].button.click()
    assert x._tab == "logs" and x._found_runs[0].request_id == RID


NESTED = zipped({
    "app.py": "def handler(event, context):\n    return 1\n",
    "lib/helpers.py": "def helper():\n    return 2\n",
    "lib/data/rates.json": '{"usd": 1}',
    "vendor/requests/api.py": "def get(url):\n    ...\n",
})


def tree_of(x):
    """
    The lines the Code tab's tree shows, as text (search marks dropped).
    """
    return [plain(re.sub("</?mark>", "", row.face.value)) for row in x._code_rows[:len(x.files_box.children)]]


def test_explorer_code_tab(core, explorer, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(code_tab, "_TOGGLE_GRACE", 0)
    x = explorer("etl-nightly", tab="code")
    assert x.package is not None and x._code_file == "app.py" and x.code_split.layout.display == ""
    assert tree_of(x) == ["KEY .env 20 B", "PY app.py HANDLER 61 B", "PY utils.py 28 B"]
    assert "lmx-on" in x._code_rows[1].box._dom_classes and "lmx-tree" in x._code_rows[0].box._dom_classes
    assert plain(x._pagers["files"][0].value) == "3 files"
    assert "The package holds .env" in plain(x.code_view.value) and "Every file" not in plain(x.code_view.value)
    source = x.source_view.value
    assert "app.py" in plain(source) and "the handler&#x27;s file" in source and '<span class="pk">def</span>' in source
    assert '<pre class="gut">1\n2</pre>' in source

    # Hide files folds the tree away so the source has the whole width, and Show files brings it back
    toggle = x.files_toggle
    assert toggle.description == "Hide files" and "lmx-i-files-hide" in toggle._dom_classes
    assert x.code_bar.children[0] is toggle and x.code_left.layout.display == ""
    toggle.click()
    assert x.code_left.layout.display == "none" and x.code_split.layout.display == ""
    assert toggle.description == "Show files" and toggle.tooltip == "Show the package's files beside the source"
    assert "lmx-i-files-show" in toggle._dom_classes and "lmx-i-files-hide" not in toggle._dom_classes
    assert plain(x.status.value) == ("3 files in its package · Show files lists them beside the source · How it works "
                                     "explains the code")
    assert x.source_view.value == source  # the file shown stays
    toggle.click()
    assert x.code_left.layout.display == "" and toggle.description == "Hide files"
    assert plain(x.status.value) == ("3 files in its package · click a folder to open it, a file to see its source · "
                                     "How it works explains the code")
    copy = re.search(r'<span class="cpy"[^>]*title="([^"]*)"><span class="cpt">(.*?)</span></span>', source, re.S)
    assert copy and "to copy all of app.py" in copy.group(1) and 'aria-label="Copy app.py"' in source
    held = "def handler(event, context):\n    return event['customer_id']\n\n"  # a browser copies one line break less
    assert html.unescape(copy.group(2)) == held
    x._code_rows[0].button.click()
    assert ".env looks like a secrets file, so its text isn't shown" in plain(x.source_view.value)
    assert "hunter2" not in x.source_view.value and 'class="cpy"' not in x.source_view.value
    x.code_find.value = "util"
    assert len(x.files_box.children) == 1 and "<mark>util</mark>s.py" in x._code_rows[0].face.value
    assert plain(x._pagers["files"][0].value) == "1 of 3 files match"

    # Download .zip saves the package the tab read, with no new download
    assert x.code_bar.layout.display == "" and "saves it in lambda-downloads/" in plain(x.code_saved.value)
    core._download = lambda url, most: pytest.fail("Download .zip downloads nothing more")
    x.code_save.click()
    assert (tmp_path / "lambda-downloads" / "etl-nightly.zip").read_bytes() == CODE and x.saved.sha256_matches
    assert "✓ Saved as lambda-downloads/etl-nightly.zip" in plain(x.code_saved.value)
    assert plain(x.status.value).startswith("Saved etl-nightly's package as lambda-downloads/etl-nightly.zip (")
    x.code_save.click()
    assert plain(x.status.value).endswith(", replacing the one there.")

    # the tree: folders first, a folder that holds only a folder on one line, opened and folded by a click
    core._download = lambda url, most: NESTED
    core._packages.clear()
    nested = explorer("etl-nightly", tab="code")
    assert tree_of(nested) == ["lib/ 37 B", "vendor/requests/ 22 B", "PY app.py HANDLER 42 B"]
    assert len(nested.files_box.children) == 3
    assert plain(nested._pagers["files"][0].value) == "4 files in 4 folders"
    assert nested._code_rows[0].button.tooltip == "lib/ · 2 files, 37 B unzipped · click to open it"
    nested._code_rows[0].button.click()
    assert tree_of(nested) == ["lib/ 37 B", "data/ 10 B", "PY helpers.py 27 B", "vendor/requests/ 22 B",
                                           "PY app.py HANDLER 42 B"]
    assert 'style="--d:1"' in nested._code_rows[2].face.value
    nested._code_rows[2].button.click()
    assert nested._code_file == "lib/helpers.py" and "def helper" in plain(nested.source_view.value)
    assert "lmx-on" in nested._code_rows[2].box._dom_classes
    nested._code_rows[0].button.click()  # folds lib/ again
    assert tree_of(nested) == ["lib/ 37 B", "vendor/requests/ 22 B", "PY app.py HANDLER 42 B"]
    monkeypatch.setattr(code_tab, "_TOGGLE_GRACE", 60)
    nested._code_rows[0].button.click()  # so soon after the last click on it: a double click's second click
    assert tree_of(nested) == ["lib/ 37 B", "vendor/requests/ 22 B", "PY app.py HANDLER 42 B"]
    monkeypatch.setattr(code_tab, "_TOGGLE_GRACE", 0)
    nested._code_rows[0].button.click()
    opened = tree_of(nested)
    assert opened == ["lib/ 37 B", "data/ 10 B", "PY helpers.py 27 B", "vendor/requests/ 22 B",
                              "PY app.py HANDLER 42 B"]
    nested.code_find.value = "api"
    assert tree_of(nested) == ["vendor/requests/ 22 B", "PY api.py 22 B"]
    nested._code_rows[0].button.click()  # folds a folder while searching
    assert tree_of(nested) == ["vendor/requests/ 22 B"]
    nested.code_find.value = ""
    assert tree_of(nested) == opened
    nested.code_save.click()
    assert nested.saved.sha256_matches is False
    assert "its SHA-256 differs from the one Lambda reports" in plain(nested.status.value)
    nested.files_toggle.click()  # hidden, the tree stays hidden for the next function's code
    nested.open("api-handler", tab="code")
    assert nested.package.function.name == "api-handler" and nested.code_left.layout.display == "none"
    assert nested.files_toggle.description == "Show files"

    def too_big(url, most):
        raise ValueError("The package is 80.0 MB, more than max_size (50.0 MB): pass max_size='81MB' to read it anyway.")

    core._download = too_big
    other = explorer("etl-nightly", tab="code")
    assert other.code_more.layout.display == "" and other.code_more.description == "Download all 80.0 MB anyway"
    assert plain(other.code_view.value) == ("The package is 80.0 MB, more than the 50 MB the window downloads at first: "
                                            "the button under this downloads all of it.")
    core._download = lambda url, most: CODE if most is None else too_big(url, most)
    other.code_more.click()
    assert other.package is not None and len(other.package.files) == 3



def test_explorer_how_it_works(core, explorer, role, monkeypatch, tmp_path):
    core._download = lambda url, most: HOW
    x = explorer("etl-nightly", tab="code")
    button = x.how_button
    assert button.layout.display == "" and "lmx-i-how" in button._dom_classes and "lmx-on" not in button._dom_classes
    assert x.code_bar.children[1] is button and "How the code works, read from its files" in button.tooltip
    assert plain(x.status.value).endswith("a file to see its source · How it works explains the code")
    source = x.source_view.value
    button.click()
    assert x._how and "lmx-on" in button._dom_classes and x.code_map.entry == "app.handler"
    shown = plain(x.source_view.value)
    assert "How etl-nightly works" in shown and "BUCKET isn't set on etl-nightly" in shown
    assert "What each call does, step by step from handler()" in shown and "Next:" not in shown
    assert "code(" not in shown and [type(b).__name__ for b in x.shown["how"]][:3] == ["_Title", "_Tabs", "_Cards"]
    assert '<g class="cn k-handler n-f0' in x.source_view.value and "Into save() ›" in x.source_view.value
    assert ".lmb .cg:has(.n-f0:hover)" in x.source_view.value  # the pointing rules come with the pane
    assert plain(x.status.value) == ("How it works, read from 2 Python files with nothing run · click a file to see its "
                                     "source · 1 warning")
    assert button.tooltip == "Back to the source of the file picked in the tree"
    button.click()  # back to the source
    assert not x._how and x.source_view.value == source and "lmx-on" not in button._dom_classes
    monkeypatch.setattr(code_tab, "map_package", lambda *args: pytest.fail("the code is read once"))
    button.click()
    assert x._how and "How etl-nightly works" in plain(x.source_view.value)
    assert x.page_button.layout.display == "" and x.page_link.layout.display == "none"
    monkeypatch.chdir(tmp_path)
    x.page_button.click()
    assert x.page_saved.path == str(tmp_path / "lambda-downloads" / "etl-nightly-how-it-works.html")
    assert x.page_link.layout.display == "" and "<!doctype" not in x.page_link.outputs[0]["data"]["text/html"]
    assert 'href="lambda-downloads/etl-nightly-how-it-works.html"' in x.page_link.outputs[0]["data"]["text/html"]
    assert plain(x.status.value).startswith("Saved how etl-nightly works as lambda-downloads/etl-nightly-how-it-works")
    x._code_rows[2].button.click()  # a file in the tree: its source, and the button is up again
    assert not x._how and "def save" in plain(x.source_view.value) and "lmx-on" not in button._dom_classes
    # a function on another runtime has no How it works
    create("web", role, Runtime="nodejs20.x", Handler="index.handler")
    web = explorer("web", tab="code")
    assert web.how_button.layout.display == "none" and "How it works" not in plain(web.status.value)


def test_explorer_how_it_works_in_the_background(core, explorer):
    held = threading.Event()

    def held_back(fn, data):
        assert held.wait(10)
        return lfmod.map_code({"app.py": "def handler(event, context):\n    return event['x']\n"}, "app.handler",
                              function=fn)

    async def main():
        x = explorer("etl-nightly", tab="code")
        while x._tasks:
            await asyncio.gather(*x._tasks.values())
        original_map = code_tab.map_package
        code_tab.map_package = held_back
        try:
            x.how_button.click()
            assert "map" in x._tasks and x.code_map is None and "skw" in x.source_view.value  # it returned at once
            assert plain(x.status.value) == "Reading etl-nightly's code, starting at its handler…"
            x.how_button.click()  # back to the source before it's read: what comes back is dropped
            held.set()
            while x._tasks:
                await asyncio.gather(*x._tasks.values())
            assert not x._how and "customer_id" in plain(x.source_view.value) and x.code_map is None
            x.how_button.click()
            while x._tasks:
                await asyncio.gather(*x._tasks.values())
            assert x._how and x.code_map.event[0].label == "x" and "How etl-nightly works" in plain(x.source_view.value)
        finally:
            code_tab.map_package = original_map

    asyncio.run(main())


def test_explorer_function_field_regions_and_commands(core, explorer):
    x = explorer()
    x.field.button.click()
    assert x.field.is_open and x.backdrop.layout.display == ""
    assert [arn.split(":")[-1] for arn in x.field.shown] == ["api-handler", "etl-nightly"]
    assert plain(x.field.foot.value) == "2 functions in us-east-1"
    x.field.search.value = "nightly"
    assert [arn.split(":")[-1] for arn in x.field.shown] == ["etl-nightly"]
    assert "<mark>nightly</mark>" in x.field.rows[x.field.shown[0]][2].value
    enter(x.field.search)
    assert not x.field.is_open and x.function.name == "etl-nightly"
    x.tab_buttons["logs"].click()
    x.field.open()
    x.field.search.value = "api"
    enter(x.field.search)
    assert x.function.name == "api-handler" and x._tab == "logs"  # the field keeps the tab
    x.field.open()
    x.field.search.value = f"arn:aws:lambda:{OTHER}:{ACCOUNT}:function:eu-fn"
    enter(x.field.search)  # not listed in us-east-1: looked up by its ARN
    assert x.function.name == "eu-fn" and x.function.region == OTHER
    x.field.open()
    x.backdrop.click()
    assert not x.field.is_open
    x.open("nope")
    assert plain(x.status.value).startswith("No function 'nope' in us-east-1.")
    x.open("etl-nightlyy")
    assert "Did you mean 'etl-nightly'?" in plain(x.status.value)
    x.open("not a name")
    assert "isn't a Lambda function name" in plain(x.status.value)
    x.region_pick.value = OTHER  # another region: a function that isn't there closes
    assert [f.name for f in x.overview.functions] == ["eu-fn"] and x.function.name == "eu-fn"
    x.region_pick.value = REGION
    assert x.function is None and x._tab == "functions"
    x.logs("etl-nightly", search="loading")
    assert x._tab == "logs" and [run.request_id for run in x._found_runs] == [RID] and x.logs_page is None
    x.log_find.value = ""  # it opened on a search: clearing it reads the time range
    assert x.logs_page is not None and [run.request_id for run in x._visible_runs()] == [RID]
    with pytest.raises(ValueError, match="tab= is one of"):
        explorer(tab="charts")


def test_explorer_every_region(core, explorer):
    x = explorer(region="all")
    assert x.region == "all" and x.region_pick.value == "all" and len(x.overview.regions) > 10
    assert sorted(f.name for f in x.overview.functions) == ["api-handler", "etl-nightly", "eu-fn"]
    assert "eu-west-1" in plain(row_of(x, "eu-fn").face.value) and "every region" in plain(x.title.value)
    x.open("eu-fn", tab="logs")
    assert x.function.region == OTHER and "eu-fn has no log group yet" in plain(x.status.value)


def test_explorer_says_what_went_wrong(seeded, explorer):
    core = LambdaAnalyzer(region=REGION, clients={"lambda": lambda_clients(denied=("list_functions",))})
    x = LambdaExplorer(core=core, mode="widgets")
    assert "Couldn't list the functions" in plain(x.list_note.value) and "AccessDeniedException" in x.field.problem
    x.open("etl-nightly")  # GetFunction still opens it
    assert x.function.name == "etl-nightly" and x.detail is not None
    broken = LambdaExplorer("etl-nightly", core=LambdaAnalyzer(
        region=REGION, clients={"lambda": lambda_clients(denied=("get_function",))}), mode="widgets")
    assert "AccessDeniedException" in plain(broken.overview_view.value)
    assert "README lists the read-only IAM permissions" in plain(broken.status.value)


def test_explorer_reads_in_the_background(seeded):
    held = threading.Event()

    def logs_client(region):
        client = boto3.client("logs", region_name=region or REGION)

        def filter_log_events(**params):
            assert held.wait(10)
            answer = client.filter_log_events(**params)
            answer.pop("ResponseMetadata", None)
            return answer

        return Patched(client, {"filter_log_events": filter_log_events})

    async def settle(x, *keys):
        while any(key in x._tasks for key in keys or list(x._tasks)):
            await asyncio.gather(*[task for key, task in x._tasks.items() if not keys or key in keys])

    async def main():
        core = LambdaAnalyzer(region=REGION, clients={"lambda": lambda_clients(), "logs": logs_client})
        x = LambdaExplorer("etl-nightly", tab="logs", core=core, mode="widgets")
        assert list(x._tasks) == ["list"] and x._said[2] and x.overview is None  # it returned at once
        await settle(x, "list", "numbers", "extras")
        assert x.function.name == "etl-nightly" and "logs" in x._tasks and x.logs_page is None
        assert "skw" in x.run_head.value
        await settle(x, "describe")
        x.tab_buttons["settings"].click()  # the window answers while the logs are read
        assert "Settings of etl-nightly" in plain(x.settings_view.value)
        x.tab_buttons["logs"].click()
        held.set()
        await settle(x)
        assert [run.request_id for run in x._runs] == [RID] and x._said[2] is False
        held.clear()
        x._reload_logs()
        x.open("api-handler", tab="logs")  # while etl-nightly's logs are still read
        held.set()
        await settle(x)
        assert x.function.name == "api-handler" and x._runs == []  # etl-nightly's came back, and were dropped
        assert "api-handler has no log group yet" in plain(x.run_head.value)
        x.open("etl-nightly", tab="logs")
        await settle(x)
        live_seconds = logs_tab._LIVE_SECONDS
        try:
            logs_tab._LIVE_SECONDS = 0.05
            x.live_button.click()
            assert "live" in x._tasks
            ids = seed_runs(datetime.now(timezone.utc), count=1)
            for _ in range(400):  # up to 20 s on a slow machine; usually a tenth of that
                await asyncio.sleep(0.05)
                if any(run.request_id == ids[0] for run in x._runs):
                    break
            assert x._runs[0].request_id == ids[0] and "1 new run since it started" in plain(x.status.value)
            task = x._tasks["live"]
            x.live_button.click()
            await asyncio.wait_for(task, 20)  # a look still in flight finishes, and is dropped
            assert not x._live and "live" not in x._tasks and plain(x.status.value) == "Live is off."
        finally:
            logs_tab._LIVE_SECONDS = live_seconds

    asyncio.run(main())


def test_explorer_shows_its_window_once_per_cell(explorer, monkeypatch):
    import IPython.display

    shown, cell = [], [1]
    monkeypatch.setattr(windowmod, "_cell_number", lambda: cell[0])  # where _Explorer looks it up
    monkeypatch.setattr(IPython.display, "display", lambda obj: shown.append(obj))
    x = explorer()
    x._ipython_display_()  # a cell ending in explore() shows it once
    assert shown == [x.root]
    cell[0] = 2
    x._ipython_display_()
    assert shown == [x.root, x.root]


def test_explorer_without_jupyter_shows_reports(core, capsys, monkeypatch):
    x = LambdaExplorer(core=core, mode="text")
    out = capsys.readouterr().out
    assert x._w is None and "Lambda functions in us-east-1" in out
    x.open("etl-nightly", tab="errors")
    assert "Errors in etl-nightly" in capsys.readouterr().out
    LambdaExplorer("etl-nightly", tab="logs", core=core, mode="text")
    assert "Logs of etl-nightly" in capsys.readouterr().out
    x.logs("etl-nightly", search="orders")
    assert "lines containing 'orders'" in capsys.readouterr().out
    x.open("nope")
    assert "No function 'nope' in us-east-1" in capsys.readouterr().out
    monkeypatch.setitem(sys.modules, "ipywidgets", None)
    LambdaExplorer(core=core, mode="widgets")
    out = capsys.readouterr().out
    assert "needs `ipywidgets` (pip install ipywidgets), which SageMaker notebooks normally have" in out
    with pytest.raises(ValueError, match="mode must be"):
        LambdaExplorer(core=core, mode="html")


def test_view_explore_opens_the_window(core):
    view = LambdaView(core, mode="html")
    view.explore("etl-nightly", tab="logs", height=700)
    x = view.explorer
    assert isinstance(x, LambdaExplorer) and x.ui is view and x.function.name == "etl-nightly" and x._tab == "logs"
    assert x.pages["logs"].layout.height == "700px"
