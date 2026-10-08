import asyncio
import json
import sys
import threading
import zipfile
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

import s3 as s3mod
import s3_explorer as sx
from s3 import S3Analyzer, S3View
from s3_explorer import (
    Entry,
    S3Explorer,
    S3Navigator,
    breadcrumbs,
    count_types,
    entry_icon,
    explain_list_error,
    filter_entries,
    folder_stats,
    folder_uri,
    parent_uri,
    parse_filter,
    parse_location,
    sort_entries,
)

NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)
LAKE = "lake"


def file(key, size=1, days=0, storage="STANDARD"):
    return Entry("file", LAKE, key, size, NOW - timedelta(days=days), storage)


def folder(key):
    return Entry("folder", LAKE, key)


# ----------------------------------------------------------------------------- pure functions


@pytest.mark.parametrize("text, expected", [
    ("s3://lake/raw/events/", ("lake", "raw/events/")),
    ("lake/raw/x.csv", ("lake", "raw/x.csv")),
    ("  s3a://lake  ", ("lake", "")),
    ("", ("", "")),
    (None, ("", "")),
    ("s3://", ("", "")),
    ("/", ("", "")),
    ("s3://lake//data/x", ("lake", "/data/x")),
    ("https://us-east-1.console.aws.amazon.com/s3/buckets/lake?region=us-east-1&prefix=raw/events/&showversions=false",
     ("lake", "raw/events/")),
    ("https://s3.console.aws.amazon.com/s3/object/lake?region=us-east-1&prefix=raw/a%20b.csv", ("lake", "raw/a b.csv")),
    ("https://s3.console.aws.amazon.com/s3/buckets/lake", ("lake", "")),
    ("https://lake.s3.us-east-1.amazonaws.com/raw/x.csv", ("lake", "raw/x.csv")),
    ("https://my.lake.s3.amazonaws.com/x", ("my.lake", "x")),
    ("https://s3.us-west-2.amazonaws.com/lake/raw/x.csv", ("lake", "raw/x.csv")),
])
def test_parse_location(text, expected):
    assert parse_location(text) == expected


def test_folder_uri_parent_and_breadcrumbs():
    assert folder_uri("lake", "raw") == "s3://lake/raw/" and folder_uri("lake") == "s3://lake/" and folder_uri("") == ""
    assert parent_uri("s3://lake/raw/events/") == "s3://lake/raw/"
    assert parent_uri("s3://lake/raw/") == "s3://lake/"
    assert parent_uri("s3://lake/") == "" and parent_uri("") == ""
    assert parent_uri("s3://lake//x/") == "s3://lake//"  # keys that start with '/' make a folder with no name
    assert parent_uri("s3://lake//") == "s3://lake/"
    assert breadcrumbs("") == [("All buckets", "")]
    assert breadcrumbs("s3://lake/raw/events/") == [
        ("All buckets", ""), ("lake", "s3://lake/"), ("raw", "s3://lake/raw/"), ("events", "s3://lake/raw/events/")]
    assert breadcrumbs("s3://lake//x/")[2:] == [("(no name)", "s3://lake//"), ("x", "s3://lake//x/")]


def test_entry_names_and_icons():
    assert file("raw/events/a.json").name == "a.json"
    assert folder("raw/events/").name == "events" and folder("/").name == ""
    assert Entry("bucket", LAKE).name == LAKE and Entry("bucket", LAKE).uri == "s3://lake/"
    icons = {key: entry_icon(file(key)) for key in (
        "a.csv.gz", "b.snappy.parquet", "model.tar.gz", "r.pdf", "w.docx", "m.safetensors", "p.png", "README", "x.weird")}
    assert icons == {"a.csv.gz": "📊", "b.snappy.parquet": "📊", "model.tar.gz": "📦", "r.pdf": "📕", "w.docx": "📘",
                     "m.safetensors": "🧠", "p.png": "🖼️", "README": "📄", "x.weird": "📄"}
    assert entry_icon(folder("a/")) == "📁" and entry_icon(Entry("bucket", LAKE)) == "🪣"
    assert file("x", storage="DEEP_ARCHIVE").archived and not file("x", storage="GLACIER_IR").archived


def test_sort_entries():
    entries = [file("part-10.csv", 5, 1), folder("b/"), file("part-2.csv", 50, 3), folder("A/"), file("Part-1.csv", 9, 2)]
    names = [e.name for e in sort_entries(entries)]
    assert names == ["A", "b", "Part-1.csv", "part-2.csv", "part-10.csv"]  # natural order, folders first
    assert [e.name for e in sort_entries(entries, "name", True)] == ["b", "A", "part-10.csv", "part-2.csv", "Part-1.csv"]
    assert [e.name for e in sort_entries(entries, "size", True)] == ["A", "b", "part-2.csv", "Part-1.csv", "part-10.csv"]
    assert [e.name for e in sort_entries(entries, "modified", True)][2:] == ["part-10.csv", "Part-1.csv", "part-2.csv"]
    assert [e.name for e in sort_entries(entries, "modified", True)][:2] == ["A", "b"]  # folders have no date
    buckets = [Entry("bucket", "old", modified=NOW - timedelta(days=9)), Entry("bucket", "new", modified=NOW)]
    assert [e.name for e in sort_entries(buckets, "modified", True)] == ["new", "old"]
    with pytest.raises(ValueError):
        sort_entries(entries, "colour")
    tree = [file("d/b/x.csv"), folder("d/b/"), file("d/a-2.csv"), folder("d/a/"), file("d/a/part-10.csv"),
            file("d/a/part-2.csv")]
    assert [e.key for e in sort_entries(tree, "path")] == [  # each folder, then what's in it
        "d/a/", "d/a/part-2.csv", "d/a/part-10.csv", "d/a-2.csv", "d/b/", "d/b/x.csv"]
    assert [e.key for e in sort_entries(tree, "path", True)][:2] == ["d/b/x.csv", "d/b/"]


def test_filter_entries():
    entries = [file("raw/Report-2024.csv"), file("raw/notes.txt"), folder("raw/reports/")]
    assert [e.name for e in filter_entries(entries, "REPORT")] == ["Report-2024.csv", "reports"]
    assert [e.name for e in filter_entries(entries, "*.txt")] == ["notes.txt"]
    assert filter_entries(entries, "  ") == entries


def test_parse_filter():
    wanted = parse_filter("  Train, .CSV *.json ext:parquet part-0* .csv type:.YML")
    assert wanted.words == ["train"] and wanted.patterns == ["part-0*"]
    assert wanted.types == ["csv", "json", "parquet", "yaml"]  # each once; .yml is .yaml
    assert parse_filter(".jpeg").types == ["jpg"] and parse_filter("*.csv.gz").types == ["csv.gz"]
    assert parse_filter("report.pdf").words == ["report.pdf"]  # a name, not a type
    assert parse_filter(".").words == ["."] and not parse_filter("") and not parse_filter(None)
    assert parse_filter("a b").words == ["a", "b"]  # every word must be in the name


def test_filter_entries_by_type_and_kind():
    entries = [file("d/a.csv"), file("d/b.CSV.gz"), file("d/c.json"), file("d/p.jpeg"), file("d/part-0.snappy.parquet"),
               file("d/model.tar.gz"), file("d/.env"), file("d/README"), folder("d/csv/"), Entry("bucket", "b")]
    names = lambda text, kind="all": [e.name for e in filter_entries(entries, text, kind)]  # noqa: E731
    assert names(".csv") == ["a.csv", "b.CSV.gz"]  # .csv also finds compressed csv; folders have no type
    assert names(".csv .json") == ["a.csv", "b.CSV.gz", "c.json"]
    assert names(".gz") == ["b.CSV.gz", "model.tar.gz"] and names(".tar.gz") == ["model.tar.gz"]
    assert names(".jpg") == ["p.jpeg"] and names(".parquet") == ["part-0.snappy.parquet"] and names(".env") == [".env"]
    assert names("b .csv") == ["b.CSV.gz"] and names("csv") == ["a.csv", "b.CSV.gz", "csv"]
    assert names("", "folders") == ["csv", "b"] and names("", "folder") == ["csv", "b"]  # buckets count as folders
    assert names("a", "files") == ["a.csv", "part-0.snappy.parquet", "model.tar.gz", "README"]
    with pytest.raises(ValueError, match="kind must be"):
        filter_entries(entries, "", "colour")


def test_count_types():
    entries = [file("a.csv", 10), file("b.csv.gz", 5), file("c.json", 100), file("d.jpeg", 1), file("e.jpg", 1),
               file("README", 50), folder("x/")]
    assert count_types(entries) == [("csv", 2, 15), ("jpg", 2, 2), ("json", 1, 100)]
    assert count_types([]) == []


def test_folder_stats():
    entries = [folder("d/x/"), file("d/a.csv", 100, 5), file("d/b.csv", 300, 1), file("d/c.json", 50, 9),
               file("d/old.csv.gz", 1000, 400, "GLACIER"), file("d/README", 7, 2)]
    stats = folder_stats(entries)
    assert (stats.folders, stats.files, stats.size, stats.archived) == (1, 5, 1457, 1)
    assert stats.newest == NOW - timedelta(days=1) and stats.oldest == NOW - timedelta(days=400)
    assert stats.types[:3] == [("csv.gz", 1, 1000), ("csv", 2, 400), ("json", 1, 50)]
    assert ("(no extension)", 1, 7) in stats.types
    assert folder_stats([]).files == 0


def test_explain_list_error():
    assert "s3:ListBucket" in explain_list_error("AccessDenied", "lake", "raw/")
    assert "arn:aws:s3:::lake" in explain_list_error("AccessDenied", "lake")
    assert "s3:ListAllMyBuckets" in explain_list_error("AccessDenied", "")
    assert "no bucket named 'ghost'" in explain_list_error("NoSuchBucket", "ghost")
    assert "credentials" in explain_list_error("ExpiredToken", "lake")


# ----------------------------------------------------------------------------- navigator (moto)


@pytest.fixture
def aws():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=LAKE)
        client.create_bucket(Bucket="models")
        for key, body in {
            "raw/": b"",  # a folder marker the console makes
            "raw/events/part-10.csv": b"a,b\n1,2\n",
            "raw/events/part-2.csv": b"a,b\n3,4\n",
            "raw/readme.md": b"# Raw data\n\nOne file per day.\n",
            "curated/table.json": b'{"a": 1}',
            "curated/old.csv.gz": b"x",
            "top.txt": b"hello",
        }.items():
            client.put_object(Bucket=LAKE, Key=key, Body=body)
        for i in range(25):
            client.put_object(Bucket=LAKE, Key=f"many/f{i:02d}.txt", Body=b"x" * i)
        yield client


@pytest.fixture
def core(aws):
    return S3Analyzer(region="us-east-1")


def test_navigator_lists_buckets_folders_and_files(core):
    nav = S3Navigator(core)
    assert [e.name for e in nav.open().entries] == [LAKE, "models"] and nav.location == ""
    root = nav.open("s3://lake/")
    assert [(e.kind, e.name) for e in sort_entries(root.entries)] == [
        ("folder", "curated"), ("folder", "many"), ("folder", "raw"), ("file", "top.txt")]
    raw = nav.open("s3://lake/raw/")
    assert [e.name for e in raw.entries] == ["events", "readme.md"]  # not the raw/ marker itself
    assert raw.requests == 1 and not raw.more and not raw.error


def test_navigator_resolves_paths(core):
    nav = S3Navigator(core)
    nav.open("lake/raw")  # no slash: the folder raw/
    assert nav.location == "s3://lake/raw/" and not nav.focus
    nav.open("s3://lake/raw/events/part-2.csv")  # a file: its folder, with the file in focus
    assert nav.location == "s3://lake/raw/events/" and nav.focus == "s3://lake/raw/events/part-2.csv"
    nav.open("s3://lake/raw/nope")
    assert nav.location == "s3://lake/raw/" and "no file or folder named 'nope'" in nav.notice
    nav.open("s3://lake/top.txt")
    assert nav.location == "s3://lake/" and nav.focus == "s3://lake/top.txt"
    nav.open("https://s3.console.aws.amazon.com/s3/buckets/lake?prefix=curated/")
    assert nav.location == "s3://lake/curated/"


def test_navigator_history(core):
    nav = S3Navigator(core)
    nav.open("s3://lake/raw/")
    assert not nav.can_back  # where it started isn't history
    nav.open("s3://lake/raw/events/")
    nav.up()
    assert nav.location == "s3://lake/raw/"
    nav.back()
    assert nav.location == "s3://lake/raw/events/" and nav.can_forward
    nav.forward()
    assert nav.location == "s3://lake/raw/"
    nav.up()
    nav.up()
    assert nav.location == "" and nav.up().uri == ""  # every bucket is the top
    nav.open("s3://lake/raw/")  # going somewhere new drops forward
    assert not nav.can_forward and nav.can_back


def test_navigator_caches_until_refresh(core, aws):
    nav = S3Navigator(core)
    first = nav.open("s3://lake/raw/")
    aws.put_object(Bucket=LAKE, Key="raw/new.txt", Body=b"new")
    nav.open("s3://lake/")
    assert nav.back() is first and "new.txt" not in [e.name for e in first.entries]
    assert "new.txt" in [e.name for e in nav.refresh().entries]


def test_navigator_pages_and_lookup(core):
    nav = S3Navigator(core, page_size=10)
    many = nav.open("s3://lake/many/")
    assert len(many.entries) == 10 and many.more and many.requests == 1
    assert nav.lookup("f2") == 5  # f20..f24 weren't loaded yet
    nav.more()
    nav.more()
    assert not many.more and many.requests == 4
    assert sorted(e.name for e in many.entries) == [f"f{i:02d}.txt" for i in range(25)]  # nothing twice
    assert nav.lookup("") == 0 and nav.lookup("zzz") == 0


def test_navigator_lists_the_rest_of_a_folder(core):
    nav = S3Navigator(core, page_size=10, list_limit=20)
    many = nav.open("s3://lake/many/")
    assert many.listed == 10 and many.more
    seen = []
    assert nav.list_rest(progress=seen.append) is many  # up to list_limit
    assert len(many.entries) == 20 and many.listed == 20 and many.more and seen == [20] and many.requests == 2
    nav.list_rest(limit=100)
    assert len(many.entries) == 25 and not many.more and many.requests == 3
    assert nav.list_rest() is many and many.requests == 3  # nothing left to list
    raw = nav.list_rest("s3://lake/raw/")  # another folder: its first page is all there is
    assert len(raw.entries) == 2 and raw.requests == 1


def test_navigator_forgets_old_folders_past_a_size(core, monkeypatch):
    monkeypatch.setattr(sx, "_CACHED_ENTRIES", 20)
    nav = S3Navigator(core)
    many = nav.open("s3://lake/many/")
    nav.open("s3://lake/raw/")  # 25 entries kept is over 20: the oldest folder goes
    again = nav.back()
    assert again is not many and len(again.entries) == 25 and nav.open("s3://lake/raw/").requests == 1


def test_navigator_lists_everything_below(core, aws):
    nav = S3Navigator(core)
    nav.open("s3://lake/")
    below = nav.below()
    assert below.deep and not below.more and below.requests == 1 and nav.below() is below  # cached
    keys = {e.key: e.kind for e in below.entries}
    assert keys["raw/"] == "folder" and keys["raw/events/"] == "folder" and keys["curated/"] == "folder"
    assert keys["raw/events/part-2.csv"] == "file" and keys["top.txt"] == "file"
    assert sum(kind == "file" for kind in keys.values()) == 31  # the raw/ marker is a folder, not a file
    raw = nav.below("s3://lake/raw/")
    assert sorted(e.key for e in raw.entries) == [
        "raw/events/", "raw/events/part-10.csv", "raw/events/part-2.csv", "raw/readme.md"]
    assert nav.below("") is nav.folder("")  # on the list of buckets, it's the buckets

    nav = S3Navigator(core, page_size=10, deep_limit=12)
    nav.open("s3://lake/many/")
    seen = []
    many = nav.below(progress=seen.append)
    assert len(many.entries) == 12 and many.more and many.requests == 2 and seen == [10, 12]
    nav.more(below=True)
    nav.more(below=True)
    assert len(many.entries) == 25 and not many.more and many.requests == 5
    aws.put_object(Bucket=LAKE, Key="many/new.txt", Body=b"x")
    nav.refresh()
    assert len(nav.below().entries) == 12 and nav.below() is not many  # listed again

    ghost = S3Navigator(core).below("s3://ghost-bucket/")
    assert ghost.error_code == "NoSuchBucket" and not ghost.entries


def test_navigator_records_listing_errors(core, monkeypatch):
    nav = S3Navigator(core)
    ghost = nav.open("s3://ghost-bucket/")
    assert ghost.error_code == "NoSuchBucket" and not ghost.entries

    def denied(**_):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}, "ListObjectsV2")

    monkeypatch.setattr(core.client, "list_objects_v2", denied)
    secret = nav.open("s3://lake/secret/")
    assert secret.error == "AccessDenied: Access Denied" and nav.location == "s3://lake/secret/"


def test_navigator_takes_a_view_or_settings(core):
    assert S3Navigator(S3View(core, mode="text")).core is core
    assert isinstance(S3Navigator(region="us-east-1").core, S3Analyzer)


# ----------------------------------------------------------------------------- explorer (widgets)


@pytest.fixture
def explorer(core, monkeypatch):
    monkeypatch.setattr(sx, "_CLICK_GRACE", 0)

    def make(uri="", **kwargs):
        return S3Explorer(uri, core=core, mode="widgets", progress="off", **kwargs)

    return make


def rows(x):
    return [row.button.description for row in x._pool[: len(x._rows_box.children)]]


def row(x, name):
    return next(r for r in x._pool[: len(x._rows_box.children)] if r.entry.name == name)


def text(x):
    return s3mod._render_text(x.shown, 0)


def test_explorer_opens_folders_and_files_by_clicking(explorer):
    x = explorer()
    assert rows(x) == ["🪣  lake", "🪣  models"] and x._back_btn.disabled and x._up_btn.disabled
    assert "Your buckets" in text(x) and "2 buckets" in x._status.value
    row(x, LAKE).button.click()
    assert x.location == "s3://lake/" and rows(x) == ["📁  curated", "📁  many", "📁  raw", "📄  top.txt"]
    assert [b.description for b in x._crumbs.children if hasattr(b, "on_click")] == ["All buckets", "lake"]
    row(x, "raw").button.click()
    row(x, "events").button.click()
    assert rows(x) == ["📊  part-2.csv", "📊  part-10.csv"]
    assert [(r.size.value, r.age.value) for r in x._pool[:2]] == [("8 B", "just now")] * 2
    assert not x._back_btn.disabled and not x._up_btn.disabled and x._fwd_btn.disabled

    row(x, "part-2.csv").button.click()
    assert x.selected == "s3://lake/raw/events/part-2.csv"
    assert row(x, "part-2.csv").button._dom_classes == ("s3x-row", "s3x-on")
    assert row(x, "part-10.csv").button._dom_classes == ("s3x-row",)
    out = text(x)
    assert "Preview of part-2.csv" in out and "s3://lake/raw/events/part-2.csv" not in out  # the name, not the path
    assert "Next:" not in out  # the S3View's next steps name commands this explorer doesn't have
    assert "s3://lake/raw/events/part-2.csv" in x._status.value
    assert list(x._act_buttons) == ["preview", "head", "download", "open", "close"]
    assert x._act_buttons["preview"]._dom_classes == ("s3x-act", "s3x-on")
    assert 'class="s3a"' in x._content.value and "<table" in x._content.value

    x._act_buttons["head"].click()
    assert "System metadata" in text(x) and x._act_buttons["head"]._dom_classes == ("s3x-act", "s3x-on")
    link = x._act_buttons["open"].value  # a link the browser opens, not a report
    assert link.startswith('<a class="s3x-act" href="https://') and 'target="_blank"' in link
    assert "part-2.csv?" in link and "response-content-disposition=inline" in link and "↗ Open in new tab" in link
    assert x._act_buttons["head"]._dom_classes == ("s3x-act", "s3x-on")
    x._act_buttons["close"].click()
    assert not x.selected and "File types here" in text(x) and list(x._act_buttons) == ["summary", "zip"]


def test_explorer_toolbar_and_breadcrumbs(explorer):
    x = explorer("s3://lake/raw/events/")
    x._up_btn.click()
    assert x.location == "s3://lake/raw/"
    x._back_btn.click()
    assert x.location == "s3://lake/raw/events/"
    x._fwd_btn.click()
    assert x.location == "s3://lake/raw/"
    crumbs = [b for b in x._crumbs.children if hasattr(b, "on_click")]
    assert [b.description for b in crumbs] == ["All buckets", "lake", "raw"]
    crumbs[1].click()
    assert x.location == "s3://lake/"
    [b for b in x._crumbs.children if hasattr(b, "on_click")][0].click()
    assert x.location == "" and rows(x)[0] == "🪣  lake" and list(x._act_buttons) == ["overview"]


def test_explorer_path_box(explorer):
    x = explorer("s3://lake/")
    x._edit_btn.click()
    assert x._path.layout.display is None and x._crumbs.layout.display == "none" and x._path.value == "s3://lake/"
    x._path.value = "s3://lake/curated/"  # typing (or leaving the box) doesn't navigate; ✕ cancels
    x._edit_btn.click()
    assert x.location == "s3://lake/" and x._path.layout.display == "none"
    x._edit_btn.click()
    x._path.value = "https://s3.console.aws.amazon.com/s3/object/lake?prefix=raw/readme.md"
    x._path._handle_custom_msg({"event": "submit"}, [])  # what the box sends on Enter
    assert x.location == "s3://lake/raw/" and x.selected == "s3://lake/raw/readme.md"
    assert x._path.layout.display == "none" and x._crumbs.layout.display is None
    assert "One file per day." in text(x)


def pages(x):
    """The bar under the list: its text, and which of « ‹ › » can be clicked."""
    return x._range.value, [name for name, b in x._page_btns.items() if not b.disabled]


def test_explorer_sort_filter_and_pages(explorer):
    x = explorer("s3://lake/many/", page_size=10)
    assert len(rows(x)) == 10 and x._pages.layout.display is None and "Show" not in x._foot_note.value
    assert pages(x) == ('<span title="Page 1 of 3"><b>1–10</b> of 25</span>', ["next", "last"])
    x._page_btns["next"].click()
    assert rows(x)[0] == "📄  f10.txt" and pages(x)[1] == ["first", "previous", "next", "last"]
    x._page_btns["last"].click()
    assert rows(x) == [f"📄  f2{i}.txt" for i in range(5)] and "<b>21–25</b> of 25" in pages(x)[0]
    assert pages(x)[1] == ["first", "previous"] and len(x._rows_box.children) == 5  # never more rows than a page
    x._page_btns["previous"].click()
    assert rows(x)[0] == "📄  f10.txt"
    x._page_btns["first"].click()
    assert rows(x)[0] == "📄  f00.txt"
    x._page_btns["last"].click()
    x._cols["size"].click()  # a new order starts on its first page
    assert rows(x)[0] == "📄  f24.txt" and x._cols["size"].description == "Size ↓" and len(rows(x)) == 10
    assert "s3x-on" in x._cols["size"]._dom_classes and "s3x-on" not in x._cols["name"]._dom_classes
    x._cols["size"].click()
    assert rows(x)[0] == "📄  f00.txt" and x._cols["size"].description == "Size ↑"
    x._cols["name"].click()
    assert x._cols["name"].description == "Name ↑" and x._cols["size"].description == "Size"
    x._filter.value = "f1"
    assert rows(x) == [f"📄  f1{i}.txt" for i in range(10)] and "10 match “f1”" in x._status.value
    assert x._pages.layout.display == "none"  # one page: no bar
    order = x._order[2]
    x._filter.value = "f"
    assert x._order[2] is order  # typing filters the sorted list it has; it doesn't sort again
    x._filter.value = "nothing-like-this"
    assert rows(x) == [] and "Nothing here matches" in x._foot_note.value
    x.open("s3://lake/raw/")
    assert x._filter.value == "" and len(rows(x)) == 2  # a new folder starts unfiltered


def kinds(x):
    return [b.description for b in x._kind_btns.values()]


def chips(x):
    return [(b.description, "s3x-on" in b._dom_classes) for b in x._types.children]


def chip(x, kind):
    return next(b for b in x._types.children if f" .{kind} " in b.description)


def test_explorer_shows_only_folders_or_files(explorer):
    x = explorer("s3://lake/")
    assert kinds(x) == ["All 4", "Folders 3", "Files 1"] and x._kind_btns["all"]._dom_classes == ("s3x-seg", "s3x-on")
    x._kind_btns["folders"].click()
    assert rows(x) == ["📁  curated", "📁  many", "📁  raw"] and "folders only" in x._status.value
    row(x, "raw").button.click()  # it stays as you move around, like the sort
    assert rows(x) == ["📁  events"] and kinds(x) == ["All 2", "Folders 1", "Files 1"]
    row(x, "events").button.click()
    assert rows(x) == [] and "No folders here." in x._foot_note.value
    assert x._fix_btn.description == "Show the 2 files" and x._fix_btn.layout.display is None
    x._fix_btn.click()
    assert x._kind_btns["all"]._dom_classes == ("s3x-seg", "s3x-on") and len(rows(x)) == 2
    x._kind_btns["files"].click()
    x._filter.value = "part-1"
    assert rows(x) == ["📊  part-10.csv"] and kinds(x) == ["All 1", "Folders 0", "Files 1"]
    x.open("s3://lake/raw/readme.md")  # opening a file shows every kind, so the file is in the list
    assert x._kind_btns["all"]._dom_classes == ("s3x-seg", "s3x-on") and "📄  readme.md" in rows(x)


def test_explorer_searches_by_file_type(explorer, aws):
    for name in ("a.csv", "b.csv.gz", "c.json", "d.parquet", "e.png", "f.pdf", "g.docx", "h.txt", "i.yaml"):
        aws.put_object(Bucket=LAKE, Key=f"mixed/{name}", Body=b"x" * 3)
    aws.put_object(Bucket=LAKE, Key="mixed/sub/z.csv", Body=b"z")
    x = explorer("s3://lake/mixed/")
    assert chips(x) == [("📊 .csv 2", False)] + [(f"{icon} .{kind} 1", False) for icon, kind in (  # most files first
        ("📘", "docx"), ("📋", "json"), ("📊", "parquet"), ("📕", "pdf"), ("🖼️", "png"))] + [("+2 more", False)]
    chip(x, "csv").click()
    assert x._filter.value == ".csv" and rows(x) == ["📊  a.csv", "📊  b.csv.gz"] and chips(x)[0] == ("📊 .csv 2", True)
    assert kinds(x) == ["All 2", "Folders 0", "Files 2"] and "2 match “.csv” (6 B)" in x._status.value
    chip(x, "json").click()  # and .json
    assert x._filter.value == ".csv .json" and len(rows(x)) == 3
    chip(x, "csv").click()  # .csv off again
    assert x._filter.value == ".json" and rows(x) == ["📋  c.json"]
    x._more_types.click()
    assert [d for d, _ in chips(x)][-3:] == ["📄 .txt 1", "📋 .yaml 1", "Fewer"]
    x._more_types.click()
    assert chips(x)[-1] == ("+2 more", False)
    x._filter.value = "*.YAML"  # typing a type lights its chip too, even one past "+N more"
    assert rows(x) == ["📋  i.yaml"] and ("📋 .yaml 1", True) in chips(x)
    x._kind_btns["folders"].click()
    assert x._types.layout.display == "none"  # folders have no type
    x._kind_btns["all"].click()
    x._clear_btn.click()
    assert x._filter.value == "" and len(rows(x)) == 10 and x._clear_btn.layout.display == "none"

    x.open("s3://lake/many/")  # one type: no chips, they'd change nothing
    assert x._types.layout.display == "none" and x._filter.value == ""


def test_explorer_includes_subfolders(explorer):
    x = explorer("s3://lake/")
    assert "s3x-on" not in x._deep_btn._dom_classes
    x._deep_btn.click()
    assert "s3x-on" in x._deep_btn._dom_classes and "Below this folder: " in x._status.value
    assert "Files below" in text(x) and "File types below" in text(x)
    x._filter.value = ".csv"
    assert rows(x) == ["📊  old.csv.gz", "📊  part-2.csv", "📊  part-10.csv"]  # by path: curated/, then raw/events/
    assert [r.where.value for r in x._pool[:3]] == ["curated", "raw/events", "raw/events"]
    assert x._pool[0].box._dom_classes == ("s3x-r", "s3x-r2") and x._pool[0].where.layout.display is None
    row(x, "part-2.csv").button.click()
    assert x.selected == "s3://lake/raw/events/part-2.csv" and "Preview of part-2.csv" in text(x)
    x.refresh()  # keeps the search and the subfolders
    assert x._filter.value == ".csv" and x._deep and len(rows(x)) == 3 and x.selected.endswith("part-2.csv")
    x._filter.value = "top"
    assert rows(x) == ["📄  top.txt"] and x._pool[0].where.value == "in this folder"
    x._filter.value = ""
    x._cols["size"].click()  # the biggest files anywhere below, before the folders
    assert rows(x)[:2] == ["📄  readme.md", "📄  f24.txt"] and rows(x)[-1] == "📁  raw"
    x._cols["name"].click()
    x._filter.value = ""
    row(x, "events").button.click()  # opening a folder goes back to one level, with an empty search
    assert x.location == "s3://lake/raw/events/" and not x._deep and x._pool[0].box._dom_classes == ("s3x-r",)
    assert "File types here" in text(x)

    x.open("s3://lake/raw/")
    x._filter.value = ".csv"  # nothing at this level...
    assert rows(x) == [] and "Nothing here matches “.csv”." in x._foot_note.value
    assert x._fix_btn.description == "Search the subfolders too"
    x._fix_btn.click()  # ...but below it
    assert x._deep and rows(x) == ["📊  part-2.csv", "📊  part-10.csv"]
    x._deep_btn.click()
    assert not x._deep and rows(x) == []


def test_explorer_includes_subfolders_a_page_at_a_time(explorer):
    x = explorer("s3://lake/many/")
    x.nav.page_size, x.nav.deep_limit = 10, 10
    x._deep_btn.click()
    assert len(rows(x)) == 10 and "10+ files" in x._status.value and x._load_btn.layout.display is None
    assert "first 10 files below this folder" in text(x) and "lists the next 10" in text(x)
    assert x._lookup_btn.layout.display == "none"
    x._load_btn.click()
    x._load_btn.click()
    assert len(rows(x)) == 25 and x._load_btn.layout.display == "none" and x.nav.below().requests == 3


def test_explorer_filter_from_code(explorer, core, capsys):
    x = explorer("s3://lake/")
    x.filter(".csv", subfolders=True)
    assert x._deep and x._filter.value == ".csv" and len(rows(x)) == 3 and "Files below" in text(x)
    x.filter(kind="folders")
    assert not x._deep and x._filter.value == "" and rows(x) == ["📁  curated", "📁  many", "📁  raw"]
    x.filter()
    assert len(rows(x)) == 4
    x.filter(kind="colour")
    assert "kind='colour': use 'all', 'folders' or 'files'" in text(x)

    t = S3Explorer("s3://lake/", core=core, mode="text", progress="off")
    capsys.readouterr()
    t.filter(".csv", subfolders=True)
    out = capsys.readouterr().out
    assert "-- Below this folder --" in out and "raw/events/part-2.csv" in out and "curated/old.csv.gz" in out
    assert "Showing 3 of" in out and "“.csv”" in out and "top.txt" not in out
    t.filter(kind="folders")
    out = capsys.readouterr().out
    assert "📁 raw/" in out and "top.txt" not in out and "folders only" in out


def test_explorer_searches_all_of_a_big_folder(explorer):
    """The search covers the whole folder, not only the first page S3 returned (here 10 entries a page)."""
    x = explorer("s3://lake/many/")
    x.nav.page_size = 10
    x.refresh()  # the rest is listed too, up to list_limit
    assert x.nav.folder().requests == 3 and "25 files · 300 B<" in x._status.value and "Files: 25 " in text(x)
    x._filter.value = "f2"  # the 21st to 25th entries
    assert rows(x) == [f"📄  f2{i}.txt" for i in range(5)] and x._lookup_btn.layout.display == "none"


def test_explorer_stops_listing_at_list_limit(explorer):
    x = explorer("s3://lake/many/", page_size=10)
    x.nav.page_size = x.nav.list_limit = 10
    x.refresh()
    assert len(rows(x)) == 10 and x._load_btn.layout.display is None and "10+ files · 45 B so far" in x._status.value
    assert x._load_btn.tooltip == "List the next 10" and "of 10+" not in x._range.value  # one page: no bar
    assert "has more than 10 entries, and these numbers cover the first 10" in text(x) and "click “Look up”" in text(x)
    x._filter.value = "f0"
    assert len(rows(x)) == 10 and "These are the matches among the first 10 entries; this folder has more." in (
        x._foot_note.value)
    x._filter.value = "f2"
    assert rows(x) == [] and "Nothing here matches “f2” among the first 10 entries." in x._foot_note.value
    assert x._lookup_btn.layout.display is None and x._lookup_btn.description.endswith("“f2” in S3")
    x._lookup_btn.click()
    assert rows(x) == [f"📄  f2{i}.txt" for i in range(5)]
    x._filter.value = "raw/f2"
    assert x._lookup_btn.layout.display == "none" and x.nav.lookup("raw/f2") == 0  # not names in this folder
    x._filter.value = ""
    x.refresh()  # without the names looked up
    x._load_btn.click()  # the next 10
    assert len(x.nav.folder().entries) == 20 and x._load_btn.layout.display == "none"  # until the last page
    assert "<b>1–10</b> of 20+" in x._range.value
    x._page_btns["last"].click()
    assert x._load_btn.layout.display is None
    x._load_btn.click()
    assert len(x.nav.folder().entries) == 25 and x._load_btn.layout.display == "none"
    assert "25 files · 300 B<" in x._status.value and "has more than" not in text(x)


def test_explorer_text_view_searches_past_list_limit(core, capsys):
    t = S3Explorer("s3://lake/", core=core, mode="text", progress="off")
    t.nav.page_size = t.nav.list_limit = 10
    t.open("s3://lake/many/")
    out = capsys.readouterr().out
    assert "has more than 10 entries" in out and "set x.nav.list_limit = 20 and run x.refresh()" in out
    assert "Load more" not in out  # no buttons here
    t.filter("f2")  # S3 is asked for the names that start with it, as Look up does
    out = capsys.readouterr().out
    assert "f22.txt" in out and "Showing 5 of 15" in out


def test_explorer_opens_a_file_past_what_is_listed(explorer):
    x = explorer("s3://lake/")
    x.nav.page_size = x.nav.list_limit = 10
    x.open("s3://lake/many/f22.txt")  # past the 10 entries listed: S3 is asked for it
    assert x.selected == "s3://lake/many/f22.txt" and "📄  f22.txt" in rows(x) and "Preview of f22.txt" in text(x)


def test_explorer_lists_the_rest_in_the_background(explorer, core, monkeypatch):
    """In a notebook, a big folder shows its first page at once and lists the rest on a worker thread; the list,
    the counts and the search catch up as each page comes in."""
    gate, real = threading.Event(), S3Navigator._request

    def slow(nav, folder, token, keys):
        if token:  # the first page comes at once, the others wait
            gate.wait(10)
        return real(nav, folder, token, keys)

    monkeypatch.setattr(S3Navigator, "_request", slow)

    async def main():
        x = explorer("s3://lake/")
        x.nav.page_size = 10
        x.open("s3://lake/many/")
        assert len(rows(x)) == 10 and x._lister is x.nav.folder() and "Listing… 10+ files" in x._status.value
        assert "s3x-busy" in x._status.value and "Still listing this folder: these numbers cover the 10 entries" in text(x)
        assert x._load_btn.layout.display == "none"  # it's coming
        x._filter.value = "f2"  # searched as far as it's listed, and again as more comes in
        assert rows(x) == [] and "Nothing here matches “f2” yet (10 entries listed so far). Still listing the rest…" in (
            x._foot_note.value)
        gate.set()
        await x._list_task
        assert rows(x) == [f"📄  f2{i}.txt" for i in range(5)] and x._lister is None
        assert "Listing" not in x._status.value and "5 match “f2”" in x._status.value
        assert "Files: 25 " in text(x) and "Still listing" not in text(x)  # the overview, redrawn

        gate.clear()
        x.refresh()  # listed again; moving elsewhere stops it, and coming back goes on from where it was
        task, many = x._list_task, x.nav.folder()
        x.open("s3://lake/raw/")
        assert x._lister is None and len(many.entries) == 10
        with pytest.raises(asyncio.CancelledError):
            await task
        gate.set()
        x.back()
        await x._list_task
        assert len(many.entries) == 25 and not many.more and "25 files" in x._status.value

        x.nav.list_limit = 20  # a listing stops at list_limit; Load more from S3 lists the next ones
        x.refresh()
        await x._list_task
        assert len(x.nav.folder().entries) == 20 and x._load_btn.layout.display is None
        x._load_btn.click()
        assert x._lister is x.nav.folder() and x._load_btn.layout.display == "none"
        await x._list_task
        assert len(x.nav.folder().entries) == 25 and x._load_btn.layout.display == "none"

        def denied(*args, **kwargs):
            raise ClientError({"Error": {"Code": "SlowDown", "Message": "Please reduce your request rate."}},
                              "ListObjectsV2")

        x.refresh()
        monkeypatch.setattr(S3Navigator, "_request", denied)
        await x._list_task  # it stops, says why, and Load more from S3 tries again
        x._page_btns["last"].click()
        assert "S3 stopped listing this folder (SlowDown: " in x._foot_note.value
        assert x._load_btn.layout.display is None and "10+ files" in x._status.value
        x.open("s3://lake/raw/")
        assert "S3 stopped" not in x._foot_note.value

    asyncio.run(main())


def test_explorer_lists_subfolders_in_the_background(explorer, monkeypatch):
    async def main():
        x = explorer("s3://lake/")
        x.nav.page_size = 10
        x._deep_btn.click()
        assert len(x.nav.below().entries) < 20 and x._lister is x.nav.below() and "Listing… Below this folder" in (
            x._status.value)
        await x._list_task
        assert sum(not e.is_folder for e in x.nav.below().entries) == 31 and not x.nav.below().more
        x._filter.value = ".csv"
        assert rows(x) == ["📊  old.csv.gz", "📊  part-2.csv", "📊  part-10.csv"]
        x._deep_btn.click()  # back to this level: nothing to list
        assert x._lister is None and len(rows(x)) == 0

    asyncio.run(main())


def test_explorer_opens_a_file_path_with_the_file_shown(explorer):
    x = explorer("s3://lake/many/f22.txt", page_size=10)
    assert x.location == "s3://lake/many/" and x.selected == "s3://lake/many/f22.txt"
    assert "📄  f22.txt" in rows(x)  # the list grew to show it
    assert "Preview of f22.txt" in text(x)


def test_explorer_ignores_clicks_aimed_at_the_old_rows(explorer, monkeypatch):
    x = explorer("s3://lake/")
    monkeypatch.setattr(sx, "_CLICK_GRACE", 60)
    row(x, "raw").button.click()  # the rows just changed: this click was meant for what was there before
    assert x.location == "s3://lake/"


def test_explorer_shows_errors_as_notes(explorer, core, monkeypatch):
    x = explorer("s3://ghost-bucket/")
    assert "There's no bucket named 'ghost-bucket'" in text(x) and "Couldn't list" in x._status.value
    assert rows(x) == [] and "Couldn't list this folder" in x._foot_note.value
    x.open("s3://lake/raw/nope.csv")
    assert x.location == "s3://lake/raw/" and "no file or folder named 'nope.csv'" in text(x)

    x.open("s3://lake/raw/")

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(x._pane, "preview", broken)
    row(x, "readme.md").button.click()
    assert "Something went wrong (RuntimeError: boom)" in text(x)

    def denied(*args, **kwargs):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}, "ListObjectsV2")

    monkeypatch.setattr(x.nav, "open", denied)
    x.open("s3://lake/curated/")
    assert "AccessDenied: Access Denied  [open]" in text(x)


def test_explorer_recovers_from_errors_and_interrupts(explorer, core, monkeypatch):
    x = explorer("s3://lake/raw/")
    real = core.preview

    def denied(*args, **kwargs):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}, "GetObject")

    monkeypatch.setattr(core, "preview", denied)
    row(x, "readme.md").button.click()
    assert "AccessDenied: Access Denied  [preview]" in text(x)
    monkeypatch.setattr(core, "preview", real)  # e.g. the permission was added
    x._act_buttons["head"].click()
    x._act_buttons["preview"].click()
    assert "Preview of readme.md" in text(x)  # the error wasn't kept: it asked again

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    x._act_buttons["close"].click()
    monkeypatch.setattr(x._pane, "summary", interrupted)
    x._act_buttons["summary"].click()
    assert "Stopped." in text(x) and "…" not in x._status.value


def test_explorer_text_mode(core, capsys):
    x = S3Explorer("s3://lake/raw/", core=core, mode="text", progress="off")
    out = capsys.readouterr().out
    assert "-- In this folder --" in out and "📁 events/" in out and "📄 readme.md" in out
    x.open("s3://lake/raw/readme.md")
    assert "Preview of readme.md" in capsys.readouterr().out
    assert repr(x) == "S3Explorer(s3://lake/raw/)" and x.location == "s3://lake/raw/"
    assert isinstance(x.ui, S3View) and "_show" not in vars(x.ui)  # x.ui reports in its own cell


def test_explorer_without_ipywidgets_falls_back_to_text(core, capsys, monkeypatch):
    real = s3mod._require

    def require(module, purpose, package=None):
        if module == "ipywidgets":
            raise ImportError(f"{purpose} needs `ipywidgets` (pip install ipywidgets)")
        return real(module, purpose, package)

    monkeypatch.setattr(s3mod, "_require", require)
    x = S3Explorer("s3://lake/", core=core, mode="widgets", progress="off")
    out = capsys.readouterr().out
    assert "pip install ipywidgets" in out and "-- In this folder --" in out and x._widgets is None


def test_explorer_without_s3_py(capsys, monkeypatch):
    def missing(core=None):
        raise ImportError("s3_explorer.py builds on s3.py, which isn't here.")

    monkeypatch.setattr(sx, "_s3_module", missing)
    x = S3Explorer("s3://lake/")
    assert "builds on s3.py" in capsys.readouterr().out
    x.open("s3://lake/raw/")
    assert "builds on s3.py" in capsys.readouterr().out and repr(x) == "S3Explorer(not ready)"


def test_s3_module_is_found_where_s3_py_is(monkeypatch):
    view = S3View(mode="text")
    assert sx._s3_module(view) is s3mod and sx._s3_module() is s3mod
    monkeypatch.setitem(sys.modules, "s3", None)  # `import s3` fails...
    main = sys.modules["__main__"]
    with pytest.raises(ImportError, match="Upload s3.py next to this notebook"):
        sx._s3_module()
    for name in ("S3Analyzer", "S3View", "_render_html"):  # ...but it was pasted into a cell
        monkeypatch.setattr(main, name, getattr(s3mod, name), raising=False)
    assert sx._s3_module() is main


# ----------------------------------------------------------------------------- PDFs, background loading, zips


def pdf_bytes(*pages):
    """A PDF with one page per text, xref offsets and all."""
    count = len(pages)
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % (4 + 2 * i) for i in range(count)),
                                                             count),
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for i, text in enumerate(pages):
        stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents %d 0 R "
                       b"/Resources << /Font << /F1 3 0 R >> >> >>" % (5 + 2 * i))
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def pager(x):
    return [child.description if hasattr(child, "description") and child.description else child.value
            for child in x._pager.children]


def test_explorer_reads_a_pdf_as_its_pages(explorer, aws):
    aws.put_object(Bucket=LAKE, Key="docs/long.pdf", Body=pdf_bytes(*(f"Page {n} text" for n in range(1, 26))))
    aws.put_object(Bucket=LAKE, Key="docs/short.pdf", Body=pdf_bytes("One", "Two"))
    aws.put_object(Bucket=LAKE, Key="docs/broken.pdf", Body=b"%PDF-1.4 not really")
    x = explorer("s3://lake/docs/long.pdf")
    assert x._content.value.count('<div class="zw"') == 3  # the preview's pages: click one to see it full size
    assert "Every page as it looks" in x._act_buttons["document"].tooltip

    x._act_buttons["document"].click()
    drawn = [block.pictures[0].page for block in x.shown if isinstance(block, s3mod._Pages)]
    assert drawn == list(range(1, 21)) and x._content.value.count('<div class="zw"') == 20
    assert "Pages 1–20 of 25; the buttons at the end show the others. Click a page to see it full size" in text(x)
    assert "Text of page 3" in text(x)  # each page's text, folded under it
    assert x._pager.layout.display is None and pager(x) == ["Pages 1–20 of 25", "Pages 21–25 ›"]
    x._pager.children[1].click()
    assert [block.pictures[0].page for block in x.shown if isinstance(block, s3mod._Pages)] == [21, 22, 23, 24, 25]
    assert pager(x) == ["Pages 21–25 of 25", "‹ Pages 1–20"]
    x._pager.children[1].click()
    assert pager(x)[0] == "Pages 1–20 of 25"
    x._act_buttons["preview"].click()
    assert x._pager.layout.display == "none"

    row(x, "short.pdf").button.click()
    x._act_buttons["document"].click()
    assert len([block for block in x.shown if isinstance(block, s3mod._Pages)]) == 2
    assert x._pager.layout.display == "none" and "Pages 1–2" not in text(x) and "Click a page" in text(x)

    row(x, "broken.pdf").button.click()
    x._act_buttons["document"].click()  # it can't be counted: document() says why
    assert "pypdf couldn't read this PDF" in text(x)


def test_explorer_reads_a_pdfs_text(explorer, aws):
    aws.put_object(Bucket=LAKE, Key="docs/long.pdf", Body=pdf_bytes(*(f"Page {n} text" for n in range(1, 61))))
    aws.put_object(Bucket=LAKE, Key="docs/broken.pdf", Body=b"%PDF-1.4 not really")
    aws.put_object(Bucket=LAKE, Key="docs/notes.txt", Body=b"plain text")
    x = explorer("s3://lake/docs/long.pdf")
    assert "laid out to read" in x._act_buttons["text"].tooltip

    x._act_buttons["text"].click()
    flows = [block for block in x.shown if isinstance(block, s3mod._Flow)]
    assert len(flows) == 1 and [value for role, value in flows[0].items if role == "page"] == list(range(1, 51))
    assert ("p", "Page 3 text") in flows[0].items and not any(isinstance(b, s3mod._Pages) for b in x.shown)
    assert '<div class="pg">Page 2</div>' in x._content.value and '<div class="zw"' not in x._content.value
    assert "Pages 1–50 of 60; the buttons at the end show the others, and 📖 Read all shows the pages" in text(x)
    assert x._action == "text" and pager(x) == ["Pages 1–50 of 60", "Pages 51–60 ›"]
    x._pager.children[1].click()
    assert [value for role, value in x.shown[-1].items if role == "page"] == list(range(51, 61))
    assert x._action == "text" and pager(x) == ["Pages 51–60 of 60", "‹ Pages 1–50"]
    x._act_buttons["document"].click()  # the pages as they look: 20 a report
    assert pager(x) == ["Pages 1–20 of 60", "Pages 21–40 ›"]
    x._act_buttons["text"].click()  # shown again from the cache
    assert pager(x)[0] == "Pages 1–50 of 60" and x._act_buttons["text"]._dom_classes == ("s3x-act", "s3x-on")

    row(x, "broken.pdf").button.click()
    x._act_buttons["text"].click()  # it can't be counted: document() says why
    assert "pypdf couldn't read this PDF" in text(x) and x._pager.layout.display == "none"
    row(x, "notes.txt").button.click()
    assert "text" not in x._act_buttons and "document" not in x._act_buttons


def test_explorer_expands_every_part_of_a_json_file(explorer, aws):
    doc = {"info": {"version": 1}, "note": "x" * 200, "images": [{"id": i, "size": [i, 2 * i]} for i in range(10)]}
    aws.put_object(Bucket=LAKE, Key="curated/images.json", Body=json.dumps(doc).encode())
    aws.put_object(Bucket=LAKE, Key="curated/labels.json", Body=json.dumps({"labels": [{"id": i} for i in range(9)]}))
    x = explorer("s3://lake/curated/images.json")
    folded = x._content.value.count('<details class="jo">')  # the images after the first, and the long note
    assert folded == 10 and x._expand_btn not in x._act_buttons.values()
    assert x._actions.children[-2:] == (x._expand_btn, x._act_buttons["close"])  # beside ✕
    assert x._expand_btn.description == "▾ Expand all" and "one by one" in x._expand_btn.tooltip

    x._expand_btn.click()
    assert x._content.value.count('<details class="jo">') == 1  # only the long string stays folded
    assert x._expand_btn._dom_classes == ("s3x-act", "s3x-expand", "s3x-on") and "Collapse" in x._expand_btn.tooltip
    assert all(tree.root.children[2].children[9].open for tree in x._trees())
    row(x, "labels.json").button.click()  # it stays on for the next file
    assert x._expand_btn in x._actions.children and '<details class="jo">' not in x._content.value
    x._act_buttons["head"].click()  # no tree, no button
    assert x._expand_btn not in x._actions.children
    x._act_buttons["preview"].click()
    assert x._expand_btn in x._actions.children and '<details class="jo">' not in x._content.value

    x._expand_btn.click()  # off: back to the first levels, here and in the next file
    assert x._content.value.count('<details class="jo">') == 8 and "s3x-on" not in x._expand_btn._dom_classes
    row(x, "images.json").button.click()
    assert x._content.value.count('<details class="jo">') == folded
    row(x, "table.json").button.click()  # {"a": 1}: nothing to expand
    assert x._expand_btn not in x._actions.children and x._actions.children[-1] is x._act_buttons["close"]
    x._act_buttons["close"].click()
    assert x._expand_btn not in x._actions.children


def test_explorer_loads_previews_in_the_background(explorer, core, monkeypatch):
    """In a notebook (a running event loop), a click returns at once; a file clicked since wins."""
    monkeypatch.setattr(sx, "_WORKERS", 1)
    gate, real, asked = threading.Event(), core.preview, []

    def slow(uri, *args, **kwargs):
        asked.append(uri.rsplit("/", 1)[-1])
        if uri.endswith("readme.md"):
            gate.wait(10)
        return real(uri, *args, **kwargs)

    monkeypatch.setattr(core, "preview", slow)

    async def main():
        x = explorer("s3://lake/raw/events/")
        x.open("s3://lake/raw/readme.md")
        first = x._task
        assert x.selected == "s3://lake/raw/readme.md" and "Opening readme.md…" in x._content.value
        while not asked:  # it has started on the worker thread (one clicked past before that is skipped)
            await asyncio.sleep(0.01)
        x.open("s3://lake/raw/events/part-10.csv")  # waits behind readme.md, then is skipped
        x.open("s3://lake/raw/events/part-2.csv")
        assert x.selected.endswith("part-2.csv") and "Opening part-2.csv…" in x._content.value
        gate.set()
        await first
        assert "Preview of readme.md" not in text(x)  # out of date: kept, not shown
        await x._task
        assert "Preview of part-2.csv" in text(x) and asked == ["readme.md", "part-2.csv"]
        x.open("s3://lake/raw/readme.md")  # it was cached on the way
        assert "Preview of readme.md" in text(x) and asked == ["readme.md", "part-2.csv"]

        def broken(self, *args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(s3mod.S3View, "preview", broken)
        x.refresh()
        await x._task
        assert "Something went wrong (RuntimeError: boom)" in x._content.value

    asyncio.run(main())


def test_explorer_zips_a_folder_within_the_limits_in_settings(explorer, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    x = explorer("s3://lake/raw/", zip_max_size="20B")
    assert list(x._act_buttons) == ["summary", "zip"] and "no bigger than 20 B and 10,000 files" in x._act_buttons["zip"].tooltip
    x._act_buttons["zip"].click()
    out = text(x)
    assert "Can't zip this here yet" in out and "⚙ at the top right raises them (now 20 B and 10,000 files)" in out
    assert out.index("Can't zip this here yet") < out.index("⚙ at the top right")  # after the line that says why
    assert not (tmp_path / "raw.zip").exists()

    x._gear.click()
    assert x._settings.layout.display is None and "Settings" in x._content.value and x._set_size.value == "20 B"
    x._set_size.value = "lots"
    x._set_size._handle_custom_msg({"event": "submit"}, [])  # Enter saves
    assert "“lots” isn&#x27;t a size" in x._set_note.value and x.zip_max_size == "20B"
    x._set_size.value, x._set_files.value, x._set_folder.value = "1MB", "5,000", "zips"
    x._set_folder._handle_custom_msg({"event": "submit"}, [])
    assert (x.zip_max_size, x.zip_max_files, x.zip_folder) == (1024 ** 2, 5000, "zips")
    assert "up to 1.0 MB and 5,000 files, into zips (made when the first zip is saved)" in x._set_note.value
    x._gear.click()  # closes the settings
    assert x._settings.layout.display == "none" and "File types here" in text(x)

    x._act_buttons["zip"].click()
    with zipfile.ZipFile(tmp_path / "zips" / "raw.zip") as made:
        assert sorted(made.namelist()) == ["events/part-10.csv", "events/part-2.csv", "readme.md"]
    assert f"Saved {tmp_path / 'zips' / 'raw.zip'} (" in text(x) and "choose Download" in text(x)
    assert "⚙" not in text(x)  # the limits only come up when they stop a zip



# ----------------------------------------------------------------------------- selecting files to download as one zip


def check(x, name):
    row(x, name).check.click()


def ticked(x):
    return [r.entry.name for r in x._pool[: len(x._rows_box.children)] if "s3x-on" in r.check._dom_classes]


def test_explorer_selects_files(explorer):
    x = explorer("s3://lake/raw/events/")
    assert x._picks_bar.layout.display == "none" and "s3x-picking" not in x._side._dom_classes
    check(x, "part-2.csv")
    assert x.picked == ["s3://lake/raw/events/part-2.csv"] and ticked(x) == ["part-2.csv"]
    assert x._picks_bar.layout.display is None and "1 selected" in x._picks_note.value and "8 B" in x._picks_note.value
    assert "s3x-picking" in x._side._dom_classes and "s3x-picked" in row(x, "part-2.csv").button._dom_classes
    assert "s3x-i-minus" in x._check_all._dom_classes  # some of the list
    x._check_all.click()
    assert ticked(x) == ["part-2.csv", "part-10.csv"] and "2 selected" in x._picks_note.value
    assert x._check_all._dom_classes == ("s3x-check", "s3x-ic", "s3x-i-check", "s3x-on")
    x._check_all.click()  # everything was: unselect it
    assert x.picked == [] and x._picks_bar.layout.display == "none"
    check(x, "part-10.csv")
    x.refresh()  # keeps the selection
    assert x.picked == ["s3://lake/raw/events/part-10.csv"] and ticked(x) == ["part-10.csv"]
    [b for b in x._picks_bar.children if getattr(b, "description", "") == "Clear"][0].click()
    assert x.picked == [] and ticked(x) == []
    check(x, "part-10.csv")
    x.up()  # another folder: a new selection
    assert x.picked == [] and x._picks_bar.layout.display == "none"

    x.open("s3://lake/many/")
    x._filter.value = "f1"
    x._check_all.click()  # what the search shows, past "Show more" too
    assert len(x.picked) == 10 and all("/f1" in uri for uri in x.picked)
    x.open("")
    assert "s3x-buckets" in x._side._dom_classes  # buckets can't be selected
    row(x, LAKE).check.click()
    assert x.picked == []


def test_explorer_downloads_the_selection_as_one_zip(explorer, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    x = explorer("s3://lake/raw/events/")
    check(x, "part-2.csv")
    check(x, "part-10.csv")
    [b for b in x._picks_bar.children if "Download selected" in getattr(b, "description", "")][0].click()
    assert x._picks_panel.layout.display is None and x._picks_name.value == "events-2-files.zip"
    out = text(x)
    assert "Download 2 files as one .zip" in out and "from s3://lake/raw/events/" in out
    assert "part-10.csv" in x._picks_list.value and "in the notebook's folder" in x._picks_where.value
    check(x, "part-10.csv")  # the panel follows the selection, and the name it suggested
    assert "Download 1 file as one .zip" in text(x) and x._picks_name.value == "part-2.csv.zip"
    check(x, "part-10.csv")
    x._picks_name.value = "my events"
    x._picks_name._handle_custom_msg({"event": "submit"}, [])  # Enter downloads
    with zipfile.ZipFile(tmp_path / "my events.zip") as made:
        assert sorted(made.namelist()) == ["part-10.csv", "part-2.csv"]
    assert "Zip of 2 files from s3://lake/raw/events/" in text(x) and x._picks_panel.layout.display == "none"
    assert x.picked  # still selected

    x._open_picks()
    assert x._picks_name.value == "events-2-files.zip"
    (tmp_path / "events-2-files.zip").write_bytes(b"mine")
    x._open_picks()
    assert x._picks_name.value == "events-2-files-2.zip"  # never a file that's already there
    x._picks_name.value = "events-2-files.zip"
    x._picks_go.click()
    assert "already there" in x._picks_msg.value and (tmp_path / "events-2-files.zip").read_bytes() == b"mine"
    [b for b in x._picks_panel.children[1].children if b.description == "Cancel"][0].click()
    assert x._picks_panel.layout.display == "none" and "File types here" in text(x)
    x._open_picks()
    x._clear_picks()  # nothing left to download: the panel closes
    assert x._picks_panel.layout.display == "none"


def test_explorer_zips_selected_files_and_folders(explorer, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    x = explorer("s3://lake/raw/", zip_max_size="20B")
    check(x, "events")
    check(x, "readme.md")
    x._open_picks()
    out = text(x)
    assert "Download 1 file and 1 folder as one .zip" in out and "with everything below it" in out
    assert "over the 20 B limit" in out and x._picks_go.disabled  # readme.md alone is 31 B
    x.zip_max_size = "1MB"
    x._open_picks()
    assert not x._picks_go.disabled and x._picks_name.value == "raw-2-items.zip"
    x._picks_go.click()
    with zipfile.ZipFile(tmp_path / "raw-2-items.zip") as made:
        assert sorted(made.namelist()) == ["events/part-10.csv", "events/part-2.csv", "readme.md"]
    assert "Zip of 1 file and 1 folder from s3://lake/raw/" in text(x)

    x.zip_max_size = "40B"  # over once the folder is listed: the report says why, and what to do
    x._open_picks()
    x._picks_name.value = "again"
    x._picks_go.click()
    out = text(x)
    assert "Can't zip this here yet" in out and "untick some files, or raise the limits with ⚙" in out
    assert "click ⬇ Download selected again" in out and not (tmp_path / "again.zip").exists()
