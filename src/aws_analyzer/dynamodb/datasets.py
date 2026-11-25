"""
DynamoDB's reports as data: the tables behind tables(), table_info(), scan() / query() / sample() / sql() /
largest(), schema() and value_counts(), each column said in plain words with how to read it, and the charts the
workbook draws from them. Items come as plain Python (numbers as int or float, not Decimal; nested maps as
'parent.child' columns), so pandas can add them up. Pure: every function takes what a command already read and
returns a ReportData (_kit/datasets.py).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .._kit.datasets import DataChart, DataColumn, DataTable, Recipe, ReportData
from .analysis import table_monthly_cost
from .helpers import GB, item_size, read_units
from .models import ItemPage, TableInfo, TableMetrics, TableProfile, TableReport, ValueCounts, items_table

_COST_PARTS = ("storage", "capacity", "requests", "backup")
_STATUS_TONES = (("CREATING", "warn"), ("UPDATING", "warn"), ("DELETING", "bad"), ("ARCHIVED", "bad"))
_OPERATIONS = {"scan": "Scan", "query": "Query", "sample": "Sample", "sql": "PartiQL", "largest": "Largest items"}


def _fraction(part: float | None, whole: float | None) -> float | None:
    """
    part / whole as a share from 0 to 1, or None without a whole.
    """
    if part is None or not whole:
        return None
    return part / whole


def _stem(command: str, name: str) -> str:
    return f"dynamodb-{command}-{name}"


def plain_value(value: Any) -> Any:
    """
    A DynamoDB value as pandas reads it best: a whole Decimal as int, any other as float, a set as a sorted list,
    lists inside with the same done; text, booleans, binary and None as they are.
    """
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (set, frozenset)):
        return sorted((plain_value(item) for item in value), key=str)
    if isinstance(value, list):
        return [plain_value(item) for item in value]
    if isinstance(value, dict):
        return {name: plain_value(item) for name, item in value.items()}
    return value


def _kind_of(values: list[Any]) -> str:
    """
    The column kind its values have: 'number' when every value there is a number, 'flag' when every one is a
    boolean, else 'text'.
    """
    present = [value for value in values if value is not None]
    if present and all(isinstance(value, bool) for value in present):
        return "flag"
    if present and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in present):
        return "number"
    return "text"


_TYPE_WORDS = ((bool, "true or false"), (int, "a number"), (float, "a number"), (str, "text"), (bytes, "binary"),
               (list, "a list or set"), (dict, "a map"))


def _type_words(values: list[Any]) -> str:
    """
    What a column's values are, in words: 'text', 'a number', 'a list or set'... or 'mixed: text, a number' when its
    items disagree (worth a look: code reading it must handle both).
    """
    kinds = []
    for value in values:
        if value is None:
            continue
        word = next((words for kind, words in _TYPE_WORDS if isinstance(value, kind)), "other")
        if word not in kinds:
            kinds.append(word)
    if len(kinds) > 1:
        return f"mixed: {', '.join(kinds)}"
    return kinds[0] if kinds else "always empty here"


def _item_columns(columns: list[str], rows: list[dict[str, Any]], keys: list[str]) -> list[DataColumn]:
    """
    A column per attribute: the keys first, said as keys, then each attribute with how many items have it.
    """
    roles = {name: role for name, role in zip(keys, ("the partition key", "the sort key"))}
    described = []
    for name in columns:
        values = [row.get(name) for row in rows]
        having = sum(1 for value in values if value is not None)
        meaning = roles.get(name) or f"an attribute ({_type_words(values)}), in {having:,} of the {len(rows):,} items"
        if "." in name and name not in roles:
            meaning += f" (a field of the map {name.split('.', 1)[0]})"
        described.append(DataColumn(name, meaning, "id" if name in roles else _kind_of(values)))
    return described


def _page_table_rows(page: ItemPage) -> tuple[list[DataColumn], list[dict[str, Any]]]:
    columns, rows = items_table(page.items, page.keys)
    plain = [{name: plain_value(value) for name, value in row.items()} for row in rows]
    described = _item_columns(columns, plain, page.keys)
    sizes = [item_size(item) for item in page.items]
    for row, size in zip(plain, sizes):
        row["_size_bytes"] = size
        row["_read_units"] = read_units(size)
    described += [
        DataColumn("_size_bytes", "the item's size by DynamoDB's sizing rules (the limit is 400 KB)", "bytes",
                   bar=True, read="Over 300 KB is close to the limit, and every read of it costs more."),
        DataColumn("_read_units", "read units one strongly consistent read of it costs (4 KB each)", "number"),
    ]
    return described, plain


def items_data(page: ItemPage, *, title: str = "") -> ReportData:
    """
    scan()'s, query()'s, sample()'s, sql()'s or largest()'s page as data: a row per item, keys first, nested maps
    as 'parent.child' columns, each with its size.
    """
    columns, rows = _page_table_rows(page)
    st = page.stats
    where = f"{page.table} ({page.index})" if page.index else page.table
    notes = [f"{st.scanned:,} items read for these {len(rows):,}, costing {st.read_units:,.1f} read units."]
    if page.has_more:
        notes.append("There are more items: more() reads the next page, and data() then holds it.")
    notes.append("Numbers are int or float here (DynamoDB keeps them as exact decimals); lists and sets stay "
                 "Python lists in their cell.")
    table = DataTable(
        "items", title or f"{_OPERATIONS.get(page.operation, 'Items')} of {where}", "an item", columns, rows,
        notes=notes,
        recipes=[Recipe("df.describe(include='all').T", "each attribute summed up"),
                 Recipe("df.isna().mean().sort_values()", "how often each attribute is missing"),
                 Recipe("df.nlargest(5, '_size_bytes')", "the biggest items")])
    return ReportData(table.title, [table], stem=_stem(page.operation, page.table))


def _table_row(report: TableReport, prices: dict[str, float]) -> dict[str, Any]:
    info, metrics = report.info, report.metrics
    cost = table_monthly_cost(info, prices, metrics)
    row: dict[str, Any] = {
        "table": info.name, "status": info.status, "items": info.item_count, "size_bytes": info.size_bytes,
        "size_gb": (info.size_bytes or 0) / GB if info.size_bytes is not None else None,
        "partition_key": info.partition_key, "sort_key": info.sort_key,
        "billing": "on-demand" if info.on_demand else "provisioned",
        "read_capacity": info.read_capacity, "write_capacity": info.write_capacity,
        "indexes": len(info.indexes), "table_class": info.table_class, "pitr": info.pitr,
        "ttl_attribute": info.ttl_attribute, "stream": info.stream, "created": info.created,
    }
    row.update(_usage(metrics))
    row.update({f"{part}_usd": cost.get(part) for part in _COST_PARTS})
    row["est_monthly_usd"] = sum(cost.values())
    return row


def _usage(metrics: TableMetrics | None) -> dict[str, Any]:
    if metrics is None:
        return {"read_units_used": None, "write_units_used": None, "throttles": None}
    return {"read_units_used": metrics.read_units, "write_units_used": metrics.write_units,
            "throttles": metrics.read_throttles + metrics.write_throttles}


_TABLE_COLUMNS = [
    DataColumn("table", "the table's name", "id"),
    DataColumn("status", "ACTIVE, or CREATING, UPDATING, DELETING...", tones=_STATUS_TONES),
    DataColumn("items", "items in it: DynamoDB's estimate, refreshed about every 6 hours", "count"),
    DataColumn("size_bytes", "its size, the same estimate", "bytes"),
    DataColumn("size_gb", "the same in gigabytes", "number", bar=True),
    DataColumn("partition_key", "its partition key"),
    DataColumn("sort_key", "its sort key, if it has one"),
    DataColumn("billing", "on-demand (pay per request) or provisioned (pay per hour of capacity)"),
    DataColumn("read_capacity", "read capacity units provisioned", "count"),
    DataColumn("write_capacity", "write capacity units provisioned", "count"),
    DataColumn("indexes", "secondary indexes", "count"),
    DataColumn("table_class", "STANDARD, or STANDARD_INFREQUENT_ACCESS (cheaper storage, dearer requests)"),
    DataColumn("pitr", "whether point-in-time recovery (continuous backups) is on", "flag"),
    DataColumn("ttl_attribute", "the attribute TTL deletes expired items by, if it's on"),
    DataColumn("stream", "the stream's view type when a stream is on"),
    DataColumn("created", "when it was created", "time"),
    DataColumn("read_units_used", "read units consumed in the window CloudWatch was read for", "number"),
    DataColumn("write_units_used", "write units consumed in that window", "number"),
    DataColumn("throttles", "requests refused for lack of capacity in that window", "count",
               read="Any is a sign capacity is too low, or a partition is hot (value_counts on the key)."),
    DataColumn("storage_usd", "storage a month, table and indexes", "usd"),
    DataColumn("capacity_usd", "provisioned capacity a month at today's settings", "usd"),
    DataColumn("requests_usd", "on-demand requests a month, at the window's pace", "usd"),
    DataColumn("backup_usd", "point-in-time recovery a month", "usd"),
    DataColumn("est_monthly_usd", "all of it, estimated at list prices", "usd", bar=True),
]


def tables_data(reports: list[TableReport], prices: dict[str, float]) -> ReportData:
    """
    tables()' report as data: a row per table, the costliest first, with its cost by part.
    """
    rows = sorted((_table_row(report, prices) for report in reports if "describe" not in report.info.errors),
                  key=lambda row: -row["est_monthly_usd"])
    table = DataTable(
        "tables", "Every table", "a table", _TABLE_COLUMNS, rows,
        notes=["The costliest first. Costs are estimates at list prices; on-demand requests come from CloudWatch."],
        recipes=[Recipe("df[['table', 'storage_usd', 'capacity_usd', 'requests_usd', 'backup_usd']].set_index("
                        "'table').plot.barh(stacked=True)", "what each table's bill is made of"),
                 Recipe("df[df.billing == 'provisioned'][['table', 'read_capacity', 'read_units_used']]",
                        "provisioned capacity against what was used")])
    charts = [DataChart("Estimated monthly cost by table", "tables", "table", ("est_monthly_usd",), "bar",
                        "The costliest first.", "USD a month"),
              DataChart("What each table's bill is made of", "tables", "table",
                        ("storage_usd", "capacity_usd", "requests_usd", "backup_usd"), "bar", "", "USD a month")]
    return ReportData("DynamoDB tables", [table], charts, stem="dynamodb-tables")


_INDEX_COLUMNS = [
    DataColumn("index", "the index's name ('(table)' for the table itself)", "id"),
    DataColumn("kind", "table, global or local"),
    DataColumn("partition_key", "its partition key"),
    DataColumn("sort_key", "its sort key"),
    DataColumn("projection", "what it copies: ALL attributes, KEYS_ONLY, or INCLUDE some",
               read="KEYS_ONLY means a query on it reads the table again for the rest."),
    DataColumn("items", "items in it", "count"),
    DataColumn("size_bytes", "its size", "bytes", bar=True),
    DataColumn("status", "ACTIVE, or CREATING while it fills"),
    DataColumn("query", "the query() call that reads it, to copy"),
]


def _index_rows(info: TableInfo) -> list[dict[str, Any]]:
    rows = [{"index": "(table)", "kind": "table", "partition_key": info.partition_key, "sort_key": info.sort_key,
             "projection": "ALL", "items": info.item_count, "size_bytes": info.size_bytes, "status": info.status,
             "query": f"query({info.name!r}, <{info.partition_key}>)"}]
    for index in info.indexes:
        rows.append({"index": index.name, "kind": index.kind, "partition_key": index.partition_key,
                     "sort_key": index.sort_key, "projection": index.projection, "items": index.item_count,
                     "size_bytes": index.size_bytes, "status": index.status or "",
                     "query": f"query({info.name!r}, <{index.partition_key}>, index={index.name!r})"})
    return rows


def table_info_data(info: TableInfo) -> ReportData:
    """
    table_info()'s report as data: the table and each of its indexes, with the call that queries it.
    """
    table = DataTable("indexes", f"{info.name} and its indexes", "the table or one of its indexes", _INDEX_COLUMNS,
                      _index_rows(info),
                      notes=["Item counts and sizes are DynamoDB's estimates, refreshed about every 6 hours."])
    return ReportData(info.name, [table], stem=_stem("table", info.name))


_ATTRIBUTE_COLUMNS = [
    DataColumn("attribute", "the attribute ('address.city' for a field of a map)", "id"),
    DataColumn("depth", "0 for a top-level attribute, 1 for a field of a map, ...", "count"),
    DataColumn("type", "its most common type: S text, N number, BOOL, M map, L list, SS / NS sets, B binary"),
    DataColumn("types", "every type it has, with how many items, separated by '; ': more than one is worth a look"),
    DataColumn("items", "items that have it", "count"),
    DataColumn("fill_rate", "items that have it / items read", "share", bar=True,
               read="Under 1 means some items don't have it: code reading it must expect that."),
    DataColumn("distinct", "distinct values seen", "count",
               read="Equal to items means every value is different (an ID); a handful means a category."),
    DataColumn("min", "the smallest number"),
    DataColumn("max", "the largest number"),
    DataColumn("min_len", "the shortest text, binary or collection", "count"),
    DataColumn("max_len", "the longest", "count"),
    DataColumn("empty", "items where it's '' or empty", "count"),
    DataColumn("examples", "a few of its values"),
]


_TYPES_JOINER = "; "


def _attribute_rows(profile: TableProfile) -> list[dict[str, Any]]:
    rows = []
    for attribute in profile.attributes.values():
        rows.append({
            "attribute": attribute.path, "depth": attribute.depth, "type": attribute.main_type,
            "types": _TYPES_JOINER.join(f"{kind} ({count:,})" for kind, count in attribute.types.most_common()),
            "items": attribute.count, "fill_rate": _fraction(attribute.count, profile.items),
            "distinct": attribute.distinct, "min": plain_value(attribute.low), "max": plain_value(attribute.high),
            "min_len": attribute.min_len, "max_len": attribute.max_len, "empty": attribute.empty,
            "examples": ", ".join(str(plain_value(example)) for example in attribute.examples),
        })
    return rows


def schema_data(profile: TableProfile) -> ReportData:
    """
    schema()'s report as data: a row per attribute, how the keys are shaped, and the items by size.
    """
    attributes = DataTable(
        "attributes", f"The attributes of {profile.table}", "an attribute", _ATTRIBUTE_COLUMNS,
        _attribute_rows(profile),
        notes=[f"From {profile.items:,} items read, spread across the table."],
        recipes=[Recipe("df[df.fill_rate < 1].sort_values('fill_rate')", "attributes some items don't have"),
                 Recipe(f"df[df.types.str.contains({_TYPES_JOINER.strip()!r})]", "attributes with more than one type")])
    patterns = [{"key": key, "pattern": pattern, "items": count, "share": _fraction(count, profile.items)}
                for key, shapes in profile.key_patterns.items() for pattern, count in shapes.items()]
    keys = DataTable(
        "key_patterns", "How the keys are shaped", "a shape of a key's values",
        [DataColumn("key", "the key attribute", "id"),
         DataColumn("pattern", "its values with the variable parts replaced: USER#<n>, ORDER#<date>"),
         DataColumn("items", "items whose key has that shape", "count", bar=True),
         DataColumn("share", "its part of the items read", "share",
                    read="Several shapes on one key usually mean several kinds of item in one table.")],
        patterns)
    bands = [{"band": band, "items": stat.count, "size_bytes": stat.size, "share": _fraction(stat.count, profile.items)}
             for band, stat in profile.size_histogram.items()]
    sizes = DataTable("size_bands", "Items by size", "a size band",
                      [DataColumn("band", "a range of item sizes"),
                       DataColumn("items", "items in it", "count", bar=True),
                       DataColumn("size_bytes", "their size together", "bytes"),
                       DataColumn("share", "its part of the items read", "share")], bands)
    charts = [DataChart("How often each attribute is there", "attributes", "attribute", ("fill_rate",), "bar",
                        "1 is every item; shorter bars are attributes some items lack.", "fill rate"),
              DataChart("Items by size", "size_bands", "band", ("items",), "column", "", "items")]
    return ReportData(f"Schema of {profile.table}", [attributes, keys, sizes], charts,
                      stem=_stem("schema", profile.table))


def value_counts_data(counts: ValueCounts) -> ReportData:
    """
    value_counts()' report as data: a row per value, the most common first, and the items without it.
    """
    rows = [{"value": plain_value(value), "items": stat.count, "share": _fraction(stat.count, counts.items),
             "size_bytes": stat.size, "share_of_size": None} for value, stat in counts.counts.items()]
    total_size = sum(row["size_bytes"] for row in rows) + counts.missing.size
    if counts.missing.count:
        rows.append({"value": None, "items": counts.missing.count, "share": _fraction(counts.missing.count, counts.items),
                     "size_bytes": counts.missing.size, "share_of_size": None})
    for row in rows:
        row["share_of_size"] = _fraction(row["size_bytes"], total_size)
    columns = [DataColumn("value", f"a value of {counts.attribute} (empty: items without it)"),
               DataColumn("items", "items with it", "count", bar=True),
               DataColumn("share", "its part of the items read", "share",
                          read="On the partition key, one value far above the rest is a hot partition."),
               DataColumn("size_bytes", "those items' size together", "bytes"),
               DataColumn("share_of_size", "their part of the size read", "share")]
    table = DataTable("values", f"Values of {counts.attribute} in {counts.table}", "a value", columns, rows,
                      notes=[f"From {counts.items:,} items read; the most common first."],
                      recipes=[Recipe("df.head(10).set_index('value')['items'].plot.barh()", "the top ten")])
    charts = [DataChart(f"Most common values of {counts.attribute}", "values", "value", ("items",), "bar", "",
                        "items")]
    return ReportData(table.title, [table], charts, stem=_stem("values", f"{counts.table}-{counts.attribute}"))
