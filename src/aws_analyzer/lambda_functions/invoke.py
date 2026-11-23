"""
LambdaAnalyzer's one call that changes something: invoke(), which runs a function once with an event the user gives
and brings back its answer and log lines together. Everything else in the package only reads.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any, Callable

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .._kit.errors import _error_code
from .._kit.fmt import _utcnow

from .models import Function, LogEvent
from .analysis.invoke import Invoked, call_cost, decode_tail, event_payload, tail_events, tail_is_whole

_READ_SLACK = 30

_LOG_POLL_SECONDS = 2

_LOG_LOOK_BACK = timedelta(minutes=1)


class _InvokeRuns:
    """
    LambdaAnalyzer's invoke() (a mixin: it uses the analyzer's session, clients, prices and log reads).
    """

    session: Any
    prices: dict[str, float]
    _config: Config
    _given: dict[str, Any]
    _lock: Any
    function: Callable[..., Function]
    locate: Callable[..., tuple[str, str | None, str]]
    _service: Callable[..., Any]
    _log_page: Callable[..., Any]

    def _invoke_client(self, region: str | None, timeout: int) -> Any:
        """
        A Lambda client for one call: it waits as long as the function may run, and never retries, since a retry
        would run the function a second time. A client passed in (clients={'lambda': ...}) is used as it is.
        """
        if self._given.get("lambda") is not None:
            return self._service("lambda", region)
        once = Config(read_timeout=timeout + _READ_SLACK, retries={"total_max_attempts": 1, "mode": "standard"})
        with self._lock:
            return self.session.client("lambda", region_name=region, config=self._config.merge(once))

    def _sent(self, fn: Function, payload: bytes, *, qualifier: str | None, dry_run: bool) -> Invoked:
        """
        Calls the function once, synchronously, asking for the last 4 KB of its log with the answer.
        """
        params: dict[str, Any] = {
            "FunctionName": fn.name, "Payload": payload, "LogType": "None" if dry_run else "Tail",
            "InvocationType": "DryRun" if dry_run else "RequestResponse",
        }
        if qualifier:
            params["Qualifier"] = qualifier
        client = self._invoke_client(fn.region or None, fn.timeout)
        started = _utcnow()
        response = client.invoke(**params)  # runs-on-request: invoke() is the one command that runs a function
        ended = _utcnow()
        body = response.get("Payload")
        data = body.read() if hasattr(body, "read") else bytes(body or b"")
        meta = response.get("ResponseMetadata") or {}
        tail = decode_tail(response.get("LogResult"))
        request_id = str(meta.get("RequestId") or "")
        return Invoked(
            fn, payload, qualifier=qualifier, started=started, ended=ended,
            status_code=int(response.get("StatusCode") or 0), request_id=request_id,
            executed_version=str(response.get("ExecutedVersion") or ""),
            function_error=response.get("FunctionError"), payload=data, tail=tail, dry_run=dry_run,
            events=tail_events(tail, request_id=request_id, started=started),
        )

    def _whole_run(self, invoked: Invoked, wait: float) -> list[LogEvent] | None:
        """
        The run's every line from CloudWatch Logs, looked for every couple of seconds for up to `wait` seconds (lines
        reach it a few seconds after the run ends); None when they didn't arrive in time.
        """
        fn = invoked.function
        give_up = time.monotonic() + wait
        start = (invoked.started or _utcnow()) - _LOG_LOOK_BACK
        while True:
            end = _utcnow() + _LOG_LOOK_BACK
            page = self._log_page(fn, start, end, request_id=invoked.request_id, limit=10_000)
            if page.errors:
                invoked.errors.update(page.errors)
                return None
            has_report = any(e.message.lstrip().startswith("REPORT") or '"platform.report"' in e.message
                             for e in page.events)
            if page.events and has_report:
                return page.events
            if time.monotonic() + _LOG_POLL_SECONDS > give_up:
                return None
            time.sleep(_LOG_POLL_SECONDS)

    def invoke(
        self,
        ref: str,
        event: Any = None,
        *,
        region: str | None = None,
        wait_logs: float = 10,
        dry_run: bool = False,
    ) -> Invoked:
        """
        Runs a function once with `event` and waits for it (like the console's Test button), then returns what came
        back (Invoked): its answer, whether it failed, its run time, memory and cost, and its log lines. event= is a
        dict, a list, JSON text or None ({}). The last 4 KB of its log come back with the answer; when the log is
        longer, the whole run is read from CloudWatch Logs, waiting up to `wait_logs` seconds for it to arrive
        (wait_logs=0 doesn't wait). dry_run=True only checks that you may invoke it. This runs the function's code,
        with whatever it writes or sends: it's the one call in aws_analyzer that changes something.
        """
        payload = event_payload(event)
        _, qualifier, _ = self.locate(ref, region)
        fn = self.function(ref, region=region)
        invoked = self._sent(fn, payload, qualifier=qualifier, dry_run=dry_run)
        if dry_run:
            return invoked
        if not tail_is_whole(invoked.tail) and wait_logs > 0 and invoked.request_id:
            try:
                events = self._whole_run(invoked, wait_logs)
            except ClientError as exc:
                invoked.errors["logs"] = _error_code(exc)
                events = None
            except BotoCoreError as exc:
                invoked.errors["logs"] = type(exc).__name__
                events = None
            if events:
                invoked.events, invoked.logs_from = events, "cloudwatch"
        report = invoked.report
        invoked.cost = call_cost(fn, report.billed if report is not None else None, self.prices)
        return invoked
