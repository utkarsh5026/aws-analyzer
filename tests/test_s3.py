import gzip
import importlib
import io
import json
import pickle
import tarfile
import zipfile
from datetime import date, datetime, timedelta, timezone

import boto3
import fastavro
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.feather as feather
import pyarrow.orc as orc
import pyarrow.parquet as pq
import pypdf
import pytest
from moto import mock_aws

import s3 as s3mod
from s3 import (
    GB,
    KB,
    MB,
    TB,
    BucketConfig,
    ObjectInfo,
    S3Analyzer,
    S3View,
    build_folder_tree,
    bucket_findings,
    cloudwatch_cost,
    compare_objects,
    detect_format,
    explain_policy,
    file_extension,
    find_duplicate_groups,
    folder_of,
    human_money,
    human_size,
    make_filter,
    object_monthly_cost,
    objects_to_df,
    parse_avro,
    parse_docx,
    parse_pdf,
    parse_pptx,
    parse_s3_uri,
    parse_size,
    parse_time,
    policy_findings,
    simulate_lifecycle_objects,
    sniff_format,
    storage_type_class,
    summarize_objects,
    summary_findings,
)

NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)
CSV = b"id,name,score\n1,a,0.5\n2,b,0.7\n3,c,0.9\n"
BUCKET = "data-lake"


def obj(key, size, days_old=0.0, storage_class="STANDARD", etag="etag"):
    return ObjectInfo("b", key, size, NOW - timedelta(days=days_old), storage_class, etag)


# ----------------------------------------------------------------------------- helpers


@pytest.mark.parametrize("uri, expected", [
    ("s3://bucket/a/b.csv", ("bucket", "a/b.csv")),
    ("s3a://bucket/", ("bucket", "")),
    ("bucket", ("bucket", "")),
    ("  bucket/prefix/  ", ("bucket", "prefix/")),
])
def test_parse_s3_uri(uri, expected):
    assert parse_s3_uri(uri) == expected


def test_parse_s3_uri_rejects_missing_bucket():
    with pytest.raises(ValueError):
        parse_s3_uri("s3://")


@pytest.mark.parametrize("value, expected", [
    (1024, 1024), ("10MB", 10 * MB), ("1.5 GiB", int(1.5 * GB)), ("512k", 512 * 1024), ("7b", 7), (None, None),
])
def test_parse_size(value, expected):
    assert parse_size(value) == expected


def test_parse_size_rejects_garbage():
    with pytest.raises(ValueError):
        parse_size("ten megs")


def test_parse_time():
    assert parse_time("7d", now=NOW) == NOW - timedelta(days=7)
    assert parse_time("12h", now=NOW) == NOW - timedelta(hours=12)
    assert parse_time("2024-05-01") == datetime(2024, 5, 1, tzinfo=timezone.utc)
    assert parse_time(datetime(2024, 5, 1, 10)).tzinfo == timezone.utc
    assert parse_time("2024-05-01T10:00:00Z") == datetime(2024, 5, 1, 10, tzinfo=timezone.utc)


def test_human_money():
    assert human_money(12.346) == "$12.35"
    assert human_money(0.004) == "<$0.01"
    assert human_money(0) == "$0.00"
    assert human_money(12345.6) == "$12,346"
    assert human_money(-3) == "-$3.00"
    assert human_money(None) == "-"


def test_human_size():
    assert human_size(0) == "0 B"
    assert human_size(1536) == "1.5 KB"
    assert human_size(5 * TB) == "5.0 TB"
    assert human_size(3 * 1024 * TB) == "3.0 PB"
    assert human_size(None) == "-"


@pytest.mark.parametrize("key, expected", [
    ("a/data.csv", "csv"), ("a/data.CSV.GZ", "csv.gz"), ("a/part-0.snappy.parquet", "parquet"),
    ("a/README", "(none)"), ("a/.env", "(none)"), ("a/", "(folder marker)"), ("a/x.2024.gz", "gz"),
])
def test_file_extension(key, expected):
    assert file_extension(key) == expected


@pytest.mark.parametrize("key, expected", [
    ("x.csv.gz", ("csv", "gz")), ("logs.gz", (None, "gz")), ("p.snappy.parquet", ("parquet", None)),
    ("a/b.jsonl.bz2", ("jsonl", "bz2")), ("README", (None, None)), ("img.PNG", ("image", None)),
    ("model.tar.gz", ("tar", "gz")), ("m.tgz", ("tar", "gz")), ("x.csv.zst", ("csv", "zst")), ("x.gzip", (None, "gz")),
    ("t.orc", ("orc", None)), ("t.feather", ("arrow", None)), ("e.avro", ("avro", None)), ("b.xlsx", ("excel", None)),
    ("d.psv", ("psv", None)), ("a.npy", ("npy", None)), ("w.safetensors", ("safetensors", None)),
    ("nb.ipynb", ("notebook", None)), ("s.mp3", ("audio", None)), ("v.mp4", ("video", None)), ("d.pdf", ("pdf", None)),
    ("m.pth", ("torch", None)), ("m.pkl", ("pickle", None)),
    ("r.docx", ("docx", None)), ("d.pptx", ("pptx", None)), ("old.DOC", ("oldoffice", None)), ("s.ppt", ("oldoffice", None)),
])
def test_detect_format(key, expected):
    assert detect_format(key) == expected


@pytest.mark.parametrize("head, expected", [
    (b"PAR1\x15\x04", ("parquet", None)), (b"ORC\x0a", ("orc", None)), (b"ARROW1\x00\x00", ("arrow", None)),
    (b"Obj\x01\x04", ("avro", None)), (b"\x93NUMPY\x01\x00", ("npy", None)), (b"%PDF-1.7", ("pdf", None)),
    (b"PK\x03\x04", ("zip", None)), (b"\x89PNG\r\n\x1a\n", ("image", None)), (b"\xff\xd8\xff\xe0", ("image", None)),
    (b"\x1f\x8b\x08", (None, "gz")), (b"BZh91AY", (None, "bz2")), (b"\xfd7zXZ\x00", (None, "xz")),
    (b"\x28\xb5\x2f\xfd", (None, "zst")), (b"  {\"a\": 1}", ("json", None)), (b"[1, 2]", ("json", None)),
    (bytes(257) + b"ustar", ("tar", None)), (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1\x00", ("oldoffice", None)), (b"id,name\n1,a", (None, None)), (b"BZhello", (None, None)), (b"", (None, None)),
])
def test_sniff_format(head, expected):
    assert sniff_format(head) == expected


def test_folder_of():
    assert folder_of("p/a/b/c.csv", "p/", 1) == "a/"
    assert folder_of("p/a/b/c.csv", "p/", 2) == "a/b/"
    assert folder_of("p/c.csv", "p/", 2) == ""


# ------------------------------------------------------------------- pure analysis


def sample_objects():
    return [
        obj("p/a/1.csv", 100, days_old=1),
        obj("p/a/2.csv", 2 * MB, days_old=400),
        obj("p/b/x.parquet", 0, days_old=10),
        obj("p/root.txt", 5, days_old=0.5),
        obj("p/b/", 0),  # folder marker
    ]


def test_summarize_objects():
    s = summarize_objects(sample_objects(), "s3://b/p/", now=NOW, top_n=2)
    assert (s.object_count, s.folder_markers, s.empty_count) == (4, 1, 1)
    assert s.total_size == 100 + 2 * MB + 5
    assert (s.min_size, s.max_size) == (0, 2 * MB)
    assert set(s.by_folder) == {"a/", "b/", ""}
    assert list(s.by_folder)[0] == "a/"  # sorted by size
    assert s.by_extension["csv"].count == 2
    assert s.size_histogram["0 B (empty)"].count == 1
    assert s.size_histogram["< 1 KB"].count == 2
    assert s.size_histogram["1 - 10 MB"].count == 1
    assert [s.age_histogram[k].count for k in ("< 1 day", "1 - 7 days", "1 - 4 weeks", "1 - 3 years")] == [1, 1, 1, 1]
    assert [o.size for o in s.largest] == [2 * MB, 100]
    assert (s.cold_standard.count, s.cold_standard.size) == (1, 2 * MB)
    assert s.oldest.key == "p/a/2.csv" and s.newest.key == "p/root.txt"
    assert s.uri == "s3://b/p/"


def test_summarize_objects_limit_marks_truncated():
    s = summarize_objects(sample_objects(), "s3://b/p/", now=NOW, limit=2)
    assert s.truncated and s.object_count == 2


def test_summarize_objects_empty():
    s = summarize_objects([], "s3://b/nothing/")
    assert s.object_count == 0 and s.largest == [] and s.avg_size == 0


def test_build_folder_tree():
    objects = [obj("p/a/x/1.csv", 10), obj("p/a/y/2.csv", 20), obj("p/a/3.csv", 30), obj("p/top.txt", 1)]
    tree = build_folder_tree(objects, "s3://b/p/", depth=2)
    assert list(tree.folders) == ["", "a/", "a/x/", "a/y/"]
    assert tree.folders["a/"].size == 60 and tree.folders["a/"].count == 3
    assert tree.folders[""].size == 1
    assert (tree.total.count, tree.total.size) == (4, 61)


def test_make_filter():
    objects = [obj("d/a.csv", 10, 1), obj("d/b.csv.gz", 20 * MB, 30), obj("d/sub/c.parquet", 5, 2, "GLACIER")]

    def keys(**kw):
        return [o.key for o in objects if make_filter(**kw)(o)]

    assert keys(pattern="*.csv") == ["d/a.csv"]
    assert keys(pattern="d/sub/*") == ["d/sub/c.parquet"]
    assert keys(extensions="csv") == ["d/a.csv", "d/b.csv.gz"]
    assert keys(extensions=[".csv.gz"]) == ["d/b.csv.gz"]
    assert keys(min_size="1MB") == ["d/b.csv.gz"]
    assert keys(max_size=10) == ["d/a.csv", "d/sub/c.parquet"]
    assert keys(storage_classes="glacier") == ["d/sub/c.parquet"]
    assert keys(regex=r"/sub/") == ["d/sub/c.parquet"]
    assert keys(modified_after=NOW - timedelta(days=3)) == ["d/a.csv", "d/sub/c.parquet"]
    assert keys(modified_before=NOW - timedelta(days=3)) == ["d/b.csv.gz"]
    assert keys(extensions="csv", min_size="1MB") == ["d/b.csv.gz"]


def test_find_duplicate_groups():
    objects = [obj("a", 10, etag="x"), obj("b", 10, etag="x"), obj("c", 10, etag="y"),
               obj("d", 500, etag="z"), obj("e", 500, etag="z"), obj("f", 500, etag="z"), obj("g", 0, etag="e0"),
               obj("h", 0, etag="e0")]
    groups = find_duplicate_groups(objects)
    assert [[o.key for o in g] for g in groups] == [["d", "e", "f"], ["a", "b"]]  # zero-byte skipped
    assert find_duplicate_groups(objects, min_size="100") == [groups[0]]


def test_compare_objects():
    a = [obj("src/same.csv", 10, etag="1"), obj("src/changed.csv", 10, etag="2"), obj("src/resized.csv", 10),
         obj("src/multi.bin", 99, etag="aaa-3"), obj("src/only_a.csv", 1)]
    b = [obj("dst/same.csv", 10, etag="1"), obj("dst/changed.csv", 10, etag="3"), obj("dst/resized.csv", 11),
         obj("dst/multi.bin", 99, etag="bbb"), obj("dst/only_b.csv", 1)]
    r = compare_objects(a, b, prefix_a="src/", prefix_b="dst/")
    assert r.identical == 1 and r.unverifiable == 1
    assert sorted(x.key for x, _ in r.different) == ["src/changed.csv", "src/resized.csv"]
    assert [o.key for o in r.only_in_a] == ["src/only_a.csv"]
    assert [o.key for o in r.only_in_b] == ["dst/only_b.csv"]
    assert not r.in_sync


def test_summary_findings():
    many_small = [obj(f"p/{i}.json", 200) for i in range(1200)] + [obj("p/cold.bin", 2 * GB, 200)]
    s = summarize_objects(many_small + [obj("p/ice.csv", 5, storage_class="DEEP_ARCHIVE")], "s3://b/p/", now=NOW)
    text = " ".join(m for _, m in summary_findings(s))
    assert "under 1 MB" in text and "DEEP_ARCHIVE" in text and "90+ days" in text
    assert "In STANDARD_IA it would cost about $0.02/month less" in text  # 2 GB x ($0.023 - $0.0125)


def test_bucket_findings():
    cfg = BucketConfig(name="b", versioning="Enabled", encryption="AES256",
                       public_access_block={"BlockPublicAcls": True, "IgnorePublicAcls": False})
    text = " ".join(m for _, m in bucket_findings(cfg))
    assert "Block Public Access" in text and "noncurrent" in text and "incomplete multipart" in text
    safe = BucketConfig(name="b", versioning="Enabled", encryption="AES256",
                        public_access_block={"A": True, "B": True},
                        lifecycle_rules=[{"Status": "Enabled", "NoncurrentVersionExpiration": {"NoncurrentDays": 30},
                                          "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7}}])
    assert bucket_findings(safe) == []


def test_bucket_findings_uses_account_block_public_access():
    cfg = BucketConfig(name="b", encryption="AES256")
    assert any("may still apply" in m for _, m in bucket_findings(cfg))
    assert not any("Block Public Access" in m for _, m in bucket_findings(cfg, {"A": True, "B": True}))
    assert any("this bucket or the account" in m for _, m in bucket_findings(cfg, {}))


# --------------------------------------------------------------------------- cost


def test_object_monthly_cost():
    assert object_monthly_cost(GB, "STANDARD") == pytest.approx(0.023)
    assert object_monthly_cost(GB, "STANDARD_IA") == pytest.approx(0.0125)
    assert object_monthly_cost(KB, "STANDARD_IA") == pytest.approx(128 * KB * 0.0125 / GB)  # billed as 128 KB
    assert object_monthly_cost(0, "GLACIER") == pytest.approx((32 * KB * 0.0036 + 8 * KB * 0.023) / GB)
    assert object_monthly_cost(GB, "OUTPOSTS") is None
    assert object_monthly_cost(GB, "STANDARD", {"STANDARD": 0.03}) == pytest.approx(0.03)


@pytest.mark.parametrize("storage_type, cls", [
    ("StandardStorage", "STANDARD"), ("StandardIAStorage", "STANDARD_IA"), ("StandardIASizeOverhead", "STANDARD_IA"),
    ("OneZoneIAStorage", "ONEZONE_IA"), ("GlacierInstantRetrievalStorage", "GLACIER_IR"),
    ("GlacierStorage", "GLACIER"), ("GlacierObjectOverhead", "GLACIER"), ("GlacierS3ObjectOverhead", "STANDARD"),
    ("DeepArchiveStorage", "DEEP_ARCHIVE"), ("DeepArchiveStagingStorage", "STANDARD"),
    ("IntelligentTieringFAStorage", "INTELLIGENT_TIERING"), ("IntelligentTieringIAStorage", "STANDARD_IA"),
    ("IntelligentTieringAIAStorage", "GLACIER_IR"), ("IntelligentTieringAAStorage", "GLACIER"),
    ("IntelligentTieringDAAStorage", "DEEP_ARCHIVE"), ("SomethingNew", None),
])
def test_storage_type_class(storage_type, cls):
    assert storage_type_class(storage_type) == cls


def test_cloudwatch_cost():
    costs = cloudwatch_cost({"StandardStorage": 10 * GB, "GlacierStorage": GB, "SomethingNew": GB})
    assert costs == {"StandardStorage": pytest.approx(0.23), "GlacierStorage": pytest.approx(0.0036),
                     "SomethingNew": None}


def test_summary_cost_and_small_files_in_ia():
    tiny = [obj(f"p/tiny{i}", KB, storage_class="STANDARD_IA") for i in range(1000)]
    s = summarize_objects([obj("p/big.bin", 10 * GB), obj("p/ia.bin", GB, storage_class="STANDARD_IA"),
                           obj("p/edge.bin", GB, storage_class="OUTPOSTS")] + tiny, "s3://b/p/", now=NOW)
    ia_cost = (GB + 1000 * 128 * KB) * 0.0125 / GB
    assert s.cost_by_storage_class == {"STANDARD": pytest.approx(0.23), "STANDARD_IA": pytest.approx(ia_cost),
                                       "OUTPOSTS": None}
    assert s.monthly_cost == pytest.approx(0.23 + ia_cost)
    assert (s.below_minimum["STANDARD_IA"].count, s.below_minimum["STANDARD_IA"].size) == (1000, 1000 * KB)
    level, message = next(f for f in summary_findings(s) if "128 KB" in f[1])
    assert level == "info" and "In STANDARD they would cost" in message  # a fraction of a cent: just a note
    pricey = {"STANDARD": 0.023, "STANDARD_IA": 50.0}
    assert next(f for f in summary_findings(s, pricey) if "128 KB" in f[1])[0] == "warn"


# --------------------------------------------------------------------------- avro

AVRO_SCHEMA = {"type": "record", "name": "Event", "namespace": "acme", "fields": [
    {"name": "id", "type": "long"},
    {"name": "name", "type": "string"},
    {"name": "score", "type": ["null", "double"]},
    {"name": "tags", "type": {"type": "array", "items": "string"}},
    {"name": "counts", "type": {"type": "map", "values": "int"}},
    {"name": "kind", "type": {"type": "enum", "name": "Kind", "symbols": ["A", "B"]}},
    {"name": "at", "type": {"type": "long", "logicalType": "timestamp-millis"}},
    {"name": "day", "type": {"type": "int", "logicalType": "date"}},
    {"name": "address", "type": ["null", {"type": "record", "name": "Address", "fields": [{"name": "city", "type": "string"}]}]},
    {"name": "home", "type": ["null", "acme.Address"]},
]}


def avro_bytes(count=500, codec="deflate"):
    records = [{"id": i, "name": f"n{i}", "score": None if i % 2 else i / 2, "tags": ["x"] * (i % 3),
                "counts": {"a": i, "b": -i}, "kind": "AB"[i % 2], "at": datetime(2024, 1, 1, tzinfo=timezone.utc),
                "day": date(2024, 1, 2), "address": None if i % 2 else {"city": "Pune"},
                "home": {"city": "Delhi"} if i % 3 == 0 else None} for i in range(count)]
    buffer = io.BytesIO()
    fastavro.writer(buffer, fastavro.parse_schema(AVRO_SCHEMA), records, codec=codec, sync_interval=2000)
    return buffer.getvalue(), records


@pytest.mark.parametrize("codec", ["null", "deflate", "bzip2", "xz"])
def test_parse_avro_matches_fastavro(codec):
    data, records = avro_bytes(codec=codec)
    schema, got_codec, got, complete = parse_avro(data)
    assert got == list(fastavro.reader(io.BytesIO(data))) and got_codec == codec and complete
    assert schema["name"].endswith("Event") and got[0]["day"] == date(2024, 1, 2) and got[0]["address"] == {"city": "Pune"}
    assert parse_avro(data, n=3)[2] == got[:3]
    _, _, part, complete = parse_avro(data[: len(data) // 2])  # a cut-off download: whole blocks only
    assert not complete and 0 < len(part) < len(records) and part == got[: len(part)]


def test_parse_avro_rejects_other_files():
    with pytest.raises(ValueError):
        parse_avro(b"PAR1 not avro")


# ---------------------------------------------------------------------- documents


def test_parse_docx():
    doc = parse_docx(io.BytesIO(docx_bytes()))
    assert doc.parts == ["Churn study", "Data", "Rows: 2,000\tok", "- direct bullet", "- style bullet",
                         "inside a content control", "plan | churn\npro | 11%", "Outline level 3"]
    assert doc.headings == [(0, "Churn study"), (1, "Data"), (3, "Outline level 3")]
    assert doc.tables == [[["plan", "churn"], ["pro", "11%"]]]
    assert (doc.title, doc.author, doc.page_count, doc.kind) == ("Churn study", "Kirti", 3, "docx")
    assert "removed" not in doc.text and doc.word_count == 21  # "-" and "|" aren't words


def test_parse_docx_refuses_xml_entities():
    evil = zip_bytes({"word/document.xml": '<!DOCTYPE x [<!ENTITY a "aaaa">]><w:document ' + W_NS + '>&a;</w:document>'})
    with pytest.raises(ValueError, match="DTD"):
        parse_docx(io.BytesIO(evil))
    with pytest.raises(ValueError, match="missing"):
        parse_docx(io.BytesIO(zip_bytes({"a.txt": "not word"})))


def test_parse_pptx():
    deck = pptx_bytes(DECK, order=[2, 3, 1])  # the deck shows slide files in this order
    doc = parse_pptx(io.BytesIO(deck))
    assert doc.slide_titles == ["Q3 review", "Highlights", "Numbers"] and doc.numbers == [1, 2, 3]
    assert doc.parts == ["Q3 review\nML platform team", "Highlights\nChurn down 3 points\nStorage cost -40%",
                         "Numbers\nmetric | value\nAUC | 0.91"]
    assert doc.notes == ["", "Mention the lifecycle rule.", ""]  # the slide-number placeholder is left out
    assert doc.tables == [[["metric", "value"], ["AUC", "0.91"]]] and (doc.title, doc.page_count) == ("Q3 review", 3)
    assert parse_pptx(io.BytesIO(deck), slides=[3]).parts == ["Numbers\nmetric | value\nAUC | 0.91"]
    with pytest.raises(ValueError, match="Slide 9"):
        parse_pptx(io.BytesIO(deck), slides=[9])


def test_parse_pdf():
    data = pdf_bytes("First page", "", "Third page", title="Handbook")
    doc = parse_pdf(io.BytesIO(data))
    assert doc.parts == ["First page", "", "Third page"] and doc.numbers == [1, 2, 3]
    assert (doc.page_count, doc.title, doc.author, doc.kind) == (3, "Handbook", "Kirti", "pdf")
    assert parse_pdf(io.BytesIO(data), pages=[3]).parts == ["Third page"]
    with pytest.raises(ValueError, match="Page 4"):
        parse_pdf(io.BytesIO(data), pages=[4])
    with pytest.raises(ValueError, match="pypdf couldn't read"):
        parse_pdf(io.BytesIO(b"%PDF-1.4 not really"))


def test_parse_pdf_with_a_password():
    writer = pypdf.PdfWriter(clone_from=io.BytesIO(pdf_bytes("Secret page")))
    writer.encrypt(user_password="letmein", owner_password="owner", algorithm="RC4-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(ValueError, match="password"):
        parse_pdf(io.BytesIO(buffer.getvalue()))
    assert parse_pdf(io.BytesIO(buffer.getvalue()), password="letmein").parts == ["Secret page"]


# ---------------------------------------------------------------------- lifecycle


def lifecycle_objects():
    return [
        obj("logs/new.log", MB, days_old=5),
        obj("logs/month.log", MB, days_old=45),
        obj("logs/old.log", MB, days_old=400),
        obj("logs/tiny.log", KB, days_old=400),
        obj("logs/ia.log", MB, days_old=45, storage_class="STANDARD_IA"),
        obj("logs/deep.log", MB, days_old=400, storage_class="DEEP_ARCHIVE"),
        obj("logs/", 0),  # folder marker
    ]


def test_simulate_lifecycle_moves():
    impact = simulate_lifecycle_objects(lifecycle_objects(), "s3://b/logs/", move_after=30, to="standard_ia", now=NOW)
    assert impact.scanned.count == 6 and impact.transitions == [(30, "STANDARD_IA")]
    assert impact.moves["STANDARD_IA"].count == 2  # month + old; ia.log is there already, deep.log is colder
    assert impact.too_small.count == 1  # tiny.log: lifecycle doesn't move objects under 128 KB
    assert impact.expired.count == 0 and impact.early_removals.count == 0
    assert impact.monthly_savings == pytest.approx(2 * MB * (0.023 - 0.0125) / GB)
    assert impact.one_time_cost == pytest.approx(2 * 0.01 / 1000)  # two transition requests
    assert impact.describe() == "move to STANDARD_IA after 30 days"
    assert impact.rule() == {"ID": "logs-lifecycle", "Status": "Enabled", "Filter": {"Prefix": "logs/"},
                             "Transitions": [{"Days": 30, "StorageClass": "STANDARD_IA"}]}


def test_simulate_lifecycle_steps_and_expiry():
    impact = simulate_lifecycle_objects(lifecycle_objects(), "s3://b/logs/", move_after={30: "STANDARD_IA", 180: "GLACIER"},
                                        delete_after=365, now=NOW)
    assert impact.expired.count == 3  # old, tiny and deep: every size expires
    assert {cls: st.count for cls, st in impact.moves.items()} == {"STANDARD_IA": 1}
    assert impact.early_removals.count == 0  # deep.log is past DEEP_ARCHIVE's 180 days
    assert impact.cost_after < impact.cost_before
    assert impact.rule()["Expiration"] == {"Days": 365} and len(impact.rule()["Transitions"]) == 2


def test_simulate_lifecycle_follows_the_s3_waterfall():
    objects = [obj("ia.bin", MB, days_old=100, storage_class="STANDARD_IA"),
               obj("it.bin", MB, days_old=100, storage_class="INTELLIGENT_TIERING")]
    impact = simulate_lifecycle_objects(objects, move_after=0, to="INTELLIGENT_TIERING", now=NOW)
    assert impact.moves["INTELLIGENT_TIERING"].count == 1  # IA -> Intelligent-Tiering is allowed
    assert simulate_lifecycle_objects(objects, move_after=30, to="STANDARD_IA", now=NOW).moves == {}


def test_simulate_lifecycle_bills_early_removal():
    impact = simulate_lifecycle_objects([obj("x", GB, days_old=10, storage_class="GLACIER")], "s3://b/",
                                        move_after=0, to="DEEP_ARCHIVE", now=NOW)
    assert impact.early_removals.count == 1  # GLACIER keeps objects for 90 days
    assert impact.one_time_cost == pytest.approx(object_monthly_cost(GB, "GLACIER") * 80 / 30 + 0.05 / 1000)


@pytest.mark.parametrize("kwargs", [
    {},
    {"move_after": 30},
    {"to": "GLACIER"},
    {"move_after": 10, "to": "STANDARD_IA"},  # IA needs 30 days
    {"move_after": 30, "to": "STANDARD"},
    {"move_after": {30: "GLACIER", 60: "STANDARD_IA"}},  # has to get colder
    {"move_after": {30: "STANDARD_IA", 40: "GLACIER"}},  # 30 days in IA first
    {"move_after": {0: "INTELLIGENT_TIERING", 30: "STANDARD_IA"}},  # S3 can't move Intelligent-Tiering to IA
    {"move_after": {30: "GLACIER"}, "to": "GLACIER"},
    {"move_after": 30, "to": "GLACIER", "delete_after": 30},  # delete after the last move
])
def test_simulate_lifecycle_rejects_rules_s3_would(kwargs):
    with pytest.raises(ValueError):
        simulate_lifecycle_objects([], **kwargs)


# ------------------------------------------------------------------------- policy

POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {"Sid": "PublicRead", "Effect": "Allow", "Principal": "*", "Action": "s3:GetObject",
         "Resource": "arn:aws:s3:::b/public/*"},
        {"Sid": "Partner", "Effect": "Allow",
         "Principal": {"AWS": ["arn:aws:iam::999988887777:role/service-role/Loader", "arn:aws:iam::123456789012:root"]},
         "Action": ["s3:PutObject", "s3:ListBucket"], "Resource": ["arn:aws:s3:::b", "arn:aws:s3:::b/*"]},
        {"Sid": "VpcOnly", "Effect": "Allow", "Principal": "*", "Action": "s3:*", "Resource": "arn:aws:s3:::b/*",
         "Condition": {"StringEquals": {"aws:SourceVpce": "vpce-123"}}},
        {"Sid": "HttpsOnly", "Effect": "Deny", "Principal": {"AWS": "*"}, "Action": "s3:*", "Resource": "arn:aws:s3:::b/*",
         "Condition": {"Bool": {"aws:SecureTransport": "false"}}},
    ],
}


def policy_for(bucket):
    return json.loads(json.dumps(POLICY).replace("arn:aws:s3:::b", f"arn:aws:s3:::{bucket}"))


def test_explain_policy():
    public, partner, vpc, https = explain_policy(json.dumps(POLICY), own_account="123456789012")
    assert public.public and public.who == ["anyone (public)"] and public.actions == ["read files"]
    assert public.resources == ["s3://b/public/*"] and not public.writes
    assert partner.who == ["role Loader (account 999988887777)", "account 123456789012"]
    assert partner.other_accounts == ["999988887777"] and partner.writes and not partner.anyone
    assert partner.actions == ["upload / overwrite files", "list files"]
    assert partner.resources == ["bucket b", "all files in b"]
    assert vpc.anyone and vpc.restricted and not vpc.public and vpc.conditions == ["VPC endpoint = vpce-123"]
    assert https.effect == "Deny" and https.conditions == ["not over HTTPS"]
    assert explain_policy(None) == []


def test_policy_findings():
    text = " ".join(m for _, m in policy_findings(explain_policy(POLICY, own_account="123456789012")))
    assert "lets anyone on the internet: read files" in text
    assert "999988887777" in text and "only when: VPC endpoint = vpce-123" in text
    assert "plain HTTP" not in text  # the Deny on aws:SecureTransport covers it
    assert any("plain HTTP" in m for _, m in policy_findings(explain_policy({"Statement": [POLICY["Statement"][1]]})))
    [everyone] = explain_policy({"Statement": [{"Effect": "Allow", "NotPrincipal": {"AWS": "arn:aws:iam::1:user/bob"},
                                                "Action": "s3:GetObject", "Resource": "*"}]})
    assert everyone.who == ["everyone except user bob (account 1)"] and everyone.public
    assert any("NotPrincipal" in m for _, m in policy_findings([everyone]))


def test_objects_to_df():
    df = objects_to_df(sample_objects()[:2])
    assert list(df["key"]) == ["p/a/1.csv", "p/a/2.csv"] and "extension" in df.columns


# ------------------------------------------------------------------- AWS (moto)


@pytest.fixture
def aws():
    with mock_aws():
        yield boto3.client("s3", region_name="us-east-1")


@pytest.fixture
def bucket(aws):
    aws.create_bucket(Bucket=BUCKET)

    def put(key, body, **kwargs):
        aws.put_object(Bucket=BUCKET, Key=key, Body=body, **kwargs)

    parquet = io.BytesIO()
    pq.write_table(pa.table({"id": list(range(100)), "value": [i * 1.5 for i in range(100)]}), parquet,
                   row_group_size=10)
    put("raw/", b"")  # console-style folder marker
    put("raw/2024/01/events.csv", CSV, Metadata={"source": "crm"}, Tagging="team=ml", ContentType="text/csv")
    put("raw/2024/01/events-copy.csv", CSV)
    put("raw/2024/02/events.csv.gz", gzip.compress(CSV))
    put("raw/2024/02/empty.txt", b"")
    put("curated/table/part-0.parquet", parquet.getvalue())
    put("curated/records.jsonl", b'{"a": 1, "b": {"c": 2}}\n\n{"a": 2, "b": {"c": 3}}\n')
    put("curated/list.json", json.dumps([{"x": 1}, {"x": 2}]).encode())
    put("curated/config.json", json.dumps({"name": "cfg", "nested": {"k": [1, 2]}}).encode())
    put("curated/lines.json", b'{"a": 1}\n{"a": 2}\n')
    put("docs/readme.md", ("# Title\n" + "line\n" * 50).encode())
    put("docs/blob.bin", bytes(range(256)) * 4)
    put("archive/old.csv", CSV, StorageClass="GLACIER")
    put("big/file.bin", b"x" * (2 * MB))
    return BUCKET


@pytest.fixture
def core(bucket):
    return S3Analyzer(region="us-east-1")


def test_list_buckets_and_region(core, aws):
    aws.create_bucket(Bucket="eu-bucket", CreateBucketConfiguration={"LocationConstraint": "eu-west-1"})
    regions = {b.name: b.region for b in core.list_buckets()}
    assert regions == {BUCKET: "us-east-1", "eu-bucket": "eu-west-1"}
    assert core.bucket_region("s3://eu-bucket/some/key") == "eu-west-1"


def test_ls(core):
    root = core.ls(f"s3://{BUCKET}/")
    assert root.folders == ["archive/", "big/", "curated/", "docs/", "raw/"] and root.objects == []
    raw = core.ls(f"s3://{BUCKET}/raw")  # no trailing slash -> follows into the folder
    assert raw.uri == f"s3://{BUCKET}/raw/" and raw.folders == ["raw/2024/"] and raw.objects == []
    docs = core.ls(f"{BUCKET}/docs/", limit=1)
    assert len(docs.objects) == 1 and docs.truncated


def test_summarize(core):
    s = core.summarize(f"s3://{BUCKET}/", top_n=3)
    assert s.object_count == 13 and s.folder_markers == 1 and s.empty_count == 1
    assert set(s.by_folder) == {"raw/", "curated/", "docs/", "archive/", "big/"}
    assert list(s.by_folder)[0] == "big/"
    assert s.by_storage_class["GLACIER"].count == 1
    assert s.largest[0].key == "big/file.bin"
    assert not s.truncated
    partial = core.summarize(f"s3://{BUCKET}/", limit=3)
    assert partial.truncated and partial.object_count == 3


def test_folder_tree(core):
    tree = core.folder_tree(f"s3://{BUCKET}/raw/", depth=2)
    assert list(tree.folders) == ["2024/", "2024/01/", "2024/02/"]
    assert tree.folders["2024/"].count == 4


def test_find(core):
    uri = f"s3://{BUCKET}/"
    assert sorted(o.key for o in core.find(uri, pattern="*.csv")) == [
        "archive/old.csv", "raw/2024/01/events-copy.csv", "raw/2024/01/events.csv"]
    assert len(core.find(uri, extensions="csv")) == 4
    assert [o.key for o in core.find(uri, min_size="1MB")] == ["big/file.bin"]
    assert [o.key for o in core.find(uri, storage_classes="GLACIER")] == ["archive/old.csv"]
    assert len(core.find(uri, modified_after="1h")) == 13
    assert core.find(uri, modified_before="1h") == []
    assert len(core.find(uri, regex=r"^raw/2024/0[12]/")) == 4
    assert len(core.find(uri, limit=2)) == 2


def test_largest_newest_oldest(core):
    uri = f"s3://{BUCKET}/"
    assert core.largest(uri, 1)[0].key == "big/file.bin"
    assert len(core.newest(uri, 5)) == 5 and len(core.oldest(uri, 5)) == 5
    assert all(not o.key.endswith("/") for o in core.oldest(uri, 20))


def test_find_duplicates(core):
    groups = core.find_duplicates(f"s3://{BUCKET}/")
    assert sorted(o.key for o in groups[0]) == ["archive/old.csv", "raw/2024/01/events-copy.csv",
                                                "raw/2024/01/events.csv"]


def test_compare(core, aws):
    for key in ("raw/2024/01/events.csv", "raw/2024/02/events.csv.gz"):
        aws.copy_object(Bucket=BUCKET, Key="backup/" + key, CopySource={"Bucket": BUCKET, "Key": key})
    aws.put_object(Bucket=BUCKET, Key="backup/raw/2024/01/events-copy.csv", Body=b"changed")
    aws.put_object(Bucket=BUCKET, Key="backup/raw/extra.csv", Body=b"x")
    r = core.compare(f"s3://{BUCKET}/raw/", f"s3://{BUCKET}/backup/raw/")
    assert r.identical == 2
    assert [a.key for a, _ in r.different] == ["raw/2024/01/events-copy.csv"]
    assert [o.key for o in r.only_in_a] == ["raw/2024/02/empty.txt"]
    assert [o.key for o in r.only_in_b] == ["backup/raw/extra.csv"]


def test_versions_and_history(core, aws):
    aws.create_bucket(Bucket="versioned")
    aws.put_bucket_versioning(Bucket="versioned", VersioningConfiguration={"Status": "Enabled"})
    for body in (b"v1", b"v2-longer", b"v3"):
        aws.put_object(Bucket="versioned", Key="doc.txt", Body=body)
    aws.put_object(Bucket="versioned", Key="gone.txt", Body=b"bye")
    aws.delete_object(Bucket="versioned", Key="gone.txt")
    stats = core.version_stats("s3://versioned/")
    assert (stats.current.count, stats.noncurrent.count) == (1, 3)
    assert stats.noncurrent.size == len(b"v1") + len(b"v2-longer") + len(b"bye")
    assert (stats.delete_markers, stats.deleted_keys) == (1, 1)
    assert stats.top_noncurrent[0][0] == "doc.txt"
    assert stats.noncurrent_cost == pytest.approx(stats.noncurrent.size * 0.023 / GB)
    history = core.object_versions("s3://versioned/doc.txt")
    assert [v.size for v in history] == [2, 9, 2] and history[0].is_latest
    assert core.object_versions("s3://versioned/gone.txt")[0].is_delete_marker


def test_incomplete_uploads(core, aws):
    upload = aws.create_multipart_upload(Bucket=BUCKET, Key="uploads/huge.bin")
    aws.upload_part(Bucket=BUCKET, Key="uploads/huge.bin", UploadId=upload["UploadId"], PartNumber=1,
                    Body=b"x" * (5 * MB))
    [found] = core.incomplete_uploads(f"s3://{BUCKET}/uploads/", with_sizes=True)
    assert found.key == "uploads/huge.bin" and found.parts == 1 and found.size == 5 * MB
    assert core.incomplete_uploads(f"s3://{BUCKET}/raw/") == []


def test_head_exists_and_tags(core):
    info = core.head(f"s3://{BUCKET}/raw/2024/01/events.csv")
    assert info["size"] == len(CSV) and info["content_type"] == "text/csv"
    assert info["metadata"] == {"source": "crm"} and info["tags"] == {"team": "ml"}
    assert core.exists(f"s3://{BUCKET}/raw/2024/01/events.csv")
    assert not core.exists(f"s3://{BUCKET}/nope.csv")


def test_readers(core):
    assert core.read_lines(f"s3://{BUCKET}/raw/2024/02/events.csv.gz", 2) == ["id,name,score", "1,a,0.5"]
    assert core.read_text(f"s3://{BUCKET}/raw/2024/02/events.csv.gz", max_bytes=2) == "id"
    assert core.read_bytes(f"s3://{BUCKET}/docs/blob.bin", 1, 3) == bytes([1, 2, 3])
    assert core.read_json(f"s3://{BUCKET}/curated/config.json")["name"] == "cfg"
    assert core.read_jsonl(f"s3://{BUCKET}/curated/records.jsonl") == [{"a": 1, "b": {"c": 2}}, {"a": 2, "b": {"c": 3}}]
    assert core.read_jsonl(f"s3://{BUCKET}/curated/records.jsonl", n=1) == [{"a": 1, "b": {"c": 2}}]
    with core.open(f"s3://{BUCKET}/raw/2024/02/events.csv.gz") as stream:
        assert stream.read() == CSV


def test_read_df(core):
    assert list(core.read_df(f"s3://{BUCKET}/raw/2024/02/events.csv.gz", nrows=2)["name"]) == ["a", "b"]
    assert len(core.read_df(f"s3://{BUCKET}/curated/table/part-0.parquet")) == 100
    head = core.read_df(f"s3://{BUCKET}/curated/table/part-0.parquet", nrows=5, columns=["id"])
    assert list(head.columns) == ["id"] and list(head["id"]) == [0, 1, 2, 3, 4]
    assert list(core.read_df(f"s3://{BUCKET}/curated/records.jsonl").columns) == ["a", "b.c"]
    assert len(core.read_df(f"s3://{BUCKET}/curated/list.json")) == 2
    with pytest.raises(ValueError):
        core.read_df(f"s3://{BUCKET}/docs/blob.bin")


def test_parquet_info(core):
    info = core.parquet_info(f"s3://{BUCKET}/curated/table/part-0.parquet")
    assert info["rows"] == 100 and info["row_groups"] == 10
    assert info["columns"] == [("id", "int64"), ("value", "double")]


@pytest.mark.parametrize("key, kind", [
    ("raw/2024/01/events.csv", "table"),
    ("raw/2024/02/events.csv.gz", "table"),
    ("curated/table/part-0.parquet", "table"),
    ("curated/records.jsonl", "table"),
    ("curated/list.json", "table"),
    ("curated/lines.json", "table"),
    ("curated/config.json", "json"),
    ("docs/readme.md", "text"),
    ("docs/blob.bin", "binary"),
    ("raw/2024/02/empty.txt", "text"),
    ("archive/old.csv", "unavailable"),
])
def test_preview_kinds(core, key, kind):
    assert core.preview(f"s3://{BUCKET}/{key}", n=5).kind == kind


def test_preview_details(core):
    text = core.preview(f"s3://{BUCKET}/docs/readme.md", n=5)
    assert text.data[0] == "# Title" and len(text.data) == 5 and text.truncated
    parquet = core.preview(f"s3://{BUCKET}/curated/table/part-0.parquet", n=5)
    assert len(parquet.data) == 5 and parquet.info["rows"] == 100
    assert core.preview(f"s3://{BUCKET}/curated/list.json").info["records"] == 2


def test_bucket_config(core, aws):
    aws.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
    aws.put_bucket_tagging(Bucket=BUCKET, Tagging={"TagSet": [{"Key": "owner", "Value": "data"}]})
    aws.put_public_access_block(Bucket=BUCKET, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
    aws.put_bucket_lifecycle_configuration(Bucket=BUCKET, LifecycleConfiguration={"Rules": [{
        "ID": "tier", "Status": "Enabled", "Filter": {"Prefix": "raw/"},
        "Transitions": [{"Days": 30, "StorageClass": "STANDARD_IA"}]}]})
    cfg = core.bucket_config(f"s3://{BUCKET}")
    assert cfg.region == "us-east-1" and cfg.versioning == "Enabled"
    assert cfg.tags == {"owner": "data"} and cfg.public_access_block["BlockPublicAcls"] is True
    assert [r["ID"] for r in cfg.lifecycle_rules] == ["tier"]
    assert cfg.object_lock is False
    messages = " ".join(m for _, m in bucket_findings(cfg))
    assert "noncurrent" in messages and "Block Public Access" not in messages


def test_bucket_metrics(core):
    metrics = core.bucket_metrics(BUCKET)  # moto publishes the daily S3 storage metrics
    assert metrics.object_count == 14 and metrics.as_of is not None
    assert "StandardStorage" in metrics.size_by_storage_type and metrics.total_size > 0


def test_bucket_policy(core, aws):
    assert core.bucket_policy(BUCKET) is None
    cfg = core.bucket_config(BUCKET)
    assert cfg.has_policy is False and cfg.policy is None
    aws.put_bucket_policy(Bucket=BUCKET, Policy=json.dumps(policy_for(BUCKET)))
    assert core.bucket_policy(f"s3://{BUCKET}")["Statement"][0]["Sid"] == "PublicRead"
    cfg = core.bucket_config(BUCKET)
    assert cfg.has_policy and cfg.policy["Statement"][1]["Sid"] == "Partner"


def test_account_public_access_block(core):
    assert core.account_id() == "123456789012"
    assert core.account_public_access_block() == {}
    boto3.client("s3control", region_name="us-east-1").put_public_access_block(
        AccountId="123456789012", PublicAccessBlockConfiguration={
            "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
    assert all(core.account_public_access_block().values())


def test_bucket_reports(core, aws):
    aws.create_bucket(Bucket="sagemaker-us-east-1-123456789012")
    reports = core.bucket_reports()
    assert sorted(r.bucket.name for r in reports) == [BUCKET, "sagemaker-us-east-1-123456789012"]
    data_lake = next(r for r in reports if r.bucket.name == BUCKET)
    assert data_lake.config.region == "us-east-1" and data_lake.metrics.object_count == 14
    only = core.bucket_reports(match="sagemaker-*", metrics=False)
    assert [r.bucket.name for r in only] == ["sagemaker-us-east-1-123456789012"] and only[0].metrics is None


def test_deleted_files(core, aws):
    aws.create_bucket(Bucket="versioned")
    aws.put_bucket_versioning(Bucket="versioned", VersioningConfiguration={"Status": "Enabled"})
    for body in (b"v1", b"v2-longer"):
        aws.put_object(Bucket="versioned", Key="docs/report.txt", Body=body)
    aws.delete_object(Bucket="versioned", Key="docs/report.txt")
    aws.put_object(Bucket="versioned", Key="docs/back.txt", Body=b"x")
    aws.delete_object(Bucket="versioned", Key="docs/back.txt")
    aws.put_object(Bucket="versioned", Key="docs/back.txt", Body=b"again")  # re-created, so not deleted
    aws.put_object(Bucket="versioned", Key="docs/purged.txt", Body=b"gone")
    purged = aws.list_object_versions(Bucket="versioned", Prefix="docs/purged.txt")["Versions"][0]["VersionId"]
    aws.delete_object(Bucket="versioned", Key="docs/purged.txt")
    aws.delete_object(Bucket="versioned", Key="docs/purged.txt", VersionId=purged)  # only the marker is left

    found = {f.key: f for f in core.deleted_files("s3://versioned/docs/").files}
    assert set(found) == {"docs/report.txt", "docs/purged.txt"}
    report = found["docs/report.txt"]
    assert report.restorable and report.last_version.size == len(b"v2-longer")
    assert (report.old_versions.count, report.old_versions.size) == (2, len(b"v1") + len(b"v2-longer"))
    assert report.monthly_cost > 0 and not found["docs/purged.txt"].restorable
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    assert core.deleted_files("s3://versioned/", deleted_after=later).files == []
    # The restore the UI suggests: delete the delete marker.
    aws.delete_object(Bucket="versioned", Key="docs/report.txt", VersionId=report.marker_version_id)
    assert aws.get_object(Bucket="versioned", Key="docs/report.txt")["Body"].read() == b"v2-longer"


def test_simulate_lifecycle(core):
    impact = core.simulate_lifecycle(f"s3://{BUCKET}/big/", move_after=0, to="GLACIER")
    assert impact.moves["GLACIER"].count == 1 and impact.monthly_savings > 0
    assert core.simulate_lifecycle(f"s3://{BUCKET}/", move_after=30, to="STANDARD_IA").moves == {}  # all new


def test_presigned_url_and_download(core, tmp_path):
    url = core.presigned_url(f"s3://{BUCKET}/docs/readme.md", expires=60)
    assert BUCKET in url and "readme.md" in url
    path = core.download(f"s3://{BUCKET}/docs/readme.md", str(tmp_path))
    assert path.endswith("readme.md") and open(path).read().startswith("# Title")


# ------------------------------------------------------------------------ formats

FORMATS = "formats"
TABLE = pa.table({"id": list(range(50)), "name": [f"n{i}" for i in range(50)]})


def zip_bytes(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def tar_bytes(files, mode="w:gz"):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode=mode) as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def to_bytes(write, *args, **kwargs):
    buffer = io.BytesIO()
    write(*args, buffer, **kwargs)
    return buffer.getvalue()


@pytest.fixture
def formats(aws):
    aws.create_bucket(Bucket=FORMATS)

    def put(key, body, **kwargs):
        aws.put_object(Bucket=FORMATS, Key=key, Body=body, **kwargs)

    parquet = to_bytes(pq.write_table, TABLE)
    put("tables/t.orc", to_bytes(orc.write_table, TABLE, stripe_size=64))
    put("tables/t.feather", to_bytes(feather.write_feather, TABLE, chunksize=10))
    put("tables/t.avro", avro_bytes()[0])
    put("tables/t.psv", b"a|b\n1|x\n2|y\n")
    excel = io.BytesIO()
    with pd.ExcelWriter(excel) as writer:
        pd.DataFrame({"x": [1, 2, 3]}).to_excel(writer, sheet_name="first", index=False)
        pd.DataFrame({"y": ["a"]}).to_excel(writer, sheet_name="second", index=False)
    put("tables/book.xlsx", excel.getvalue())
    put("tables/matrix.npy", to_bytes(lambda a, f: np.save(f, a), np.arange(300, dtype="float32").reshape(100, 3)))
    put("tables/cube.npy", to_bytes(lambda a, f: np.save(f, a), np.zeros((2, 3, 4))))
    put("tables/objects.npy", to_bytes(lambda a, f: np.save(f, a, allow_pickle=True), np.array([{"a": 1}], dtype=object)))
    put("spark/part-00000", parquet)  # no extension: recognised from its first bytes
    put("firehose/events", gzip.compress(b'{"a": 1}\n{"a": 2}\n'))
    put("logs/app.log.gz", b"plain text, not gzip\nline 2\n")
    put("logs/bracket-log", b"[2024-01-01 10:00] started\n[2024-01-01 10:01] done\n")
    put("archives/data.zip", zip_bytes({"a.csv": b"x\n" * 100, "docs/readme.md": b"# hi", "empty/": b""}))
    put("models/job-1/output/model.tar.gz", tar_bytes({"model.pth": b"w" * 5000, "code/inference.py": b"def model_fn(): pass",
                                                       "config.json": b"{}"}))
    put("archives/plain.tar", tar_bytes({"one.txt": b"1", "two.txt": b"22"}, mode="w"))
    put("models/arrays.npz", to_bytes(lambda f: np.savez_compressed(f, weights=np.ones((4, 5)), bias=np.zeros(5, dtype="int8"))))
    header = json.dumps({"__metadata__": {"format": "pt"}, "layer.weight": {"dtype": "F32", "shape": [2, 3], "data_offsets": [0, 24]},
                         "layer.bias": {"dtype": "F16", "shape": [3], "data_offsets": [24, 30]}}).encode()
    put("models/model.safetensors", len(header).to_bytes(8, "little") + header + bytes(30))
    put("models/model.pth", zip_bytes({"archive/data.pkl": pickle.dumps({"w": 1}), "archive/version": b"3"}))
    put("models/model.pkl", pickle.dumps({"w": 1}))
    notebook = {"nbformat": 4, "metadata": {"kernelspec": {"display_name": "Python 3"}, "language_info": {"name": "python"}},
                "cells": [{"cell_type": "markdown", "source": ["# Title\n", "text"]},
                          {"cell_type": "code", "source": "\nimport s3\nui = s3.S3View()", "outputs": [{}, {}]}]}
    put("notebooks/explore.ipynb", json.dumps(notebook).encode())
    put("media/clip.mp3", b"ID3" + bytes(100))
    put("media/movie.mp4", bytes(64))
    put("docs/report.pdf", pdf_bytes("Hello S3", "Second page", title="Handbook"))
    put("docs/scan.pdf", pdf_bytes(""))
    put("docs/broken.pdf", b"%PDF-1.4\nnot really a pdf")
    put("docs/study.docx", docx_bytes())
    put("docs/deck.pptx", pptx_bytes(DECK))
    put("docs/old.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(600))
    put("docs/attachment-7", docx_bytes())  # no extension: recognised as Word from the parts inside
    return FORMATS


def pdf_bytes(*pages, title=None):
    """A PDF with one page per text (an empty string = a page with no text layer), xref offsets and all."""
    pages = pages or ("",)
    count = len(pages)
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % (4 + 2 * i) for i in range(count)), count),
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for i, text in enumerate(pages):
        stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode() if text else b""
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents %d 0 R "
                       b"/Resources << /Font << /F1 3 0 R >> >> >>" % (5 + 2 * i))
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
    if title:
        objects.append(b"<< /Title (%s) /Author (Kirti) >>" % title.encode())
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    info = b" /Info %d 0 R" % len(objects) if title else b""
    out += b"trailer\n<< /Size %d /Root 1 0 R%s >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, info, xref)
    return bytes(out)


W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
P_NS = ('xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
REL_NS = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
CORE = ('<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{title}</dc:title><dc:creator>Kirti</dc:creator>'
        '</cp:coreProperties>')
APP = '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Pages>{pages}</Pages></Properties>'


def run(text):
    return f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'


def para(text, style=None, numbered=False, outline=None):
    props = (f'<w:pStyle w:val="{style}"/>' if style else "") + \
            ('<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>' if numbered else "") + \
            (f'<w:outlineLvl w:val="{outline}"/>' if outline is not None else "")
    return f"<w:p>{f'<w:pPr>{props}</w:pPr>' if props else ''}{run(text) if text else ''}</w:p>"


DOCX_BODY = "".join([
    para("Churn study", style="Titre"),  # a localized style id; styles.xml names it "Title"
    para("Data", style="Heading1"),  # not in styles.xml: recognised by its id
    '<w:p>' + run("Rows: ") + '<w:r><w:t>2,000</w:t><w:tab/><w:t>ok</w:t></w:r>'
    '<w:del><w:r><w:delText>removed</w:delText></w:r></w:del></w:p>',
    para("direct bullet", numbered=True),
    para("style bullet", style="MyList"),  # its style is based on a numbered style
    '<w:sdt><w:sdtContent>' + para("inside a content control") + '</w:sdtContent></w:sdt>',
    '<w:tbl>' + "".join('<w:tr>' + "".join(f'<w:tc>{para(cell)}</w:tc>' for cell in row) + '</w:tr>'
                        for row in (["plan", "churn"], ["pro", "11%"])) + '</w:tbl>',
    para("Outline level 3", outline=2),
    para(""),
])
DOCX_STYLES = ('<w:style w:type="paragraph" w:styleId="Titre"><w:name w:val="Title"/></w:style>'
               '<w:style w:type="paragraph" w:styleId="ListBase"><w:name w:val="List Base"/>'
               '<w:pPr><w:numPr><w:numId w:val="2"/></w:numPr></w:pPr></w:style>'
               '<w:style w:type="paragraph" w:styleId="MyList"><w:name w:val="My List"/><w:basedOn w:val="ListBase"/></w:style>')


def docx_bytes(body=DOCX_BODY, styles=DOCX_STYLES, title="Churn study", pages=3):
    return zip_bytes({"[Content_Types].xml": "<Types/>",
                      "word/document.xml": f"<w:document {W_NS}><w:body>{body}</w:body></w:document>",
                      "word/styles.xml": f"<w:styles {W_NS}>{styles}</w:styles>",
                      "docProps/core.xml": CORE.format(title=title), "docProps/app.xml": APP.format(pages=pages)})


def shape(text, placeholder=None):
    nv = f'<p:nvPr><p:ph type="{placeholder}"/></p:nvPr>' if placeholder else "<p:nvPr/>"
    paragraphs = "".join(f"<a:p><a:r><a:t>{line}</a:t></a:r></a:p>" for line in text.split("\n"))
    return f'<p:sp><p:nvSpPr><p:cNvPr id="1" name="s"/><p:cNvSpPr/>{nv}</p:nvSpPr><p:txBody>{paragraphs}</p:txBody></p:sp>'


def pptx_bytes(slides, order=None, title="Q3 review"):
    """slides: (title, body, notes, table rows) per slide file; order: slide file numbers as the deck shows them."""
    files = {"[Content_Types].xml": "<Types/>", "docProps/core.xml": CORE.format(title=title)}
    for i, (slide_title, body, notes, table) in enumerate(slides, 1):
        shapes = (shape(slide_title, "title") if slide_title else "") + (shape(body) if body else "")
        if table:
            cells = "".join("<a:tr>" + "".join(f"<a:tc><a:txBody><a:p><a:r><a:t>{c}</a:t></a:r></a:p></a:txBody></a:tc>"
                                               for c in row) + "</a:tr>" for row in table)
            shapes += f"<p:graphicFrame><a:graphic><a:graphicData><a:tbl>{cells}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>"
        files[f"ppt/slides/slide{i}.xml"] = f"<p:sld {P_NS}><p:cSld><p:spTree>{shapes}</p:spTree></p:cSld></p:sld>"
        if notes:
            files[f"ppt/notesSlides/notesSlide{i}.xml"] = (f"<p:notes {P_NS}><p:cSld><p:spTree>{shape('7', 'sldNum')}"
                                                           f"{shape(notes, 'body')}</p:spTree></p:cSld></p:notes>")
            files[f"ppt/slides/_rels/slide{i}.xml.rels"] = (
                f'<Relationships {REL_NS}><Relationship Id="rId2" Target="../notesSlides/notesSlide{i}.xml" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide"/></Relationships>')
    order = order or range(1, len(slides) + 1)
    files["ppt/presentation.xml"] = (f"<p:presentation {P_NS}><p:sldIdLst>"
                                     + "".join(f'<p:sldId id="{255 + i}" r:id="rId{i}"/>' for i in order)
                                     + "</p:sldIdLst></p:presentation>")
    files["ppt/_rels/presentation.xml.rels"] = (
        f"<Relationships {REL_NS}>" + "".join(
            f'<Relationship Id="rId{i}" Target="slides/slide{i}.xml" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"/>'
            for i in range(1, len(slides) + 1)) + "</Relationships>")
    return zip_bytes(files)


DECK = [("Numbers", "", "", [["metric", "value"], ["AUC", "0.91"]]),
        ("Q3 review", "ML platform team", "", None),
        ("Highlights", "Churn down 3 points\nStorage cost -40%", "Mention the lifecycle rule.", None)]


def uri_of(key):
    return f"s3://{FORMATS}/{key}"


@pytest.mark.parametrize("key, kind, fmt", [
    ("tables/t.orc", "table", "orc"),
    ("tables/t.feather", "table", "arrow"),
    ("tables/t.avro", "table", "avro"),
    ("tables/t.psv", "table", "psv"),
    ("tables/book.xlsx", "table", "excel"),
    ("tables/matrix.npy", "table", "npy"),
    ("tables/cube.npy", "text", "npy"),
    ("tables/objects.npy", "binary", "npy"),  # holds a pickle: never loaded
    ("spark/part-00000", "table", "parquet"),
    ("firehose/events", "table", "jsonl"),
    ("logs/app.log.gz", "text", "text"),
    ("logs/bracket-log", "text", "json"),
    ("archives/data.zip", "listing", "zip"),
    ("models/job-1/output/model.tar.gz", "listing", "tar"),
    ("archives/plain.tar", "listing", "tar"),
    ("models/arrays.npz", "listing", "npz"),
    ("models/model.safetensors", "listing", "safetensors"),
    ("models/model.pth", "listing", "torch"),
    ("models/model.pkl", "binary", "pickle"),
    ("notebooks/explore.ipynb", "listing", "notebook"),
    ("media/clip.mp3", "media", "audio"),
    ("media/movie.mp4", "media", "video"),
    ("docs/report.pdf", "document", "pdf"),
    ("docs/study.docx", "document", "docx"),
    ("docs/deck.pptx", "listing", "pptx"),
    ("docs/old.doc", "binary", "oldoffice"),
    ("docs/attachment-7", "document", "docx"),
])
def test_preview_formats(core, formats, key, kind, fmt):
    p = core.preview(uri_of(key), n=5)
    assert (p.kind, p.format) == (kind, fmt), p.note


def test_preview_details_for_new_formats(core, formats):
    orc_preview = core.preview(uri_of("tables/t.orc"), n=5)
    assert len(orc_preview.data) == 5 and orc_preview.info["rows"] == 50 and ("id", "int64") in orc_preview.info["columns"]
    assert core.preview(uri_of("tables/t.feather")).info["batches"] == 5
    avro = core.preview(uri_of("tables/t.avro"), n=4)
    assert len(avro.data) == 4 and avro.info["codec"] == "deflate" and ("score", "null | double") in avro.info["columns"]
    excel = core.preview(uri_of("tables/book.xlsx"))
    assert excel.info["sheets"] == ["first", "second"] and list(excel.data["x"]) == [1, 2, 3]
    matrix = core.preview(uri_of("tables/matrix.npy"), n=5)
    assert matrix.info == {"shape": (100, 3), "dtype": "float32"} and matrix.data.shape == (5, 3)
    assert "pickle" in core.preview(uri_of("tables/objects.npy")).note
    spark = core.preview(uri_of("spark/part-00000"), n=3)
    assert spark.info["rows"] == 50 and len(spark.data) == 3
    firehose = core.preview(uri_of("firehose/events"))
    assert firehose.compression == "gz" and list(firehose.data["a"]) == [1, 2]
    misnamed = core.preview(uri_of("logs/app.log.gz"))
    assert misnamed.data[0] == "plain text, not gzip" and "isn't gz-compressed" in misnamed.note
    assert core.preview(uri_of("logs/bracket-log")).note == ""  # looked like JSON, but it's just a log
    archive = core.preview(uri_of("archives/data.zip"))
    assert [row["name"] for row in archive.data] == ["a.csv", "docs/readme.md"] and archive.info["files"] == "2"
    model = core.preview(uri_of("models/job-1/output/model.tar.gz"))
    assert [row["name"] for row in model.data] == ["model.pth", "code/inference.py", "config.json"]
    assert model.info["unpacked_size"] == 5000 + len(b"def model_fn(): pass") + 2
    arrays = {row["array"]: row for row in core.preview(uri_of("models/arrays.npz")).data}
    assert arrays["weights"]["shape"] == (4, 5) and arrays["bias"]["dtype"] == "int8"
    tensors = core.preview(uri_of("models/model.safetensors"))
    assert tensors.info["parameters"] == 9 and tensors.info["dtypes"] == ["F16", "F32"]
    assert tensors.info["metadata"] == {"format": "pt"}
    assert "weights_only" in core.preview(uri_of("models/model.pth")).note
    assert "unpickling" in core.preview(uri_of("models/model.pkl")).note
    notebook = core.preview(uri_of("notebooks/explore.ipynb"))
    assert notebook.info == {"kernel": "Python 3", "language": "python", "cells": 2}
    assert notebook.data[1] == {"#": 2, "type": "code", "starts with": "import s3", "lines": 3, "outputs": 2}
    clip = core.preview(uri_of("media/clip.mp3"))
    assert clip.data.startswith("https://") and clip.info == {"media": "audio", "mime": "audio/mpeg"}


def test_preview_documents(core, formats):
    pdf = core.preview(uri_of("docs/report.pdf"))
    assert pdf.data == "Hello S3" and pdf.info["pages"] == 2 and pdf.info["title"] == "Handbook"
    assert pdf.info["url"].startswith("https://") and pdf.info["excerpt"] == "Page 1 of 2"
    assert "no text layer" in core.preview(uri_of("docs/scan.pdf")).note
    broken = core.preview(uri_of("docs/broken.pdf"))
    assert broken.kind == "media" and broken.data.startswith("https://")
    word = core.preview(uri_of("docs/study.docx"), n=3)
    assert word.data == "Churn study\n\nData\n\nRows: 2,000\tok" and word.truncated
    assert word.info["words"] == 21 and word.info["headings"] == 3 and word.info["tables"] == 1
    assert word.info["outline"][1] == {"level": 1, "heading": "Data"} and word.info["table"][0] == ["plan", "churn"]
    deck = core.preview(uri_of("docs/deck.pptx"))
    assert deck.data[2] == {"slide": 3, "title": "Highlights", "text": "Churn down 3 points · Storage cost -40%",
                            "words": 8, "notes": "yes"}
    assert deck.info["slides"] == 3 and deck.info["tables"] == 1
    assert "convert-to docx" in core.preview(uri_of("docs/old.doc")).note


def test_preview_pdf_without_pypdf(core, formats, monkeypatch):
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "pypdf" else real_find_spec(name, *a))
    p = core.preview(uri_of("docs/report.pdf"))
    assert p.kind == "media" and p.data.startswith("https://") and "pip install pypdf" in p.note


def test_read_document(core, formats):
    pdf = core.read_document(uri_of("docs/report.pdf"))
    assert pdf.parts == ["Hello S3", "Second page"] and pdf.text == "Hello S3\n\nSecond page"
    assert core.read_pdf(uri_of("docs/report.pdf"), pages=[2]).parts == ["Second page"]
    assert core.read_document(uri_of("docs/deck.pptx"), pages=[1]).slide_titles == ["Numbers"]
    assert core.read_docx(uri_of("docs/study.docx")).headings[0] == (0, "Churn study")
    assert core.read_document(uri_of("docs/attachment-7")).kind == "docx"
    assert core.read_pptx(uri_of("docs/deck.pptx")).notes[2] == "Mention the lifecycle rule."
    with pytest.raises(ValueError, match="no fixed pages"):
        core.read_document(uri_of("docs/study.docx"), pages=[1])
    with pytest.raises(ValueError, match="old binary format"):
        core.read_document(uri_of("docs/old.doc"))
    with pytest.raises(ValueError, match="isn't a PDF"):
        core.read_document(uri_of("tables/t.psv"))


def test_read_df_new_formats(core, formats):
    assert list(core.read_df(uri_of("tables/t.orc"), nrows=3)["id"]) == [0, 1, 2]
    assert core.read_df(uri_of("tables/t.orc"), columns=["name"]).shape == (50, 1)
    assert list(core.read_df(uri_of("tables/t.feather"), nrows=12, columns=["id"]).columns) == ["id"]
    assert len(core.read_df(uri_of("tables/t.feather"))) == 50
    assert core.read_df(uri_of("tables/t.avro"), nrows=2)["address.city"].tolist()[0] == "Pune"
    assert list(core.read_df(uri_of("tables/book.xlsx"), sheet_name="second")["y"]) == ["a"]
    assert list(core.read_df(uri_of("tables/t.psv"))["b"]) == ["x", "y"]
    assert core.read_df(uri_of("tables/matrix.npy"), nrows=4).shape == (4, 3)
    assert len(core.read_df(uri_of("spark/part-00000"), fmt="parquet")) == 50
    assert list(core.read_df(uri_of("firehose/events"), fmt="jsonl", compression="gz")["a"]) == [1, 2]
    with pytest.raises(ValueError):
        core.read_df(uri_of("tables/cube.npy"))
    with pytest.raises(ValueError):
        core.read_df(uri_of("tables/objects.npy"))


def test_read_npy_and_avro(core, formats):
    matrix = core.read_npy(uri_of("tables/matrix.npy"), nrows=2)
    assert matrix.tolist() == [[0, 1, 2], [3, 4, 5]] and core.read_npy(uri_of("tables/matrix.npy")).shape == (100, 3)
    assert core.read_npy(uri_of("tables/cube.npy")).shape == (2, 3, 4)
    records = core.read_avro(uri_of("tables/t.avro"))
    assert len(records) == 500 and core.read_avro(uri_of("tables/t.avro"), n=3) == records[:3]


def test_list_archive(core, formats):
    zipped = core.list_archive(uri_of("archives/data.zip"))
    assert zipped.kind == "zip" and zipped.complete and zipped.total_files == 3
    model = uri_of("models/job-1/output/model.tar.gz")
    full = core.list_archive(model)
    assert full.kind == "tar" and full.complete and full.total_files == 3 and full.bytes_read > 0
    first = core.list_archive(model, limit=1)
    assert [e.name for e in first.entries] == ["model.pth"] and not first.complete and first.total_files is None
    assert not core.list_archive(model, max_bytes=0).complete
    plain = core.list_archive(uri_of("archives/plain.tar"))
    assert [(e.name, e.size) for e in plain.entries] == [("one.txt", 1), ("two.txt", 2)] and plain.bytes_read is None


def test_safetensors_info(core, formats):
    info = core.safetensors_info(uri_of("models/model.safetensors"))
    assert info["tensors"][0] == {"tensor": "layer.weight", "dtype": "F32", "shape": (2, 3), "parameters": 6}
    with pytest.raises(ValueError):
        core.safetensors_info(uri_of("tables/t.psv"))


def test_zstd(core, aws):
    try:
        from compression import zstd

        compress = zstd.compress
    except ImportError:
        zstandard = pytest.importorskip("zstandard", reason="needs Python 3.14+ or the zstandard package")
        compress = zstandard.ZstdCompressor().compress
    aws.create_bucket(Bucket="zstd")
    aws.put_object(Bucket="zstd", Key="t.csv.zst", Body=compress(CSV))
    aws.put_object(Bucket="zstd", Key="noext", Body=compress(b'{"a": 1}\n'))
    assert list(core.read_df("s3://zstd/t.csv.zst")["name"]) == ["a", "b", "c"]
    assert core.preview("s3://zstd/noext").compression == "zst"


# ----------------------------------------------------------------------------- UI


@pytest.fixture
def ui(core):
    return S3View(core, mode="text")


def run(capsys, fn, *args, **kwargs):
    fn(*args, **kwargs)
    return capsys.readouterr().out


def test_ui_text_reports(ui, capsys, aws):
    root = f"s3://{BUCKET}/"
    assert "S3 buckets (1)" in run(capsys, ui.buckets)
    out = run(capsys, ui.summary, root)
    for expected in ("Summary of s3://data-lake/", "File types", "Storage classes", "Largest", "GLACIER", "big/"):
        assert expected in out
    assert "📁 raw/" in run(capsys, ui.ls, root)
    tree = run(capsys, ui.tree, f"{root}raw/", depth=2)
    assert "2024/" in tree and "    01/" in tree
    assert "big/file.bin" in run(capsys, ui.find, root, min_size="1MB")
    assert "big/file.bin" in run(capsys, ui.largest, root, 1)
    assert "Reclaimable" in run(capsys, ui.duplicates, root)
    assert "In sync" in run(capsys, ui.compare, f"{root}raw/", f"{root}raw/")
    assert "Noncurrent versions" in run(capsys, ui.versions, root)
    assert "Uploads" in run(capsys, ui.uploads, root)
    assert "source" in run(capsys, ui.head, f"{root}raw/2024/01/events.csv")
    assert "Schema" in run(capsys, ui.preview, f"{root}curated/table/part-0.parquet")
    assert '"nested"' in run(capsys, ui.preview, f"{root}curated/config.json")
    assert "00000000" in run(capsys, ui.preview, f"{root}docs/blob.bin")
    assert "https://" in run(capsys, ui.link, f"{root}docs/readme.md")
    assert "Block public access" in run(capsys, ui.bucket_info, BUCKET)
    assert "summary(" in run(capsys, ui.help)


def test_ui_cost_policy_overview_what_if_deleted(ui, capsys, aws):
    root = f"s3://{BUCKET}/"
    assert "Est. cost / month" in run(capsys, ui.summary, root)
    assert "Est. $/month" in run(capsys, ui.bucket_info, BUCKET)
    assert "no bucket policy" in run(capsys, ui.policy, BUCKET)
    aws.put_bucket_policy(Bucket=BUCKET, Policy=json.dumps(policy_for(BUCKET)))
    out = run(capsys, ui.policy, BUCKET)
    assert "lets anyone on the internet" in out and "Policy JSON" in out and "999988887777" in out
    assert "PublicRead" in run(capsys, ui.bucket_info, BUCKET)
    overview = run(capsys, ui.overview)
    assert "All buckets (1)" in overview and "Buckets by size" in overview and "Warnings" in overview
    what_if = run(capsys, ui.what_if, root, move_after=0, to="GLACIER")
    assert "move to GLACIER" in what_if and '"StorageClass": "GLACIER"' in what_if and "under 128 KB" in what_if
    small = run(capsys, ui.what_if, f"{root}raw/", move_after=0, to="GLACIER")  # old enough, but all under 128 KB
    assert "under 128 KB" in small and "Nothing under this prefix" not in small
    assert "move_after= needs to=" in run(capsys, ui.what_if, root, move_after=30)
    assert "Versioning is off" in run(capsys, ui.deleted, root)
    aws.create_bucket(Bucket="versioned")
    aws.put_bucket_versioning(Bucket="versioned", VersioningConfiguration={"Status": "Enabled"})
    aws.put_object(Bucket="versioned", Key="a.txt", Body=b"a")
    aws.delete_object(Bucket="versioned", Key="a.txt")
    deleted = run(capsys, ui.deleted, "s3://versioned/")
    assert "How to restore a file" in deleted and "Key='a.txt'" in deleted


def test_ui_previews_new_formats(ui, capsys, formats):
    model = run(capsys, ui.preview, uri_of("models/job-1/output/model.tar.gz"))
    assert "-- Files --" in model and "model.pth" in model and "Unpacked size" in model
    assert "-- Cells --" in run(capsys, ui.preview, uri_of("notebooks/explore.ipynb"))
    tensors = run(capsys, ui.preview, uri_of("models/model.safetensors"))
    assert "Parameters: 9" in tensors and "layer.weight" in tensors and "-- Metadata --" in tensors
    assert "https://" in run(capsys, ui.preview, uri_of("media/clip.mp3"))
    assert "Sheets: first, second" in run(capsys, ui.preview, uri_of("tables/book.xlsx"))
    assert "isn't gz-compressed" in run(capsys, ui.preview, uri_of("logs/app.log.gz"))
    assert "Codec: deflate" in run(capsys, ui.preview, uri_of("tables/t.avro"))
    word = run(capsys, ui.preview, uri_of("docs/study.docx"))
    assert "Words: 21" in word and "-- Outline --" in word and "-- First table --" in word and "11%" in word
    assert "-- Slides --" in run(capsys, ui.preview, uri_of("docs/deck.pptx"))
    assert "old binary format" in run(capsys, ui.preview, uri_of("docs/old.doc"))
    pdf = run(capsys, ui.document, uri_of("docs/report.pdf"))
    assert "-- Page 1 --" in pdf and "Second page" in pdf and "Title: Handbook" in pdf
    deck = run(capsys, ui.document, uri_of("docs/deck.pptx"), pages=[3])
    assert "-- Slide 3: Highlights --" in deck and "Speaker notes:" in deck
    assert "-- Text --" in run(capsys, ui.document, uri_of("docs/study.docx"))
    assert "Stopped after 5 characters" in run(capsys, ui.document, uri_of("docs/report.pdf"), max_chars=5)
    assert "old binary format" in run(capsys, ui.document, uri_of("docs/old.doc"))


def test_ui_turns_errors_into_notes(ui, capsys):
    out = run(capsys, ui.head, f"s3://{BUCKET}/missing.csv")
    assert "[!]" in out and "not found" in out
    assert "NoSuchBucket" in run(capsys, ui.ls, "s3://no-such-bucket/")
    assert "ValueError" in run(capsys, ui.find, f"s3://{BUCKET}/", min_size="lots")


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
    ui = S3View(core, mode="html")
    ui.summary(f"s3://{BUCKET}/")
    ui.preview(f"s3://{BUCKET}/raw/2024/01/events.csv")
    ui.overview()
    ui.what_if(f"s3://{BUCKET}/", move_after=0, to="GLACIER")
    aws = boto3.client("s3", region_name="us-east-1")
    aws.put_object(Bucket=BUCKET, Key="media/clip.mp3", Body=b"ID3")
    aws.put_object(Bucket=BUCKET, Key="media/model.tar.gz", Body=tar_bytes({"model.pth": b"w"}))
    ui.preview(f"s3://{BUCKET}/media/clip.mp3")
    ui.preview(f"s3://{BUCKET}/media/model.tar.gz")
    html_out = "".join(shown)
    assert '<audio controls preload="metadata"><source src="https://' in html_out and "model.pth" in html_out
    assert '<div class="s3a">' in html_out and 'class="fill"' in html_out and "<table" in html_out


def test_html_escapes_untrusted_keys():
    blocks = [s3mod._Title("<b>x</b>"), s3mod._Table(["Key"], [["<script>alert(1)</script>"]])]
    rendered = s3mod._render_html(blocks, 50)
    assert "<script>alert" not in rendered and "&lt;script&gt;" in rendered


def test_text_tables_cap_rows():
    table = s3mod._Table(["n"], [[str(i)] for i in range(10)])
    out = s3mod._render_text([table], 3)
    assert "7 more rows" in out
