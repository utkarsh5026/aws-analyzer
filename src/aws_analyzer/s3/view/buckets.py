"""
The Buckets commands, and the tables and cells they share.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Text, _Title, _Tone
from ..._kit.fmt import _fmt_dt, _plural, _share, _tz_label, human_age, human_money, human_size
from ..._kit.render import _call
from ..._kit.view import _friendly_errors

from ..helpers import parse_s3_uri, s3_uri
from ..models import BucketConfig, PolicyStatement
from ..analysis.cost import cloudwatch_cost
from ..analysis.bucket import block_label, bucket_findings, encryption_label, public_exposure, rule_actions, rule_scope
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

    @_friendly_errors
    def overview(self, match: str | None = None, *, metrics: bool = True) -> None:
        """
        Every bucket in one table: size, estimated cost, versioning, encryption, public access,
        lifecycle and warnings. match='sagemaker-*' checks only matching bucket names.
        """
        with self._progress("Checking buckets", unit="buckets") as tick:
            reports = self.core.bucket_reports(
                match=match, metrics=metrics, progress=tick
            )
        account_block, account = self._account_block(), self.core.account_id()
        rows: list[
            tuple[int, list[Any]]
        ] = []  # cells are text, or _Tone for a coloured status
        warnings: list[list[str]] = []
        objects = size = 0
        cost = 0.0
        missing_metrics: list[str] = []
        for report in reports:
            cfg, usage = report.config, report.metrics
            found = bucket_findings(cfg, account_block) + policy_findings(
                explain_policy(cfg.policy, account)
            )
            bucket_warnings = [message for level, message in found if level == "warn"]
            warnings += [[cfg.name, message] for message in bucket_warnings]
            bucket_size = bucket_cost = bucket_objects = None
            if usage is not None and usage.as_of is not None:
                bucket_size, bucket_objects = usage.total_size, usage.object_count
                bucket_cost = sum(
                    c
                    for c in cloudwatch_cost(
                        usage.size_by_storage_type, self.core.prices
                    ).values()
                    if c
                )
                size, cost, objects = (
                    size + bucket_size,
                    cost + bucket_cost,
                    objects + (bucket_objects or 0),
                )
            elif metrics:
                missing_metrics.append(
                    f"{cfg.name} ({report.metrics_error})"
                    if report.metrics_error
                    else cfg.name
                )
            enabled_rules = sum(
                rule.get("Status") == "Enabled" for rule in cfg.lifecycle_rules
            )
            exposure, encryption = public_exposure(cfg, account_block), encryption_label(cfg)
            exposed = {"blocked": "ok", "not blocked": "warn"}.get(
                exposure, "bad" if "PUBLIC" in exposure else ""
            )
            rows.append(
                (
                    -1 if bucket_size is None else bucket_size,
                    [
                        cfg.name,
                        cfg.region or "?",
                        "-" if bucket_objects is None else f"{bucket_objects:,}",
                        human_size(bucket_size),
                        human_money(bucket_cost),
                        _section(cfg, "versioning", cfg.versioning or "?"),
                        _Tone(encryption, "warn" if encryption == "none" else ""),
                        _Tone(exposure, exposed),
                        _section(cfg, "lifecycle", str(enabled_rules)),
                        _Tone(
                            str(len(bucket_warnings)), "warn" if bucket_warnings else ""
                        ),
                    ],
                )
            )
        rows.sort(key=lambda row: row[0], reverse=True)
        blocks: list[Any] = [
            _Title(
                f"All buckets ({len(reports)})",
                f"names matching {match!r}" if match else "",
            ),
            _Cards(
                [
                    ("Buckets", f"{len(reports):,}"),
                    ("Objects", f"{objects:,}" if metrics else "-"),
                    ("Total size", human_size(size if metrics else None)),
                    ("Est. cost / month", human_money(cost if metrics else None)),
                    (
                        "Buckets with warnings",
                        f"{len({bucket for bucket, _ in warnings}):,}",
                        "warn" if warnings else "ok",
                    ),
                    (
                        "Account block public access",
                        "?" if account_block is None else block_label(account_block),
                    ),
                    (
                        "Regions",
                        f"{len({r.config.region for r in reports if r.config.region}):,}",
                    ),
                ]
            ),
        ]
        if missing_metrics:
            blocks.append(
                _Note(
                    f"No CloudWatch size for {_plural(len(missing_metrics), 'bucket')}: "
                    f"{', '.join(missing_metrics[:10])}{' …' if len(missing_metrics) > 10 else ''}. "
                    "Metrics arrive once a day; new or empty buckets have none."
                )
            )
        blocks.append(
            _Table(
                [
                    "Bucket",
                    "Region",
                    "Objects",
                    "Size",
                    "Est. $/month",
                    "Versioning",
                    "Encryption",
                    "Public access",
                    "Lifecycle rules",
                    "Warnings",
                ],
                [row for _, row in rows],
                title=f"Buckets by size (CloudWatch, all versions; cost {self._price_basis()})",
                bars=[_share(max(s, 0), size) for s, _ in rows],
                bar_label="% of size",
                max_rows=0,
            )
        )
        if warnings:
            blocks.append(
                _Table(
                    ["Bucket", "Warning"],
                    warnings,
                    prose_cols=(1,),
                    title="Warnings (bucket_info(name) shows every finding for one bucket)",
                )
            )
        if rows:
            flagged = Counter(bucket for bucket, _ in warnings).most_common(1)
            biggest = rows[0][1][0]
            look = flagged[0][0] if flagged else biggest
            blocks.append(
                _Next(
                    [
                        (
                            _call("bucket_info", look),
                            "why it's flagged, and what to change"
                            if flagged
                            else "its settings, policy and cost",
                        ),
                        (
                            _call("summary", s3_uri(biggest)),
                            "what's in the biggest bucket",
                        ),
                    ]
                )
            )
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
