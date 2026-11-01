"""
An in-memory Step Functions for the tests and the demo: state machines with real definitions, executions, and
histories built event by event the way Step Functions writes them (each event's previousEventId pointing at the
one that caused it), plus CloudWatch's AWS/States numbers. Every request and answer is checked against the service
model, as a real client's would be. moto has state machines, but its execution histories are canned.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import boto3
from botocore import xform_name
from botocore.exceptions import ClientError
from botocore.validate import ParamValidator


ACCOUNT = "123456789012"

REGION = "us-east-1"


def machine_arn(name: str, region: str = REGION) -> str:
    return f"arn:aws:states:{region}:{ACCOUNT}:stateMachine:{name}"


def run_arn(machine: str, name: str, region: str = REGION) -> str:
    return f"arn:aws:states:{region}:{ACCOUNT}:execution:{machine}:{name}"


def function_arn(name: str, region: str = REGION) -> str:
    return f"arn:aws:lambda:{region}:{ACCOUNT}:function:{name}"


def lambda_error(kind: str, message: str, request_id: str) -> str:
    """
    The cause Step Functions records when a Lambda function raises: the error payload Lambda returned.
    """
    return json.dumps({"errorMessage": message, "errorType": kind, "requestId": request_id,
                       "stackTrace": ['  File "/var/task/etl.py", line 22, in handler\n']})


def throttled(request_id: str) -> str:
    return (f"Rate Exceeded. (Service: AWSLambda; Status Code: 429; Error Code: TooManyRequestsException; "
            f"Request ID: {request_id}; Proxy: null)")


@dataclass
class Try:
    """
    One try of a task: how long it ran, and how it ended ('ok', or an error with its cause). request_id is the
    Lambda run's.
    """

    seconds: float = 0.2
    error: str = ""
    cause: str = ""
    request_id: str = ""
    output: Any = None
    wait: float = 0.0


@dataclass
class History:
    """
    Builds one execution's history. Each method adds a state's events after the last one and returns self.
    """

    start: datetime
    input: Any = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.now = self.start
        self.last = self._add("ExecutionStarted", None, executionStartedEventDetails={
            "input": json.dumps(self.input or {}), "roleArn": f"arn:aws:iam::{ACCOUNT}:role/sfn"})

    def _add(self, kind: str, previous: int | None, seconds: float = 0.0, **details: Any) -> int:
        self.now += timedelta(seconds=seconds)
        event = {"timestamp": self.now, "type": kind, "id": len(self.events) + 1, **details}
        if previous is not None:
            event["previousEventId"] = previous
        self.events.append(event)
        return event["id"]

    def _entered(self, kind: str, name: str, previous: int, data: Any = None) -> int:
        return self._add(f"{kind}StateEntered", previous, 0.01, stateEnteredEventDetails={
            "name": name, "input": json.dumps(data if data is not None else {})})

    def _exited(self, kind: str, name: str, previous: int, data: Any = None) -> int:
        return self._add(f"{kind}StateExited", previous, 0.005, stateExitedEventDetails={
            "name": name, "output": json.dumps(data if data is not None else {})})

    def state(self, kind: str, name: str, *, seconds: float = 0.01) -> History:
        """
        A state that calls nothing: Pass, Choice or Wait.
        """
        entered = self._entered(kind, name, self.last)
        self.now += timedelta(seconds=seconds)
        self.last = self._exited(kind, name, entered)
        return self

    def lambda_task(self, name: str, function: str, tries: list[Try], *, data: Any = None) -> History:
        """
        A lambda:invoke task: a TaskScheduled, TaskStarted and TaskSucceeded or TaskFailed per try, then
        TaskStateExited when it succeeded (none when its last try failed: the error moves on to a Catch or ends the
        execution).
        """
        entered = self._entered("Task", name, self.last, data)
        previous = entered
        for attempt in tries:
            scheduled = self._add("TaskScheduled", previous, attempt.wait, taskScheduledEventDetails={
                "resourceType": "lambda", "resource": "invoke", "region": REGION,
                "parameters": json.dumps({"FunctionName": function_arn(function), "Payload": data or {}})})
            started = self._add("TaskStarted", scheduled, 0.02, taskStartedEventDetails={
                "resourceType": "lambda", "resource": "invoke"})
            previous = self._try_end(started, attempt)
        self.last = previous if tries[-1].error else self._exited("Task", name, previous, tries[-1].output)
        return self

    def _try_end(self, started: int, attempt: Try) -> int:
        if attempt.error:
            return self._add("TaskFailed", started, attempt.seconds, taskFailedEventDetails={
                "resourceType": "lambda", "resource": "invoke", "error": attempt.error, "cause": attempt.cause})
        output = {"ExecutedVersion": "$LATEST", "Payload": attempt.output or {}, "StatusCode": 200,
                  "SdkHttpMetadata": {"HttpStatusCode": 200},
                  "SdkResponseMetadata": {"RequestId": attempt.request_id}}
        return self._add("TaskSucceeded", started, attempt.seconds, taskSucceededEventDetails={
            "resourceType": "lambda", "resource": "invoke", "output": json.dumps(output)})

    def sdk_task(self, name: str, resource: str, *, seconds: float = 0.04) -> History:
        """
        An AWS SDK integration task that succeeds ('dynamodb' / 'updateItem').
        """
        service, action = resource.split(":")
        entered = self._entered("Task", name, self.last)
        scheduled = self._add("TaskScheduled", entered, 0.01, taskScheduledEventDetails={
            "resourceType": service, "resource": action, "region": REGION, "parameters": "{}"})
        started = self._add("TaskStarted", scheduled, 0.01, taskStartedEventDetails={
            "resourceType": service, "resource": action})
        done = self._add("TaskSucceeded", started, seconds, taskSucceededEventDetails={
            "resourceType": service, "resource": action, "output": "{}"})
        self.last = self._exited("Task", name, done)
        return self

    def map_state(self, name: str, inner: str, function: str, items: list[Try]) -> History:
        """
        A Map state running one Lambda task per item, one item after another.
        """
        entered = self._entered("Map", name, self.last)
        started = self._add("MapStateStarted", entered, 0.01, mapStateStartedEventDetails={"length": len(items)})
        previous = started
        for index, attempt in enumerate(items):
            iteration = self._add("MapIterationStarted", started, 0.01, mapIterationStartedEventDetails={
                "name": name, "index": index})
            self.last = iteration
            self.lambda_task(inner, function, [attempt])
            ending = "MapIterationFailed" if attempt.error else "MapIterationSucceeded"
            previous = self._add(ending, self.last, 0.01, **{
                f"{ending[0].lower()}{ending[1:]}EventDetails": {"name": name, "index": index}})
        if any(a.error for a in items):
            self.last = self._add("MapStateFailed", previous, 0.01)
            return self
        done = self._add("MapStateSucceeded", previous, 0.01)
        self.last = self._exited("Map", name, done)
        return self

    def fail(self, name: str, error: str, cause: str) -> History:
        entered = self._add("FailStateEntered", self.last, 0.01, stateEnteredEventDetails={"name": name,
                                                                                           "input": "{}"})
        self.last = self._add("ExecutionFailed", entered, 0.01, executionFailedEventDetails={"error": error,
                                                                                            "cause": cause})
        return self

    def failed(self, error: str, cause: str) -> History:
        """
        The execution fails with the last state's error, uncaught.
        """
        self.last = self._add("ExecutionFailed", self.last, 0.01, executionFailedEventDetails={"error": error,
                                                                                             "cause": cause})
        return self

    def succeed(self, output: Any = None) -> History:
        self.last = self._add("ExecutionSucceeded", self.last, 0.01, executionSucceededEventDetails={
            "output": json.dumps(output or {})})
        return self

    def timed_out(self, seconds: float) -> History:
        self.last = self._add("ExecutionTimedOut", self.last, seconds, executionTimedOutEventDetails={
            "error": "States.Timeout"})
        return self

    @property
    def stop(self) -> datetime:
        return self.now


@dataclass
class FakeRun:
    """
    One execution: its machine, name, status and history.
    """

    machine: str
    name: str
    status: str
    history: History
    redrive_count: int = 0

    @property
    def arn(self) -> str:
        return run_arn(self.machine, self.name)

    def item(self) -> dict[str, Any]:
        item = {"executionArn": self.arn, "stateMachineArn": machine_arn(self.machine), "name": self.name,
                "status": self.status, "startDate": self.history.start}
        if self.status != "RUNNING":
            item["stopDate"] = self.history.stop
        if self.redrive_count:
            item["redriveCount"] = self.redrive_count
        return item


class _Checked:
    """
    A boto3 client stand-in answering from methods of its own, every request and answer checked against the
    service model. An operation it has no method for doesn't exist on it. denied: operations refused as
    AccessDenied; holds: an Event per operation that its calls wait for (the background tests hold one back).
    """

    service = ""

    def __init__(self, region: str = REGION):
        model = boto3.client(self.service, region_name=region).meta.service_model
        self.meta = SimpleNamespace(region_name=region, service_model=model)
        self._ops = {xform_name(op): model.operation_model(op) for op in model.operation_names}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.denied: set[str] = set()
        self.holds: dict[str, threading.Event] = {}

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or not hasattr(type(self), f"_{name}"):
            raise AttributeError(name)
        return lambda **params: self._call(name, params)

    def _call(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        operation = self._ops[name]
        report = ParamValidator().validate(params, operation.input_shape)
        assert not report.has_errors(), report.generate_report()
        self.calls.append((name, params))
        hold = self.holds.get(name)
        if hold is not None:
            hold.wait(5)
        if name in self.denied:
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "not authorized"}},
                              operation.name)
        answer = getattr(self, f"_{name}")(**params)
        report = ParamValidator().validate(answer, operation.output_shape)
        assert not report.has_errors(), report.generate_report()
        return answer

    def get_paginator(self, name: str) -> Any:
        def pages(**params: Any) -> Any:
            yield self._call(name, params)
        return SimpleNamespace(paginate=pages)

    def called(self, name: str) -> list[dict[str, Any]]:
        return [params for op, params in self.calls if op == name]


class FakeStepFunctions(_Checked):
    """
    Step Functions' read operations over `machines` (name -> (type, definition, logging)) and `runs`.
    """

    service = "stepfunctions"

    def __init__(self, machines: dict[str, dict[str, Any]], runs: list[FakeRun], region: str = REGION):
        super().__init__(region)
        self.machines = machines
        self.runs = runs

    def _missing(self, code: str, message: str, operation: str) -> ClientError:
        return ClientError({"Error": {"Code": code, "Message": message}}, operation)

    def _list_state_machines(self, **params: Any) -> dict[str, Any]:
        return {"stateMachines": [{"stateMachineArn": machine_arn(name), "name": name,
                                   "type": spec.get("type", "STANDARD"), "creationDate": spec["created"]}
                                  for name, spec in self.machines.items()]}

    def _describe_state_machine(self, stateMachineArn: str, **params: Any) -> dict[str, Any]:
        name = stateMachineArn.rsplit(":", 1)[-1]
        if name not in self.machines:
            raise self._missing("StateMachineDoesNotExist", f"State Machine Does Not Exist: '{stateMachineArn}'",
                                "DescribeStateMachine")
        spec = self.machines[name]
        answer = {"stateMachineArn": stateMachineArn, "name": name, "status": "ACTIVE",
                  "definition": json.dumps(spec["definition"]), "roleArn": f"arn:aws:iam::{ACCOUNT}:role/sfn-{name}",
                  "type": spec.get("type", "STANDARD"), "creationDate": spec["created"],
                  "loggingConfiguration": spec.get("logging", {"level": "OFF", "includeExecutionData": False}),
                  "tracingConfiguration": {"enabled": bool(spec.get("tracing"))}}
        if spec.get("description"):
            answer["description"] = spec["description"]
        return answer

    def _list_executions(self, stateMachineArn: str, statusFilter: str | None = None, maxResults: int = 100,
                         nextToken: str | None = None, **params: Any) -> dict[str, Any]:
        name = stateMachineArn.rsplit(":", 1)[-1]
        if self.machines.get(name, {}).get("type") == "EXPRESS":
            raise self._missing("StateMachineTypeNotSupported", "This operation is not supported by this type of "
                                "state machine", "ListExecutions")
        runs = sorted((r for r in self.runs if r.machine == name and statusFilter in (None, r.status)),
                      key=lambda r: r.history.start, reverse=True)
        begin = int(nextToken or 0)
        page = runs[begin: begin + maxResults]
        answer: dict[str, Any] = {"executions": [r.item() for r in page]}
        if begin + maxResults < len(runs):
            answer["nextToken"] = str(begin + maxResults)
        return answer

    def _run(self, arn: str, operation: str) -> FakeRun:
        found = next((r for r in self.runs if r.arn == arn), None)
        if found is None:
            raise self._missing("ExecutionDoesNotExist", f"Execution Does Not Exist: '{arn}'", operation)
        return found

    def _describe_execution(self, executionArn: str, **params: Any) -> dict[str, Any]:
        run = self._run(executionArn, "DescribeExecution")
        answer = run.item()
        answer["input"] = json.dumps(run.history.input or {})
        ending = run.history.events[-1]
        details = ending.get("executionFailedEventDetails") or ending.get("executionTimedOutEventDetails") or {}
        if details.get("error"):
            answer["error"] = details["error"]
        if details.get("cause"):
            answer["cause"] = details["cause"]
        if run.status in ("FAILED", "TIMED_OUT", "ABORTED"):
            answer["redriveStatus"] = "REDRIVABLE"
        return answer

    def _get_execution_history(self, executionArn: str, maxResults: int = 1000, reverseOrder: bool = False,
                               nextToken: str | None = None, **params: Any) -> dict[str, Any]:
        events = list(self._run(executionArn, "GetExecutionHistory").history.events)
        if reverseOrder:
            events.reverse()
        begin = int(nextToken or 0)
        answer: dict[str, Any] = {"events": events[begin: begin + maxResults]}
        if begin + maxResults < len(events):
            answer["nextToken"] = str(begin + maxResults)
        return answer


class FakeCloudWatch(_Checked):
    """
    GetMetricData over `daily`: (state machine name, metric) -> {day start: value}.
    """

    service = "cloudwatch"

    def __init__(self, daily: dict[tuple[str, str], dict[datetime, float]], region: str = REGION):
        super().__init__(region)
        self.daily = daily

    def _get_metric_data(self, MetricDataQueries: list[dict[str, Any]], StartTime: datetime, EndTime: datetime,
                         **params: Any) -> dict[str, Any]:
        results = []
        for query in MetricDataQueries:
            metric = query["MetricStat"]["Metric"]
            machine = metric["Dimensions"][0]["Value"].rsplit(":", 1)[-1]
            values = {day: value for day, value in self.daily.get((machine, metric["MetricName"]), {}).items()
                      if StartTime <= day < EndTime}
            results.append({"Id": query["Id"], "Label": metric["MetricName"], "Timestamps": list(values),
                            "Values": list(values.values()), "StatusCode": "Complete"})
        return {"MetricDataResults": results}


LAMBDA_RETRY = {"ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException",
                                "Lambda.TooManyRequestsException"], "IntervalSeconds": 1, "MaxAttempts": 3,
                "BackoffRate": 2}


def invoke(function: str, **state: Any) -> dict[str, Any]:
    """
    A lambda:invoke Task state calling `function`.
    """
    return {"Type": "Task", "Resource": "arn:aws:states:::lambda:invoke",
            "Parameters": {"FunctionName": function_arn(function), "Payload.$": "$"}, **state}


ORDER_PIPELINE = {
    "Comment": "Takes an order from validation to the customer's email",
    "StartAt": "ValidateOrder",
    "States": {
        "ValidateOrder": invoke("validate-order", Retry=[LAMBDA_RETRY], Next="IsPriority"),
        "IsPriority": {"Type": "Choice", "Choices": [{"Variable": "$.priority", "BooleanEquals": True,
                                                      "Next": "BackorderWait"}], "Default": "ReserveStock"},
        "BackorderWait": {"Type": "Wait", "Seconds": 3600, "Next": "ChargeCard"},
        "ReserveStock": {"Type": "Task", "Resource": "arn:aws:states:::dynamodb:updateItem",
                         "Parameters": {"TableName": "inventory"}, "Next": "ChargeCard"},
        "ChargeCard": invoke("charge-card", Retry=[{"ErrorEquals": ["Lambda.TooManyRequestsException"],
                                                    "IntervalSeconds": 1, "MaxAttempts": 2, "BackoffRate": 2}],
                             Next="LoadOrders"),
        "LoadOrders": invoke("orders-etl", Catch=[{"ErrorEquals": ["States.ALL"], "Next": "OrderFailed"}],
                             Next="ProcessItems"),
        "ProcessItems": {"Type": "Map", "ItemsPath": "$.items", "MaxConcurrency": 4, "ItemProcessor": {
            "StartAt": "ResizeImage", "States": {"ResizeImage": invoke("resize-image", Retry=[LAMBDA_RETRY],
                                                                       End=True)}}, "Next": "NotifyCustomer"},
        "NotifyCustomer": {"Type": "Task", "Resource": "arn:aws:states:::sns:publish",
                           "Parameters": {"TopicArn": f"arn:aws:sns:{REGION}:{ACCOUNT}:order-updates"}, "End": True},
        "OrderFailed": {"Type": "Fail", "Error": "OrderFailed", "Cause": "The order couldn't be loaded"},
    },
}

REFUND_APPROVALS = {
    "Comment": "Asks a manager to approve a refund, then issues it",
    "StartAt": "AskManager",
    "States": {
        "AskManager": {"Type": "Task", "Resource": "arn:aws:states:::sqs:sendMessage.waitForTaskToken",
                       "Parameters": {"QueueUrl": "https://sqs.us-east-1.amazonaws.com/123456789012/approvals"},
                       "Next": "IssueRefund"},
        "IssueRefund": invoke("issue-refund", Retry=[LAMBDA_RETRY], End=True),
    },
}

IMAGE_RESIZE = {
    "StartAt": "Resize",
    "States": {"Resize": invoke("resize-image", Retry=[LAMBDA_RETRY], Next="Done"), "Done": {"Type": "Succeed"}},
}

KB_SYNC = {
    "StartAt": "StartIngestion",
    "States": {"StartIngestion": {"Type": "Task", "Resource": "arn:aws:states:::aws-sdk:bedrockagent:startIngestionJob",
                                  "Parameters": {"KnowledgeBaseId": "KB12345678", "DataSourceId": "DS12345678"},
                                  "End": True}},
}

FAILED_RUN = "c0ffee00-1d2e-4f3a-9b8c-7d6e5f4a3b2c"

ORDER = {"order_id": "88219", "customer": {"email": "dana@example.com"}, "items": ["a.jpg", "b.jpg", "c.jpg"]}


def _rid(n: int) -> str:
    return f"{n:08x}-0000-4000-8000-{n:012x}"


def _good_order(start: datetime, n: int) -> History:
    history = History(start, ORDER)
    history.lambda_task("ValidateOrder", "validate-order", [Try(0.21, request_id=_rid(n))], data=ORDER)
    history.state("Choice", "IsPriority").sdk_task("ReserveStock", "dynamodb:updateItem")
    history.lambda_task("ChargeCard", "charge-card", [Try(0.85, request_id=_rid(n + 1))])
    history.lambda_task("LoadOrders", "orders-etl", [Try(5.7, request_id=_rid(n + 2))])
    history.map_state("ProcessItems", "ResizeImage", "resize-image", [Try(9.5, request_id=_rid(n + 3 + i))
                                                                      for i in range(3)])
    return history.sdk_task("NotifyCustomer", "sns:publish").succeed({"notified": True})


def _keyerror_order(start: datetime, request_id: str) -> History:
    history = History(start, ORDER)
    history.lambda_task("ValidateOrder", "validate-order", [Try(0.21, request_id=_rid(1))], data=ORDER)
    history.state("Choice", "IsPriority").sdk_task("ReserveStock", "dynamodb:updateItem")
    history.lambda_task("ChargeCard", "charge-card", [
        Try(0.04, "Lambda.TooManyRequestsException", throttled("9be1a2b3-0000-4000-8000-000000000001")),
        Try(0.04, "Lambda.TooManyRequestsException", throttled("9be1a2b3-0000-4000-8000-000000000002"), wait=1.0),
        Try(0.85, request_id=_rid(2), wait=2.0)])
    history.lambda_task("LoadOrders", "orders-etl", [
        Try(1.73, "KeyError", lambda_error("KeyError", "'customer_id'", request_id))], data=ORDER)
    return history.fail("OrderFailed", "OrderFailed", "The order couldn't be loaded")


def _throttled_order(start: datetime) -> History:
    history = History(start, ORDER)
    history.lambda_task("ValidateOrder", "validate-order", [Try(0.21, request_id=_rid(3))], data=ORDER)
    history.state("Choice", "IsPriority").sdk_task("ReserveStock", "dynamodb:updateItem")
    tries = [Try(0.04, "Lambda.TooManyRequestsException", throttled(f"9be1a2b3-0000-4000-8000-00000000001{i}"),
                 wait=float(i)) for i in range(3)]
    history.lambda_task("ChargeCard", "charge-card", tries)
    return history.failed("Lambda.TooManyRequestsException", tries[-1].cause)


def _stuck_order(start: datetime) -> History:
    history = History(start, ORDER)
    history.lambda_task("ValidateOrder", "validate-order", [Try(0.21, request_id=_rid(4))], data=ORDER)
    history.state("Choice", "IsPriority").sdk_task("ReserveStock", "dynamodb:updateItem")
    history.lambda_task("ChargeCard", "charge-card", [Try(0.85, request_id=_rid(5))])
    history.lambda_task("LoadOrders", "orders-etl", [Try(5.1, request_id=_rid(6))])
    entered = history._entered("Map", "ProcessItems", history.last)
    started = history._add("MapStateStarted", entered, 0.01, mapStateStartedEventDetails={"length": 2})
    history.last = history._add("MapIterationStarted", started, 0.01, mapIterationStartedEventDetails={
        "name": "ProcessItems", "index": 0})
    task = history._entered("Task", "ResizeImage", history.last)
    history._add("TaskScheduled", task, 0.01, taskScheduledEventDetails={
        "resourceType": "lambda", "resource": "invoke", "region": REGION,
        "parameters": json.dumps({"FunctionName": function_arn("resize-image")})})
    history.last = history._add("TaskStarted", history.events[-1]["id"], 0.02, taskStartedEventDetails={
        "resourceType": "lambda", "resource": "invoke"})
    return history.timed_out(240)


def _approval(start: datetime) -> History:
    history = History(start, {"refund": 42})
    entered = history._entered("Task", "AskManager", history.last)
    scheduled = history._add("TaskScheduled", entered, 0.01, taskScheduledEventDetails={
        "resourceType": "sqs", "resource": "sendMessage.waitForTaskToken", "region": REGION, "parameters": "{}"})
    history.last = history._add("TaskStarted", scheduled, 0.02, taskStartedEventDetails={
        "resourceType": "sqs", "resource": "sendMessage.waitForTaskToken"})
    return history


def scene(now: datetime) -> tuple[dict[str, dict[str, Any]], list[FakeRun], dict[tuple[str, str], dict[datetime,
                                                                                                    float]]]:
    """
    acme's state machines: order-pipeline (Standard, failing at LoadOrders on orders without a customer_id),
    refund-approvals (waits for a manager with no timeout), image-resize-express (Express, logging everything) and
    kb-sync (not started in a week); their executions; and their CloudWatch numbers for the last 7 days.
    """
    created = now - timedelta(days=200)
    machines = {
        "order-pipeline": {"definition": ORDER_PIPELINE, "created": created,
                           "logging": {"level": "ERROR", "includeExecutionData": False, "destinations": [
                               {"cloudWatchLogsLogGroup": {"logGroupArn": f"arn:aws:logs:{REGION}:{ACCOUNT}:"
                                                                          "log-group:/aws/vendedlogs/states/orders"}}]}},
        "refund-approvals": {"definition": REFUND_APPROVALS, "created": created + timedelta(days=90)},
        "image-resize-express": {"definition": IMAGE_RESIZE, "type": "EXPRESS", "created": created,
                                 "logging": {"level": "ALL", "includeExecutionData": True, "destinations": [
                                     {"cloudWatchLogsLogGroup": {"logGroupArn": f"arn:aws:logs:{REGION}:{ACCOUNT}:"
                                                                                "log-group:/aws/states/resize"}}]}},
        "kb-sync": {"definition": KB_SYNC, "created": created + timedelta(days=150),
                    "description": "Re-syncs the support-docs knowledge base"},
    }
    ago = lambda **kw: now - timedelta(**kw)  # noqa: E731
    runs = [
        FakeRun("order-pipeline", "ord-91c2", "RUNNING", History(ago(minutes=2), ORDER)),
        FakeRun("order-pipeline", "ord-25a8", "SUCCEEDED", _good_order(ago(minutes=40), 100)),
        FakeRun("order-pipeline", "ord-7f3a", "FAILED", _keyerror_order(ago(minutes=58), FAILED_RUN)),
        FakeRun("order-pipeline", "ord-cfae", "SUCCEEDED", _good_order(ago(hours=1, minutes=20), 200)),
        FakeRun("order-pipeline", "ord-0b17", "TIMED_OUT", _stuck_order(ago(hours=2, minutes=40))),
        FakeRun("order-pipeline", "ord-5d21", "FAILED", _keyerror_order(ago(hours=5), _rid(901))),
        FakeRun("order-pipeline", "ord-a410", "SUCCEEDED", _good_order(ago(hours=9), 300)),
        FakeRun("order-pipeline", "ord-77e0", "FAILED", _throttled_order(ago(days=1, hours=2))),
        FakeRun("order-pipeline", "ord-3c9b", "FAILED", _keyerror_order(ago(days=3), _rid(902)), redrive_count=1),
        FakeRun("order-pipeline", "ord-e2f4", "FAILED", _keyerror_order(ago(days=20), _rid(903))),
        FakeRun("refund-approvals", "refund-42", "RUNNING", _approval(ago(days=2))),
    ]
    end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    days = [end - timedelta(days=d) for d in range(7, 0, -1)]
    per_day = {
        "order-pipeline": {"ExecutionsStarted": 177, "ExecutionsSucceeded": 168, "ExecutionsFailed": 6,
                           "ExecutionsTimedOut": 1, "ExecutionTime": 38_200},
        "image-resize-express": {"ExecutionsStarted": 26_057, "ExecutionsSucceeded": 26_000, "ExecutionsFailed": 57,
                                 "ExecutionTime": 410},
        "refund-approvals": {"ExecutionsStarted": 3, "ExecutionsSucceeded": 3, "ExecutionTime": 3 * 3_600_000},
    }
    daily = {(machine, metric): {day: float(value) for day in days}
             for machine, metrics in per_day.items() for metric, value in metrics.items()}
    return machines, runs, daily


def clients(now: datetime) -> dict[str, Any]:
    """
    The fake Step Functions, CloudWatch and CloudWatch Logs clients over scene(now) (the Logs one holds
    image-resize-express's log group), for StepFunctionsAnalyzer(clients=...).
    """
    machines, runs, daily = scene(now)
    lines = [line for arn, history in express_runs(now).items() for line in express_log_lines(arn, history)]
    return {"stepfunctions": FakeStepFunctions(machines, runs), "cloudwatch": FakeCloudWatch(daily),
            "logs": FakeLogs({"/aws/states/resize": lines})}


def lambda_lines(start: datetime, request_id: str = FAILED_RUN) -> list[tuple[datetime, str]]:
    """
    What orders-etl logged in the run that broke ord-7f3a: START, its lines, the KeyError and REPORT.
    """
    at = lambda seconds: start + timedelta(seconds=seconds)  # noqa: E731
    return [
        (at(0), f"START RequestId: {request_id} Version: $LATEST"),
        (at(0.05), f"{at(0.05).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]}Z\t{request_id}\treading "
                   "s3://acme-uploads/orders/2026-10-06/batch-002.json"),
        (at(1.72), f"[ERROR]\t{at(1.72).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]}Z\t{request_id}\tKeyError: "
                   "'customer_id'\nTraceback (most recent call last):\n  File "
                   "\"/var/task/etl.py\", line 22, in handler\n    table.put_item(Item={\"pk\": row[\"customer_id\"]})"),
        (at(1.73), f"END RequestId: {request_id}"),
        (at(1.73), f"REPORT RequestId: {request_id}\tDuration: 1730.00 ms\tBilled Duration: 1731 ms\tMemory Size: "
                   "1024 MB\tMax Memory Used: 151 MB"),
    ]


def _stamp(moment: datetime) -> str:
    return f"{moment.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]}Z"


def ok_run_lines(start: datetime, request_id: str, said: list[str], *, ms: float, used: int,
                 init_ms: float | None = None) -> list[tuple[datetime, str]]:
    """
    A run that went well, as Lambda logs it: a start-up line on a cold start, START, what it said, END and REPORT.
    """
    at = lambda seconds: start + timedelta(seconds=seconds)  # noqa: E731
    lines = []
    if init_ms:
        lines.append((at(-init_ms / 1000), "INIT_START Runtime Version: python:3.12.v40\tRuntime Version ARN: "
                                           "arn:aws:lambda:us-east-1::runtime:abc"))
    lines.append((at(0), f"START RequestId: {request_id} Version: $LATEST"))
    step = ms / 1000 / (len(said) + 1)
    for i, text in enumerate(said, start=1):
        lines.append((at(step * i), f"{_stamp(at(step * i))}\t{request_id}\t{text}"))
    end = at(ms / 1000)
    init = f"\tInit Duration: {init_ms:.2f} ms" if init_ms else ""
    lines += [(end, f"END RequestId: {request_id}"),
              (end, f"REPORT RequestId: {request_id}\tDuration: {ms:.2f} ms\tBilled Duration: {int(ms) + 1} ms\t"
                    f"Memory Size: 512 MB\tMax Memory Used: {used} MB{init}")]
    return lines


def story_logs(history: History) -> dict[str, list[tuple[datetime, str]]]:
    """
    What validate-order, charge-card and orders-etl logged in ord-7f3a's runs (each function's log lines, by
    function), timed from its history: charge-card's last try a cold start, orders-etl's run the KeyError.
    """
    scheduled = {}
    for event in history.events:
        if event["type"] == "TaskScheduled":
            parameters = json.loads(event["taskScheduledEventDetails"]["parameters"])
            name = str(parameters.get("FunctionName", "")).rsplit(":", 1)[-1]
            scheduled.setdefault(name, []).append(event["timestamp"])
    return {
        "validate-order": ok_run_lines(scheduled["validate-order"][0] + timedelta(seconds=0.03), _rid(1),
                                       ["validated order 88219: 3 items, total $184.20"], ms=180, used=71),
        "charge-card": ok_run_lines(scheduled["charge-card"][-1] + timedelta(seconds=0.33), _rid(2),
                                    ["charging card ••4242 $184.20", "charged: ch_3Q8xk2"], ms=520, used=96,
                                    init_ms=310),
        "orders-etl": lambda_lines(scheduled["orders-etl"][0]),
    }


_PATTERN_TERM_RE = re.compile(r'\$\.(\w+) = "([^"]*)"')

_LOG_PAGE = 50


class FakeLogs(_Checked):
    """
    CloudWatch Logs' FilterLogEvents over `groups` (log group name -> [(time, message)]): the time window, and the
    JSON filter patterns the Express reads use ('{ $.field = "value" || ... }'), a page of _LOG_PAGE at a time.
    """

    service = "logs"

    def __init__(self, groups: dict[str, list[tuple[datetime, str]]], region: str = REGION):
        super().__init__(region)
        self.groups = groups

    @staticmethod
    def _matches(message: str, pattern: str | None) -> bool:
        if not pattern:
            return True
        try:
            line = json.loads(message)
        except ValueError:
            return False
        return any(str(line.get(field)) == value for field, value in _PATTERN_TERM_RE.findall(pattern))

    def _filter_log_events(self, logGroupName: str, startTime: int = 0, endTime: int = 2**62,
                           filterPattern: str | None = None, nextToken: str | None = None,
                           **params: Any) -> dict[str, Any]:
        if logGroupName not in self.groups:
            raise ClientError({"Error": {"Code": "ResourceNotFoundException",
                                         "Message": "The specified log group does not exist."}}, "FilterLogEvents")
        found = [(moment, message) for moment, message in sorted(self.groups[logGroupName])
                 if startTime <= int(moment.timestamp() * 1000) < endTime and self._matches(message, filterPattern)]
        begin = int(nextToken or 0)
        page = found[begin: begin + _LOG_PAGE]
        answer: dict[str, Any] = {"events": [
            {"logStreamName": "states/x", "timestamp": int(m.timestamp() * 1000), "message": text,
             "ingestionTime": int(m.timestamp() * 1000), "eventId": str(begin + i)}
            for i, (m, text) in enumerate(page)]}
        if begin + _LOG_PAGE < len(found):
            answer["nextToken"] = str(begin + _LOG_PAGE)
        return answer


def express_arn(name: str, machine: str = "image-resize-express") -> str:
    return f"arn:aws:states:{REGION}:{ACCOUNT}:express:{machine}:{name}:{name}-0000-4000-8000-{name[-4:]:0>12}"


def express_log_lines(arn: str, history: History) -> list[tuple[datetime, str]]:
    """
    An execution's history as Step Functions logs it for an Express workflow: one JSON line per event.
    """
    lines = []
    for event in history.events:
        details = next((value for key, value in event.items() if key.endswith("EventDetails")), {})
        lines.append((event["timestamp"], json.dumps({
            "id": str(event["id"]), "type": event["type"], "details": details,
            "previous_event_id": str(event.get("previousEventId", 0)),
            "event_timestamp": str(int(event["timestamp"].timestamp() * 1000)), "execution_arn": arn})))
    return lines


RESIZE_FAILED_RUN = "beef0000-1d2e-4f3a-9b8c-7d6e5f4a3b2c"


def express_runs(now: datetime) -> dict[str, History]:
    """
    image-resize-express's executions in the last hour, by ARN: three that resized their picture and one where
    resize-image ran out of memory.
    """
    runs: dict[str, History] = {}
    for i, minutes in enumerate((4, 18, 33)):
        history = History(now - timedelta(minutes=minutes), {"key": f"photos/{i}.jpg"})
        history.lambda_task("Resize", "resize-image", [Try(0.38, request_id=_rid(500 + i))])
        runs[express_arn(f"rsz-{i:04d}")] = history.state("Succeed", "Done").succeed({"resized": True})
    broken = History(now - timedelta(minutes=11), {"key": "photos/huge.tiff"})
    cause = lambda_error("Runtime.OutOfMemory", "RequestId: beef0000 Error: Runtime exited with error: signal: killed",
                         RESIZE_FAILED_RUN)
    broken.lambda_task("Resize", "resize-image", [Try(2.9, "Runtime.OutOfMemory", cause)])
    runs[express_arn("rsz-0099")] = broken.failed("Runtime.OutOfMemory", cause)
    return runs
