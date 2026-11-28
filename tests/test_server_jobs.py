"""
aws-analyzer serve's Phase 3: a report's blocks as JSON (and the stylesheet they're drawn with), commands run as jobs
(progress, each report as it's shown, Stop, a second View when the first is busy) followed as Server-Sent Events,
and the live streams, read once however many pages follow them. The rest of the server is tests/test_server.py.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from aws_analyzer._kit.blockjson import _report_json
from aws_analyzer._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Tabs, _Title, _Tone
from aws_analyzer._kit.charts import _Bars
from aws_analyzer.lambda_functions import LambdaAnalyzer, LambdaView
from aws_analyzer.lambda_functions import style as lambda_style
from aws_analyzer.lambda_functions.blocks.wiring import _Wiring
from aws_analyzer.s3 import S3Analyzer, S3View
from aws_analyzer.server import feeds as feeds_module
from aws_analyzer.server import live as live_module
from aws_analyzer.server.app import TOKEN_HEADER, create_app
from aws_analyzer.server.feeds import FEEDS, Feed, FeedProblem, feed_params
from aws_analyzer.server.jobs import JobStopped
from aws_analyzer.server.live import LiveHub
from aws_analyzer.server.runner import VIEWS_PER_SERVICE, CantRun, ReportRunner, Settings, style_of
from aws_analyzer.server.streams import last_seen, sse_message

TOKEN = "t0ken-for-tests"
BUCKET = "acme-lake"
HEADERS = {TOKEN_HEADER: TOKEN}
WAIT_SECONDS = 10
DEMO = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "demo" / "demo.py"


@pytest.fixture
def aws():
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        s3.put_object(Bucket=BUCKET, Key="raw/events.csv", Body=b"id,amount\n1,10\n2,20\n")
        s3.put_object(Bucket=BUCKET, Key="raw/more/a.json", Body=b'{"a": 1}')
        yield s3


@pytest.fixture
def runner(aws):
    view = S3View(S3Analyzer(region="us-east-1"), mode="html", progress="off")
    return ReportRunner(Settings(region="us-east-1"), views={"s3": view})


@pytest.fixture
def client(runner):
    with TestClient(create_app(runner, TOKEN, cookie="aws_analyzer_8765")) as test_client:
        yield test_client


def _events(text: str) -> list[tuple[str, dict]]:
    """
    The messages of a Server-Sent Events body, heartbeats left out.
    """
    events = []
    for block in text.strip().split("\n\n"):
        lines = [line for line in block.splitlines() if not line.startswith(":")]
        if not lines:
            continue
        fields = dict(line.split(": ", 1) for line in lines)
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def _wait_until(done, seconds: float = WAIT_SECONDS) -> None:
    deadline = time.monotonic() + seconds
    while not done():
        if time.monotonic() > deadline:
            raise AssertionError("waited too long")
        time.sleep(0.02)


class Gate:
    """
    A command for the main View that ticks progress until the test lets it go (or Stop stops it), so a job can be
    watched while it runs.
    """

    def __init__(self, view: S3View, *, total: int = 100):
        self.view, self.total = view, total
        self.started, self.go = threading.Event(), threading.Event()
        self.ticks = 0

    def summary(self, uri: str = "", **options: object) -> None:
        with self.view._progress("Scanning", "objects") as tick:
            self.started.set()
            while not self.go.is_set():
                self.ticks += 1
                tick(min(self.ticks, self.total), self.total)
                time.sleep(0.01)
        self.view._show([_Title("Summary held back"), _Note("done")])


@pytest.fixture
def gate(runner):
    view = runner._services["s3"].lanes[0].view
    held = Gate(view)
    view.summary = held.summary
    yield held
    held.go.set()



def test_each_shared_block_comes_as_its_parts():
    blocks = [_Title("Lambda functions", "the last 30 days"), _Cards([("Calls", "1,234"), ("Errors", "3", "warn")]),
              _Findings([("info", "A note."), ("warn", "Something's wrong: why.")]),
              _Table(["name", "state"], [["etl", _Tone("failing", "bad")], ["api", "ok"]], title="Functions"),
              _Next([("errors('etl')", "its errors")]), _Bars(["mon"], [3.0], ["3"]),
              _Tabs([("One", [_Note("in a tab", "ok")])])]
    report = _report_json(blocks, 50, lambda_style._LOOK, theme="console")
    kinds = [block["kind"] for block in report["blocks"]]
    assert kinds == ["title", "cards", "findings", "table", "next", "bars", "tabs"]
    title, cards, findings, table, steps, bars, tabs = report["blocks"]
    assert title["badge"] == lambda_style._BADGE and title["sub"] == "the last 30 days"
    assert cards["items"][1] == {"label": "Errors", "value": "3", "tone": "warn"}
    assert [item["level"] for item in findings["items"]] == ["warn", "info"]
    assert table["rows"][0] == ["etl", {"text": "failing", "tone": "bad"}] and table["max_rows"] == 50
    assert steps["items"] == [{"call": "errors('etl')", "why": "its errors"}]
    assert bars["values"] == [3.0] and tabs["tabs"][0]["blocks"][0]["kind"] == "note"
    assert report["box_class"] == f"{lambda_style._ROOT} theme-console"
    assert json.loads(json.dumps(report)) == report


def test_a_block_with_no_json_drawing_comes_as_its_html():
    wiring = _Wiring([("SQS", "orders", "", "")], ("etl", "python3.12"), [])
    report = _report_json([wiring], 50, lambda_style._LOOK)
    block = report["blocks"][0]
    assert block["kind"] == "html" and block["block"] == "Wiring"
    assert "<style" not in block["html"]


def test_a_services_stylesheet_is_plain_css_with_its_themes_rules():
    css = style_of("lambda_functions")
    assert css.startswith(f".{lambda_style._ROOT}") and "<style" not in css and "</style" not in css


def test_a_run_gives_each_report_as_blocks_and_its_html(client):
    answer = client.post("/api/run", json={"service": "s3", "command": "ls", "arguments": {"uri": f"s3://{BUCKET}/"}},
                         headers=HEADERS).json()
    assert answer["html"].startswith("<style")
    kinds = [block["kind"] for block in answer["reports"][0]["blocks"]]
    assert kinds[0] == "title" and "table" in kinds
    style = client.get("/api/style/s3", headers=HEADERS).json()
    assert style["css"] and "<style" not in style["css"]
    assert client.get("/api/style/nothing", headers=HEADERS).status_code == 400
    assert client.get("/api/style/s3").status_code == 403



def test_a_job_answers_at_once_then_streams_its_progress_reports_and_end(client, gate):
    started = client.post("/api/jobs", json={"service": "s3", "command": "summary",
                                             "arguments": {"uri": f"s3://{BUCKET}/"}}, headers=HEADERS).json()
    assert started["state"] == "running" and started["call"] == f"summary('s3://{BUCKET}/')"
    assert gate.started.wait(WAIT_SECONDS)
    _wait_until(lambda: client.get(f"/api/jobs/{started['id']}", headers=HEADERS).json()["progress"])
    listed = client.get("/api/jobs", headers=HEADERS).json()["jobs"]
    assert [job["id"] for job in listed] == [started["id"]] and listed[0]["state"] == "running"
    gate.go.set()
    text = client.get(f"/api/jobs/{started['id']}/events?token={TOKEN}").text
    events = _events(text)
    kinds = [kind for kind, _ in events]
    assert kinds[0] == "progress" and kinds[-2:] == ["report", "done"]
    progress = events[0][1]
    assert progress["label"] == "Scanning" and progress["unit"] == "objects" and progress["total"] == 100
    report = events[-2][1]
    assert "Summary held back" in report["html"] and report["report"]["blocks"][0]["kind"] == "title"
    assert events[-1][1]["state"] == "done"
    assert "id: 1\n" in text


def test_stop_ends_a_job_at_its_next_tick_and_says_so(client, gate):
    job = client.post("/api/jobs", json={"service": "s3", "call": f"summary('s3://{BUCKET}/')"},
                      headers=HEADERS).json()
    assert gate.started.wait(WAIT_SECONDS)
    stopping = client.delete(f"/api/jobs/{job['id']}", headers=HEADERS).json()
    assert stopping["stopping"] is True
    events = _events(client.get(f"/api/jobs/{job['id']}/events", headers=HEADERS).text)
    kinds = [kind for kind, _ in events]
    assert "note" in kinds and kinds[-1] == "done"
    assert events[-1][1]["state"] == "stopped"
    report = next(data for kind, data in events if kind == "report")
    assert "Stopped before it finished" in report["html"]
    assert "_progress" not in vars(gate.view) and "_show" not in vars(gate.view)


def test_a_stream_opened_late_or_again_gets_what_it_missed(client, gate):
    job = client.post("/api/jobs", json={"service": "s3", "command": "summary", "arguments": {"uri": BUCKET}},
                      headers=HEADERS).json()
    gate.go.set()
    whole = _events(client.get(f"/api/jobs/{job['id']}/events?token={TOKEN}").text)
    again = _events(client.get(f"/api/jobs/{job['id']}/events?token={TOKEN}",
                               headers={"Last-Event-ID": str(len(whole) - 1)}).text)
    assert again == whole[-1:]
    after_the_end = _events(client.get(f"/api/jobs/{job['id']}/events?token={TOKEN}&after={len(whole)}").text)
    assert after_the_end == [("done", {"state": "done", "seconds": after_the_end[0][1]["seconds"]})]
    snapshot = client.get(f"/api/jobs/{job['id']}?after=1", headers=HEADERS).json()
    assert [event["number"] for event in snapshot["events_after"]] == list(range(2, len(whole) + 1))


def test_a_second_command_runs_on_a_view_beside_the_busy_one(runner, gate):
    job = runner.start_job("s3", "summary", {"uri": BUCKET})
    assert gate.started.wait(WAIT_SECONDS)
    report = runner.run("s3", "ls", {"uri": f"s3://{BUCKET}/raw/"})
    assert "events.csv" in report.html and job.is_running
    lanes = runner._services["s3"].lanes
    assert len(lanes) == 2 and lanes[1].view.core is lanes[0].view.core
    gate.go.set()
    _wait_until(lambda: not job.is_running)
    assert job.state == "done" and VIEWS_PER_SERVICE >= 2


def test_a_job_that_cant_run_is_refused_before_it_starts(client, runner):
    refused = client.post("/api/jobs", json={"service": "s3", "command": "summary", "arguments": {}}, headers=HEADERS)
    assert refused.status_code == 400 and "Fill in uri" in refused.json()["message"]
    assert client.post("/api/jobs", json={"service": "s3", "call": "download('x')"}, headers=HEADERS).status_code == 400
    assert client.get("/api/jobs/nope", headers=HEADERS).status_code == 404
    assert client.delete("/api/jobs/nope", headers=HEADERS).status_code == 404
    assert client.get("/api/jobs/nope/events").status_code == 403
    assert client.post("/api/jobs", json={"service": "s3", "command": "ls"}).status_code == 403
    with pytest.raises(CantRun):
        runner.start_job("s3", "nothing")
    assert runner.jobs.all() == []


def test_a_real_scan_sends_progress_and_its_report(client):
    job = client.post("/api/jobs", json={"service": "s3", "command": "summary",
                                         "arguments": {"uri": f"s3://{BUCKET}/"}}, headers=HEADERS).json()
    events = _events(client.get(f"/api/jobs/{job['id']}/events?token={TOKEN}").text)
    kinds = [kind for kind, _ in events]
    assert kinds[-1] == "done" and events[-1][1]["state"] == "done"
    assert any("raw" in data["html"] for kind, data in events if kind == "report")


def test_stopped_is_a_base_exception_so_a_commands_guard_lets_it_through():
    assert not issubclass(JobStopped, Exception)


def test_the_last_event_seen_comes_from_the_browser_else_the_address():
    assert last_seen("4", 2) == 4 and last_seen(None, 2) == 2 and last_seen("x", None) == 0
    assert sse_message("note", {"a": 1}, 3) == 'id: 3\nevent: note\ndata: {"a":1}\n\n'



def _counting_feed(reads: list[int], *, every: float = 0.02) -> Feed:
    def make(core: object, params: dict[str, str]):
        def read() -> dict:
            reads.append(1)
            return {"n": len(reads), **params}

        return read

    return Feed("s3", "count", every, "a count", make, ("name",), 1)


def test_a_live_stream_is_read_once_for_every_page_and_stops_when_they_leave(runner):
    reads: list[int] = []
    feed = _counting_feed(reads)
    hub = LiveHub(runner)

    async def follow_twice() -> tuple[list[str], list[str]]:
        stream, first = hub.follow(feed, {"name": "a"})
        same, second = hub.follow(feed, {"name": "a"})
        assert same is stream
        got = [await asyncio.wait_for(first.get(), WAIT_SECONDS) for _ in range(3)]
        also = [await asyncio.wait_for(second.get(), WAIT_SECONDS) for _ in range(3)]
        hub.leave(stream, first)
        hub.leave(stream, second)
        await asyncio.wait_for(stream.task, WAIT_SECONDS)
        assert hub._streams == {}
        return got, also

    got, also = asyncio.run(follow_twice())
    assert got == also and len(reads) <= 5
    assert [json.loads(message.split("data: ")[1])["n"] for message in got] == [1, 2, 3]


def test_a_newcomer_gets_the_newest_sample_at_once(runner):
    reads: list[int] = []
    hub = LiveHub(runner)

    async def late() -> str:
        stream, first = hub.follow(_counting_feed(reads, every=5), {"name": "a"})
        await asyncio.wait_for(first.get(), WAIT_SECONDS)
        _same, second = hub.follow(stream.feed, {"name": "a"})
        newest = second.get_nowait()
        await hub.close()
        return newest

    assert '"n":1' in asyncio.run(late()) and reads == [1]


def test_a_read_that_fails_is_a_note_and_the_stream_goes_on(runner):
    def make(core: object, params: dict[str, str]):
        raise FeedProblem("There's no queue called nope.")

    hub = LiveHub(runner)

    async def follow() -> str:
        stream, queue = hub.follow(Feed("s3", "broken", 0.02, "", make), {})
        message = await asyncio.wait_for(queue.get(), WAIT_SECONDS)
        await hub.close()
        return message

    assert asyncio.run(follow()).startswith("event: note\ndata: ") and "no queue called nope" in asyncio.run(follow())


def test_a_live_stream_from_the_page_starts_says_what_it_is_and_ends(client, monkeypatch):
    reads: list[int] = []
    monkeypatch.setitem(FEEDS, ("s3", "count"), _counting_feed(reads))
    monkeypatch.setattr(live_module, "LIVE_MINUTES", 0.002)
    events = _events(client.get(f"/api/live/s3/count?name=x&token={TOKEN}").text)
    kinds = [kind for kind, _ in events]
    assert kinds[0] == "start" and kinds[-1] == "done" and "sample" in kinds
    assert events[0][1]["params"] == {"name": "x"} and events[1][1]["name"] == "x"
    listed = client.get("/api/live", headers=HEADERS).json()["feeds"]
    assert {(feed["service"], feed["feed"]) for feed in listed} >= {("lambda_functions", "activity"),
                                                                   ("messaging", "queue")}


def test_a_live_stream_needs_its_token_a_feed_there_is_and_what_it_needs(client):
    assert client.get("/api/live/lambda_functions/activity").status_code == 403
    assert client.get(f"/api/live/s3/nothing?token={TOKEN}").status_code == 404
    missing = client.get(f"/api/live/lambda_functions/lines?token={TOKEN}")
    assert missing.status_code == 400 and "needs name" in missing.json()["message"]
    unknown = client.get(f"/api/live/lambda_functions/activity?region=x&token={TOKEN}")
    assert unknown.status_code == 400 and "takes no region" in unknown.json()["message"]


def test_feed_parameters_are_checked_and_trimmed():
    feed = FEEDS[("lambda_functions", "lines")]
    assert feed_params(feed, {"name": " etl "}) == {"name": "etl"}
    with pytest.raises(FeedProblem):
        feed_params(feed, {})


@pytest.fixture
def demo_lambda():
    spec = importlib.util.spec_from_file_location("demo", DEMO)
    demo = importlib.util.module_from_spec(spec)
    sys.modules["demo"] = demo
    spec.loader.exec_module(demo)
    with mock_aws():
        clients = demo.seed_lambda_functions() or {}
        yield LambdaAnalyzer(region="us-east-1", **clients)


def test_lambdas_live_feeds_read_activity_and_new_lines(demo_lambda):
    activity = FEEDS[("lambda_functions", "activity")].make(demo_lambda, {})()
    names = {row["name"] for row in activity["functions"]}
    assert activity["quiet"] + len(names) == len(demo_lambda.list_functions("us-east-1"))
    assert activity["minutes"] == 10 and activity["read_at"].endswith("Z")
    lines = FEEDS[("lambda_functions", "lines")].make(demo_lambda, {"name": "orders-etl"})
    first, second = lines(), lines()
    assert first["function"] == "orders-etl" and first["timeout"] == 60
    assert second["lines"] == [] or len(second["lines"]) < len(first["lines"])
    assert json.loads(json.dumps(first)) == first


def test_a_live_feed_on_the_runner_uses_its_views_analyzer(demo_lambda):
    view = LambdaView(demo_lambda, mode="html", progress="off")
    runner = ReportRunner(Settings(region="us-east-1"), views={"lambda_functions": view})
    assert runner.core("lambda_functions") is demo_lambda
    assert feeds_module.ACTIVITY_SECONDS >= 60
