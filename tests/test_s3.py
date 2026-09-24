import gzip
import io
import json
from datetime import datetime, timedelta, timezone

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from moto import mock_aws

import s3 as s3mod
from s3 import (
    GB,
    MB,
    TB,
    BucketConfig,
    ObjectInfo,
    S3Analyzer,
    S3View,
    build_folder_tree,
    bucket_findings,
    compare_objects,
    detect_format,
    file_extension,
    find_duplicate_groups,
    folder_of,
    human_size,
    make_filter,
    objects_to_df,
    parse_s3_uri,
    parse_size,
    parse_time,
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
])
def test_detect_format(key, expected):
    assert detect_format(key) == expected


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


def test_presigned_url_and_download(core, tmp_path):
    url = core.presigned_url(f"s3://{BUCKET}/docs/readme.md", expires=60)
    assert BUCKET in url and "readme.md" in url
    path = core.download(f"s3://{BUCKET}/docs/readme.md", str(tmp_path))
    assert path.endswith("readme.md") and open(path).read().startswith("# Title")


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
    html_out = "".join(shown)
    assert '<div class="s3a">' in html_out and 'class="fill"' in html_out and "<table" in html_out


def test_html_escapes_untrusted_keys():
    blocks = [s3mod._Title("<b>x</b>"), s3mod._Table(["Key"], [["<script>alert(1)</script>"]])]
    rendered = s3mod._render_html(blocks, 50)
    assert "<script>alert" not in rendered and "&lt;script&gt;" in rendered


def test_text_tables_cap_rows():
    table = s3mod._Table(["n"], [[str(i)] for i in range(10)])
    out = s3mod._render_text([table], 3)
    assert "7 more rows" in out
