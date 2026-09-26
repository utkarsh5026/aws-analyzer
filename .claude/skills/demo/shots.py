"""Regenerate the guides' screenshots: every figure in docs/*.html, light and dark, as the tool renders it.

    .venv/bin/python .claude/skills/demo/shots.py                     # every figure
    .venv/bin/python .claude/skills/demo/shots.py summary what-if     # just these (image names, no -light/-dark)
    .venv/bin/python .claude/skills/demo/shots.py --list              # the figures and the command behind each
    .venv/bin/python .claude/skills/demo/shots.py find --html out/    # also keep the pages it screenshots

Each figure runs its command against a scene in moto, renders the notebook HTML of the reports, screenshots it
984 CSS px wide at 1.5x (1476 px, like the rest of the images) in headless Chrome, trims the empty space below,
and writes docs/images/<name>-{light,dark}.webp. Then it sets that <img>'s height= in the guide.

Scenes:
  s3          acme-ml-data, the bucket the S3 guide is written around, plus acme-logs, a SageMaker bucket and
              acme-eu-exports. moto holds the files a figure opens (the parquet, model.tar.gz, Word, PowerPoint
              and PDF files, the deleted ones); the listing adds the ~15,000 objects and terabytes around them,
              which moto couldn't hold, and CloudWatch's storage metrics come from a small fake.
  dynamodb    acme-app (customers, their orders and support tickets in one table) and three other tables.
              DescribeTable reports item counts and sizes at production scale; moto holds ~1,700 real items.
  bedrock_kb  demo.py's fake Bedrock and support-docs knowledge base, as /demo uses it.

Needs Pillow (pip install pillow) and Chrome: $CHROME, or the headless shell Playwright installs
(npx playwright install chromium-headless-shell).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import io
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT / "analyzers"), str(HERE)]

IMAGES = ROOT / "docs" / "images"
GUIDES = {"s3": "s3.html", "dynamodb": "dynamodb.html", "bedrock_kb": "bedrock_kb.html"}
WIDTH, SCALE, MARGIN = 984, 1.5, 12  # CSS px wide, device pixels per CSS px, page margin in CSS px
REGION, ACCOUNT = "us-east-1", "123456789012"
NOW = datetime.now(timezone.utc)
KB, MB, GB = 1024, 1024 ** 2, 1024 ** 3


@dataclass
class Figure:
    name: str  # docs/images/<name>-light.webp
    service: str
    code: str  # what the figure shows: every report it renders ends up in the picture
    setup: str = ""  # runs first, shows nothing (e.g. use())
    crop: int | None = None  # keep only the top this many CSS px ("The top of ui.summary(...)")


LAKE = "s3://acme-ml-data"
FIGURES = [
    Figure("overview", "s3", "ui.overview()"),
    Figure("bucket-info", "s3", 'ui.bucket_info("acme-ml-data")'),
    Figure("policy", "s3", 'ui.policy("acme-ml-data")'),
    Figure("tree", "s3", f'ui.tree("{LAKE}/curated/", depth=3)'),
    Figure("find", "s3", f'ui.find("{LAKE}/training/", pattern="*.tar.gz", min_size="4GB", modified_after="90d")'),
    Figure("summary", "s3", f'ui.summary("{LAKE}/")', crop=1300),
    Figure("preview-parquet", "s3", f'ui.preview("{LAKE}/curated/features/churn/train.parquet", n=8)'),
    Figure("preview-model", "s3", f'ui.preview("{LAKE}/training/churn-xgb-2025-09-01-1030/output/model.tar.gz")'),
    Figure("preview-safetensors", "s3", f'ui.preview("{LAKE}/models/tiny-llm/model.safetensors")'),
    Figure("preview-docx", "s3", f'ui.preview("{LAKE}/docs/model-card-churn-xgb.docx")'),
    Figure("preview-pptx", "s3", f'ui.preview("{LAKE}/docs/q3-ml-platform-review.pptx")'),
    Figure("document-pdf", "s3", f'ui.document("{LAKE}/docs/data-retention-policy.pdf")'),
    Figure("what-if", "s3", f'ui.what_if("{LAKE}/training/", move_after={{30: "STANDARD_IA", 180: "GLACIER"}})'),
    Figure("duplicates", "s3", f'ui.duplicates("{LAKE}/public-samples/")'),
    Figure("deleted", "s3", f'ui.deleted("{LAKE}/")'),
    Figure("dynamodb-tables", "dynamodb", "ui.tables()"),
    Figure("dynamodb-table-info", "dynamodb", 'ui.table_info("acme-app")'),
    Figure("dynamodb-query", "dynamodb", 'ui.query("acme-app", "CUSTOMER#1042", sort=("begins_with", "ORDER#"), n=8, '
                                         'attributes=["status", "created_at", "total", "currency", "coupon"])'),
    Figure("dynamodb-get", "dynamodb", 'ui.get("acme-app", "CUSTOMER#1042", "ORDER#2026-08-14#7731")'),
    Figure("dynamodb-scan-filter", "dynamodb", 'ui.scan("acme-app", 10, where={"status": "failed", "total": (">", 300)}, '
                                               'attributes=["status", "created_at", "total", "failure_reason"])'),
    Figure("dynamodb-schema", "dynamodb", 'ui.schema("acme-app")'),
    Figure("dynamodb-value-counts", "dynamodb", 'ui.value_counts("acme-app", "status")'),
    Figure("bedrock-kbs", "bedrock_kb", "ui.kbs()"),
    Figure("bedrock-kb-info", "bedrock_kb", 'ui.kb_info("support-docs")'),
    Figure("bedrock-syncs", "bedrock_kb", 'ui.syncs("support-docs")'),
    Figure("bedrock-documents", "bedrock_kb", 'ui.documents("support-docs")'),
    Figure("bedrock-unsynced", "bedrock_kb", 'ui.unsynced("support-docs")'),
    Figure("bedrock-search", "bedrock_kb", 'ui.search("How long do refunds take?")', setup='ui.use("support-docs")'),
    Figure("bedrock-ask", "bedrock_kb", 'ui.ask("How long do refunds take?")', setup='ui.use("support-docs")'),
    Figure("bedrock-ask-converse", "bedrock_kb", 'ui.ask("How long do refunds take?", engine="converse", model="sonnet")',
           setup='ui.use("support-docs")'),
    Figure("bedrock-models", "bedrock_kb", "ui.models()"),
    Figure("bedrock-compare", "bedrock_kb", 'ui.compare("what does error E1234 mean?", n=(2, 5))',
           setup='ui.use("support-docs")'),
    Figure("bedrock-evaluate", "bedrock_kb", "ui.evaluate(cases)", setup='ui.use("support-docs"); cases = [\n'
           '    ("How long do refunds take?", "refund-policy.pdf"),\n'
           '    ("How do I reset my password?", "account-faq"),\n'
           '    ("Can EU customers return an order?", "eu-returns.pdf"),\n'
           '    ("What does error E1234 mean?", "payment-errors"),\n'
           '    ("Can I return a faulty laptop?", "warranty.pdf"),\n'
           '    ("When do holiday orders ship?", "holiday-shipping"),\n]'),
]


# ----------------------------------------------------------------------------- file builders


def _zip(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _tar_gz(files: dict[str, bytes], when: datetime) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mtime = len(data), when.timestamp()
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _pdf(pages: list[list[str]], title: str, author: str) -> bytes:
    """A PDF with a few lines of text per page and a title and author."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % (4 + 2 * i) for i in range(len(pages))),
                                                             len(pages)),
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for i, lines in enumerate(pages):
        text = " ".join(f"({line}) Tj T*" for line in lines)
        stream = f"BT /F1 11 Tf 14 TL 50 740 Td {text} ET".encode()
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
                       b"/Resources << /Font << /F1 3 0 R >> >> >>" % (5 + 2 * i))
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
    objects.append(b"<< /Title (%s) /Author (%s) >>" % (title.encode(), author.encode()))
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, len(objects), xref)
    return bytes(out)


W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
P_NS = ('xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
REL_NS = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _core(title: str, author: str) -> str:
    return ('<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            f'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{title}</dc:title><dc:creator>{author}</dc:creator>'
            '</cp:coreProperties>')


def _docx(parts: list[tuple[str, str | list[list[str]]]], title: str, author: str) -> bytes:
    """parts: (style, text) paragraphs (style '' = body, 'Bullet' = a numbered list item) or ('table', rows)."""
    def para(style: str, text: str) -> str:
        props = ('<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>' if style == "Bullet"
                 else f'<w:pStyle w:val="{style}"/>' if style else "")
        return f'<w:p><w:pPr>{props}</w:pPr><w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'

    body = "".join('<w:tbl>' + "".join('<w:tr>' + "".join(f'<w:tc>{para("", cell)}</w:tc>' for cell in row) + '</w:tr>'
                                       for row in content) + '</w:tbl>' if style == "table" else para(style, content)
                   for style, content in parts)
    styles = "".join(f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/></w:style>'
                     for sid, name in (("Title", "Title"), ("Heading1", "heading 1"), ("Heading2", "heading 2")))
    return _zip({"[Content_Types].xml": "<Types/>",
                 "word/document.xml": f"<w:document {W_NS}><w:body>{body}</w:body></w:document>",
                 "word/styles.xml": f"<w:styles {W_NS}>{styles}</w:styles>", "docProps/core.xml": _core(title, author)})


def _pptx(slides: list[tuple[str, str, str]], title: str, author: str) -> bytes:
    """slides: (title, body, speaker notes)."""
    def shape(text: str, placeholder: str | None = None) -> str:
        nv = f'<p:nvPr><p:ph type="{placeholder}"/></p:nvPr>' if placeholder else "<p:nvPr/>"
        paragraphs = "".join(f"<a:p><a:r><a:t>{line}</a:t></a:r></a:p>" for line in text.split("\n"))
        return f'<p:sp><p:nvSpPr><p:cNvPr id="1" name="s"/><p:cNvSpPr/>{nv}</p:nvSpPr><p:txBody>{paragraphs}</p:txBody></p:sp>'

    files: dict[str, str | bytes] = {"[Content_Types].xml": "<Types/>", "docProps/core.xml": _core(title, author)}
    for i, (heading, body, notes) in enumerate(slides, 1):
        files[f"ppt/slides/slide{i}.xml"] = (f"<p:sld {P_NS}><p:cSld><p:spTree>{shape(heading, 'title')}{shape(body)}"
                                             "</p:spTree></p:cSld></p:sld>")
        if notes:
            files[f"ppt/notesSlides/notesSlide{i}.xml"] = (f"<p:notes {P_NS}><p:cSld><p:spTree>{shape(str(i), 'sldNum')}"
                                                           f"{shape(notes, 'body')}</p:spTree></p:cSld></p:notes>")
            files[f"ppt/slides/_rels/slide{i}.xml.rels"] = (
                f'<Relationships {REL_NS}><Relationship Id="rId2" Target="../notesSlides/notesSlide{i}.xml" '
                f'Type="{REL}/notesSlide"/></Relationships>')
    files["ppt/presentation.xml"] = (f"<p:presentation {P_NS}><p:sldIdLst>" + "".join(
        f'<p:sldId id="{255 + i}" r:id="rId{i}"/>' for i in range(1, len(slides) + 1)) + "</p:sldIdLst></p:presentation>")
    files["ppt/_rels/presentation.xml.rels"] = f"<Relationships {REL_NS}>" + "".join(
        f'<Relationship Id="rId{i}" Target="slides/slide{i}.xml" Type="{REL}/slide"/>'
        for i in range(1, len(slides) + 1)) + "</Relationships>"
    return _zip(files)


def _safetensors_header() -> tuple[bytes, int]:
    """The header of a small decoder-only model and the file's full size. Only the header is read, so the weights
    can be left out."""
    tensors, offset = {}, 0

    def add(name: str, shape: list[int], dtype: str = "BF16") -> None:
        nonlocal offset
        size = (2 if dtype == "BF16" else 4) * math.prod(shape)
        tensors[name] = {"dtype": dtype, "shape": shape, "data_offsets": [offset, offset + size]}
        offset += size

    add("model.embed_tokens.weight", [32000, 512])
    for layer in range(4):
        for proj in ("q_proj", "k_proj", "v_proj", "o_proj"):
            add(f"model.layers.{layer}.self_attn.{proj}.weight", [512, 512])
        add(f"model.layers.{layer}.mlp.gate_proj.weight", [1376, 512])
        add(f"model.layers.{layer}.mlp.up_proj.weight", [1376, 512])
        add(f"model.layers.{layer}.mlp.down_proj.weight", [512, 1376])
        add(f"model.layers.{layer}.input_layernorm.weight", [512], "F32")
    add("model.norm.weight", [512], "F32")
    header = json.dumps({"__metadata__": {"format": "pt"}, **tensors}).encode()
    return len(header).to_bytes(8, "little") + header, 8 + len(header) + offset


# ----------------------------------------------------------------------------- S3 scene


class S3Overlay:
    """moto's S3 client, plus objects that are only in listings. moto holds the files a figure opens; the listing
    adds the terabytes around them (keys, sizes, dates, storage classes, ETags) that moto couldn't hold."""

    def __init__(self, client, listed: dict[str, list[dict]], sizes: dict[tuple[str, str], int]):
        self._client, self._listed, self._sizes = client, listed, sizes

    def __getattr__(self, name):
        return getattr(self._client, name)

    def head_object(self, **params):
        response = self._client.head_object(**params)
        response["ContentLength"] = self._sizes.get((params["Bucket"], params["Key"]), response["ContentLength"])
        return response

    def get_bucket_policy_status(self, **params):
        """What AWS says of acme-ml-data's policy, which lets anyone read public-samples/ (moto says private)."""
        response = self._client.get_bucket_policy_status(**params)
        response["PolicyStatus"]["IsPublic"] = params["Bucket"] == "acme-ml-data"
        return response

    def list_objects_v2(self, *, Bucket, Prefix="", Delimiter=None, ContinuationToken=None, StartAfter=None,
                        MaxKeys=1000, **_):
        real = [dict(o, Size=self._sizes.get((Bucket, o["Key"]), o["Size"]))
                for page in self._client.get_paginator("list_objects_v2").paginate(Bucket=Bucket, Prefix=Prefix)
                for o in page.get("Contents", [])]
        after = ContinuationToken or StartAfter or ""
        objects = sorted((o for o in real + self._listed.get(Bucket, []) if o["Key"].startswith(Prefix)
                          and o["Key"] > after), key=lambda o: o["Key"])
        if Delimiter:  # one page: enough for the folders a figure lists
            folders: dict[str, None] = {}
            files = []
            for o in objects:
                rest = o["Key"][len(Prefix):]
                if Delimiter in rest:
                    folders.setdefault(Prefix + rest.split(Delimiter, 1)[0] + Delimiter)
                else:
                    files.append(o)
            return {"Contents": files, "CommonPrefixes": [{"Prefix": p} for p in folders], "IsTruncated": False,
                    "KeyCount": len(files) + len(folders)}
        page, more = objects[:MaxKeys], len(objects) > MaxKeys
        return {"Contents": page, "IsTruncated": more, "KeyCount": len(page),
                **({"NextContinuationToken": page[-1]["Key"]} if more else {})}

    def get_paginator(self, name):
        if name != "list_objects_v2":
            return self._client.get_paginator(name)
        overlay = self

        class Paginator:
            def paginate(self, **params):
                token = None
                while True:
                    page = overlay.list_objects_v2(**params, **({"ContinuationToken": token} if token else {}))
                    yield page
                    if not page["IsTruncated"]:
                        return
                    token = page["NextContinuationToken"]

        return Paginator()


class S3Metrics:
    """CloudWatch's daily S3 storage metrics for the scene's buckets (moto derives its own from what it holds)."""

    def __init__(self, sizes: dict[str, dict[str, float]], counts: dict[str, int]):
        self._sizes, self._counts = sizes, counts
        self._as_of = (NOW - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    def get_paginator(self, name):
        metrics = self

        class Paginator:
            def paginate(self, Namespace, Dimensions, **_):
                bucket = Dimensions[0]["Value"]
                dims = [{"Name": "BucketName", "Value": bucket}]
                found = [{"Namespace": Namespace, "MetricName": "BucketSizeBytes",
                          "Dimensions": dims + [{"Name": "StorageType", "Value": kind}]}
                         for kind in metrics._sizes.get(bucket, {})]
                if bucket in metrics._counts:
                    found.append({"Namespace": Namespace, "MetricName": "NumberOfObjects",
                                  "Dimensions": dims + [{"Name": "StorageType", "Value": "AllStorageTypes"}]})
                yield {"Metrics": found}

        return Paginator()

    def get_metric_data(self, MetricDataQueries, **_):
        results = []
        for query in MetricDataQueries:
            metric = query["MetricStat"]["Metric"]
            dims = {d["Name"]: d["Value"] for d in metric["Dimensions"]}
            value = (self._counts[dims["BucketName"]] if metric["MetricName"] == "NumberOfObjects"
                     else self._sizes[dims["BucketName"]][dims["StorageType"]])
            results.append({"Id": query["Id"], "Values": [float(value)], "Timestamps": [self._as_of]})
        return {"MetricDataResults": results}


class S3Session:
    """A boto3 session whose S3 clients are overlays and whose CloudWatch is the fake above."""

    def __init__(self, listed, sizes, cloudwatch):
        import boto3

        self._session, self._listed, self._sizes, self._cloudwatch = boto3.Session(region_name=REGION), listed, sizes, \
            cloudwatch
        self.region_name = REGION

    def client(self, service, region_name=None, **kwargs):
        if service == "cloudwatch":
            return self._cloudwatch
        client = self._session.client(service, region_name=region_name or REGION, **kwargs)
        return S3Overlay(client, self._listed, self._sizes) if service == "s3" else client


def _etag(key: str) -> str:
    return '"' + hashlib.md5(key.encode()).hexdigest() + '"'


def _listed(key: str, size: float, age_days: float, storage: str = "STANDARD", etag: str | None = None) -> dict:
    return {"Key": key, "Size": int(size), "LastModified": NOW - timedelta(days=age_days), "StorageClass": storage,
            "ETag": etag or _etag(key)}


def _lake_listing(rng: random.Random) -> list[dict]:
    """acme-ml-data's objects that are only listed: ~15,000 keys and 1.2 TB."""
    out = []
    jobs = [("churn-xgb", 420), ("ltv-lgbm", 350), ("fraud-gnn", 300), ("reco-two-tower", 240), ("forecast-tft", 200),
            ("churn-xgb", 150), ("llm-finetune", 120), ("fraud-gnn", 75), ("reco-two-tower", 45), ("llm-finetune", 20),
            ("ltv-lgbm", 12), ("forecast-tft", 4)]
    for name, age in jobs:
        started = NOW - timedelta(days=age)
        job = f"training/{name}-{started:%Y-%m-%d-%H%M}"
        out.append(_listed(f"{job}/output/model.tar.gz", rng.uniform(4.1, 8.7) * GB if age < 100
                           else rng.uniform(0.6, 3.5) * GB, age))
        out.append(_listed(f"{job}/output/metrics.json", rng.uniform(1.5, 3) * KB, age))
        for epoch in range(1, rng.randint(42, 58)):
            out.append(_listed(f"{job}/checkpoints/epoch-{epoch:03d}.pt", rng.uniform(0.9, 2.3) * GB,
                               age - epoch * 0.05))
    for feature_set in ("churn", "ltv", "fraud"):
        for split in ("train", "test", "validation"):
            if (feature_set, split) != ("churn", "train"):  # that one is a real file in moto
                out.append(_listed(f"curated/features/{feature_set}/{split}.parquet", rng.uniform(0.2, 1.4) * GB, 30))
    for part in range(150):
        out.append(_listed(f"curated/tables/orders/part-{part:05d}.parquet", rng.uniform(650, 900) * MB, 3 + part % 40))
    for part in range(40):
        out.append(_listed(f"curated/tables/customers/part-{part:05d}.parquet", rng.uniform(80, 120) * MB, 6))
    for i in range(500):
        year = 2022 + i % 3
        out.append(_listed(f"archive/{year}/events-{year}-{1 + i % 12:02d}-{i:03d}.csv.gz", rng.uniform(220, 310) * MB,
                           (NOW.year - year) * 365 + 30, "GLACIER"))
    for day in range(360):
        when = NOW - timedelta(days=day)
        for part in range(30):
            out.append(_listed(f"raw/events/dt={when:%Y-%m-%d}/part-{part:04d}.json.gz", rng.uniform(120, 290) * KB,
                               day + 0.2))
    for i in range(3000):
        day = 30 + i // 8
        out.append(_listed(f"logs/app/{NOW - timedelta(days=day):%Y-%m-%d}/worker-{i % 8}.log", rng.uniform(20, 42) * KB,
                           day, "STANDARD_IA"))
    sample = '"' + hashlib.md5(b"churn-sample.csv").hexdigest() + '"'
    for i in range(8):  # the same file eight times: same size, same ETag
        out.append(_listed(f"public-samples/sample-{i}/churn-sample.csv", 48 * MB, 200 - i, etag=sample))
    for i in range(30):
        out.append(_listed(f"tmp/spark-staging/part-{i:05d}.bin", 0, 2))
    return out


def seed_s3_docs() -> dict:
    """The acme scene: returns the S3Analyzer's session and client."""
    import boto3
    import pyarrow as pa
    import pyarrow.parquet as pq

    import demo

    rng = random.Random(11)
    s3 = boto3.client("s3", region_name=REGION)
    lake = "acme-ml-data"
    s3.create_bucket(Bucket=lake)
    s3.put_bucket_versioning(Bucket=lake, VersioningConfiguration={"Status": "Enabled"})
    s3.put_bucket_encryption(Bucket=lake, ServerSideEncryptionConfiguration={"Rules": [{
        "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "aws:kms",
                                               "KMSMasterKeyID": f"arn:aws:kms:{REGION}:{ACCOUNT}:alias/acme-data"},
        "BucketKeyEnabled": True}]})
    s3.put_bucket_tagging(Bucket=lake, Tagging={"TagSet": [{"Key": "team", "Value": "ml-platform"},
                                                           {"Key": "cost-center", "Value": "ml-42"}]})
    s3.put_bucket_policy(Bucket=lake, Policy=json.dumps({"Version": "2012-10-17", "Statement": [
        {"Sid": "PublicSamples", "Effect": "Allow", "Principal": "*", "Action": "s3:GetObject",
         "Resource": f"arn:aws:s3:::{lake}/public-samples/*"},
        {"Sid": "PartnerUpload", "Effect": "Allow", "Principal": {"AWS": "arn:aws:iam::999988887777:role/DataLoader"},
         "Action": ["s3:PutObject", "s3:ListBucket"], "Resource": [f"arn:aws:s3:::{lake}", f"arn:aws:s3:::{lake}/raw/*"]},
        {"Sid": "TrainingFromVpc", "Effect": "Allow", "Principal": "*", "Action": ["s3:GetObject", "s3:PutObject"],
         "Resource": f"arn:aws:s3:::{lake}/training/*",
         "Condition": {"StringEquals": {"aws:SourceVpce": "vpce-0a1b2c3d"}}}]}))
    s3.put_bucket_lifecycle_configuration(Bucket=lake, LifecycleConfiguration={"Rules": [
        {"ID": "logs-to-ia", "Status": "Enabled", "Filter": {"Prefix": "logs/"},
         "Transitions": [{"Days": 30, "StorageClass": "STANDARD_IA"}]},
        {"ID": "archive", "Status": "Enabled", "Filter": {"Prefix": "archive/"},
         "Transitions": [{"Days": 0, "StorageClass": "GLACIER"}]}]})

    def put(key: str, body: bytes, age_days: float = 0, **kwargs) -> None:
        s3.put_object(Bucket=lake, Key=key, Body=body, **kwargs)
        if age_days:
            demo._backdate(lake, key, demo.NOW - timedelta(days=age_days))

    # The files the figures open.
    rows = 2000
    plans = ["basic", "pro", "team", "enterprise"]
    table = pa.table({"customer_id": [f"C{100000 + i}" for i in range(rows)],
                      "age": [rng.randint(19, 71) for _ in range(rows)],
                      "plan": [rng.choice(plans) for _ in range(rows)],
                      "monthly_spend": [round(rng.uniform(9, 480), 2) for _ in range(rows)],
                      "support_tickets": [rng.choices(range(8), [40, 25, 14, 9, 6, 3, 2, 1])[0] for _ in range(rows)],
                      "logins_per_week": [round(rng.uniform(0, 21), 1) for _ in range(rows)],
                      "churned": [rng.random() < 0.18 for _ in range(rows)]})
    buffer = io.BytesIO()
    pq.write_table(table, buffer, row_group_size=500, compression="zstd")
    put("curated/features/churn/train.parquet", buffer.getvalue(), 30)

    job_time = datetime(2025, 9, 1, 10, 30, tzinfo=timezone.utc)
    put("training/churn-xgb-2025-09-01-1030/output/model.tar.gz", _tar_gz({
        "model.pth": random.Random(3).randbytes(300_000),
        "code/inference.py": b"import torch\n\ndef model_fn(model_dir):\n    return torch.load(f'{model_dir}/model.pth')\n",
        "code/requirements.txt": b"xgboost==2.1.1\n", "config.json": b'{"max_depth": 6}'}, job_time),
        (NOW - job_time).days)
    header, size = _safetensors_header()
    put("models/tiny-llm/model.safetensors", header, 60)
    put("models/tiny-llm/config.json", json.dumps({"hidden_size": 512, "num_hidden_layers": 4,
                                                   "vocab_size": 32000}).encode(), 60)
    put("notebooks/churn-data-checks.ipynb", json.dumps({"nbformat": 4, "metadata": {"kernelspec": {"name": "python3"}},
                                                         "cells": []}).encode(), 12)
    put("docs/model-card-churn-xgb.docx", _docx([
        ("Title", "Model card: churn-xgb"),
        ("", "Predicts which customers are likely to cancel in the next 30 days, so the success team can reach out "
             "first."),
        ("Heading1", "Training data"),
        ("", "2,000 labelled customers from s3://acme-ml-data/curated/features/churn/, January to August 2025."),
        ("Bullet", "18% of customers churned; the classes were rebalanced with sample weights."),
        ("Bullet", "Features: age, plan, monthly spend, support tickets, logins per week."),
        ("Heading1", "Evaluation"),
        ("table", [["Metric", "Validation", "Holdout"], ["AUC", "0.91", "0.89"], ["Precision@10%", "0.62", "0.58"],
                   ["Recall@10%", "0.47", "0.44"]]),
        ("Heading1", "Limitations"),
        ("", "Customers on the enterprise plan are under-represented (4% of the data); treat their scores with care."),
        ("Heading2", "Owners"),
        ("", "ML platform team; retrained monthly by the churn-xgb pipeline."),
    ], "Model card: churn-xgb", "ML platform team"), 25)
    put("docs/q3-ml-platform-review.pptx", _pptx([
        ("Q3 ML platform review", "Training, serving and cost, July to September", ""),
        ("What shipped", "churn-xgb v3 in production\nFeature store for ltv and fraud\nSpot training for every job",
         "Mention the two-week delay on the feature store."),
        ("Cost", "Training: $41k (-18%)\nStorage: $2.3k/month\nServing: $12k", "Storage is mostly old checkpoints."),
        ("Next quarter", "Move checkpoints to cheaper storage\nRetire the v1 fraud model", ""),
    ], "Q3 ML platform review", "ML platform team"), 18)
    put("docs/data-retention-policy.pdf", _pdf([
        ["Data retention policy", "", "Raw events are kept for 13 months, then deleted.",
         "Curated tables are kept while a model or report uses them.",
         "Training checkpoints move to cheaper storage after 30 days."],
        ["Exceptions", "", "Data under legal hold is kept until the hold is lifted.",
         "Ask the data platform team before deleting anything in archive/."],
    ], "Data retention policy", "Data platform team"), 90)
    for key, body in (("curated/exports/customers-eu.csv", 16_000), ("curated/exports/old-features.parquet", 24_000),
                      ("curated/exports/q3-report.csv", 8_000)):  # deleted since: versioning keeps them
        put(key, random.Random(key).randbytes(body), 40)
        s3.delete_object(Bucket=lake, Key=key)
    _backdate_markers(lake, rng)

    others = {"acme-logs": REGION, f"sagemaker-{REGION}-{ACCOUNT}": REGION, "acme-eu-exports": "eu-west-1"}
    for name, region in others.items():
        client = boto3.client("s3", region_name=region)
        client.create_bucket(Bucket=name, **({} if region == REGION else
                                             {"CreateBucketConfiguration": {"LocationConstraint": region}}))
        if name != f"sagemaker-{REGION}-{ACCOUNT}":
            client.put_bucket_encryption(Bucket=name, ServerSideEncryptionConfiguration={"Rules": [
                {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]})
    s3.put_public_access_block(Bucket="acme-logs", PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
    s3.put_bucket_lifecycle_configuration(Bucket="acme-logs", LifecycleConfiguration={"Rules": [
        {"ID": "expire-after-a-year", "Status": "Enabled", "Filter": {"Prefix": ""}, "Expiration": {"Days": 365},
         "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7}}]})

    tb = 1024 * GB
    cloudwatch = S3Metrics(
        sizes={lake: {"StandardStorage": 1.31 * tb, "StandardIAStorage": 669.6 * GB, "GlacierStorage": 132.4 * GB,
                      "StandardIASizeOverhead": 284.2 * MB, "GlacierObjectOverhead": 15.6 * MB,
                      "GlacierS3ObjectOverhead": 3.9 * MB},
               "acme-logs": {"StandardStorage": 1.52 * tb, "StandardIAStorage": 0.41 * tb},
               f"sagemaker-{REGION}-{ACCOUNT}": {"StandardStorage": 212 * GB},
               "acme-eu-exports": {"StandardStorage": 38 * GB}},
        counts={lake: 17_342, "acme-logs": 412_000, f"sagemaker-{REGION}-{ACCOUNT}": 1_840, "acme-eu-exports": 96})
    listed = {lake: _lake_listing(rng)}
    sizes = {(lake, "models/tiny-llm/model.safetensors"): size}
    return {"session": S3Session(listed, sizes, cloudwatch), "client": S3Overlay(s3, listed, sizes)}


def _backdate_markers(bucket: str, rng: random.Random) -> None:
    """Put the deletes of the scene's deleted files a few days back, after their last upload."""
    from moto.core.models import DEFAULT_ACCOUNT_ID
    from moto.s3.models import s3_backends

    import demo

    keys = s3_backends[DEFAULT_ACCOUNT_ID]["aws"].buckets[bucket].keys
    for days, key in enumerate(("curated/exports/customers-eu.csv", "curated/exports/old-features.parquet",
                                "curated/exports/q3-report.csv"), 2):
        for version in keys.getlist(key):
            if type(version).__name__ == "FakeDeleteMarker":
                version.last_modified = demo.NOW - timedelta(days=days, hours=rng.randint(1, 20))


# ----------------------------------------------------------------------------- DynamoDB scene


class DescribeOverlay:
    """moto's DynamoDB client, with DescribeTable reporting a production table's item counts, sizes and age."""

    def __init__(self, client, stats: dict[str, dict]):
        self._client, self._stats = client, stats

    def __getattr__(self, name):
        return getattr(self._client, name)

    def describe_table(self, **params):
        response = self._client.describe_table(**params)
        table, stats = response["Table"], self._stats.get(params["TableName"], {})
        table["ItemCount"], table["TableSizeBytes"] = stats.get("items", 0), stats.get("size", 0)
        table["CreationDateTime"] = stats.get("created", table.get("CreationDateTime"))
        for index in table.get("GlobalSecondaryIndexes", []):
            index["ItemCount"], index["IndexSizeBytes"] = stats.get(index["IndexName"], (0, 0))
        return response

    def _priced(self, response: dict, table: str) -> dict:
        """The read units of what a scan or query read (moto says 1 per call): items of the table's average size,
        eventually consistent, so half a unit per 4 KB."""
        stats = self._stats.get(table, {})
        average = stats.get("size", 0) / max(stats.get("items", 0), 1) or 400
        if "ConsumedCapacity" in response:
            response["ConsumedCapacity"]["CapacityUnits"] = max(
                0.5, math.ceil(response.get("ScannedCount", 0) * average / 4096) / 2)
        return response

    def scan(self, **params):
        return self._priced(self._client.scan(**params), params["TableName"])

    def query(self, **params):
        return self._priced(self._client.query(**params), params["TableName"])


def seed_dynamodb_docs() -> dict:
    """The acme scene: returns the DynamoDBAnalyzer's client."""
    from decimal import Decimal

    import boto3
    from boto3.dynamodb.types import TypeSerializer

    rng = random.Random(5)
    ddb = boto3.client("dynamodb", region_name=REGION)
    serialize = TypeSerializer().serialize

    def fix(value):
        if isinstance(value, float):
            return Decimal(str(value))
        if isinstance(value, dict):
            return {k: fix(v) for k, v in value.items()}
        if isinstance(value, list):
            return [fix(v) for v in value]
        return value

    def put(table: str, item: dict) -> None:
        ddb.put_item(TableName=table, Item={k: serialize(fix(v)) for k, v in item.items()})

    ddb.create_table(
        TableName="acme-app", BillingMode="PAY_PER_REQUEST", DeletionProtectionEnabled=True,
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": n, "AttributeType": "S"} for n in ("pk", "sk", "status", "created_at",
                                                                                   "email")],
        GlobalSecondaryIndexes=[
            {"IndexName": "by-status", "KeySchema": [{"AttributeName": "status", "KeyType": "HASH"},
                                                     {"AttributeName": "created_at", "KeyType": "RANGE"}],
             "Projection": {"ProjectionType": "INCLUDE", "NonKeyAttributes": ["total", "currency"]}},
            {"IndexName": "by-email", "KeySchema": [{"AttributeName": "email", "KeyType": "HASH"}],
             "Projection": {"ProjectionType": "KEYS_ONLY"}}],
        StreamSpecification={"StreamEnabled": True, "StreamViewType": "NEW_AND_OLD_IMAGES"},
        Tags=[{"Key": "team", "Value": "growth"}, {"Key": "env", "Value": "prod"}])
    ddb.update_time_to_live(TableName="acme-app", TimeToLiveSpecification={"Enabled": True,
                                                                           "AttributeName": "expires_at"})
    cities = [("Austin", "US", "78701"), ("Seattle", "US", "98101"), ("Boston", "US", "02108"),
              ("Berlin", "DE", "10115"), ("Lyon", "FR", "69001"), ("Pune", "IN", "411001"), ("Leeds", "GB", "LS1 4AP")]
    statuses = ["delivered", "shipped", "paid", "failed", "refunded", "cancelled"]
    reasons = ["card declined", "address not deliverable", "fraud check failed", "payment timed out"]
    coupons = ["", "WELCOME10", "SPRING15", "VIP20", ""]
    customers = [1042] + rng.sample(range(1000, 1400), 279)
    for c in customers:
        city, country, zip_code = rng.choice(cities)
        pk = f"CUSTOMER#{c}"
        joined = NOW - timedelta(days=rng.randrange(60, 1000))
        profile = {"pk": pk, "sk": "PROFILE", "name": f"Customer {c}", "email": f"customer{c}@example.com",
                   "tier": rng.choice(["free", "plus", "pro"]), "created_at": f"{joined:%Y-%m-%dT%H:%M:%SZ}",
                   "phone": f"+1-555-{rng.randrange(1000, 9999)}", "marketing_opt_in": rng.random() < 0.6,
                   "tags": set(rng.sample(["early-adopter", "b2b", "gift-buyer", "returns-often"], rng.randint(1, 2))),
                   "address": {"street": f"{rng.randrange(1, 300)} Main St", "city": city, "country": country,
                               # an old import stored some US zip codes as numbers
                               "zip": int(zip_code) if country == "US" and rng.random() < 0.3 else zip_code}}
        if c % 7 == 0:
            profile["updated_at"] = f"{NOW - timedelta(days=rng.randrange(30)):%Y-%m-%dT%H:%M:%SZ}"
        put("acme-app", profile)
        orders = 12 if c == 1042 else rng.randint(1, 7)
        for n in range(orders):
            day = NOW - timedelta(days=rng.randrange(400))
            order_id = 7731 if (c == 1042 and n == 7) else rng.randrange(1000, 9999)
            if c == 1042 and n == 7:
                day = datetime(2026, 8, 14, 16, 5, tzinfo=timezone.utc)
            status = rng.choices(statuses, [30, 12, 8, 6, 3, 2])[0]
            lines = [{"sku": f"SKU-{rng.randrange(100, 999)}", "qty": rng.randint(1, 3),
                      "price": round(rng.uniform(4, 180), 2)} for _ in range(3 if order_id == 7731 else rng.randint(1, 4))]
            item = {"pk": pk, "sk": f"ORDER#{day:%Y-%m-%d}#{order_id}", "status": status,
                    "created_at": f"{day:%Y-%m-%dT%H:%M:%SZ}", "currency": "EUR" if country in ("DE", "FR") else "USD",
                    "total": round(sum(line["qty"] * line["price"] for line in lines), 2),
                    "coupon": rng.choice(coupons), "lines": lines,
                    "shipping": {"method": rng.choice(["standard", "express"]), "eta": f"{day + timedelta(days=4):%Y-%m-%d}"}}
            if status == "failed":
                item["failure_reason"] = rng.choice(reasons)
            if status in ("cancelled", "failed"):
                item["expires_at"] = int((day + timedelta(days=400)).timestamp())
            put("acme-app", item)
        for n in range(rng.choice([0, 0, 1, 2])):
            opened = NOW - timedelta(days=rng.randrange(200))
            put("acme-app", {"pk": pk, "sk": f"TICKET#{rng.randrange(10000, 99999)}",
                             "subject": rng.choice(["Where is my order?", "Refund request", "Change my address",
                                                    "Coupon didn't apply"]),
                             "priority": rng.choice(["low", "normal", "high"]), "channel": rng.choice(["email", "chat"]),
                             "opened_at": f"{opened:%Y-%m-%dT%H:%M:%SZ}"})

    ddb.create_table(
        TableName="acme-sessions", BillingMode="PROVISIONED",
        ProvisionedThroughput={"ReadCapacityUnits": 400, "WriteCapacityUnits": 200},
        KeySchema=[{"AttributeName": "session_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "session_id", "AttributeType": "S"}])
    ddb.update_time_to_live(TableName="acme-sessions", TimeToLiveSpecification={"Enabled": True,
                                                                                "AttributeName": "expires"})
    ddb.create_table(
        TableName="acme-feature-store", BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "entity_id", "KeyType": "HASH"}, {"AttributeName": "feature_set", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": n, "AttributeType": "S"} for n in ("entity_id", "feature_set")])
    ddb.create_table(
        TableName="acme-events-archive", BillingMode="PROVISIONED", TableClass="STANDARD_INFREQUENT_ACCESS",
        ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        KeySchema=[{"AttributeName": "event_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "event_id", "AttributeType": "S"}])
    for table in ("acme-sessions", "acme-feature-store"):
        ddb.update_continuous_backups(TableName=table,
                                      PointInTimeRecoverySpecification={"PointInTimeRecoveryEnabled": True})
    for i in range(20):
        put("acme-sessions", {"session_id": f"{rng.getrandbits(64):016x}", "user": f"CUSTOMER#{rng.choice(customers)}",
                              "expires": int((NOW + timedelta(hours=rng.randrange(48))).timestamp())})
        put("acme-feature-store", {"entity_id": f"CUSTOMER#{rng.choice(customers)}", "feature_set": "churn-v3",
                                   "values": {"logins_7d": rng.randrange(40), "spend_30d": round(rng.uniform(0, 500), 2)}})
        put("acme-events-archive", {"event_id": f"{rng.getrandbits(64):016x}", "type": rng.choice(["view", "click"])})

    # CloudWatch usage, every 5 minutes for the last day: acme-app is busy; acme-sessions uses a sliver of its capacity.
    cloudwatch = boto3.client("cloudwatch", region_name=REGION)
    data = []
    for table, reads, writes in (("acme-app", 167.0, 34.0), ("acme-sessions", 9.0, 4.0), ("acme-feature-store", 48.0, 6.0),
                                 ("acme-events-archive", 0.4, 1.5)):
        dims = [{"Name": "TableName", "Value": table}]
        for step in range(1, 288):
            when = NOW - timedelta(minutes=5 * step)
            busy = 1.9 if 14 <= when.hour <= 17 else 1.0 if 8 <= when.hour <= 22 else 0.35
            data += [{"MetricName": "ConsumedReadCapacityUnits", "Dimensions": dims, "Timestamp": when,
                      "Value": reads * 300 * busy * rng.uniform(0.85, 1.15)},
                     {"MetricName": "ConsumedWriteCapacityUnits", "Dimensions": dims, "Timestamp": when,
                      "Value": writes * 300 * busy * rng.uniform(0.85, 1.15)}]
    for start in range(0, len(data), 1000):
        cloudwatch.put_metric_data(Namespace="AWS/DynamoDB", MetricData=data[start:start + 1000])

    def on(year: int, month: int, day: int) -> datetime:
        return datetime(year, month, day, 10, 2, tzinfo=timezone.utc)

    stats = {"acme-app": {"items": 4_213_876, "size": int(3.4 * GB), "created": on(2023, 3, 14),
                          "by-status": (3_650_110, int(610 * MB)), "by-email": (184_220, int(14.2 * MB))},
             "acme-sessions": {"items": 88_410, "size": int(41 * MB), "created": on(2024, 1, 20)},
             "acme-feature-store": {"items": 1_204_551, "size": int(2.1 * GB), "created": on(2025, 2, 3)},
             "acme-events-archive": {"items": 18_300_442, "size": int(12.4 * GB), "created": on(2022, 6, 7)}}
    return {"client": DescribeOverlay(ddb, stats)}


def seed_bedrock_docs() -> dict:
    import demo

    return demo.seed_bedrock_kb()


SCENES = {"s3": seed_s3_docs, "dynamodb": seed_dynamodb_docs, "bedrock_kb": seed_bedrock_docs}


# ----------------------------------------------------------------------------- rendering and screenshots


def render(service: str, figures: list[Figure]) -> dict[str, list[str]]:
    """Every figure's report(s) as notebook HTML fragments, from one moto session with the service's scene."""
    for name, value in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                        "AWS_SESSION_TOKEN": "testing", "AWS_DEFAULT_REGION": REGION}.items():
        os.environ[name] = value
    os.environ.pop("AWS_PROFILE", None)
    from moto import mock_aws

    with mock_aws():
        kwargs = SCENES[service]()
        mod = importlib.import_module(service)
        view_cls = next(v for k, v in vars(mod).items() if k.endswith("View") and isinstance(v, type))
        core_cls = next(v for k, v in vars(mod).items() if k.endswith("Analyzer") and isinstance(v, type))
        core = core_cls(region=REGION, **kwargs) if "session" not in kwargs else core_cls(**kwargs)
        out: dict[str, list[str]] = {}
        for figure in figures:
            ui = view_cls(core, mode="text", progress="off")
            shown: list[str] = []
            scope = {"ui": ui, "core": core, "mod": mod}
            ui._show = lambda blocks: None
            exec(figure.setup, scope)
            ui._show = lambda blocks, ui=ui, shown=shown: shown.append(mod._render_html(blocks, ui.max_rows))
            exec(figure.code, scope)
            out[figure.name] = shown
        return out


def page(fragments: list[str], theme: str) -> str:
    return (f'<!doctype html><html lang="en" style="color-scheme:{theme}"><head><meta charset="utf-8"><style>'
            f"body{{margin:0;padding:{MARGIN}px {MARGIN}px 0;background:Canvas;color:CanvasText}}</style></head><body>"
            + "".join(fragments) + "</body></html>")


def chrome() -> str:
    found = os.environ.get("CHROME") or next(iter(sorted(Path.home().glob(
        ".cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"))), None)
    found = found or shutil.which("chromium") or shutil.which("google-chrome") or shutil.which("chrome")
    if not found:
        sys.exit("No Chrome found: set CHROME=/path/to/chrome, or: npx playwright install chromium-headless-shell")
    return str(found)


def shoot(html_path: Path, png_path: Path, browser: str) -> None:
    subprocess.run([browser, "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                    f"--window-size={WIDTH},6000", f"--force-device-scale-factor={SCALE}",
                    f"--screenshot={png_path}", html_path.as_uri()], check=True, capture_output=True, timeout=120)


def trim(png_path: Path, crop: int | None):
    """The screenshot cut just below the last thing drawn (and at `crop` CSS px, when given)."""
    from PIL import Image, ImageChops

    image = Image.open(png_path).convert("RGB")
    background = Image.new("RGB", image.size, image.getpixel((image.width - 1, image.height - 1)))
    box = ImageChops.difference(image, background).getbbox()
    bottom = (box[3] if box else image.height) + round(MARGIN * SCALE)
    if crop:
        bottom = min(bottom, round(crop * SCALE))
    return image.crop((0, 0, image.width, min(bottom, image.height)))


def set_height(service: str, name: str, height: int) -> None:
    guide = ROOT / "docs" / GUIDES[service]
    text = guide.read_text(encoding="utf-8")
    pattern = re.compile(rf'(<img src="images/{re.escape(name)}-light\.webp"[^>]*?height=")(\d+)(")')
    if pattern.search(text):
        guide.write_text(pattern.sub(rf"\g<1>{height}\g<3>", text), encoding="utf-8")
    else:
        print(f"  (no <img> for {name} in {guide.name}: add one with height=\"{height}\")")


def main() -> int:
    intro, _, rest = (__doc__ or "").partition("\n\n")
    parser = argparse.ArgumentParser(description=intro, epilog=rest, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", help="figures to make (image names); default: all")
    parser.add_argument("--list", action="store_true", help="list the figures and exit")
    parser.add_argument("--html", type=Path, help="also keep the rendered pages in this folder")
    args = parser.parse_args()
    if args.list:
        for figure in FIGURES:
            print(f"{figure.name:24} {figure.service:11} {figure.code}")
        return 0
    unknown = set(args.names) - {f.name for f in FIGURES}
    if unknown:
        parser.error(f"no figure {', '.join(sorted(unknown))} (--list shows them)")
    try:
        importlib.import_module("PIL")
    except ImportError:
        parser.error("needs Pillow: pip install pillow")
    browser = chrome()
    chosen = [f for f in FIGURES if not args.names or f.name in args.names]
    with tempfile.TemporaryDirectory() as tmp:
        for service in GUIDES:
            figures = [f for f in chosen if f.service == service]
            if not figures:
                continue
            print(f"{service}: rendering {len(figures)} figure{'s' * (len(figures) != 1)}", file=sys.stderr)
            fragments = render(service, figures)
            for figure in figures:
                heights = set()
                for theme in ("light", "dark"):
                    html_path = Path(tmp) / f"{figure.name}-{theme}.html"
                    html_path.write_text(page(fragments[figure.name], theme), encoding="utf-8")
                    if args.html:
                        args.html.mkdir(parents=True, exist_ok=True)
                        shutil.copy(html_path, args.html / html_path.name)
                    png = Path(tmp) / f"{figure.name}-{theme}.png"
                    shoot(html_path, png, browser)
                    image = trim(png, figure.crop)
                    image.save(IMAGES / f"{figure.name}-{theme}.webp", "WEBP", quality=90, method=6)
                    heights.add(round(image.height / SCALE))
                height = max(heights)
                set_height(service, figure.name, height)
                print(f"  {figure.name}: {WIDTH} x {height}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
