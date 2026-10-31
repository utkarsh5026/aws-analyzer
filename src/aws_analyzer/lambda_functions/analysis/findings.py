"""
What's wrong with a function or the account, why it matters and what to do about it.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Iterable

from ..._kit.fmt import _count, _fmt_dt, _utcnow, human_age, human_money, human_size

from ..prices import LAMBDA_PRICES
from ..helpers import GB, MAX_TIMEOUT, MB, MEMORY_STEPS, _in_days, _midnight, _pct, human_ms
from ..models import AccountLimits, ChangeEffect, Function, FunctionDetail, FunctionMetrics, RuntimeStatus
from .text import _cli, _reason, _region_flag, _window
from .functions import runtime_status
from .cost import function_monthly_cost, log_storage_monthly_cost, provisioned_monthly_cost


_SECRET_NAME_RE = re.compile(r"PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_?KEY|ACCESS_?KEY|CREDENTIAL", re.I)

_POINTER_NAME_RE = re.compile(
    r"_(ARN|NAME|ID|PATH|URL|URI|PARAM|PARAMETER|REGION|TTL|HEADER|FILE|LENGTH|EXPIRY|SECONDS|ENDPOINT|TYPE)$", re.I
)


def secret_like(names: Iterable[str]) -> list[str]:
    """
    Environment variable names that look like they hold a secret (DB_PASSWORD, API_KEY), leaving out the ones
    that point at a secret instead (DB_SECRET_ARN, TOKEN_PARAMETER_NAME).
    """
    return [n for n in names if _SECRET_NAME_RE.search(n) and not _POINTER_NAME_RE.search(n)]


def _next_memory(memory: int) -> int:
    return next((step for step in MEMORY_STEPS if step > memory), min(memory * 2, 10240))


def _next_timeout(timeout: int) -> int:
    return min(MAX_TIMEOUT, max(timeout * 2, timeout + 3))


def _nice_timeout(longest_ms: float) -> int:
    """
    A timeout with room above the longest run seen: about 3x it, rounded to a round number of seconds.
    """
    want = longest_ms / 1000 * 3
    return next((s for s in (3, 5, 10, 15, 30, 60, 120, 300, 600, MAX_TIMEOUT) if s >= want), MAX_TIMEOUT)


def _runtime_findings(fn: Function, status: RuntimeStatus) -> list[tuple[str, str]]:
    if status.state not in ("deprecated", "blocked", "ending"):
        return []
    target = status.upgrade or "a supported runtime"
    move = (
        f"test the code on it, then aws lambda update-function-configuration {_cli(fn)} --runtime {status.upgrade}"
        if status.upgrade else "test the code on it, then change the runtime"
    )
    if status.state == "blocked":
        return [("warn", (
            f"{fn.runtime} lost support on {status.deprecated}, and since {status.block_update} Lambda blocks "
            f"updates to functions on it: it keeps running, without security patches, and can't be changed. Move "
            f"it to {target}, as a new function if the update is refused."
        ))]
    if status.state == "deprecated":
        blocked = f", and from {status.block_update} Lambda blocks updates to it" if status.block_update else ""
        return [("warn", (
            f"{fn.runtime} reached end of support on {status.deprecated}: AWS no longer patches it{blocked}. "
            f"Move it to {target}: {move}."
        ))]
    blocked = f", and from {status.block_update} Lambda blocks updates" if status.block_update else ""
    return [("warn", (
        f"{fn.runtime} reaches end of support on {status.deprecated} ({_in_days(status.days_left or 0)}): after "
        f"that AWS stops patching it{blocked}. Plan the move to {target}: {move}."
    ))]


def _day_age(moment: datetime | None, now: datetime) -> str:
    """
    'today', 'yesterday' or '5d ago', by UTC calendar day.
    """
    if moment is None:
        return "-"
    days = (now.astimezone(timezone.utc).date() - moment.astimezone(timezone.utc).date()).days
    return "today" if days <= 0 else "yesterday" if days == 1 else f"{days}d ago"


def _last_seen(moment: datetime | None, metrics: FunctionMetrics, now: datetime) -> str:
    """
    ', the last today' / ', the last 3d ago' (daily numbers) or ', the last 2h ago' (hourly).
    """
    if moment is None:
        return ""
    return f", the last {_day_age(moment, now) if metrics.period >= 86400 else human_age(moment, now)}"


RECENT_DAYS = 3  # how far back "lately" reaches when daily numbers are checked for errors on the rise


def _recent(metrics: FunctionMetrics, now: datetime) -> tuple[float, float] | None:
    """
    (calls, errors) over the last RECENT_DAYS days of daily numbers; None for hourly numbers or short windows.
    """
    if metrics.period < 86400 or metrics.days < 2 * RECENT_DAYS:
        return None
    start = _midnight(now) - timedelta(days=RECENT_DAYS - 1)
    rows = [d for d in metrics.daily if d.start >= start]
    return sum(d.invocations for d in rows), sum(d.errors for d in rows)


def _errors_level(metrics: FunctionMetrics | None, now: datetime) -> str | None:
    """
    'warn' when a function's failed calls are worth acting on (at least 10, and 1% of its calls over the window
    or over the last few days, the last within a week), 'info' when they stopped earlier or are many (100) but a
    small share, None when there are too few to mention.
    """
    if metrics is None or not metrics.invocations or metrics.errors < 1:
        return None
    recent = _recent(metrics, now)
    high = metrics.errors >= 10 and metrics.errors / metrics.invocations >= 0.01
    if recent and recent[0] and recent[1] >= 10 and recent[1] / recent[0] >= 0.01:
        high = True
    if not high:
        return "info" if metrics.errors >= 100 else None
    lately = metrics.last_error is not None and now - metrics.last_error <= timedelta(days=7)
    return "warn" if lately else "info"


def _metrics_window(metrics: FunctionMetrics) -> str:
    """
    '30 days' for daily numbers (whole UTC days, today's part included), '24 hours' for hourly ones.
    """
    days = math.ceil(metrics.days - 1e-6) if metrics.period >= 86400 else metrics.days
    return _window(days * 86400)


def _times(count: int) -> str:
    return "once" if count == 1 else "twice" if count == 2 else f"{count:,} times"


def _longer(fn: Function) -> str:
    """
    What to do about runs that need more time than the timeout gives them.
    """
    if fn.timeout >= MAX_TIMEOUT:
        return (f"it's already at the most Lambda allows ({MAX_TIMEOUT} s), so split the work into smaller pieces, "
                "or run it somewhere without the limit (Step Functions, ECS or Batch)")
    return f"aws lambda update-function-configuration {_cli(fn)} --timeout {_next_timeout(fn.timeout)}"


CHANGE_BEFORE_DAYS = 7

_CHANGE_JUMP = 3

_CHANGE_ERRORS = 10

_CHANGE_RATE = 0.01


def change_effect(metrics: FunctionMetrics | None, changed: datetime | None, now: datetime | None = None
                  ) -> ChangeEffect | None:
    """
    A function's calls and failed calls in the days before it was last changed (at most CHANGE_BEFORE_DAYS) and in
    the days since, from CloudWatch's daily numbers (whole UTC days: the day of the change counts as after only when
    nothing came after it yet). None for hourly numbers, a change outside the window, or no calls before it.
    """
    now = now or _utcnow()
    if metrics is None or changed is None or metrics.period < 86400 or changed > now:
        return None
    change_day = _midnight(changed)
    first_day = _midnight(now) - timedelta(days=math.ceil(metrics.days - 1e-6) - 1)
    if change_day <= first_day:
        return None
    since = max(first_day, change_day - timedelta(days=CHANGE_BEFORE_DAYS))
    before = [d for d in metrics.daily if since <= d.start < change_day]
    after = [d for d in metrics.daily if d.start > change_day]
    if not sum(d.invocations for d in after):
        after = [d for d in metrics.daily if d.start >= change_day]
    calls_before = sum(d.invocations for d in before)
    if not calls_before:
        return None
    return ChangeEffect(changed, calls_before, sum(d.errors for d in before), sum(d.invocations for d in after),
                        sum(d.errors for d in after), (change_day - since).days)


def _change_findings(fn: Function, metrics: FunctionMetrics, detail: FunctionDetail, now: datetime
                     ) -> list[tuple[str, str]]:
    """
    A warning when calls began to fail after the function was last changed: at least _CHANGE_ERRORS failed calls
    since, at least _CHANGE_RATE of them, and _CHANGE_JUMP times the share before.
    """
    effect = change_effect(metrics, fn.last_modified, now)
    if effect is None:
        return []
    after, before = effect.rate_after, effect.rate_before
    jumped = after >= _CHANGE_JUMP * before and after - before >= _CHANGE_RATE
    if effect.errors_after < _CHANGE_ERRORS or after < _CHANGE_RATE or not jumped:
        return []
    name = fn.name
    look = (f"diff('{name}') shows what changed against the version before it"
            if detail.versions else f"code('{name}') shows the code it runs now")
    return [("warn", (
        f"Its calls began to fail after it was last changed ({_fmt_dt(effect.changed, zone=True)}, "
        f"{human_age(effect.changed, now)}): {_pct(after)} failed since ({_count(round(effect.errors_after))} of "
        f"{_count(round(effect.calls_after))}), against {_pct(before)} in the {effect.days_before} days before "
        f"(CloudWatch, by UTC day). The change may have broken it: {look}, and errors('{name}') groups the errors by "
        "cause."
    ))]


def _usage_findings(
    fn: Function, metrics: FunctionMetrics, detail: FunctionDetail, prices: dict[str, float], now: datetime
) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    window = _metrics_window(metrics)
    name, cli = fn.name, _cli(fn)

    def last(moment: datetime | None) -> str:
        return _last_seen(moment, metrics, now)

    if not metrics.invocations:
        if not detail.provisioned:  # provisioned concurrency has its own finding, with the cost
            unused = not detail.triggers and not {"policy", "triggers"} & set(detail.errors)
            found.append(("info", (
                f"It wasn't called in the last {window}"
                + (
                    ", and nothing triggers it (no event source mapping or resource policy): it may be unused, or "
                    f"only called directly. If it's no longer needed, keep its code (code('{name}')), then delete "
                    f"it: aws lambda delete-function {cli}."
                    if unused else "."
                )
            )))
        return found
    errors, calls = metrics.errors, metrics.invocations
    rate = errors / calls
    level = _errors_level(metrics, now)
    if level:
        recent = _recent(metrics, now)
        rising = ""
        if recent and recent[0] and recent[1] >= 10 and recent[1] / recent[0] >= max(0.01, 2 * rate):
            rising = (f", and rising: {_pct(recent[1] / recent[0])} of calls failed in the last {RECENT_DAYS} days "
                      f"({_count(round(recent[1]))} of {_count(round(recent[0]))})")
        found.append((level, (
            f"{_count(round(errors))} of {_count(round(calls))} calls failed ({_pct(rate)}) in the last {window}"
            f"{last(metrics.last_error)}{rising}: errors('{name}') groups them by cause."
        )))
    found += _change_findings(fn, metrics, detail, now)
    if metrics.throttles >= 1:
        found.append(("warn", (
            f"{_count(round(metrics.throttles))} calls were throttled in the last {window}"
            f"{last(metrics.last_throttle)}: more arrived than it may run at once (its reserved concurrency, or the "
            f"region's limit), so they were turned away or retried later. function_info('{name}') shows its "
            "concurrency."
        )))
    if metrics.duration_max and fn.timeout:
        share = metrics.duration_max / (fn.timeout * 1000)
        if share >= 0.9:
            found.append(("warn", (
                f"Its longest run took {human_ms(metrics.duration_max)} of its {fn.timeout} s timeout: runs that "
                f"need longer are cut off and fail. performance('{name}') shows how close they come; if they need "
                f"more time: {_longer(fn)}."
            )))
        elif fn.timeout >= 300 and calls >= 20 and share <= 0.1:
            suggested = _nice_timeout(metrics.duration_max)
            found.append(("info", (
                f"Its timeout is {fn.timeout} s, but its longest run took {human_ms(metrics.duration_max)}: a call "
                f"that hangs keeps running, and billing, for up to {fn.timeout} s. A timeout nearer its runs stops "
                f"that sooner: aws lambda update-function-configuration {cli} --timeout {suggested}."
            )))
    cost = function_monthly_cost(fn, metrics, prices=prices)
    compute = cost.get("compute") or 0.0
    saving = compute * (1 - prices["gb_second_arm"] / prices["gb_second"])
    if not fn.arm and fn.package_type == "Zip" and saving >= 1:
        found.append(("info", (
            f"It runs on x86_64; on arm64 (Graviton) its compute would cost about {human_money(saving)}/month less, "
            "and it often runs faster. Its code and layers must have no x86-only binaries: build them for arm64, "
            f"then aws lambda update-function-code {cli} --architectures arm64 --zip-file fileb://function.zip."
        )))
    if fn.memory <= 128 and (metrics.avg_duration or 0) >= 1000:
        found.append(("info", (
            f"It has 128 MB, which also means the least CPU, and its runs average {human_ms(metrics.avg_duration)}. "
            f"More memory often makes it faster for about the same cost: performance('{name}') shows what it uses."
        )))
    logs = cost.get("logs")
    if logs is not None and logs >= 1 and logs > compute:
        found.append(("info", (
            f"It logs {human_size(metrics.log_bytes)} every {window} ({human_money(logs)}/month at "
            f"{human_money(prices['log_ingestion'])} per GB), more than its compute costs ({human_money(compute)}/month). Log "
            "less, or switch to JSON logs and raise the log level (console: Configuration, Monitoring and operations "
            "tools)."
        )))
    return found


def function_findings(
    fn: Function,
    metrics: FunctionMetrics | None = None,
    detail: FunctionDetail | None = None,
    *,
    prices: dict[str, float] | None = None,
    now: datetime | None = None,
) -> list[tuple[str, str]]:
    """
    What's wrong with a function, or worth knowing: (level, message) pairs, each with the next step. Works on
    what you have: the configuration alone, plus CloudWatch's numbers (metrics), plus the rest of describe()'s
    answer (detail: triggers, provisioned concurrency, the log group, versions...).
    """
    prices = {**LAMBDA_PRICES, **(prices or {})}
    now = now or _utcnow()
    detail = detail or FunctionDetail(fn)
    metrics = metrics or detail.metrics
    name, cli = fn.name, _cli(fn)
    found: list[tuple[str, str]] = []
    if fn.state == "Failed":
        found.append(("warn", (
            f"It's in the Failed state{_reason(fn.state_reason)}: every call fails until that's fixed. Fix the "
            "cause, then deploy it again."
        )))
    elif fn.state == "Inactive":
        found.append(("info", (
            f"It's Inactive{_reason(fn.state_reason)}: it went unused for weeks, so Lambda released what it had "
            "ready. The next call waits while it starts again."
        )))
    if fn.last_update == "Failed":
        found.append(("warn", (
            f"Its last update failed{_reason(fn.last_update_reason)}: it still runs the code and settings from "
            "before. Fix the cause and deploy again."
        )))
    if fn.reserved_concurrency == 0:
        found.append(("warn", (
            "Its reserved concurrency is 0, so every call is throttled: the function is switched off. To switch it "
            f"back on: aws lambda delete-function-concurrency {cli}."
        )))
    if fn.env_error:
        found.append(("warn", (
            f"Lambda can't decrypt its environment variables ({fn.env_error.rstrip('.')}): calls fail until its "
            "KMS key can be used again."
        )))
    found += _runtime_findings(fn, runtime_status(fn.runtime, package_type=fn.package_type, today=now.date()))
    if metrics is not None:
        found += _usage_findings(fn, metrics, detail, prices, now)
    window = _metrics_window(metrics) if metrics else ""
    for pc in detail.provisioned:
        copies = pc.billed or pc.requested
        money = human_money(provisioned_monthly_cost(fn, copies, prices))
        peak = metrics.concurrency_max if metrics else None
        remove = f"aws lambda delete-provisioned-concurrency-config {cli} --qualifier {pc.qualifier}"
        if pc.status == "FAILED":
            found.append(("warn", (
                f"Provisioned concurrency on {pc.qualifier} failed{_reason(pc.reason)}: none of the {pc.requested} "
                f"copies it asks for are ready. Fix the cause, or remove it: {remove}."
            )))
        elif metrics is not None and not metrics.invocations:
            found.append(("warn", (
                f"{pc.qualifier} keeps {copies} copies started and ready (provisioned concurrency), costing {money}"
                f"/month, but the function wasn't called in the last {window}. Remove it: {remove}."
            )))
        elif peak is not None and copies >= 2 and peak < copies / 2:
            lower = max(1, math.ceil(peak * 1.5))
            found.append(("warn", (
                f"{pc.qualifier} keeps {copies} copies ready (provisioned concurrency), costing {money}/month, but "
                f"at most {peak:,.0f} ran at once in the last {window}. Lower it: aws lambda "
                f"put-provisioned-concurrency-config {cli} --qualifier {pc.qualifier} "
                f"--provisioned-concurrent-executions {lower}."
            )))
        else:
            found.append(("info", (
                f"{pc.qualifier} keeps {copies} copies ready (provisioned concurrency): {money}/month whether "
                "they're used or not."
            )))
    for t in detail.triggers:
        if t.public and t.kind == "Function URL":
            found.append(("warn", (
                "Its function URL needs no sign-in (auth type NONE): anyone who has the URL can run it, and you pay "
                f"for every call. If that isn't meant to be: aws lambda update-function-url-config {cli} "
                "--auth-type AWS_IAM."
            )))
        elif t.public:
            found.append(("warn", (
                f"Its resource policy lets anyone invoke it (statement {t.statement or '?'}: Principal \"*\" with no "
                "condition): any AWS account can run it, and you pay for every call. Remove that statement: "
                f"aws lambda remove-permission {cli} --statement-id {t.statement or '<id>'}."
            )))
        elif t.unscoped:
            found.append(("info", (
                f"Its resource policy lets {t.principal} invoke it without naming the {t.kind} (no SourceArn or "
                "SourceAccount condition), so that service may call it for other accounts' resources too. Grant it "
                "again with --source-arn (and --source-account), then remove statement "
                f"{t.statement or '?'}: aws lambda remove-permission {cli} --statement-id {t.statement or '<id>'}."
            )))
        if t.via == "event source mapping":
            if t.last_result and t.last_result.upper().startswith("PROBLEM"):
                found.append(("warn", (
                    f"Its {t.kind} {t.source} reports '{t.last_result}': records from it aren't being processed. "
                    f"errors('{name}') shows why the calls fail."
                )))
            elif (t.state or "").lower() == "disabled":
                found.append(("info", (
                    f"Its trigger from {t.kind} {t.source} is disabled: nothing from it reaches the function, and "
                    "messages wait (or expire). If it should run: aws lambda update-event-source-mapping --uuid "
                    f"{t.uuid} --enabled{_region_flag(fn)}."
                )))
    asynchronous = list(dict.fromkeys(f"{t.kind} {t.source}".strip() for t in detail.triggers if t.asynchronous))
    if asynchronous and detail.async_config is not None and not (detail.async_config.on_failure or fn.dead_letter):
        account = fn.arn.split(":")[4] if fn.arn.count(":") >= 5 else "123456789012"
        queue = f"arn:aws:sqs:{fn.region or 'us-east-1'}:{account}:{fn.name}-failed"
        sources = ", ".join(asynchronous[:3]) + (f" and {len(asynchronous) - 3} more" if len(asynchronous) > 3 else "")
        found.append(("info", (
            f"Events from {sources} that still fail after {detail.async_config.retries} retries are "
            "dropped: nothing keeps them. Send them to a queue to look at later (create it first): aws lambda "
            f"put-function-event-invoke-config {cli} --destination-config "
            f"'{{\"OnFailure\":{{\"Destination\":\"{queue}\"}}}}'."
        )))
    group = detail.log_group
    if group is not None and group.retention_days is None and (group.stored_bytes or 0) >= 100 * MB:
        found.append(("info", (
            f"Its log group {group.name} keeps logs forever: {human_size(group.stored_bytes)} so far, "
            f"{human_money(log_storage_monthly_cost(group, prices))}/month and growing. Keep 30 days of them: aws logs "
            f"put-retention-policy --log-group-name {group.name} --retention-in-days 30{_region_flag(fn)}."
        )))
    secrets = secret_like(fn.env_names)
    if secrets:
        found.append(("info", (
            (f"Environment variables {', '.join(secrets)} look like secrets" if len(secrets) > 1
             else f"Environment variable {secrets[0]} looks like a secret")
            + ": anyone allowed lambda:GetFunction can read the values. Keep secrets in Secrets Manager or Parameter "
            "Store, and read them when the function starts."
        )))
    if detail.runtime_updates == "Manual":
        pinned = detail.runtime_version.split(":")[-1][:12] if detail.runtime_version else "one version"
        found.append(("info", (
            f"Runtime updates are Manual: it stays on runtime version {pinned}, so it doesn't get AWS's security "
            f"patches. Let Lambda update it: aws lambda put-runtime-management-config {cli} --update-runtime-on Auto."
        )))
    if detail.versions:
        stored = sum(v.code_size for v in detail.versions)
        if len(detail.versions) >= 50 or stored >= GB:
            found.append(("info", (
                f"It has {len(detail.versions):,} published versions holding {human_size(stored)} of code, which "
                "counts against the region's code storage. Delete the ones no alias points to: aws lambda "
                f"delete-function {cli} --qualifier <version>."
            )))
    return found


def account_findings(limits: AccountLimits, days: float = 30) -> list[tuple[str, str]]:
    """
    A region's limits that are close to running out: code storage, and how many runs may happen at once.
    """
    found: list[tuple[str, str]] = []
    region = limits.region
    if limits.code_storage is not None and limits.code_storage_limit:
        share = limits.code_storage / limits.code_storage_limit
        if share >= 0.8:
            found.append(("warn", (
                f"Lambda code in {region} takes {human_size(limits.code_storage)} of its "
                f"{human_size(limits.code_storage_limit)} limit ({_pct(share)}): deploys fail once it's full. Old "
                "published versions usually hold most of it (function_info() lists a function's versions); Service "
                "Quotas can raise the limit."
            )))
    if limits.concurrency and limits.peak_concurrency is not None:
        share = limits.peak_concurrency / limits.concurrency
        if share >= 0.8:
            found.append(("warn", (
                f"Up to {limits.peak_concurrency:,.0f} runs happened at once in {region} in the last "
                f"{_window(days * 86400)}, of the {limits.concurrency:,} the region allows: past that, calls are "
                "throttled. Ask for more in Service Quotas (AWS Lambda, Concurrent executions)."
            )))
    if limits.concurrency and limits.unreserved is not None and limits.unreserved < min(100, limits.concurrency):
        found.append(("warn", (
            f"Reserved concurrency leaves {limits.unreserved:,} of {region}'s {limits.concurrency:,} concurrent runs "
            "for every other function, so they're throttled sooner. function_info() shows each function's reserved "
            "concurrency."
        )))
    return found
