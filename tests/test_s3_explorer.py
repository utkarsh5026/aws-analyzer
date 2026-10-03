import sys
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
    entry_icon,
    explain_list_error,
    filter_entries,
    folder_stats,
    folder_uri,
    parent_uri,
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


def test_filter_entries():
    entries = [file("raw/Report-2024.csv"), file("raw/notes.txt"), folder("raw/reports/")]
    assert [e.name for e in filter_entries(entries, "REPORT")] == ["Report-2024.csv", "reports"]
    assert [e.name for e in filter_entries(entries, "*.txt")] == ["notes.txt"]
    assert filter_entries(entries, "  ") == entries


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
    assert list(x._act_buttons) == ["preview", "head", "download", "link", "close"]
    assert x._act_buttons["preview"]._dom_classes == ("s3x-act", "s3x-on")
    assert 'class="s3a"' in x._content.value and "<table" in x._content.value

    x._act_buttons["head"].click()
    assert "System metadata" in text(x) and x._act_buttons["head"]._dom_classes == ("s3x-act", "s3x-on")
    x._act_buttons["link"].click()
    assert "Download part-2.csv (valid 60 min)" in text(x)
    x._act_buttons["close"].click()
    assert not x.selected and "File types here" in text(x) and list(x._act_buttons) == ["summary"]


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


def test_explorer_sort_filter_and_show_more(explorer):
    x = explorer("s3://lake/many/", page_size=10)
    assert len(rows(x)) == 10 and x._more_btn.layout.display is None
    assert x._more_btn.description == "Show 10 more (of 15)"
    x._more_btn.click()
    x._more_btn.click()
    assert len(rows(x)) == 25 and x._more_btn.layout.display == "none"
    x._cols["size"].click()
    assert rows(x)[0] == "📄  f24.txt" and x._cols["size"].description == "Size ▼" and len(rows(x)) == 10
    x._cols["size"].click()
    assert rows(x)[0] == "📄  f00.txt" and x._cols["size"].description == "Size ▲"
    x._cols["name"].click()
    assert x._cols["name"].description == "Name ▲" and x._cols["size"].description == "Size"
    x._filter.value = "f1"
    assert rows(x) == [f"📄  f1{i}.txt" for i in range(10)] and "10 match “f1”" in x._status.value
    x._filter.value = "nothing-like-this"
    assert rows(x) == [] and "Nothing here matches" in x._foot_note.value
    x.open("s3://lake/raw/")
    assert x._filter.value == "" and len(rows(x)) == 2  # a new folder starts unfiltered


def test_explorer_loads_big_folders_a_page_at_a_time(explorer):
    x = explorer("s3://lake/many/")
    x.nav.page_size = 10
    x.refresh()
    assert len(rows(x)) == 10 and x._load_btn.layout.display is None and "10+ files · 45 B so far" in x._status.value
    assert "first 10 entries S3 returned" in text(x)
    x._filter.value = "f2"
    assert x._lookup_btn.layout.display is None and x._lookup_btn.description.endswith("“f2” in S3")
    x._lookup_btn.click()
    assert rows(x) == [f"📄  f2{i}.txt" for i in range(5)]
    x._filter.value = ""
    x._load_btn.click()
    x._load_btn.click()
    assert len(rows(x)) == 25 and x._load_btn.layout.display == "none" and "25 files · 300 B<" in x._status.value


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
