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
    invoke, lambda_error, lambda_lines, machine_arn, run_arn, scene, story_logs,
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
    LambdaSettings, resource_label, state_limit_findings, state_limits,
)
from aws_analyzer.stepfunctions.analysis.express import (
    LoggedRun, arn_pattern, endings_pattern, execution_of, group_log_lines, log_group_name, parse_log_line,
)
from aws_analyzer.stepfunctions.analysis.layout import (
    END, START, failure_marks, flow_layout, inner_failure_marks, inner_path_marks, inner_states, inner_timing_marks, owner_of,
    path_marks, timing_marks,
)
from aws_analyzer.stepfunctions.analysis.timing import state_timings, timing_findings, timing_report, took_text
from aws_analyzer.stepfunctions.explorer.graph import _execution_blocks as graph_execution_blocks
from aws_analyzer.stepfunctions.analysis.trend import daily_counts, failure_jump, trend_findings
from aws_analyzer.stepfunctions.analysis.suspects import (
    change_findings, change_verdict, gap_text, group_change_findings,
)
from aws_analyzer.stepfunctions.models import Change, FailureGroup, FailureReport
from aws_analyzer.stepfunctions.analysis.compare import (
    compare_executions, compare_inputs, comparison_findings,
)
from aws_analyzer.stepfunctions.comparing import _newest_before
from aws_analyzer.stepfunctions.analysis.retries import (
    advice_finding, error_kind, matching_retry, retry_advice, roomier_rule,
)
from aws_analyzer.stepfunctions.models import StateDef
from aws_analyzer.stepfunctions.analysis.story import LogLine, merge_log, step_lines, waterfall
from aws_analyzer.stepfunctions.explorer import frame as sfx_frame
from aws_analyzer.stepfunctions.explorer.frame import _window_text as window_text
from aws_analyzer.stepfunctions.explorer.window import StepFunctionsExplorer
from aws_analyzer.stepfunctions.helpers import console_url
from aws_analyzer import set_theme
from aws_analyzer._kit.themes import _theme_css
from test_lambda_functions import html_widgets


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


def test_explorer_failures_tab_lists_each_kind_of_failure(fakes):
    """
    The Failures tab is a list: every failure at once first (the report's summary and days, without the table the
    list replaces), then a line per kind, the most common first; a kind opens its error, what to read, its redrive
    commands and its executions, each a line into the Executions tab.
    """
    x = explorer(fakes, "order-pipeline")
    x.tab_buttons["failures"].click()
    assert x.report is not None and x.picked_failure == ""
    summary = plain(x.failures_view.value)
    assert "Why order-pipeline fails" in summary and "Executions a day" in summary
    assert "Failures by where they stopped" not in summary
    lines = [line for line in x._failure_lines if line.box in x.failure_list.children]
    assert [line.key for line in lines] == ["", "LoadOrders · KeyError", "ResizeImage · States.Timeout",
                                            "ChargeCard · Lambda.TooManyRequestsException"]
    assert "All failures" in plain(lines[0].face.value) and "×3" in plain(lines[1].face.value)
    assert "since 3d ago" in plain(lines[1].face.value)
    lines[1].button.click()
    assert x.picked_failure == "LoadOrders · KeyError" and "sfx-on" in lines[1].box._dom_classes
    group = plain(x.failures_view.value)
    assert "LoadOrders fails with KeyError" in group and "The error it returned" in group
    assert "LambdaView().logs('orders-etl'" in group and "aws stepfunctions redrive-execution" in group
    runs = [line for line in x._failure_runs if line.box in x.failure_runs_box.children]
    assert [line.key.rsplit(":", 1)[-1] for line in runs] == ["ord-7f3a", "ord-5d21", "ord-3c9b"]
    assert "3 executions that failed this way" in plain(x.failure_runs_head.value)
    assert x.focus.value("failed_state") == "LoadOrders" and x.focus.value("error") == "KeyError"
    runs[1].button.click()
    assert x._tab == "executions" and x.detail.execution.name == "ord-5d21"
    x.tab_buttons["failures"].click()
    assert x.picked_failure == "LoadOrders · KeyError"
    lines[0].button.click()
    assert x.picked_failure == "" and not x.failure_runs_box.children and not x.focus.value("error")


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
    x.column_buttons["trend"].click()
    assert names()[0] == "image-resize-express"
    face = x._machine_rows[0].face.value
    assert face.count('<span class="tr">') == 1 and face.count("<i ") + face.count('<i class="z">') == 7
    assert "<b style=" in face and ".sfx-app .tr i b{" in sfx_frame._EXPLORER_CSS

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
    layer = {n.name: n.layer for n in layout.top}
    assert [p.type for p in layout.pills] == ["Start", "End"] and layout.node(START).layer == 0
    assert layer["ValidateOrder"] == 1 and layer["IsPriority"] == 2
    assert layer["BackorderWait"] == layer["ReserveStock"] == 3 and layer["ChargeCard"] == 4
    assert layer["ProcessItems"] == layer["OrderFailed"] == 6 and layer["NotifyCustomer"] == 7
    assert layout.node(END).layer == 8
    process, notify, resize = layout.node("ProcessItems"), layout.node("NotifyCustomer"), layout.node("ResizeImage")
    assert process.inner == ["ResizeImage"] and process.height > layout.node("LoadOrders").height
    assert resize.parent == "ProcessItems" and resize.depth == 1 and resize.kind == "Lambda: Invoke"
    assert process.x < resize.x and resize.right < process.right and process.y < resize.y < process.bottom
    assert notify.middle == process.middle and notify.ends and not layout.node("OrderFailed").ends
    assert layout.node("ReserveStock").kind == "DynamoDB: UpdateItem" and layout.node("IsPriority").icon == "choice"
    edges = {(e.source, e.target): e for e in layout.edges}
    assert edges[("LoadOrders", "OrderFailed")].kind == "catch" and edges[("LoadOrders", "OrderFailed")].label == "any error"
    assert edges[("IsPriority", "ReserveStock")].label == "Default"
    assert edges[("IsPriority", "BackorderWait")].label == "$.priority = true"
    assert (START, "ValidateOrder") in edges and {("NotifyCustomer", END), ("OrderFailed", END)} <= set(edges)
    for edge in layout.edges:
        corners = edge.points
        assert all(a[0] == b[0] or a[1] == b[1] for a, b in zip(corners, corners[1:]))
    boxes = [(n.x, n.y, n.right, n.bottom) for n in layout.top + layout.pills]
    assert all(not (a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3])
               for i, a in enumerate(boxes) for b in boxes[i + 1:])
    assert all(0 <= n.x and n.right <= layout.width and n.bottom <= layout.height for n in layout.nodes)


def test_flow_layout_draws_a_parallel_s_branches_side_by_side():
    branch = {"Type": "Task", "Resource": "arn:aws:states:::sns:publish", "End": True}
    definition = {"StartAt": "Fan", "States": {"Fan": {"Type": "Parallel", "End": True, "Branches": [
        {"StartAt": "A", "States": {"A": {**branch, "End": False, "Next": "A2"}, "A2": branch}},
        {"StartAt": "B", "States": {"B": branch}}]}}}
    layout = flow_layout(parse_state_machine(described("fan", json.dumps(definition))))
    fan, a, a2, b = (layout.node(n) for n in ("Fan", "A", "A2", "B"))
    assert (a.branch, a2.branch, b.branch) == (0, 0, 1) and a.right < b.x and a.y == b.y < a2.y
    assert len(fan.dividers) == 1 and a.right < fan.dividers[0] < b.x
    assert [(e.source, e.target) for e in layout.edges if e.source in ("A", "B")] == [("A", "A2")]


def test_flow_layout_draws_a_loop_beside_it():
    definition = {"StartAt": "Check", "States": {
        "Check": {"Type": "Task", "Resource": "arn:aws:states:::lambda:invoke",
                  "Parameters": {"FunctionName": "poll"}, "Next": "Done?"},
        "Done?": {"Type": "Choice", "Choices": [{"Variable": "$.done", "BooleanEquals": True, "Next": "Finish"}],
                  "Default": "Wait"},
        "Wait": {"Type": "Wait", "Seconds": 30, "Next": "Check"},
        "Finish": {"Type": "Succeed"}}}
    layout = flow_layout(parse_state_machine(described("poller", json.dumps(definition))))
    loops = [e for e in layout.edges if e.kind == "loop"]
    assert [(e.source, e.target) for e in loops] == [("Wait", "Check")]
    assert layout.node("Wait").layer > layout.node("Check").layer
    lane = loops[0].points[2][0]
    assert all(lane < n.x or lane > n.right for n in layout.nodes) and loops[0].points[-1][1] == layout.node("Check").y


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
    assert heat.states == {"LoadOrders": "h3", "ProcessItems": "h2", "ResizeImage": "h2"}
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
    assert "<svg" in html and 'class="fn t-task m-failed n' in html and 'class="fe catch on s' in html
    assert 'class="fe next on s0 t' in html and "any error</text>" in html and ".fg:has(.n1:hover)" in html
    assert "<script" not in html


def test_explorer_says_what_it_shows_as_a_focus(fakes):
    x = explorer(fakes)
    assert (x.focus.service, x.focus.kind, x.focus.chips()) == ("stepfunctions", "machines", ["State machines tab"])
    x.tab_buttons["overview"].click()
    assert x.focus.kind == "machine" and x.focus.chips() == ["order-pipeline", "Overview tab"]
    assert x.focus.value("state_machine") == machine_arn("order-pipeline") and x.focus.since is None
    x.tab_buttons["executions"].click()
    assert x.focus.kind == "executions" and x.focus.since == x.page.since
    next(c for c in x.chips.children if c.description.startswith("Failed")).click()
    x.find.value = "ord-7"
    assert x.focus.chips()[2:] == ["last 7 days", "failed only", "search: ord-7"]
    x.execution("ord-7f3a")
    assert x.focus.kind == "execution" and x.focus.value("error") == "OrderFailed"
    assert x.focus.chips()[3:] == ["execution ord-7f3a", "failed", "OrderFailed", "stopped at LoadOrders (KeyError)"]
    x.tab_buttons["graph"].click()
    next(line for line in x._state_lines if line.key == "ChargeCard").button.click()
    assert x.focus.kind == "state" and x.focus.chips()[2:] == ["execution ord-7f3a", "ChargeCard state"]
    x.mode_pick.value = "failures"
    assert x.focus.chips()[2:] == ["last 7 days", "ChargeCard state"] and x.focus.since == x.report.since
    x.open("kb-sync", tab="definition")
    assert x._tab == "definition" and x.focus.kind == "definition" and x.focus.chips()[0] == "kb-sync"
    x.open("kb-sync", tab="logs")
    assert "tab is one of overview" in plain(x.status.value)


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


def test_explorer_graph_boxes_can_be_clicked(fakes):
    x = explorer(fakes, "order-pipeline")
    x.execution("ord-7f3a")
    x.tab_buttons["graph"].click()
    layout = x.graph_layout
    assert x.graph_canvas.layout.width == f"{layout.width:.0f}px" and len(x._hits) == len(layout.nodes)
    process = next(button for button in x._hits if button.box_name == "ProcessItems")
    resize = next(button for button in x._hits if button.box_name == "ResizeImage")
    assert x._hits.index(process) < x._hits.index(resize) and resize.layout.left == f"{layout.node('ResizeImage').x:.0f}px"
    drawn = x.graph_svg.value
    charge = next(button for button in x._hits if button.box_name == "ChargeCard")
    charge.click()
    assert x.picked_state == "ChargeCard" and "sfx-hit-on" in charge._dom_classes
    assert x.graph_svg.value == drawn and "Its tries in ord-7f3a" in plain(x.state_view.value)
    number = next(c for c in charge._dom_classes if c.startswith("fgh"))[3:]
    assert f".fgc:has(.fgh{number}:hover) .sfn .fg" in drawn



def test_explorer_lays_out_by_the_room_its_pages_get(fakes):
    """
    The pages sit in a box the stylesheet measures (.sfx-main), so opening the ✦ Ask drawer stacks the Executions and
    Graph tabs' panes, the tab bar keeps its names (or icons) whole with ✦ Ask in view, and a narrow log puts each
    message on its own line.
    """
    x = explorer(fakes, "order-pipeline")
    main = x.root.children[2].children[0]
    assert "sfx-main" in main._dom_classes
    css = x.root.children[0].value
    assert ".sfx-main{container:sfx-main/inline-size}" in css
    assert "@container sfx-main (max-width:900px){.sfx-app.sfx-app .sfx-page.sfx-split{flex-wrap:wrap" in css
    assert ".sfx-states{flex:0 0 44%;min-width:360px;max-width:560px" in css
    assert ".sfx-tabs button.sfx-assist{position:sticky;right:0" in css
    assert ".sfn .story{container-type:inline-size" in css and ".sfn .story .sx{grid-column:1/-1" in css

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


def test_console_url():
    machine = "arn:aws:states:us-east-1:123456789012:stateMachine:order-pipeline"
    assert console_url(machine) == ("https://us-east-1.console.aws.amazon.com/states/home?region=us-east-1"
                                    f"#/statemachines/view/{machine}")
    standard = "arn:aws:states:eu-west-1:123456789012:execution:order-pipeline:ord-7f3a"
    assert console_url(standard).endswith(f"#/v2/executions/details/{standard}")
    assert parse_execution_ref(console_url(standard)) == (None, standard)
    express = "arn:aws:states:us-east-1:1:express:resize:r-1:r-1-uuid"
    started = datetime(2026, 5, 1, tzinfo=timezone.utc)
    assert console_url(express, started=started).endswith(
        f"#/express-executions/details/{express}?startDate={int(started.timestamp() * 1000)}")
    assert console_url("arn:aws-cn:states:cn-north-1:1:stateMachine:x").startswith(
        "https://cn-north-1.console.amazonaws.cn/states/home?region=cn-north-1#")
    assert console_url("order-pipeline") == "" and console_url("arn:aws:lambda:us-east-1:1:function:x") == ""


def test_explorer_links_to_the_console(fakes):
    x = explorer(fakes, "order-pipeline")
    assert f'href="{html_lib.escape(console_url(x.machine.arn))}"' in x.console_link.value
    assert 'target="_blank"' in x.console_link.value and x.execution_link.value == ""
    x.tab_buttons["executions"].click()
    row = next(line for line in x._rows if line.key.endswith(":ord-7f3a"))
    row.button.click()
    assert f"#/v2/executions/details/{row.key}" in x.execution_link.value
    x._open_machine(x.core.resolve("kb-sync"))
    assert x.execution_link.value == "" and "kb-sync" in x.console_link.value


def test_explorer_draws_its_reports_in_the_theme_set(fakes):
    """
    Like the Lambda and S3 explorers, the window draws its reports in the theme set_theme() names when it opens, so
    a tab looks like the same command run in a cell. Its hidden style widget holds that theme's rules once, and
    _html drops them from each page.
    """
    set_theme("soft")
    x = explorer(fakes)
    theme_rules = _theme_css("sfn", "soft")
    pages = [pane.value for pane in html_widgets(x.root) if 'class="sfn' in pane.value]
    assert pages and all('<div class="sfn theme-soft">' in page for page in pages)
    assert x.root.children[0].value.count(theme_rules) == 1
    assert not any(theme_rules in page for page in pages)


def test_executions_cut_short_still_list_every_failure(core, fakes):
    page = core.executions_with_failures("order-pipeline", since="30d", limit=7)
    names = [e.name for e in page.executions]
    assert len(names) == 10 and page.truncated and page.with_failures and not page.failures_truncated
    assert names[7:] == ["ord-77e0", "ord-3c9b", "ord-e2f4"]
    filters = [call.get("statusFilter") for call in fakes["stepfunctions"].called("list_executions")]
    assert filters == [None, "FAILED", "TIMED_OUT", "ABORTED"]
    whole = core.executions_with_failures("order-pipeline", since="30d")
    assert not whole.truncated and not whole.with_failures and len(whole.executions) == 10
    assert core.executions_with_failures("order-pipeline", since="30d", limit=2).failures_truncated


def test_explorer_list_counts_agree_with_the_header(fakes, monkeypatch):
    monkeypatch.setattr(sfnmod.explorer.window, "_EXECUTIONS_READ", 6)
    x = explorer(fakes, "order-pipeline")
    chips = {c.description.rsplit(" ", 1)[0]: c.description.rsplit(" ", 1)[1] for c in x.chips.children}
    assert chips["Failed"] == "4" and chips["Timed out"] == "1"
    assert plain(x.cut_note.value) == "The newest 6 of about 1,239, and every one that didn't succeed."
    assert "(the newest 6 and every one that didn't succeed)" in plain(x.status.value)
    assert "Executions · 7d 1,239" in plain(x.stats.value)
    x.since_pick.value = "24h"
    assert x.range_numbers.days == 1 and "Executions · 1d 177 Didn't succeed 10 (5.6%)" in plain(x.stats.value)
    assert {e.name for e in x.page.executions if e.failed} == {"ord-7f3a", "ord-0b17", "ord-5d21"}
    assert plain(x.cut_note.value) == "The newest 6 of about 177, and every one that didn't succeed."
    x.since_pick.value = "7d"
    assert x.range_numbers is None and "Executions · 7d 1,239" in plain(x.stats.value)
    x.open("refund-approvals")
    assert plain(x.cut_note.value) == "" and x.range_numbers is None


def test_execution_report_shows_what_it_returned_as_a_tree(ui, capsys):
    out = run(capsys, ui.execution, "order-pipeline/ord-25a8", logs=False)
    assert "What it returned" in out and '"notified": true' in out
    detail = ui.core.execution("order-pipeline/ord-25a8", logs=False)
    html = sfnmod.view._render_html(ui._execution_blocks(detail), 50)
    assert "What it returned" in html and "jt" in html and ".sfn .jt" in html
    failed = run(capsys, ui.execution, "order-pipeline/ord-7f3a", logs=False)
    assert "What it returned" not in failed and "The execution's input" in failed


def test_express_output_comes_from_its_log_lines():
    history = History(NOW, {"key": "a.jpg"})
    history.state("Pass", "Resize").succeed({"thumbnail": "a-small.jpg"})
    execution = Execution(arn="arn", name="x", machine="m", status="SUCCEEDED")
    fill_detail(ExecutionDetail(execution), history.events)
    assert json.loads(execution.output) == {"thumbnail": "a-small.jpg"}


def test_states_inside_a_map_can_be_picked(fakes):
    x = explorer(fakes, "order-pipeline")
    keys = [line.key for line in x._state_lines]
    assert keys.index("ResizeImage") == keys.index("ProcessItems") + 1 and len(keys) == 10
    face = next(line for line in x._state_lines if line.key == "ResizeImage").face.value
    assert 'class="er d1"' in face and "↳ ResizeImage" in plain(face)
    x.execution("ord-25a8")
    x.tab_buttons["graph"].click()
    line = next(line for line in x._state_lines if line.key == "ResizeImage")
    assert "ran 3 times" in plain(line.face.value) and "m-ok" in line.face.value
    line.button.click()
    inspector = plain(x.state_view.value)
    assert "Inside ProcessItems, a Map: it runs once for each item" in inspector
    assert "It ran 3 times in ord-25a8: the last one is shown." in inspector
    assert 'class="fn t-task m-ok n' in x.graph_svg.value and "ran 3 times" in x.graph_svg.value
    hit = next(button for button in x._hits if button.box_name == "ResizeImage")
    assert "sfx-hit-on" in hit._dom_classes
    x.execution("ord-0b17")
    assert "m-running" in line.face.value and "ran 1 time" in plain(line.face.value)


def test_a_map_shows_its_failed_item_first():
    history = History(NOW, {"items": [1, 2, 3]})
    history.map_state("ProcessItems", "ResizeImage", "resize-image",
                      [Try(1, request_id="r1"), Try(1, "Boom", lambda_error("Boom", "bad item", "r2")),
                       Try(1, request_id="r3")])
    execution = Execution(arn="arn", name="ord-x", machine="order-pipeline", status="FAILED")
    detail = fill_detail(ExecutionDetail(execution, machine=order_pipeline()), history.events)
    marks = inner_path_marks(order_pipeline(), detail)
    assert marks.states["ResizeImage"] == "failed" and marks.notes["ResizeImage"] == "ran 3 times, 1 failed"
    blocks = graph_execution_blocks("ResizeImage", detail, None)
    assert blocks[0].text == "It ran 3 times in ord-x, 1 of them failed: the last failed one is shown."
    assert blocks[0].level == "warn" and blocks[1].rows[0][4] == "Boom"
    assert inner_states(order_pipeline(), "ProcessItems")[0].name == "ResizeImage"
    assert owner_of(order_pipeline(), "ResizeImage") == "ProcessItems"
    assert owner_of(order_pipeline(), "ChargeCard") == "ChargeCard"


def test_failures_inside_a_map_are_shaded_on_their_line():
    machine = order_pipeline()
    report = sfnmod.FailureReport(machine, NOW, groups=[sfnmod.FailureGroup("ResizeImage", "Boom", count=1),
                                                        sfnmod.FailureGroup("LoadOrders", "KeyError", count=3)])
    marks = inner_failure_marks(machine, report)
    assert marks.states == {"ResizeImage": "h2"} and marks.notes["ResizeImage"] == "1 stopped here (25%)"


SUCCEEDED = ("ord-25a8", "ord-cfae", "ord-a410")


def timings_of(names=SUCCEEDED, numbers=None):
    report = sfnmod.TimingReport(order_pipeline(), NOW - timedelta(days=7), asked=20, numbers=numbers)
    return timing_report(report.machine, [detail_of(name) for name in names], report)


def test_state_timings_over_succeeded_executions():
    timings = {t.name: t for t in state_timings([detail_of(name) for name in SUCCEEDED])}
    assert list(timings)[:3] == ["ValidateOrder", "IsPriority", "ReserveStock"] and "BackorderWait" not in timings
    charge = timings["ChargeCard"]
    assert (charge.runs, charge.tries, charge.tried_runs, charge.retried_runs, charge.retries) == (3, 4, 3, 1, 1)
    assert charge.tries_per_run == pytest.approx(4 / 3) and charge.retry_error == "Lambda.TooManyRequestsException"
    assert charge.retried_in.name == "ord-cfae" and charge.function == "charge-card"
    load = timings["LoadOrders"]
    assert load.median == pytest.approx(5.725) and load.longest == pytest.approx(21.425)
    assert load.slowest.name == "ord-a410"
    resize = timings["ResizeImage"]
    assert (resize.depth, resize.runs, resize.executions, resize.share) == (1, 9, 3, None)
    assert timings["IsPriority"].tries_per_run is None
    top_shares = [t.share for t in timings.values() if t.depth == 0]
    assert max(top_shares) == timings["ProcessItems"].share and sum(top_shares) <= 1.0
    assert state_timings([]) == []
    assert took_text(0.04) == "0.04s" and took_text(9.5) == "9.5s" and took_text(None) == "-"


def test_timing_findings_say_what_retries_and_what_takes_the_time():
    numbers = MachineNumbers(days=7, started=7 * 177)
    report = timings_of(numbers=numbers)
    found = timing_findings(report, STEPFUNCTIONS_PRICES)
    levels = [level for level, _ in found]
    assert levels == ["warn", "info", "info"]
    retry, slow, uneven = (message for _, message in found)
    assert retry.startswith("ChargeCard retries in 1 of its 3 runs (1.33 tries a run, mostly after Lambda.Too")
    assert "about 1,770 retries a month at this pace, $0.04" in retry
    assert "give charge-card reserved concurrency" in retry and "execution('order-pipeline/ord-cfae')" in retry
    assert slow.startswith("ProcessItems takes 6") and "Inside it, ResizeImage takes the most" in slow
    assert "LambdaView().performance('resize-image')" in slow
    assert uneven.startswith("LoadOrders runs unevenly") and "execution('order-pipeline/ord-a410')" in uneven
    unpriced = timing_findings(timings_of(), STEPFUNCTIONS_PRICES)[0][1]
    assert "$" not in unpriced and "a month" not in unpriced
    quiet = timing_findings(timings_of(("ord-25a8",)), STEPFUNCTIONS_PRICES)
    assert [message.split(" ")[0] for _, message in quiet] == ["ProcessItems"]
    assert timing_findings(timings_of(()), STEPFUNCTIONS_PRICES) == []


def test_timing_marks_shade_boxes_by_their_share_of_the_time():
    layout = flow_layout(order_pipeline())
    report = timings_of()
    marks = timing_marks(layout, report)
    assert marks.legend == "Where the time goes in 3 succeeded executions (all there were)"
    assert marks.states["ProcessItems"] == "t3" and marks.states["LoadOrders"] == "t2"
    assert marks.states["ValidateOrder"] == "t1" and "BackorderWait" not in marks.states
    assert marks.notes["ChargeCard"] == "3% · 1.3 tries · 0.9s" and marks.notes["IsPriority"] == "0% · 0.01s"
    inner = inner_timing_marks(order_pipeline(), report)
    assert inner.notes == {"ResizeImage": "3× an execution · 9.5s"} and inner.states == {}
    report.asked = 3
    assert timing_marks(layout, report).legend == "Where the time goes in the newest 3 succeeded executions"


def test_timings_read_the_newest_succeeded_histories(core, fakes):
    report = core.timings("order-pipeline", n=2)
    assert [e.name for e in report.executions] == ["ord-25a8", "ord-cfae"] and report.asked == 2
    assert report.source == "history" and report.numbers is not None and report.partial == 0
    sfn = fakes["stepfunctions"]
    assert {call.get("statusFilter") for call in sfn.called("list_executions")} == {"SUCCEEDED"}
    histories = sfn.called("get_execution_history")
    assert len(histories) == 2 and all(call["includeExecutionData"] is False for call in histories)
    assert report.state("ChargeCard").retried_runs == 1
    assert list(report.to_df()["state"])[:2] == ["ValidateOrder", "IsPriority"]
    express = core.timings("image-resize-express")
    assert express.source == "logs" and len(express.executions) == 3 and express.state("Resize").runs == 3
    assert core.timings("refund-approvals").executions == []


def test_express_timings_need_every_event_logged(fakes):
    fakes["stepfunctions"].machines["image-resize-express"]["logging"]["level"] = "ERROR"
    core = StepFunctionsAnalyzer(region="us-east-1", clients=fakes)
    report = core.timings("image-resize-express")
    assert "only executions that failed are in its log group" in report.note
    fakes["stepfunctions"].machines["image-resize-express"]["logging"]["level"] = "OFF"
    core = StepFunctionsAnalyzer(region="us-east-1", clients=fakes)
    with pytest.raises(_Hint, match="logging off"):
        core.timings("image-resize-express")


def test_graph_report_shows_where_the_time_goes(ui, capsys):
    out = run(capsys, ui.graph, "order-pipeline", timings=True)
    assert "Where the time goes in 3 succeeded executions (all there were)" in out
    assert "ProcessItems (Map) [half the time or more] · 69% · 29s" in out
    assert "[!] ChargeCard retries in 1 of its 3 runs" in out
    assert "-- Each state's time, from 3 succeeded executions since" in out
    assert re.search(r"↳ ResizeImage\s+9 \(3 an execution\)\s+9\.5s", out)
    assert "graph('order-pipeline', timings=50) reads more" in out
    assert out.index("Findings") < out.index("Where the time goes") < out.index("Each state, what it does")
    out = run(capsys, ui.graph, "order-pipeline")
    assert "graph('order-pipeline', timings=True)" in out and "Each state's time" not in out
    out = run(capsys, ui.graph, "refund-approvals", timings=2)
    assert "No execution of refund-approvals succeeded since" in out
    view = StepFunctionsView(ui.core, mode="html")
    shown = []
    view._show = shown.append
    view.graph("order-pipeline", timings=True)
    html = sfnmod.view._render_html(shown[0], 50)
    assert 'class="fn fc t-map m-t3' in html and 'class="m-t3"' in html and "half the time or more" in html


def test_explorer_graph_tab_shows_where_the_time_goes(fakes):
    fakes["stepfunctions"].machines["kb-sync"]["type"] = "EXPRESS"
    x = explorer(fakes, "order-pipeline")
    x.tab_buttons["graph"].click()
    assert x.timings is None
    x.mode_pick.value = "timing"
    assert x.timings is not None and len(x.timings.executions) == 3
    graph = plain(x.graph_view.value + x.graph_foot.value)
    assert "Where the time goes in 3 succeeded executions" in graph and "ChargeCard retries in 1 of its 3 runs" in graph
    assert "ord-cfae in the Executions tab shows one" in graph and "Each state's time" not in graph
    assert "From 3 succeeded executions since" in graph
    assert "where the time goes" in x.focus.chips() and "last 7 days" in x.focus.chips()
    load = next(line for line in x._state_lines if line.key == "LoadOrders")
    assert "27% · 5.7s" in plain(load.face.value) and "m-t2" in load.face.value
    resize = next(line for line in x._state_lines if line.key == "ResizeImage")
    assert "3× an execution · 9.5s" in plain(resize.face.value)
    load.button.click()
    inspector = plain(x.state_view.value)
    assert "Its time in the succeeded executions read" in inspector and "Slowest in ord-a410" in inspector
    next(line for line in x._state_lines if line.key == "ChargeCard").button.click()
    assert "Retried in ord-cfae" in plain(x.state_view.value)
    read = x.timings
    x.mode_pick.value = "plain"
    x.mode_pick.value = "timing"
    assert x.timings is read
    x.since_pick.value = "24h"
    assert x.timings is not read and x.timings.since > read.since
    x.open("image-resize-express")
    assert x.timings is not None and x.timings.source == "logs"
    x.open("kb-sync")
    assert x.timings is None and "logging off leaves no record" in plain(x.graph_view.value)


def test_explorer_reads_timings_in_the_background(fakes):
    async def settle(x):
        while x._tasks:
            await asyncio.gather(*x._tasks.values())

    async def main():
        x = explorer(fakes, "order-pipeline", tab="graph")
        await settle(x)
        history = fakes["stepfunctions"].holds["get_execution_history"] = threading.Event()
        x.mode_pick.value = "timing"
        assert "timings" in x._tasks and x.timings is None
        assert "Reading the newest succeeded executions" in plain(x.graph_view.value)
        assert "Reading order-pipeline's newest succeeded executions" in plain(x.status.value)
        history.set()
        await settle(x)
        assert len(x.timings.executions) == 3 and "Where the time goes" in plain(x.graph_view.value)
        assert "Where the time goes, from 3 succeeded executions in the last 7 days" in plain(x.status.value)

    asyncio.run(main())


BARS = re.compile(r'class="dz( jz)?"')


def week(*unsuccessful, started=100):
    """
    MachineNumbers for a week ending on 15 Nov 2026 with these failures a day, oldest first.
    """
    return MachineNumbers(days=len(unsuccessful), started=started * len(unsuccessful),
                          failed=sum(unsuccessful), daily_started=[started] * len(unsuccessful),
                          daily_failed=list(unsuccessful), daily_unsuccessful=list(unsuccessful),
                          first_day=datetime(2026, 11, 9).date())


def test_failures_jump_on_the_day_they_rose_most():
    days = daily_counts(week(3, 2, 4, 3, 2, 21, 14))
    assert [d.unsuccessful for d in days] == [3, 2, 4, 3, 2, 21, 14] and days[0].day.day == 9
    jump = failure_jump(days)
    assert jump.day.day == 14 and jump.after == 35 and jump.total == 49 and jump.days_after == 2
    assert jump.per_day_before == pytest.approx(2.8)


def test_no_jump_without_enough_failures_or_a_real_rise():
    assert failure_jump(daily_counts(week(3, 3, 3, 3, 3, 3, 3))) is None
    assert failure_jump(daily_counts(week(0, 0, 0, 0, 0, 1, 2))) is None
    assert failure_jump(daily_counts(week(9, 9, 9, 9, 9, 12, 12))) is None
    assert failure_jump(daily_counts(week(0, 9))) is None
    assert daily_counts(MachineNumbers(days=7)) == [] and daily_counts(None) == []


def test_a_jump_is_a_finding_that_reads_just_those_failures(core):
    machine = core.state_machine("order-pipeline")
    found = trend_findings(machine, week(3, 2, 4, 3, 2, 21, 14))
    assert found[0][0] == "warn" and found[0][1].startswith("Failures jumped on 14 Nov: 35 of the 49 in the last 7 days")
    assert "failures('order-pipeline', since='2d')" in found[0][1]
    assert trend_findings(machine, week(3, 3, 3, 3, 3, 3, 3)) == []


def test_reports_draw_executions_a_day(core, capsys):
    out = run(capsys, StepFunctionsView(core, mode="text").state_machine, "order-pipeline")
    assert "-- Executions a day, the part that didn't succeed in red --" in out
    assert "177 started · 10 didn't succeed  <- failures jumped" in out and "Failures jumped on" in out
    out = run(capsys, StepFunctionsView(core, mode="text").failures, "order-pipeline")
    assert "Executions a day" in out and "Since" in out and "3d ago" in out
    view = StepFunctionsView(core, mode="html")
    shown = []
    view._show = shown.append
    view.state_machine("order-pipeline")
    page = sfnmod.view._render_html(shown[0], 50)
    assert len(BARS.findall(page)) == 7 and page.count('class="dz jz"') == 1 and "UTC days" in page


def test_explorer_shows_executions_a_day_over_the_range(fakes):
    x = explorer(fakes, "order-pipeline")
    assert plain(x.days_view.value).count("Nov") == 7 and 'class="dz jz"' in x.days_view.value
    x.since_pick.value = "24h"
    assert x.days_view.value == ""
    x.since_pick.value = "30d"
    assert x.range_numbers.days == 30 and len(BARS.findall(x.days_view.value)) == 30


def test_a_failure_group_lists_its_executions_and_redrive_commands(core):
    report = core.failures("order-pipeline")
    group = report.groups[0]
    assert [e.name for e in group.executions] == ["ord-7f3a", "ord-5d21", "ord-3c9b"]
    assert group.first_seen == group.executions[-1].start
    blocks = StepFunctionsView(core, mode="text")._failure_group_blocks(report, group)
    assert blocks[0].text == "LoadOrders fails with KeyError"
    commands = [b for b in blocks if getattr(b, "code", False)]
    assert len(commands) == 1 and commands[0].text.count("aws stepfunctions redrive-execution") == 3


def keyerror_report(core, *changes, since_days=7, truncated=False):
    """
    A FailureReport with one group, LoadOrders failing with KeyError in orders-etl: three executions, the first
    three days ago and the newest an hour ago, and these changes.
    """
    machine = core.state_machine("order-pipeline")
    starts = [NOW - timedelta(hours=1), NOW - timedelta(days=1), NOW - timedelta(days=3)]
    executions = [Execution(f"arn:x:{i}", f"ord-{i}", "order-pipeline", "FAILED", start=start)
                  for i, start in enumerate(starts)]
    group = FailureGroup("LoadOrders", "KeyError", count=3, newest=executions[0], function="orders-etl",
                         executions=executions)
    return FailureReport(machine, NOW - timedelta(days=since_days), failed=executions, groups=[group],
                         truncated=truncated, changes=list(changes))


def deploy(ago, name="orders-etl", detail="version 7"):
    return Change(NOW - ago, "function", name, detail)


def test_a_deploy_just_before_the_first_failure_is_the_suspect(core):
    report = keyerror_report(core, deploy(timedelta(days=30), detail="version 6"),
                             deploy(timedelta(days=3, minutes=4)), deploy(timedelta(hours=5), name="charge-card"))
    verdict = change_verdict(report, report.groups[0])
    assert verdict.before.detail == "version 7" and verdict.exact
    level, text = group_change_findings(report, report.groups[0])[0]
    assert level == "warn" and text.startswith(
        "LoadOrders began failing with KeyError 4 minutes after orders-etl version 7 was published: ")
    assert "Timing, not proof" in text and "LambdaView().function_info('orders-etl')" in text


def test_a_change_after_it_began_that_it_failed_past(core):
    report = keyerror_report(core, Change(NOW - timedelta(days=2), "definition", "order-pipeline"))
    level, text = group_change_findings(report, report.groups[0])[0]
    assert level == "info" and text.startswith("order-pipeline's definition was updated 2d ago, after LoadOrders")
    assert "didn't fix it" in text


def test_nothing_changed_nearby_points_at_the_input(core):
    report = keyerror_report(core, deploy(timedelta(days=30)))
    level, text = group_change_findings(report, report.groups[0])[0]
    assert level == "info" and text.startswith("Nothing changed in the 2 days before LoadOrders began failing")
    assert "orders-etl version 7 was published 27 days before" in text and "execution('order-pipeline/ord-0')" in text


def test_a_start_that_may_be_earlier_is_said_or_not_claimed(core):
    early = keyerror_report(core, deploy(timedelta(days=3, minutes=4)), since_days=3, truncated=True)
    assert "It may have begun earlier" in group_change_findings(early, early.groups[0])[0][1]
    quiet = keyerror_report(core, deploy(timedelta(days=30)), truncated=True)
    assert group_change_findings(quiet, quiet.groups[0]) == []
    assert gap_text(timedelta(seconds=20)) == "1 minute" and gap_text(timedelta(hours=30)) == "30 hours"


def test_failures_read_what_changed(core, fakes):
    report = core.failures("order-pipeline")
    deployed = [(c.name, c.detail) for c in report.changes if c.kind == "function"]
    assert ("orders-etl", "version 2") in deployed and ("charge-card", "version 1") in deployed
    assert [c.name for c in report.changes if c.kind == "definition"] == ["order-pipeline"]
    asked = fakes["stepfunctions"].called("describe_state_machine_for_execution")
    assert len(asked) == len(report.groups) and not report.change_errors
    found = change_findings(report)
    assert found[0][1].startswith("LoadOrders began failing with KeyError 4 minutes after orders-etl version 2")
    assert sum(text.startswith("Nothing changed") for _, text in found) == 2


def test_what_changed_degrades_to_a_note(fakes):
    fakes["lambda"].denied.add("get_function")
    fakes["stepfunctions"].denied.add("describe_state_machine_for_execution")
    core = StepFunctionsAnalyzer(region="us-east-1", clients=fakes)
    core.max_workers = 1
    report = core.failures("order-pipeline")
    assert report.change_errors["definition"] == "AccessDeniedException"
    assert report.change_errors["lambda:orders-etl"] == "AccessDeniedException" and report.changes == []
    assert "Couldn't read what changed before these failures" in change_findings(report)[-1][1]
    asked = len(fakes["stepfunctions"].called("describe_state_machine_for_execution"))
    express = core.failures("image-resize-express")
    assert len(fakes["stepfunctions"].called("describe_state_machine_for_execution")) == asked
    assert "definition" not in express.change_errors


def test_what_changed_in_the_reports_and_the_window(core, fakes, capsys):
    out = run(capsys, StepFunctionsView(core, mode="text").failures, "order-pipeline")
    assert "LoadOrders began failing with KeyError 4 minutes after orders-etl version 2 was published" in out
    report = core.failures("order-pipeline")
    blocks = StepFunctionsView(core, mode="text")._failure_group_blocks(report, report.groups[0])
    timeline = next(b for b in blocks if type(b).__name__ == "_Changes")
    assert timeline.suspect is not None and timeline.suspect.detail == "version 2"
    text = "\n".join(sfnmod.blocks._changes_text(timeline, None))
    assert "◆ orders-etl version 2 was published  <- just before" in text and "✕ ord-3c9b failed" in text
    x = explorer(fakes, "order-pipeline")
    x.tab_buttons["failures"].click()
    next(line for line in x._failure_lines if line.key == "LoadOrders · KeyError").button.click()
    page = x.failures_view.value
    assert 'class="chl sus"' in page and "4 minutes after orders-etl version 2" in plain(page)
    other = next(line for line in x._failure_lines if line.key == "ChargeCard · Lambda.TooManyRequestsException")
    other.button.click()
    assert "Nothing changed in the 2 days before ChargeCard" in plain(x.failures_view.value)


def test_inputs_differ_by_field_not_by_value():
    this = json.dumps({"order_id": "1", "customer": {"email": ""}, "items": [1], "total": "12", "coupon": "X"})
    other = json.dumps({"order_id": "2", "customer_id": "C-1", "customer": {"email": "a@b.c"}, "items": [2],
                        "total": 12})
    found = {d.path: (d.kind, d.this, d.other) for d in compare_inputs(this, other)}
    assert found == {"customer_id": ("missing", "", '"C-1"'), "customer.email": ("empty", '""', '"a@b.c"'),
                     "total": ("type", '"12"', "12"), "coupon": ("extra", '"X"', "")}
    assert compare_inputs("not json", "{}") == [] and compare_inputs("", "") == []


def test_a_failed_execution_beside_the_last_success(core):
    this = core.execution("order-pipeline/ord-7f3a", logs=False)
    other = core.execution("order-pipeline/ord-cfae", logs=False)
    comparison = compare_executions(this, other)
    kinds = {step.state: step.kind for step in comparison.steps}
    assert kinds["ValidateOrder"] == "same" and kinds["ChargeCard"] == "retried" and kinds["LoadOrders"] == "failed"
    assert kinds["OrderFailed"] == "after_failure" and kinds["NotifyCustomer"] == "not_reached"
    assert [step.state for step in comparison.differing_steps] == ["ChargeCard", "LoadOrders"]
    assert comparison.other_is_before
    found = comparison_findings(comparison)
    assert found[0] == ("warn", 'ord-7f3a\'s input has no customer_id, which ord-cfae\'s had ("C-4471"): that\'s the '
                                "likely cause, since LoadOrders failed with KeyError, naming it. Find what sent this "
                                "input without it, or make the state handle a missing customer_id.")
    assert ("info", "ChargeCard took 3 tries in ord-7f3a and 2 in ord-cfae: it was retried more, so its errors were "
                    "the kind a Retry catches.") in found
    assert not any(text.startswith("LoadOrders failed in") for _, text in found)
    assert not any("different branches" in text for _, text in found)


def test_without_a_named_field_the_failed_state_is_the_finding(core):
    this = core.execution("order-pipeline/ord-77e0", logs=False)
    comparison = compare_executions(this, core.execution("order-pipeline/ord-cfae", logs=False))
    found = comparison_findings(comparison)
    assert found[0][0] == "warn" and found[0][1].startswith("ChargeCard failed in ord-77e0 with ")
    same = compare_executions(core.execution("order-pipeline/ord-cfae", logs=False),
                              core.execution("order-pipeline/ord-cfae", logs=False))
    assert same.differing_steps == []
    assert "ran the same states with the same input fields" in comparison_findings(same)[0][1]
    later = compare_executions(this, core.execution("order-pipeline/ord-25a8", logs=False))
    assert not later.other_is_before and "which started after it" in comparison_findings(later)[-1][1]


def test_compare_finds_the_last_success_before_it(core):
    comparison = core.compare("order-pipeline/ord-7f3a")
    assert comparison.other.execution.name == "ord-cfae" and comparison.other_is_before
    named = core.compare("order-pipeline/ord-7f3a", other="ord-25a8")
    assert named.other.execution.name == "ord-25a8" and not named.other_is_before
    with pytest.raises(_Hint, match="No execution of refund-approvals succeeded in the last 30d"):
        core.compare("refund-approvals/refund-42")
    page = core.executions("order-pipeline", status="succeeded", since="30d")
    assert _newest_before(page.executions, None) is page.executions[0] and _newest_before([], NOW) is None


def test_compare_report(ui, capsys):
    out = run(capsys, ui.compare, "order-pipeline/ord-7f3a")
    for expected in ("ord-7f3a beside ord-cfae", "States that differ: 2 (!)", "Input fields that differ: 1 (!)",
                     "[!] ord-7f3a's input has no customer_id, which ord-cfae's had",
                     "customer_id  -         \"C-4471\"  missing here",
                     "failed here with KeyError; went through in ord-cfae", "execution('order-pipeline/ord-cfae')"):
        assert expected in out, expected
    assert "No execution of refund-approvals succeeded" in run(capsys, ui.compare, "refund-approvals/refund-42")
    out = run(capsys, ui.execution, "order-pipeline/ord-7f3a", logs=False)
    assert "compare('order-pipeline/ord-7f3a')" in out


def test_explorer_compares_a_failed_execution_with_the_last_success(fakes):
    x = explorer(fakes, "order-pipeline")
    x.execution("ord-25a8")
    assert x.compare_button.layout.display == "none"
    x.execution("ord-7f3a")
    assert x.compare_button.layout.display == "" and x.compare_view.value == ""
    x.compare_button.click()
    assert x.comparison.other.execution.name == "ord-cfae"
    shown = plain(x.compare_view.value)
    assert "ord-7f3a beside ord-cfae" in shown and "That's the likely cause" in shown
    assert x.compare_button.description == "✕ Hide the comparison"
    x.compare_button.click()
    assert x.comparison is None and x.compare_view.value == "" and x.compare_button.layout.display == ""
    x.compare("ord-5d21", other="ord-25a8")
    assert x._selected.endswith("ord-5d21") and x.comparison.other.execution.name == "ord-25a8"
    x.execution("ord-0b17")
    assert x.comparison is None and x.compare_view.value == ""


def test_an_errors_kind_says_whether_it_passes():
    assert error_kind("Lambda.TooManyRequestsException") == "transient"
    assert error_kind("DynamoDB.ProvisionedThroughputExceededException") == "transient"
    assert error_kind("Lambda.ServiceException") == "transient"
    assert error_kind("KeyError") == "permanent" and error_kind("TypeError") == "permanent"
    assert error_kind("States.Permissions") == "permanent" and error_kind("S3.AccessDeniedException") == "permanent"
    assert error_kind("States.Timeout") == "timeout"
    assert error_kind("Lambda.Unknown", "Task timed out after 30.03 seconds") == "timeout"
    assert error_kind("Runtime.OutOfMemory") == "memory" and error_kind("Something.Odd") == "unknown"


def test_a_states_retry_matches_as_step_functions_picks_one():
    named = {"ErrorEquals": ["Lambda.TooManyRequestsException"], "MaxAttempts": 2}
    everything = {"ErrorEquals": ["States.ALL"], "MaxAttempts": 3}
    task_failed = {"ErrorEquals": ["States.TaskFailed"]}
    state = StateDef("ChargeCard", "Task", retries=[named, everything])
    assert matching_retry(state, "Lambda.TooManyRequestsException") is named
    assert matching_retry(state, "KeyError") is everything and matching_retry(state, "States.Runtime") is None
    only_tasks = StateDef("X", "Task", retries=[task_failed])
    assert matching_retry(only_tasks, "KeyError") is task_failed and matching_retry(only_tasks, "States.Timeout") is None
    assert matching_retry(None, "KeyError") is None and matching_retry(StateDef("Y", "Task"), "KeyError") is None
    assert roomier_rule("ThrottlingException", None) == {"ErrorEquals": ["ThrottlingException"], "IntervalSeconds": 2,
                                                         "MaxAttempts": 6, "BackoffRate": 2, "JitterStrategy": "FULL"}
    assert roomier_rule("X", {**named, "MaxAttempts": 8})["MaxAttempts"] == 10
    assert roomier_rule("X", named)["MaxAttempts"] == 6 and roomier_rule("X", named)["JitterStrategy"] == "FULL"


def test_each_kind_of_failure_says_whether_a_retry_would_help(core):
    report = core.failures("order-pipeline")
    advice = {g.state: retry_advice(report.machine, g) for g in report.groups}
    load, charge, resize = advice["LoadOrders"], advice["ChargeCard"], advice["ResizeImage"]
    assert (load.kind, load.would_help, load.verdict, load.suggested) == ("permanent", False, "no: fix the cause", None)
    assert (charge.kind, charge.would_help) == ("transient", True) and charge.rule is not None
    assert charge.suggested["MaxAttempts"] == 6 and charge.verdict == "it retries, but ran out: give it more tries"
    assert (resize.kind, resize.would_help) == ("timeout", None)
    level, text = advice_finding(charge)
    assert level == "warn" and text.startswith("ChargeCard retries Lambda.TooManyRequestsException (×2, back-off 2) "
                                               "and still ran out of tries once")
    assert advice_finding(load)[1].startswith("A Retry won't help the 3 executions that failed at LoadOrders")


def test_a_retry_that_only_repeats_a_code_error(core):
    report = core.failures("order-pipeline")
    group = next(g for g in report.groups if g.state == "LoadOrders")
    report.machine.state("LoadOrders").retries = [{"ErrorEquals": ["States.ALL"], "MaxAttempts": 3}]
    advice = retry_advice(report.machine, group)
    assert advice.verdict == "no, and its Retry repeats it"
    level, text = advice_finding(advice)
    assert level == "warn" and "its Retry on States.ALL (×3, back-off 2.0) only repeats the failure" in text
    report.machine.state("LoadOrders").retries = []
    nothing = next(g for g in report.groups if g.state == "ChargeCard")
    nothing.error = "Something.Odd"
    assert advice_finding(retry_advice(report.machine, nothing)) is None


def test_the_reports_and_the_window_say_whether_a_retry_helps(core, fakes, capsys):
    out = run(capsys, StepFunctionsView(core, mode="text").failures, "order-pipeline")
    assert "Would a Retry help?" in out and "no: fix the cause" in out
    assert "it retries, but ran out: give it more tries" in out and "not first: find why it's slow" in out
    x = explorer(fakes, "order-pipeline")
    x.tab_buttons["failures"].click()
    next(line for line in x._failure_lines if line.key.startswith("ChargeCard")).button.click()
    page = plain(x.failures_view.value)
    assert "A Retry rule for ChargeCard, to paste into its Retry list" in page and '"MaxAttempts": 6' in page
    next(line for line in x._failure_lines if line.key.startswith("LoadOrders")).button.click()
    assert "to paste into its Retry list" not in plain(x.failures_view.value)


def test_an_execution_joins_each_lambda_step_to_its_run():
    from types import SimpleNamespace

    from aws_analyzer.lambda_functions.models import Invocation
    from aws_analyzer.stepfunctions.analysis.datasets import execution_data
    from aws_analyzer.stepfunctions.analysis.story import ExecutionStory, StoryRun

    detail = detail_of("ord-7f3a")
    load = detail.step("LoadOrders")
    report = Invocation(FAILED_RUN, duration=1500.0, billed=1501.0, memory=1024, max_memory=980, init=200.0)
    run = SimpleNamespace(report=report, failures=[("line", "KeyError", "x")], events=[1, 2, 3])
    story = ExecutionStory([], [StoryRun(load, run, "found by its request ID"),
                                StoryRun(detail.step("ValidateOrder"), None, "no log group")])
    data = execution_data(detail, story, STEPFUNCTIONS_PRICES)
    assert data.names == ["steps", "tries", "lambda_runs"]
    joined = data["lambda_runs"]
    first = joined.iloc[0]
    assert first.step == "LoadOrders" and first.request_id == FAILED_RUN and first.lambda_ms == 1500.0
    assert first.outside_ms == pytest.approx(250.0, abs=60) and bool(first.cold_start) and bool(first.failed)
    assert first.memory_share == pytest.approx(980 / 1024)
    assert first.lambda_cost_usd == pytest.approx(1.501 * 1 * 0.0000166667 + 0.0000002)
    assert joined.iloc[1].note == "no log group" and joined.iloc[1].lambda_ms != joined.iloc[1].lambda_ms
    steps = data["steps"]
    charge = steps[steps.step == "ChargeCard"].iloc[0]
    assert charge.retries == 2 and charge.transitions == 3 and charge.est_cost_usd == pytest.approx(3 * 0.000025)
    assert steps.share_of_execution.max() <= 1
    assert len(data["tries"][data["tries"].step == "ChargeCard"]) == 3


def test_reports_give_their_tables_for_pandas(ui, capsys, tmp_path, monkeypatch):
    out = run(capsys, ui.executions, "order-pipeline")
    assert "-- This report as data (pandas, Excel) --" in out
    executions = ui.data()["executions"]
    assert executions.ref.str.startswith("order-pipeline/").all() and (executions.est_cost_usd > 0).all()
    assert ui.data().again == "data('executions', 'order-pipeline')"
    run(capsys, ui.overview)
    machines = ui.data()["machines"]
    assert set(machines.state_machine) >= {"order-pipeline", "image-resize-express"}
    assert machines.est_monthly_usd.fillna(0).is_monotonic_decreasing
    failures = ui.data("failures", "order-pipeline")
    assert capsys.readouterr().out == "" and failures["causes"]["share"].sum() == pytest.approx(1.0)
    assert ui.data("graph", "order-pipeline", timings=True).names == ["timings"]
    assert ui.data("state_machine", "order-pipeline")["states"].state.iloc[0] == "ValidateOrder"
    monkeypatch.chdir(tmp_path)
    ui.data("execution", "order-pipeline/ord-7f3a", logs=False)
    ui.to_excel()
    assert "Saved Execution ord-7f3a as a workbook" in capsys.readouterr().out


def _machine_with(states, *, kind="STANDARD", settings=None):
    machine = parse_state_machine(described("limits", {"StartAt": next(iter(states)), "States": states}))
    machine.type = kind
    machine.lambda_settings = settings or {}
    return machine


def test_a_task_that_gives_up_before_its_function_does():
    retried = _machine_with({"Load": invoke("orders-etl", TimeoutSeconds=30, End=True, Retry=[
        {"ErrorEquals": ["States.Timeout"], "MaxAttempts": 2}])}, settings={"orders-etl": LambdaSettings(60)})
    [clash] = state_limits(retried)
    assert (clash.tone, clash.level, clash.state, clash.function) == ("warn", "warn", "Load", "orders-etl")
    assert "after 30 s it fails with States.Timeout, while the run goes on for up to 60 s" in clash.effect
    assert "the work is done twice" in clash.effect and '"TimeoutSeconds": 65' in clash.fix
    quiet = _machine_with({"Load": invoke("orders-etl", TimeoutSeconds=30, End=True)},
                          settings={"orders-etl": LambdaSettings(60)})
    assert state_limits(quiet)[0].level == "info"
    roomy = _machine_with({"Load": invoke("orders-etl", TimeoutSeconds=90, End=True)},
                          settings={"orders-etl": LambdaSettings(60)})
    assert state_limits(roomy)[0].tone == "ok" and state_limit_findings(roomy) == []
    assert state_limits(_machine_with({"Load": invoke("orders-etl", TimeoutSeconds=30, End=True)})) == []


def test_an_express_workflow_stops_before_a_long_function():
    machine = _machine_with({"Load": invoke("backfill", End=True)}, kind="EXPRESS",
                            settings={"backfill": LambdaSettings(900)})
    [limit] = state_limits(machine)
    assert "which stops at 5 minutes, but backfill may run 900 s" in limit.effect and "--timeout 300" in limit.fix


def test_a_map_runs_more_of_a_function_than_it_may():
    def mapped(**map_settings):
        inner = {"StartAt": "Resize", "States": {"Resize": invoke("resize", End=True)}}
        return {"Items": {"Type": "Map", "ItemProcessor": inner, "End": True, **map_settings}}

    reserved = _machine_with(mapped(MaxConcurrency=10), settings={"resize": LambdaSettings(30, 4)})
    [limit] = state_limits(reserved)
    assert limit.level == "warn" and "up to 10 items against its reserved concurrency of 4" in limit.effect
    assert "a throttle fails the item" in limit.effect and '{"MaxConcurrency": 4} on Items' in limit.fix
    inline = _machine_with(mapped(), settings={"resize": LambdaSettings(30, 100)})
    assert state_limits(inline)[0].tone == "ok"
    distributed = {"ItemProcessor": {"ProcessorConfig": {"Mode": "DISTRIBUTED"}, "StartAt": "Resize",
                                     "States": {"Resize": invoke("resize", End=True)}}}
    pool = _machine_with({"Items": {"Type": "Map", "End": True, **distributed}},
                         settings={"resize": LambdaSettings(30, None, 1_000)})
    [flood] = state_limits(pool)
    assert "up to 10,000 runs of resize at once, as many as the region's whole Lambda pool (1,000)" in flood.effect
    assert '{"MaxConcurrency": 100}' in flood.fix and pool.states[0].distributed


def test_state_machine_reads_its_functions_settings_and_shows_the_clash(ui, capsys, fakes):
    out = run(capsys, ui.state_machine, "order-pipeline")
    assert "-- How its states and the functions and state machines they call limit each other --" in out
    assert "ProcessItems runs more of resize-image at once than it may run" in out
    assert "runs 4 items at once (MaxConcurrency)" in out and "reserved concurrency 2" in out
    called = [name for name, _ in fakes["lambda"].calls]
    assert "get_account_settings" not in called and called.count("get_function") == 4


def _start(child, *, wait=True, **state):
    resource = "arn:aws:states:::states:startExecution" + (".sync:2" if wait else "")
    return {"Type": "Task", "Resource": resource, "Parameters": {"StateMachineArn": machine_arn(child)}, **state}


def _child(name, *, kind="STANDARD", timeout=None):
    definition = {"StartAt": "Go", "States": {"Go": {"Type": "Pass", "End": True}}}
    if timeout is not None:
        definition["TimeoutSeconds"] = timeout
    child = parse_state_machine(described(name, definition))
    child.type = kind
    return child


def _parent(state, child):
    machine = _machine_with({"Run": state})
    if child is not None:
        machine.children[machine_arn(child.name)] = child
    return machine


def test_a_task_that_gives_up_on_the_state_machine_it_starts():
    retried = _start("nightly-load", TimeoutSeconds=600, End=True,
                     Retry=[{"ErrorEquals": ["States.ALL"], "MaxAttempts": 2}])
    [clash] = state_limits(_parent(retried, _child("nightly-load", timeout=3600)))
    assert (clash.tone, clash.level, clash.function) == ("warn", "warn", "nightly-load")
    assert "after 600 s it fails with States.Timeout, while nightly-load may run 3,600 s" in clash.effect
    assert "starts nightly-load again" in clash.effect and '"TimeoutSeconds": 3605' in clash.fix
    [unbounded_child] = state_limits(_parent(_start("nightly-load", TimeoutSeconds=600, End=True),
                                            _child("nightly-load")))
    assert unbounded_child.level == "info" and "up to a year" in unbounded_child.effect
    [express] = state_limits(_parent(_start("quick", TimeoutSeconds=600, End=True), _child("quick", kind="EXPRESS")))
    assert express.tone == "ok" and express.setting == "runs 5 minutes (Express)"
    assert _parent(_start("quick", End=True), None).states[0].resource.endswith(".sync:2")
    assert resource_label(_parent(_start("quick", End=True), None).states[0]) == "⇢ quick"


def test_a_child_with_no_timeout_on_either_side_or_started_and_left():
    [stuck] = state_limits(_parent(_start("nightly-load", End=True), _child("nightly-load")))
    assert "Neither Run nor nightly-load has a timeout" in stuck.effect and stuck.level == "info"
    [left] = state_limits(_parent(_start("nightly-load", wait=False, End=True), None))
    assert left.tone == "" and left.finding is None and "doesn't wait for nightly-load" in left.effect
    assert state_limits(_parent(_start("nightly-load", End=True), None)) == []


def test_read_children_describes_the_state_machines_a_workflow_starts(core):
    parent = _parent(_start("refund-approvals", TimeoutSeconds=60, End=True), None)
    parent.states.append(StateDef("Gone", "Task", resource="arn:aws:states:::states:startExecution.sync:2",
                                  child=machine_arn("not-there")))
    core.read_connections(parent)
    assert list(parent.children) == [machine_arn("refund-approvals")] and "children" in parent.errors
    [limit] = state_limits(parent)
    assert limit.function == "refund-approvals" and limit.tone == "warn"
