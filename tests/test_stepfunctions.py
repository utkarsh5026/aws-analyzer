import asyncio
import html as html_lib
import json
import re
import threading
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from fake_stepfunctions import (
    FAILED_RUN, ORDER_PIPELINE, RESIZE_FAILED_RUN, express_arn, express_log_lines, express_runs, REFUND_APPROVALS, FakeCloudWatch, FakeStepFunctions, History, Try, clients,
    lambda_lines, machine_arn, run_arn, scene, story_logs,
)
from moto import mock_aws
from test_lambda_functions import create, lambda_clients, put_logs

from aws_analyzer import stepfunctions as sfnmod
from aws_analyzer._kit.errors import _Hint
from aws_analyzer._kit.view import _commands
from aws_analyzer.lambda_functions import LambdaAnalyzer
from aws_analyzer.stepfunctions import (
    STEPFUNCTIONS_PRICES, Execution, ExecutionDetail, MachineNumbers, StepFunctionsAnalyzer, StepFunctionsView,
    can_redrive, definition_findings, fill_detail, group_failures, lambda_name, logging_findings, machine_name,
    mermaid, monthly_cost, parse_definition, parse_execution_ref, parse_state_machine, request_id_of_cause,
    request_id_of_output, short_cause, transitions_per_execution, unbounded_waits, unretried_lambda_states,
)
from aws_analyzer.stepfunctions.analysis.express import (
    LoggedRun, arn_pattern, endings_pattern, execution_of, group_log_lines, log_group_name, parse_log_line,
)
from aws_analyzer.stepfunctions.analysis.layout import failure_marks, flow_layout, path_marks
from aws_analyzer.stepfunctions.analysis.story import LogLine, merge_log, step_lines, waterfall
from aws_analyzer.stepfunctions.explorer.frame import _window_text as window_text
from aws_analyzer.stepfunctions.explorer.window import StepFunctionsExplorer


NOW = datetime.now(timezone.utc).replace(microsecond=0)


def described(name, definition, **extra):
    return {"stateMachineArn": machine_arn(name), "name": name, "definition": definition, "type": "STANDARD",
            "creationDate": NOW, **extra}


def order_pipeline():
    return parse_state_machine(described("order-pipeline", json.dumps(ORDER_PIPELINE)))


def detail_of(name):
    machines, runs, _ = scene(NOW)
    run = next(r for r in runs if r.name == name)
    execution = sfnmod.parse_execution(run.item(), run.machine)
    return fill_detail(ExecutionDetail(execution, machine=order_pipeline()), run.history.events)


def test_execution_references():
    arn = run_arn("order-pipeline", "ord-7f3a")
    assert parse_execution_ref(arn) == (None, arn)
    assert parse_execution_ref("order-pipeline/ord-7f3a") == ("order-pipeline", "ord-7f3a")
    assert parse_execution_ref(" ord-7f3a ") == (None, "ord-7f3a")
    link = f"https://us-east-1.console.aws.amazon.com/states/home?region=us-east-1#/v2/executions/details/{arn}"
    assert parse_execution_ref(link) == (None, arn)
    with pytest.raises(ValueError):
        parse_execution_ref("  ")


def test_names_from_arns_and_causes():
    assert machine_name(machine_arn("order-pipeline")) == "order-pipeline"
    assert lambda_name("arn:aws:lambda:us-east-1:1:function:orders-etl:$LATEST") == "orders-etl"
    assert lambda_name("orders-etl:prod") == "orders-etl"
    payload = '{"errorType": "KeyError", "errorMessage": "\'customer_id\'", "requestId": "r1"}'
    assert short_cause(payload) == "KeyError: 'customer_id'"
    assert short_cause("Rate   Exceeded.\n(Service: AWSLambda; Request ID: x)") == "Rate Exceeded."
    assert request_id_of_cause(payload) == "r1" and request_id_of_cause("Rate Exceeded. Request ID: x") == ""
    assert request_id_of_output('{"SdkResponseMetadata": {"RequestId": "r2"}}') == "r2"
    assert request_id_of_output("not json") == ""


def test_parse_definition_reads_states_in_order_with_nesting():
    start, states = parse_definition(ORDER_PIPELINE)
    assert start == "ValidateOrder"
    names = [s.name for s in states]
    assert names.index("ResizeImage") == names.index("ProcessItems") + 1
    inner = states[names.index("ResizeImage")]
    assert inner.depth == 1 and inner.path == "ProcessItems > ResizeImage" and inner.function == "resize-image"
    choice = states[names.index("IsPriority")]
    assert choice.next == ["BackorderWait", "ReserveStock"]
    load = states[names.index("LoadOrders")]
    assert load.next == ["ProcessItems", "OrderFailed"] and load.catches
    assert parse_definition("not json") == ("", [])


def test_state_machine_and_what_its_definition_risks():
    machine = order_pipeline()
    assert machine.functions == ["validate-order", "charge-card", "orders-etl", "resize-image"]
    assert [s.name for s in unretried_lambda_states(machine)] == ["LoadOrders"]
    findings = definition_findings(machine)
    assert len(findings) == 1 and findings[0][0] == "info" and "LoadOrders calls Lambda" in findings[0][1]
    approvals = parse_state_machine(described("refund-approvals", REFUND_APPROVALS))
    assert [s.name for s in unbounded_waits(approvals)] == ["AskManager"]
    assert definition_findings(approvals)[0][0] == "warn"
    flow = mermaid(machine)
    assert "s_LoadOrders -.->|error| s_OrderFailed" in flow and "s_IsPriority{" in flow
    assert "s_NotifyCustomer --> stop((end))" in flow and "ResizeImage" not in flow


def test_logging_findings_for_express_only():
    express = parse_state_machine(described("x", "{}", type="EXPRESS"))
    assert logging_findings(express)[0][0] == "warn"
    express.log_level = "ERROR"
    assert logging_findings(express)[0][0] == "info"
    express.log_level = "ALL"
    assert logging_findings(express) == []
    assert logging_findings(order_pipeline()) == []


def test_cost_estimates():
    machine = order_pipeline()
    assert transitions_per_execution(machine) == 9
    numbers = MachineNumbers(days=7, started=700, avg_ms=2_000)
    assert monthly_cost(machine, numbers, STEPFUNCTIONS_PRICES) == pytest.approx(3000 * 9 * 0.000025)
    assert monthly_cost(machine, numbers, STEPFUNCTIONS_PRICES, transitions=20) == pytest.approx(3000 * 20 * 0.000025)
    machine.type = "EXPRESS"
    express = 3000 * (0.000001 + 2 * 0.0625 * 0.00001667)
    assert monthly_cost(machine, numbers, STEPFUNCTIONS_PRICES) == pytest.approx(express)
    assert monthly_cost(machine, None, STEPFUNCTIONS_PRICES) is None


def test_history_of_a_failed_execution():
    detail = detail_of("ord-7f3a")
    names = [s.name for s in detail.steps]
    assert names == ["ValidateOrder", "IsPriority", "ReserveStock", "ChargeCard", "LoadOrders", "OrderFailed"]
    charge = detail.step("ChargeCard")
    assert charge.retries == 2 and charge.status == "ok" and charge.attempts[0].request_id == ""
    step = detail.failed_step
    assert step.name == "LoadOrders" and step.function == "orders-etl" and step.request_id == FAILED_RUN
    assert step.error == "KeyError" and step.seconds == pytest.approx(1.75, abs=0.05)
    assert detail.execution.error == "OrderFailed"
    assert detail.step("OrderFailed").status == "ended"


def test_history_with_a_map_and_a_timeout():
    good = detail_of("ord-25a8")
    resized = [s for s in good.steps if s.name == "ResizeImage"]
    assert len(resized) == 3 and {s.depth for s in resized} == {1}
    assert good.step("ProcessItems").items == 3 and good.step("NotifyCustomer").depth == 0
    assert good.failed_step is None
    stuck = detail_of("ord-0b17")
    assert stuck.failed_step.name == "ResizeImage" and stuck.failed_step.status == "running"
    throttled = detail_of("ord-77e0")
    assert throttled.failed_step.name == "ChargeCard" and throttled.failed_step.retries == 2
    assert throttled.failed_step.request_id == ""


def test_history_read_from_its_end_only():
    machines, runs, _ = scene(NOW)
    run = next(r for r in runs if r.name == "ord-7f3a")
    tail = list(reversed(run.history.events))[:8]
    detail = fill_detail(ExecutionDetail(sfnmod.parse_execution(run.item(), "order-pipeline")), tail)
    assert detail.failed_step.name == "LoadOrders" and detail.failed_step.request_id == FAILED_RUN


def test_group_failures_and_redrive():
    details = [detail_of(name) for name in ("ord-7f3a", "ord-5d21", "ord-0b17", "ord-77e0", "ord-3c9b")]
    groups = group_failures(details, NOW)
    assert [(g.state, g.error, g.count) for g in groups][0] == ("LoadOrders", "KeyError", 3)
    assert groups[0].request_id == FAILED_RUN and groups[0].redrivable == 3
    assert {g.error for g in groups} == {"KeyError", "States.Timeout", "Lambda.TooManyRequestsException"}
    old = Execution("a", "a", "m", "FAILED", stop=NOW - timedelta(days=20))
    assert not can_redrive(old, NOW)
    fresh = Execution("a", "a", "m", "FAILED", stop=NOW - timedelta(days=2))
    assert can_redrive(fresh, NOW) and not can_redrive(fresh, NOW, express=True)
    fresh.redrive_status = "NOT_REDRIVABLE"
    assert not can_redrive(fresh, NOW)


@pytest.fixture
def fakes():
    return clients(NOW)


@pytest.fixture
def core(fakes):
    core = StepFunctionsAnalyzer(region="us-east-1", clients=fakes)
    core.max_workers = 1
    return core


def test_overview_reads_numbers_for_every_machine(core, fakes):
    overview = core.overview()
    assert sorted(m.name for m in overview.machines) == ["image-resize-express", "kb-sync", "order-pipeline",
                                                         "refund-approvals"]
    numbers = overview.numbers[machine_arn("order-pipeline")]
    assert (numbers.started, numbers.failed, numbers.timed_out) == (1239, 42, 7)
    assert numbers.avg_ms == pytest.approx(38_200) and numbers.failure_rate == pytest.approx(49 / 1225)
    assert overview.numbers[machine_arn("kb-sync")].started == 0
    assert overview.metrics_read == 4 * 7
    assert len(fakes["cloudwatch"].called("get_metric_data")) == 1


def test_overview_without_cloudwatch(core, fakes):
    fakes["cloudwatch"].denied.add("get_metric_data")
    overview = core.overview()
    assert overview.errors == {"metrics": "AccessDeniedException"} and not overview.numbers


def test_state_machine_by_name_or_arn_and_close_names(core, fakes):
    assert core.state_machine("order-pipeline").start_at == "ValidateOrder"
    assert core.state_machine(machine_arn("order-pipeline")).name == "order-pipeline"
    assert len(fakes["stepfunctions"].called("describe_state_machine")) == 1
    with pytest.raises(_Hint, match="Did you mean 'order-pipeline'"):
        core.state_machine("order-pipelin")


def test_executions_paging_filters_and_limits(core, fakes):
    page = core.executions("order-pipeline", since="30d")
    assert [e.name for e in page.executions][:3] == ["ord-91c2", "ord-25a8", "ord-7f3a"]
    assert len(page.executions) == 10 and not page.truncated
    assert len(core.executions("order-pipeline", since="7d").executions) == 9
    failed = core.executions("order-pipeline", status="failed", since="30d")
    assert {e.status for e in failed.executions} == {"FAILED"}
    assert fakes["stepfunctions"].called("list_executions")[-1]["statusFilter"] == "FAILED"
    cut = core.executions("order-pipeline", limit=2)
    assert len(cut.executions) == 2 and cut.truncated
    with pytest.raises(ValueError, match="status= is one of"):
        core.executions("order-pipeline", status="broken")
    express = core.executions("image-resize-express", since="24h")
    assert express.source == "logs" and len(express.executions) == 4


def test_execution_by_reference(core):
    detail = core.execution("order-pipeline/ord-7f3a", logs=False)
    assert detail.failed_step.name == "LoadOrders" and detail.machine.name == "order-pipeline"
    assert detail.lambda_run is None and detail.lambda_note == ""
    assert core.execution("ord-7f3a", machine="order-pipeline", logs=False).execution.name == "ord-7f3a"
    with pytest.raises(_Hint, match="Which state machine"):
        core.execution("ord-7f3a")


def test_execution_history_stops_at_max_events(core):
    detail = core.execution("order-pipeline/ord-25a8", logs=False, max_events=10)
    assert detail.partial and detail.events == 10


def test_failures_read_only_the_end_of_each_history(core, fakes):
    report = core.failures("order-pipeline")
    assert report.read == 5 and report.groups[0].state == "LoadOrders" and report.groups[0].count == 3
    tails = fakes["stepfunctions"].called("get_execution_history")
    assert len(tails) == 5 and all(call["reverseOrder"] and call["maxResults"] == 200 for call in tails)
    assert report.numbers.started == 1239
    assert core.failures("order-pipeline", limit=2).truncated


@pytest.fixture
def lambda_logs():
    with mock_aws():
        role = boto3.client("iam").create_role(RoleName="etl", AssumeRolePolicyDocument="{}")["Role"]["Arn"]
        create("orders-etl", role)
        yield LambdaAnalyzer(region="us-east-1", clients={"lambda": lambda_clients()})


def test_a_failed_lambda_step_shows_its_run(fakes, lambda_logs):
    core = StepFunctionsAnalyzer(region="us-east-1", clients=fakes, lambda_core=lambda_logs)
    scheduled = core.execution("order-pipeline/ord-7f3a", logs=False).failed_step.attempts[-1].scheduled
    detail = core.execution("order-pipeline/ord-7f3a")
    assert detail.lambda_run is None and "orders-etl has no log group" in detail.lambda_note
    put_logs("/aws/lambda/orders-etl", lambda_lines(scheduled - timedelta(hours=1)), stream="other")
    assert "no run with that request ID" in core.execution("order-pipeline/ord-7f3a").lambda_note
    put_logs("/aws/lambda/orders-etl", lambda_lines(scheduled))
    detail = core.execution("order-pipeline/ord-7f3a")
    run = detail.lambda_run
    assert run.request_id == FAILED_RUN and run.status == "failed" and run.report.max_memory == 151
    assert detail.lambda_note == "found by its request ID"


def test_a_lambda_step_without_a_request_id_is_matched_by_time(fakes, lambda_logs):
    core = StepFunctionsAnalyzer(region="us-east-1", clients=fakes, lambda_core=lambda_logs)
    step = core.execution("order-pipeline/ord-7f3a", logs=False).failed_step
    put_logs("/aws/lambda/orders-etl", lambda_lines(step.attempts[-1].scheduled + timedelta(seconds=0.1)))
    for attempt in step.attempts:
        attempt.request_id = ""
    run, note = core.lambda_run(step)
    assert run.request_id == FAILED_RUN and note.startswith("matched by time")


def test_a_lambda_step_whose_function_is_gone(fakes, lambda_logs):
    core = StepFunctionsAnalyzer(region="us-east-1", clients=fakes, lambda_core=lambda_logs)
    step = core.execution("order-pipeline/ord-7f3a", logs=False).failed_step
    step.function = "deleted-fn"
    run, note = core.lambda_run(step)
    assert run is None and note == "deleted-fn no longer exists in this region"


def run(capsys, fn, *args, **kwargs):
    fn(*args, **kwargs)
    return capsys.readouterr().out


@pytest.fixture
def ui(core):
    return StepFunctionsView(core, mode="text")


def test_help_lists_every_command(ui, capsys):
    assert set(_commands(StepFunctionsView)) == {n for names in StepFunctionsView._GROUPS.values() for n in names}
    out = run(capsys, ui.help)
    assert "failures(name, *, since='7d', limit=50)" in out and "Start here" in out


def test_overview_report(ui, capsys):
    out = run(capsys, ui.overview)
    for expected in ("Step Functions in us-east-1", "Executions · 7d: 183,659", "Need attention: 1 (!)",
                     "[!] 49 of 1,239 order-pipeline executions didn't succeed",
                     "failures('order-pipeline')",
                     "kb-sync was not started in the last 7 days", "Read 28 CloudWatch metrics"):
        assert expected in out, expected
    assert out.index("\norder-pipeline ") < out.index("\nkb-sync ")


def test_state_machine_and_graph_reports(ui, capsys):
    out = run(capsys, ui.state_machine, "order-pipeline")
    for expected in ("order-pipeline\n", "Standard workflow", "LoadOrders calls Lambda with no Retry",
                     "    ↳ ResizeImage", "States.ALL -> OrderFailed", "It calls 4 Lambda functions",
                     "9 state transitions", "Definition (Amazon States Language)", "executions('order-pipeline')"):
        assert expected in out, expected
    out = run(capsys, ui.graph, "order-pipeline")
    assert "How order-pipeline flows" in out and "flowchart TD" in out and "inside a Map or Parallel" in out


def test_executions_report(ui, capsys):
    out = run(capsys, ui.executions, "order-pipeline", since="30d")
    for expected in ("Executions of order-pipeline", "Shown: 10", "Didn't succeed: 6 (!)", "ord-7f3a",
                     "timed out", "execution('order-pipeline/ord-7f3a')"):
        assert expected in out, expected
    out = run(capsys, ui.executions, "order-pipeline", status="aborted")
    assert "No aborted executions" in out


def test_execution_report(ui, capsys):
    out = run(capsys, ui.execution, "order-pipeline/ord-7f3a", logs=False)
    for expected in ("Execution ord-7f3a", "Stopped at: LoadOrders (!)",
                     "LoadOrders (orders-etl) failed and ended the execution. It returned KeyError: 'customer_id'",
                     f"LambdaView().logs('orders-etl', request_id='{FAILED_RUN}')", "A Catch on LoadOrders sent it",
                     "aws stepfunctions redrive-execution", "ChargeCard took 3 tries", "failed: KeyError",
                     "The error LoadOrders returned", "The execution's input"):
        assert expected in out, expected
    assert "couldn't be shown" not in out and "What LoadOrders was given" not in out
    out = run(capsys, ui.execution, "order-pipeline/ord-25a8")
    assert "    ↳ ResizeImage" in out and "×3" in out and "Stopped at" not in out


def test_execution_report_with_the_lambda_run(fakes, lambda_logs, capsys):
    core = StepFunctionsAnalyzer(region="us-east-1", clients=fakes, lambda_core=lambda_logs)
    scheduled = core.execution("order-pipeline/ord-7f3a", logs=False).failed_step.attempts[-1].scheduled
    put_logs("/aws/lambda/orders-etl", lambda_lines(scheduled))
    out = run(capsys, StepFunctionsView(core, mode="text").execution, "order-pipeline/ord-7f3a")
    assert "orders-etl      reading s3://acme-uploads" in out and "REPORT: run 1.73 s" in out
    assert "Its Lambda run's lines are in the log below" in out
    assert "Not in the log: validate-order (validate-order no longer exists in this region)" in out


def test_failures_report(ui, capsys):
    out = run(capsys, ui.failures, "order-pipeline")
    for expected in ("Why order-pipeline fails", "Causes: 3", "Can be redriven: 5",
                     "LoadOrders fails with KeyError in orders-etl: 3 executions (60% of the failures read)",
                     "ResizeImage in resize-image was still running when 1 execution timed out",
                     "See one: execution('order-pipeline/ord-77e0')", "LoadOrders · λ orders-etl"):
        assert expected in out, expected


def test_reports_say_what_went_wrong_instead_of_a_traceback(ui, fakes, capsys):
    out = run(capsys, ui.state_machine, "orders")
    assert "No state machine named 'orders'" in out and "Traceback" not in out
    fakes["stepfunctions"].machines["kb-sync"]["type"] = "EXPRESS"
    out = run(capsys, ui.failures, "kb-sync")
    assert "Express workflow with logging off" in out and "Traceback" not in out
    out = run(capsys, ui.execution, "order-pipeline/nope")
    assert "ExecutionDoesNotExist" in out and "kept 90 days" in out
    fakes["stepfunctions"].denied.add("list_state_machines")
    out = run(capsys, ui.overview)
    assert "README lists the read-only IAM permissions" in out


def test_no_state_machines(capsys):
    empty = StepFunctionsAnalyzer(region="us-east-1", clients={"stepfunctions": FakeStepFunctions({}, []),
                                                              "cloudwatch": FakeCloudWatch({})})
    out = run(capsys, StepFunctionsView(empty, mode="text").overview)
    assert "No state machines in us-east-1" in out


def test_html_report_draws(core):
    view = StepFunctionsView(core, mode="html")
    shown = []
    view._show = shown.append
    view.failures("order-pipeline")
    html = sfnmod.view._render_html(shown[0], 50)
    assert "sfn" in html and "Why order-pipeline fails" in html


def test_history_builder_makes_valid_chains():
    history = History(NOW).lambda_task("A", "fn", [Try(0.1, request_id="r")]).succeed()
    ids = {e["id"] for e in history.events}
    assert all(e.get("previousEventId", 0) in ids | {0} for e in history.events)


def story_core(fakes, lambda_logs):
    """
    An analyzer whose Lambda steps' runs are in moto: validate-order, charge-card and orders-etl, with the lines
    they logged in ord-7f3a.
    """
    role = boto3.client("iam").create_role(RoleName="steps", AssumeRolePolicyDocument="{}")["Role"]["Arn"]
    for function in ("validate-order", "charge-card"):
        create(function, role)
    failed = next(r for r in fakes["stepfunctions"].runs if r.name == "ord-7f3a")
    for function, lines in story_logs(failed.history).items():
        put_logs(f"/aws/lambda/{function}", lines, stream=f"2026/11/01/[$LATEST]{function}")
    core = StepFunctionsAnalyzer(region="us-east-1", clients=fakes, lambda_core=lambda_logs)
    core.max_workers = 1
    return core


def test_waterfall_places_tries_and_back_off():
    chart = waterfall(detail_of("ord-7f3a"))
    names = [row.name for row in chart.rows]
    assert names == ["ValidateOrder", "IsPriority", "ReserveStock", "ChargeCard", "LoadOrders", "OrderFailed"]
    charge = chart.rows[3]
    assert [seg.kind for seg in charge.segments] == ["retry", "wait", "retry", "wait", "ok"]
    assert chart.rows[4].segments[0].kind == "failed" and chart.rows[4].segments[0].label == "KeyError"
    assert all(0 <= seg.start <= 1 and seg.start + seg.width <= 1.0001 for row in chart.rows for seg in row.segments)
    mapped = waterfall(detail_of("ord-25a8"))
    resize = next(row for row in mapped.rows if row.name == "ResizeImage")
    assert resize.depth == 1 and resize.count == 3 and len(resize.segments) == 3
    assert waterfall(ExecutionDetail(Execution("a", "a", "m", "RUNNING"))).rows == []


def test_step_lines_and_merging():
    lines = step_lines(detail_of("ord-7f3a"))
    texts = [line.text for line in lines]
    assert texts[0] == "Execution ord-7f3a started" and texts[-1] == "Execution failed: OrderFailed"
    assert "ChargeCard failed: Rate Exceeded, retried" in texts
    assert any(t.startswith("LoadOrders scheduled · orders-etl · request c0ffee00") for t in texts)
    late = LogLine(lines[1].time, "orders-etl", "hello")
    merged = merge_log([late], lines)
    assert merged.index(late) > merged.index(lines[1])


def test_execution_story_reads_every_lambda_step(fakes, lambda_logs):
    core = story_core(fakes, lambda_logs)
    detail = core.execution("order-pipeline/ord-7f3a", logs=False)
    story = core.execution_story(detail)
    assert [r.step.name for r in story.runs] == ["LoadOrders", "ValidateOrder", "ChargeCard"]
    assert all(r.run is not None for r in story.runs) and story.skipped == 0
    assert story.sources == ["step functions", "validate-order", "charge-card", "orders-etl"]
    texts = [line.text for line in story.lines]
    assert "validated order 88219: 3 items, total $184.20" in texts and "charged: ch_3Q8xk2" in texts
    assert texts.index("charged: ch_3Q8xk2") < next(i for i, t in enumerate(texts) if t.startswith("LoadOrders"))
    assert [line.kind for line in story.lines if line.source == "orders-etl"].count("error") == 1
    small = core.execution_story(detail, most=1)
    assert [r.step.name for r in small.runs] == ["LoadOrders"] and small.skipped == 2


def test_execution_report_draws_the_waterfall_and_the_log(fakes, lambda_logs, capsys):
    core = story_core(fakes, lambda_logs)
    out = run(capsys, StepFunctionsView(core, mode="text").execution, "order-pipeline/ord-7f3a")
    for expected in ("-- When each state ran --", "ChargeCard", "·······", "-- What happened, in order",
                     "validate-order  validated order 88219", "charge-card     charged: ch_3Q8xk2",
                     "Its Lambda run's lines are in the log below"):
        assert expected in out, expected
    view = StepFunctionsView(core, mode="html")
    shown = []
    view._show = shown.append
    view.execution("order-pipeline/ord-7f3a")
    html = sfnmod.view._render_html(shown[0], 50)
    assert 'class="wfs retry"' in html and 'class="src sf"' in html and "charged: ch_3Q8xk2" in html


def explorer(fakes, machine=None, **kwargs):
    core = StepFunctionsAnalyzer(region="us-east-1", clients=fakes)
    core.max_workers = 1
    return StepFunctionsExplorer(machine, core=core, mode="widgets", **kwargs)


def plain(value):
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", value)).split())


def test_window_wording():
    assert window_text("See where and why: failures('order-pipeline')") == "See where and why: the Failures tab"
    assert window_text("See one: execution('order-pipeline/ord-0b17')") == "See one: ord-0b17 in the Executions tab"
    assert window_text("$1 a month. state_machine('x') shows it.") == "$1 a month. The Overview tab shows it."
    assert window_text("Read it: LambdaView().logs('etl', request_id='x')") == (
        "Read it: LambdaView().logs('etl', request_id='x')")


def test_explorer_opens_the_failing_state_machine(fakes):
    x = explorer(fakes)
    assert x.machine.name == "order-pipeline" and x._tab == "machines"
    assert [m.name for m in x.field.choices] == ["image-resize-express", "kb-sync", "order-pipeline",
                                                 "refund-approvals"]
    assert "Executions · 7d 1,239 Didn't succeed 49 (4.0%)" in plain(x.stats.value)
    assert "order-pipeline" in plain(x.machines_box.children[1].children[1].value)
    assert len(x.page.executions) == 9 and len(x._visible) == 9
    assert "9 executions in the last 7 days · 5 didn't succeed" == plain(x.status.value)
    assert "sfx-alarm" in x.tab_buttons["executions"]._dom_classes
    assert "Executions of order-pipeline" in plain(x.execution_view.value)
    assert "How order-pipeline flows" in plain(x.definition_view.value)
    assert "LoadOrders calls Lambda with no Retry" in plain(x.overview_view.value)


def test_explorer_opens_an_execution_with_its_log(fakes, lambda_logs):
    core = story_core(fakes, lambda_logs)
    x = StepFunctionsExplorer("order-pipeline", core=core, mode="widgets")
    x.tab_buttons["executions"].click()
    row = next(line for line in x._rows if line.key.endswith(":ord-7f3a"))
    row.button.click()
    assert x.detail.execution.name == "ord-7f3a" and "sfx-on" in row.box._dom_classes
    page = plain(x.execution_view.value)
    assert "Execution ord-7f3a" in page and "When each state ran" in page and "charged: ch_3Q8xk2" in page
    assert "ord-7f3a: failed · 3 Lambda runs in its log" == plain(x.status.value)
    assert x.shown["execution"][0].text == "Execution ord-7f3a"
    x.execution("ord-25a8")
    assert x.detail.execution.name == "ord-25a8" and "×3" in plain(x.execution_view.value)


def test_explorer_filters_searches_and_reads_another_range(fakes):
    x = explorer(fakes, "order-pipeline")
    chips = {c.description.rsplit(" ", 1)[0]: c for c in x.chips.children}
    assert sorted(chips) == ["All", "Failed", "Running", "Succeeded", "Timed out"]
    chips["Failed"].click()
    assert {e.status for e in x._visible} == {"FAILED"} and len(x._visible) == 4
    x.chips.children[1].click()
    assert len(x._visible) == 9
    x.find.value = "ord-7"
    assert [e.name for e in x._visible] == ["ord-7f3a", "ord-77e0"]
    x.find.value = "nothing"
    assert "No execution matches" in plain(x.rows_box.children[0].value)
    x.find.value = ""
    x.since_pick.value = "30d"
    assert len(x.page.executions) == 10 and x.report is None


def test_explorer_failures_tab_opens_the_newest_of_each_group(fakes):
    x = explorer(fakes, "order-pipeline")
    x.tab_buttons["failures"].click()
    assert x.report is not None and "Why order-pipeline fails" in plain(x.failures_view.value)
    assert "ord-0b17 in the Executions tab" in plain(x.failures_view.value)
    labels = [b.description for b in x.failure_opens.children]
    assert labels[0] == "Open ord-7f3a (LoadOrders: KeyError)" and len(labels) == 3
    x.failure_opens.children[1].click()
    assert x._tab == "executions" and x.detail.execution.name == "ord-0b17"


def test_explorer_express_and_picking(fakes):
    x = explorer(fakes, "image-resize-express")
    assert x.machine.is_express and x.page.source == "logs" and len(x._visible) == 4
    x.field.open()
    x.field.search.value = "order"
    assert x.field.shown == [machine_arn("order-pipeline")]
    x.field._entered()
    assert x.machine.name == "order-pipeline" and not x.field.is_open
    x._machine_rows[2].button.click()
    assert x._tab == "overview" and x.machine.name == "kb-sync"
    x.open("order-pipelin")
    assert "No state machine named 'order-pipelin'" in plain(x.status.value)


def test_explorer_state_machines_search_chips_and_sorting(fakes):
    x = explorer(fakes)

    def names():
        return [line.machine.name for line in x.machines_shown]

    assert names() == ["order-pipeline", "image-resize-express", "kb-sync", "refund-approvals"]
    assert x.column_buttons["unsuccessful"].description == "Didn't succeed ▾"
    chips = {c.description.rsplit(" ", 1)[0]: c for c in x.machine_chips.children}
    assert sorted(chips) == ["All", "Express", "Failing", "Logging off", "Not run", "Standard"]
    assert chips["Failing"].description == "Failing 2" and chips["Logging off"].description == "Logging off 2"
    chips["Express"].click()
    assert names() == ["image-resize-express"] and "sfx-on" in x.machine_chips.children[2]._dom_classes
    assert plain(x.machine_count.value) == "1 of 4 state machines"
    x.machine_chips.children[2].click()
    assert len(names()) == 4 and plain(x.machine_count.value) == "4 state machines"

    x.column_buttons["name"].click()
    assert names() == ["image-resize-express", "kb-sync", "order-pipeline", "refund-approvals"]
    assert x.column_buttons["name"].description == "State machine ▴"
    assert x.column_buttons["unsuccessful"].description == "Didn't succeed"
    x.column_buttons["name"].click()
    assert names() == ["refund-approvals", "order-pipeline", "kb-sync", "image-resize-express"]
    x.column_buttons["started"].click()
    assert names()[0] == "image-resize-express" and names()[-1] == "kb-sync"
    x.column_buttons["logging"].click()
    assert [line.machine.log_level for line in x.machines_shown] == ["OFF", "OFF", "ERROR", "ALL"]

    x.machine_find.value = "charge-card"
    assert names() == ["order-pipeline"]
    assert "<mark>" not in x._machine_rows[0].face.value
    x.machine_find.value = "refund"
    assert names() == ["refund-approvals"] and "<mark>refund</mark>" in x._machine_rows[0].face.value
    x.machine_find.value = "nothing"
    assert names() == [] and "No state machine matches 'nothing'" in plain(x.machines_box.children[-1].value)
    x.machine_find.value = ""
    x._machine_rows[0].button.click()
    assert x._tab == "overview" and x.machine.name == x.machines_shown[0].machine.name


def test_explorer_starts_where_asked(fakes):
    x = explorer(fakes, "order-pipeline", execution="ord-0b17", tab="definition")
    assert x._tab == "executions" and x.detail.execution.name == "ord-0b17"
    with pytest.raises(ValueError, match="tab is one of"):
        explorer(fakes, tab="logs")
    fakes["stepfunctions"].denied.add("list_state_machines")
    blocked = explorer(fakes)
    assert "Couldn't list the state machines" in plain(blocked.status.value) and blocked.machine is None


def test_explorer_reads_in_the_background(fakes):
    history = fakes["stepfunctions"].holds["get_execution_history"] = threading.Event()

    async def settle(x, *keys):
        while any(key in x._tasks for key in keys or list(x._tasks)):
            await asyncio.gather(*[task for key, task in x._tasks.items() if not keys or key in keys])

    async def main():
        x = explorer(fakes, "order-pipeline")
        assert list(x._tasks) == ["list"] and x.machine is None
        await settle(x)
        first = next(line for line in x._rows if line.key.endswith(":ord-7f3a"))
        second = next(line for line in x._rows if line.key.endswith(":ord-25a8"))
        first.button.click()
        assert "execution" in x._tasks and "skw" in x.execution_view.value
        second.button.click()
        history.set()
        await settle(x)
        assert x.detail.execution.name == "ord-25a8"

    asyncio.run(main())


def test_explorer_without_widgets_shows_reports(fakes, capsys):
    core = StepFunctionsAnalyzer(region="us-east-1", clients=fakes)
    StepFunctionsExplorer("order-pipeline", core=core, mode="text")
    out = capsys.readouterr().out
    assert "order-pipeline" in out and "Its states" in out
    view = StepFunctionsView(core, mode="text")
    assert "needs Jupyter" in run(capsys, view.explore)
    html_view = StepFunctionsView(core, mode="html")
    html_view._show = lambda blocks: None
    html_view.explore("order-pipeline")
    assert html_view.explorer.machine.name == "order-pipeline"


def test_explorer_shows_its_window_once_per_cell(fakes, monkeypatch):
    import IPython.display

    from aws_analyzer._kit import window as kit_window

    shown, cell = [], [1]
    monkeypatch.setattr(kit_window, "_cell_number", lambda: cell[0])
    monkeypatch.setattr(IPython.display, "display", lambda obj: shown.append(obj))
    x = explorer(fakes, "order-pipeline")
    x._ipython_display_()
    assert shown == [x.root]
    cell[0] = 2
    x._ipython_display_()
    assert shown == [x.root, x.root]


def test_flow_layout_puts_states_in_layers_under_their_parents():
    layout = flow_layout(order_pipeline())
    layer = {n.name: n.layer for n in layout.nodes}
    assert layer["ValidateOrder"] == 0 and layer["IsPriority"] == 1
    assert layer["BackorderWait"] == layer["ReserveStock"] == 2 and layer["ChargeCard"] == 3
    assert layer["ProcessItems"] == layer["OrderFailed"] == 5 and layer["NotifyCustomer"] == 6
    process, notify = layout.node("ProcessItems"), layout.node("NotifyCustomer")
    assert process.inner == ["ResizeImage"] and process.height > layout.node("LoadOrders").height
    assert notify.x == process.x and notify.ends and not layout.node("OrderFailed").ends
    kinds = {(e.source, e.target): e.kind for e in layout.edges}
    assert kinds[("LoadOrders", "OrderFailed")] == "catch" and kinds[("IsPriority", "ReserveStock")] == "choice"
    boxes = [(n.x, n.y, n.x + n.width, n.y + n.height) for n in layout.nodes]
    assert all(not (a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3])
               for i, a in enumerate(boxes) for b in boxes[i + 1:])
    assert all(0 <= n.x and n.x + n.width <= layout.width for n in layout.nodes)


def test_flow_layout_draws_a_loop_beside_it():
    definition = {"StartAt": "Check", "States": {
        "Check": {"Type": "Task", "Resource": "arn:aws:states:::lambda:invoke",
                  "Parameters": {"FunctionName": "poll"}, "Next": "Done?"},
        "Done?": {"Type": "Choice", "Choices": [{"Variable": "$.done", "BooleanEquals": True, "Next": "Finish"}],
                  "Default": "Wait"},
        "Wait": {"Type": "Wait", "Seconds": 30, "Next": "Check"},
        "Finish": {"Type": "Succeed"}}}
    layout = flow_layout(parse_state_machine(described("poller", json.dumps(definition))))
    loops = [(e.source, e.target) for e in layout.edges if e.kind == "loop"]
    assert loops == [("Wait", "Check")] and layout.node("Wait").layer > layout.node("Check").layer


def test_path_and_failure_marks():
    layout = flow_layout(order_pipeline())
    marks = path_marks(layout, detail_of("ord-7f3a"))
    assert marks.states["LoadOrders"] == "failed" and marks.states["ChargeCard"] == "ok"
    assert marks.states["BackorderWait"] == "skip" and marks.states["OrderFailed"] == "failed"
    assert ("LoadOrders", "OrderFailed") in marks.taken and ("IsPriority", "BackorderWait") not in marks.taken
    assert marks.notes["ChargeCard"] == "4.00s · 3 tries"
    mapped = path_marks(layout, detail_of("ord-0b17"))
    assert mapped.states["ProcessItems"] == "running" and mapped.notes["ProcessItems"] == "never finished"
    report = sfnmod.FailureReport(order_pipeline(), NOW, failed=[detail_of(n).execution for n in
                                                                  ("ord-7f3a", "ord-5d21", "ord-3c9b", "ord-0b17")])
    report.groups = group_failures([detail_of(n) for n in ("ord-7f3a", "ord-5d21", "ord-3c9b", "ord-0b17")], NOW)
    heat = failure_marks(layout, report)
    assert heat.states == {"LoadOrders": "h3", "ProcessItems": "h2"}
    assert heat.notes["ProcessItems"] == "1 stopped here (25%)"


def test_graph_report_colours_a_path_or_failures(ui, capsys):
    out = run(capsys, ui.graph, "order-pipeline", execution="ord-7f3a")
    assert "The path ord-7f3a took" in out and "LoadOrders (Task) [failed]" in out
    assert "BackorderWait (Wait) [not reached]" in out and "OrderFailed (on error)" in out
    out = run(capsys, ui.graph, "order-pipeline", failures="7d")
    assert "Where 5 executions stopped" in out and "LoadOrders (Task) [most stopped here]" in out
    view = StepFunctionsView(ui.core, mode="html")
    shown = []
    view._show = shown.append
    view.graph("order-pipeline", execution="ord-7f3a")
    html = sfnmod.view._render_html(shown[0], 50)
    assert "<svg" in html and 'class="fn t-task m-failed"' in html and 'class="fe catch on"' in html
    assert "<script" not in html


def test_explorer_graph_tab(fakes):
    x = explorer(fakes, "order-pipeline")
    x.tab_buttons["graph"].click()
    assert "Open an execution in the Executions tab" in plain(x.graph_view.value)
    assert [line.key for line in x._state_lines][:3] == ["ValidateOrder", "IsPriority", "BackorderWait"]
    assert x.picked_state == "ValidateOrder" and "What the definition says" in plain(x.state_view.value)
    x.execution("ord-7f3a")
    x.tab_buttons["graph"].click()
    assert "The path ord-7f3a took" in plain(x.graph_view.value)
    line = next(line for line in x._state_lines if line.key == "ChargeCard")
    line.button.click()
    assert x.picked_state == "ChargeCard" and "sfx-on" in line.box._dom_classes
    inspector = plain(x.state_view.value)
    assert "Its tries in ord-7f3a" in inspector and "Lambda.TooManyRequestsException" in inspector
    x.mode_pick.value = "failures"
    assert x.report is not None and "Where 5 executions stopped" in plain(x.graph_view.value)
    next(line for line in x._state_lines if line.key == "LoadOrders").button.click()
    assert "The executions that stopped here" in plain(x.state_view.value)
    x.mode_pick.value = "plain"
    assert "The path" not in plain(x.graph_view.value) and "Where" not in plain(x.graph_view.value)
    x.open("image-resize-express")
    x.mode_pick.value = "failures"
    assert "Where 1 execution stopped" in plain(x.graph_view.value)


def test_log_lines_read_back_as_history_events():
    runs = express_runs(NOW)
    arn = express_arn("rsz-0099")
    lines = [message for _, message in express_log_lines(arn, runs[arn])]
    parsed = parse_log_line(lines[0])
    assert parsed[0] == arn and parsed[1]["type"] == "ExecutionStarted" and "previousEventId" not in parsed[1]
    entered = parse_log_line(next(m for m in lines if '"TaskStateEntered"' in m))[1]
    assert entered["stateEnteredEventDetails"]["name"] == "Resize"
    assert parse_log_line("START RequestId: x") is None and parse_log_line('{"type": "X"}') is None
    grouped = group_log_lines(lines + [m for _, m in express_log_lines(express_arn("rsz-0000"),
                                                                       runs[express_arn("rsz-0000")])])
    assert [r.arn for r in grouped] == [express_arn("rsz-0000"), arn]
    execution = execution_of(grouped[1], logs_everything=True)
    assert (execution.name, execution.machine, execution.status) == ("rsz-0099", "image-resize-express", "FAILED")
    assert execution.error == "Runtime.OutOfMemory" and execution.seconds > 2
    started_only = grouped[1].events[:2]
    assert execution_of(LoggedRun(arn, started_only), logs_everything=True).status == "RUNNING"
    assert execution_of(LoggedRun(arn, started_only), logs_everything=False).status == ""
    assert log_group_name("arn:aws:logs:us-east-1:1:log-group:/aws/states/resize:*") == "/aws/states/resize"
    assert endings_pattern(("FAILED", "TIMED_OUT")) == '{ $.type = "ExecutionFailed" || $.type = "ExecutionTimedOut" }'


def test_express_execution_from_its_log_group(core, fakes):
    detail = core.execution(express_arn("rsz-0099"), logs=False)
    assert detail.source == "logs" and detail.execution.status == "FAILED"
    step = detail.failed_step
    assert step.name == "Resize" and step.function == "resize-image" and step.request_id == RESIZE_FAILED_RUN
    searched = fakes["logs"].called("filter_log_events")
    assert all(call["filterPattern"] == arn_pattern(express_arn("rsz-0099")) for call in searched)
    started = detail.execution.start
    fakes["logs"].calls.clear()
    core.execution(express_arn("rsz-0099"), logs=False, started=started)
    window = fakes["logs"].called("filter_log_events")[0]
    assert window["endTime"] - window["startTime"] <= 7 * 60 * 1000
    with pytest.raises(_Hint, match="named by their ARN"):
        core.execution("image-resize-express/rsz-0099")
    with pytest.raises(_Hint, match="No lines for"):
        core.execution(express_arn("rsz-9999"), logs=False)


def test_express_failures_and_logging_gaps(core, fakes):
    report = core.failures("image-resize-express")
    assert report.source == "logs" and report.read == 1
    assert report.groups[0].error == "Runtime.OutOfMemory" and report.groups[0].redrivable == 0
    spec = fakes["stepfunctions"].machines["image-resize-express"]
    spec["logging"] = {**spec["logging"], "level": "ERROR", "includeExecutionData": False}
    group = fakes["logs"].groups["/aws/states/resize"]
    group[:] = [(moment, line) for moment, line in group if re.search(r'"type": "\w*(Failed|TimedOut)"', line)]
    fresh = StepFunctionsAnalyzer(region="us-east-1", clients=fakes)
    page = fresh.executions("image-resize-express", since="24h")
    assert "only executions that failed" in page.note and "inputs, outputs" in page.note
    assert [(e.name, e.status) for e in page.executions] == [("rsz-0099", "FAILED")]
    detail = fresh.execution(express_arn("rsz-0099"), logs=False)
    assert detail.failed_step is None and detail.execution.error == "Runtime.OutOfMemory"
    spec["logging"] = {"level": "OFF"}
    with pytest.raises(_Hint, match="logging off"):
        StepFunctionsAnalyzer(region="us-east-1", clients=fakes).executions("image-resize-express")


def test_express_reads_back_in_slices_and_stops_at_the_budget(core, fakes, monkeypatch):
    from aws_analyzer.stepfunctions import express as express_mod

    monkeypatch.setattr(express_mod, "_LINE_BUDGET", 5)
    page = core.executions("image-resize-express", since="24h")
    assert page.truncated and page.covered_from is not None
    assert [e.name for e in page.executions][0] == "rsz-0000"


def test_express_reports(ui, capsys):
    out = run(capsys, ui.executions, "image-resize-express", since="24h")
    assert "from its log group (/aws/states/resize)" in out and "rsz-0099" in out
    assert f"execution('{express_arn('rsz-0099')}')" in out
    out = run(capsys, ui.execution, express_arn("rsz-0099"), logs=False)
    assert "read from its log group" in out and "Resize (resize-image) failed and ended the execution" in out
    out = run(capsys, ui.failures, "image-resize-express")
    assert "read from its log group" in out and "Resize fails with Runtime.OutOfMemory" in out
    out = run(capsys, ui.overview)
    assert "See where and why: failures('image-resize-express')" in out


def test_explorer_opens_an_express_execution(fakes):
    x = explorer(fakes, "image-resize-express")
    line = next(line for line in x._rows if line.key == express_arn("rsz-0099"))
    line.button.click()
    assert x.detail.execution.name == "rsz-0099" and x.detail.source == "logs"
    assert "Execution rsz-0099" in plain(x.execution_view.value)
    window = fakes["logs"].called("filter_log_events")[-1]
    assert window["endTime"] - window["startTime"] <= 7 * 60 * 1000
    x.tab_buttons["failures"].click()
    assert "Why image-resize-express fails" in plain(x.failures_view.value)
