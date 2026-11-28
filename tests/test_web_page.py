"""
The web page (`aws-analyzer serve`, built from web/) in a real browser: Chromium through Playwright, on the real
server running on uvicorn, over demo.py's S3 and Lambda data in moto. Each test does what someone using the page
does (search a service, open what it finds in a tab, switch the theme, pick a command, fill its form, run it, go back,
follow a next step, find a command or a name with Ctrl K) and checks what they'd see. Every test also fails on an error in the page's script or a Content-Security-Policy refusal.

They need Playwright's Chromium (`python -m playwright install chromium`) and skip without it, except where
AWS_ANALYZER_PAGE_TESTS=required, as in CI's Page tests job, where a missing browser fails them. A Chromium already
on the machine can be named with AWS_ANALYZER_TEST_CHROMIUM (its executable's path).
"""

from __future__ import annotations

import importlib.util
import os
import re
import socket
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import uvicorn
from moto import mock_aws

from aws_analyzer._kit.blocks import _Title
from aws_analyzer.lambda_functions import LambdaAnalyzer, LambdaView
from aws_analyzer.s3 import S3Analyzer, S3View
from aws_analyzer.server.app import create_app
from aws_analyzer.server.runner import ReportRunner, Settings

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / ".claude" / "skills" / "demo" / "demo.py"
TOKEN = "page-test-token"
BUCKET_FOLDER = "s3://demo-lake/raw/"
REQUIRED = os.environ.get("AWS_ANALYZER_PAGE_TESTS") == "required"
CHROMIUM = os.environ.get("AWS_ANALYZER_TEST_CHROMIUM")
SERVER_START_SECONDS = 20
WAIT_MS = 15_000
FAILED_REQUEST = "Failed to load resource"

pytestmark = pytest.mark.xdist_group("web_page")


def _missing(reason: str) -> None:
    if REQUIRED:
        pytest.fail(f"{reason} (AWS_ANALYZER_PAGE_TESTS=required)")
    pytest.skip(reason)


try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import Page, expect, sync_playwright
except ImportError:
    sync_playwright = None


def _demo_module():
    spec = importlib.util.spec_from_file_location("demo", DEMO)
    module = importlib.util.module_from_spec(spec)
    sys.modules["demo"] = module
    spec.loader.exec_module(module)
    return module


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _demo_views() -> dict:
    """
    Views on demo.py's S3 and Lambda data, seeded in the moto mock that's running.
    """
    demo = _demo_module()
    demo.seed_s3()
    lambda_clients = demo.seed_lambda_functions() or {}
    return {"s3": S3View(S3Analyzer(region="us-east-1"), mode="html", progress="off"),
            "lambda_functions": LambdaView(LambdaAnalyzer(region="us-east-1", **lambda_clients), mode="html",
                                           progress="off")}


def _wait_until_started(server: uvicorn.Server) -> None:
    deadline = time.monotonic() + SERVER_START_SECONDS
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("The page's server didn't start.")
        time.sleep(0.05)


SERVED_VIEWS: dict = {}


@pytest.fixture(scope="module")
def served() -> Iterator[str]:
    """
    The page's address: the real app on uvicorn, on a free port, in a thread, over demo data in moto. Its Views are in
    SERVED_VIEWS, for a test that holds a command back.
    """
    with mock_aws():
        SERVED_VIEWS.update(_demo_views())
        runner = ReportRunner(Settings(region="us-east-1"), views=SERVED_VIEWS)
        port = _free_port()
        config = uvicorn.Config(create_app(runner, TOKEN, cookie=f"aws_analyzer_{port}"), host="127.0.0.1",
                                port=port, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        _wait_until_started(server)
        yield f"http://127.0.0.1:{port}"
        server.should_exit = True
        thread.join(timeout=SERVER_START_SECONDS)
        SERVED_VIEWS.clear()


def _launch(playwright):
    options = {"executable_path": CHROMIUM} if CHROMIUM else {}
    try:
        return playwright.chromium.launch(**options)
    except PlaywrightError as error:
        _missing(f"No Chromium for Playwright: {str(error).splitlines()[0]}")


@pytest.fixture(scope="module")
def chromium():
    if sync_playwright is None:
        _missing("Playwright isn't installed (pip install -r requirements-dev.txt)")
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        yield browser
        browser.close()


@dataclass
class Troubles:
    """
    What went wrong in the page while a test used it: script errors and what its Content-Security-Policy refused.
    """

    errors: list[str] = field(default_factory=list)


@pytest.fixture
def troubles() -> Troubles:
    return Troubles()


def _page_in(chromium, troubles: Troubles, *, scheme: str = "light") -> Page:
    page = chromium.new_page(viewport={"width": 1280, "height": 900}, color_scheme=scheme)
    page.set_default_timeout(WAIT_MS)
    page.on("pageerror", lambda error: troubles.errors.append(f"script error: {error}"))
    page.on("console", lambda message: _note_console(message, troubles))
    return page


def _note_console(message, troubles: Troubles) -> None:
    """
    Notes a console error: the page's own, or what its Content-Security-Policy refused ("Refused to ..."). The
    browser's line for an answer that wasn't 200 isn't one: a test that makes the server refuse a call expects it.
    """
    is_failed_request = message.text.startswith(FAILED_REQUEST)
    if message.type == "error" and not is_failed_request:
        troubles.errors.append(f"console: {message.text}")


@pytest.fixture
def page(chromium, troubles: Troubles) -> Iterator[Page]:
    page = _page_in(chromium, troubles)
    yield page
    page.close()
    assert troubles.errors == []


def _open(page: Page, served: str, hash_: str = "") -> None:
    page.goto(f"{served}/?token={TOKEN}{hash_}")
    page.locator(".aa-home, .aa-svc").first.wait_for()


def _status(page: Page):
    return page.locator(".aa-status")


def _wait_for_report(page: Page) -> None:
    expect(page.locator(".aa-status button", has_text="Run again")).to_be_visible()
    expect(page.locator(".aa-report")).to_be_visible()


def _hash(page: Page) -> str:
    return page.evaluate("decodeURIComponent(location.hash)")


def test_the_home_page_searches_one_service_and_reads_only_it(page, served):
    _open(page, served)
    expect(page.locator(".aa-home-title")).to_have_text("Find anything in your account")
    expect(page.locator(".aa-results-head")).to_contain_text("functions in Lambda")
    expect(page.locator(".aa-chip", has_text="Lambda").locator(".aa-read")).to_have_count(1)
    expect(page.locator(".aa-chip", has_text="S3").locator(".aa-read")).to_have_count(0)
    expect(page.locator(".aa-notread")).to_contain_text("S3")
    page.fill("#search", "orders")
    expect(page.locator(".aa-hit-name").first).to_have_text("orders-etl")
    expect(page.locator("#where")).to_have_text("Region: us-east-1 · read-only")
    expect(page.locator(".aa-ver")).not_to_be_empty()
    assert page.title() == "aws-analyzer"


def test_a_service_read_once_shows_its_names_from_memory_in_another(page, served):
    _open(page, served)
    page.locator(".aa-chip", has_text="S3").click()
    expect(page.locator(".aa-results-head")).to_contain_text("buckets in S3")
    page.locator(".aa-chip", has_text="Lambda").click()
    page.fill("#search", "demo-lake")
    memory = page.locator(".aa-memory")
    expect(memory).to_contain_text("from memory · no new reads")
    expect(memory.locator("a", has_text="demo-lake")).to_be_visible()
    expect(page.locator(".aa-chip", has_text="S3").locator(".aa-read")).to_have_count(1)


def test_pasting_an_address_picks_its_service(page, served):
    _open(page, served)
    expect(page.locator(".aa-results-head")).to_contain_text("in Lambda")
    page.fill("#search", "s3://demo-lake/raw/2026/")
    expect(page.locator(".aa-chip.aa-on")).to_have_text("S3")
    expect(page.locator("#search")).to_have_value("demo-lake")
    expect(page.locator(".aa-hit-name").first).to_have_text("demo-lake")


def test_a_result_opens_in_a_tab_that_comes_back_after_a_reload(page, served):
    _open(page, served)
    page.fill("#search", "orders-etl")
    page.locator(".aa-hit-name", has_text="orders-etl").click()
    _wait_for_report(page)
    assert _hash(page) == "#/lambda_functions/function_info?name=orders-etl&run=1"
    tab = page.locator(".aa-tab.aa-on")
    expect(tab).to_contain_text("orders-etl")
    expect(page.locator(".aa-tab")).to_have_count(2)
    page.reload()
    expect(page.locator(".aa-tab")).to_have_count(2)
    page.locator(".aa-tab-close").click()
    expect(page.locator(".aa-tab")).to_have_count(1)
    expect(page.locator(".aa-home-title")).to_be_visible()


def test_enter_opens_the_picked_result_and_new_tab_keeps_the_one_open(page, served):
    _open(page, served)
    page.fill("#search", "report")
    page.keyboard.press("Enter")
    expect(page.locator(".aa-tab.aa-on")).to_contain_text("report-api")
    page.locator(".aa-tab", has_text="Home").click()
    page.fill("#search", "orders-etl")
    page.locator(".aa-hit").first.hover()
    page.locator(".aa-hit-tab").first.click()
    expect(page.locator(".aa-tab")).to_have_count(3)
    expect(page.locator(".aa-tab.aa-on")).to_contain_text("orders-etl")


def test_the_theme_switch_is_kept(page, served):
    _open(page, served)
    page.locator(".aa-seg button", has_text="Dark").click()
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    assert page.evaluate("document.body.dataset.jpThemeLight") == "false"
    page.reload()
    page.locator(".aa-home").wait_for()
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    page.locator(".aa-seg button", has_text="Light").click()
    assert page.evaluate("document.documentElement.dataset.theme") == "light"


def test_ctrl_k_finds_names_already_read(page, served):
    _open(page, served)
    expect(page.locator(".aa-results-head")).to_contain_text("in Lambda")
    page.keyboard.press("Control+k")
    page.keyboard.type("orders-etl")
    first = page.locator("#palette-list li").first
    expect(first.locator(".aa-t")).to_have_text("orders-etl · function · Lambda")
    page.keyboard.press("Enter")
    assert _hash(page) == "#/lambda_functions/function_info?name=orders-etl&run=1"


def test_a_service_lists_its_commands_by_group_beside_and_on_its_page(page, served):
    _open(page, served)
    page.locator(".aa-browse a", has_text="S3").click()
    expect(page.locator(".aa-head h1")).to_have_text("S3")
    expect(page.locator(".aa-svc.aa-on")).to_have_text("S3")
    expect(page.locator(".aa-cmds .aa-cmd", has_text=re.compile(r"^ls$"))).to_be_visible()
    groups = page.locator(".aa-group").all_inner_texts()
    assert groups and groups == page.locator(".aa-cmds h4").all_inner_texts()
    assert page.title() == "S3 · aws-analyzer"


def test_the_form_writes_the_call_as_in_a_notebook(page, served):
    _open(page, served, "#/s3/ls")
    call = page.locator(".aa-callbox input")
    expect(call).to_have_value("ls()")
    page.fill("#f-uri", BUCKET_FOLDER)
    page.check("input[name=details]")
    expect(call).to_have_value(f"ls('{BUCKET_FOLDER}', details=True)")
    page.uncheck("input[name=details]")
    page.fill("#f-limit", "1,000")
    expect(call).to_have_value(f"ls('{BUCKET_FOLDER}', limit=1000)")


def test_a_list_typed_in_a_box_keeps_its_commas_in_the_call(page, served):
    _open(page, served, "#/lambda_functions/functions")
    page.fill("#f-regions", "['us-east-1', 'eu-west-1']")
    expect(page.locator(".aa-callbox input")).to_have_value("functions(regions=['us-east-1', 'eu-west-1'])")


def test_running_shows_the_report_and_puts_the_run_in_the_address(page, served):
    _open(page, served, "#/s3/ls")
    page.fill("#f-uri", BUCKET_FOLDER)
    page.click(".aa-run")
    _wait_for_report(page)
    assert _hash(page) == f"#/s3/ls?uri={BUCKET_FOLDER}&run=1"
    expect(_status(page).locator("code")).to_have_text(f"ls('{BUCKET_FOLDER}')")
    expect(_status(page)).to_contain_text(re.compile(r" · [\d.]+ s · "))
    expect(page.locator(".aa-report")).to_contain_text("backfill")
    assert page.title() == "ls · S3 · aws-analyzer"


def test_back_and_forward_show_a_report_again_without_running_it(page, served):
    _open(page, served, "#/s3/ls")
    page.fill("#f-uri", BUCKET_FOLDER)
    page.click(".aa-run")
    _wait_for_report(page)
    page.go_back()
    expect(page.locator(".aa-report")).to_have_count(0)
    expect(page.locator("#f-uri")).to_have_value("")
    page.go_forward()
    expect(_status(page)).to_contain_text("shown from earlier")
    expect(page.locator("#f-uri")).to_have_value(BUCKET_FOLDER)


def test_an_address_with_run_runs_its_command_when_opened(page, served):
    _open(page, served, "#/lambda_functions/errors?name=orders-etl&run=1")
    _wait_for_report(page)
    expect(_status(page).locator("code")).to_have_text("errors('orders-etl')")
    expect(page.locator("#f-name")).to_have_value("orders-etl")


def test_run_again_runs_it_afresh(page, served):
    _open(page, served, f"#/s3/ls?uri={BUCKET_FOLDER}&run=1")
    _wait_for_report(page)
    page.goto(f"{served}/?token={TOKEN}#/s3")
    page.go_back()
    expect(_status(page)).to_contain_text("shown from earlier")
    page.locator(".aa-status button", has_text="Run again").click()
    expect(_status(page)).to_contain_text(re.compile(r" · [\d.]+ s · "))


def test_a_next_step_runs_with_a_click_and_moves_to_its_command(page, served):
    _open(page, served, f"#/s3/ls?uri={BUCKET_FOLDER}&run=1")
    _wait_for_report(page)
    step = page.locator(".aa-step-run").first
    expect(step).to_have_attribute("title", f"Run summary('{BUCKET_FOLDER}') here")
    step.click()
    expect(page.locator(".aa-head h1 code")).to_contain_text("summary(")
    _wait_for_report(page)
    assert _hash(page) == f"#/s3/summary?uri={BUCKET_FOLDER}&run=1"
    expect(_status(page)).not_to_contain_text("shown from earlier")


def test_an_edited_call_runs_as_written_and_fills_the_form(page, served):
    _open(page, served, "#/s3/ls")
    page.fill(".aa-callbox input", "ls('s3://demo-lake/')")
    page.click(".aa-run")
    _wait_for_report(page)
    assert _hash(page) == "#/s3/ls?uri=s3://demo-lake/&run=1"
    expect(page.locator("#f-uri")).to_have_value("s3://demo-lake/")


def test_a_call_the_server_refuses_says_why_in_red(page, served):
    _open(page, served, "#/s3/ls")
    page.fill(".aa-callbox input", "clean_downloads()")
    page.click(".aa-run")
    expect(page.locator(".aa-status.aa-bad")).to_contain_text("Couldn't run clean_downloads(): ")
    expect(page.locator(".aa-report")).to_have_count(0)


def test_ctrl_k_finds_a_command_and_enter_opens_it(page, served):
    _open(page, served, "#/s3")
    page.keyboard.press("Control+k")
    page.keyboard.type("lambda errors")
    first = page.locator("#palette-list li").first
    expect(first).to_have_class("aa-on")
    expect(first.locator(".aa-t")).to_have_text("errors · Lambda")
    page.keyboard.press("Enter")
    expect(page.locator("#palette")).to_be_hidden()
    assert _hash(page) == "#/lambda_functions/errors"


def test_the_finder_moves_with_the_arrows_and_closes_on_escape_or_a_click_outside(page, served):
    _open(page, served)
    page.click("#find")
    page.keyboard.type("largest")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowUp")
    expect(page.locator("#palette-list li.aa-on")).to_contain_text("largest")
    page.keyboard.press("Escape")
    expect(page.locator("#palette")).to_be_hidden()
    page.keyboard.press("Control+k")
    expect(page.locator("#palette-input")).to_have_value("")
    page.mouse.click(5, 880)
    expect(page.locator("#palette")).to_be_hidden()


def test_typing_straight_after_ctrl_k_keeps_every_letter(page, served):
    _open(page, served)
    for _ in range(10):
        page.keyboard.press("Control+k")
        page.keyboard.type("errors")
        expect(page.locator("#palette-input")).to_have_value("errors")
        page.keyboard.press("Escape")


def test_in_a_dark_browser_the_reports_are_told_the_page_is_dark(chromium, troubles, served):
    page = _page_in(chromium, troubles, scheme="dark")
    _open(page, served)
    assert page.evaluate("document.body.dataset.jpThemeLight") == "false"
    page.close()
    assert troubles.errors == []


def test_an_address_the_page_doesnt_know_opens_the_home_page(page, served):
    _open(page, served, "#/nothing/here")
    expect(page.locator(".aa-home-title")).to_have_text("Find anything in your account")


HELD_TOTAL = 1000
HELD_STEP = 10
HELD_TICK_SECONDS = 0.05


@pytest.fixture
def held_summary(served) -> Iterator[threading.Event]:
    """
    S3's summary held back: it ticks its progress (of HELD_TOTAL objects) until the test sets the Event it gets, or
    the page's Stop stops it, then shows a one-line report.
    """
    view = SERVED_VIEWS["s3"]
    go = threading.Event()

    def summary(uri: str, **options: object) -> None:
        with view._progress("Scanning", "objects") as tick:
            count = 0
            while not go.is_set():
                count = min(count + HELD_STEP, HELD_TOTAL - HELD_STEP)
                tick(count, HELD_TOTAL)
                time.sleep(HELD_TICK_SECONDS)
        view._show([_Title(f"Held summary of {uri}")])

    view.summary = summary
    yield go
    go.set()
    del view.summary


HELD_PAGE = "#/s3/summary?uri=s3://demo-lake/&run=1"


def test_a_long_command_shows_its_progress_and_stops_when_asked(page, served, held_summary):
    _open(page, served, HELD_PAGE)
    expect(page.locator(".aa-progress-text")).to_contain_text("of 1,000 objects")
    expect(page.locator(".aa-progress-track")).to_have_attribute("aria-valuenow", re.compile(r"\d+"))
    expect(page.locator(".aa-jobs summary")).to_have_text("1 running")
    page.locator(".aa-jobs summary").click()
    expect(page.locator(".aa-job")).to_contain_text("summary('s3://demo-lake/')")
    page.locator(".aa-jobs summary").click()
    page.locator(".aa-status .aa-stop").click()
    expect(_status(page)).to_contain_text("stopped after")
    expect(page.locator(".aa-report")).to_contain_text("Stopped before it finished")
    expect(page.locator(".aa-jobs")).to_have_count(0)


def test_a_job_goes_on_while_you_look_elsewhere_and_after_a_reload(page, served, held_summary):
    _open(page, served, HELD_PAGE)
    expect(page.locator(".aa-progress")).to_be_visible()
    page.evaluate("location.hash = '#/lambda_functions'")
    expect(page.locator(".aa-jobs summary")).to_have_text("1 running")
    page.reload()
    expect(page.locator(".aa-jobs summary")).to_have_text("1 running")
    page.locator(".aa-jobs summary").click()
    page.locator(".aa-job-call").click()
    expect(page.locator(".aa-progress")).to_be_visible()
    held_summary.set()
    _wait_for_report(page)
    expect(page.locator(".aa-report")).to_contain_text("Held summary of s3://demo-lake/")
    expect(page.locator(".aa-jobs")).to_have_count(0)
    jobs = page.evaluate("fetch('api/jobs', {headers: {'X-AWS-Analyzer-Token': '%s'}}).then(r => r.json())" % TOKEN)
    assert len(jobs["jobs"]) >= 1 and all(job["call"] != "" for job in jobs["jobs"])
    assert sum(job["state"] == "running" for job in jobs["jobs"]) == 0


def test_lambdas_page_shows_whats_running_now_pushed_by_the_server(page, served):
    _open(page, served, "#/lambda_functions")
    live = page.locator(".aa-live")
    expect(live.locator("h2")).to_have_text("Running now")
    expect(live.locator(".aa-live-headline")).to_have_text(re.compile(r"ran in the last 10 minutes|Nothing ran"))
    expect(live.locator(".aa-live-foot")).to_contain_text("CloudWatch metrics a read")
    live.locator("button", has_text="Pause").click()
    expect(live.locator("button")).to_have_text("Watch again")
    expect(page.locator(".aa-live-dot.aa-on")).to_have_count(0)


def test_the_result_picked_has_a_quick_look_from_what_the_search_read(page, served):
    _open(page, served)
    page.fill("#search", "orders-etl")
    look = page.locator(".aa-look")
    expect(look.locator(".aa-look-name")).to_have_text("orders-etl")
    expect(look.locator(".aa-facts")).to_contain_text("Handler")
    expect(look.locator(".aa-facts")).to_contain_text("Memory")
    expect(look.locator(".aa-run-link")).to_have_attribute("href", "#/lambda_functions/function_info?name=orders-etl&run=1")
    page.fill("#search", "")
    page.locator("#search").press("ArrowDown")
    second = page.locator(".aa-hit").nth(1).locator(".aa-hit-name").inner_text()
    expect(look.locator(".aa-look-name")).to_have_text(second)


def test_a_service_page_says_what_needs_attention_and_home_remembers_it(page, served):
    _open(page, served, "#/lambda_functions")
    block = page.locator(".aa-attention")
    expect(block.locator("h2")).to_have_text("Needs attention in Lambda")
    expect(block.locator(".aa-verdict")).to_contain_text("need you")
    failing = block.locator(".aa-concern", has_text="Started failing after a change")
    expect(failing.locator(".aa-targets a", has_text="orders-etl")).to_have_attribute(
        "href", "#/lambda_functions/function_info?name=orders-etl&run=1")
    expect(block.locator(".aa-attention-meta")).to_contain_text("From functions()")
    block.locator("a", has_text="See the whole report").click()
    _wait_for_report(page)
    expect(_status(page)).to_contain_text("shown from earlier")

    page.goto(f"{served}/?token={TOKEN}#/")
    checked = page.locator(".aa-checked")
    expect(checked.locator(".aa-checked-line", has_text="Lambda")).to_contain_text("warning")
    expect(checked.locator(".aa-checked-line", has_text="S3")).to_have_count(0)
    page.fill("#search", "orders-etl")
    expect(page.locator(".aa-look-check")).to_contain_text("What functions() found")
    expect(page.locator(".aa-look-check")).to_contain_text("Started failing after a change")
