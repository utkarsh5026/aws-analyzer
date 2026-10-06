import io
import json
import zipfile
from datetime import date, datetime, timedelta, timezone

import boto3
import pytest
from botocore import xform_name
from botocore.exceptions import ClientError
from botocore.validate import ParamValidator
from moto import mock_aws

import lambda_functions as lfmod
from lambda_functions import (
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
    LambdaView,
    Layer,
    LogEvent,
    LogGroup,
    Performance,
    ProvisionedConcurrency,
    Trigger,
    Version,
    account_findings,
    classify_error,
    error_findings,
    function_findings,
    function_monthly_cost,
    group_errors,
    handler_file,
    human_ms,
    masked_config,
    package_findings,
    parse_event_source_mapping,
    parse_function,
    parse_function_ref,
    parse_policy,
    parse_report,
    percentile,
    performance_findings,
    provisioned_monthly_cost,
    request_id_of,
    runtime_status,
    secret_like,
    suggest_memory,
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
    assert lfmod._lambda_time("2024-05-01T10:00:00.000+0000") == datetime(2024, 5, 1, 10, tzinfo=timezone.utc)
    assert lfmod._lambda_time("2026-10-05T19:34:24.781359000Z").microsecond == 781359
    assert lfmod._lambda_time("2026-10-05T19:34:24Z").second == 24
    assert lfmod._lambda_time(None) is None and lfmod._lambda_time("yesterday") is None
    assert [human_ms(v) for v in (0.42, 102.4, 1530, 15300, 75000, 185000)] == [
        "0.42 ms", "102 ms", "1.53 s", "15.3 s", "75.0 s", "3m 05s"]
    assert lfmod._ms(1530) == "1,530 ms" and lfmod._ms(None) == "-"
    assert [lfmod._pct(v) for v in (0, 0.0004, 0.024, None)] == ["0%", "<0.1%", "2.4%", "-"]
    assert lfmod._window(900) == "15 minutes" and lfmod._window(3600) == "hour"
    assert lfmod._window(86400) == "24 hours" and lfmod._window(30 * 86400) == "30 days"
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
    assert lfmod._log_line(f"2026-10-05T23:28:28.000Z {RID} Task timed out after 60.00 seconds") == (
        "Task timed out after 60.00 seconds")
    assert lfmod._log_line(f"[ERROR]\t2026-10-05T12:00:00.123Z\t{RID}\tCouldn't save") == "[ERROR]\tCouldn't save"
    assert lfmod._log_line(f"2026-10-05T12:00:00.123Z\t{RID}\tERROR\tInvoke Error") == "ERROR\tInvoke Error"
    assert lfmod._log_line("[ERROR] KeyError: 'x'") == "[ERROR] KeyError: 'x'"
    assert lfmod._log_line('{"level": "WARN", "message": "slow"}') == "[WARN] slow"
    assert lfmod._log_line(f"REPORT RequestId: {RID}\tDuration: 5.00 ms\tBilled Duration: 5 ms\tMemory Size: 128 MB"
                           "\tMax Memory Used: 64 MB") == "REPORT: run 5 ms (billed 5 ms), 64 of 128 MB used"
    assert lfmod._lifecycle(f"START RequestId: {RID} Version: $LATEST") and lfmod._lifecycle("INIT_START Runtime")
    assert lfmod._lifecycle('{"time":"2026-10-05T12:00:00Z","type":"platform.start","record":{}}')
    assert not lfmod._lifecycle("hello")


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
    assert "The most common other error, KeyError: 'customer_id', happened 7 times" in text
    quiet = error_findings(report([], metrics=metrics(invocations=50, errors=4, throttles=2)))
    assert "CloudWatch counted 4 failed calls" in quiet[0][1] and "2 calls were throttled" in quiet[1][1]
    assert error_findings(report([])) == []
    first = error_findings(report([group("Logged error", "Couldn't save", 2)]))
    assert first == [("info", f"The most common error, Couldn't save, happened twice (the last 1h ago). "
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


def test_secret_like():
    assert secret_like(["DB_PASSWORD", "API_KEY", "STRIPE_SECRET", "DB_SECRET_ARN", "TOKEN_PARAMETER_NAME",
                        "AWS_ACCESS_KEY_ID", "MODE", "GITHUB_TOKEN"]) == ["DB_PASSWORD", "API_KEY", "STRIPE_SECRET",
                                                                          "GITHUB_TOKEN"]


def test_layer_names():
    assert Layer(f"arn:aws:lambda:{REGION}:{ACCOUNT}:layer:pandas:7").name == "pandas:7"
    assert Layer("odd").name == "odd"


# ---- AWS (moto)


def checked(client, handlers):
    """Handlers for the Lambda reads moto doesn't have, with each request and answer checked against the service
    model like a real client's."""
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
    """A moto client, with some operations answered by functions instead."""

    def __init__(self, client, handlers):
        self._client, self._handlers = client, checked(client, handlers)

    def __getattr__(self, name):
        return self._handlers.get(name) or getattr(self._client, name)


def lambda_clients(provisioned=None, reserved=None, policies=None, runtime_updates="Auto", denied=(), **handlers):
    """A function that makes a patched Lambda client per region: moto has no GetAccountSettings,
    ListProvisionedConcurrencyConfigs or GetRuntimeManagementConfig, leaves Concurrency out of GetFunction and
    writes a function URL's auth type outside the policy's Condition."""
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
    """etl-nightly: python3.9, 1 GB, 900 s timeout, environment variables with a password, an SQS queue and an S3
    bucket that trigger it, CloudWatch numbers with errors and throttles, and a run in its logs. api-handler: anyone
    may invoke it. eu-fn: nodejs18.x in eu-west-1."""
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
    with pytest.raises(ValueError, match="isn't a readable .zip"):
        core.code("etl-nightly")


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
    monkeypatch.setattr(lfmod.urllib.request, "urlopen", lambda url, timeout: Answer(b"x" * 100))
    assert download("https://s3.example/pkg.zip", 1000) == b"x" * 100
    monkeypatch.setattr(lfmod.urllib.request, "urlopen", lambda url, timeout: Answer(b"x" * 10, length=80 * MB))
    with pytest.raises(ValueError, match="The package is 80.0 MB, more than max_size"):
        download("https://s3.example/pkg.zip", 50 * MB)

    def offline(url, timeout):
        raise OSError("Network is unreachable")

    monkeypatch.setattr(lfmod.urllib.request, "urlopen", offline)
    with pytest.raises(ValueError, match="S3 gateway endpoint"):
        download("https://s3.example/pkg.zip", 50 * MB)
    with pytest.raises(ValueError, match="isn't an https"):
        download("file:///etc/passwd", 50 * MB)


def test_code_of_a_container_image(core, monkeypatch):
    image = fn(package_type="Image", runtime=None, image_uri=f"{ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/etl:1")
    monkeypatch.setattr(core, "function", lambda ref, region=None: image)
    package = core.code("etl")
    assert package.files == [] and "container image" in package.note and "etl:1" in package.note


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
    assert "The most common error, KeyError: 'customer_id', happened once" in out
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


def test_code_report(ui, capsys):
    out = run(capsys, ui.code, "etl-nightly")
    assert "Code of etl-nightly" in out and "Handler file: app.py" in out and "Files: 3" in out
    assert "The package holds .env" in out and "return event['customer_id']" in out
    assert "code('etl-nightly', file='utils.py')" in out
    assert "No file 'nope.py' in the package" in run(capsys, ui.code, "etl-nightly", "nope.py")
    secret = run(capsys, ui.code, "etl-nightly", ".env")
    assert ".env looks like a secrets file, so its text isn't shown" in secret and "hunter2" not in secret
    assert "aws lambda get-function --function-name etl-nightly --region us-east-1 --query Code.Location" in secret


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
    page = lfmod._render_html(shown[0], 50)
    assert page.startswith("<style>") and 'class="lmb"' in page and "⚡ Lambda" in page
    assert '<span class="pill bad">python3.9 (ended)</span>' in page


def test_help_lists_every_command(ui, capsys):
    out = run(capsys, ui.help)
    commands = {name for name, member in vars(LambdaView).items() if not name.startswith("_") and callable(member)}
    assert commands == {name for names in LambdaView._GROUPS.values() for name in names}
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
