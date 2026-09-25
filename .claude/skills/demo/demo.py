"""Run View commands against realistic demo data in moto (a fake AWS in memory) and show what a user would see.

    python .claude/skills/demo/demo.py s3 'ui.summary("s3://demo-lake/")'
    python .claude/skills/demo/demo.py dynamodb 'ui.table_info("orders"); ui.scan("orders"); ui.more()'
    python .claude/skills/demo/demo.py dynamodb 'ui.schema("orders")' --html /tmp/schema.html --theme dark
    python .claude/skills/demo/demo.py s3 'ui.overview()' --live --profile dev     # the real account (read-only)

The code runs with `ui` (the View, text mode), `core` (the Analyzer), `mod` (the analyzer module) and the module
under its own name (`s3`, `dynamodb`, ...) in scope, so pure functions work too: 'print(mod.human_size(5e9))'.
Plain text goes to stdout, like a terminal user would see it. --html also writes the notebook (HTML) rendering
of every report to a standalone page.

Demo data (moto, us-east-1, everything synthetic):
  s3        demo-lake (versioned): raw/events/YYYY/MM/DD/ ~1,000 small gzipped JSON-lines files over 90 days,
            raw/backfill/ duplicates of a week of them, curated/orders/ parquet + curated/customers.csv (metadata,
            tags), logs/app/ backdated 1-3 years, archive/ in GLACIER, reports/monthly/ small STANDARD_IA files,
            reports/daily.csv overwritten 4 times, 3 deleted files under reports/, tmp/ with an empty file and an
            unfinished multipart upload, a policy sharing curated/ with another account and not requiring HTTPS,
            a lifecycle rule for tmp/, tags, CloudWatch size metrics. demo-models: a SageMaker-style
            xgboost/2024-06-01/output/model.tar.gz. demo-scratch: empty.
  dynamodb  orders (pk/sk, on-demand, GSI by-status): USER#<n> profiles + ORDER#<nnnn> items, nested maps,
            sets, a mixed-type attribute, empty strings, one ~330 KB item. sessions (provisioned 100/50, far above
            the CloudWatch usage seeded for it, with throttles). counters (numeric key, provisioned 5/5).
"""

from __future__ import annotations

import argparse
import gzip
import importlib
import io
import json
import os
import random
import sys
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "analyzers"))

NOW = datetime.now(timezone.utc).replace(tzinfo=None)  # moto keeps naive UTC timestamps


# ----------------------------------------------------------------------------- S3


def _backdate(bucket: str, key: str, when: datetime) -> None:
    """moto stamps objects with the current time; lifecycle and age reports need older data."""
    from moto.core.models import DEFAULT_ACCOUNT_ID
    from moto.s3.models import s3_backends

    for version in s3_backends[DEFAULT_ACCOUNT_ID]["aws"].buckets[bucket].keys.getlist(key):
        version.last_modified = when


def seed_s3() -> None:
    import boto3

    rng = random.Random(7)
    s3 = boto3.client("s3", region_name="us-east-1")
    lake = "demo-lake"
    s3.create_bucket(Bucket=lake)
    s3.put_bucket_versioning(Bucket=lake, VersioningConfiguration={"Status": "Enabled"})
    s3.put_bucket_tagging(Bucket=lake, Tagging={"TagSet": [{"Key": "team", "Value": "ml"},
                                                           {"Key": "env", "Value": "prod"}]})

    def put(key: str, body: bytes, *, age_days: float | None = None, **kwargs) -> None:
        s3.put_object(Bucket=lake, Key=key, Body=body, **kwargs)
        if age_days:
            _backdate(lake, key, NOW - timedelta(days=age_days))

    # raw/: over a thousand small gzipped JSON-lines files (the small-file problem), up to three months old.
    for day in range(90):
        when = NOW - timedelta(days=day)
        for part in range(12):
            rows = [{"event_id": f"{day:03d}{part:02d}{i:04d}", "user": f"USER#{rng.randrange(50)}",
                     "type": rng.choice(["view", "click", "purchase"]), "ts": when.isoformat() + "Z",
                     "value": round(rng.uniform(0, 250), 2)} for i in range(rng.randint(10, 60))]
            body = gzip.compress("\n".join(json.dumps(r) for r in rows).encode())
            put(f"raw/events/{when:%Y/%m/%d}/part-{part:04d}.json.gz", body, age_days=day + 0.1)
    # raw/backfill/: byte-for-byte copies of a week of events (duplicates).
    for day in range(7):
        when = NOW - timedelta(days=day)
        source = f"raw/events/{when:%Y/%m/%d}/part-0000.json.gz"
        body = s3.get_object(Bucket=lake, Key=source)["Body"].read()
        put(f"raw/backfill/{when:%Y-%m-%d}.json.gz", body, age_days=3)

    # curated/: parquet (when pyarrow is installed) and CSV.
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        for part in range(4):
            table = pa.table({"order_id": [f"ORDER#{part}{i:05d}" for i in range(5000)],
                              "user": [f"USER#{rng.randrange(50)}" for _ in range(5000)],
                              "total": [round(rng.uniform(5, 500), 2) for _ in range(5000)],
                              "status": [rng.choice(["paid", "shipped", "failed"]) for _ in range(5000)]})
            buffer = io.BytesIO()
            pq.write_table(table, buffer, row_group_size=1000)
            put(f"curated/orders/part-{part:05d}.parquet", buffer.getvalue(), age_days=20 + part)
    except ImportError:
        print("(pyarrow isn't installed: no parquet files in the demo bucket)", file=sys.stderr)
    csv = "user_id,name,city,signup\n" + "".join(
        f"USER#{i},user {i},{rng.choice(['Pune', 'Delhi', 'Mumbai'])},2024-0{1 + i % 9}-1{i % 10}\n" for i in range(50))
    put("curated/customers.csv", csv.encode(), ContentType="text/csv", Metadata={"source": "crm"},
        Tagging="pii=true", age_days=45)
    put("curated/_SUCCESS", b"", age_days=20)

    # logs/: old application logs in STANDARD (cold data a lifecycle rule would move).
    for month in range(12, 36, 2):
        lines = "".join(f"{NOW - timedelta(days=30 * month):%Y-%m-%d} INFO request {i} ok\n" for i in range(4000))
        put(f"logs/app/{NOW - timedelta(days=30 * month):%Y-%m}.log.gz", gzip.compress(lines.encode()) * 20,
            age_days=30 * month)

    # archive/: GLACIER.
    for year in (2021, 2022):
        put(f"archive/{year}/events.json.gz", os.urandom(300_000), StorageClass="GLACIER",
            age_days=(NOW.year - year) * 365)

    # reports/: overwritten (noncurrent versions) and deleted (delete markers) keys.
    for version in range(4):
        put("reports/daily.csv", f"day,total\n{version},{version * 100}\n".encode() * 2000)
    for name in ("q1.csv", "q2.csv", "old-model-metrics.json"):
        put(f"reports/{name}", os.urandom(50_000))
        s3.delete_object(Bucket=lake, Key=f"reports/{name}")
    # reports/monthly/: small files in STANDARD_IA, each billed as 128 KB.
    for month in range(1, 13):
        put(f"reports/monthly/2025-{month:02d}.csv", f"month,total\n{month},{month * 1234}\n".encode() * 50,
            StorageClass="STANDARD_IA", age_days=400 - 30 * month)
    put("tmp/empty.txt", b"")
    put("tmp/scratch.bin", os.urandom(10_000), age_days=40)

    # An upload that was started and never finished.
    upload = s3.create_multipart_upload(Bucket=lake, Key="tmp/big-export.parquet")
    s3.upload_part(Bucket=lake, Key="tmp/big-export.parquet", UploadId=upload["UploadId"], PartNumber=1,
                   Body=os.urandom(5 * 1024 * 1024))

    s3.put_bucket_policy(Bucket=lake, Policy=json.dumps({"Version": "2012-10-17", "Statement": [
        {"Sid": "PartnerRead", "Effect": "Allow", "Principal": {"AWS": "arn:aws:iam::210987654321:root"},
         "Action": ["s3:GetObject", "s3:ListBucket"],
         "Resource": [f"arn:aws:s3:::{lake}", f"arn:aws:s3:::{lake}/curated/*"]},
        {"Sid": "DenyUnencryptedUploads", "Effect": "Deny", "Principal": "*", "Action": "s3:PutObject",
         "Resource": f"arn:aws:s3:::{lake}/*",
         "Condition": {"StringNotEquals": {"s3:x-amz-server-side-encryption": "aws:kms"}}}]}))
    s3.put_bucket_lifecycle_configuration(Bucket=lake, LifecycleConfiguration={"Rules": [
        {"ID": "expire-tmp", "Status": "Enabled", "Filter": {"Prefix": "tmp/"}, "Expiration": {"Days": 7}}]})

    # demo-models: a SageMaker-style model artifact.
    s3.create_bucket(Bucket="demo-models")
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        for name, body in (("model.joblib", os.urandom(200_000)), ("code/inference.py", b"def model_fn(d): ...\n"),
                           ("metrics.json", json.dumps({"auc": 0.91}).encode())):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    s3.put_object(Bucket="demo-models", Key="xgboost/2024-06-01/output/model.tar.gz", Body=archive.getvalue())
    s3.create_bucket(Bucket="demo-scratch")

    # CloudWatch storage metrics: moto publishes StandardStorage and NumberOfObjects itself; add the other classes.
    listing = [o for page in s3.get_paginator("list_object_versions").paginate(Bucket=lake)
               for o in page.get("Versions", [])]
    by_type: dict[str, int] = {}
    for obj in listing:
        storage = {"GLACIER": "GlacierStorage", "STANDARD_IA": "StandardIAStorage"}.get(obj.get("StorageClass", ""))
        if storage:
            by_type[storage] = by_type.get(storage, 0) + obj["Size"]
    boto3.client("cloudwatch", region_name="us-east-1").put_metric_data(Namespace="AWS/S3", MetricData=[
        {"MetricName": "BucketSizeBytes", "Value": float(size), "Unit": "Bytes", "Timestamp": NOW - timedelta(hours=6),
         "Dimensions": [{"Name": "StorageType", "Value": storage}, {"Name": "BucketName", "Value": lake}]}
        for storage, size in by_type.items()])


# ----------------------------------------------------------------------------- DynamoDB


def seed_dynamodb() -> None:
    import boto3

    rng = random.Random(7)
    ddb = boto3.client("dynamodb", region_name="us-east-1")
    from boto3.dynamodb.types import TypeSerializer

    serialize = TypeSerializer().serialize

    def put(table: str, item: dict) -> None:
        from decimal import Decimal

        def fix(value):
            if isinstance(value, float):
                return Decimal(str(value))
            if isinstance(value, dict):
                return {k: fix(v) for k, v in value.items()}
            if isinstance(value, list):
                return [fix(v) for v in value]
            return value

        ddb.put_item(TableName=table, Item={k: serialize(fix(v)) for k, v in item.items()})

    ddb.create_table(
        TableName="orders", BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": n, "AttributeType": "S"} for n in ("pk", "sk", "status", "created")],
        GlobalSecondaryIndexes=[{"IndexName": "by-status", "Projection": {"ProjectionType": "ALL"}, "KeySchema": [
            {"AttributeName": "status", "KeyType": "HASH"}, {"AttributeName": "created", "KeyType": "RANGE"}]}],
        Tags=[{"Key": "team", "Value": "ml"}, {"Key": "env", "Value": "prod"}])
    cities = ["Pune", "Delhi", "Mumbai", "Bengaluru"]
    for user in range(40):
        put("orders", {"pk": f"USER#{user}", "sk": "PROFILE", "name": f"user {user}", "email": f"user{user}@example.com",
                       "vip": user % 7 == 0, "address": {"city": rng.choice(cities), "zip": f"4110{user % 10:02d}"},
                       "tags": {"new", "promo"} if user % 3 == 0 else {"returning"}})
        for order in range(rng.randint(3, 15)):
            day = NOW - timedelta(days=rng.randrange(365))
            status = rng.choices(["paid", "shipped", "failed", "refunded"], [5, 8, 1, 1])[0]
            put("orders", {"pk": f"USER#{user}", "sk": f"ORDER#{order:04d}", "status": status,
                           "created": f"{day:%Y-%m-%d}", "total": round(rng.uniform(5, 900), 2),
                           "amount": "n/a" if (user, order) == (3, 1) else order,  # one string among numbers
                           "lines": [{"sku": f"SKU-{rng.randrange(100):03d}", "qty": rng.randint(1, 4)}
                                     for _ in range(rng.randint(1, 4))],
                           "note": "" if order % 9 == 4 else "gift wrap" if order % 5 == 0 else "ok"})
    put("orders", {"pk": "USER#999", "sk": "EXPORT", "payload": "x" * (330 * 1024)})

    ddb.create_table(
        TableName="sessions", BillingMode="PROVISIONED",
        ProvisionedThroughput={"ReadCapacityUnits": 100, "WriteCapacityUnits": 50},
        KeySchema=[{"AttributeName": "session_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "session_id", "AttributeType": "S"}])
    for i in range(200):
        put("sessions", {"session_id": f"{rng.getrandbits(64):016x}", "user": f"USER#{rng.randrange(40)}",
                         "expires": int((NOW + timedelta(hours=rng.randrange(48))).timestamp())})

    ddb.create_table(
        TableName="counters", BillingMode="PROVISIONED",
        ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "N"}])
    for i in range(1, 11):
        put("counters", {"id": i, "count": i * 10})

    # sessions: about 3 read units/s against 100 provisioned, and a few throttles in a burst.
    cloudwatch = boto3.client("cloudwatch", region_name="us-east-1")
    dims = [{"Name": "TableName", "Value": "sessions"}]
    data = []
    for hour in range(1, 24):
        when = NOW - timedelta(hours=hour)
        data += [{"MetricName": "ConsumedReadCapacityUnits", "Dimensions": dims, "Timestamp": when,
                  "Value": 3.0 * 300 * rng.uniform(0.5, 1.5)},
                 {"MetricName": "ConsumedWriteCapacityUnits", "Dimensions": dims, "Timestamp": when,
                  "Value": 1.0 * 300 * rng.uniform(0.5, 1.5)}]
    data.append({"MetricName": "WriteThrottleEvents", "Dimensions": dims, "Timestamp": NOW - timedelta(hours=5),
                 "Value": 12.0})
    for start in range(0, len(data), 20):
        cloudwatch.put_metric_data(Namespace="AWS/DynamoDB", MetricData=data[start:start + 20])


SEEDERS = {"s3": seed_s3, "dynamodb": seed_dynamodb}


# ----------------------------------------------------------------------------- run


def _page(fragments: list[str], theme: str, title: str) -> str:
    scheme = {"light": "light", "dark": "dark"}.get(theme, "light dark")
    return (f'<!doctype html><html lang="en" style="color-scheme:{scheme}"><head><meta charset="utf-8">'
            f"<title>{title}</title><style>body{{margin:24px auto;max-width:1200px;padding:0 16px;"
            "background:Canvas;color:CanvasText}</style></head><body>"
            + "<hr style='opacity:.2;margin:28px 0'>".join(fragments) + "</body></html>")


def main() -> int:
    intro, _, rest = (__doc__ or "").partition("\n\n")
    parser = argparse.ArgumentParser(description=intro, epilog=rest,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("service", help="analyzer module name: s3, dynamodb, ...")
    parser.add_argument("code", help="Python to run, e.g. 'ui.summary(\"s3://demo-lake/\")'")
    parser.add_argument("--html", type=Path, help="also write the HTML rendering of every report to this file")
    parser.add_argument("--theme", choices=["auto", "light", "dark"], default="auto", help="page theme for --html")
    parser.add_argument("--max-rows", type=int, default=50, help="the View's max_rows")
    parser.add_argument("--live", action="store_true", help="use the real AWS account instead of moto")
    parser.add_argument("--region", help="with --live: region")
    parser.add_argument("--profile", help="with --live: AWS profile")
    args = parser.parse_args()

    if not (ROOT / "analyzers" / f"{args.service}.py").exists():
        names = ", ".join(sorted(p.stem for p in (ROOT / "analyzers").glob("*.py")))
        parser.error(f"no analyzers/{args.service}.py (have: {names})")

    mock = None
    if not args.live:
        for name, value in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                            "AWS_SESSION_TOKEN": "testing", "AWS_DEFAULT_REGION": "us-east-1"}.items():
            os.environ[name] = value
        os.environ.pop("AWS_PROFILE", None)
        try:
            from moto import mock_aws
        except ImportError:
            parser.error("moto isn't installed: pip install -r requirements-dev.txt")
        mock = mock_aws()
        mock.start()
        seeder = SEEDERS.get(args.service)
        if seeder:
            seeder()
        else:
            print(f"(no demo data for {args.service} yet: add a seed_{args.service}() to {Path(__file__).name}; "
                  "running against an empty account)", file=sys.stderr)

    try:
        mod = importlib.import_module(args.service)
        view_cls = next(v for k, v in vars(mod).items() if k.endswith("View") and isinstance(v, type))
        core_cls = next(v for k, v in vars(mod).items() if k.endswith("Analyzer") and isinstance(v, type))
        kwargs = {"region": args.region, "profile": args.profile} if args.live else {"region": "us-east-1"}
        core = core_cls(**{k: v for k, v in kwargs.items() if v})
        ui = view_cls(core, mode="text", max_rows=args.max_rows)
        fragments: list[str] = []

        def show(blocks: list) -> None:
            print(mod._render_text(blocks, ui.max_rows), flush=True)
            if args.html:
                fragments.append(mod._render_html(blocks, ui.max_rows))

        ui._show = show
        exec(compile(args.code, "<demo>", "exec"), {"ui": ui, "core": core, "mod": mod, args.service: mod})
        if args.html:
            args.html.parent.mkdir(parents=True, exist_ok=True)
            args.html.write_text(_page(fragments, args.theme, f"{args.service}: {args.code}"), encoding="utf-8")
            print(f"\n(HTML: {args.html.resolve()})", file=sys.stderr)
    finally:
        if mock is not None:
            mock.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
