"""
DynamoDBView: the notebook UI, which renders what DynamoDBAnalyzer returns as cards and tables.
"""

from __future__ import annotations

import difflib
from collections import Counter
from decimal import Decimal
from typing import Any, Callable, Iterable

from botocore.exceptions import BotoCoreError, ClientError

from .._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Text, _Title, _Tone
from .._kit.errors import _error_name, _why
from .._kit.fmt import _as_count, _as_int, _count, _fmt_dt, _plural, _share, human_age, human_money, human_size
from .._kit.render import _call
from .._kit.text import _clip
from .._kit.view import _BaseView, _friendly_errors

from .helpers import KB, TYPE_NAMES, _units, dynamo_type, format_value, item_size, read_units, to_json, write_units
from .models import AttributeProfile, IndexInfo, ItemPage, ReadStats, TableInfo, items_table
from .analysis import (
    _LARGE_ITEM_ADVICE, describe_condition, describe_filter, profile_findings, request_cost, table_findings,
    table_monthly_cost,
)
from .analyzer import _FROM_RE, DynamoDBAnalyzer
from .style import _render_html


def _target(table: str, index: str | None) -> str:
    return f"{table} (index {index})" if index else table


def _keys_label(info: TableInfo | IndexInfo, types: dict[str, str]) -> str:
    """
    'pk (string) + sk (number)'.
    """
    return (
        " + ".join(
            f"{k} ({TYPE_NAMES.get(types.get(k, ''), '?')})" for k in info.keys if k
        )
        or "-"
    )


def _capacity_label(read: int | None, write: int | None) -> str:
    return f"{_count(read)} read / {_count(write)} write units"


def _billing_label(info: TableInfo) -> str:
    if info.on_demand:
        return "on-demand"
    return "provisioned: " + _capacity_label(info.read_capacity, info.write_capacity)


_TABLE_CLASSES = {
    "STANDARD": "standard",
    "STANDARD_INFREQUENT_ACCESS": "standard-infrequent access (cheaper storage)",
}

_STREAM_VIEWS = {
    "KEYS_ONLY": "on: keys only",
    "NEW_IMAGE": "on: new item",
    "OLD_IMAGE": "on: old item",
    "NEW_AND_OLD_IMAGES": "on: old and new item",
}

_TTL_STATES = {"DISABLED": "off", "ENABLING": "turning on", "DISABLING": "turning off"}

_NO_MATCH = (
    "No items matched. Values are typed: '100' (text) doesn't match 100 (a number); "
    "schema() shows each attribute's type."
)


def _projection_label(idx: IndexInfo) -> str:
    if idx.projection == "INCLUDE":
        return "keys + " + ", ".join(idx.projected)
    return {"ALL": "all attributes", "KEYS_ONLY": "keys only"}.get(
        idx.projection, idx.projection
    )


def _query_hint(info: TableInfo, idx: IndexInfo | None) -> str:
    source = idx or info
    hint = f"query({info.name!r}, <{source.partition_key}>"
    hint += f", sort=<{source.sort_key}>" if source.sort_key else ""
    return hint + (f", index={idx.name!r})" if idx else ")")


def _section(info: TableInfo, section: str, text: str) -> str:
    return f"? ({info.errors[section]})" if section in info.errors else text


def _types_label(attr: AttributeProfile) -> str:
    if len(attr.types) == 1:
        return TYPE_NAMES.get(attr.main_type, attr.main_type)
    return " · ".join(
        f"{TYPE_NAMES.get(kind, kind)} {_share(n, attr.count):.0%}"
        for kind, n in attr.types.most_common()
    )


def _distinct_label(attr: AttributeProfile) -> str:
    if attr.distinct_capped:
        return f"{attr.distinct:,}+"
    if attr.count > 1 and attr.distinct == attr.count:
        return "all unique"
    return f"{attr.distinct:,}"


def _range_label(attr: AttributeProfile) -> str:
    if attr.low is not None:
        low, high = format_value(attr.low, 24), format_value(attr.high, 24)
        return low if low == high else f"{low} … {high}"
    if attr.min_len is not None:
        unit = {"S": "char", "B": "byte", "M": "field"}.get(attr.main_type, "element")
        if attr.min_len == attr.max_len:
            return _plural(attr.min_len, unit)
        return f"{attr.min_len:,} - {attr.max_len:,} {unit}s"
    return ""


def _item_rows(
    item: dict[str, Any],
    keys: list[str],
    *,
    max_depth: int = 6,
    max_elements: int = 100,
) -> list[list[str]]:
    """
    Attribute / type / value rows for one item, with maps (and lists holding maps or lists) expanded.
    """
    rows: list[list[str]] = []
    roles = dict(zip(keys, ("partition key", "sort key")))

    def walk(label: str, value: Any, depth: int) -> None:
        kind = dynamo_type(value)
        indent = "    " * depth
        nested = isinstance(value, dict) or (
            isinstance(value, list) and any(isinstance(v, (dict, list)) for v in value)
        )
        if not (nested and value and depth < max_depth):
            rows.append(
                [
                    indent + label,
                    TYPE_NAMES.get(kind, kind),
                    format_value(value, 2000, oneline=False),
                ]
            )
            return
        rows.append(
            [
                indent + label,
                TYPE_NAMES.get(kind, kind),
                _plural(len(value), "field" if kind == "M" else "element"),
            ]
        )
        children = (
            value.items()
            if isinstance(value, dict)
            else ((f"[{i}]", v) for i, v in enumerate(value))
        )
        for i, (name, child) in enumerate(children):
            if i == max_elements:
                rows.append(
                    [indent + "    …", "", f"{len(value) - max_elements:,} more"]
                )
                break
            walk(str(name), child, depth + 1)

    for name in [k for k in keys if k in item] + [k for k in item if k not in keys]:
        walk(name + (f"  ({roles[name]})" if name in roles else ""), item[name], 0)
    return rows


class DynamoDBView(_BaseView):
    """
    Notebook UI over DynamoDBAnalyzer. Each method renders a report and returns nothing;
    for the underlying data call the same-named method on `view.core` (a DynamoDBAnalyzer).

    mode: 'auto' (HTML inside Jupyter, text elsewhere), 'html' or 'text'.
    max_rows: default cap for long tables (set to 0 for no cap).
    progress: 'auto' (a tqdm bar while long commands run, when tqdm is installed; else a line with the count,
    rate and time left), 'plain' (always that line) or 'off'.
    max_columns: most attributes shown side by side in a table of items (0 = all).
    """

    _render_html = staticmethod(_render_html)  # how its reports look in HTML: the stylesheet, root class and badge
    _GROUPS = {  # help() lists the commands in these groups, in this order
        "🗂️ Tables": ("tables", "table_info"),
        "👀 Look at items": ("sample", "scan", "query", "get", "sql", "more"),
        "🔬 Understand the data": ("schema", "value_counts", "largest", "count"),
        "❓ Help": ("help",),
    }
    _START = (
        ("tables()", "every table: items, size, cost and warnings"),
        ("table_info('table')", "keys, indexes and how to query each"),
        ("sample('table')", "a few items from across the table"),
    )

    def __init__(
        self,
        core: DynamoDBAnalyzer | None = None,
        *,
        mode: str = "auto",
        max_rows: int = 50,
        max_columns: int = 30,
        progress: str = "auto",
    ):
        super().__init__(mode=mode, max_rows=max_rows, progress=progress)
        self.core: DynamoDBAnalyzer = core or DynamoDBAnalyzer()
        self.max_columns = max_columns
        self._pager: tuple[str, str, Callable, str, Any, int] | None = None  # what more() continues

    # ------------------------------------------------------------------ plumbing

    def _explain_call(self, code: str, message: str, command: str, args: tuple, kwargs: dict) -> str:
        """
        A table that wasn't found is named, with the closest names in the region (_not_found).
        """
        if code == "ResourceNotFoundException":
            return self._not_found(command, args, kwargs)
        return super()._explain_call(code, message, command, args, kwargs)

    def _not_found(self, command: str, args: tuple, kwargs: dict) -> str:
        """
        Why a table wasn't found, with the closest names in the region ('Orders' -> 'orders').
        """
        name = kwargs.get(
            "statement" if command == "sql" else "table", args[0] if args else ""
        )
        if command == "sql":
            match = _FROM_RE.search(str(name))
            name = (match.group(1) or match.group(2)) if match else ""
        text = (
            f"table {name!r} not found in {self.core.region}"
            if name
            else f"table not found in {self.core.region}"
        )
        try:
            names = self.core.list_table_names()
        except (ClientError, BotoCoreError):
            names = []
        close = [
            n for n in names if n.lower() == str(name).lower()
        ] or difflib.get_close_matches(str(name), names, n=3)
        if close:
            return (
                text
                + f". Did you mean {' or '.join(map(repr, close))}? Names are case-sensitive."
            )
        return (
            text
            + " (names are case-sensitive, and tables are regional). tables() lists every table in the region."
        )

    def _read_cost(self, stats: ReadStats) -> str:
        return human_money(request_cost(stats.read_units, prices=self.core.prices))

    def _items_blocks(
        self, items: list[dict[str, Any]], keys: list[str], title: str = ""
    ) -> list[Any]:
        """
        A table of items (one row each, one column per attribute) plus a note about hidden columns.
        """
        columns, rows = items_table(items, keys)
        shown = columns[: self.max_columns] if self.max_columns else columns
        blocks: list[Any] = [
            _Table(
                shown,
                [
                    [format_value(row[c]) if c in row else "" for c in shown]
                    for row in rows
                ],
                title=title,
                max_rows=0,
            )
        ]
        if len(shown) < len(columns):
            rest = columns[len(shown) :]
            blocks.append(
                _Note(
                    f"{_plural(len(rest), 'more attribute')} not shown ({', '.join(rest[:6])}"
                    f"{', …' if len(rest) > 6 else ''}): pass attributes=[...] to choose, "
                    "or get a DataFrame of everything from .core (page.to_df())."
                )
            )
        return blocks

    def _page(
        self,
        title: str,
        sub: str,
        fetch: Callable[[Any, Callable[[int], None]], ItemPage],
        *,
        empty: str,
        start: Any = None,
        number: int = 1,
    ) -> None:
        """
        Fetch one page of items, show it, and remember how to continue for more().
        """
        with self._progress() as tick:
            page = fetch(start, tick)
        self._pager = (
            (title, sub, fetch, empty, page.last_key, number + 1)
            if page.has_more
            else None
        )
        st = page.stats
        cards = [("Items", f"{len(page.items):,}")]
        if st.scanned > st.matched:
            cards.append(("Items read", f"{st.scanned:,}"))
        cards += [
            ("Read units", _units(st.read_units)),
            ("Read cost (on-demand)", self._read_cost(st)),
            ("Time", f"{st.seconds:.1f}s"),
        ]
        blocks: list[Any] = [
            _Title(title + (f"  (page {number})" if number > 1 else ""), sub),
            _Cards(cards),
        ]
        if not page.items:
            blocks.append(_Note(empty))
        elif page.operation == "scan" and st.scanned >= max(1000, 10 * st.matched):
            blocks.append(
                _Note(
                    f"Read {st.scanned:,} items to return {st.matched:,}: a filter runs after the read, "
                    "so every item read is billed. If you search by this attribute often, a query on a "
                    "key or an index is far cheaper."
                )
            )
        if page.items:
            blocks += self._items_blocks(page.items, page.keys)
        blocks.append(
            _Next(
                ([("more()", "the next page")] if page.has_more else [])
                + self._item_steps(page)
            )
        )
        self._show(blocks)

    def _item_steps(self, page: ItemPage) -> list[tuple[str, str]]:
        """
        Next steps after a page of items: the first item in full, and what the table's items look like.
        """
        try:
            keys = self.core.keys(page.table) if page.table else []
        except (ClientError, BotoCoreError, ValueError):
            keys = []
        steps = []
        first = page.items[0] if page.items else {}
        if keys and all(k in first for k in keys):
            steps.append(
                (
                    _call("get", page.table, *[first[k] for k in keys]),
                    "the first item in full",
                )
            )
        if page.table and page.operation != "largest":
            steps.append(
                (
                    _call("schema", page.table),
                    "every attribute's types, fill rate and examples",
                )
            )
        return steps

    # ------------------------------------------------------------------ tables

    @_friendly_errors
    def tables(self, match: str | None = None, *, metrics: bool = True) -> None:
        """
        Every table in the region: key, items, size, billing, estimated monthly cost and warnings.
        match='prod-*' checks only matching table names.
        """
        with self._progress("Checking tables", unit="tables") as tick:
            reports = sorted(
                self.core.table_reports(match=match, metrics=metrics, progress=tick),
                key=lambda r: r.info.name,
            )
        rows: list[list[Any]] = []  # cells are text, or _Tone for a coloured status
        warnings: list[list[str]] = []
        unreadable: list[str] = []
        no_usage: list[str] = []
        total = 0.0
        for report in reports:
            t, usage = report.info, report.metrics
            if "describe" in t.errors:
                unreadable.append(
                    f"{t.name} ({_why(t.errors['describe'], 'dynamodb:DescribeTable')})"
                )
                rows.append([t.name, "?", "-", "-", "-", "-", "-", "-", "-", "-"])
                continue
            cost = sum(table_monthly_cost(t, self.core.prices, usage).values())
            total += cost
            found = [
                message
                for level, message in table_findings(t, usage, self.core.prices)
                if level == "warn"
            ]
            warnings += [[t.name, message] for message in found]
            if report.metrics_error:
                no_usage.append(
                    f"{t.name} ({_why(report.metrics_error, 'cloudwatch:GetMetricData')})"
                )
            rows.append(
                [
                    t.name,
                    _Tone(t.status or "?", "" if t.status == "ACTIVE" else "warn"),
                    _count(t.item_count),
                    human_size(t.size_bytes),
                    _keys_label(t, t.attribute_types),
                    _billing_label(t),
                    str(len(t.indexes)),
                    human_money(cost),
                    _Tone(str(len(found)), "warn" if found else ""),
                    human_age(t.created),
                ]
            )
        if metrics:
            basis = f"storage, capacity, backups and on-demand requests at the last 24h's rate, at {self._price_basis()}"
        else:
            basis = f"storage, capacity and backups at {self._price_basis()} (on-demand requests not included)"
        blocks: list[Any] = [
            _Title(
                f"DynamoDB tables in {self.core.region} ({len(reports)})",
                (f"names matching {match!r} · " if match else "")
                + "item counts and sizes are DynamoDB's "
                f"estimates, refreshed about every 6 hours · cost is {basis}",
            ),
            _Cards(
                [
                    ("Tables", f"{len(reports):,}"),
                    (
                        "Total size",
                        human_size(sum(r.info.size_bytes or 0 for r in reports)),
                    ),
                    ("Est. cost / month", human_money(total)),
                    (
                        "Tables with warnings",
                        f"{len({name for name, _ in warnings}):,}",
                        "warn" if warnings else "ok",
                    ),
                ]
            ),
        ]
        if not reports:
            where = f"matching {match!r} " if match else ""
            blocks.append(
                _Note(
                    f"No tables {where}in {self.core.region}. Tables are regional: try "
                    "DynamoDBView(DynamoDBAnalyzer(region='eu-west-1'))."
                )
            )
            self._show(blocks)
            return
        if unreadable:
            blocks.append(_Note(f"Couldn't describe {', '.join(unreadable)}.", "warn"))
        if no_usage:
            blocks.append(
                _Note(
                    f"No CloudWatch usage for {', '.join(no_usage[:10])}{' …' if len(no_usage) > 10 else ''}: "
                    "their on-demand request cost and capacity checks are missing."
                )
            )
        blocks.append(
            _Table(
                [
                    "Table",
                    "Status",
                    "Items",
                    "Size",
                    "Key",
                    "Billing",
                    "Indexes",
                    "Est. $/month",
                    "Warnings",
                    "Created",
                ],
                rows,
                max_rows=0,
            )
        )
        if warnings:
            blocks.append(
                _Table(
                    ["Table", "Warning"],
                    warnings,
                    prose_cols=(1,),
                    title="Warnings (table_info(name) shows every finding for one table)",
                    max_rows=0,
                )
            )
        readable = [r.info for r in reports if "describe" not in r.info.errors]
        if readable:
            flagged = Counter(name for name, _ in warnings).most_common(1)
            look = (
                flagged[0][0]
                if flagged
                else max(readable, key=lambda t: t.size_bytes or 0).name
            )
            blocks.append(
                _Next(
                    [
                        (
                            _call("table_info", look),
                            "why it's flagged, and what to change"
                            if flagged
                            else "its indexes and how to query each, usage and cost",
                        ),
                        (_call("sample", look), "a few of its items"),
                    ]
                )
            )
        self._show(blocks)

    @_friendly_errors
    def table_info(self, table: str, *, metrics: bool = True, hours: int = 24) -> None:
        """
        Keys, indexes and how to query each, capacity, usage, backups, cost and risks.
        Also TTL, stream, encryption and tags; usage is CloudWatch's last `hours`.
        """
        info = self.core.describe(table)
        blocks: list[Any] = [_Title(f"Table {info.name}", info.arn or self.core.region)]
        usage = None
        if metrics:
            try:
                usage = self.core.table_metrics(table, hours=hours)
            except (ClientError, BotoCoreError) as exc:
                blocks.append(
                    _Note(
                        f"No CloudWatch usage ({_why(_error_name(exc), 'cloudwatch:GetMetricData')}), so "
                        "the capacity checks and the on-demand request cost are missing.",
                        "warn",
                    )
                )
        types = info.attribute_types
        cost = table_monthly_cost(info, self.core.prices, usage)
        ttl = (
            f"on ({info.ttl_attribute})"
            if info.ttl_status == "ENABLED"
            else _TTL_STATES.get(
                info.ttl_status or "DISABLED", str(info.ttl_status).lower()
            )
        )
        pitr = (
            "off"
            if not info.pitr
            else "on" + (f", {info.pitr_days} days" if info.pitr_days else "")
        )
        encryption = info.encryption + (
            f" ({info.kms_key.rsplit('/', 1)[-1]})" if info.kms_key else ""
        )
        cards = [
            ("Status", info.status or "?", "" if info.status == "ACTIVE" else "warn"),
            ("Items (estimate)", _count(info.item_count)),
            ("Size (estimate)", human_size(info.size_bytes)),
            ("Average item", human_size(info.avg_item_size)),
            (
                "Partition key",
                f"{info.partition_key} ({TYPE_NAMES.get(types.get(info.partition_key, ''), '?')})",
            ),
            (
                "Sort key",
                f"{info.sort_key} ({TYPE_NAMES.get(types.get(info.sort_key, ''), '?')})"
                if info.sort_key
                else "none",
            ),
            ("Billing", _billing_label(info)),
            ("Est. cost / month", human_money(sum(cost.values()))),
            ("Table class", _TABLE_CLASSES.get(info.table_class, info.table_class)),
            ("Stream", _STREAM_VIEWS.get(info.stream, "on") if info.stream else "off"),
            ("TTL", _section(info, "ttl", ttl)),
            (
                "Point-in-time recovery",
                _section(info, "pitr", pitr),
                "warn" if info.pitr is False and "pitr" not in info.errors else "",
            ),
            ("Deletion protection", "on" if info.deletion_protection else "off"),
            ("Encryption", encryption),
            ("Created", _fmt_dt(info.created)),
        ]
        if info.replicas:
            cards.append(("Replicas", ", ".join(info.replicas)))
        blocks.append(_Cards(cards))
        blocks.append(
            _Findings(
                table_findings(info, usage, self.core.prices),
                empty="No issues found by these checks.",
            )
        )

        def capacity(idx: IndexInfo | None) -> list[str]:
            if info.on_demand:
                return []  # every row would say on-demand
            if idx is not None and idx.kind == "local":
                return ["the table's"]
            source = idx or info
            return [_capacity_label(source.read_capacity, source.write_capacity)]

        rows = [
            [
                "(table)",
                "table",
                _keys_label(info, types),
                "all attributes",
                _count(info.item_count),
                human_size(info.size_bytes),
                *capacity(None),
                _query_hint(info, None),
            ]
        ]
        rows += [
            [
                idx.name,
                f"{idx.kind} index",
                _keys_label(idx, types),
                _projection_label(idx),
                _count(idx.item_count),
                human_size(idx.size_bytes),
                *capacity(idx),
                _query_hint(info, idx),
            ]
            for idx in info.indexes
        ]
        headers = ["Read from", "Kind", "Key", "Projection", "Items", "Size"] + (
            [] if info.on_demand else ["Capacity"]
        )
        blocks.append(
            _Table(
                headers + ["Query with"],
                rows,
                title="Table and indexes",
                max_rows=0,
                code_cols=(len(headers),),
            )
        )
        labels = {
            "storage": "Storage (table + indexes)",
            "capacity": "Provisioned capacity",
            "requests": f"On-demand reads and writes (at the last {hours}h's rate)",
            "backup": "Point-in-time recovery",
        }
        blocks.append(
            _Table(
                ["Cost", "Est. $/month"],
                [[labels[k], human_money(v)] for k, v in cost.items()],
                title=f"Estimated monthly cost ({self._price_basis()})",
            )
        )
        if usage and usage.has_data:
            minutes = usage.period // 60
            rows = [
                [
                    "Read units",
                    _units(usage.read_units),
                    _units(usage.peak_reads),
                    "on-demand" if info.on_demand else _count(info.read_capacity),
                ],
                [
                    "Write units",
                    _units(usage.write_units),
                    _units(usage.peak_writes),
                    "on-demand" if info.on_demand else _count(info.write_capacity),
                ],
                ["Read throttle events", f"{usage.read_throttles:,}", "", ""],
                ["Write throttle events", f"{usage.write_throttles:,}", "", ""],
            ]
            blocks.append(
                _Table(
                    [
                        "Metric",
                        f"Last {hours}h",
                        f"Busiest {minutes} min, per second",
                        "Provisioned",
                    ],
                    rows,
                    title="Usage (CloudWatch, table only)",
                )
            )
        elif usage:
            blocks.append(
                _Note(
                    f"No reads or writes recorded in CloudWatch in the last {hours}h."
                )
            )
        if info.tags:
            blocks.append(
                _Table(
                    ["Tag", "Value"],
                    [[k, v] for k, v in sorted(info.tags.items())],
                    title="Tags",
                    collapsed=True,
                )
            )
        blocks.append(
            _Next(
                [
                    (_call("sample", info.name), "a few items from across the table"),
                    (
                        _call("schema", info.name),
                        "every attribute's types, fill rate and examples",
                    ),
                ]
            )
        )
        self._show(blocks)

    # ------------------------------------------------------------------- items

    @_friendly_errors
    def scan(
        self,
        table: str,
        n: int = 20,
        *,
        where: Any = None,
        index: str | None = None,
        attributes: str | Iterable[str] | None = None,
        scan_limit: int | None = 100_000,
    ) -> None:
        """
        Items from the start of the table (or an index) as a table; more() shows the next page.
        where= filters, e.g. where={'status': 'failed', 'total': ('>', 100)}.
        """

        def fetch(start: Any, tick: Callable[[int], None]) -> ItemPage:
            return self.core.scan(
                table,
                n,
                where=where,
                index=index,
                attributes=attributes,
                start_key=start,
                scan_limit=scan_limit,
                progress=tick,
            )

        scan_limit = _as_count(scan_limit, "scan_limit")
        empty = "The table is empty." if where is None else _NO_MATCH
        if where is not None and scan_limit:
            empty += f" Each page reads at most {scan_limit:,} items (scan_limit); pass scan_limit=None to read on."
        self._page(
            f"Scan {_target(table, index)}",
            f"where {describe_filter(where)}"
            if where
            else "from the start of the table",
            fetch,
            empty=empty,
        )

    @_friendly_errors
    def query(
        self,
        table: str,
        partition: Any,
        sort: Any = None,
        *,
        n: int = 20,
        where: Any = None,
        index: str | None = None,
        attributes: str | Iterable[str] | None = None,
        descending: bool = False,
    ) -> None:
        """
        Items sharing one partition key, in sort-key order: query('orders', 'USER#42', sort=('begins_with', 'ORDER#')).
        index= queries a secondary index; descending=True starts from the highest sort key.
        """
        keys = self.core.keys(table, index)

        def fetch(start: Any, tick: Callable[[int], None]) -> ItemPage:
            return self.core.query(
                table,
                partition,
                sort,
                n=n,
                where=where,
                index=index,
                attributes=attributes,
                descending=descending,
                start_key=start,
                progress=tick,
            )

        condition = f"{keys[0]} = {partition!r}"
        if sort is not None and len(keys) > 1:
            condition += ", " + describe_condition(keys[1], sort)
        sub = " · ".join(
            filter(
                None,
                [
                    f"where {describe_filter(where)}" if where else "",
                    "highest sort key first" if descending else "",
                ],
            )
        )
        empty = (
            f"No items with {keys[0]} = {partition!r}"
            + (" and that sort key" if sort is not None else "")
            + (" matching the filter" if where else "")
            + ". Keys are case-sensitive and typed: "
            "'42' (a string) and 42 (a number) are different keys."
        )
        self._page(
            f"Query {_target(table, index)}: {condition}", sub, fetch, empty=empty
        )

    @_friendly_errors
    def sample(
        self,
        table: str,
        n: int = 20,
        *,
        where: Any = None,
        index: str | None = None,
        attributes: str | Iterable[str] | None = None,
        scan_limit: int | None = 100_000,
    ) -> None:
        """
        About n items spread across the whole table (scan() only shows its start): a better first look.
        """

        def fetch(start: Any, tick: Callable[[int], None]) -> ItemPage:
            return self.core.sample(
                table,
                n,
                where=where,
                index=index,
                attributes=attributes,
                scan_limit=scan_limit,
                progress=tick,
            )

        sub = "spread across the table" + (
            f" · where {describe_filter(where)}" if where else ""
        )
        self._page(
            f"Sample of {_target(table, index)}",
            sub,
            fetch,
            empty="The table is empty." if where is None else _NO_MATCH,
        )

    @_friendly_errors
    def more(self) -> None:
        """
        Next page of the last scan / query / sql.
        """
        if self._pager is None:
            self._show(
                [
                    _Note(
                        "Nothing to continue: the last scan, query or sql returned everything "
                        "(or none has run yet)."
                    )
                ]
            )
            return
        title, sub, fetch, empty, start, number = self._pager
        self._page(title, sub, fetch, empty=empty, start=start, number=number)

    @_friendly_errors
    def get(self, table: str, *key: Any, as_json: bool = False) -> None:
        """
        One item by primary key with nested maps and lists expanded: get('orders', 'USER#42', 'ORDER#0017').
        The key can also be a dict: get('orders', {'pk': ..., 'sk': ...}). as_json=True adds a JSON copy.
        """
        primary = self.core.primary_key(table, key)
        item = self.core.get(table, primary)
        blocks: list[Any] = [
            _Title(
                f"Item in {table}",
                ", ".join(f"{k} = {format_value(v, 60)}" for k, v in primary.items()),
            )
        ]
        if item is None:
            blocks.append(
                _Note(
                    "No item with this key. Keys are case-sensitive and typed: '42' (a string) and "
                    "42 (a number) are different keys.",
                    "warn",
                )
            )
            self._show(blocks)
            return
        size = item_size(item)
        blocks.append(
            _Cards(
                [
                    ("Attributes", f"{len(item):,}"),
                    (
                        "Size (estimate)",
                        human_size(size),
                        "warn" if size > 300 * KB else "",
                    ),
                    (
                        "Read cost",
                        f"{_units(read_units(size))} read unit{'' if read_units(size) == 1 else 's'} "
                        f"({_units(read_units(size, consistent=False))} if eventually consistent)",
                    ),
                    ("Write cost", _plural(write_units(size), "write unit")),
                ]
            )
        )
        if size > 300 * KB:
            blocks.append(
                _Note(
                    f"This item is {human_size(size)}; DynamoDB rejects items over 400 KB. {_LARGE_ITEM_ADVICE}",
                    "warn",
                )
            )
        blocks.append(
            _Table(
                ["Attribute", "Type", "Value"],
                _item_rows(item, list(primary)),
                tree=True,
                max_rows=0,
            )
        )
        if as_json:
            blocks.append(_Text(to_json(item, indent=2), title="JSON"))
        keys = list(primary)
        if len(keys) > 1:
            blocks.append(
                _Next(
                    [
                        (
                            _call("query", table, primary[keys[0]]),
                            f"every item with {keys[0]} = {format_value(primary[keys[0]], 40)}",
                        )
                    ]
                )
            )
        self._show(blocks)

    @_friendly_errors
    def sql(self, statement: str, *parameters: Any, n: int = 50) -> None:
        """
        Run a SQL-like (PartiQL) statement: sql('SELECT * FROM "orders" WHERE pk = ?', 'USER#42'); more() continues.
        Without the partition key in WHERE, a SELECT scans the whole table.
        """

        def fetch(start: Any, tick: Callable[[int], None]) -> ItemPage:
            return self.core.sql(
                statement, *parameters, n=n, next_token=start, progress=tick
            )

        sub = _clip(" ".join(statement.split()), 200) + (
            f"  with {list(parameters)!r}" if parameters else ""
        )
        self._page("PartiQL", sub, fetch, empty="No items came back.")

    # ---------------------------------------------------------------- analysis

    @_friendly_errors
    def schema(
        self,
        table: str,
        n: int = 1000,
        *,
        where: Any = None,
        index: str | None = None,
        spread: bool = True,
        max_depth: int = 2,
    ) -> None:
        """
        What the items look like: every attribute's types, fill rate, examples and range, key patterns.
        Map fields are nested under their map. Reads about n items spread across the table.
        """
        with self._progress("Sampling") as tick:
            p = self.core.profile(
                table,
                n,
                where=where,
                index=index,
                spread=spread,
                max_depth=max_depth,
                progress=tick,
            )
        of = f" of ~{p.approx_item_count:,}" if p.approx_item_count else ""
        how = "spread across the table" if spread else "from the start of the table"
        blocks: list[Any] = [
            _Title(
                f"Schema of {_target(table, index)}",
                f"{p.items:,} items{of} profiled, {how}"
                + (f" · where {describe_filter(where)}" if where else ""),
            )
        ]
        if not p.items:
            blocks.append(_Note("No items to profile."))
            self._show(blocks)
            return
        top = sum(1 for a in p.attributes.values() if a.depth == 0)
        blocks.append(
            _Cards(
                [
                    ("Items profiled", f"{p.items:,}"),
                    ("Attributes", f"{top:,}"),
                    ("Map fields", f"{len(p.attributes) - top:,}"),
                    ("Average item", human_size(p.avg_size)),
                    ("Largest item", human_size(p.max_size)),
                    ("Read units", _units(p.stats.read_units)),
                ]
            )
        )
        blocks.append(_Findings(profile_findings(p)))
        info = self.core.table(table)
        roles = {
            info.partition_key: "partition key",
            **({info.sort_key: "sort key"} if info.sort_key else {}),
        }
        if index:
            idx = info.index(index)
            roles.update(
                {
                    idx.partition_key: f"{index} partition key",
                    **({idx.sort_key: f"{index} sort key"} if idx.sort_key else {}),
                }
            )
        rows = [
            [
                "    " * a.depth
                + a.name
                + (f"  ({roles[a.path]})" if a.depth == 0 and a.path in roles else ""),
                _types_label(a),
                _distinct_label(a),
                ", ".join(format_value(v, 30) for v in a.examples),
                _range_label(a),
            ]
            for a in p.attributes.values()
        ]
        blocks.append(
            _Table(
                ["Attribute", "Type", "Distinct", "Examples", "Range"],
                rows,
                title="Attributes",
                bars=[_share(a.count, p.items) for a in p.attributes.values()],
                bar_label="Present in",
                tree=True,
                max_rows=300,
            )
        )
        for key, patterns in p.key_patterns.items():
            total = sum(patterns.values())
            shown = list(patterns.items())[:15]
            blocks.append(
                _Table(
                    ["Pattern", "Items"],
                    [[pattern, f"{count:,}"] for pattern, count in shown],
                    title=f"Key patterns: {key}"
                    + (f" ({roles[key]})" if key in roles else ""),
                    bars=[_share(count, total) for _, count in shown],
                    bar_label="% of items",
                )
            )
        blocks.append(
            _Table(
                ["Item size", "Items"],
                [[label, f"{st.count:,}"] for label, st in p.size_histogram.items()],
                title="Item sizes",
                bars=[_share(st.count, p.items) for st in p.size_histogram.values()],
                bar_label="% of items",
            )
        )
        blocks.append(
            _Table(
                [*p.keys, "Size", "Read units"],
                [
                    [format_value(key.get(k), 60) for k in p.keys]
                    + [human_size(size), _units(read_units(size))]
                    for size, key in p.largest
                ],
                title=f"Largest {len(p.largest)} items",
            )
        )
        grouping = [
            a
            for a in p.attributes.values()
            if a.depth == 0
            and a.path not in roles
            and a.main_type in ("S", "N", "BOOL")
            and 1 < a.distinct <= 50
            and not a.distinct_capped
        ]
        on_index = {"index": index} if index else {}
        steps = (
            [
                (
                    _call(
                        "value_counts",
                        table,
                        max(grouping, key=lambda a: a.count).path,
                        **on_index,
                    ),
                    "how often each of its values occurs",
                )
            ]
            if grouping
            else []
        )
        steps += (
            [(_call("largest", table), "the biggest items in the table")]
            if p.max_size > 100 * KB
            else []
        )
        blocks.append(_Next(steps))
        self._show(blocks)

    @_friendly_errors
    def value_counts(
        self,
        table: str,
        attribute: str,
        *,
        limit: int | None = 10_000,
        where: Any = None,
        index: str | None = None,
        top: int = 30,
    ) -> None:
        """
        How often each value of an attribute occurs ('address.city' reaches into maps).
        On the partition key this is each item collection's size: hot or oversized partitions stand out.
        """
        limit, top = _as_count(limit, "limit"), _as_int(top, "top")
        with self._progress() as tick:
            vc = self.core.value_counts(
                table, attribute, limit=limit, where=where, index=index, progress=tick
            )
        st = vc.stats
        sub = f"{st.scanned:,} items read" + (
            f"; stopped at limit={limit:,}, pass limit=None to read the whole table"
            if st.truncated
            else " (the whole table)"
        )
        blocks: list[Any] = [
            _Title(
                f"Values of {attribute} in {_target(table, index)}",
                sub + (f" · where {describe_filter(where)}" if where else ""),
            ),
            _Cards(
                [
                    ("Items", f"{vc.items:,}"),
                    ("Distinct values", f"{len(vc.counts):,}"),
                    ("Without it", f"{vc.missing.count:,}"),
                    ("Read units", _units(st.read_units)),
                    ("Read cost (on-demand)", self._read_cost(st)),
                ]
            ),
        ]
        if attribute == self.core.keys(table, index)[0]:
            blocks.append(
                _Note(
                    "This is the partition key, so each count is the size of one item collection. "
                    "A few values holding most of the items (or data) can mean hot partitions."
                )
            )
        shown = list(vc.counts.items())[:top]
        rows = [
            [format_value(value), f"{s.count:,}", human_size(s.size)]
            for value, s in shown
        ]
        bars = [_share(s.count, vc.items) for _, s in shown]
        if vc.missing.count:
            rows.append(
                ["(not set)", f"{vc.missing.count:,}", human_size(vc.missing.size)]
            )
            bars.append(_share(vc.missing.count, vc.items))
        blocks.append(
            _Table(
                ["Value", "Items", "Size"],
                rows,
                bars=bars,
                bar_label="% of items",
                max_rows=0,
                title=f"Top {len(shown)} values" if len(vc.counts) > top else "Values",
            )
        )
        if len(vc.counts) > top:
            blocks.append(
                _Note(
                    f"{len(vc.counts) - top:,} more values not shown; pass top= for more."
                )
            )
        common = shown[0][0] if shown else None
        if isinstance(common, (str, int, float, Decimal)) and not isinstance(
            common, bool
        ):
            on_index = {"index": index} if index else {}
            biggest = shown[0][1].count
            if attribute == self.core.keys(table, index)[0]:
                steps = (
                    [
                        (
                            _call("query", table, common, **on_index),
                            f"the {biggest:,} items of the biggest item collection",
                        )
                    ]
                    if biggest > 1
                    else []
                )
            else:
                steps = [
                    (
                        _call("scan", table, where={attribute: common}, **on_index),
                        f"items where {attribute} = {format_value(common, 30)}",
                    )
                ]
            blocks.append(_Next(steps))
        self._show(blocks)

    @_friendly_errors
    def largest(
        self,
        table: str,
        n: int = 10,
        *,
        limit: int | None = 10_000,
        where: Any = None,
        index: str | None = None,
    ) -> None:
        """
        The biggest items (DynamoDB's size rules; the limit is 400 KB) among the first `limit` items read.
        """
        n, limit = _as_int(n, "n"), _as_count(limit, "limit")
        with self._progress() as tick:
            page = self.core.largest(
                table, n, limit=limit, where=where, index=index, progress=tick
            )
        st = page.stats
        sizes = [item_size(item) for item in page.items]
        sub = f"{st.scanned:,} items read" + (
            f"; stopped at limit={limit:,}, pass limit=None to read the whole table"
            if st.truncated
            else " (the whole table)"
        )
        blocks: list[Any] = [
            _Title(f"Largest items in {_target(table, index)}", sub),
            _Cards(
                [
                    ("Largest", human_size(max(sizes, default=None))),
                    ("Read units", _units(st.read_units)),
                    ("Read cost (on-demand)", self._read_cost(st)),
                    ("Time", f"{st.seconds:.1f}s"),
                ]
            ),
        ]
        if any(size > 300 * KB for size in sizes):
            blocks.append(
                _Note(
                    f"Some items are over 300 KB, close to DynamoDB's 400 KB item limit. {_LARGE_ITEM_ADVICE}",
                    "warn",
                )
            )
        blocks.append(
            _Table(
                [*page.keys, "Size", "Read units", "Attributes"],
                [
                    [format_value(item.get(k), 60) for k in page.keys]
                    + [human_size(size), _units(read_units(size)), f"{len(item):,}"]
                    for item, size in zip(page.items, sizes)
                ],
            )
        )
        blocks.append(_Note("Sizes are estimates using DynamoDB's sizing rules."))
        blocks.append(_Next(self._item_steps(page)))
        self._show(blocks)

    @_friendly_errors
    def count(self, table: str, *, where: Any = None, index: str | None = None) -> None:
        """
        Exact item count (optionally of items matching where=): reads, and pays for, every item.
        """
        with self._progress("Counting") as tick:
            st = self.core.count(table, where=where, index=index, progress=tick)
        info = self.core.table(table)
        estimate = info.index(index).item_count if index else info.item_count
        self._show(
            [
                _Title(
                    f"Count of {_target(table, index)}",
                    f"where {describe_filter(where)}" if where else "full scan",
                ),
                _Cards(
                    [
                        ("Items" + (" matching" if where else ""), f"{st.matched:,}"),
                        ("Items read", f"{st.scanned:,}"),
                        ("Read units", _units(st.read_units)),
                        ("Read cost (on-demand)", self._read_cost(st)),
                        ("Time", f"{st.seconds:.1f}s"),
                        ("DynamoDB's estimate", _count(estimate)),
                    ]
                ),
            ]
        )
