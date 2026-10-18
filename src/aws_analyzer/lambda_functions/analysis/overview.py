"""
Overview: every function in one region or all of them, with what the account allows, and
what each costs and how its runtime stands.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..._kit.deps import _require
from ..._kit.fmt import _total

from ..prices import LAMBDA_PRICES
from ..models import AccountLimits, Function, FunctionDetail, FunctionMetrics, LogGroup, ProvisionedConcurrency, Trigger
from .functions import runtime_status
from .cost import function_monthly_cost


@dataclass
class Overview:
    """
    Every function in one or more regions, with what CloudWatch counted for each and what invokes it.
    """

    regions: list[str]
    days: int
    functions: list[Function] = field(default_factory=list)
    metrics: dict[str, FunctionMetrics] = field(default_factory=dict)  # by function ARN
    triggers: dict[str, list[Trigger]] = field(default_factory=dict)  # by function ARN
    provisioned: dict[str, list[ProvisionedConcurrency]] = field(default_factory=dict)  # by function ARN
    log_groups: dict[str, LogGroup] = field(default_factory=dict)  # by function ARN
    accounts: dict[str, AccountLimits] = field(default_factory=dict)  # by region
    errors: dict[str, str] = field(default_factory=dict)  # 'region:section' -> error code
    skipped: dict[str, str] = field(default_factory=dict)  # regions that aren't turned on -> why
    details: bool = True  # whether each function's resource policy and provisioned concurrency were read
    metrics_read: int = 0  # metrics requested from CloudWatch, for the cost of this report
    prices: dict[str, float] = field(default_factory=lambda: dict(LAMBDA_PRICES), repr=False)

    def add_extras(self, found: dict[str, tuple[list[Trigger], list[ProvisionedConcurrency], dict[str, str]]]) -> None:
        """
        Adds the reads made per function (LambdaAnalyzer._extras: the callers its resource policy allows, its
        provisioned concurrency, and what couldn't be read), by function ARN.
        """
        for fn in self.functions:
            if fn.arn in found:
                triggers, provisioned, errors = found[fn.arn]
                self.triggers.setdefault(fn.arn, []).extend(triggers)
                if provisioned:
                    self.provisioned[fn.arn] = provisioned
                fn.errors.update(errors)
        self.details = True

    def add_numbers(self, metrics: dict[str, FunctionMetrics], read: int, peaks: dict[str, float | None],
                    errors: dict[str, str]) -> None:
        """
        Adds CloudWatch's numbers (LambdaAnalyzer.overview_numbers): each function's by ARN, how many metrics that
        read, each region's most runs at once, and 'region:metrics' -> the error code of a region that couldn't be
        read.
        """
        self.metrics.update(metrics)
        self.metrics_read += read
        for region, peak in peaks.items():
            if region in self.accounts:
                self.accounts[region].peak_concurrency = peak
        self.errors.update(errors)

    def detail(self, fn: Function) -> FunctionDetail:
        """
        What the overview knows about one function, shaped like describe()'s answer (fewer sections). Its errors
        name what wasn't read, so findings don't claim, say, that nothing triggers a function whose policy is unread.
        """
        errors = {section: code for section, code in fn.errors.items() if section in ("policy", "provisioned")}
        if not self.details:
            errors.update(policy="not read", provisioned="not read")
        if f"{fn.region}:triggers" in self.errors:
            errors["triggers"] = self.errors[f"{fn.region}:triggers"]
        return FunctionDetail(
            fn,
            triggers=self.triggers.get(fn.arn, []),
            provisioned=self.provisioned.get(fn.arn, []),
            log_group=self.log_groups.get(fn.arn),
            metrics=self.metrics.get(fn.arn),
            errors=errors,
        )

    def to_df(self):
        """
        One row per function: its settings, what CloudWatch counted and the estimated monthly cost.
        """
        pd = _require("pandas", "Overview.to_df()")
        rows = []
        for fn in self.functions:
            m = self.metrics.get(fn.arn)
            cost = function_monthly_cost(fn, m, self.provisioned.get(fn.arn, ()), self.log_groups.get(fn.arn),
                                         prices=self.prices)
            rows.append({
                "region": fn.region,
                "function": fn.name,
                "runtime": fn.runtime or "container image",
                "runtime_status": runtime_status(fn.runtime, package_type=fn.package_type).state,
                "memory_mb": fn.memory,
                "timeout_s": fn.timeout,
                "architecture": fn.architecture,
                "package": fn.package_type,
                "code_bytes": fn.code_size,
                "last_modified": fn.last_modified,
                "triggers": ", ".join(t.short for t in self.triggers.get(fn.arn, [])),
                "invocations": m.invocations if m else None,
                "errors": m.errors if m else None,
                "throttles": m.throttles if m else None,
                "avg_duration_ms": m.avg_duration if m else None,
                "max_duration_ms": m.duration_max if m else None,
                "last_invoked": m.last_invoked if m else None,
                "est_monthly_usd": _total(cost),
                "description": fn.description,
            })
        return pd.DataFrame(rows)
