"""Run View commands against realistic demo data in moto (a fake AWS in memory) and show what a user would see.

    python .claude/skills/demo/demo.py s3 'ui.summary("s3://demo-lake/")'
    python .claude/skills/demo/demo.py dynamodb 'ui.table_info("orders"); ui.scan("orders"); ui.more()'
    python .claude/skills/demo/demo.py dynamodb 'ui.schema("orders")' --html /tmp/schema.html --theme dark
    python .claude/skills/demo/demo.py bedrock_kb 'ui.kbs(); ui.ask("How long do refunds take?", kb="support-docs")'
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
  bedrock_kb  moto has no Bedrock, so the seeder hands the analyzer fake bedrock / bedrock-agent / -runtime
            clients that check every request and response against botocore's service model, answer from a small
            corpus of support documents (a toy ranker: SEMANTIC matches concepts and misses error codes, HYBRID
            also matches exact words) and sleep like the real calls. support-docs (OpenSearch Serverless):
            docs-s3 over moto's support-docs-bucket, with a failed sync, 2 failed and 1 ignored document, and two
            files changed after the last sync; help-center (WEB, semantic chunking, model parser, RETAIN).
            sales-playbooks (Pinecone, never synced), hr-policies (S3 Vectors, no chunking), legacy-faq
            (FAILED, Aurora). Models: Claude and Llama through us. profiles, Nova and Mistral on demand, and
            embedding and rerank models that models() leaves out.
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


# ----------------------------------------------------------------------------- Bedrock Knowledge Bases

REGION = "us-east-1"
ACCOUNT = "123456789012"
SUPPORT, SALES, HR, LEGACY = "K7QJ2M4XNA", "S4LE5PB9KB", "HR8POL1CY0", "LG3FAQ7Z2Q"
DOCS_S3, HELP_WEB, CRM_S3, HR_S3, LEGACY_S3 = "D0CS3SRC01", "HE1PWEB002", "CRM5RC0003", "HRD0CS0004", "LGFAQ00005"
TITAN = f"arn:aws:bedrock:{REGION}::foundation-model/amazon.titan-embed-text-v2:0"
COHERE_EMBED = f"arn:aws:bedrock:{REGION}::foundation-model/cohere.embed-english-v3"

# The support-docs corpus: (file, page, metadata, text). Retrieve ranks these for a question.
CHUNKS = [
    ("policies/refund-policy.pdf", 3, {"team": "billing", "year": 2024},
     "Refunds are issued within 5-7 business days of receiving the returned item. The refund goes back to the "
     "original payment method; store credit is issued instantly."),
    ("policies/refund-policy.pdf", 4, {"team": "billing", "year": 2024},
     "Orders paid by bank transfer are refunded to the same account, which can take up to 10 business days "
     "depending on the bank."),
    ("policies/eu-returns.pdf", 1, {"team": "legal", "year": 2025},
     "Customers in the EU can return any order within 14 days of delivery without giving a reason. The 14 days "
     "start the day after the parcel arrives."),
    ("policies/returns-2023-copy.pdf", 1, {"team": "legal", "year": 2023},
     "Customers in the EU can return any order within 14 days of delivery without giving a reason. The 14 days "
     "start the day after the parcel arrives."),
    ("policies/digital-goods.pdf", 2, {"team": "billing", "year": 2023},
     "Digital goods, such as e-books and software licences, can't be refunded once they have been downloaded, "
     "unless the download failed."),
    ("faq/payment-errors.md", None, {"team": "billing", "year": 2025},
     "Error E1234 means the card issuer declined the payment. Ask the customer to contact their bank or use "
     "another card; retrying the same card won't help."),
    ("faq/payment-errors.md", None, {"team": "billing", "year": 2025},
     "Error E2002 means the payment timed out before the bank answered. It's safe to retry after a minute: the "
     "customer isn't charged twice."),
    ("faq/account-faq.md", None, {"team": "support", "year": 2024},
     "To reset a password, open the login page, choose Forgot password and follow the link in the email. The "
     "link expires after 30 minutes."),
    ("faq/account-faq.md", None, {"team": "support", "year": 2024},
     "Two-factor authentication can be turned off by support only after the customer confirms their identity by "
     "phone."),
    ("policies/shipping-times.pdf", 1, {"team": "logistics", "year": 2024},
     "Standard shipping takes 3-5 business days within the EU and 7-10 business days to the UK. Express "
     "shipping arrives the next business day when ordered before 2 pm."),
    ("policies/warranty.pdf", 6, {"team": "legal", "year": 2024},
     "Hardware comes with a two-year warranty. A faulty item is repaired or replaced; if neither is possible, it "
     "is refunded under the refund policy."),
    ("faq/gift-cards.md", None, {"team": "billing", "year": 2024},
     "Gift cards can't be exchanged for cash and aren't refundable, but an unused gift card never expires."),
]
_SYNONYMS = {"refund": "refund", "refunded": "refund", "refunds": "refund", "money": "refund", "return": "return",
             "returned": "return", "returns": "return", "password": "password", "login": "password",
             "error": "error", "declined": "error", "fail": "error", "failed": "error", "payment": "payment",
             "card": "payment", "ship": "shipping", "shipping": "shipping", "delivery": "shipping",
             "long": "time", "days": "time", "take": "time", "warranty": "warranty", "guarantee": "warranty"}
_STOP = set("a an and are be by can do does for from how i in is it my of on or our the to what when with you your "
            "they this that there".split())


def _words(text: str) -> list[str]:
    import re

    return [w for w in re.findall(r"[a-z0-9][a-z0-9-]*", text.lower()) if w not in _STOP]


def _meaning(words: list[str]) -> set[str]:
    """What 'semantic' search sees: words (mapped to a concept where one fits) without codes like E1234."""
    return {_SYNONYMS.get(w, w.rstrip("s")) for w in words if not any(c.isdigit() for c in w)}


def _rank(question: str, search_type: str, chunks: list) -> list[tuple[float, tuple]]:
    """A toy retriever: SEMANTIC matches concepts and misses exact codes; HYBRID also matches exact words."""
    import zlib

    asked = _words(question)
    concepts, exact = _meaning(asked), set(asked)
    scored = []
    for chunk in chunks:
        words = _words(chunk[3])
        overlap = len(concepts & _meaning(words)) / max(len(concepts), 1)
        jitter = (zlib.crc32(f"{question}|{chunk[3]}".encode()) % 100) / 1000  # embeddings aren't exact either
        score = 0.30 + 0.45 * overlap + jitter
        if search_type == "HYBRID":
            score = 0.75 * score + 0.25 * len(exact & set(words)) / max(len(exact), 1)
            score += 0.2 * any(w in words for w in exact if any(c.isdigit() for c in w))
        scored.append((round(min(score, 0.99), 4), chunk))
    return sorted(scored, key=lambda s: -s[0])


def _matches(md: dict, condition: dict | None) -> bool:
    """Evaluate a Bedrock RetrievalFilter against one chunk's metadata."""
    if not condition:
        return True
    (op, arg), = condition.items()
    if op == "andAll":
        return all(_matches(md, c) for c in arg)
    if op == "orAll":
        return any(_matches(md, c) for c in arg)
    value, have = arg["value"], md.get(arg["key"])
    tests = {"equals": lambda: have == value, "notEquals": lambda: have != value,
             "greaterThan": lambda: have is not None and have > value,
             "greaterThanOrEquals": lambda: have is not None and have >= value,
             "lessThan": lambda: have is not None and have < value,
             "lessThanOrEquals": lambda: have is not None and have <= value,
             "in": lambda: have in value, "notIn": lambda: have not in value,
             "startsWith": lambda: isinstance(have, str) and have.startswith(value),
             "stringContains": lambda: isinstance(have, str) and value in have,
             "listContains": lambda: isinstance(have, list) and value in have}
    return tests[op]()


def _reference(score: float | None, chunk: tuple, i: int) -> dict:
    key, page, md, text = chunk
    uri = f"s3://support-docs-bucket/{key}"
    meta = {"x-amz-bedrock-kb-source-uri": uri, "x-amz-bedrock-kb-chunk-id": f"chunk-{CHUNKS.index(chunk):03d}",
            "x-amz-bedrock-kb-data-source-id": DOCS_S3, **md}
    if page is not None:
        meta["x-amz-bedrock-kb-document-page-number"] = float(page)
    ref = {"content": {"type": "TEXT", "text": text}, "location": {"type": "S3", "s3Location": {"uri": uri}},
           "metadata": meta}
    if score is not None:
        ref["score"] = score
    return ref


class _FakeAWS:
    """Answers boto3 calls from Python functions, in any order, and checks each request and response against the
    service model the way botocore's Stubber does. moto doesn't cover Bedrock Knowledge Bases."""

    def __init__(self, service: str, handlers: dict, latency: dict | None = None):
        import boto3
        from botocore import xform_name

        model = boto3.client(service, region_name=REGION).meta.service_model
        self.meta = type("Meta", (), {"region_name": REGION, "service_model": model})()
        self._ops = {xform_name(op): model.operation_model(op) for op in model.operation_names}
        self._handlers, self._latency = handlers, latency or {}

    def _call(self, name: str, params: dict) -> dict:
        import time

        from botocore.validate import ParamValidator, validate_parameters

        op = self._ops[name]
        validate_parameters(params, op.input_shape)
        if name not in self._handlers:
            raise NotImplementedError(f"the Bedrock demo doesn't answer {op.name}")
        time.sleep(self._latency.get(name, 0.0))
        resp = self._handlers[name](**params)
        report = ParamValidator().validate(resp, op.output_shape)
        if report.has_errors():
            raise AssertionError(f"demo {op.name} response doesn't match the service model:\n{report.generate_report()}")
        return resp

    def __getattr__(self, name: str):
        if name.startswith("_") or name not in self._ops:
            raise AttributeError(name)
        return lambda **params: self._call(name, params)

    def get_paginator(self, name: str):
        client = self

        class Paginator:
            def paginate(self, **params):
                yield client._call(name, params)

        return Paginator()


def _client_error(code: str, message: str, operation: str):
    from botocore.exceptions import ClientError

    return ClientError({"Error": {"Code": code, "Message": message}}, operation)


def seed_bedrock_kb() -> dict:
    """moto S3 for the files behind support-docs, and fake Bedrock clients for everything else."""
    import boto3

    # The bucket behind docs-s3: most files are older than the last sync; two changed after it.
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(Bucket="support-docs-bucket")
    for key in sorted({c[0] for c in CHUNKS}) + ["policies/scanned-invoice.pdf", "policies/catalogue-2019.pdf",
                                                 "other/not-in-the-knowledge-base.pdf"]:
        s3.put_object(Bucket="support-docs-bucket", Key=key, Body=b"%PDF demo " * 400)
        s3.put_object(Bucket="support-docs-bucket", Key=key + ".metadata.json",
                      Body=b'{"metadataAttributes": {"team": "billing", "year": 2024}}')
        _backdate("support-docs-bucket", key, NOW - timedelta(days=20))
        _backdate("support-docs-bucket", key + ".metadata.json", NOW - timedelta(days=20))
    s3.put_object(Bucket="support-docs-bucket", Key="faq/holiday-shipping.md", Body=b"# Holiday shipping\n" * 50)
    _backdate("support-docs-bucket", "faq/holiday-shipping.md", NOW - timedelta(hours=26))
    s3.put_object(Bucket="support-docs-bucket", Key="policies/refund-policy.pdf", Body=b"%PDF updated " * 420)
    _backdate("support-docs-bucket", "policies/refund-policy.pdf", NOW - timedelta(hours=5))

    now = datetime.now(timezone.utc)
    kb_arn = {kb: f"arn:aws:bedrock:{REGION}:{ACCOUNT}:knowledge-base/{kb}" for kb in (SUPPORT, SALES, HR, LEGACY)}
    role = f"arn:aws:iam::{ACCOUNT}:role/service-role/AmazonBedrockExecutionRoleForKnowledgeBase_support"
    fields = {"vectorField": "embedding", "textField": "text", "metadataField": "metadata"}

    def kb(kb_id, name, description, status, storage, embedding=TITAN, dims=1024, reasons=None, age=120):
        desc = {"knowledgeBaseId": kb_id, "name": name, "description": description, "knowledgeBaseArn": kb_arn[kb_id],
                "roleArn": role, "status": status, "createdAt": now - timedelta(days=age),
                "updatedAt": now - timedelta(days=9), "storageConfiguration": storage,
                "knowledgeBaseConfiguration": {"type": "VECTOR", "vectorKnowledgeBaseConfiguration": {
                    "embeddingModelArn": embedding,
                    "embeddingModelConfiguration": {"bedrockEmbeddingModelConfiguration": {"dimensions": dims}}}}}
        if reasons:
            desc["failureReasons"] = reasons
        return desc

    kbs = {
        SUPPORT: kb(SUPPORT, "support-docs", "Refund, returns, shipping and account answers for the support team",
                    "ACTIVE", {"type": "OPENSEARCH_SERVERLESS", "opensearchServerlessConfiguration": {
                        "collectionArn": f"arn:aws:aoss:{REGION}:{ACCOUNT}:collection/k1x9q2support",
                        "vectorIndexName": "bedrock-knowledge-base-default-index", "fieldMapping": fields}}),
        SALES: kb(SALES, "sales-playbooks", "Pitch decks and objection handling", "ACTIVE",
                  {"type": "PINECONE", "pineconeConfiguration": {
                      "connectionString": "https://sales-playbooks-a1b2c3.svc.aped-4627-b74a.pinecone.io",
                      "credentialsSecretArn": f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:pinecone-key",
                      "namespace": "prod", "fieldMapping": {"textField": "text", "metadataField": "metadata"}}},
                  embedding=COHERE_EMBED, age=40),
        HR: kb(HR, "hr-policies", "Leave, benefits and expenses", "ACTIVE",
               {"type": "S3_VECTORS", "s3VectorsConfiguration": {
                   "vectorBucketArn": f"arn:aws:s3vectors:{REGION}:{ACCOUNT}:bucket/hr-vectors",
                   "indexName": "hr-policies-index"}}, age=200),
        LEGACY: kb(LEGACY, "legacy-faq", "Old FAQ, kept for reference", "FAILED",
                   {"type": "RDS", "rdsConfiguration": {
                       "resourceArn": f"arn:aws:rds:{REGION}:{ACCOUNT}:cluster:kb-aurora",
                       "credentialsSecretArn": f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:kb-aurora",
                       "databaseName": "kb", "tableName": "bedrock_integration.legacy_faq",
                       "fieldMapping": {"primaryKeyField": "id", "vectorField": "embedding", "textField": "chunks",
                                        "metadataField": "metadata"}}},
                   reasons=["The Aurora cluster kb-aurora can't be reached: it was stopped on 2026-08-02."], age=400),
    }

    def s3_source(ds_id, kb_id, name, bucket, prefixes, chunking, policy="DELETE", age=100):
        config = {"bucketArn": f"arn:aws:s3:::{bucket}"}
        if prefixes:
            config["inclusionPrefixes"] = prefixes
        return {"knowledgeBaseId": kb_id, "dataSourceId": ds_id, "name": name, "status": "AVAILABLE",
                "dataSourceConfiguration": {"type": "S3", "s3Configuration": config}, "dataDeletionPolicy": policy,
                "vectorIngestionConfiguration": {"chunkingConfiguration": chunking},
                "createdAt": now - timedelta(days=age), "updatedAt": now - timedelta(days=age)}

    fixed = {"chunkingStrategy": "FIXED_SIZE", "fixedSizeChunkingConfiguration": {"maxTokens": 300,
                                                                                  "overlapPercentage": 20}}
    sources = {
        SUPPORT: [
            s3_source(DOCS_S3, SUPPORT, "docs-s3", "support-docs-bucket", ["policies/", "faq/"], fixed),
            {"knowledgeBaseId": SUPPORT, "dataSourceId": HELP_WEB, "name": "help-center", "status": "AVAILABLE",
             "dataSourceConfiguration": {"type": "WEB", "webConfiguration": {"sourceConfiguration": {
                 "urlConfiguration": {"seedUrls": [{"url": "https://help.acme.example/"}]}}}},
             "dataDeletionPolicy": "RETAIN",
             "vectorIngestionConfiguration": {"chunkingConfiguration": {
                 "chunkingStrategy": "SEMANTIC", "semanticChunkingConfiguration": {
                     "maxTokens": 300, "bufferSize": 1, "breakpointPercentileThreshold": 95}},
                 "parsingConfiguration": {"parsingStrategy": "BEDROCK_FOUNDATION_MODEL",
                                          "bedrockFoundationModelConfiguration": {
                                              "modelArn": f"arn:aws:bedrock:{REGION}::foundation-model/"
                                                          "anthropic.claude-haiku-4-5-20251001-v1:0"}}},
             "createdAt": now - timedelta(days=60), "updatedAt": now - timedelta(days=60)}],
        SALES: [s3_source(CRM_S3, SALES, "crm-exports", "sales-playbooks-bucket", ["playbooks/"], fixed, age=40)],
        HR: [s3_source(HR_S3, HR, "hr-docs", "hr-policies-bucket", [], {"chunkingStrategy": "NONE"}, age=200)],
        LEGACY: [s3_source(LEGACY_S3, LEGACY, "faq-archive", "legacy-faq-bucket", [], fixed, age=400)],
    }
    by_source = {d["dataSourceId"]: d for ds in sources.values() for d in ds}

    def job(job_id, ds_id, kb_id, days, status="COMPLETE", minutes=6, scanned=48, metadata=46, new=0, modified=2,
            deleted=0, failed=0, reasons=None):
        started = now - timedelta(days=days)
        summary = {"knowledgeBaseId": kb_id, "dataSourceId": ds_id, "ingestionJobId": job_id, "status": status,
                   "startedAt": started, "updatedAt": started + timedelta(minutes=minutes), "statistics": {
                       "numberOfDocumentsScanned": scanned, "numberOfMetadataDocumentsScanned": metadata,
                       "numberOfNewDocumentsIndexed": new, "numberOfModifiedDocumentsIndexed": modified,
                       "numberOfDocumentsDeleted": deleted, "numberOfDocumentsFailed": failed}}
        return summary, reasons or []

    jobs = {
        DOCS_S3: [job("SYNC000009", DOCS_S3, SUPPORT, 3, minutes=7, modified=4, failed=2, reasons=[
                      "2 documents couldn't be parsed: s3://support-docs-bucket/policies/scanned-invoice.pdf, "
                      "s3://support-docs-bucket/policies/catalogue-2019.pdf"]),
                  job("SYNC000008", DOCS_S3, SUPPORT, 5, "FAILED", minutes=1, scanned=0, metadata=0, modified=0,
                      reasons=["The knowledge base's role isn't allowed s3:GetObject on "
                               "s3://support-docs-bucket/policies/ (AccessDenied)."]),
                  job("SYNC000007", DOCS_S3, SUPPORT, 12, minutes=65, new=40, modified=0),
                  job("SYNC000006", DOCS_S3, SUPPORT, 30, minutes=9, new=6, modified=3)],
        HELP_WEB: [job("SYNC000105", HELP_WEB, SUPPORT, 1, minutes=41, scanned=212, metadata=0, new=5, modified=11),
                   job("SYNC000104", HELP_WEB, SUPPORT, 8, minutes=39, scanned=207, metadata=0, new=0, modified=3)],
        CRM_S3: [],
        HR_S3: [job("SYNC000301", HR_S3, HR, 40, minutes=12, scanned=85, metadata=0, new=85, modified=0)],
        LEGACY_S3: [job("SYNC000401", LEGACY_S3, LEGACY, 70, "FAILED", minutes=2, scanned=0, metadata=0, modified=0,
                        reasons=["Couldn't connect to the Aurora cluster kb-aurora."])],
    }

    def find_kb(knowledgeBaseId, **_):
        if knowledgeBaseId not in kbs:
            raise _client_error("ResourceNotFoundException", f"Knowledge base {knowledgeBaseId} not found",
                                "GetKnowledgeBase")
        return kbs[knowledgeBaseId]

    def list_ingestion_jobs(knowledgeBaseId, dataSourceId, maxResults=10, filters=None, **_):
        found = [summary for summary, _ in jobs[dataSourceId]]
        if filters:
            found = [s for s in found if s["status"] in filters[0]["values"]]
        return {"ingestionJobSummaries": found[:maxResults]}

    def get_ingestion_job(knowledgeBaseId, dataSourceId, ingestionJobId):
        summary, reasons = next(j for j in jobs[dataSourceId] if j[0]["ingestionJobId"] == ingestionJobId)
        return {"ingestionJob": {**summary, "failureReasons": reasons}}

    def list_documents(knowledgeBaseId, dataSourceId, **_):
        if by_source[dataSourceId]["dataSourceConfiguration"]["type"] != "S3":
            raise _client_error("ValidationException", "ListKnowledgeBaseDocuments supports S3 and CUSTOM data sources "
                                "only", "ListKnowledgeBaseDocuments")
        keys = sorted({c[0] for c in CHUNKS}) + [f"faq/answer-{i:02d}.md" for i in range(30)]
        details = [{"knowledgeBaseId": knowledgeBaseId, "dataSourceId": dataSourceId, "status": "INDEXED",
                    "updatedAt": now - timedelta(days=3),
                    "identifier": {"dataSourceType": "S3", "s3": {"uri": f"s3://support-docs-bucket/{k}"}}}
                   for k in keys]
        for key, status, reason in [
                ("policies/scanned-invoice.pdf", "FAILED", "The file is a scanned image with no text layer; use a "
                                                            "foundation model parser to read it."),
                ("policies/catalogue-2019.pdf", "FAILED", "The file is encrypted and can't be read."),
                ("faq/training-video.mp4", "IGNORED", "Unsupported file type: .mp4")]:
            details.append({"knowledgeBaseId": knowledgeBaseId, "dataSourceId": dataSourceId, "status": status,
                            "statusReason": reason, "updatedAt": now - timedelta(days=3),
                            "identifier": {"dataSourceType": "S3", "s3": {"uri": f"s3://support-docs-bucket/{key}"}}})
        return {"documentDetails": details}

    def search(knowledgeBaseId, question, config):
        if knowledgeBaseId != SUPPORT:
            return []
        kind = config.get("overrideSearchType", "SEMANTIC")
        chunks = [c for c in CHUNKS if _matches(c[2], config.get("filter"))]
        ranked = _rank(question, kind, chunks)[: config["numberOfResults"]]
        rerank = config.get("rerankingConfiguration")
        if rerank:
            exact = set(_words(question))
            ranked = sorted(((round(0.2 + 0.8 * len(exact & set(_words(c[3]))) / max(len(exact), 1), 4), c)
                             for _, c in ranked), key=lambda s: -s[0])
            ranked = ranked[: rerank["bedrockRerankingConfiguration"]["numberOfRerankedResults"]]
        return ranked

    def retrieve(knowledgeBaseId, retrievalQuery, retrievalConfiguration, **_):
        found = search(knowledgeBaseId, retrievalQuery["text"], retrievalConfiguration["vectorSearchConfiguration"])
        return {"retrievalResults": [_reference(score, chunk, i) for i, (score, chunk) in enumerate(found)]}

    def first_sentence(text):
        return text.split(". ")[0].rstrip(".") + "."

    def retrieve_and_generate(input, retrieveAndGenerateConfiguration, sessionId=None, **_):
        config = retrieveAndGenerateConfiguration["knowledgeBaseConfiguration"]
        found = search(config["knowledgeBaseId"], input["text"],
                       config["retrievalConfiguration"]["vectorSearchConfiguration"])
        useful = [(s, c) for s, c in found if s >= max(0.55, found[0][0] - 0.1)][:3] if found else []
        if not useful:
            return {"output": {"text": "Sorry, I am unable to assist you with this request."},
                    "sessionId": sessionId or "demo-session-1"}
        text, citations = "", []
        for _, chunk in useful:
            sentence = first_sentence(chunk[3])
            start = len(text) + (1 if text else 0)
            text += (" " if text else "") + sentence
            citations.append({"generatedResponsePart": {"textResponsePart": {
                "text": sentence, "span": {"start": start, "end": start + len(sentence) - 1}}},
                "retrievedReferences": [{k: v for k, v in _reference(None, chunk, 0).items()}]})
        text += " Anything else is decided case by case by the support team."  # uncited, like real answers
        return {"output": {"text": text}, "citations": citations, "sessionId": sessionId or "demo-session-1"}

    def converse(modelId, messages, system=None, inferenceConfig=None, **_):
        import re

        prompt = messages[-1]["content"][0]["text"]
        question = (re.findall(r"Question: (.*)", prompt) or [prompt[-200:]])[-1]
        sources = re.findall(r'<source id="(\d+)"[^>]*>\n(.*?)\n</source>', prompt, re.S)
        concepts = _meaning(_words(question))
        overlap = {n: len(concepts & _meaning(_words(text))) for n, text in sources}
        best = max(overlap.values(), default=0)
        picked = [(n, text) for n, text in sources if best and overlap[n] == best][:3]
        if picked:
            answer = " ".join(first_sentence(text).rstrip(".") + f" [{n}]." for n, text in picked)
        else:
            answer = "The sources don't say."
        chars = sum(len(m["content"][0]["text"]) for m in messages) + len((system or [{"text": ""}])[0]["text"])
        usage = {"inputTokens": chars // 4, "outputTokens": len(answer) // 4 + 180}  # + thinking
        usage["totalTokens"] = usage["inputTokens"] + usage["outputTokens"]
        return {"output": {"message": {"role": "assistant", "content": [
                    {"reasoningContent": {"reasoningText": {"text": "Checking which sources answer this.",
                                                            "signature": "demo"}}},
                    {"text": answer}]}},
                "stopReason": "end_turn", "usage": usage, "metrics": {"latencyMs": 2100}}

    def model(model_id, name, provider, on_demand):
        return {"modelArn": f"arn:aws:bedrock:{REGION}::foundation-model/{model_id}", "modelId": model_id,
                "modelName": name, "providerName": provider, "inputModalities": ["TEXT"],
                "outputModalities": ["TEXT"] if "embed" not in model_id else ["EMBEDDING"],
                "inferenceTypesSupported": ["ON_DEMAND"] if on_demand else [], "modelLifecycle": {"status": "ACTIVE"}}

    profiled = ["anthropic.claude-opus-5", "anthropic.claude-opus-5-5", "anthropic.claude-sonnet-5",
                "anthropic.claude-haiku-4-5-20251001-v1:0", "meta.llama4-maverick-17b-instruct-v1:0"]
    models = [model(profiled[0], "Claude Opus 5", "Anthropic", False),
              model(profiled[1], "Claude Opus 5.5", "Anthropic", False),
              model(profiled[2], "Claude Sonnet 5", "Anthropic", False),
              model(profiled[3], "Claude Haiku 4.5", "Anthropic", False),
              model("amazon.nova-pro-v1:0", "Nova Pro", "Amazon", True),
              model("amazon.nova-lite-v1:0", "Nova Lite", "Amazon", True),
              model("amazon.nova-micro-v1:0", "Nova Micro", "Amazon", True),
              model(profiled[4], "Llama 4 Maverick 17B Instruct", "Meta", False),
              model("mistral.mistral-large-3-675b-instruct", "Mistral Large 3", "Mistral AI", True),
              model("cohere.rerank-v3-5:0", "Rerank 3.5", "Cohere", True),
              model("amazon.titan-embed-text-v2:0", "Titan Text Embeddings V2", "Amazon", True)]
    profiles = [{"inferenceProfileName": f"{geo.upper()} {m}", "inferenceProfileId": f"{geo}.{m}",
                 "inferenceProfileArn": f"arn:aws:bedrock:{REGION}:{ACCOUNT}:inference-profile/{geo}.{m}",
                 "models": [{"modelArn": f"arn:aws:bedrock:{r}::foundation-model/{m}"}
                            for r in ("us-east-1", "us-east-2", "us-west-2")],
                 "status": "ACTIVE", "type": "SYSTEM_DEFINED"} for geo in ("us", "global") for m in profiled]

    agent = _FakeAWS("bedrock-agent", {
        "list_knowledge_bases": lambda **_: {"knowledgeBaseSummaries": [
            {k: d[k] for k in ("knowledgeBaseId", "name", "description", "status", "updatedAt")} for d in kbs.values()]},
        "get_knowledge_base": lambda **p: {"knowledgeBase": find_kb(**p)},
        "list_data_sources": lambda knowledgeBaseId, **_: {"dataSourceSummaries": [
            {k: d[k] for k in ("knowledgeBaseId", "dataSourceId", "name", "status", "updatedAt")}
            for d in sources[find_kb(knowledgeBaseId)["knowledgeBaseId"]]]},
        "get_data_source": lambda knowledgeBaseId, dataSourceId: {"dataSource": by_source[dataSourceId]},
        "list_ingestion_jobs": list_ingestion_jobs,
        "get_ingestion_job": get_ingestion_job,
        "list_knowledge_base_documents": list_documents,
        "list_tags_for_resource": lambda resourceArn: {"tags": {"team": "customer-support", "cost-center": "cx-42"}
                                                       if resourceArn == kb_arn[SUPPORT] else {}},
    })
    runtime = _FakeAWS("bedrock-agent-runtime", {"retrieve": retrieve, "retrieve_and_generate": retrieve_and_generate},
                       latency={"retrieve": 0.3, "retrieve_and_generate": 1.9})
    llm = _FakeAWS("bedrock-runtime", {"converse": converse}, latency={"converse": 2.1})
    bedrock = _FakeAWS("bedrock", {
        "list_foundation_models": lambda **_: {"modelSummaries": models},
        "list_inference_profiles": lambda **_: {"inferenceProfileSummaries": profiles},
    })
    return {"client": agent, "clients": {"bedrock-agent-runtime": runtime, "bedrock-runtime": llm,
                                         "bedrock": bedrock, "s3": s3}}


SEEDERS = {"s3": seed_s3, "dynamodb": seed_dynamodb, "bedrock_kb": seed_bedrock_kb}


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
    extra: dict = {}
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
            extra = seeder() or {}  # a seeder may hand the analyzer clients moto can't fake (Bedrock)
        else:
            print(f"(no demo data for {args.service} yet: add a seed_{args.service}() to {Path(__file__).name}; "
                  "running against an empty account)", file=sys.stderr)

    try:
        mod = importlib.import_module(args.service)
        view_cls = next(v for k, v in vars(mod).items() if k.endswith("View") and isinstance(v, type))
        core_cls = next(v for k, v in vars(mod).items() if k.endswith("Analyzer") and isinstance(v, type))
        kwargs = {"region": args.region, "profile": args.profile} if args.live else {"region": "us-east-1"}
        core = core_cls(**{k: v for k, v in kwargs.items() if v}, **extra)
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
