"""
The code every service shares (aws_analyzer._kit): what the renderer does with a block nothing draws, the
stylesheet every report starts with and the themes drawn after it, and what the base View and Analyzer do that no service shows in normal use
(an error none of them raises, a command no group lists, prices passed to one analyzer), and the windows' guards for
what no window meets (a command that can't run, an error that should still raise). Each service's tests cover how its
reports read, and each window's tests click through it.
"""

import asyncio
import itertools
import threading
import os
import re
import time
from datetime import datetime, timedelta, timezone

import pytest
from botocore.exceptions import ClientError

from aws_analyzer import (
    bedrock_chat, bedrock_kb, dynamodb, lambda_functions, opensearch, s3, sagemaker_env, set_theme, set_timezone,
)
from aws_analyzer._kit import view as viewmod
from aws_analyzer._kit.analyzer import _BaseAnalyzer
from aws_analyzer._kit.blocks import _Next, _Note, _Tabs, _Text, _Title
from aws_analyzer._kit.errors import _Hint
from aws_analyzer._kit.fmt import _fmt_dt, _tz_label, parse_time
from aws_analyzer._kit.render import _base_css, _copy_button, _Look, _render_text, _report_html, _sort_key
from aws_analyzer._kit.themes import DEFAULT_THEME, THEMES, _theme_name
from aws_analyzer._kit.view import _BaseView, _commands, _friendly_errors
from aws_analyzer._kit.window import _Explorer, _for_window, _Window, _window_errors
from aws_analyzer.dynamodb import style as ddb_style

# Each service: its module, Analyzer and View, and its list prices.
SERVICES = [
    (s3, s3.S3Analyzer, s3.S3View, s3.S3_PRICES),
    (dynamodb, dynamodb.DynamoDBAnalyzer, dynamodb.DynamoDBView, dynamodb.DYNAMODB_PRICES),
    (bedrock_kb, bedrock_kb.BedrockKBAnalyzer, bedrock_kb.BedrockKBView, bedrock_kb.BEDROCK_PRICES),
    (bedrock_chat, bedrock_chat.BedrockChatAnalyzer, bedrock_chat.BedrockChatView, bedrock_chat.BEDROCK_PRICES),
    (sagemaker_env, sagemaker_env.SageMakerAnalyzer, sagemaker_env.SageMakerView, sagemaker_env.SAGEMAKER_PRICES),
    (opensearch, opensearch.OpenSearchAnalyzer, opensearch.OpenSearchView, opensearch.OPENSEARCH_PRICES),
    (lambda_functions, lambda_functions.LambdaAnalyzer, lambda_functions.LambdaView, lambda_functions.LAMBDA_PRICES),
]


class _Mystery:  # a block no service registers a drawing for
    pass


class _Loud(_Note):  # a block made from another one
    pass


def test_a_block_nothing_draws_becomes_a_note():
    html = _report_html([_Title("Report"), _Mystery()], 0, _Look("<style></style>", "ddb", "🗄️ DynamoDB"))
    assert html.startswith('<style></style><div class="ddb"><h3><span class="badge">🗄️ DynamoDB</span>Report</h3>')
    assert ('<div class="note warn"><span class="ld">Part of this report isn&#x27;t shown:</span> there&#x27;s no way to '
            'draw a _Mystery') in html
    assert _render_text([_Mystery()], 0).startswith("[!] Part of this report isn't shown")


def test_tabs_show_one_part_at_a_time():
    tabs = _Tabs([("First", [_Note("one")]), ("Second", [_Note("two"), _Next([("x()", "more")])])])
    html = _report_html([tabs], 0, _Look(_base_css("x"), "x", ""))
    assert re.search(r'<div class="tbar" hidden="hidden"><label><input type="radio" name="t\w{10}" class="tr0" checked '
                     r'hidden="hidden">First</label><label><input type="radio" name="t\w{10}" class="tr1" hidden="hidden">'
                     r'Second</label></div><div class="tp tp0">', html)
    assert ".x .tabs:has(>.tbar .tr1:checked)>.tp1{display:block}" in html and "two" in html
    assert _render_text([tabs], 0).splitlines()[:2] == ["[i] one", "[i] two"]
    windowed = _for_window([tabs], str.upper)[0]
    assert windowed.tabs == [("First", [_Note("ONE")]), ("Second", [_Note("TWO")])]


def test_a_block_made_from_another_is_drawn_like_it():
    assert _report_html([_Loud("Careful", "warn")], 0, _Look("", "ddb", "")) == (
        '<div class="ddb"><div class="note warn">Careful</div></div>')
    assert _render_text([_Loud("Careful", "warn")], 0) == "[!] Careful"


def test_code_to_copy_has_a_copy_button():
    look = _Look(_base_css("x"), "x", "")
    html = _report_html([_Text("print('<hi>')\n", title="Run this", code=True)], 10, look)
    assert html.count('class="copy"') == 1 and "print(&#x27;&lt;hi&gt;&#x27;)" in html.split('class="cpt">')[1]
    assert "Run this" in html and 'hidden="hidden"' in html
    assert 'class="copy"' not in _report_html([_Text("not code")], 10, look)
    assert '<span class="cpt">a\n\n</span>' in _copy_button("a\n")


def test_base_css_restyles_only_a_rule_it_finds_once():
    assert _base_css("ddb") == ddb_style._CSS and _base_css("x").count(".x ") == 187
    css = _base_css("x", ".x .own{}", theme=".x{--c:red}", restyle={".pill{": ".x .pill{color:var(--c)}"},
                     after={"pre.code{": ".x pre.hl{}"})
    lines = css.split("\n")
    assert lines[:2] == ["<style>", ".x{--c:red}"] and lines[-2:] == [".x .own{}", "</style>"]
    assert ".x .pill{color:var(--c)}" in lines and lines[lines.index(".x pre.hl{}") - 1].startswith(".x pre.code{")
    with pytest.raises(ValueError, match="'.nope{' starts 0 of the shared CSS rules"):
        _base_css("x", restyle={".nope{": ".x .nope{}"})
    with pytest.raises(ValueError, match="'.track{' starts 2 of"):  # .track{display:..} and .track{vertical-align:..}
        _base_css("x", restyle={".track{": ".x .track{}"})


# ----------------------------------------------------------------------------- the base View


class _Core(_BaseAnalyzer):
    _LIST_PRICES = {"thing": 1.0}


class _Toy(_BaseView):
    """
    A View with one command that raises what it's given, and its own answers to the hooks.
    """

    _render_html = staticmethod(lambda blocks, max_rows: "")
    _PROGRESS = ("Toying", "toys")
    _DATA_ERRORS = (ValueError, OSError)
    _GROUPS = {"Help": ("help",)}

    def __init__(self, **options):
        super().__init__(mode="text", **options)
        self.core = _Core(region="us-east-1")

    def _explain_call(self, code, message, command, args, kwargs):
        return f"{message} ({command} was given {args[1:]} and {kwargs})"

    def _extra_note(self, exc, command):
        return _Note(f"Mine: {exc}  [{command}]", "warn") if isinstance(exc, LookupError) else None

    @_friendly_errors
    def fail(self, exc: BaseException, *args, **kwargs) -> None:
        """
        Raises exc.
        """
        raise exc


def shown(capsys, exc, *args, **kwargs):
    _Toy().fail(exc, *args, **kwargs)
    return capsys.readouterr().out.strip()


def test_friendly_errors_asks_the_view_what_each_error_means(capsys):
    denied = ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "GetThing")
    assert shown(capsys, denied, "a", n=1) == "[!] AccessDenied: no (fail was given ('a',) and {'n': 1})  [fail]"
    assert shown(capsys, _Hint("Which one? Pass name=")) == "[i] Which one? Pass name="  # a question, not an error
    assert shown(capsys, ImportError("Drawing needs `x` (pip install x)")) == "[!] Drawing needs `x` (pip install x)."
    assert shown(capsys, OSError("disk full")) == "[!] OSError: disk full  [fail]"  # one of its _DATA_ERRORS
    assert shown(capsys, KeyError("k")) == "[!] Mine: 'k'  [fail]"  # its _extra_note
    for unknown in (TypeError("not one of its _DATA_ERRORS"), RuntimeError("a bug")):
        with pytest.raises(type(unknown)):
            _Toy().fail(unknown)


def test_help_lists_the_views_commands_and_its_own(capsys):
    assert [name for name in vars(_BaseView) if not name.startswith("_")] == ["help"]  # the base's only command
    assert list(_commands(_Toy)) == ["fail", "help"]  # the View's own first, then the base's
    _Toy().help()
    out = capsys.readouterr().out
    assert "_Toy commands" in out and "-- Help --\nCommand" in out and "-- Other --" in out and "fail(exc," in out
    assert out.rstrip().endswith("[i] Times are in UTC: set_timezone('Asia/Kolkata') (from aws_analyzer) shows them in "
                                 "your own zone, set_timezone('local') in this machine's.")
    set_timezone("Asia/Kolkata")
    _Toy().help()
    assert "Times are in IST: set_timezone('UTC') (from aws_analyzer) shows them in UTC again." in (
        capsys.readouterr().out)
    _Toy().help("fial")
    assert "_Toy has no command 'fial'. Did you mean 'fail'?" in capsys.readouterr().out


def test_progress_says_the_views_words_unless_given_its_own(capsys, monkeypatch):
    clock = itertools.count()
    monkeypatch.setattr(viewmod.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(viewmod, "_progress_bar_class", lambda notebook: None)  # no tqdm: the plain line
    with _Toy(progress="plain")._progress() as tick:
        tick(3000)
    assert "Toying... 3,000 toys" in capsys.readouterr().err
    with _Toy(progress="plain")._progress("Reading", unit="B") as tick:
        tick(2048)
    assert "Reading... 2.0 KB" in capsys.readouterr().err


# ----------------------------------------------------------------------------- shared state


@pytest.mark.parametrize("module, analyzer, view, list_prices", SERVICES, ids=[m.__name__ for m, *_ in SERVICES])
def test_prices_given_to_one_analyzer_change_nothing_else(module, analyzer, view, list_prices):
    # prices= is copied into the analyzer: the service's table, and every other analyzer's prices, stay as they were.
    before = {m.__name__: dict(table) for m, _, _, table in SERVICES}
    key = next(iter(list_prices))
    mine, other = analyzer(region="us-east-1", prices={key: 1234.5}), analyzer(region="us-east-1")
    mine.prices[key] = 99.0  # changing it afterwards doesn't reach the table either
    assert {m.__name__: dict(table) for m, _, _, table in SERVICES} == before
    assert other.prices == list_prices and other.prices is not list_prices and mine.prices[key] == 99.0
    assert "your prices" in view(mine, mode="text")._price_basis()
    assert "us-east-1 list prices" in view(other, mode="text")._price_basis()
    if hasattr(module, "MODEL_PRICES"):  # Bedrock: the models' prices too
        model = next(iter(module.MODEL_PRICES))
        custom = analyzer(region="us-east-1", model_prices={model: (1.0, 2.0)})
        assert module.MODEL_PRICES[model] != (1.0, 2.0) and other.model_prices == module.MODEL_PRICES
        assert view(custom, mode="text")._price_basis(models=True) == "your prices"
        assert view(other, mode="text")._price_basis(models=True) == "us-east-1 list prices"


MAY_DAY = datetime(2024, 5, 1, 10, 0, 31, tzinfo=timezone.utc)
JANUARY = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)


def test_times_show_in_utc_until_a_zone_is_picked():
    assert _fmt_dt(MAY_DAY) == "2024-05-01 10:00" and _fmt_dt(MAY_DAY, zone=True) == "2024-05-01 10:00 UTC"
    assert _fmt_dt(MAY_DAY, seconds=True) == "2024-05-01 10:00:31" and _fmt_dt(None) == "-" and _tz_label() == "UTC"
    assert parse_time("2024-05-01") == datetime(2024, 5, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("zone, label, may, january", [
    ("Asia/Kolkata", "IST", "2024-05-01 15:30 IST", "2024-01-15 15:30 IST"),
    ("kolkata", "IST", "2024-05-01 15:30 IST", "2024-01-15 15:30 IST"),  # its city, in any case
    ("new york", "EST/EDT", "2024-05-01 06:00 EDT", "2024-01-15 05:00 EST"),  # the clock changes: both names
    ("Australia/Sydney", "AEST/AEDT", "2024-05-01 20:00 AEST", "2024-01-15 21:00 AEDT"),  # standard time first
    ("Asia/Dubai", "UTC+04", "2024-05-01 14:00 UTC+04", "2024-01-15 14:00 UTC+04"),  # no name in letters
    ("+05:45", "UTC+05:45", "2024-05-01 15:45 UTC+05:45", "2024-01-15 15:45 UTC+05:45"),
    ("UTC-4", "UTC-04", "2024-05-01 06:00 UTC-04", "2024-01-15 06:00 UTC-04"),
    (timezone(timedelta(hours=9)), "UTC+09", "2024-05-01 19:00 UTC+09", "2024-01-15 19:00 UTC+09"),
    ("utc", "UTC", "2024-05-01 10:00 UTC", "2024-01-15 10:00 UTC"),
])
def test_set_timezone_shows_times_in_that_zone(zone, label, may, january):
    assert set_timezone(zone) == label == _tz_label()
    assert _fmt_dt(MAY_DAY, zone=True) == may and _fmt_dt(JANUARY, zone=True) == january
    assert _fmt_dt(MAY_DAY) == may.rsplit(" ", 1)[0]
    assert _sort_key(may) == (1, may)  # still sorts as a date in a table
    typed = parse_time("2024-05-01 10:00")  # a time typed in is read in the same zone
    assert typed is not None and _fmt_dt(typed, zone=True).startswith("2024-05-01 10:00")
    assert parse_time("2024-05-01T10:00Z") == datetime(2024, 5, 1, 10, tzinfo=timezone.utc)  # one that says its zone
    set_timezone(None)
    assert _tz_label() == "UTC" and _fmt_dt(MAY_DAY) == "2024-05-01 10:00"


@pytest.mark.parametrize("zone, message", [
    ("Asia/Kolkatta", "'Asia/Kolkatta' isn't a time zone set_timezone() knows. Did you mean 'Asia/Kolkata'"),
    ("londno", "Did you mean 'Europe/London'?"),
    ("IST", "A short name like IST can mean more than one zone (IST is India's, Ireland's and Israel's)."),
    ("+15:00", "'+15:00' isn't an offset from UTC: they run from -12:00 to +14:00"),
])
def test_set_timezone_says_what_it_takes(zone, message):
    with pytest.raises(ValueError, match="by its offset|from -12:00") as caught:
        set_timezone(zone)
    assert message in str(caught.value) and _tz_label() == "UTC"  # nothing changed
    with pytest.raises(TypeError, match="takes a zone's name"):
        set_timezone(5.5)


def test_the_environment_can_pick_the_zone(monkeypatch):
    monkeypatch.setenv("AWS_ANALYZER_TZ", "Europe/London")
    assert _tz_label() == "GMT/BST" and _fmt_dt(MAY_DAY, zone=True) == "2024-05-01 11:00 BST"
    assert set_timezone("Asia/Tokyo") == "JST"  # set_timezone() wins
    assert set_timezone(None) == "GMT/BST"  # and None goes back to the environment's
    monkeypatch.setenv("AWS_ANALYZER_TZ", "Mars/Olympus")
    with pytest.warns(UserWarning, match="AWS_ANALYZER_TZ='Mars/Olympus': .* Times show in UTC."):
        assert _tz_label() == "UTC"


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="only Unix can change its zone while running")
def test_local_is_this_machines_zone():
    before = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Kolkata"
    time.tzset()
    try:
        assert set_timezone("local") == "IST" and _fmt_dt(MAY_DAY, zone=True) == "2024-05-01 15:30 IST"
        assert parse_time("2024-05-01") == datetime(2024, 5, 1, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    finally:
        os.environ.pop("TZ") if before is None else os.environ.update(TZ=before)
        time.tzset()


class _ToyWindow(_Window):
    """
    A window that writes down what it says, and says a ValueError from a command but nothing else.
    """

    def __init__(self, ready=True):
        self.said, self.ran, self.ready = [], [], ready

    def _warn(self, text):
        self.said.append(text)

    def _explain(self, code, message):
        return f"{message}. See syncs()"

    @staticmethod
    def _reword(text):
        return text.replace("syncs()", "the Syncs tab")

    def _ready(self):
        return self.ready

    def _command_failed(self, exc, command):
        if isinstance(exc, ValueError):
            self.said.append(f"{command}: {exc}")
            return True
        return False

    @_window_errors
    def go(self, exc=None):
        """
        A command: raises exc when given one.
        """
        self.ran.append(exc)
        if exc is not None:
            raise exc
        return "went"


def test_window_guards_say_errors_in_the_window():
    w = _ToyWindow()
    denied = ClientError({"Error": {"Code": "AccessDenied", "Message": "not allowed"}}, "ListThings")
    w._safely(w.go)(denied)  # a callback's error goes to the window, in its words (the command lets it through)
    assert w.said == ["AccessDenied: not allowed. See the Syncs tab"]
    assert w._on_enter(lambda: w.said.append("entered"))(None, {"event": "blur"}, []) is None and len(w.said) == 1
    w._on_enter(lambda: w.said.append("entered"))(None, {"event": "submit"}, [])
    assert w.said[-1] == "entered" and w._error_text(_Hint("Try syncs()")) == "Try the Syncs tab"
    assert w.go() == "went" and w.go(ValueError("bad")) is None and w.said[-1] == "go: bad"
    with pytest.raises(KeyError):  # an error the window doesn't say still raises
        w.go(KeyError("k"))
    idle = _ToyWindow(ready=False)
    assert idle.go() is None and idle.ran == []  # a window that can't run a command doesn't start it


def test_reports_use_the_console_theme_until_another_is_picked():
    assert _theme_name() == DEFAULT_THEME == "console"
    assert set_theme(" Soft ") == "soft" == _theme_name()
    assert set_theme(None) == "console"


@pytest.mark.parametrize("name, message", [
    ("sofft", "'sofft' isn't a theme. Did you mean 'soft'?"),
    ("neon", "'neon' isn't a theme. The themes are 'console', 'soft', 'editorial', 'brand', 'classic'"),
])
def test_set_theme_says_what_it_takes(name, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        set_theme(name)
    with pytest.raises(TypeError, match="takes a theme's name"):
        set_theme(3)


def test_the_environment_can_pick_the_theme(monkeypatch):
    """
    set_theme() wins over AWS_ANALYZER_THEME, and None goes back to the environment's.
    """
    monkeypatch.setenv("AWS_ANALYZER_THEME", "editorial")
    assert _theme_name() == "editorial"
    assert set_theme("brand") == "brand"
    assert set_theme(None) == "editorial"
    monkeypatch.setenv("AWS_ANALYZER_THEME", "Neon")
    with pytest.warns(UserWarning, match="AWS_ANALYZER_THEME='Neon': .* Reports use 'console'."):
        assert _theme_name() == "console"


@pytest.mark.parametrize("theme", [name for name in THEMES if name != "classic"])
def test_a_themes_rules_reach_only_reports_drawn_in_it(theme):
    html = _report_html([_Title("Report")], 0, _Look("<style></style>", "ddb", "B"), theme=theme)
    scoped = f".ddb.theme-{theme}"
    assert html.startswith(f"<style></style><style>\n{scoped}{{")
    assert f'<div class="ddb theme-{theme}"><h3>' in html
    css = html[len("<style></style><style>"):html.index("</style>", 1)]
    assert css.count("{") == css.count("}")
    selectors = re.findall(r"(?:^|})\s*([^{}@]+){", css)
    unscoped = [part for group in selectors for part in group.split(",") if scoped not in part]
    assert not unscoped, f"rules that would restyle reports drawn in other themes: {unscoped}"


def test_background_jobs_tick_while_they_run():
    explorer = _Explorer()
    explorer._COUNT_EVERY = 0.01
    released, ticks, results = threading.Event(), [], []

    def work():
        released.wait(5)
        return "read"

    async def main():
        explorer._later("logs", work, results.append, ticking=lambda: ticks.append(len(ticks)))
        while len(ticks) < 3:
            await asyncio.sleep(0.01)
        released.set()
        await explorer._tasks["logs"]
        count = len(ticks)
        await asyncio.sleep(0.05)
        return count

    assert asyncio.run(main()) == len(ticks) >= 3 and results == ["read"]


def test_classic_and_the_windows_reports_draw_no_theme():
    """
    A window draws its reports without a theme (a service's _render_html with none named), as classic does.
    """
    plain = _report_html([_Title("Report")], 0, _Look("", "ddb", "B"))
    assert plain == _report_html([_Title("Report")], 0, _Look("", "ddb", "B"), theme="classic")
    assert plain.startswith('<div class="ddb"><h3>') and "theme-" not in plain
    assert "theme-" not in ddb_style._render_html([_Title("Report")], 0)


def test_a_view_draws_its_reports_in_the_theme_picked(monkeypatch):
    import IPython.display

    drawn = []
    monkeypatch.setattr(IPython.display, "display", lambda shown, **_: drawn.append(shown.data))
    view = _Toy()
    view.use_html = True
    view._render_html = ddb_style._render_html
    view._show([_Title("Report")])
    set_theme("editorial")
    view._show([_Title("Report")])
    assert '<div class="ddb theme-console">' in drawn[0] and ".ddb.theme-console{" in drawn[0]
    assert '<div class="ddb theme-editorial">' in drawn[1] and "theme-console" not in drawn[1]



def test_frecency_counts_often_and_lately():
    from datetime import datetime, timedelta, timezone

    from aws_analyzer._kit.history import Visit, frecency, rank_places

    now = datetime(2026, 10, 29, tzinfo=timezone.utc)
    assert frecency([now], now) == 1.0 and frecency([now - timedelta(days=7)], now) == 0.5
    daily = [Visit("s3://lake/daily/", "daily", now - timedelta(days=d)) for d in range(5)]
    march = [Visit("s3://lake/march/", "march", now - timedelta(days=210)) for _ in range(30)]
    once = [Visit("s3://lake/once/", "once", now - timedelta(hours=1))]
    ranked = rank_places(march + once + daily, now)
    assert [p.key for p in ranked] == ["s3://lake/daily/", "s3://lake/once/", "s3://lake/march/"]
    assert ranked[0].visits == 5 and ranked[0].last == now
    assert len(rank_places(march + once, now, limit=1)) == 1


def test_history_path_and_store(tmp_path, monkeypatch):
    from aws_analyzer._kit import history

    assert history.history_path(False) == "" and history.history_path(True) == ""
    monkeypatch.setenv(history.HISTORY_ENV, str(tmp_path / "h.sqlite"))
    assert history.history_path(True) == str(tmp_path / "h.sqlite")
    monkeypatch.delenv(history.HISTORY_ENV)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert history.history_path(True) == str(tmp_path / "aws-analyzer" / "history" / "places.sqlite")

    store = history.PlaceHistory(str(tmp_path / "places.sqlite"), "s3")
    assert store.places("111") == [] and store.last("111") is None
    store.record("111", "s3://a/", "a")
    store.record("111", "s3://b/", "b")
    store.record("222", "s3://other/", "other")
    assert [p.key for p in store.places("111")] == ["s3://b/", "s3://a/"] and store.last("111").key == "s3://b/"
    assert history.PlaceHistory(store.path, "lambda").places("111") == []
    assert store.forget("111", "s3://a/") == 1 and store.forget("111") == 1
    assert [p.key for p in store.places("222")] == ["s3://other/"]

    monkeypatch.setattr(history, "KEPT_VISITS", 3)
    for i in range(5):
        store.record("111", f"s3://k{i}/")
    assert len(store.visits("111")) == 3


def test_history_that_cant_be_written_turns_itself_off(tmp_path):
    from aws_analyzer._kit.history import PlaceHistory

    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    store = PlaceHistory(str(blocker / "places.sqlite"), "s3")
    store.record("111", "s3://a/")
    assert not store.is_on and "Recent places are off" in store.problem and store.places("111") == []
