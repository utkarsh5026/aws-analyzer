from datetime import datetime, timedelta, timezone

import boto3
import pytest
from moto import mock_aws

import dynamodb as ddbmod
from dynamodb import (
    DYNAMODB_PRICES,
    GB,
    KB,
    DynamoDBAnalyzer,
    DynamoDBView,
    TableInfo,
    TableMetrics,
    build_filter,
    count_values,
    describe_filter,
    dynamo_type,
    flatten_item,
    format_value,
    from_dynamo,
    item_size,
    items_table,
    items_to_df,
    key_pattern,
    parse_table,
    profile_findings,
    profile_items,
    read_units,
    request_cost,
    table_findings,
    table_monthly_cost,
    to_dynamo,
    write_units,
)

TABLE = "orders"
STATUSES = ["paid", "shipped", "failed"]


# ----------------------------------------------------------------------------- helpers


def test_to_and_from_dynamo_round_trip():
    value = {"s": "x", "i": 42, "f": 1.5, "b": b"\x00\x01", "t": True, "none": None,
             "m": {"a": [1, "two", {"deep": False}]}, "ss": {"a", "b"}, "ns": {1, 2.5}, "bs": {b"x"}}
    assert to_dynamo(1.5) == {"N": "1.5"} and to_dynamo("x") == {"S": "x"}
    assert from_dynamo(to_dynamo(value)) == value
    assert isinstance(from_dynamo({"N": "42"}), int) and from_dynamo({"N": "1E+2"}) == 100
    assert from_dynamo({"B": "AAE="}) == b"\x00\x01"  # S3 exports hold binary as base64 text


def test_to_dynamo_explains_unsupported_types():
    with pytest.raises(ValueError, match="ISO strings"):
        to_dynamo(datetime(2024, 5, 1))


@pytest.mark.parametrize("value, expected", [
    ("x", "S"), (1, "N"), (1.5, "N"), (True, "BOOL"), (None, "NULL"), (b"x", "B"), ({"a": 1}, "M"),
    ([1], "L"), ({"a"}, "SS"), ({1, 2}, "NS"), ({b"x"}, "BS"),
])
def test_dynamo_type(value, expected):
    assert dynamo_type(value) == expected


def test_item_size_follows_dynamodb_rules():
    assert item_size({"pk": "abc"}) == 2 + 3
    assert item_size({"n": 123}) == 1 + 3  # 3 significant digits -> 2 bytes + 1
    assert item_size({"n": 1000}) == 1 + 2  # trailing zeros don't count
    assert item_size({"m": {"a": 1}}) == 1 + 3 + (1 + 2 + 1)  # map overhead, name, value, element overhead
    assert item_size({"l": ["ab", True]}) == 1 + 3 + (2 + 1) + (1 + 1)
    assert item_size({"ss": {"a", "bc"}}) == 2 + 3
    assert (read_units(100), read_units(4 * KB + 1), read_units(9 * KB, consistent=False)) == (1, 2, 1.5)
    assert (write_units(100), write_units(1500)) == (1, 2)


def test_format_value():
    assert format_value("") == '""' and format_value(None) == "null" and format_value(True) == "true"
    assert format_value({"b": {2, 1}}) == '{"b": [1, 2]}'
    assert format_value(b"\x00\x01") == "b64:AAE="
    assert format_value("line1\nline2") == "line1 ↵ line2"
    assert format_value("x" * 100, 10) == "x" * 9 + "…"


def test_flatten_item_and_items_table():
    assert flatten_item({"a": {"b": {"c": 1}, "d": []}, "e": {}}) == {"a.b.c": 1, "a.d": [], "e": {}}
    items = [{"sk": 1, "pk": "a", "rare": 1, "addr": {"city": "x"}},
             {"pk": "b", "sk": 2, "addr": {"city": "y", "zip": "1"}, "common": 1},
             {"pk": "c", "sk": 3, "common": 2, "addr": {"zip": "2"}}]
    columns, rows = items_table(items, keys=["pk", "sk"])
    assert columns == ["pk", "sk", "addr.city", "addr.zip", "common", "rare"]  # keys, then by fill, maps kept together
    assert rows[0] == {"sk": 1, "pk": "a", "rare": 1, "addr.city": "x"}
    assert items_table(items, keys=["pk"], flatten=False)[0] == ["pk", "sk", "addr", "common", "rare"]
    df = items_to_df(items, keys=["pk", "sk"])
    assert list(df.columns) == columns and list(df["pk"]) == ["a", "b", "c"]


@pytest.mark.parametrize("value, expected", [
    ("USER#42", "USER#<number>"),
    ("ORDER#2024-05-01#a1b2", "ORDER#<date>#<id>"),
    ("PROFILE", "PROFILE"),
    ("alice", "<text>"),
    ("tenant#acme#user#7", "tenant#<text>#<text>#<number>"),
    ("3f2b8c1e-1d2a-4c3b-9f00-0123456789ab", "<uuid>"),
    ("a@example.com", "<email>"),
    (7, "<number>"),
])
def test_key_pattern(value, expected):
    assert key_pattern(value) == expected


def test_build_filter():
    params = ddbmod._expression_params(
        where=build_filter({"status": "failed", "total": ("between", 1, 2.5), "sk": ("begins_with", "O"),
                            "gone": ("not_exists",), "kind": ("in", ["a", "b"]), "address.city": "Pune"}),
        attributes=["address.city", "lines[0]"])
    expression = params["FilterExpression"]
    assert "BETWEEN" in expression and "begins_with" in expression and "attribute_not_exists" in expression
    assert " IN " in expression
    assert params["ProjectionExpression"].count(".") == 1 and "[0]" in params["ProjectionExpression"]
    assert {"S": "failed"} in params["ExpressionAttributeValues"].values()
    assert {"N": "2.5"} in params["ExpressionAttributeValues"].values()  # floats are fine
    assert {"city", "address", "lines"} <= set(params["ExpressionAttributeNames"].values())
    assert build_filter(None) is None
    for bad in ({"a": ("~", 1)}, {"a": ("between", 1)}, {"a": ()}, ["status"]):
        with pytest.raises(ValueError):
            build_filter(bad)


def test_describe_filter():
    text = describe_filter({"status": "failed", "total": (">", 100), "sk": ("between", "a", "c"), "x": ("exists",)})
    assert text == "status = 'failed', total > 100, sk between 'a' and 'c', x exists"


def sample_items():
    return [
        {"pk": "USER#1", "sk": "PROFILE", "name": "a", "address": {"city": "Pune", "zip": "1"}},
        {"pk": "USER#1", "sk": "ORDER#1", "total": 10, "note": ""},
        {"pk": "USER#2", "sk": "ORDER#2", "total": "12.50", "note": "late"},
        {"pk": "USER#2", "sk": "ORDER#3", "total": 30.5, "blob": "x" * (310 * KB)},
    ]


def test_profile_items():
    p = profile_items(sample_items(), "orders", keys=["pk", "sk"], top_n=2)
    assert p.items == 4 and list(p.attributes)[:2] == ["pk", "sk"]
    paths = list(p.attributes)
    assert paths.index("address.city") == paths.index("address") + 1  # map fields right under their map
    total = p.attributes["total"]
    assert dict(total.types) == {"N": 2, "S": 1} and (total.low, total.high) == (10, 30.5)
    assert p.attributes["note"].empty == 1 and p.attributes["name"].examples == ["a"]
    assert p.attributes["pk"].distinct == 2 and p.attributes["address.city"].depth == 1
    assert p.key_patterns == {"pk": {"USER#<number>": 4}, "sk": {"ORDER#<number>": 3, "PROFILE": 1}}
    assert [key for _, key in p.largest] == [{"pk": "USER#2", "sk": "ORDER#3"}, {"pk": "USER#1", "sk": "PROFILE"}]
    assert p.size_histogram["300 - 400 KB"].count == 1
    text = " ".join(m for _, m in profile_findings(p))
    assert "'total' holds different types" in text and "'note'" in text and "over 300 KB" in text
    assert list(p.to_df()["attribute"]) == paths


def test_profile_items_empty():
    p = profile_items([], "t")
    assert p.items == 0 and p.avg_size == 0 and profile_findings(p) == []


def test_count_values():
    vc = count_values(sample_items() + [{"pk": "USER#3", "address": {"city": "Pune"}}], "address.city")
    assert vc.items == 5 and vc.missing.count == 3 and vc.counts["Pune"].count == 2
    assert list(count_values(sample_items(), "pk").counts) == ["USER#2", "USER#1"]  # tie on count: bigger first


def provisioned_description():
    return {
        "TableName": "t", "TableStatus": "ACTIVE", "TableArn": "arn:aws:dynamodb:us-east-1:1:table/t",
        "KeySchema": [{"AttributeName": "id", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "id", "AttributeType": "N"}, {"AttributeName": "g", "AttributeType": "S"}],
        "ItemCount": 1000, "TableSizeBytes": 2 * GB,
        "ProvisionedThroughput": {"ReadCapacityUnits": 10, "WriteCapacityUnits": 5},
        "GlobalSecondaryIndexes": [{"IndexName": "by-g", "KeySchema": [{"AttributeName": "g", "KeyType": "HASH"}],
                                    "Projection": {"ProjectionType": "INCLUDE", "NonKeyAttributes": ["a"]},
                                    "IndexStatus": "ACTIVE", "IndexSizeBytes": GB,
                                    "ProvisionedThroughput": {"ReadCapacityUnits": 10, "WriteCapacityUnits": 5}}],
        "SSEDescription": {"Status": "ENABLED", "SSEType": "KMS", "KMSMasterKeyArn": "arn:aws:kms:us-east-1:1:key/abc"},
        "StreamSpecification": {"StreamEnabled": True, "StreamViewType": "NEW_IMAGE"},
    }


def test_parse_table_and_monthly_cost():
    info = parse_table(provisioned_description())
    assert info.keys == ["id"] and info.attribute_types["id"] == "N" and not info.on_demand
    assert (info.read_capacity, info.write_capacity, info.stream, info.encryption) == (10, 5, "NEW_IMAGE", "KMS")
    idx = info.index("by-g")
    assert idx.projection == "INCLUDE" and idx.projected == ["a"] and idx.read_capacity == 10
    with pytest.raises(ValueError, match="by-g"):
        info.index("nope")
    info.pitr = True
    cost = table_monthly_cost(info)
    assert cost["storage"] == pytest.approx(3 * DYNAMODB_PRICES["storage"])
    assert cost["capacity"] == pytest.approx(730 * (20 * 0.00013 + 10 * 0.00065))
    assert cost["backup"] == pytest.approx(2 * DYNAMODB_PRICES["pitr"])
    assert request_cost(2_000_000, 1_000_000) == pytest.approx(0.25 + 0.625)
    on_demand = TableInfo("t", billing_mode="PAY_PER_REQUEST", size_bytes=GB)
    busy_day = TableMetrics("t", 24, 300, read_units=2_000_000, write_units=1_000_000)
    assert table_monthly_cost(on_demand, metrics=busy_day)["requests"] == pytest.approx((0.25 + 0.625) * 730 / 24)
    assert "requests" not in table_monthly_cost(on_demand)


def test_table_findings():
    info = parse_table(provisioned_description())
    info.pitr, info.deletion_protection = False, False
    busy = TableMetrics("t", 24, 300, read_units=1.0, write_units=1.0, peak_reads=9.0, peak_writes=0.1,
                        read_throttles=4)
    text = " ".join(m for _, m in table_findings(info, busy))
    assert "Point-in-time recovery is off" in text and "Deletion protection is off" in text
    assert "4 read and 0 write throttle events" in text
    assert "Reads averaged 9 units/s" in text and "Writes peaked at 2%" in text
    safe = TableInfo("t", status="ACTIVE", partition_key="id", pitr=True, deletion_protection=True)
    assert table_findings(safe) == []


# ------------------------------------------------------------------- AWS (moto)


@pytest.fixture
def aws():
    with mock_aws():
        yield boto3.client("dynamodb", region_name="us-east-1")


def put(client, table, item):
    client.put_item(TableName=table, Item={k: to_dynamo(v) for k, v in item.items()})


@pytest.fixture
def tables(aws):
    aws.create_table(
        TableName=TABLE, BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": name, "AttributeType": "S"} for name in ("pk", "sk", "status", "created")],
        GlobalSecondaryIndexes=[{"IndexName": "by-status", "Projection": {"ProjectionType": "ALL"}, "KeySchema": [
            {"AttributeName": "status", "KeyType": "HASH"}, {"AttributeName": "created", "KeyType": "RANGE"}]}],
        Tags=[{"Key": "team", "Value": "ml"}])
    aws.create_table(
        TableName="counters", BillingMode="PROVISIONED",
        ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "N"}])
    for user in range(5):
        put(aws, TABLE, {"pk": f"USER#{user}", "sk": "PROFILE", "name": f"user {user}", "vip": user == 1,
                         "address": {"city": "Pune" if user % 2 else "Delhi", "zip": "411001"}, "tags": {"new", "promo"},
                         "avatar": b"\x89PNG"})
        for order in range(8):
            put(aws, TABLE, {"pk": f"USER#{user}", "sk": f"ORDER#{order:04d}", "status": STATUSES[order % 3],
                             "created": f"2024-05-{order + 1:02d}", "total": order * 10.5,
                             "amount": "zero" if (user, order) == (0, 0) else order,
                             "lines": [{"sku": "A", "qty": order}], "note": "" if order == 3 else "ok"})
    put(aws, TABLE, {"pk": "USER#9", "sk": "BLOB", "payload": "x" * (310 * KB)})
    for i in range(1, 6):
        put(aws, "counters", {"id": i, "count": i * 10})
    return aws


@pytest.fixture
def core(tables):
    return DynamoDBAnalyzer(region="us-east-1")


def keys_of(items):
    return [(item["pk"], item["sk"]) for item in items]


def test_list_tables_and_describe(core):
    infos = {t.name: t for t in core.list_tables()}
    assert set(infos) == {TABLE, "counters"}
    orders = infos[TABLE]
    assert orders.keys == ["pk", "sk"] and orders.on_demand and orders.status == "ACTIVE"
    assert [(i.name, i.kind, i.keys) for i in orders.indexes] == [("by-status", "global", ["status", "created"])]
    assert infos["counters"].read_capacity == 5 and infos["counters"].attribute_types == {"id": "N"}
    info = core.describe(TABLE)
    assert info.tags == {"team": "ml"} and info.pitr is False and info.ttl_status == "DISABLED"
    assert "Point-in-time recovery is off" in " ".join(m for _, m in table_findings(info))
    assert core.keys(TABLE, "by-status") == ["status", "created"]
    assert core.key_attributes(TABLE, "by-status") == ["status", "created", "pk", "sk"]


def test_get(core):
    item = core.get(TABLE, "USER#1", "PROFILE")
    assert item["address"] == {"city": "Pune", "zip": "411001"} and item["tags"] == {"new", "promo"}
    assert item["vip"] is True and item["avatar"] == b"\x89PNG"
    assert core.get(TABLE, {"pk": "USER#1", "sk": "PROFILE"}) == item
    assert core.get(TABLE, "USER#1", "NOPE") is None
    assert core.get("counters", "3") == {"id": 3, "count": 30}  # '3' matched to the number key
    with pytest.raises(ValueError):
        core.get(TABLE, "USER#1")
    with pytest.raises(ValueError):
        core.get("counters", "three")


def page_through(read, **kwargs):
    seen, start, pages = [], None, 0
    while True:
        page = read(start_key=start, **kwargs)
        seen += page.items
        pages += 1
        if not page.has_more:
            return seen, pages
        start = page.last_key


def test_scan_pages_resume_exactly(core):
    items, pages = page_through(lambda **kw: core.scan(TABLE, 7, **kw))
    assert len(items) == 46 == len(set(keys_of(items))) and pages == 7
    failed, _ = page_through(lambda **kw: core.scan(TABLE, 3, where={"status": "failed"}, **kw))
    assert len(failed) == 10 == len(set(keys_of(failed))) and {i["status"] for i in failed} == {"failed"}
    by_status, _ = page_through(lambda **kw: core.scan(TABLE, 4, index="by-status", **kw))
    assert len(by_status) == 40 == len(set(keys_of(by_status)))


def test_scan_details(core):
    page = core.scan(TABLE, 5, attributes=["name"])
    assert page.keys == ["pk", "sk"] and all(set(i) <= {"pk", "sk", "name"} for i in page.items)
    assert page.stats.scanned == 5 and page.stats.read_units > 0 and page.has_more
    capped = core.scan(TABLE, 50, where={"status": "failed"}, scan_limit=10)
    assert capped.stats.scanned == 10 and capped.has_more and len(capped.items) < 10
    assert len(core.scan(TABLE, None).items) == 46
    assert list(page.to_df().columns)[:2] == ["pk", "sk"]
    with pytest.raises(ValueError):
        core.scan(TABLE, 0)


def test_query(core):
    orders = core.query(TABLE, "USER#2", ("begins_with", "ORDER#"))
    assert [i["sk"] for i in orders.items] == [f"ORDER#{o:04d}" for o in range(8)]
    assert len(core.query(TABLE, "USER#2", ("between", "ORDER#0002", "ORDER#0004")).items) == 3
    assert len(core.query(TABLE, "USER#2", (">=", "ORDER#0006")).items) == 3  # 0006, 0007, PROFILE
    assert core.query(TABLE, "USER#2", n=2, descending=True).items[0]["sk"] == "PROFILE"
    assert core.query(TABLE, "USER#2", "PROFILE").items[0]["name"] == "user 2"
    assert len(core.query(TABLE, "USER#2", where={"total": (">", 40)}).items) == 4
    all_failed, pages = page_through(lambda **kw: core.query(TABLE, "failed", n=3, index="by-status", **kw))
    assert len(all_failed) == 10 and pages == 4 and core.query(TABLE, "failed", index="by-status").keys[0] == "status"
    assert core.query("counters", "4").items == [{"id": 4, "count": 40}]
    with pytest.raises(ValueError, match="no sort key"):
        core.query("counters", 4, sort=5)
    with pytest.raises(ValueError, match="Sort key conditions"):
        core.query(TABLE, "USER#2", ("contains", "ORDER"))
    with pytest.raises(ValueError, match="no index"):
        core.query(TABLE, "x", index="nope")


def test_sample_spreads_over_segments(core):
    first = core.scan(TABLE, 10)
    spread = core.sample(TABLE, 10)
    assert len(spread.items) == 10 == len(set(keys_of(spread.items)))
    assert len({i["pk"] for i in spread.items}) > len({i["pk"] for i in first.items})
    assert len(core.sample(TABLE, 500).items) == 46  # small table: everything, once
    assert {i["status"] for i in core.sample(TABLE, 5, where={"status": "paid"}).items} == {"paid"}


def test_sql(core):
    page = core.sql('SELECT * FROM "orders" WHERE pk = ?', "USER#3")
    assert len(page.items) == 9 and page.keys == ["pk", "sk"] and page.table == TABLE
    assert ddbmod._FROM_RE.search('SELECT * FROM "orders"."by-status" WHERE x = 1').groups() == (
        "orders", None, "by-status")


def test_count_value_counts_largest(core):
    everything = core.count(TABLE)
    assert everything.matched == everything.scanned == 46 and everything.read_units > 0
    assert core.count(TABLE, where={"status": "failed"}).matched == 10
    vc = core.value_counts(TABLE, "status")
    assert {k: s.count for k, s in vc.counts.items()} == {"paid": 15, "shipped": 15, "failed": 10}
    assert vc.missing.count == 6 and vc.stats.scanned == 46 and not vc.stats.truncated
    assert core.value_counts(TABLE, "address.city").counts["Pune"].count == 2
    partial = core.value_counts(TABLE, "status", limit=10)
    assert partial.items == 10 and partial.stats.truncated
    biggest = core.largest(TABLE, 3)
    assert keys_of(biggest.items)[0] == ("USER#9", "BLOB") and len(biggest.items) == 3
    stats = ddbmod.ReadStats()
    assert len(list(core.iter_items(TABLE, limit=12, stats=stats))) == 12 and stats.truncated


def test_profile(core):
    p = core.profile(TABLE, 1000)
    assert p.items == 46 and p.approx_item_count == 46 and p.stats.read_units > 0
    assert p.attributes["amount"].types == {"N": 39, "S": 1}
    assert "address.city" in p.attributes and p.key_patterns["pk"] == {"USER#<number>": 46}
    assert p.largest[0][1] == {"pk": "USER#9", "sk": "BLOB"}
    assert core.profile(TABLE, 5, spread=False).items == 5


def test_table_metrics(core):
    cloudwatch = boto3.client("cloudwatch", region_name="us-east-1")
    now = datetime.now(timezone.utc) - timedelta(minutes=10)
    dimensions = [{"Name": "TableName", "Value": "counters"}]
    cloudwatch.put_metric_data(Namespace="AWS/DynamoDB", MetricData=[
        {"MetricName": "ConsumedReadCapacityUnits", "Dimensions": dimensions, "Timestamp": now, "Value": 1500.0},
        {"MetricName": "ReadThrottleEvents", "Dimensions": dimensions, "Timestamp": now, "Value": 3.0}])
    metrics = core.table_metrics("counters", hours=1)
    assert metrics.read_units == 1500 and metrics.peak_reads == 5.0 and metrics.read_throttles == 3
    assert metrics.write_units is None and metrics.has_data
    text = " ".join(m for _, m in table_findings(core.describe("counters"), metrics))
    assert "Reads averaged 5 units/s" in text and "3 read and 0 write throttle events" in text
    assert not core.table_metrics(TABLE).has_data


# ----------------------------------------------------------------------------- UI


@pytest.fixture
def ui(core):
    return DynamoDBView(core, mode="text")


def run(capsys, fn, *args, **kwargs):
    fn(*args, **kwargs)
    return capsys.readouterr().out


def test_ui_text_reports(ui, capsys):
    out = run(capsys, ui.tables)
    assert "DynamoDB tables in us-east-1 (2)" in out and "pk (S) + sk (S)" in out and "provisioned 5 R / 5 W" in out
    out = run(capsys, ui.table_info, TABLE)
    for expected in ("Point-in-time recovery", "by-status", "query('orders', <status>, sort=<created>, index='by-status')",
                     "Estimated monthly cost", "team"):
        assert expected in out
    out = run(capsys, ui.scan, TABLE, 3)
    assert "USER#0" in out and "call .more()" in out
    out = run(capsys, ui.get, TABLE, "USER#1", "PROFILE", as_json=True)
    assert "    city" in out and "string set" in out and "(partition key)" in out and '"zip": "411001"' in out
    out = run(capsys, ui.query, TABLE, "USER#2", ("begins_with", "ORDER#"), n=3)
    assert "sk begins_with 'ORDER#'" in out and "ORDER#0002" in out
    assert "status = 'failed'" in run(capsys, ui.query, TABLE, "failed", index="by-status")
    assert "spread across the table" in run(capsys, ui.sample, TABLE, 5)
    out = run(capsys, ui.schema, TABLE)
    for expected in ("Schema of orders", "Key patterns: pk", "USER#<number>", "'amount' holds different types",
                     "    city", "string set", "over 300 KB"):
        assert expected in out
    out = run(capsys, ui.value_counts, TABLE, "pk")
    assert "item collection" in out and "USER#0" in out
    assert "(not set)" in run(capsys, ui.value_counts, TABLE, "status")
    assert "BLOB" in run(capsys, ui.largest, TABLE, 2)
    out = run(capsys, ui.count, TABLE, where={"status": "failed"})
    assert "Items matching: 10" in out and "DynamoDB's estimate: 46" in out
    assert "USER#3" in run(capsys, ui.sql, 'SELECT * FROM "orders" WHERE pk = ?', "USER#3")
    assert "schema(" in run(capsys, ui.help)


def test_ui_more_pages_through(ui, capsys):
    assert "Nothing to continue" in run(capsys, ui.more)
    run(capsys, ui.query, TABLE, "USER#1", n=5)
    out = run(capsys, ui.more)
    assert "(page 2)" in out and "ORDER#0005" in out and "call .more()" not in out  # 9 items: 5 + 4
    assert "Nothing to continue" in run(capsys, ui.more)


def test_ui_notes_empty_results(ui, capsys):
    assert "Keys are case-sensitive" in run(capsys, ui.query, TABLE, "user#1")
    assert "No item with this key" in run(capsys, ui.get, TABLE, "USER#1", "NOPE")
    assert "No items matched" in run(capsys, ui.scan, TABLE, where={"status": "lost"})


def test_ui_turns_errors_into_notes(ui, capsys):
    out = run(capsys, ui.scan, "missing")
    assert "[!]" in out and "table not found in us-east-1" in out
    assert "no index 'nope'" in run(capsys, ui.query, TABLE, "x", index="nope")
    assert "ValueError" in run(capsys, ui.scan, TABLE, where={"status": ("~", 1)})
    assert "pass 2 values" in run(capsys, ui.get, TABLE, "USER#1")


def test_ui_hides_extra_columns(core, capsys):
    ui = DynamoDBView(core, mode="text", max_columns=3)
    assert "more attributes not shown" in run(capsys, ui.scan, TABLE, 2)


def test_ui_html_mode(core, monkeypatch):
    import IPython.display

    shown = []

    class Handle:
        def update(self, obj):
            pass

    def fake_display(obj, display_id=None):
        shown.append(obj.data)
        return Handle() if display_id else None

    monkeypatch.setattr(IPython.display, "display", fake_display)
    ui = DynamoDBView(core, mode="html")
    ui.schema(TABLE)
    ui.get(TABLE, "USER#1", "PROFILE")
    html_out = "".join(shown)
    assert '<div class="ddb">' in html_out and 'class="fill"' in html_out and 'class="tree"' in html_out


def test_html_escapes_item_values():
    blocks = [ddbmod._Title("<b>x</b>"), ddbmod._Table(["Value"], [["<script>alert(1)</script>"]])]
    rendered = ddbmod._render_html(blocks, 50)
    assert "<script>alert" not in rendered and "&lt;script&gt;" in rendered
