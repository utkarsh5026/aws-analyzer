"""
invoke(): running a function once and showing its answer and log lines together. moto can't run a function without
Docker, so Invoke is answered by a handler here, checked against the service model like a real client's.
"""

import base64
import html
import io
import json
import re
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.validate import ParamValidator

from aws_analyzer.lambda_functions import LAMBDA_PRICES, Function, LambdaAnalyzer, LambdaView
from aws_analyzer.lambda_functions.explorer.window import LambdaExplorer
from aws_analyzer.lambda_functions import invoke as invoke_mod
from aws_analyzer.lambda_functions.analysis.invoke import (
    EVENT_SAMPLES, TAIL_STREAM, Invoked, call_cost, check_event, code_lines, decode_tail, event_payload,
    format_event, invoke_extras, invoke_findings, lambda_lines, read_answer, sample_event, tail_events,
    tail_is_whole, xray_trace,
)
from aws_analyzer.lambda_functions.blocks import invoke as invoke_block

from test_lambda_functions import REGION, create, lambda_clients, put_logs, role, run  # noqa: F401
from test_lambda_functions import aws  # noqa: F401

RID = "5c2bd1c6-0d55-4b9c-9a41-6f9c2a2f0a11"

OK_TAIL = (
    f"START RequestId: {RID} Version: $LATEST\n"
    f"[INFO]\t2026-11-23T10:00:00.120Z\t{RID}\tloading order 42\n"
    f"END RequestId: {RID}\n"
    f"REPORT RequestId: {RID}\tDuration: 312.40 ms\tBilled Duration: 313 ms\tMemory Size: 128 MB\t"
    "Max Memory Used: 121 MB\tInit Duration: 401.02 ms\n"
)

FAILED_TAIL = (
    f"START RequestId: {RID} Version: $LATEST\n"
    f"[ERROR] KeyError: 'customer_id'\rTraceback (most recent call last):\r  File \"/var/task/app.py\", line 2\n"
    f"END RequestId: {RID}\n"
    f"REPORT RequestId: {RID}\tDuration: 12.00 ms\tBilled Duration: 12 ms\tMemory Size: 128 MB\t"
    "Max Memory Used: 40 MB\n"
)

FAILURE = {"errorType": "KeyError", "errorMessage": "'customer_id'",
           "stackTrace": ['  File "/var/task/app.py", line 2, in handler\n    return event["customer_id"]\n']}


def _tail(text):
    return base64.b64encode(text.encode()).decode()


def invoking(answer, *, tail=OK_TAIL, error=None, calls=None):
    """
    clients={'lambda': ...} whose Invoke checks its request, records it in `calls` and answers with `answer`.
    """
    make = lambda_clients()

    def factory(region):
        client = make(region)
        model = client._client.meta.service_model.operation_model("Invoke")

        def invoke(**params):
            found = ParamValidator().validate(params, model.input_shape)
            assert not found.has_errors(), found.generate_report()
            if calls is not None:
                calls.append(params)
            reply = {"StatusCode": 204 if params.get("InvocationType") == "DryRun" else 200,
                     "Payload": io.BytesIO(json.dumps(answer).encode()), "ExecutedVersion": "$LATEST"}
            if params.get("LogType") == "Tail":
                reply["LogResult"] = _tail(tail)
            if error:
                reply["FunctionError"] = error
            found = ParamValidator().validate(reply, model.output_shape)
            assert not found.has_errors(), found.generate_report()
            headers = {"X-Amzn-Trace-Id": "Root=1-6743a1b2-0123456789abcdef01234567;Parent=53995c3f42cd8ad8;Sampled=1",
                       "X-Amz-Executed-Version": "$LATEST"}
            return {**reply, "ResponseMetadata": {"RequestId": RID, "HTTPStatusCode": 200, "HTTPHeaders": headers}}

        client._handlers["invoke"] = invoke
        return client

    return {"lambda": factory}


@pytest.fixture
def fn_role(role):  # noqa: F811
    create("orders-api", role, MemorySize=128, Timeout=3)
    return role


def analyzer(answer, **kwargs):
    return LambdaAnalyzer(region=REGION, clients=invoking(answer, **kwargs))



@pytest.mark.parametrize("event, sent", [
    (None, b"{}"),
    ({"order_id": 42}, b'{"order_id": 42}'),
    ('{"order_id": 42}', b'{"order_id": 42}'),
    ("{'order_id': 42, 'paid': True}", b'{"order_id": 42, "paid": true}'),
    ("hello", b'"hello"'),
    ([1, 2], b"[1, 2]"),
    (b"raw", b"raw"),
    ("   ", b"{}"),
])
def test_event_payload_reads_what_people_type(event, sent):
    assert event_payload(event) == sent


def test_event_payload_says_what_is_wrong():
    with pytest.raises(ValueError, match="looks like JSON but isn't"):
        event_payload("{order_id: }")
    with pytest.raises(ValueError, match="at most 6 MB"):
        event_payload("x" * (7 * 1024 * 1024))


def test_tail_lines_keep_their_order_and_times():
    started = datetime(2026, 11, 23, 10, 0, tzinfo=timezone.utc)
    events = tail_events(OK_TAIL, request_id=RID, started=started)
    assert [e.message.split()[0] for e in events] == ["START", "[INFO]", "END", "REPORT"]
    assert events[1].time == datetime(2026, 11, 23, 10, 0, 0, 120000, tzinfo=timezone.utc)
    assert all(a.time < b.time for a, b in zip(events, events[1:])) and {e.stream for e in events} == {TAIL_STREAM}
    assert tail_is_whole(OK_TAIL) and not tail_is_whole("…lines\nREPORT RequestId: x")
    assert decode_tail(_tail("hi")) == "hi" and decode_tail("not base64!") == "" and decode_tail(None) == ""


def test_a_tail_cut_short_marks_its_first_line():
    cut = "x" * 5000 + "\n" + OK_TAIL.split("\n", 1)[1]
    events = tail_events(cut[-4096:], request_id=RID, started=datetime.now(timezone.utc))
    assert events[0].message.startswith("…")


def test_answer_and_cost():
    assert read_answer(b'{"ok": true}') == {"ok": True} and read_answer(b"plain") == "plain"
    assert read_answer(b"") is None
    fn = Function("f", memory=1024)
    assert call_cost(fn, 1000, LAMBDA_PRICES) == pytest.approx(LAMBDA_PRICES["gb_second"] + 0.2 / 1_000_000)
    arm = Function("f", memory=1024, architecture="arm64")
    assert call_cost(arm, 1000, LAMBDA_PRICES) < call_cost(fn, 1000, LAMBDA_PRICES)
    assert call_cost(fn, None, LAMBDA_PRICES) is None


def _invoked(tail, *, error=None, answer=None, timeout=3):
    started = datetime(2026, 11, 23, 10, 0, tzinfo=timezone.utc)
    return Invoked(Function("orders-api", timeout=timeout, memory=128), b"{}", started=started, request_id=RID,
                   function_error=error, payload=json.dumps(answer).encode(), tail=tail,
                   events=tail_events(tail, request_id=RID, started=started))


def test_findings_say_what_to_change():
    ok = invoke_findings(_invoked(OK_TAIL, answer={"ok": True}))
    assert ok[0][0] == "warn" and "121 of its 128 MB" in ok[0][1] and "--memory-size 256" in ok[0][1]
    assert any("cold start" in message for _, message in ok)
    failed = invoke_findings(_invoked(FAILED_TAIL, error="Unhandled", answer=FAILURE))
    assert failed[0][0] == "warn" and failed[0][1].startswith("It failed with KeyError: 'customer_id'")
    assert "errors('orders-api')" in failed[0][1]
    timeout = {"errorType": "Sandbox.Timedout", "errorMessage": "Task timed out after 3.00 seconds"}
    timed = invoke_findings(_invoked(FAILED_TAIL, error="Unhandled", answer=timeout))
    assert "--timeout 13" in timed[0][1]



def test_invoke_sends_the_event_once_and_reads_the_tail(fn_role):
    calls = []
    core = analyzer({"statusCode": 200, "body": "saved"}, calls=calls)
    invoked = core.invoke("orders-api", {"order_id": 42})
    assert calls == [{"FunctionName": "orders-api", "Payload": b'{"order_id": 42}', "LogType": "Tail",
                      "InvocationType": "RequestResponse"}]
    assert invoked.ok and invoked.answer == {"statusCode": 200, "body": "saved"} and invoked.request_id == RID
    assert invoked.logs_from == "tail" and invoked.report.duration == pytest.approx(312.4)
    assert invoked.run.status == "ok" and invoked.cost == pytest.approx(call_cost(invoked.function, 313, core.prices))


def test_invoke_passes_an_alias(fn_role):
    boto3.client("lambda", region_name=REGION).create_alias(
        FunctionName="orders-api", Name="live", FunctionVersion="$LATEST")
    calls = []
    analyzer({}, calls=calls).invoke("orders-api:live")
    assert calls[0]["Qualifier"] == "live"


def test_a_dry_run_runs_nothing(fn_role):
    calls = []
    invoked = analyzer(None, calls=calls).invoke("orders-api", dry_run=True)
    assert calls[0]["InvocationType"] == "DryRun" and invoked.dry_run and invoked.events == []


def test_a_long_log_is_read_whole_from_cloudwatch(fn_role, monkeypatch):
    now = datetime.now(timezone.utc)
    lines = [(now, f"START RequestId: {RID} Version: $LATEST")]
    lines += [(now + timedelta(milliseconds=i + 1), f"row {i} of 400") for i in range(400)]
    lines += [(now + timedelta(seconds=1), f"END RequestId: {RID}"),
              (now + timedelta(seconds=1, milliseconds=1), f"REPORT RequestId: {RID}\tDuration: 1000.00 ms\t"
                                                            "Billed Duration: 1000 ms\tMemory Size: 128 MB\t"
                                                            "Max Memory Used: 60 MB")]
    put_logs("/aws/lambda/orders-api", lines)
    cut = "".join(f"{m}\n" for _, m in lines)[-4096:]
    invoked = analyzer({}, tail=cut).invoke("orders-api")
    assert invoked.logs_from == "cloudwatch" and len(invoked.run.events) == 403
    assert not any("4 KB" in message for _, message in invoke_findings(invoked))


def test_a_long_log_that_has_not_arrived_keeps_the_tail(fn_role, monkeypatch):
    monkeypatch.setattr(invoke_mod.time, "sleep", lambda seconds: None)
    cut = "".join(f"row {i}\n" for i in range(800))[-4096:] + f"REPORT RequestId: {RID}\tDuration: 5.00 ms\t" \
          "Billed Duration: 5 ms\tMemory Size: 128 MB\tMax Memory Used: 60 MB\n"
    invoked = analyzer({}, tail=cut).invoke("orders-api", wait_logs=0)
    assert invoked.logs_from == "tail" and invoked.events[0].message.startswith("…")
    assert any("4 KB" in message and f"request_id='{RID}'" in message for _, message in invoke_findings(invoked))


def test_invoke_never_retries():
    core = LambdaAnalyzer(region=REGION)
    client = core._invoke_client(REGION, 900)
    assert client.meta.config.retries["total_max_attempts"] == 1 and client.meta.config.read_timeout == 930



def test_the_report_puts_the_answer_and_logs_together(fn_role, capsys):
    ui = LambdaView(analyzer({"statusCode": 200}), mode="text", progress="off")
    out = run(capsys, ui.invoke, "orders-api", {"order_id": 42})
    assert "orders-api ran" in out and "312 ms of 3 s" in out and "121 of 128 MB" in out
    assert "statusCode" in out and "loading order 42" in out and "--memory-size 256" in out
    assert "invoke('orders-api', {'order_id': 42})" in out and f"logs('orders-api', request_id='{RID}')" in out


def test_the_report_shows_a_failure_as_a_traceback(fn_role, capsys):
    ui = LambdaView(analyzer(FAILURE, tail=FAILED_TAIL, error="Unhandled"), mode="text", progress="off")
    out = run(capsys, ui.invoke, "orders-api")
    assert "orders-api failed" in out and "KeyError: 'customer_id'" in out and 'File "/var/task/app.py"' in out
    assert "errors('orders-api')" in out


def test_the_report_draws_as_html(fn_role, capsys):
    ui = LambdaView(analyzer({"rows": list(range(3))}), mode="html", progress="off")
    blocks = invoke_block.invoke_blocks(ui.core.invoke("orders-api"), call=lambda *a, **k: "x()")
    page = ui._render_html(blocks, 50)
    assert "orders-api ran" in page and "loading order 42" in page


def test_a_missing_function_names_the_closest(fn_role, capsys):
    ui = LambdaView(analyzer({}), mode="text", progress="off")
    out = run(capsys, ui.invoke, "orders-apl")
    assert "Did you mean 'orders-api'" in out


def test_a_denied_invoke_is_a_note(fn_role, capsys):
    clients = invoking({})
    make = clients["lambda"]

    def denied(region):
        client = make(region)

        def invoke(**params):
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "not authorized to perform "
                                         "lambda:InvokeFunction"}}, "Invoke")
        client._handlers["invoke"] = invoke
        return client

    ui = LambdaView(LambdaAnalyzer(region=REGION, clients={"lambda": denied}), mode="text", progress="off")
    out = run(capsys, ui.invoke, "orders-api")
    assert "lambda:InvokeFunction" in out and "on top of the read-only ones" in out and "Traceback" not in out


def plain_text(value):
    return " ".join(re.sub(r"<[^>]+>", " ", html.unescape(value)).split())


def seed_old_run(event=None):
    now = datetime.now(timezone.utc) - timedelta(minutes=5)
    old = "0ld0ld00-0000-4000-8000-000000000000"
    lines = [(now, f"START RequestId: {old} Version: $LATEST")]
    if event is not None:
        lines.append((now + timedelta(milliseconds=1), json.dumps({"level": "INFO", "message": "got", "event": event})))
    lines += [(now + timedelta(milliseconds=5), f"END RequestId: {old}"),
              (now + timedelta(milliseconds=6), f"REPORT RequestId: {old}\tDuration: 5.00 ms\tBilled Duration: 5 ms\t"
                                                "Memory Size: 128 MB\tMax Memory Used: 40 MB")]
    put_logs("/aws/lambda/orders-api", lines)
    return old


def window(core):
    return LambdaExplorer("orders-api", tab="logs", core=core, mode="widgets")


def test_explorer_invoke_puts_your_run_first_and_opened(fn_role):
    old = seed_old_run()
    calls = []
    x = window(analyzer({"statusCode": 200, "body": {"saved": 42}}, calls=calls))
    assert [run.request_id for run in x._runs] == [old] and x.invoke_panel.layout.display == "none"
    x.invoke_button.click()
    assert x.invoke_panel.layout.display == "" and x.event_box.value == "{}"
    assert "Invoke orders-api" in plain_text(x.invoke_title.value)
    x.event_box.value = "{'order_id': 42}"
    assert "read as a Python dict, sent as JSON" in plain_text(x.event_said.value) and not x.run_button.disabled
    x.run_button.click()
    assert calls[0]["Payload"] == b'{"order_id": 42}' and calls[0]["InvocationType"] == "RequestResponse"
    assert [run.request_id for run in x._visible_runs()] == [RID, old]
    first = x._run_rows[0]
    assert "you ran" in plain_text(first.face.value) and "lmx-mine" in first.box._dom_classes
    body = plain_text(first.body.value)
    assert first.body.layout.display == "" and "Sent the event" in body and '"saved" : 42' in body
    assert "loading order 42" in body and "121 of its 128 MB" in body
    assert [c.description for c in x.run_chips.children][:2] == ["All runs 2", "You ran 1"]
    assert plain_text(x.status.value) == "orders-api ran in 312 ms · your run is first below, opened"
    assert first.invoke_button.layout.display == ""
    x.event_box.value = "{}"
    first.invoke_button.click()
    assert json.loads(x.event_box.value) == {"order_id": 42}
    x._toggle_invoke(False)
    x.invoke_button.click()
    assert json.loads(x.event_box.value) == {"order_id": 42}


def test_explorer_invoke_keeps_your_run_through_a_reread(fn_role):
    seed_old_run()
    x = window(analyzer({}))
    x.invoke_button.click()
    x.run_button.click()
    x._reload_logs()
    assert x._runs[0].request_id == RID and "you ran" in plain_text(x._run_rows[0].face.value)


def test_explorer_invoke_refuses_an_event_it_cannot_read(fn_role):
    seed_old_run()
    x = window(analyzer({}))
    x.invoke_button.click()
    x.event_box.value = "{order_id: }"
    assert x.run_button.disabled and "looks like JSON but isn't" in plain_text(x.event_said.value)


def test_explorer_invoke_shows_a_failure(fn_role):
    seed_old_run()
    x = window(analyzer(FAILURE, tail=FAILED_TAIL, error="Unhandled"))
    x.invoke_button.click()
    x.run_button.click()
    assert plain_text(x.status.value).startswith("orders-api failed with KeyError in 12 ms")
    body = plain_text(x._run_rows[0].body.value)
    assert "Returned KeyError" in body and "It failed with KeyError" in body


def test_explorer_invoke_with_a_logged_event(fn_role):
    seed_old_run({"order_id": 7, "customer_id": "c-1"})
    x = window(analyzer({}))
    x.invoke_button.click()
    assert json.loads(x.event_box.value) == {"order_id": 7, "customer_id": "c-1"}
    x._toggle_invoke(False)
    row = x._run_rows[0]
    row.button.click()
    assert row.invoke_button.layout.display == ""
    row.invoke_button.click()
    assert x.invoke_panel.layout.display == "" and json.loads(x.event_box.value)["order_id"] == 7
    assert "is in the box above" in plain_text(x.status.value)


def test_explorer_dry_run_and_denied(fn_role):
    seed_old_run()
    calls = []
    x = window(analyzer({}, calls=calls))
    x.invoke_button.click()
    next(b for b in x.invoke_panel.children[2].children if getattr(b, "description", "") == "Dry run").click()
    assert calls[0]["InvocationType"] == "DryRun" and "nothing ran" in plain_text(x.status.value)
    assert len(x._runs) == 1

    def denied(region):
        client = invoking({})["lambda"](region)

        def invoke(**params):
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "not authorized to perform "
                                         "lambda:InvokeFunction"}}, "Invoke")
        client._handlers["invoke"] = invoke
        return client

    y = window(LambdaAnalyzer(region=REGION, clients={"lambda": denied}))
    y.invoke_button.click()
    y.run_button.click()
    assert "lambda:InvokeFunction" in plain_text(y.event_said.value) and not y.run_button.disabled


def test_explorer_invoke_shows_your_run_during_a_cloudwatch_search(fn_role):
    seed_old_run()
    x = window(analyzer({}))
    x.log_find.value = "nothing logged this"
    x._log_search()
    assert x.found is not None
    x.invoke_button.click()
    x.run_button.click()
    assert x.found is None and x.log_find.value == ""
    assert [run.request_id for run in x._visible_runs()][:1] == [RID] and len(x._visible_runs()) == 2
    assert plain_text(x.status.value).endswith("the search was cleared to show it")


def test_check_event_says_where_json_breaks():
    good = check_event('{"order_id": 42, "items": [1, 2]}')
    assert good.ok and good.message.startswith("Valid JSON") and json.loads(good.pretty) == {"order_id": 42,
                                                                                            "items": [1, 2]}
    assert good.pretty.startswith('{\n  "order_id": 42')
    python = check_event("{'paid': True}")
    assert python.ok and "Python dict" in python.message and json.loads(python.pretty) == {"paid": True}
    broken = check_event('{\n  "order_id": 42,\n  "x": \n}')
    assert not broken.ok and broken.line == 4 and broken.column == 1 and "line 4, column 1" in broken.message
    assert check_event("").ok and check_event("").pretty == "{}"
    assert format_event('{"b":1,"a":[]}') == '{\n  "b": 1,\n  "a": []\n}' and format_event("{oops") == "{oops"


def test_samples_are_events():
    assert {sample.name for sample in EVENT_SAMPLES} >= {"Empty", "S3 upload", "SQS message", "API Gateway (HTTP API)"}
    assert json.loads(sample_event("S3 upload"))["Records"][0]["eventSource"] == "aws:s3"
    with pytest.raises(KeyError):
        sample_event("nothing")


XRAY_TAIL = OK_TAIL.replace("Init Duration: 401.02 ms\n", "Init Duration: 401.02 ms\t\nXRAY TraceId: "
                            "1-6743a1b2-aaaa SegmentId: 1a2b3c Sampled: true\n")
EXTENSION_TAIL = f"EXTENSION\tName: datadog-agent\tState: Ready\tEvents: [INVOKE, SHUTDOWN]\n{XRAY_TAIL}"


def test_extras_say_everything_else_lambda_returned():
    invoked = _invoked(EXTENSION_TAIL, answer={"ok": True})
    invoked.executed_version, invoked.status_code = "7", 200
    invoked.headers = {"x-amzn-trace-id": "Root=1-ffff;Parent=0;Sampled=0", "content-type": "application/json"}
    extras = dict(invoke_extras(invoked))
    assert extras["Request ID"] == RID and extras["Version that ran"] == "7" and extras["HTTP status"] == "200 OK"
    assert extras["X-Ray trace"] == "1-6743a1b2-aaaa · sampled: true"
    assert extras["Billed duration"] == "313 ms" and extras["Init duration (cold start)"] == "401 ms"
    assert extras["Log lines"].startswith("1 from its code, ") and extras["Content type"] == "application/json"
    assert [e.message.split()[0] for e in lambda_lines(invoked)] == ["EXTENSION", "START", "END", "REPORT", "XRAY"]
    assert [e.message for e in code_lines(invoked)] == [OK_TAIL.split("\n")[1]]
    header_only = _invoked(OK_TAIL, answer={})
    header_only.headers = {"x-amzn-trace-id": "Root=1-ffff;Parent=0;Sampled=0"}
    assert xray_trace(header_only).trace_id == "1-ffff" and xray_trace(header_only).sampled == "false"


def test_the_report_shows_everything_else(fn_role, capsys):
    ui = LambdaView(analyzer({"ok": True}, tail=EXTENSION_TAIL), mode="text", progress="off")
    out = run(capsys, ui.invoke, "orders-api", {"order_id": 42})
    assert "Everything else Lambda returned" in out and "1-6743a1b2-aaaa" in out and "X-Ray trace header" in out


def open_test_tab(core):
    return LambdaExplorer("orders-api", tab="test", core=core, mode="widgets")


def test_the_test_tab_checks_formats_and_runs(fn_role):
    old = seed_old_run()
    calls = []
    x = open_test_tab(analyzer({"statusCode": 200, "body": {"saved": 42}}, calls=calls))
    assert x._tab == "test" and x.test_box.value == "{}" and "Test orders-api" in plain_text(x.test_head.value)
    x.test_box.value = '{\n  "order_id": 42,\n  "x": \n}'
    assert x.test_run_button.disabled and "line 4, column 1" in plain_text(x.test_said.value)
    assert '<tr class="bad"><td class="ln">4</td>' in x.test_preview.value
    x.test_box.value = '{"order_id":42,"items":[{"sku":"A-1"}]}'
    assert not x.test_run_button.disabled and '<span class="jk">' in x.test_preview.value
    next(b for b in x.pages["test"].children[-1].children[0].children[1].children
         if getattr(b, "description", "") == "{ } Format").click()
    assert x.test_box.value.startswith('{\n  "order_id": 42,\n  "items": [')
    x.sample_pick.value = "SQS message"
    assert json.loads(x.test_box.value)["Records"][0]["eventSource"] == "aws:sqs" and x.sample_pick.value == ""
    x.test_box.value = '{"order_id": 42}'
    x.test_run_button.click()
    assert calls[0]["Payload"] == b'{"order_id": 42}' and calls[0]["InvocationType"] == "RequestResponse"
    result = plain_text(x.test_result.value)
    assert "orders-api ran" in result and "Everything else Lambda returned" in result and '"saved"' in result
    assert "loading order 42" in result and "1-6743a1b2-0123456789abcdef01234567" in result
    assert plain_text(x.status.value) == "orders-api ran in 312 ms · what it returned and logged is below"
    assert x.test_runs_pick.layout.display == "" and x.test_logs_button.layout.display == ""
    x.test_logs_button.click()
    assert x._tab == "logs" and [run.request_id for run in x._visible_runs()] == [RID, old]
    assert "you ran" in plain_text(x._run_rows[0].face.value)


def test_the_test_tab_keeps_earlier_runs_and_the_box(fn_role):
    seed_old_run()
    x = open_test_tab(analyzer(FAILURE, tail=FAILED_TAIL, error="Unhandled"))
    x.test_box.value = '{"order_id": 1}'
    x.test_run_button.click()
    assert plain_text(x.status.value).startswith("orders-api failed with KeyError in 12 ms")
    assert "KeyError" in plain_text(x.test_result.value)
    x.test_box.value = '{"order_id": 2}'
    x.test_run_button.click()
    assert [label for label, _ in x.test_runs_pick.options][0].startswith("Run 2 · ✕ failed")
    x.test_runs_pick.value = 0
    assert json.loads(x.test_box.value) == {"order_id": 1}
    x._show_tab("overview")
    x._show_tab("test")
    assert json.loads(x.test_box.value) == {"order_id": 1}


def test_x_test_puts_an_event_in_the_box(fn_role):
    seed_old_run()
    calls = []
    x = LambdaExplorer("orders-api", core=analyzer({}, calls=calls), mode="widgets")
    x.test(event={"order_id": 9, "lines": [1]})
    assert x._tab == "test" and json.loads(x.test_box.value) == {"order_id": 9, "lines": [1]}
    assert x.test_box.value.startswith('{\n  "order_id": 9') and calls == []
    x.test(event='{"pasted": true}')
    assert json.loads(x.test_box.value) == {"pasted": True}
