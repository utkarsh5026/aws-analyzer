"""
aws-analyzer serve (src/aws_analyzer/server/): the commands the page offers, how its boxes become arguments, the
runner on moto's S3, the FastAPI app (its token, its headers, its answers), the address it prints on SageMaker and
the terminal command. The page's own script is driven in a real browser by hand (see CLAUDE.md), not here.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import NoRegionError
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from moto import mock_aws

from aws_analyzer.lambda_functions import LambdaView
from aws_analyzer.s3 import S3Analyzer, S3View
from aws_analyzer.server import cli, ping
from aws_analyzer.server.app import POLICY, TOKEN_HEADER, create_app
from aws_analyzer.server.catalog import (
    NOT_SERVED, SERVICES, Parameter, argument_value, served_names, view_commands,
)
from aws_analyzer.server.links import page_link, read_metadata
from aws_analyzer.server.runner import CantRun, ReportRunner, Settings, _form_arguments

TOKEN = "t0ken-for-tests"
BUCKET = "acme-lake"
STATIC = Path(__file__).resolve().parents[1] / "src" / "aws_analyzer" / "server" / "static"


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
    return TestClient(create_app(runner, TOKEN, cookie="aws_analyzer_8765"))


def _run(client: TestClient, **body: object):
    return client.post("/api/run", json=body, headers={TOKEN_HEADER: TOKEN})




def test_every_service_offers_its_report_commands_and_none_that_change_anything():
    for service in SERVICES:
        view_class = getattr(service.module(), service.view_name)
        names = served_names(view_class)
        assert names, service.key
        assert not set(names) & (NOT_SERVED | set(view_class._CHANGES))
        groups = [command.group for command in view_commands(view_class)]
        known = [*view_class._GROUPS, "Other"]
        assert groups == sorted(groups, key=known.index), f"{service.key}'s commands aren't in help()'s order"
    assert "download" not in served_names(S3View) and "invoke" not in served_names(LambdaView)
    assert {"errors", "logs", "performance"} <= set(served_names(LambdaView))


def test_a_command_lists_its_parameters_as_the_form_shows_them():
    errors = next(command for command in view_commands(LambdaView) if command.name == "errors")
    params = {param.name: param for param in errors.parameters}
    assert params["name"] == Parameter("name", "", True, "text", True)
    assert params["since"].default == "'24h'" and not params["since"].positional
    assert errors.about.startswith("A function's errors") and "\n" not in errors.about
    assert errors.signature.startswith("(name, *, since='24h'")


@pytest.mark.parametrize(("text", "default", "kind", "expected"), [
    ("orders-etl", "", "text", "orders-etl"),
    ("42", "", "text", "42"),
    ("['a', 'b']", "", "text", ["a", "b"]),
    ("7d", "'24h'", "text", "7d"),
    ("1,000", "10000", "text", 1000),
    ("250", "None", "text", 250),
    ("s3://b/x", "None", "text", "s3://b/x"),
    ("None", "None", "text", None),
    ("yes", "False", "flag", True),
    ("false", "True", "flag", False),
])
def test_a_box_becomes_the_value_the_command_gets(text, default, kind, expected):
    assert argument_value(text, Parameter("p", default, not default, kind)) == expected


def test_the_boxes_go_in_order_while_they_can_then_by_name():
    params = (Parameter("uri", "", True, "text"), Parameter("limit", "None", False, "text", False))
    assert _form_arguments(params, {"uri": "s3://b/", "limit": "5"}) == (("s3://b/",), {"limit": 5})
    assert _form_arguments(params, {"uri": "s3://b/", "limit": " "}) == (("s3://b/",), {})
    with pytest.raises(CantRun, match="Fill in uri first"):
        _form_arguments(params, {"limit": "5"})
    with pytest.raises(CantRun, match="No parameter called depth"):
        _form_arguments(params, {"uri": "s3://b/", "depth": "2"})




def test_running_a_command_gives_the_notebooks_report_as_html(runner):
    report = runner.run("s3", "ls", {"uri": f"s3://{BUCKET}/raw/", "limit": "100"})
    assert report.call == f"ls('s3://{BUCKET}/raw/', limit=100)"
    assert report.arguments == {"uri": f"s3://{BUCKET}/raw/", "limit": "100"}
    assert report.html.startswith("<style>") and "events.csv" in report.html
    assert report.seconds >= 0


def test_a_call_runs_as_written_in_a_notebook_and_fills_the_form(runner):
    report = runner.run_call("s3", f"ui.summary('s3://{BUCKET}/', top_n=3)")
    assert report.command == "summary" and report.arguments == {"uri": f"s3://{BUCKET}/", "top_n": "3"}
    assert "Summary of" in report.html


@pytest.mark.parametrize(("call", "said"), [
    (f"download('s3://{BUCKET}/raw/events.csv')", "can't run download"),
    ("explore()", "can't run explore"),
    ("__import__('os').system('true')", "isn't one command"),
    ("ls(open('x'))", "isn't one command"),
])
def test_a_call_that_writes_opens_a_window_or_isnt_plain_is_refused(runner, call, said):
    with pytest.raises(CantRun, match=said):
        runner.run_call("s3", call)


def test_an_unknown_service_or_command_is_refused(runner):
    with pytest.raises(CantRun, match="no service called 'ec2'"):
        runner.run("ec2", "instances")
    with pytest.raises(CantRun, match="can't run to_excel"):
        runner.run("s3", "to_excel")


def test_an_aws_error_is_the_views_own_note(runner):
    report = runner.run("s3", "ls", {"uri": "s3://no-such-bucket/"})
    assert "note warn" in report.html and "NoSuchBucket" in report.html


def test_a_command_that_breaks_is_a_note_and_the_traceback_goes_to_the_log(runner, monkeypatch, caplog):
    def broken(self, *args, **kwargs):
        raise RuntimeError("the disk caught fire")

    monkeypatch.setattr(S3View, "buckets", broken)
    with caplog.at_level(logging.ERROR, logger="aws_analyzer.server"):
        report = runner.run("s3", "buckets")
    assert "RuntimeError:</span> the disk caught fire" in report.html and "terminal" in report.html
    assert "Traceback" in caplog.text
    assert "_show" not in vars(runner._services["s3"].view)


def test_a_view_that_cant_start_says_why_and_is_tried_again_next_time(aws, monkeypatch):
    runner = ReportRunner()
    calls = []

    def no_region(key):
        calls.append(key)
        raise NoRegionError()

    monkeypatch.setattr(runner, "_make_view", no_region)
    report = runner.run("lambda_functions", "functions")
    assert "start Lambda:</span> You must specify a region. Check the region (--region)" in report.html
    runner.run("lambda_functions", "functions")
    assert calls == ["lambda_functions", "lambda_functions"]


def test_views_are_made_once_per_service_from_the_settings(aws):
    runner = ReportRunner(Settings(region="us-east-1", max_rows=7))
    runner.run("s3", "buckets")
    view = runner._services["s3"].view
    runner.run("s3", "buckets")
    assert runner._services["s3"].view is view
    assert view.max_rows == 7 and view.progress == "off" and view.core.region == "us-east-1"




def test_the_page_needs_its_link_once_then_the_cookie_keeps_it(client):
    locked = client.get("/")
    assert locked.status_code == 403 and "?token=" in locked.text and TOKEN not in locked.text
    opened = client.get(f"/?token={TOKEN}")
    assert opened.status_code == 200 and f'content="{TOKEN}"' in opened.text
    assert "httponly" in opened.headers["set-cookie"].lower() and "samesite=strict" in opened.headers["set-cookie"].lower()
    assert client.get("/").status_code == 200
    assert TestClient(client.app).get("/?token=wrong").status_code == 403
    assert TestClient(client.app).get("/?token=caf%C3%A9").status_code == 403


def test_the_api_needs_the_token_in_its_header(client):
    assert client.get("/api/catalog").status_code == 403
    assert client.post("/api/run", json={"service": "s3", "command": "buckets"}).json()["message"].startswith(
        "This needs the page's token")
    client.cookies.set("aws_analyzer_8765", TOKEN)
    assert client.get("/api/catalog").status_code == 403


def test_every_answer_runs_only_the_servers_own_scripts(client):
    for response in (client.get("/"), client.get("/static/app.js"), client.get("/api/catalog")):
        assert response.headers["content-security-policy"] == POLICY
        assert response.headers["x-content-type-options"] == "nosniff"
    assert "script-src 'self';" in POLICY and "unsafe-eval" not in POLICY


def test_the_catalog_lists_every_service_with_its_commands(client):
    catalog = client.get("/api/catalog", headers={TOKEN_HEADER: TOKEN}).json()
    assert [service["key"] for service in catalog["services"]] == [service.key for service in SERVICES]
    assert catalog["region"] == "us-east-1"
    lambda_service = next(service for service in catalog["services"] if service["key"] == "lambda_functions")
    errors = next(command for command in lambda_service["commands"] if command["name"] == "errors")
    assert errors["parameters"][0] == {"name": "name", "default": "", "required": True, "kind": "text",
                                       "positional": True}


def test_running_from_the_page(client):
    form = _run(client, service="s3", command="ls", arguments={"uri": f"s3://{BUCKET}/raw/"})
    assert form.status_code == 200 and form.json()["call"] == f"ls('s3://{BUCKET}/raw/')"
    step = _run(client, service="s3", call=f"ui.ls('s3://{BUCKET}/raw/more/')")
    assert step.status_code == 200 and "a.json" in step.json()["html"]
    refused = _run(client, service="s3", call="clean_downloads()")
    assert refused.status_code == 400 and "can't run clean_downloads" in refused.json()["message"]
    assert _run(client, service="s3", command="ls").json()["message"].startswith("Fill in uri first")


def test_the_pages_files_are_served_and_what_it_names_is_there(client):
    page = client.get(f"/?token={TOKEN}").text
    assert client.get("/static/app.js").status_code == 200
    for service in SERVICES:
        assert (STATIC / "icons" / f"{service.icon}.svg").exists()
    for name in ("app.css", "app.js", "icons/favicon.svg"):
        assert f"static/{name}" in page or name.startswith("icons/") and (STATIC / name).exists()
    assert client.get("/static/../app.py").status_code == 404


@pytest.fixture
def quick_pings(monkeypatch):
    monkeypatch.setattr(ping, "PING_SECONDS", 0.05)
    monkeypatch.setattr(ping, "PINGS", 3)


def _events(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def test_the_proxy_check_page_needs_the_link_and_names_its_files(client):
    assert client.get("/ping").status_code == 403
    page = client.get(f"/ping?token={TOKEN}")
    assert page.status_code == 200 and f'content="{TOKEN}"' in page.text
    assert "static/ping.js" in page.text and "static/ping.css" in page.text and "socket_library" in page.text
    assert client.get("/ping").status_code == 200
    for name in ("ping.js", "ping.css"):
        assert client.get(f"/static/{name}").status_code == 200
    assert page.headers["Content-Security-Policy"] == POLICY


def test_the_ping_stream_sends_a_line_at_a_time_then_done(client, quick_pings):
    assert client.get("/api/ping-stream").status_code == 403
    assert client.get("/api/ping-stream?token=wrong").status_code == 403
    answer = client.get(f"/api/ping-stream?token={TOKEN}")
    assert answer.headers["content-type"].startswith("text/event-stream")
    assert answer.headers["x-accel-buffering"] == "no"
    events = _events(answer.text)
    assert [kind for kind, _ in events] == ["ping", "ping", "ping", "done"]
    assert [data["n"] for _, data in events[:3]] == [1, 2, 3]
    assert events[2][1]["sent"] - events[0][1]["sent"] >= 90
    assert client.get("/api/ping-stream", headers={TOKEN_HEADER: TOKEN}).status_code == 200


def test_the_ping_socket_wants_the_token_first_then_ticks_and_answers(client, quick_pings):
    with client.websocket_connect("/api/ping-socket") as socket:
        socket.send_text(json.dumps({"token": "wrong"}))
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
    assert closed.value.code == ping.WRONG_TOKEN

    with client.websocket_connect("/api/ping-socket") as socket:
        socket.send_text(json.dumps({"token": TOKEN}))
        assert socket.receive_json()["kind"] == "hello"
        socket.send_text("1")
        messages = []
        with pytest.raises(WebSocketDisconnect) as closed:
            while True:
                messages.append(socket.receive_json())
    assert closed.value.code == 1000
    assert [message["n"] for message in messages if message["kind"] == "tick"] == [1, 2, 3]
    assert [message["said"] for message in messages if message["kind"] == "pong"] == ["1"]


def test_the_server_says_which_websocket_library_it_has(monkeypatch):
    assert ping.socket_library().startswith(("websockets", "wsproto"))
    monkeypatch.setattr(ping.importlib.util, "find_spec", lambda name: None)
    assert ping.socket_library() == ""
    assert '&quot;socket_library&quot;: &quot;none&quot;' in ping.ping_page_html(TOKEN)


def test_on_sagemaker_the_terminal_also_gives_the_proxy_checks_link():
    metadata = {"ResourceArn": "arn:aws:sagemaker:ap-south-1:123456789012:notebook-instance/test2",
                "ResourceName": "test2"}
    link = page_link(8765, "abc", metadata=metadata, find_address=lambda name, region: "test2-kbmv.notebook."
                                                                                         "ap-south-1.sagemaker.aws")
    shown = cli.welcome(link, Settings(region="ap-south-1"), "127.0.0.1")
    assert "https://test2-kbmv.notebook.ap-south-1.sagemaker.aws/proxy/8765/ping?token=abc" in shown
    local = cli.welcome(page_link(8765, "abc", metadata={}), Settings(region="ap-south-1"), "127.0.0.1")
    assert "ping" not in local




def test_on_a_notebook_instance_the_link_goes_through_its_own_address():
    metadata = {"ResourceArn": "arn:aws:sagemaker:eu-west-1:123456789012:notebook-instance/churn-nb",
                "ResourceName": "churn-nb"}
    link = page_link(8765, "abc", metadata=metadata)
    assert link.url == "https://churn-nb.notebook.eu-west-1.sagemaker.aws/proxy/8765/?token=abc"
    assert link.local == "http://127.0.0.1:8765/?token=abc" and link.is_proxied
    assert link.where == "notebook instance churn-nb" and link.is_guess


def test_a_notebook_instance_link_uses_the_address_sagemaker_gives_it():
    metadata = {"ResourceArn": "arn:aws:sagemaker:ap-south-1:123456789012:notebook-instance/test2",
                "ResourceName": "test2"}
    asked = []

    def find_address(name, region):
        asked.append((name, region))
        return "test2-kbmv.notebook.ap-south-1.sagemaker.aws"

    link = page_link(8765, "abc", metadata=metadata, find_address=find_address)
    assert link.url == "https://test2-kbmv.notebook.ap-south-1.sagemaker.aws/proxy/8765/?token=abc"
    assert asked == [("test2", "ap-south-1")] and not link.is_guess
    shown = cli.welcome(link, Settings(region="ap-south-1"), "127.0.0.1")
    assert "guessed" not in shown


def test_when_sagemaker_doesnt_say_the_address_the_guess_says_how_to_mend_it():
    metadata = {"ResourceArn": "arn:aws:sagemaker:ap-south-1:123456789012:notebook-instance/test2",
                "ResourceName": "test2"}
    link = page_link(8765, "abc", metadata=metadata, find_address=lambda name, region: "")
    assert link.url == "https://test2.notebook.ap-south-1.sagemaker.aws/proxy/8765/?token=abc" and link.is_guess
    shown = cli.welcome(link, Settings(region="ap-south-1"), "127.0.0.1")
    assert "guessed from its name" in shown and "/proxy/8765/?token=abc" in shown


def test_the_notebook_instance_address_is_read_from_sagemaker(aws):
    import boto3

    from aws_analyzer.server.links import notebook_instance_address

    sagemaker = boto3.client("sagemaker", region_name="ap-south-1")
    sagemaker.create_notebook_instance(NotebookInstanceName="test2", InstanceType="ml.t3.medium",
                                       RoleArn="arn:aws:iam::123456789012:role/sm")
    address = notebook_instance_address("test2", "ap-south-1")
    assert address.endswith(".notebook.ap-south-1.sagemaker.aws") and not address.startswith("https://")
    assert notebook_instance_address("missing", "ap-south-1") == ""


@pytest.mark.parametrize(("app_type", "path"), [("JupyterLab", "jupyterlab/default"),
                                                ("CodeEditor", "codeeditor/default"),
                                                ("KernelGateway", "jupyter/default")])
def test_in_studio_the_link_goes_through_the_domains_address(app_type, path):
    metadata = {"ResourceArn": f"arn:aws:sagemaker:us-east-1:123456789012:app/d-abc123/churn/{app_type}/default",
                "DomainId": "d-abc123", "AppType": app_type, "SpaceName": "churn"}
    link = page_link(8800, "abc", metadata=metadata)
    assert link.url == f"https://d-abc123.studio.us-east-1.sagemaker.aws/{path}/proxy/8800/?token=abc"


def test_elsewhere_the_link_is_the_local_address(tmp_path):
    assert page_link(8765, "abc", metadata={}).url == "http://127.0.0.1:8765/?token=abc"
    assert not page_link(8765, "abc", metadata={}).is_proxied
    broken = tmp_path / "resource-metadata.json"
    broken.write_text("{not json")
    assert read_metadata(broken) == {} and read_metadata(tmp_path / "missing.json") == {}
    broken.write_text(json.dumps(["a list"]))
    assert read_metadata(broken) == {}




def test_a_taken_port_moves_to_the_next_free_one():
    import socket

    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        port = taken.getsockname()[1]
        assert cli.free_port("127.0.0.1", port) != port


def test_serve_prints_the_link_then_runs_the_app(monkeypatch, capsys):
    started = {}
    monkeypatch.setattr(cli, "free_port", lambda host, wanted: wanted + 1)
    monkeypatch.setattr(cli, "page_link",
                        lambda port, token, host, **_: page_link(port, token, host=host, metadata={}))
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **options: started.update(app=app, **options))
    assert cli.main(["serve", "--region", "eu-west-1", "--port", "9000"]) == 0
    shown = capsys.readouterr().out
    assert "Open: http://127.0.0.1:9001/?token=" in shown and "port 9000 was taken" in shown
    assert "Reads AWS in eu-west-1" in shown and "Ctrl+C stops it." in shown and shown.rstrip().endswith("Stopped.")
    assert started["host"] == "127.0.0.1" and started["port"] == 9001
    token = shown.split("?token=")[1].split()[0]
    assert TestClient(started["app"]).get(f"/?token={token}").status_code == 200


def test_listening_beyond_this_machine_says_who_can_reach_it():
    link = page_link(8765, "abc", host="0.0.0.0", metadata={})
    shown = cli.welcome(link, Settings(region="us-east-1"), "0.0.0.0")
    assert "anyone who can reach it" in shown


def test_without_a_command_it_shows_its_help(capsys):
    assert cli.main([]) == 2
    assert "serve" in capsys.readouterr().out


def test_python_dash_m_runs_the_same_command():
    src = Path(__file__).resolve().parents[1] / "src"
    shown = subprocess.run([sys.executable, "-m", "aws_analyzer", "serve", "--help"], cwd=src, capture_output=True,
                           text=True, check=True).stdout
    assert "--port" in shown and "--region" in shown
