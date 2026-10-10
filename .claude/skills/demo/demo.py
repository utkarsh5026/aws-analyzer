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
            tags), exports/ a multipart re-upload of one parquet file (same content, different ETag), logs/app/ backdated 1-3 years, archive/ in GLACIER, reports/monthly/ small STANDARD_IA files,
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
            embedding and rerank models that models() leaves out. bedrock_chat uses the same fake Bedrock, which also
            answers RetrieveAndGenerateStream a few words at a time.
  sagemaker_env  moto has no Studio, so the seeder hands the analyzer fake sagemaker / sts / cloudwatch
            clients (checked against botocore's service model) and a fake machine: this code "runs" in the
            JupyterLab space churn-analysis (ml.g5.2xlarge, 2 days, idle GPU, domain without idle shutdown) whose
            home is 88% full, with Jupyter trash, a Hugging Face cache, checkpoints and year-old parquet files
            (sparse files, so nothing big is written). running(): notebook instances old-experiment (9 days, no
            auto-stop) and team-reporting (auto-stop), stopped archive-2024 and sandbox, a Code Editor app in
            space forecasting, endpoints churn-v1 (no traffic), churn-v2 (busy) and a serverless one, and a spot
            training job.
  opensearch  moto domains: vectors-prod (3 x r6g.large + masters, gp3, fine-grained access control), search-legacy
            (Elasticsearch 7.10, open to the internet, gp2) and rag-dev (one t3.medium). Fake clusters behind them
            (tests/fake_opensearch.py): vectors-prod holds support-docs (faiss, cosine, 1,024 dims, 1.8M documents
            reported, 1.5% without a vector, a few repeats, graphs bigger than the nodes' k-NN memory),
            product-search (lucene, 384 dims), legacy-faq (nmslib, inner product, unnormalized vectors) and
            app-logs-2026.10 (no vectors). A fake Serverless with kb-support (a Bedrock knowledge base's index),
            kb-sandbox (no standby replicas) and app-logs (VPC only), CloudWatch OCU use, and a fake Bedrock whose
            embedding model knows four topics (refunds, shipping, accounts, billing).
  lambda_functions  moto functions, triggers, 30 days of CloudWatch numbers and logs, with patched Lambda and Logs
            clients for what moto lacks (account limits, provisioned concurrency, log sizes) and the deployment
            packages served from memory. us-east-1: orders-etl (python3.9, SQS and S3 triggers, errors rising for
            3 days, runs near its 60 s timeout, KeyError / AccessDenied / timeouts in the last 24 hours of logs,
            logs costing more than its compute and kept forever, a .env file and bundled boto3 in its package),
            churn-scoring (arm64, EventBridge, 4 copies of provisioned concurrency it never needs), report-api
            (nodejs20.x, a public function URL, throttled by reserved concurrency 10, JSON logs for its last three
            hours with WARN lines and unhandled TypeErrors), feature-backfill (python3.10, never called, no triggers)
            and support-agent-actions (a Bedrock agent's action group). eu-west-1: gdpr-export (SQS). The run
            c0ffee00-1d2e-4f3a-9b8c-7d6e5f4a3b2c is one of orders-etl's KeyErrors.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import importlib
import io
import json
import os
import random
import re
import sys
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

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
        # exports/: the same parquet file uploaded again in parts, so its ETag differs (only a SHA-256 of the
        # content shows it's a copy).
        export = s3.create_multipart_upload(Bucket=lake, Key="exports/orders-part-0.parquet")
        body = s3.get_object(Bucket=lake, Key="curated/orders/part-00000.parquet")["Body"].read()
        part = s3.upload_part(Bucket=lake, Key="exports/orders-part-0.parquet", UploadId=export["UploadId"],
                              PartNumber=1, Body=body)
        s3.complete_multipart_upload(Bucket=lake, Key="exports/orders-part-0.parquet", UploadId=export["UploadId"],
                                     MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": part["ETag"]}]})
        _backdate(lake, "exports/orders-part-0.parquet", NOW - timedelta(days=5))
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

# The files the explorer opens, beyond the corpus above: what a search limited to one file finds (a filter on its
# x-amz-bedrock-kb-source-uri). They never come up in searches of the whole knowledge base, so the other reports
# read as before. warranty.pdf is the long one: fixed-size chunks with 20% overlap, over 8 pages.
WARRANTY_PAGES = [
    "Warranty policy. This policy explains what our warranty covers, how long it lasts and how to make a claim. It "
    "applies to every hardware product sold in our online store and in our shops, including refurbished products, "
    "and it is in addition to your rights under consumer law, which it never limits. Accessories bought separately, "
    "such as cables, cases and chargers, have their own one-year warranty described in section 7. Software, digital "
    "content and gift cards are not covered by this policy: the refund policy describes what happens to them.",
    "What the warranty covers. Every hardware product comes with a warranty against faults in materials and "
    "workmanship that appear under normal use. A fault is anything that stops the product from working as described "
    "in its manual: a screen that stays dark, a battery that no longer charges, a button that does not respond, a "
    "hinge that breaks without being forced. Cosmetic wear such as scratches, dents and fading is not a fault unless "
    "it stops the product from working. The warranty follows the product, so a gift recipient can claim with the "
    "original order number.",
    "What the warranty does not cover. Damage caused by accidents, drops, liquids, power surges, unauthorised "
    "repairs or modifications is not covered, and neither is normal wear of parts that are meant to be replaced, "
    "such as batteries after 500 charge cycles, ear tips and filters. Products used commercially, rented out or used "
    "outside the specifications in their manual are covered for six months only. Lost or stolen products are never "
    "covered; contact your insurer instead. Data on a device is not covered either: back it up before you send a "
    "device in, because a repair can erase it.",
    "How to make a claim. Start a claim from your order history, or contact support with the order number, the "
    "product's serial number and a short description of the fault. Support may ask for photos or a video and will "
    "suggest simple fixes first, such as a reset or a software update, since many faults are solved in a few "
    "minutes. If the fault remains, support issues a returns label and a claim number. Pack the product securely, "
    "include the claim number and send it within 14 days of the label being issued, or the claim is closed.",
    "Repairs. When a claim is accepted, the product is repaired at one of our service centres with new or "
    "refurbished parts that perform like new. Repairs usually take 5 to 7 business days from the day the product "
    "arrives, and you can follow them from your order history. A repaired product keeps the rest of its original "
    "warranty, or gets 90 days of warranty on the repair, whichever is longer. If a repair isn't possible, or the "
    "same fault comes back three times, the product is replaced instead.",
    "Replacements and refunds. Hardware comes with a two-year warranty. A faulty item is repaired or replaced; if "
    "neither is possible, it is refunded under the refund policy. A replacement is a new or refurbished product of the "
    "same model, or of a newer model with the same features when the original is no longer made. A refund under "
    "warranty is the price you paid, less any discount, and goes back to the original payment method within 5 to 7 "
    "business days of the decision, as described in the refund policy.",
    "Shipping for warranty claims. We pay for shipping both ways within the EU and the UK, with tracking and "
    "insurance. Customers elsewhere pay to send the product in, and we pay to send it back. A product sent without a "
    "claim number may be returned unopened, at your cost. Express replacement, available for an extra fee in some "
    "countries, sends the replacement before the faulty product arrives, against a deposit that is refunded when it "
    "does; if the faulty product isn't received within 14 days, the deposit is kept.",
    "Extended warranty and business customers. An extended warranty adds one or two years to this warranty and "
    "covers accidental damage twice a year, with an excess of 49 euros per claim. It can be bought with the product "
    "or within 60 days of delivery, and cancelled within 30 days for a full refund. Business customers buying more "
    "than 20 products a year can ask for advance replacement and a named account manager. Contact the business "
    "team for terms. Questions about this policy go to support, who answer within one business day.",
]
FAQ_ANSWERS = [
    ("How do I track my order?", "Open your order history and choose Track: the carrier's tracking page opens with "
     "the latest scan. Tracking starts a few hours after the order ships."),
    ("Can I change my delivery address?", "Yes, until the order ships: open the order and choose Change address. "
     "After it ships, ask the carrier to redirect the parcel."),
    ("Can I cancel an order?", "Orders can be cancelled until they ship, from the order page. A cancelled order is "
     "refunded to the original payment method within 5-7 business days."),
    ("Do you ship to Switzerland and Norway?", "Yes. Shipping takes 4-7 business days, and import duties are "
     "collected by the carrier on delivery."),
    ("Why was my card charged twice?", "The second charge is usually a pending authorisation that disappears within "
     "3 business days. If it doesn't, contact support with both amounts."),
    ("Can I pay by invoice?", "Business customers can pay by invoice with 30-day terms after a credit check. "
     "Consumers can pay by card, PayPal or bank transfer."),
    ("How do I update my billing details?", "Open Account, then Payment methods, and add the new card before "
     "removing the old one. Subscriptions move to the new card automatically."),
    ("How do I close my account?", "Choose Close account under Account settings. Open orders must be delivered or "
     "cancelled first, and order history is kept for 7 years for tax reasons."),
    ("I didn't get the verification email", "Check the spam folder, then ask for a new email from the login page. "
     "Company email filters sometimes hold it for up to an hour."),
    ("Can two people share an account?", "No: each account is for one person. Families can link up to five "
     "accounts under one household to share delivery addresses."),
    ("How do I change my email address?", "Open Account settings and choose Change email. We send a link to the new "
     "address, and the change is made when you open it."),
    ("Do you price match?", "We match the price of an identical product sold new by a store in the same country, "
     "within 14 days of your order. Marketplace sellers don't count."),
    ("What is the student discount?", "Students get 10% off hardware with a verified student email. The discount "
     "can't be combined with other offers."),
    ("Can I return a gift?", "Yes, with the gift receipt or the order number. The refund goes to a gift card in the "
     "recipient's name, not to the buyer's card."),
    ("How do I return an item bought in a shop?", "Bring it back to any of our shops with the receipt, or start an "
     "online return with the receipt number printed on it."),
    ("What does error E3001 mean?", "Error E3001 means the delivery address couldn't be verified. Check the postcode "
     "and house number, then place the order again."),
    ("What does error E4100 mean?", "Error E4100 means the promotion code has expired or doesn't apply to the items "
     "in your basket."),
    ("Is my data shared with third parties?", "Only with the carrier and the payment provider, as the privacy "
     "policy describes. We never sell personal data."),
    ("How do I download an invoice?", "Open the order and choose Invoice: a PDF downloads with the VAT breakdown. "
     "Invoices for business accounts are also emailed."),
    ("Can I add a VAT number to an order?", "Yes, before you pay: add it under Billing details at checkout. It "
     "can't be added to an invoice afterwards."),
    ("How long is a gift card valid?", "Gift cards never expire, and their balance can be used over several orders."),
    ("What happens if a product is out of stock?", "You can order it and it ships when it's back, or choose Notify "
     "me to get an email. Pre-orders are only charged when they ship."),
    ("Do you offer installation?", "Installation is available for large appliances in the EU, booked at checkout "
     "for a fixed fee per item."),
    ("How do I report a damaged delivery?", "Take photos of the parcel and the item, then report it from the order "
     "page within 48 hours of delivery."),
    ("Can I collect my order from a shop?", "Yes, choose Click and collect at checkout. Orders are ready within two "
     "hours and kept for 7 days."),
    ("Why can't I use two promotion codes?", "Only one code can be used per order, but it can be combined with "
     "student or business discounts."),
    ("How do I recycle an old device?", "Bring it to any shop, or print a free recycling label from the website. "
     "Devices in working condition earn store credit."),
    ("Do you deliver on Saturdays?", "Saturday delivery is available in most cities for an extra fee, when the "
     "order is placed before 2 pm on Friday."),
    ("How do I contact support?", "By chat from the help centre, every day from 8 am to 8 pm, or by email, with an "
     "answer within one business day."),
    ("What is the returns address?", "Returns go to our returns centre in Utrecht, but always use the label from "
     "your return request, which tracks the parcel."),
]
HR_FILES = {  # hr-docs (no chunking): each file is one chunk; the benefits guide is far too long for one
    "leave-policy.pdf": "Leave policy. Full-time employees get 25 days of paid leave a year, plus public holidays. "
                        "Leave is booked in the HR portal at least two weeks ahead for more than three days.",
    "benefits-guide.pdf": " ".join(
        f"Section {n}. Benefits are reviewed every January. Health insurance covers employees and their families "
        "from the first day, dental after three months, and the pension plan matches contributions up to 5% of "
        "salary. Gym, cycling and learning budgets are paid each quarter against receipts submitted in the HR portal."
        for n in range(1, 121)),
    "expenses.docx": "Expenses. Claim travel, meals and equipment within 30 days in the expenses tool, with a "
                     "receipt for anything over 25 euros. Approvals take up to five business days.",
    "travel-policy.pdf": "Travel policy. Book trains for journeys under four hours and economy flights for longer "
                         "ones, through the travel portal. Hotels up to 150 euros a night are approved automatically.",
}
SALES_FILES = ["playbooks/discovery-calls.pdf", "playbooks/objection-handling.pdf", "playbooks/pricing-2026.xlsx",
               "playbooks/enterprise-pitch.pptx", "playbooks/renewals.docx"]


def _warranty_chunks(words: int = 210, overlap: int = 42) -> list[tuple[int, str]]:
    """warranty.pdf cut like Bedrock's fixed-size chunking with 20% overlap: (page, text) for each chunk, the page
    being where it starts."""
    tokens = [(page, word) for page, text in enumerate(WARRANTY_PAGES, 1) for word in text.split()]
    found, start = [], 0
    while start < len(tokens):
        piece = tokens[start:start + words]
        found.append((piece[0][0], " ".join(word for _, word in piece)))
        if start + words >= len(tokens):
            break
        start += words - overlap
    return found


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


def _reference(score: float | None, chunk: tuple, i: int, *, uri: str = "", chunk_id: str = "",
               data_source: str = DOCS_S3) -> dict:
    key, page, md, text = chunk
    uri = uri or f"s3://support-docs-bucket/{key}"
    meta = {"x-amz-bedrock-kb-source-uri": uri, "x-amz-bedrock-kb-chunk-id": chunk_id or f"chunk-{CHUNKS.index(chunk):03d}",
            "x-amz-bedrock-kb-data-source-id": data_source, **md}
    if page is not None:
        meta["x-amz-bedrock-kb-document-page-number"] = float(page)
    ref = {"content": {"type": "TEXT", "text": text}, "location": {"type": "S3", "s3Location": {"uri": uri}},
           "metadata": meta}
    if score is not None:
        ref["score"] = score
    return ref


def _file_filter(condition: dict | None) -> str | None:
    """The file a Retrieve filter limits a search to (an equals on x-amz-bedrock-kb-source-uri), if it does."""
    if not condition:
        return None
    (op, arg), = condition.items()
    if op == "equals" and arg.get("key") == "x-amz-bedrock-kb-source-uri":
        return arg.get("value")
    if op == "andAll":
        return next(filter(None, map(_file_filter, arg)), None)
    return None


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
        if op.has_event_stream_output:  # check each event as it's read, like botocore's EventStream parses them
            shape = op.get_event_stream_output()
            resp["stream"] = self._checked_events(op.name, resp["stream"], shape)
            return resp
        report = ParamValidator().validate(resp, op.output_shape)
        if report.has_errors():
            raise AssertionError(f"demo {op.name} response doesn't match the service model:\n{report.generate_report()}")
        return resp

    @staticmethod
    def _checked_events(operation: str, events, shape):
        from botocore.validate import ParamValidator

        for event in events:
            report = ParamValidator().validate(event, shape)
            if report.has_errors():
                raise AssertionError(f"demo {operation} event doesn't match the service model:\n"
                                     f"{report.generate_report()}")
            yield event

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

    # The bucket behind docs-s3: most files are older than the last sync; two changed after it. Each file's metadata
    # file holds what its chunks carry, but digital-goods.pdf's forgot its "metadataAttributes". The FAQ answers are
    # one chunk each (answer-29.md is gone from S3, still in the index), the Markdown files hold their chunks' words,
    # and onboarding-deck.pptx is a type the sync skipped.
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(Bucket="support-docs-bucket")

    def put(key: str, body: bytes | str, *, age: timedelta = timedelta(days=20), bucket: str = "support-docs-bucket",
            metadata: dict | None = None) -> None:
        s3.put_object(Bucket=bucket, Key=key, Body=body.encode() if isinstance(body, str) else body)
        _backdate(bucket, key, NOW - age)
        if metadata is not None:
            put(key + ".metadata.json", json.dumps(metadata), age=age, bucket=bucket)

    tags = {c[0]: c[2] for c in CHUNKS}
    for key in sorted(tags) + ["policies/scanned-invoice.pdf", "policies/catalogue-2019.pdf",
                               "other/not-in-the-knowledge-base.pdf"]:
        words = "\n\n".join(c[3] for c in CHUNKS if c[0] == key)
        body = words if key.endswith(".md") else b"%PDF demo " * 400
        attributes = tags.get(key, {"team": "billing", "year": 2024})
        put(key, body, metadata=attributes if key == "policies/digital-goods.pdf" else {"metadataAttributes": attributes})
    for i, (question, answer) in enumerate(FAQ_ANSWERS[:29]):
        put(f"faq/answer-{i:02d}.md", f"{question}\n\n{answer}", metadata={"metadataAttributes": {
            "team": "support", "year": 2025}})
    put("faq/training-video.mp4", b"\x00\x00\x00\x18ftypmp42" * 2000)
    put("policies/onboarding-deck.pptx", b"PK\x03\x04 demo deck " * 300, age=timedelta(days=10))
    put("faq/holiday-shipping.md", "# Holiday shipping\n" * 50, age=timedelta(hours=26))
    put("policies/refund-policy.pdf", b"%PDF updated " * 420, age=timedelta(hours=5))
    s3.create_bucket(Bucket="hr-policies-bucket")
    for name, text in HR_FILES.items():
        put(name, b"%PDF hr " * (len(text) // 8 + 50), bucket="hr-policies-bucket", age=timedelta(days=45))
    s3.create_bucket(Bucket="sales-playbooks-bucket")
    for key in SALES_FILES:
        put(key, b"%PDF sales " * 300, bucket="sales-playbooks-bucket", age=timedelta(days=30))
    file_chunks = {  # file -> (knowledge base, data source, [(page, metadata, text)]): what a search of it alone finds
        "s3://support-docs-bucket/policies/warranty.pdf": (SUPPORT, DOCS_S3, [
            (page, tags["policies/warranty.pdf"], text) for page, text in _warranty_chunks()]),
        **{f"s3://support-docs-bucket/faq/answer-{i:02d}.md": (SUPPORT, DOCS_S3, [
            (None, {"team": "support", "year": 2025}, f"{question}\n\n{answer}")])
           for i, (question, answer) in enumerate(FAQ_ANSWERS)},
        **{f"s3://hr-policies-bucket/{name}": (HR, HR_S3, [(None, {}, text)]) for name, text in HR_FILES.items()},
    }

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
        if dataSourceId == HR_S3:
            return {"documentDetails": [
                {"knowledgeBaseId": knowledgeBaseId, "dataSourceId": dataSourceId, "status": "INDEXED",
                 "updatedAt": now - timedelta(days=40),
                 "identifier": {"dataSourceType": "S3", "s3": {"uri": f"s3://hr-policies-bucket/{name}"}}}
                for name in HR_FILES]}
        if dataSourceId != DOCS_S3:  # sales was never synced; the legacy one's index is gone with its cluster
            return {"documentDetails": []}
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
        own = {"x-amz-bedrock-kb-data-source-id": DOCS_S3}  # Bedrock filters on its own keys too
        chunks = [c for c in CHUNKS if _matches(
            {**own, "x-amz-bedrock-kb-source-uri": f"s3://support-docs-bucket/{c[0]}", **c[2]}, config.get("filter"))]
        ranked = _rank(question, kind, chunks)[: config.get("numberOfResults", 5)]  # Bedrock's default is 5
        rerank = config.get("rerankingConfiguration")
        if rerank:
            exact = set(_words(question))
            ranked = sorted(((round(0.2 + 0.8 * len(exact & set(_words(c[3]))) / max(len(exact), 1), 4), c)
                             for _, c in ranked), key=lambda s: -s[0])
            ranked = ranked[: rerank["bedrockRerankingConfiguration"].get("numberOfRerankedResults", len(ranked))]
        return ranked

    def retrieve(knowledgeBaseId, retrievalQuery, retrievalConfiguration, **_):
        config = retrievalConfiguration["vectorSearchConfiguration"]
        target = _file_filter(config.get("filter"))
        if target in file_chunks and file_chunks[target][0] == knowledgeBaseId:  # one file the explorer opens
            _, ds_id, chunks = file_chunks[target]
            listed = [(target, page, md, text) for page, md, text in chunks]
            ranked = _rank(retrievalQuery["text"], config.get("overrideSearchType", "SEMANTIC"), listed)
            ids = {chunk[3]: f"{target.rsplit('/', 1)[-1].split('.')[0]}-{i:02d}" for i, chunk in enumerate(listed)}
            return {"retrievalResults": [
                _reference(score, chunk, 0, uri=target, chunk_id=ids[chunk[3]], data_source=ds_id)
                for score, chunk in ranked[: config.get("numberOfResults", 5)]]}
        found = search(knowledgeBaseId, retrievalQuery["text"], config)
        return {"retrievalResults": [_reference(score, chunk, i) for i, (score, chunk) in enumerate(found)]}

    def first_sentence(text):
        return text.split(". ")[0].rstrip(".") + "."

    def retrieve_and_generate(input, retrieveAndGenerateConfiguration, sessionId=None, **_):
        config = retrieveAndGenerateConfiguration["knowledgeBaseConfiguration"]
        listing = any(word in input["text"].lower() for word in ("summar", "list", "steps"))
        question = re.sub(r"(?i)\b(summari[sz]e|summary|as a list|list|steps|the rules for)\b", " ", input["text"])
        found = search(config["knowledgeBaseId"], question,
                       config.get("retrievalConfiguration", {}).get("vectorSearchConfiguration", {}))
        useful = [(s, c) for s, c in found if s >= max(0.55, found[0][0] - 0.1)][:3] if found else []
        if not useful:
            return {"output": {"text": "Sorry, I am unable to assist you with this request."},
                    "sessionId": sessionId or "demo-session-1"}
        # Asked for a summary or a list, it answers in markdown, as models often do: a bold lead-in and a bullet per
        # passage, each starting with its first word in bold.
        text, citations = "**Here's what the policies say:**\n\n" if listing else "", []
        for _, chunk in useful:
            sentence = first_sentence(chunk[3])
            if listing:
                first, _, rest = sentence.partition(" ")
                sentence = f"**{first}** {rest}"
                text += "- " if text.endswith("\n") else "\n- "
                start = len(text)
                text += sentence
            else:
                start = len(text) + (1 if text else 0)
                text += (" " if text else "") + sentence
            citations.append({"generatedResponsePart": {"textResponsePart": {
                "text": sentence, "span": {"start": start, "end": start + len(sentence) - 1}}},
                "retrievedReferences": [{k: v for k, v in _reference(None, chunk, 0).items()}]})
        text += ("\n\n" if listing else " ") + "Anything else is decided case by case by the support team."  # uncited
        return {"output": {"text": text}, "citations": citations, "sessionId": sessionId or "demo-session-1"}

    def retrieve_and_generate_stream(**params):
        """The same answer, as RetrieveAndGenerateStream's events: the text a few words at a time, then each
        citation once the text it covers has been written."""
        import time

        resp = retrieve_and_generate(**params)
        text = resp["output"]["text"]

        def events():
            pieces = text.split(" ")
            for i, word in enumerate(pieces):
                time.sleep(0.03)
                yield {"output": {"text": word + (" " if i < len(pieces) - 1 else "")}}
            for cite in resp.get("citations", []):
                yield {"citation": cite}

        return {"sessionId": resp["sessionId"], "stream": events()}

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
    runtime = _FakeAWS("bedrock-agent-runtime", {"retrieve": retrieve, "retrieve_and_generate": retrieve_and_generate,
                                                 "retrieve_and_generate_stream": retrieve_and_generate_stream},
                       latency={"retrieve": 0.3, "retrieve_and_generate": 1.9, "retrieve_and_generate_stream": 0.8})
    llm = _FakeAWS("bedrock-runtime", {"converse": converse}, latency={"converse": 2.1})
    bedrock = _FakeAWS("bedrock", {
        "list_foundation_models": lambda **_: {"modelSummaries": models},
        "list_inference_profiles": lambda **_: {"inferenceProfileSummaries": profiles},
    })
    return {"client": agent, "clients": {"bedrock-agent-runtime": runtime, "bedrock-runtime": llm,
                                         "bedrock": bedrock, "s3": s3}}


# ----------------------------------------------------------------------------- SageMaker

SM_DOMAIN = "d-acme12345678"
SM_ROLE = f"arn:aws:iam::{ACCOUNT}:role/service-role/AmazonSageMaker-ExecutionRole-20240611"


def seed_sagemaker_env() -> dict:
    """A fake machine (metadata, /proc, a home folder of sparse files) and fake sagemaker / sts / cloudwatch clients.
    moto has no Studio apps, spaces or ListApps."""
    import tempfile

    mod = importlib.import_module("aws_analyzer.sagemaker_env")
    now = datetime.now(timezone.utc)
    root = Path(tempfile.mkdtemp(prefix="sagemaker-demo-"))
    space_arn = f"arn:aws:sagemaker:{REGION}:{ACCOUNT}:app/{SM_DOMAIN}/churn-analysis/JupyterLab/default"
    meta = root / "opt/ml/metadata"
    meta.mkdir(parents=True)
    (meta / "resource-metadata.json").write_text(json.dumps({
        "AppType": "JupyterLab", "DomainId": SM_DOMAIN, "SpaceName": "churn-analysis", "UserProfileName": "",
        "ExecutionRoleArn": SM_ROLE, "ResourceArn": space_arn, "ResourceName": "default", "AppImageVersion": "latest"}))
    proc = root / "proc"
    proc.mkdir()
    (proc / "loadavg").write_text("0.31 0.42 0.38 2/412 8812\n")
    (proc / "meminfo").write_text("MemTotal:       32212254 kB\nMemFree:  9123456 kB\nMemAvailable:   20132659 kB\n")
    (proc / "uptime").write_text(f"{2 * 86400 + 5 * 3600 + 120}.4 1000.0\n")
    kernels = [(os.getpid(), 3_950_000, "-f /home/sagemaker-user/.local/share/jupyter/runtime/kernel-7f3a.json"),
               (48211, 5_600_000, "-f /home/sagemaker-user/.local/share/jupyter/runtime/kernel-1c9e.json"),
               (48377, 1_250_000, "-f /home/sagemaker-user/.local/share/jupyter/runtime/kernel-9b21.json")]
    for pid, rss, args in kernels + [(311, 420_000, None), (290, 95_000, None)]:
        folder = proc / str(pid)
        folder.mkdir()
        name = "python" if args else ("jupyter-lab" if pid == 311 else "sagemaker-idle-check")
        (folder / "status").write_text(f"Name:\t{name}\nVmRSS:\t{rss} kB\n")
        cmd = f"/opt/conda/bin/python -m ipykernel_launcher {args}" if args else f"/opt/conda/bin/{name}"
        (folder / "cmdline").write_bytes(cmd.replace(" ", "\0").encode() + b"\0")
    home = root / "home/sagemaker-user"
    for rel, size, days in [
        ("data/raw/events-2024.parquet", 9.8e9, 400), ("data/raw/events-2025-h1.parquet", 6.1e9, 390),
        ("data/raw/events-2025-h2.parquet", 5.4e9, 21), ("data/features/train.parquet", 2.2e9, 3),
        ("data/features/valid.parquet", 0.6e9, 3), ("models/xgb-2025-09/model.tar.gz", 0.9e9, 12),
        ("models/bert-finetune/checkpoint-2000/pytorch_model.bin", 1.3e9, 2),
        ("models/bert-finetune/checkpoint-4000/pytorch_model.bin", 1.3e9, 2),
        (".local/share/Trash/files/events-2023.parquet", 4.1e9, 40),
        (".cache/huggingface/hub/models--bert-base-uncased/model.safetensors", 0.44e9, 60),
        (".cache/huggingface/hub/models--roberta-large/model.safetensors", 1.4e9, 9),
        (".cache/pip/http-v2/wheels.bin", 0.8e9, 25),
        ("notebooks/churn-eda.ipynb", 38e6, 0.1), ("notebooks/.ipynb_checkpoints/churn-eda-checkpoint.ipynb", 37e6, 0.1),
        ("notebooks/feature-importance.ipynb", 4e6, 1), ("README.md", 3e3, 90),
    ]:
        path = home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.truncate(int(size))  # sparse: the size shows, nothing is written
        when = (now - timedelta(days=days)).timestamp()
        os.utime(path, (when, when))
    gb = 1024**3
    mod.SageMakerAnalyzer._disk_usage = staticmethod(
        lambda path: (50 * gb, 44 * gb, 6 * gb) if "sagemaker-user" in str(path) else (40 * gb, 17 * gb, 23 * gb))
    mod.SageMakerAnalyzer._cpu_count = staticmethod(lambda: 8)
    mod.SageMakerAnalyzer._gpu_query = lambda self: "NVIDIA A10G, 0, 5, 23028\n"

    def arn(kind: str, name: str) -> str:
        return f"arn:aws:sagemaker:{REGION}:{ACCOUNT}:{kind}/{name}"

    image = {"SageMakerImageArn": f"arn:aws:sagemaker:{REGION}:885854791233:image/sagemaker-distribution-gpu",
             "SageMakerImageVersionAlias": "3.1.0"}
    apps = {
        "churn-analysis": {"DomainId": SM_DOMAIN, "SpaceName": "churn-analysis", "AppType": "JupyterLab",
                           "AppName": "default", "Status": "InService",
                           "CreationTime": now - timedelta(days=2, hours=5),
                           "ResourceSpec": {"InstanceType": "ml.g5.2xlarge", **image}},
        "forecasting": {"DomainId": SM_DOMAIN, "SpaceName": "forecasting", "AppType": "CodeEditor",
                        "AppName": "default", "Status": "InService", "CreationTime": now - timedelta(days=5, hours=2),
                        "ResourceSpec": {"InstanceType": "ml.m5.xlarge", **image}},
    }
    notebooks = {
        "old-experiment": ("InService", "ml.m5.2xlarge", 100, now - timedelta(days=9, hours=3), None),
        "team-reporting": ("InService", "ml.t3.medium", 20, now - timedelta(hours=6), "auto-stop-idle"),
        "archive-2024": ("Stopped", "ml.m5.xlarge", 250, now - timedelta(days=210), None),
        "sandbox": ("Stopped", "ml.t3.medium", 50, now - timedelta(days=35), None),
    }

    def notebook(name: str) -> dict:
        status, instance, volume, changed, lifecycle = notebooks[name]
        desc = {"NotebookInstanceName": name, "NotebookInstanceArn": arn("notebook-instance", name),
                "NotebookInstanceStatus": status, "InstanceType": instance, "RoleArn": SM_ROLE,
                "DirectInternetAccess": "Enabled", "VolumeSizeInGB": volume, "RootAccess": "Enabled",
                "PlatformIdentifier": "notebook-al2023-v1", "CreationTime": now - timedelta(days=400),
                "LastModifiedTime": changed, "Url": f"{name}.notebook.{REGION}.sagemaker.aws"}
        if lifecycle:
            desc["NotebookInstanceLifecycleConfigName"] = lifecycle
        return desc

    domain = {"DomainId": SM_DOMAIN, "DomainArn": arn("domain", SM_DOMAIN), "DomainName": "acme-ml",
              "Status": "InService", "AuthMode": "IAM", "AppNetworkAccessType": "PublicInternetOnly",
              "DefaultUserSettings": {"ExecutionRole": SM_ROLE},
              "DefaultSpaceSettings": {"ExecutionRole": SM_ROLE}}
    endpoints = {
        "churn-v1": (now - timedelta(days=48), [("AllTraffic", "ml.m5.large", 1)], 0),
        "churn-v2": (now - timedelta(days=12), [("AllTraffic", "ml.m5.xlarge", 2)], 184_220),
        "sentiment-serverless": (now - timedelta(days=30), [("AllTraffic", None, 0)], 950),
    }

    def endpoint_summary(name: str) -> dict:
        return {"EndpointName": name, "EndpointArn": arn("endpoint", name), "CreationTime": endpoints[name][0],
                "LastModifiedTime": endpoints[name][0], "EndpointStatus": "InService"}

    def describe_endpoint(EndpointName: str) -> dict:
        created, variants, _ = endpoints[EndpointName]
        return {**endpoint_summary(EndpointName), "EndpointConfigName": f"{EndpointName}-config",
                "ProductionVariants": [
                    {"VariantName": v, "CurrentServerlessConfig": {"MemorySizeInMB": 2048, "MaxConcurrency": 5}}
                    if t is None else {"VariantName": v, "CurrentInstanceCount": n} for v, t, n in variants]}

    def describe_endpoint_config(EndpointConfigName: str) -> dict:
        name = EndpointConfigName.removesuffix("-config")
        created, variants, _ = endpoints[name]
        return {"EndpointConfigName": EndpointConfigName, "EndpointConfigArn": arn("endpoint-config", EndpointConfigName),
                "CreationTime": created, "ProductionVariants": [
                    {"VariantName": v, "ModelName": name, "ServerlessConfig": {"MemorySizeInMB": 2048, "MaxConcurrency": 5}}
                    if t is None else {"VariantName": v, "ModelName": name, "InstanceType": t, "InitialInstanceCount": n}
                    for v, t, n in variants]}

    def space(DomainId: str, SpaceName: str) -> dict:
        app_type = apps[SpaceName]["AppType"]
        key = "JupyterLabAppSettings" if app_type == "JupyterLab" else "CodeEditorAppSettings"
        return {"DomainId": DomainId, "SpaceName": SpaceName, "SpaceArn": arn("space", f"{DomainId}/{SpaceName}"),
                "Status": "InService", "CreationTime": now - timedelta(days=90),
                "SpaceSettings": {"AppType": app_type,
                                  "SpaceStorageSettings": {"EbsStorageSettings": {"EbsVolumeSizeInGb": 50}},
                                  key: {"DefaultResourceSpec": {"InstanceType": apps[SpaceName]["ResourceSpec"]["InstanceType"]},
                                        "CodeRepositories": [{"RepositoryUrl": "https://github.com/acme/churn-model.git"}]}},
                "OwnershipSettings": {"OwnerUserProfileName": "priya"},
                "SpaceSharingSettings": {"SharingType": "Private"},
                "Url": f"https://{DomainId}.studio.{REGION}.sagemaker.aws/jupyterlab/default"}

    def training_job(TrainingJobName: str) -> dict:
        return {"TrainingJobName": TrainingJobName, "TrainingJobArn": arn("training-job", TrainingJobName),
                "TrainingJobStatus": "InProgress", "SecondaryStatus": "Training",
                "CreationTime": now - timedelta(hours=3, minutes=10), "TrainingStartTime": now - timedelta(hours=3),
                "AlgorithmSpecification": {"TrainingInputMode": "File"}, "EnableManagedSpotTraining": True,
                "ResourceConfig": {"InstanceType": "ml.g5.2xlarge", "InstanceCount": 2, "VolumeSizeInGB": 50},
                "StoppingCondition": {"MaxRuntimeInSeconds": 86400}, "ModelArtifacts": {"S3ModelArtifacts": ""}}

    autostop = "#!/bin/bash\nset -e\nIDLE_TIME=3600\nwget .../scripts/auto-stop-idle/autostop.py\n"
    sagemaker = _FakeAWS("sagemaker", {
        "describe_app": lambda DomainId, AppType, AppName, SpaceName=None, UserProfileName=None: {
            **apps[SpaceName], "AppArn": arn("app", f"{DomainId}/{SpaceName}/{AppType}/{AppName}")},
        "describe_domain": lambda DomainId: domain,
        "describe_space": space,
        "describe_user_profile": lambda DomainId, UserProfileName: {
            "DomainId": DomainId, "UserProfileName": UserProfileName, "UserSettings": {"ExecutionRole": SM_ROLE}},
        "list_notebook_instances": lambda **_: {"NotebookInstances": [
            {k: v for k, v in notebook(n).items() if k in ("NotebookInstanceName", "NotebookInstanceArn",
                                                        "NotebookInstanceStatus", "InstanceType", "CreationTime",
                                                        "LastModifiedTime", "NotebookInstanceLifecycleConfigName")}
            for n in notebooks]},
        "describe_notebook_instance": lambda NotebookInstanceName: notebook(NotebookInstanceName),
        "describe_notebook_instance_lifecycle_config": lambda NotebookInstanceLifecycleConfigName: {
            "NotebookInstanceLifecycleConfigName": NotebookInstanceLifecycleConfigName,
            "OnStart": [{"Content": base64.b64encode(autostop.encode()).decode()}]},
        "list_apps": lambda **_: {"Apps": list(apps.values()) + [
            {"DomainId": SM_DOMAIN, "UserProfileName": "priya", "AppType": "JupyterServer", "AppName": "default",
             "Status": "InService", "ResourceSpec": {"InstanceType": "system"}}]},
        "list_domains": lambda **_: {"Domains": [{"DomainId": SM_DOMAIN, "DomainName": "acme-ml"}]},
        "list_spaces": lambda **_: {"Spaces": [{"DomainId": SM_DOMAIN, "SpaceName": n} for n in apps]},
        "list_endpoints": lambda **_: {"Endpoints": [endpoint_summary(n) for n in endpoints]},
        "describe_endpoint": describe_endpoint,
        "describe_endpoint_config": describe_endpoint_config,
        "list_training_jobs": lambda **_: {"TrainingJobSummaries": [
            {"TrainingJobName": "xgb-tuning-7", "TrainingJobArn": arn("training-job", "xgb-tuning-7"),
             "CreationTime": now - timedelta(hours=3, minutes=10), "TrainingJobStatus": "InProgress"}]},
        "describe_training_job": training_job,
        "list_processing_jobs": lambda **_: {"ProcessingJobSummaries": []},
    })

    def metric_data(MetricDataQueries: list, StartTime, EndTime, **_) -> dict:
        results = []
        for q in MetricDataQueries:
            dims = {d["Name"]: d["Value"] for d in q["MetricStat"]["Metric"]["Dimensions"]}
            count = endpoints.get(dims.get("EndpointName"), (None, None, 0))[2]
            results.append({"Id": q["Id"], "Label": "Invocations", "StatusCode": "Complete",
                            "Timestamps": [EndTime] if count else [], "Values": [float(count)] if count else []})
        return {"MetricDataResults": results}

    sts = _FakeAWS("sts", {"get_caller_identity": lambda: {
        "UserId": "AROAEXAMPLE:SageMaker", "Account": ACCOUNT,
        "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/{SM_ROLE.rsplit('/', 1)[1]}/SageMaker"}})
    cloudwatch = _FakeAWS("cloudwatch", {"get_metric_data": metric_data})
    return {"client": sagemaker, "clients": {"sts": sts, "cloudwatch": cloudwatch}, "root": str(root)}


# ----------------------------------------------------------------------------- OpenSearch

OS_TOPICS = {  # topic -> words a question about it uses, and the passages indexed about it
    0: (("refund", "refunds", "money back", "return"), [
        "Refunds go back to the original payment method within 5 to 10 business days.",
        "To request a refund, open the order and choose Return or refund within 30 days of delivery.",
        "Digital goods can be refunded within 14 days if they haven't been downloaded.",
        "Refunds for orders paid with a gift card are returned as store credit.",
    ]),
    1: (("ship", "shipping", "delivery", "deliver", "track"), [
        "Standard shipping takes 3 to 5 business days; express arrives the next business day.",
        "Track a parcel from Orders, Track package, with the tracking number in the confirmation email.",
        "Orders over $50 ship free within the continental US.",
        "Holiday orders placed after December 18 may arrive after Christmas.",
    ]),
    2: (("account", "password", "login", "sign in", "email"), [
        "Reset a forgotten password from the sign-in page with Forgot password.",
        "Two-step verification can be turned on under Account, Security.",
        "An account locked after five failed sign-ins unlocks itself after 30 minutes.",
        "Change the email on an account under Account, Profile; we send a link to confirm it.",
    ]),
    3: (("bill", "billing", "invoice", "charge", "payment"), [
        "Invoices are emailed on the first business day of each month.",
        "A pending charge disappears within 3 days if the order is cancelled.",
        "Error E1234 means the card issuer declined the payment: try another card.",
        "Download past invoices as PDF from Billing, Invoices.",
    ]),
}


def _os_topic(text: str) -> int | None:
    lowered = text.lower()
    for topic, (words, _) in OS_TOPICS.items():
        if any(word in lowered for word in words):
            return topic
    return None


def seed_opensearch() -> dict:
    """moto for the domains; a fake opensearchserverless, CloudWatch, STS and Bedrock (a toy embedding model that
    knows four topics), and fake OpenSearch clusters behind each endpoint (tests/fake_opensearch.py)."""
    import boto3

    sys.path.insert(0, str(ROOT / "tests"))
    from fake_opensearch import FakeCluster, FakeIndex, topic_vector, unit

    es = boto3.client("opensearch", region_name=REGION)
    secure = {"EncryptionAtRestOptions": {"Enabled": True}, "NodeToNodeEncryptionOptions": {"Enabled": True},
              "DomainEndpointOptions": {"EnforceHTTPS": True}}
    es.create_domain(
        DomainName="vectors-prod", EngineVersion="OpenSearch_2.17",
        ClusterConfig={"InstanceType": "r6g.large.search", "InstanceCount": 3, "DedicatedMasterEnabled": True,
                       "DedicatedMasterType": "m6g.large.search", "DedicatedMasterCount": 3,
                       "ZoneAwarenessEnabled": True, "ZoneAwarenessConfig": {"AvailabilityZoneCount": 3}},
        EBSOptions={"EBSEnabled": True, "VolumeType": "gp3", "VolumeSize": 200},
        AdvancedSecurityOptions={"Enabled": True, "InternalUserDatabaseEnabled": False}, **secure)
    es.create_domain(
        DomainName="search-legacy", EngineVersion="Elasticsearch_7.10",
        ClusterConfig={"InstanceType": "m5.large.search", "InstanceCount": 2},
        EBSOptions={"EBSEnabled": True, "VolumeType": "gp2", "VolumeSize": 100},
        AccessPolicies=json.dumps({"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Principal": {"AWS": "*"}, "Action": "es:*", "Resource": "*"}]}))
    es.create_domain(
        DomainName="rag-dev", EngineVersion="OpenSearch_2.11",
        ClusterConfig={"InstanceType": "t3.medium.search", "InstanceCount": 1},
        EBSOptions={"EBSEnabled": True, "VolumeType": "gp2", "VolumeSize": 20}, **secure)

    rng = random.Random(42)

    def passages(n: int, dims: int, *, missing: float = 0.0, repeats: int = 0, lengths: bool = False) -> list:
        docs = []
        for i in range(n):
            topic = i % 4
            products = ("the store", "the mobile app", "the marketplace")
            text = f"{OS_TOPICS[topic][1][(i // 4) % 4]} This applies to {products[i % 3]} (article {i // 4 + 1})."
            vector = topic_vector(topic, dims, rng)
            if lengths:  # unnormalized vectors whose length grows with the text, as some models return
                vector = [x * rng.uniform(0.4, 2.4) for x in vector]
            source = {"text": text, "embedding": vector, "lang": "en" if i % 5 else "de",
                      "product": ["store", "app", "marketplace"][i % 3], "updated": f"2026-0{1 + i % 9}-15",
                      "metadata": {"source": f"s3://acme-support/articles/{['refunds', 'shipping', 'account', 'billing'][topic]}-{i // 4:03d}.md",
                                   "page": 1 + i % 3}}
            if missing and rng.random() < missing:
                source.pop("embedding")
            docs.append((f"art-{i:05d}", source))
        for j in range(repeats):  # the same passages indexed twice by an overlapping load
            doc_id, source = docs[j * 7]
            docs.append((f"{doc_id}-dup", json.loads(json.dumps(source))))
        return docs

    def vector_mapping(dims: int, engine: str, space: str, **extra) -> dict:
        return {"properties": {
            "text": {"type": "text", "fields": {"keyword": {"type": "keyword", "ignore_above": 256}}},
            "embedding": {"type": "knn_vector", "dimension": dims, "method": {
                "name": "hnsw", "engine": engine, "space_type": space,
                "parameters": {"m": 16, "ef_construction": 512}}},
            "lang": {"type": "keyword"}, "product": {"type": "keyword"}, "updated": {"type": "date"},
            "metadata": {"properties": {"source": {"type": "keyword"}, "page": {"type": "integer"}}},
            **extra}}

    prod = FakeCluster([
        FakeIndex("support-docs", vector_mapping(1024, "faiss", "cosinesimil"),
                  passages(400, 1024, missing=0.015, repeats=9), settings={"knn": True}, shards=2, replicas=1,
                  segments=34, size_bytes=29 * 1024**3, scale=4500),
        FakeIndex("product-search", vector_mapping(384, "lucene", "cosinesimil"), passages(120, 384),
                  settings={"knn": True}, shards=1, replicas=1, size_bytes=740 * 1024**2, scale=650),
        FakeIndex("legacy-faq", vector_mapping(768, "nmslib", "innerproduct"), passages(80, 768, lengths=True),
                  settings={"knn": True}, shards=5, replicas=1, size_bytes=96 * 1024**2, scale=20),
        FakeIndex("app-logs-2026.10", {"properties": {"message": {"type": "text"}, "level": {"type": "keyword"}}},
                  [(str(i), {"message": "GET /health 200", "level": "info"}) for i in range(50)],
                  shards=1, replicas=1, size_bytes=2 * 1024**3, scale=200_000),
        FakeIndex(".kibana_1", {"properties": {"type": {"type": "keyword"}}}, []),
    ], nodes=3, knn_memory_kb=11_200_000, evictions=36)
    kb_docs = []
    for doc_id, source in passages(240, 1024, repeats=4):
        kb_docs.append((doc_id, {
            "bedrock-knowledge-base-default-vector": source["embedding"],
            "AMAZON_BEDROCK_TEXT_CHUNK": source["text"],
            "AMAZON_BEDROCK_METADATA": json.dumps({"source": source["metadata"]["source"]}),
            "x-amz-bedrock-kb-source-uri": source["metadata"]["source"],
            "x-amz-bedrock-kb-data-source-id": "DS0SUPPORT",
        }))
    kb = FakeCluster([FakeIndex("bedrock-knowledge-base-default-index", {"properties": {
        "bedrock-knowledge-base-default-vector": {"type": "knn_vector", "dimension": 1024, "method": {
            "name": "hnsw", "engine": "faiss", "space_type": "l2", "parameters": {"m": 16, "ef_construction": 512}}},
        "AMAZON_BEDROCK_TEXT_CHUNK": {"type": "text"},
        "AMAZON_BEDROCK_METADATA": {"type": "text", "index": False},
        "x-amz-bedrock-kb-source-uri": {"type": "keyword"},
        "x-amz-bedrock-kb-data-source-id": {"type": "keyword"},
    }}, kb_docs, settings={"knn": True, "knn.algo_param.ef_search": 512}, scale=55)], serverless=True)
    dev = FakeCluster([FakeIndex("notes", vector_mapping(1024, "faiss", "l2"), passages(40, 1024))], nodes=1)

    domains = {"vectors-prod": prod, "search-legacy": FakeCluster([]), "rag-dev": dev}
    collections = {"kb-support": ("8k2mq7cxlz0r4ef9wtya", kb), "kb-sandbox": ("p1vn4s8hjw2d6ugq0ezc", FakeCluster(
        [], serverless=True)), "app-logs": ("c9xr2k5mtf3e7yzn1bqw", FakeCluster([], serverless=True))}
    clusters = {f"{name}.{REGION}.es.amazonaws.com": fake for name, fake in domains.items()}
    clusters.update({f"{cid}.{REGION}.aoss.amazonaws.com": fake for cid, fake in collections.values()})

    def http(method: str, url: str, body, headers):
        from urllib.parse import urlsplit

        return clusters[urlsplit(url).hostname](method, url, body, headers)

    created = int((NOW.replace(tzinfo=timezone.utc) - timedelta(days=120)).timestamp() * 1000)
    details = [
        {"id": cid, "name": name, "arn": f"arn:aws:aoss:{REGION}:{ACCOUNT}:collection/{cid}", "status": "ACTIVE",
         "type": {"kb-support": "VECTORSEARCH", "kb-sandbox": "VECTORSEARCH", "app-logs": "TIMESERIES"}[name],
         "standbyReplicas": "DISABLED" if name == "kb-sandbox" else "ENABLED", "kmsKeyArn": "auto",
         "createdDate": created + i * 86_400_000 * 20, "lastModifiedDate": created,
         "collectionEndpoint": f"https://{cid}.{REGION}.aoss.amazonaws.com",
         "dashboardEndpoint": f"https://{cid}.{REGION}.aoss.amazonaws.com/_dashboards",
         "description": {"kb-support": "Vector store of the support-docs knowledge base"}.get(name, "")}
        for i, (name, (cid, _)) in enumerate(collections.items())
    ]
    policies = {
        "kb-public": [{"Rules": [{"ResourceType": "collection", "Resource": ["collection/kb-*"]}],
                       "AllowFromPublic": True}],
        "logs-vpc": [{"Rules": [{"ResourceType": "collection", "Resource": ["collection/app-logs"]}],
                      "SourceVPCEs": ["vpce-0a1b2c3d4e5f6a7b8"]}],
    }

    def batch_get(ids=None, names=None):
        found = [d for d in details if (ids and d["id"] in ids) or (names and d["name"] in names)]
        return {"collectionDetails": found, "collectionErrorDetails": []}

    aoss = _FakeAWS("opensearchserverless", {
        "list_collections": lambda **_: {"collectionSummaries": [
            {k: d[k] for k in ("id", "name", "status", "arn")} for d in details]},
        "batch_get_collection": batch_get,
        "list_security_policies": lambda type, **_: {"securityPolicySummaries": [
            {"name": n, "type": type} for n in policies]},
        "get_security_policy": lambda name, type: {"securityPolicyDetail": {
            "name": name, "type": type, "policy": policies[name]}},
        "get_account_settings": lambda: {"accountSettingsDetail": {"capacityLimits": {
            "maxIndexingCapacityInOCU": 10, "maxSearchCapacityInOCU": 10}}},
    })

    def metric_data(MetricDataQueries, **_):
        level = {"IndexingOCU": 2.5, "SearchOCU": 3.4}
        return {"MetricDataResults": [
            {"Id": q["Id"], "Label": q["MetricStat"]["Metric"]["MetricName"], "StatusCode": "Complete",
             "Values": [level[q["MetricStat"]["Metric"]["MetricName"]] + 0.1 * (h % 3) for h in range(24)]}
            for q in MetricDataQueries]}

    def invoke(modelId, body, contentType=None, accept=None):
        request = json.loads(body)
        text = request.get("inputText") or request["texts"][0]
        size = request.get("dimensions") or 1024
        topic = _os_topic(text)
        vector = topic_vector(topic, size, random.Random(len(text))) if topic is not None else unit(
            [random.Random(text).gauss(0, 1) for _ in range(size)])
        payload = {"embedding": vector, "inputTextTokenCount": len(text.split()) + 2}
        return {"body": io.BytesIO(json.dumps(payload).encode()), "contentType": "application/json"}

    sts = _FakeAWS("sts", {"get_caller_identity": lambda: {
        "UserId": "AROAEXAMPLE:SageMaker", "Account": ACCOUNT,
        "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/AmazonSageMaker-ExecutionRole/SageMaker"}})
    return {"http": http, "clients": {
        "opensearchserverless": aoss, "cloudwatch": _FakeAWS("cloudwatch", {"get_metric_data": metric_data}),
        "sts": sts, "bedrock-runtime": _FakeAWS("bedrock-runtime", {"invoke_model": invoke})}}


# ----------------------------------------------------------------------------- Lambda

LAMBDA_RUN = "c0ffee00-1d2e-4f3a-9b8c-7d6e5f4a3b2c"  # a request ID the orders-etl logs use, so calls can be copied


def _lambda_package(files: dict[str, str]) -> bytes:
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


ETL_SOURCE = '''"""Loads the day's orders from S3 into the warehouse."""
import json
import os

import boto3

from db import connect

s3 = boto3.client("s3")
table = boto3.resource("dynamodb").Table(os.environ["ORDERS_TABLE"])


def handler(event, context):
    loaded = 0
    for record in event["Records"]:
        body = json.loads(record["body"])
        order = s3.get_object(Bucket=body["bucket"], Key=body["key"])
        rows = json.loads(order["Body"].read())
        with connect(os.environ["DB_HOST"], os.environ["DB_PASSWORD"]) as db:
            for row in rows:
                db.insert("orders", row)
                table.put_item(Item={"pk": row["customer_id"], "sk": row["order_id"]})
                loaded += 1
    print(f"loaded {loaded} orders")
    return {"loaded": loaded}
'''


def _epoch_ms(moment: datetime) -> int:
    """A naive UTC time (NOW's kind) as CloudWatch Logs' epoch milliseconds, whatever the machine's time zone."""
    return int(moment.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _report_api_logs(logs) -> None:
    """report-api logs JSON (its LoggingConfig says so): Lambda's platform.* records and the app's own lines with a
    level and a message, over the last three hours: a WARN now and then, and unhandled TypeErrors."""
    rng = random.Random(23)
    streams = [f"2026/10/06/[$LATEST]{i:032x}" for i in (0xD4, 0xE5)]
    events: dict[str, list[dict]] = {stream: [] for stream in streams}
    for stream in streams:
        logs.create_log_stream(logGroupName="/aws/lambda/report-api", logStreamName=stream)

    def stamp(moment: datetime) -> str:
        return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"

    for i in range(48):
        start = NOW - timedelta(minutes=4 * i + rng.uniform(0, 2))
        rid = f"{rng.getrandbits(32):08x}-{rng.getrandbits(16):04x}-4{rng.getrandbits(12):03x}-b{rng.getrandbits(12):03x}-{rng.getrandbits(48):012x}"
        failed, slow, cold = i in (3, 17, 30), i % 7 == 5, i % 16 == 0
        took = rng.uniform(900, 2100) if slow else rng.uniform(40, 140)
        region = rng.choice(["EMEA", "AMER", "APAC"])

        def at(seconds: float) -> datetime:
            return start + timedelta(seconds=seconds)

        def app(seconds: float, level: str, message, **fields) -> tuple[datetime, str]:
            return at(seconds), json.dumps({"timestamp": stamp(at(seconds)), "level": level, "requestId": rid,
                                            "message": message, **fields})

        lines = [(at(0), json.dumps({"time": stamp(at(0)), "type": "platform.start",
                                      "record": {"requestId": rid, "version": "$LATEST"}})),
                 app(0.004, "INFO", f"GET /reports/weekly?region={region}", path="/reports/weekly",
                     query={"region": region})]
        if slow:
            lines.append(app(0.01, "WARN", f"cache miss for weekly-2026-41-{region.lower()}: rebuilding it from "
                                           "the warehouse", cacheKey=f"weekly-2026-41-{region.lower()}"))
        if failed:
            lines.append(app(took / 1000 - 0.002, "ERROR", {
                "errorType": "TypeError", "errorMessage": "Cannot read properties of undefined (reading 'total')",
                "stack": ["TypeError: Cannot read properties of undefined (reading 'total')",
                          "    at buildReport (file:///var/task/index.mjs:42:31)",
                          "    at Runtime.handler (file:///var/task/index.mjs:12:18)"]}))
        else:
            lines.append(app(took / 1000 - 0.001, "INFO", f"200 in {took:.0f} ms", statusCode=200,
                             rows=rng.randint(120, 900)))
        metrics = {"durationMs": round(took, 2), "billedDurationMs": int(took) + 1, "memorySizeMB": 512,
                   "maxMemoryUsedMB": rng.randint(88, 131)}
        if cold:
            metrics["initDurationMs"] = round(rng.uniform(180, 320), 2)
        record = {"requestId": rid, "metrics": metrics, "status": "error" if failed else "success"}
        if failed:
            record["errorType"] = "TypeError"
        lines.append((at(took / 1000), json.dumps({"time": stamp(at(took / 1000)), "type": "platform.report",
                                                   "record": record})))
        events[streams[i % 2]] += [{"timestamp": _epoch_ms(moment), "message": message} for moment, message in lines]
    for stream, batch in events.items():
        logs.put_log_events(logGroupName="/aws/lambda/report-api", logStreamName=stream,
                            logEvents=sorted(batch, key=lambda e: e["timestamp"]))


def seed_lambda_functions() -> dict:
    """moto for the functions, their triggers, CloudWatch numbers and logs. moto has no GetAccountSettings,
    ListProvisionedConcurrencyConfigs or GetRuntimeManagementConfig, leaves reserved concurrency out of GetFunction
    and keeps no real log sizes, so the seeder hands the analyzer Lambda and Logs clients per region that answer
    those, and serves the deployment packages GetFunction links to."""
    import urllib.request

    import boto3

    rng = random.Random(11)
    other = "eu-west-1"
    role = boto3.client("iam").create_role(RoleName="acme-lambda-role", AssumeRolePolicyDocument="{}")["Role"]["Arn"]
    packages = {
        "orders-etl": _lambda_package({
            "etl.py": ETL_SOURCE,
            "db.py": "def connect(host, password):\n    ...\n",
            "requirements.txt": "boto3==1.40.0\npsycopg2-binary==2.9.10\n",
            ".env": "DB_PASSWORD=change-me\n",
            "boto3/__init__.py": "# boto3, bundled\n" + "#" * (2 * 1024**2),
            "botocore/data/endpoints.json": '{"partitions": []}' + " " * (11 * 1024**2),
            "psycopg2/_psycopg.so": "\0" * (3 * 1024**2),
        }),
        "churn-scoring": _lambda_package({"app.py": "def predict(event, context):\n    return {'score': 0.42}\n",
                                          "model/churn.json": '{"trees": []}' + " " * 400_000}),
        "report-api": _lambda_package({"index.mjs": "export const handler = async (event) => ({ statusCode: 200 });\n"}),
        "feature-backfill": _lambda_package({"backfill.py": "def run(event, context):\n    return 'done'\n"}),
        "support-agent-actions": _lambda_package({"actions.py": "def handler(event, context):\n    return event\n"}),
        "gdpr-export": _lambda_package({"export.py": "def handler(event, context):\n    return 'exported'\n"}),
    }

    def create(name: str, region: str = REGION, **settings) -> None:
        params = dict(FunctionName=name, Role=role, Code={"ZipFile": packages[name]}, Runtime="python3.12",
                      Handler="app.handler")
        boto3.client("lambda", region_name=region).create_function(**{**params, **settings})

    create("orders-etl", Runtime="python3.9", Handler="etl.handler", MemorySize=1024, Timeout=60,
           Description="Loads the day's orders from S3 into the warehouse",
           Environment={"Variables": {"DB_HOST": "warehouse.acme.internal", "DB_PASSWORD": "change-me",
                                      "ORDERS_TABLE": "orders"}},
           Tags={"team": "data", "cost-center": "analytics"})
    create("churn-scoring", Handler="app.predict", Architectures=["arm64"], MemorySize=2048, Timeout=30,
           Description="Scores customers for churn with the model trained in SageMaker", Tags={"team": "ml"})
    create("report-api", Runtime="nodejs20.x", Handler="index.handler", MemorySize=512, Timeout=10,
           Description="Serves the weekly sales report as JSON")
    create("feature-backfill", Runtime="python3.10", Handler="backfill.run", MemorySize=3008, Timeout=900,
           Description="One-off backfill of the feature store (2025)")
    create("support-agent-actions", Runtime="python3.13", Handler="actions.handler", Architectures=["arm64"],
           MemorySize=256, Timeout=30, Description="Action group for the support Bedrock agent")
    create("gdpr-export", region=other, Runtime="python3.11", Handler="export.handler", MemorySize=512, Timeout=120,
           Description="Exports a customer's data on request")

    lam = boto3.client("lambda", region_name=REGION)
    for code in range(2, 4):  # three published versions of churn-scoring; live points at the newest
        lam.update_function_code(FunctionName="churn-scoring", ZipFile=_lambda_package(
            {"app.py": f"def predict(event, context):\n    return {{'score': 0.{code}}}\n"}))
        lam.publish_version(FunctionName="churn-scoring", Description=f"model v{code}")
    lam.publish_version(FunctionName="churn-scoring", Description="model v1")
    newest = max(int(v["Version"]) for v in lam.list_versions_by_function(FunctionName="churn-scoring")["Versions"]
                 if v["Version"] != "$LATEST")
    lam.create_alias(FunctionName="churn-scoring", Name="live", FunctionVersion=str(newest))
    lam.put_function_event_invoke_config(FunctionName="churn-scoring", DestinationConfig={
        "OnFailure": {"Destination": f"arn:aws:sqs:{REGION}:{ACCOUNT}:churn-scoring-failed"}})
    lam.add_permission(FunctionName="orders-etl", StatementId="acme-uploads", Action="lambda:InvokeFunction",
                       Principal="s3.amazonaws.com", SourceArn="arn:aws:s3:::acme-uploads", SourceAccount=ACCOUNT)
    lam.add_permission(FunctionName="churn-scoring", StatementId="nightly-scoring", Action="lambda:InvokeFunction",
                       Principal="events.amazonaws.com",
                       SourceArn=f"arn:aws:events:{REGION}:{ACCOUNT}:rule/nightly-scoring")
    lam.add_permission(FunctionName="support-agent-actions", StatementId="bedrock-agent",
                       Action="lambda:InvokeFunction", Principal="bedrock.amazonaws.com",
                       SourceArn=f"arn:aws:bedrock:{REGION}:{ACCOUNT}:agent/SUPPORT01A")
    lam.create_function_url_config(FunctionName="report-api", AuthType="NONE")
    report_policy = {"Version": "2012-10-17", "Statement": [
        {"Sid": "FunctionURLAllowPublicAccess", "Effect": "Allow", "Principal": "*",
         "Action": "lambda:InvokeFunctionUrl", "Resource": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:report-api",
         "Condition": {"StringEquals": {"lambda:FunctionUrlAuthType": "NONE"}}},
        {"Sid": "FunctionURLAllowInvokeAction", "Effect": "Allow", "Principal": "*", "Action": "lambda:InvokeFunction",
         "Resource": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:report-api",
         "Condition": {"Bool": {"lambda:InvokedViaFunctionUrl": "true"}}}]}
    for region, queue, function in ((REGION, "orders-queue", "orders-etl"), (other, "export-requests", "gdpr-export")):
        sqs = boto3.client("sqs", region_name=region)
        url = sqs.create_queue(QueueName=queue)["QueueUrl"]
        arn = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        boto3.client("lambda", region_name=region).create_event_source_mapping(
            EventSourceArn=arn, FunctionName=function, BatchSize=10)

    # 30 days of CloudWatch numbers: (calls a day, error share, throttles a day, average ms, longest ms, most at once,
    # bytes logged a day). orders-etl's errors started three days ago.
    usage = {
        "orders-etl": (1200, 0.004, 0, 4_000, 58_200, 6, 1.0 * 1024**3),
        "churn-scoring": (30, 0.0, 0, 900, 2_400, 1, 2 * 1024**2),
        "report-api": (5000, 0.002, 25, 120, 2_100, 10, 300 * 1024**2),
        "support-agent-actions": (200, 0.0, 0, 350, 1_200, 2, 20 * 1024**2),
        "gdpr-export": (50, 0.0, 0, 8_000, 31_000, 2, 5 * 1024**2),
    }
    today = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    for name, (calls, errors, throttles, average, longest, most, logged) in usage.items():
        region = other if name == "gdpr-export" else REGION
        cloudwatch = boto3.client("cloudwatch", region_name=region)
        dims = [{"Name": "FunctionName", "Value": name}]
        data, logs_data = [], []
        for day in range(30):
            when = today - timedelta(days=day) + timedelta(hours=12 if day else 0, minutes=30)
            count = round(calls * rng.uniform(0.8, 1.2))
            failed = round(count * (0.03 if name == "orders-etl" and day < 3 else errors))
            data += [
                {"MetricName": "Invocations", "Dimensions": dims, "Timestamp": when, "Value": count},
                {"MetricName": "Errors", "Dimensions": dims, "Timestamp": when, "Value": failed},
                {"MetricName": "Throttles", "Dimensions": dims, "Timestamp": when,
                 "Value": round(throttles * rng.uniform(0, 2))},
                {"MetricName": "Duration", "Dimensions": dims, "Timestamp": when, "StatisticValues": {
                    "SampleCount": count, "Sum": count * average * rng.uniform(0.9, 1.1), "Minimum": 5.0,
                    "Maximum": longest * (1 if day == 1 else rng.uniform(0.6, 0.95))}},
                {"MetricName": "ConcurrentExecutions", "Dimensions": dims, "Timestamp": when,
                 "Value": float(most if day == 1 else max(1, round(most * rng.uniform(0.4, 1))))},
            ]
            logs_data.append({"MetricName": "IncomingBytes", "Timestamp": when, "Value": logged * rng.uniform(0.9, 1.1),
                              "Dimensions": [{"Name": "LogGroupName", "Value": f"/aws/lambda/{name}"}]})
        for start in range(0, len(data), 20):
            cloudwatch.put_metric_data(Namespace="AWS/Lambda", MetricData=data[start:start + 20])
        for start in range(0, len(logs_data), 20):
            cloudwatch.put_metric_data(Namespace="AWS/Logs", MetricData=logs_data[start:start + 20])
    for region, peak in ((REGION, 18.0), (other, 2.0)):
        boto3.client("cloudwatch", region_name=region).put_metric_data(Namespace="AWS/Lambda", MetricData=[
            {"MetricName": "ConcurrentExecutions", "Timestamp": today - timedelta(days=day) + timedelta(hours=12),
             "Value": peak * rng.uniform(0.5, 1)} for day in range(1, 30)])

    # orders-etl's logs for the last 24 hours: a run every 20 minutes or so, with the errors CloudWatch counted.
    logs = boto3.client("logs", region_name=REGION)
    for name in ("orders-etl", "churn-scoring", "report-api", "support-agent-actions"):
        logs.create_log_group(logGroupName=f"/aws/lambda/{name}")
    for name, days in (("churn-scoring", 30), ("report-api", 14), ("support-agent-actions", 90)):
        logs.put_retention_policy(logGroupName=f"/aws/lambda/{name}", retentionInDays=days)
    streams = [f"2026/10/06/[$LATEST]{i:032x}" for i in (0xA1, 0xB2, 0xC3)]
    for stream in streams:
        logs.create_log_stream(logGroupName="/aws/lambda/orders-etl", logStreamName=stream)
    events: dict[str, list[dict]] = {stream: [] for stream in streams}
    for i in range(70):
        start = NOW - timedelta(minutes=20 * i + rng.randint(0, 5), seconds=rng.randint(0, 59))
        rid = LAMBDA_RUN if i == 2 else f"{rng.getrandbits(32):08x}-{rng.getrandbits(16):04x}-4{rng.getrandbits(12):03x}-a{rng.getrandbits(12):03x}-{rng.getrandbits(48):012x}"
        stream = streams[i % 3]
        cold = i % 9 == 0
        kind = "timeout" if i in (5, 31, 47) else "key" if i in (2, 9, 14, 22, 40, 51, 58, 66) else (
            "denied" if i in (18, 37) else "ok")
        seconds = 60.0 if kind == "timeout" else rng.uniform(1.5, 9.0) if kind == "ok" else rng.uniform(0.4, 2.0)
        ms = lambda offset: _epoch_ms(start + timedelta(seconds=offset))  # noqa: E731
        lines = [(0, f"START RequestId: {rid} Version: $LATEST"),
                 (0.05, f"reading s3://acme-uploads/orders/2026-10-06/batch-{i:03d}.json")]
        if cold:  # the runtime starting, just before a cold start's first line
            lines.insert(0, (-1.2, "INIT_START Runtime Version: python:3.9.v68\tRuntime Version ARN: "
                                   "arn:aws:lambda:us-east-1::runtime:5ad8c1e3b3f0b7e2a1f9d4c6e8b0a2f4c6d8e0b2"))
        if kind == "key":
            lines.append((seconds - 0.01, "[ERROR] KeyError: 'customer_id'\nTraceback (most recent call last):\n  "
                                          "File \"/var/task/etl.py\", line 23, in handler\n    table.put_item(Item="
                                          "{\"pk\": row[\"customer_id\"], \"sk\": row[\"order_id\"]})"))
        elif kind == "denied":
            lines.append((seconds - 0.01, "[ERROR] ClientError: An error occurred (AccessDeniedException) when calling "
                                          "the PutItem operation: User: arn:aws:sts::123456789012:assumed-role/"
                                          "acme-lambda-role/orders-etl is not authorized to perform: dynamodb:PutItem "
                                          "on resource: arn:aws:dynamodb:us-east-1:123456789012:table/orders"))
        elif kind == "timeout":
            lines.append((60.0, f"{(start + timedelta(seconds=60)).strftime('%Y-%m-%dT%H:%M:%S.000Z')} {rid} Task "
                                "timed out after 60.00 seconds"))
        else:
            lines.append((seconds - 0.02, f"loaded {rng.randint(800, 1500)} orders"))
        used = rng.randint(140, 182)
        report = (f"REPORT RequestId: {rid}\tDuration: {seconds * 1000:.2f} ms\tBilled Duration: "
                  f"{int(seconds * 1000) + 1} ms\tMemory Size: 1024 MB\tMax Memory Used: {used} MB")
        if cold:
            report += f"\tInit Duration: {rng.uniform(900, 1400):.2f} ms"
        if kind == "timeout":
            report += "\tStatus: timeout"
        lines += [(seconds, f"END RequestId: {rid}"), (seconds + 0.001, report)]
        events[stream] += [{"timestamp": ms(offset), "message": message} for offset, message in lines]
    for stream, batch in events.items():
        logs.put_log_events(logGroupName="/aws/lambda/orders-etl", logStreamName=stream,
                            logEvents=sorted(batch, key=lambda e: e["timestamp"]))
    _report_api_logs(logs)
    stored = {"/aws/lambda/orders-etl": int(24.5 * 1024**3), "/aws/lambda/report-api": int(1.2 * 1024**3),
              "/aws/lambda/churn-scoring": 40 * 1024**2, "/aws/lambda/support-agent-actions": 180 * 1024**2}

    class _Answer:
        """What urlopen returns, for a deployment package served from memory."""

        def __init__(self, body: bytes):
            self.body, self.headers = body, {"Content-Length": str(len(body))}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, size: int = -1) -> bytes:
            return self.body if size is None or size < 0 else self.body[:size]

    real_urlopen = urllib.request.urlopen

    def urlopen(url, *args, **kwargs):
        name = str(url).rsplit("/", 1)[-1].removesuffix(".zip")
        if "/demo-packages/" in str(url) and name in packages:
            return _Answer(packages[name])
        return real_urlopen(url, *args, **kwargs)

    urllib.request.urlopen = urlopen  # the analyzer downloads code with urllib, which moto doesn't serve

    def lambda_client(region):
        client = boto3.client("lambda", region_name=region or REGION)

        def get_function(FunctionName, **params):
            answer = client.get_function(FunctionName=FunctionName, **params)
            answer.pop("ResponseMetadata", None)
            name = answer["Configuration"]["FunctionName"]
            answer["Code"]["Location"] = (f"https://awslambda-{region}-tasks.s3.{region}.amazonaws.com/demo-packages/"
                                          f"{name}.zip")
            if name == "report-api":
                answer["Concurrency"] = {"ReservedConcurrentExecutions": 10}
                answer["Configuration"]["LoggingConfig"] = {"LogFormat": "JSON", "ApplicationLogLevel": "INFO",
                                                            "SystemLogLevel": "INFO", "LogGroup": "/aws/lambda/report-api"}
            return answer

        def get_policy(FunctionName, **params):
            if FunctionName == "report-api":
                return {"Policy": json.dumps(report_policy), "RevisionId": "1"}
            answer = client.get_policy(FunctionName=FunctionName, **params)
            answer.pop("ResponseMetadata", None)
            return answer

        def provisioned(FunctionName, **params):
            if FunctionName != "churn-scoring":
                return {"ProvisionedConcurrencyConfigs": []}
            return {"ProvisionedConcurrencyConfigs": [{
                "FunctionArn": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:churn-scoring:live",
                "RequestedProvisionedConcurrentExecutions": 4, "AllocatedProvisionedConcurrentExecutions": 4,
                "AvailableProvisionedConcurrentExecutions": 4, "Status": "READY"}]}

        handlers = {
            "get_function": get_function,
            "get_policy": get_policy,
            "list_provisioned_concurrency_configs": provisioned,
            "get_account_settings": lambda: {
                "AccountLimit": {"TotalCodeSize": 75 * 1024**3, "CodeSizeUnzipped": 250 * 1024**2,
                                 "CodeSizeZipped": 50 * 1024**2, "ConcurrentExecutions": 1000,
                                 "UnreservedConcurrentExecutions": 990 if region == REGION else 1000},
                "AccountUsage": {"TotalCodeSize": int((3.2 if region == REGION else 0.1) * 1024**3),
                                 "FunctionCount": 5 if region == REGION else 1}},
            "get_runtime_management_config": lambda FunctionName, **_: {"UpdateRuntimeOn": "Auto"},
        }
        return _Patched(client, handlers)

    def logs_client(region):
        client = boto3.client("logs", region_name=region or REGION)

        def describe_log_groups(**params):
            answer = client.describe_log_groups(**params)
            answer.pop("ResponseMetadata", None)
            for group in answer.get("logGroups", []):
                group["storedBytes"] = stored.get(group["logGroupName"], group.get("storedBytes", 0))
            return answer

        return _Patched(client, {"describe_log_groups": describe_log_groups})

    return {"clients": {"lambda": lambda_client, "logs": logs_client}}


class _Patched:
    """A moto client with a few operations answered by functions instead (each checked against the service model)."""

    def __init__(self, client, handlers: dict):
        from botocore import xform_name
        from botocore.validate import ParamValidator

        model = client.meta.service_model
        operations = {xform_name(op): model.operation_model(op) for op in model.operation_names}

        def checked(name, handler):
            def call(**params):
                for shape, value in ((operations[name].input_shape, params),):
                    report = ParamValidator().validate(value, shape)
                    if report.has_errors():
                        raise AssertionError(report.generate_report())
                answer = handler(**params)
                report = ParamValidator().validate(answer, operations[name].output_shape)
                if report.has_errors():
                    raise AssertionError(f"demo {name} response doesn't match the service model:\n"
                                         f"{report.generate_report()}")
                return answer
            return call

        self._client = client
        self._handlers = {name: checked(name, handler) for name, handler in handlers.items()}

    def __getattr__(self, name: str):
        return self._handlers.get(name) or getattr(self._client, name)


SEEDERS = {"s3": seed_s3, "dynamodb": seed_dynamodb, "bedrock_kb": seed_bedrock_kb, "bedrock_chat": seed_bedrock_kb,
           "sagemaker_env": seed_sagemaker_env, "opensearch": seed_opensearch,
           "lambda_functions": seed_lambda_functions}


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

    package = ROOT / "src" / "aws_analyzer"
    if args.service.startswith("_") or not (package / f"{args.service}.py").exists():
        names = ", ".join(sorted(p.stem for p in package.glob("*.py") if not p.stem.startswith("_")))
        parser.error(f"no src/aws_analyzer/{args.service}.py (have: {names})")

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
        mod = importlib.import_module(f"aws_analyzer.{args.service}")
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
