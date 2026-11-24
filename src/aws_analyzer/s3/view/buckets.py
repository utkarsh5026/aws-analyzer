"""
The Buckets commands, and the tables and cells they share.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Text, _Title, _Verdict
from ..._kit.fmt import _fmt_dt, _plural, _share, _tz_label, human_age, human_money, human_size
from ..._kit.render import _call
from ..._kit.view import _friendly_errors

from ..helpers import parse_s3_uri, s3_uri
from ..blocks import _CheckGrid, _Fixes, _GridRow
from ..models import BucketChecks, BucketConfig, BucketReport, Fix, PolicyStatement
from ..analysis.cost import cloudwatch_cost
from ..analysis.bucket import block_label, bucket_findings, encryption_label, public_exposure, rule_actions, rule_scope
from ..analysis.overview import (
    BAD, CHECK_COLUMNS, OK, UNKNOWN, bucket_checks, overview_fixes, overview_verdict, state_rank,
)
from ..analysis.policy import explain_policy, policy_findings


def _policy_table(statements: list[PolicyStatement], title: str = "") -> _Table:
    return _Table(
        ["Statement", "Effect", "Who", "Can", "On", "When"],
        [
            [
                st.sid,
                st.effect,
                "\n".join(st.who),
                "\n".join(st.actions),
                "\n".join(st.resources),
                "\n".join(st.conditions) or "always",
            ]
            for st in statements
        ],
        title=title,
        max_rows=0,
    )


def _public_access_label(cfg: BucketConfig) -> str:
    if "public_access_block" in cfg.errors:
        return f"? ({cfg.errors['public_access_block']})"
    return block_label(cfg.public_access_block)


def _section(cfg: BucketConfig, section: str, text: str) -> str:
    return f"? ({cfg.errors[section]})" if section in cfg.errors else text


_GRID_ROWS = 25

_NO_METRICS_LISTED = 10


@dataclass
class _OverviewRow:
    """
    One bucket in the overview: its report, its checks, and its size, object count and storage cost (None
    without CloudWatch numbers).
    """

    report: BucketReport
    checks: BucketChecks
    size: int | None = None
    objects: int | None = None
    cost: float | None = None

    @property
    def name(self) -> str:
        return self.report.config.name


def _worst_first(row: _OverviewRow) -> tuple[int, int]:
    """
    The overview's order: public buckets, then warnings, then unread, then fine; the biggest first within each.
    """
    return state_rank(row.checks.worst), -(row.size or 0)


def _overview_title(rows: list[_OverviewRow], match: str | None, *, metrics: bool) -> _Title:
    """
    'All buckets (4)', over the objects and regions they hold and the names asked for.
    """
    regions = {row.report.config.region for row in rows if row.report.config.region}
    parts = [f"names matching {match!r}"] if match else []
    if metrics:
        objects = sum(row.objects or 0 for row in rows)
        parts.append(f"{objects:,} objects")
    parts.append(_plural(len(regions), "region"))
    return _Title(f"All buckets ({len(rows)})", " · ".join(parts) if rows else "")


def _missing_metrics(rows: list[_OverviewRow]) -> list[Any]:
    """
    A note naming the buckets CloudWatch has no size for (with why, when the read failed), if any.
    """
    missing = []
    for row in rows:
        if row.size is None:
            error = row.report.metrics_error
            missing.append(f"{row.name} ({error})" if error else row.name)
    if not missing:
        return []
    listed = ", ".join(missing[:_NO_METRICS_LISTED]) + (" …" if len(missing) > _NO_METRICS_LISTED else "")
    return [_Note(
        f"No CloudWatch size for {_plural(len(missing), 'bucket')}: {listed}. "
        "Metrics arrive once a day; new or empty buckets have none."
    )]


class _BucketCommands:
    """
    S3View's commands about buckets: buckets(), overview(), bucket_info() and policy().
    """

    def _account_block(self) -> dict[str, bool] | None:
        """
        Account-level Block Public Access, or None when it can't be read.
        """
        try:
            return self.core.account_public_access_block()
        except (ClientError, BotoCoreError, ValueError):
            return None

    # ------------------------------------------------------------------ buckets

    @_friendly_errors
    def buckets(self, *, with_region: bool = True) -> None:
        """
        All buckets in the account with region, creation date and age.
        """
        buckets = sorted(
            self.core.list_buckets(with_region=with_region), key=lambda b: b.name
        )
        regions = Counter(b.region or "unknown" for b in buckets)
        self._show(
            [
                _Title(
                    f"S3 buckets ({len(buckets)})",
                    " · ".join(f"{r}: {n}" for r, n in regions.most_common()),
                ),
                _Table(
                    ["Bucket", "Region", f"Created ({_tz_label()})", "Age"],
                    [
                        [
                            b.name,
                            b.region or "-",
                            _fmt_dt(b.created),
                            human_age(b.created),
                        ]
                        for b in buckets
                    ],
                    max_rows=0,
                ),
                _Next(
                    [("overview()", "size, cost and warnings of every bucket")]
                    + (
                        [
                            (
                                _call("bucket_info", buckets[0].name),
                                "one bucket's settings and risks",
                            )
                        ]
                        if buckets
                        else []
                    )
                ),
            ]
        )

    @_friendly_errors
    def bucket_info(self, bucket: str, *, metrics: bool = True) -> None:
        """
        Bucket settings, policy in plain English, risks, and CloudWatch size, object count and
        estimated monthly cost (instant, even for huge buckets).
        """
        cfg = self.core.bucket_config(bucket)
        account_block = self._account_block()
        blocked = any(
            settings and all(settings.values())
            for settings in (account_block, cfg.public_access_block)
        )
        open_to_public = (
            "warn" if not blocked and "public_access_block" not in cfg.errors else ""
        )
        cards = [
            ("Region", cfg.region or "?"),
            ("Versioning", _section(cfg, "versioning", cfg.versioning or "?")),
            (
                "Encryption",
                encryption_label(cfg),
                "" if cfg.encryption or "encryption" in cfg.errors else "warn",
            ),
            ("Block public access", _public_access_label(cfg), open_to_public),
            (
                "Account block public access",
                "?" if account_block is None else block_label(account_block),
                open_to_public,
            ),
            (
                "Bucket policy",
                _section(
                    cfg,
                    "policy",
                    "public"
                    if cfg.policy_is_public
                    else "private"
                    if cfg.has_policy
                    else "none",
                ),
                "bad" if cfg.policy_is_public else "",
            ),
            (
                "Object ownership",
                _section(cfg, "ownership", cfg.object_ownership or "-"),
            ),
            (
                "Object lock",
                _section(cfg, "object_lock", "on" if cfg.object_lock else "off"),
            ),
            (
                "Lifecycle rules",
                _section(cfg, "lifecycle", str(len(cfg.lifecycle_rules))),
            ),
            (
                "Replication rules",
                _section(cfg, "replication", str(len(cfg.replication_rules))),
            ),
            ("Access logging", _section(cfg, "logging", cfg.logging_target or "off")),
            (
                "Inventory",
                _section(cfg, "inventory", ", ".join(cfg.inventory_configs) or "none"),
            ),
        ]
        blocks: list[Any] = [
            _Title(f"Bucket s3://{cfg.name}", f"region {cfg.region or 'unknown'}")
        ]
        storage_table = None
        if metrics:
            try:
                usage = self.core.bucket_metrics(cfg.name)
            except (ClientError, BotoCoreError) as exc:
                blocks.append(_Note(f"CloudWatch metrics unavailable: {exc}", "warn"))
            else:
                if usage.as_of is None:
                    blocks.append(
                        _Note(
                            "No CloudWatch storage metrics yet (published once a day; "
                            "new or empty buckets have none)."
                        )
                    )
                else:
                    count = (
                        "-" if usage.object_count is None else f"{usage.object_count:,}"
                    )
                    costs = cloudwatch_cost(
                        usage.size_by_storage_type, self.core.prices
                    )
                    monthly = sum(c for c in costs.values() if c)
                    cards = [
                        ("Objects", count),
                        ("Total size", human_size(usage.total_size)),
                        ("Est. cost / month", human_money(monthly)),
                    ] + cards
                    total = usage.total_size
                    storage_table = _Table(
                        ["Storage type", "Size", "Est. $/month"],
                        [
                            [kind, human_size(size), human_money(costs[kind])]
                            for kind, size in usage.size_by_storage_type.items()
                        ],
                        title=f"Size by storage type (CloudWatch, {_fmt_dt(usage.as_of, zone=True)}; "
                        f"all versions; cost {self._price_basis()})",
                        bars=[
                            _share(size, total)
                            for size in usage.size_by_storage_type.values()
                        ],
                        bar_label="% of size",
                    )
        statements = explain_policy(cfg.policy, self.core.account_id())
        blocks.append(_Cards(cards))
        blocks.append(
            _Findings(
                bucket_findings(cfg, account_block) + policy_findings(statements),
                empty="No issues found by these checks.",
            )
        )
        if storage_table:
            blocks.append(storage_table)
        if statements:
            blocks.append(
                _policy_table(
                    statements,
                    f"Bucket policy ({_plural(len(statements), 'statement')}; "
                    "policy() shows the JSON)",
                )
            )
        if cfg.lifecycle_rules:
            blocks.append(
                _Table(
                    ["Rule", "Status", "Applies to", "Actions"],
                    [
                        [
                            r.get("ID", "-"),
                            r.get("Status"),
                            rule_scope(r),
                            rule_actions(r),
                        ]
                        for r in cfg.lifecycle_rules
                    ],
                    title="Lifecycle rules",
                )
            )
        if cfg.replication_rules:
            blocks.append(
                _Table(
                    ["Rule", "Status", "Destination"],
                    [
                        [
                            r.get("ID", "-"),
                            r.get("Status"),
                            r.get("Destination", {}).get("Bucket", "-"),
                        ]
                        for r in cfg.replication_rules
                    ],
                    title="Replication rules",
                )
            )
        if cfg.tags:
            blocks.append(
                _Table(
                    ["Tag", "Value"],
                    [[k, v] for k, v in sorted(cfg.tags.items())],
                    title="Tags",
                    collapsed=True,
                )
            )
        root = s3_uri(cfg.name, "")
        blocks.append(
            _Next(
                [
                    (
                        _call("summary", root),
                        "what's in it: folders, file types, cost, findings",
                    )
                ]
                + (
                    [
                        (
                            _call("policy", cfg.name),
                            "the policy in plain English, and its JSON",
                        )
                    ]
                    if statements
                    else []
                )
                + (
                    [
                        (
                            _call("versions", root),
                            "old versions and deleted files still billed",
                        )
                    ]
                    if cfg.versioning == "Enabled"
                    else [(_call("tree", root), "folder sizes")]
                )
            )
        )
        self._show(blocks)

    def _overview_row(
        self, report: BucketReport, account_block: dict[str, bool] | None, account: str | None
    ) -> _OverviewRow:
        """
        One bucket of the overview: its checks, and its size, object count and storage cost from CloudWatch (None
        when CloudWatch has no numbers for it).
        """
        checks = bucket_checks(report.config, account_block, account=account)
        usage = report.metrics
        if usage is None or usage.as_of is None:
            return _OverviewRow(report, checks)
        costs = cloudwatch_cost(usage.size_by_storage_type, self.core.prices).values()
        cost = sum(amount for amount in costs if amount)
        return _OverviewRow(report, checks, size=usage.total_size, objects=usage.object_count, cost=cost)

    def _overview_grid(self, rows: list[_OverviewRow], *, metrics: bool) -> _CheckGrid:
        """
        The grid of buckets against checks: the ones with problems first (at most _GRID_ROWS), those that pass
        every check folded under one line.
        """
        total = sum(row.size or 0 for row in rows)

        def grid_row(row: _OverviewRow) -> _GridRow:
            size = human_size(row.size) if metrics else "-"
            cost = human_money(row.cost) if metrics else "-"
            share = _share(row.size or 0, total)
            return _GridRow(row.name, row.report.config.region or "?", row.checks.checks, size, cost, share)

        problems = [row for row in rows if row.checks.worst != OK]
        passing = [grid_row(row) for row in rows if row.checks.worst == OK]
        shown = [grid_row(row) for row in problems[:_GRID_ROWS]]
        basis = f"sizes from CloudWatch, all versions; cost {self._price_basis()}" if metrics else "no sizes asked for"
        title = f"Buckets, worst first ({basis})"
        return _CheckGrid(CHECK_COLUMNS, shown, passing, title=title, more=len(problems) - len(shown))

    @staticmethod
    def _overview_cards(rows: list[_OverviewRow], *, metrics: bool) -> list[tuple[str, ...]]:
        """
        The four numbers that matter: buckets open to the internet, buckets that need attention, the space stored
        and what it costs, and how many couldn't be fully checked when some couldn't.
        """
        public = sum(row.checks.worst == BAD for row in rows)
        attention = sum(bool(row.checks.problems) for row in rows)
        unread = sum(row.checks.worst == UNKNOWN for row in rows)
        size = sum(row.size or 0 for row in rows)
        cost = sum(row.cost or 0.0 for row in rows)
        cards: list[tuple[str, ...]] = [
            ("Open to the internet", _plural(public, "bucket"), "bad") if public else ("Open to the internet", "none"),
            ("Need attention", f"{attention:,} of {len(rows):,}", "warn") if attention else ("Need attention", "none"),
            ("Stored", human_size(size) if metrics else "-"),
            ("Est. cost / month", human_money(cost) if metrics else "-"),
        ]
        if unread:
            cards.append(("Couldn't fully check", _plural(unread, "bucket")))
        return cards

    @staticmethod
    def _overview_fixes(fixes: list[Fix], rows: list[_OverviewRow]) -> list[Any]:
        """
        The changes that clear the warnings, or a note that there's nothing to fix (and what couldn't be read).
        """
        if fixes:
            warnings = sum(fix.clears for fix in fixes)
            cleared = "the warning" if warnings == 1 else f"all {warnings:,} warnings"
            fixes_word = "1 fix clears" if len(fixes) == 1 else f"{len(fixes)} fixes clear"
            title = f"{fixes_word} {cleared}"
            return [_Fixes(fixes, title=title)]
        if not rows:
            return []
        unread = [row.name for row in rows if row.checks.worst == UNKNOWN]
        if unread:
            return [_Note(
                f"No problems found, but {_plural(len(unread), 'bucket')} couldn't be fully read: a ? in the grid "
                "names the setting and the error, usually a missing s3:Get* permission (README lists them).",
                "warn",
            )]
        return [_Note(f"Every bucket passes these checks ({_plural(len(rows), 'bucket')}).", "ok")]

    def _overview_settings(self, rows: list[_OverviewRow], account_block: dict[str, bool] | None) -> _Table:
        """
        Each bucket's settings as they are, folded: what the grid's words come from.
        """
        table_rows = []
        for row in rows:
            cfg = row.report.config
            enabled_rules = sum(rule.get("Status") == "Enabled" for rule in cfg.lifecycle_rules)
            table_rows.append([
                row.name,
                cfg.region or "?",
                "-" if row.objects is None else f"{row.objects:,}",
                human_size(row.size),
                human_money(row.cost),
                _section(cfg, "versioning", cfg.versioning or "?"),
                encryption_label(cfg),
                public_exposure(cfg, account_block),
                _section(cfg, "lifecycle", str(enabled_rules)),
            ])
        headers = ["Bucket", "Region", "Objects", "Size", "Est. $/month", "Versioning", "Encryption",
                   "Public access", "Lifecycle rules"]
        return _Table(headers, table_rows, title="Every setting per bucket", max_rows=0, collapsed=True)

    @staticmethod
    def _overview_next(rows: list[_OverviewRow], fixes: list[Fix]) -> list[Any]:
        """
        The fix to look at first (its own call, or bucket_info on its first bucket), and what's in the biggest bucket.
        """
        if not rows:
            return []
        steps = []
        first_look = next((fix for fix in fixes if not fix.call.startswith("aws ")), None)
        if first_look:
            steps.append((first_look.call, "the first thing to change, in detail"))
        elif fixes:
            steps.append((_call("bucket_info", fixes[0].buckets[0]), "why it's flagged, and what to change"))
        biggest = max(rows, key=lambda row: row.size or 0).name
        steps.append((_call("summary", s3_uri(biggest)), "what's in the biggest bucket"))
        return [_Next(steps)]

    @_friendly_errors
    def overview(self, match: str | None = None, *, metrics: bool = True) -> None:
        """
        Every bucket at a glance: what to fix first, the changes that clear its warnings, and a grid of each
        bucket's policy, Block Public Access, encryption and old versions with its size and estimated cost.
        match='sagemaker-*' checks only matching bucket names.
        """
        with self._progress("Checking buckets", unit="buckets") as tick:
            reports = self.core.bucket_reports(match=match, metrics=metrics, progress=tick)
        account_block, account = self._account_block(), self.core.account_id()
        rows = sorted(
            (self._overview_row(report, account_block, account) for report in reports), key=_worst_first
        )
        checks = [row.checks for row in rows]
        fixes = overview_fixes(checks, account=account)
        verdict = overview_verdict(checks)

        blocks: list[Any] = [_overview_title(rows, match, metrics=metrics)]
        if verdict:
            blocks.append(_Verdict(verdict.headline, verdict.detail, verdict.tone))
        blocks.append(_Cards(self._overview_cards(rows, metrics=metrics)))
        blocks += _missing_metrics(rows) if metrics else []
        blocks += self._overview_fixes(fixes, rows)
        if rows:
            blocks += [self._overview_grid(rows, metrics=metrics), self._overview_settings(rows, account_block)]
        blocks += self._overview_next(rows, fixes)
        self._show(blocks)

    @_friendly_errors
    def policy(self, bucket: str) -> None:
        """
        Bucket policy in plain English: who can do what, on which files, when; risks; the raw JSON.
        """
        name = parse_s3_uri(bucket)[0]
        document = self.core.bucket_policy(name)
        blocks: list[Any] = [_Title(f"Bucket policy of s3://{name}")]
        if document is None:
            blocks.append(
                _Note(
                    "This bucket has no bucket policy. Access comes only from IAM policies "
                    "(and ACLs, if they're enabled)."
                )
            )
            return self._show(blocks)
        statements = explain_policy(document, self.core.account_id())
        blocks.append(
            _Cards(
                [
                    ("Statements", f"{len(statements):,}"),
                    ("Allow", f"{sum(st.effect == 'Allow' for st in statements):,}"),
                    ("Deny", f"{sum(st.effect == 'Deny' for st in statements):,}"),
                    ("Open to anyone", f"{sum(st.public for st in statements):,}"),
                    (
                        "Other accounts",
                        ", ".join(
                            sorted({a for st in statements for a in st.other_accounts})
                        )
                        or "none",
                    ),
                ]
            )
        )
        blocks += [
            _Findings(policy_findings(statements)),
            _policy_table(statements),
            _Text(json.dumps(document, indent=2), title="Policy JSON", collapsed=True),
            _Next(
                [(_call("bucket_info", name), "the bucket's other settings and risks")]
            ),
        ]
        self._show(blocks)
