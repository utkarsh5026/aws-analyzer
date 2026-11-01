import json
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from fake_stepfunctions import (
    FAILED_RUN, ORDER_PIPELINE, REFUND_APPROVALS, FakeCloudWatch, FakeStepFunctions, History, Try, clients,
    lambda_lines, machine_arn, run_arn, scene,
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
    with pytest.raises(_Hint, match="Express workflow"):
        core.executions("image-resize-express")


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
                     "failures('order-pipeline')", "image-resize-express logs at ERROR only",
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
    assert "What orders-etl logged in that run" in out and "found by its request ID" in out
    assert "reading s3://acme-uploads" in out and "REPORT: run 1.73 s" in out and "Its Lambda run's lines" in out


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
    out = run(capsys, ui.failures, "image-resize-express")
    assert "Express workflow" in out
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
