"""
Runs a View's command for the web page and hands back its report as HTML, the same report a notebook cell shows, or,
for a View with reports as data (_DataCommands), the tables the report was drawn from as JSON (ReportData.to_json).

One View per service, made the first time the page asks for it and kept, like a `ui = LambdaView()` in a notebook:
its analyzer's caches and what use() / more() / follow_up() remember carry over from one command to the next. A View
runs one command at a time (it keeps the command running), so a second command for a service while the first runs
gets a View of its own on the same analyzer, up to VIEWS_PER_SERVICE of them (the explorers' workers do the same);
the first View is the one a command gets whenever it's free. Different services run side by side.

A command runs as a request (run, run_call: the page waits for its report) or as a job (start_job, start_call_job:
jobs.py), which sends its progress and each report as it comes, and stops on the page's Stop.
"""

from __future__ import annotations

import inspect
import logging
import sys
import threading
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from .._kit.blockjson import _look_css, _report_json
from .._kit.blocks import _Note
from .._kit.dataview import _DataCommands
from .._kit.errors import _error_code, _Hint
from .._kit.fmt import _utcnow
from .._kit.render import _call, _Look
from .._kit.themes import _theme_name
from .._kit.view import _commands, _parse_call
from .attention import ATTENTION_KEPT, Attention, cards_of, check_for, concerns_of, note_of, opened, verdict_of
from .catalog import BY_KEY, Parameter, Service, argument_value, served_names, view_commands
from .jobs import DONE, FAILED, STOPPED, Job, JobBoard, JobStopped, job_progress
from .resources import LISTERS, NAMES_KEPT, ResourceIndex, sorted_resources

log = logging.getLogger("aws_analyzer.server")
_GATHERING = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
VIEWS_PER_SERVICE = 4
_NOTHING_SHOWN = "This command showed nothing."
_STOPPED = ("Stopped before it finished, so there's no report for it. Run it again to read everything, or ask for "
            "less (a narrower prefix, a shorter since=, limit=).")


class CantRun(LookupError):
    """
    What the page asked for can't run: a service or command it doesn't offer, a parameter the command hasn't got, or
    one it needs left empty. The message says which, in words for the page.
    """


class NoData(LookupError):
    """
    A command ran but gave no tables: its report was a note (a missing bucket, a permission it hasn't got), or it
    isn't one of the reports that give their data. The message says why, in words for the page.
    """


class CantList(LookupError):
    """
    A service's resources couldn't be listed: the page can't search it yet, the credentials can't list it, or the
    region is wrong. The message says which, in words for the page.
    """


@dataclass
class Settings:
    """
    What every View the server makes starts with: the AWS region and profile (None: boto3's own, the notebook
    role on SageMaker), and the most rows a table shows before it says how many more there are.
    """

    region: str | None = None
    profile: str | None = None
    max_rows: int = 100


@dataclass
class Report:
    """
    A command's report as the page shows it: the call it ran (as you'd write it in a notebook), what each of its
    parameters was given, as the form's boxes show it, the report's HTML (its own stylesheet first, as in a cell),
    each report it showed as blocks of JSON (_report_json: the page's components draw them; the stylesheet is
    GET /api/style/<service>'s) and how long it took.
    """

    service: str
    command: str
    call: str
    arguments: dict[str, str]
    html: str
    seconds: float
    reports: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DataReport:
    """
    A command's report as data: the call it ran, what each of its parameters was given (as the form's boxes show
    it), the report's tables, charts and summary as JSON (ReportData.to_json) and how long it took.
    """

    service: str
    command: str
    call: str
    arguments: dict[str, str]
    data: dict[str, Any]
    seconds: float


@dataclass(frozen=True)
class _CommandRun:
    """
    A command the page asked a service for, with the arguments it's called with.
    """

    service: str
    command: str
    args: tuple
    kwargs: dict[str, Any]

    @property
    def call(self) -> str:
        return _call(self.command, *self.args, **self.kwargs)

    def texts(self) -> dict[str, str]:
        return _form_texts(_commands(_view_class(self.service))[self.command], self.args, self.kwargs)


@dataclass
class _Lane:
    """
    One of a service's Views and the lock it runs one command at a time under.
    """

    view: Any = None
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class _Service:
    """
    A service's Views (the first is its main one: the one given, or made first), the lock they're made under, and
    what the runner keeps for it under names_lock: the names its search read last and its last attention check.
    """

    lanes: list[_Lane] = field(default_factory=lambda: [_Lane()])
    making: threading.Lock = field(default_factory=threading.Lock)
    names_lock: threading.Lock = field(default_factory=threading.Lock)
    names: _KeptNames | None = None
    attention: _KeptAttention | None = None


@dataclass(frozen=True)
class _KeptNames:
    index: ResourceIndex
    seconds_at: float


@dataclass(frozen=True)
class _KeptAttention:
    attention: Attention
    report: Report
    seconds_at: float


@dataclass(frozen=True)
class Checked:
    """
    One check of a service, as the page gets it: what needs attention, and the report it was read from (so "See
    the whole report" shows it without running it again).
    """

    attention: Attention
    report: Report


def _service_named(key: str) -> Service:
    if key not in BY_KEY:
        raise CantRun(f"There's no service called {key!r}.")
    return BY_KEY[key]


def _view_class(key: str) -> type:
    service = _service_named(key)
    return getattr(service.module(), service.view_name)


def _look_of(view_class: type) -> _Look:
    """
    A service's look (its stylesheet, root class and badge): the _LOOK beside the _render_html its View draws with.
    """
    return sys.modules[view_class._render_html.__module__]._LOOK


def style_of(key: str) -> str:
    """
    A service's stylesheet and the theme's rules as CSS, what its reports' <style> elements hold: the page puts it in
    once, for the reports it draws from their blocks.
    """
    return _look_css(_look_of(_view_class(key)), _theme_name())


def gives_data(key: str) -> bool:
    """
    Whether a service's reports come as data too: its View has data() and to_excel() (_DataCommands).
    """
    return issubclass(_view_class(key), _DataCommands)


class ReportRunner:
    """
    The Views the server runs commands on, one per service. views= gives some ready-made ({'s3': S3View(...)}),
    which tests and demos use for Views on fake data; the rest are made from settings when first asked for.
    """

    def __init__(self, settings: Settings | None = None, *, views: dict[str, Any] | None = None):
        self.settings = settings or Settings()
        self._services = {key: _Service([_Lane(view)]) for key, view in (views or {}).items()}
        self._making = threading.Lock()
        self.jobs = JobBoard()

    def _service(self, key: str) -> _Service:
        _service_named(key)
        with self._making:
            return self._services.setdefault(key, _Service())

    def _make_view(self, key: str) -> Any:
        """
        The service's View on a new analyzer, in HTML mode with no progress line (the page shows its own).
        """
        service = BY_KEY[key]
        module = service.module()
        options = {"region": self.settings.region, "profile": self.settings.profile}
        core = getattr(module, service.analyzer_name)(**{k: v for k, v in options.items() if v})
        view_class = getattr(module, service.view_name)
        return view_class(core, mode="html", max_rows=self.settings.max_rows, progress="off")

    def _view_beside(self, main: Any) -> Any:
        """
        Another View of the main one's class on the same analyzer, so its caches are shared: a second command runs
        on it while the main one is busy.
        """
        return type(main)(main.core, mode="html", max_rows=self.settings.max_rows, progress="off")

    def _lane_view(self, key: str, held: _Service, lane: _Lane) -> Any:
        """
        The lane's View, made the first time it's needed: the main one from settings, another beside it.
        """
        with held.making:
            if lane.view is None:
                main = held.lanes[0].view
                is_main = lane is held.lanes[0] or main is None
                lane.view = self._make_view(key) if is_main else self._view_beside(main)
            return lane.view

    def _free_lane(self, held: _Service) -> _Lane:
        """
        A lane whose lock this took: the first free one (the main one first), a new one when all are busy and there
        are fewer than VIEWS_PER_SERVICE, else the main one once it's free.
        """
        with held.making:
            for lane in held.lanes:
                if lane.lock.acquire(blocking=False):
                    return lane
            if len(held.lanes) < VIEWS_PER_SERVICE:
                lane = _Lane()
                lane.lock.acquire()
                held.lanes.append(lane)
                return lane
        main = held.lanes[0]
        main.lock.acquire()
        return main

    @contextmanager
    def _borrowed(self, key: str) -> Iterator[_Lane]:
        """
        One of the service's Views, all to itself while the block runs (its view made by _lane_view).
        """
        held = self._service(key)
        lane = self._free_lane(held)
        try:
            yield lane
        finally:
            lane.lock.release()

    def core(self, key: str) -> Any:
        """
        The service's analyzer (its main View's), for reads that don't go through a command: the live streams.
        """
        held = self._service(key)
        return self._lane_view(key, held, held.lanes[0]).core

    def commands(self, key: str) -> list:
        """
        The commands the page offers for a service, from its View class (no View is made, nothing is read).
        """
        return view_commands(_view_class(key))

    def run(self, key: str, command: str, arguments: dict[str, str] | None = None) -> Report:
        """
        Runs a command with what the form's boxes hold ({'name': 'orders-etl', 'since': '7d'}).
        """
        args, kwargs = self._form_call(key, command, arguments)
        return self._run(key, command, args, kwargs)

    def run_call(self, key: str, call: str) -> Report:
        """
        Runs a call written as in a notebook, "errors('orders-etl', since='7d')" or "ui.errors(...)" (a report's
        next step). Its arguments are read as Python literals and nothing in it is run (_parse_call).
        """
        return self._run(key, *self._parsed_call(key, call))

    def data(self, key: str, command: str, arguments: dict[str, str] | None = None) -> DataReport:
        """
        A report's tables as JSON, for the form's boxes ({'name': 'orders-etl', 'since': '7d'}): the command runs
        without drawing its report, as ui.data('errors', 'orders-etl', since='7d') does in a notebook.
        """
        self._check_gives_data(key)
        args, kwargs = self._form_call(key, command, arguments)
        return self._read_data(key, command, args, kwargs)

    def data_call(self, key: str, call: str) -> DataReport:
        """
        A report's tables as JSON, for a call written as in a notebook ("errors('orders-etl', since='7d')").
        """
        self._check_gives_data(key)
        return self._read_data(key, *self._parsed_call(key, call))

    def resources(self, key: str, *, refresh: bool = False) -> ResourceIndex:
        """
        A service's resources, for the page's search: what its listing call returns, kept for NAMES_KEPT seconds (a
        second search, from any tab, reads nothing), read afresh with refresh=True. Only the service asked for is
        read.
        """
        service = _service_named(key)
        if key not in LISTERS:
            raise CantList(f"Searching {service.title} isn't ready yet: open its commands instead.")
        held = self._service(key)
        with held.names_lock:
            kept = held.names
            is_fresh = kept is not None and time.monotonic() - kept.seconds_at < NAMES_KEPT
            if kept is not None and is_fresh and not refresh:
                return replace(kept.index, from_memory=True)
            index = self._read_names(key, held)
            held.names = _KeptNames(index, time.monotonic())
            return index

    def attention(self, key: str, *, refresh: bool = False) -> Checked:
        """
        What needs attention in one service: its overview run as a notebook runs it, its findings read out of the
        report (attention.py), kept for ATTENTION_KEPT seconds (Home and the quick look reuse it), run afresh with
        refresh=True. Only the service asked for is read.
        """
        service = _service_named(key)
        check = check_for(key)
        if check is None:
            raise CantList(f"{service.title} has no overview to check yet: open its commands instead.")
        held = self._service(key)
        with held.names_lock:
            kept = held.attention
            is_fresh = kept is not None and time.monotonic() - kept.seconds_at < ATTENTION_KEPT
            if kept is not None and is_fresh and not refresh:
                return Checked(replace(kept.attention, from_memory=True), kept.report)
        report, blocks = self._run_blocks(key, check.command, (), dict(check.kwargs))
        with held.names_lock:
            names = held.names.index.resources if held.names else []
        by_name = {resource.name: resource for resource in names}
        concerns = opened(concerns_of(blocks, list(by_name)), check, by_name)
        verdict, tone = verdict_of(blocks)
        found = Attention(key, report.call, check.command, verdict, tone, cards_of(blocks), concerns, note_of(blocks),
                          _utcnow(), report.seconds)
        with held.names_lock:
            held.attention = _KeptAttention(found, report, time.monotonic())
        return Checked(found, report)

    def checked(self) -> list[Attention]:
        """
        The checks the server still keeps, one per service checked, for Home's "Checked so far": nothing is read.
        """
        with self._making:
            held = list(self._services.values())
        now = time.monotonic()
        return [replace(each.attention.attention, from_memory=True) for each in held
                if each.attention is not None and now - each.attention.seconds_at < ATTENTION_KEPT]

    def _read_names(self, key: str, held: _Service) -> ResourceIndex:
        title = BY_KEY[key].title
        started = time.monotonic()
        try:
            core = self.core(key)
            found = LISTERS[key](core)
        except ClientError as exc:
            message = exc.response.get("Error", {}).get("Message", "")
            raise CantList(f"Couldn't list {title}: {_error_code(exc)}. {message}".strip()) from exc
        except BotoCoreError as exc:
            self._forget_views(held)
            raise CantList(f"Couldn't list {title}: {str(exc).rstrip('.')}. Check the region (--region) and the AWS "
                           "credentials this server runs with.") from exc
        seconds = round(time.monotonic() - started, 2)
        return ResourceIndex(key, sorted_resources(found), _utcnow(), seconds)

    def _form_call(self, key: str, command: str, arguments: dict[str, str] | None) -> tuple[tuple, dict[str, Any]]:
        offered = {each.name: each for each in self.commands(key)}
        if command not in offered:
            raise CantRun(f"The page can't run {command}() for {BY_KEY[key].title}.")
        return _form_arguments(offered[command].parameters, arguments or {})

    def _parsed_call(self, key: str, call: str) -> tuple[str, tuple, dict[str, Any]]:
        parsed = _parse_call(_without_receiver(call.strip()))
        if parsed is None:
            raise CantRun(f"{call!r} isn't one command with plain values, like errors('orders-etl', "
                                 "since='7d').")
        service = _service_named(key)
        if parsed.name not in served_names(_view_class(key)):
            raise CantRun(f"The page can't run {parsed.name}() for {service.title}.")
        return parsed.name, tuple(parsed.args), parsed.kwargs

    def _check_gives_data(self, key: str) -> None:
        if not gives_data(key):
            raise NoData(f"{_service_named(key).title}'s reports don't come as data yet: run them for their "
                         "report instead.")

    def _read_data(self, key: str, command: str, args: tuple, kwargs: dict[str, Any]) -> DataReport:
        started = time.monotonic()
        with self._borrowed(key) as lane:
            data = self._tables(key, lane, command, args, kwargs)
        texts = _form_texts(_commands(_view_class(key))[command], args, kwargs)
        seconds = round(time.monotonic() - started, 2)
        return DataReport(key, command, _call(command, *args, **kwargs), texts, data.to_json(), seconds)

    def _tables(self, key: str, lane: _Lane, command: str, args: tuple, kwargs: dict) -> Any:
        """
        What a command read, as its View keeps it for data(): the command runs quietly, so nothing is drawn. A
        report that's only a note (an AWS or input error, which the command's own guard says) is NoData with that
        note's words; anything else that breaks is NoData too, its traceback in the server's log.
        """
        held = self._service(key)
        try:
            view = self._lane_view(key, held, lane)
        except BotoCoreError as exc:
            self._forget_views(held)
            raise NoData(f"Couldn't start {BY_KEY[key].title}: {str(exc).rstrip('.')}. Check the region (--region) "
                         "and the AWS credentials this server runs with.") from exc
        try:
            return view._read_quietly(command, args, kwargs)
        except _Hint as hint:
            raise NoData(str(hint)) from hint
        except Exception as exc:  # noqa: BLE001 - the server's guard: the page gets the words, the log the traceback
            log.error("%s.%s failed:\n%s", key, command, traceback.format_exc())
            raise NoData(f"{command}() stopped with {type(exc).__name__}: {exc}. The details are in the terminal "
                         "running aws-analyzer serve.") from exc

    def _run(self, key: str, command: str, args: tuple, kwargs: dict[str, Any]) -> Report:
        return self._run_blocks(key, command, args, kwargs)[0]

    def _run_blocks(self, key: str, command: str, args: tuple, kwargs: dict[str, Any]) -> tuple[Report, list]:
        """
        A command's report, and the blocks it was drawn from.
        """
        asked = _CommandRun(key, command, args, kwargs)
        started = time.monotonic()
        with self._borrowed(key) as lane:
            blocks = self._blocks(asked, lane)
        return self._report(asked, blocks, round(time.monotonic() - started, 2)), blocks

    def _report(self, asked: _CommandRun, blocks: list[list[Any]], seconds: float) -> Report:
        view_class = _view_class(asked.service)
        html = _reports_html(view_class, self.settings.max_rows, blocks)
        drawn = [self._report_blocks(view_class, each) for each in blocks or [[_Note(_NOTHING_SHOWN)]]]
        return Report(asked.service, asked.command, asked.call, asked.texts(), html, seconds, drawn)

    def _report_blocks(self, view_class: type, blocks: list[Any]) -> dict[str, Any]:
        return _report_json(blocks, self.settings.max_rows, _look_of(view_class), theme=_theme_name())

    def _forget_views(self, held: _Service) -> None:
        """
        Drops the service's Views after its analyzer couldn't start (no region, no credentials), so the next command
        tries again.
        """
        with held.making:
            for lane in held.lanes:
                lane.view = None

    def _blocks(self, asked: _CommandRun, lane: _Lane, job: Job | None = None) -> list[list[Any]]:
        """
        The reports a command shows, each as its blocks. The View's own guard (@_friendly_errors) turns AWS and
        input errors into a note; what it doesn't is said as a note too, its traceback in the server's log. For a job,
        each report is sent as it's shown, and its progress as it comes (job_progress); its Stop (JobStopped) goes
        on up to the job.
        """
        key, command = asked.service, asked.command
        held = self._service(key)
        try:
            view = self._lane_view(key, held, lane)
        except BotoCoreError as exc:
            self._forget_views(held)
            reason = str(exc).rstrip(".")
            return [[_Note(f"Couldn't start {BY_KEY[key].title}: {reason}. Check the region (--region) and the AWS "
                           "credentials this server runs with.", "warn")]]
        shown: list[list[Any]] = []
        view._show = _keeper(view, shown, self._sender(key, job))
        if job is not None:
            view._progress = job_progress(job, view._PROGRESS)
        try:
            getattr(view, command)(*asked.args, **asked.kwargs)
        except Exception as exc:  # noqa: BLE001 - the server's guard: the page gets a note, the log the traceback
            log.error("%s.%s failed:\n%s", key, command, traceback.format_exc())
            note = [_Note(f"{command}() stopped with {type(exc).__name__}: {exc}. The details are in the "
                          "terminal running aws-analyzer serve.", "warn")]
            shown.append(note)
            self._sender(key, job)(note)
        finally:
            del view._show
            view.__dict__.pop("_progress", None)
        return shown

    def _sender(self, key: str, job: Job | None) -> Callable[[list[Any]], None]:
        """
        What sends each report a job shows to the page as it's shown: its HTML and its blocks. Nothing outside a job.
        """
        if job is None:
            return lambda blocks: None
        view_class = _view_class(key)

        def send(blocks: list[Any]) -> None:
            html = view_class._render_html(blocks, self.settings.max_rows, theme=_theme_name())
            job.add("report", {"html": html, "report": self._report_blocks(view_class, blocks)})

        return send

    def start_job(self, key: str, command: str, arguments: dict[str, str] | None = None) -> Job:
        """
        Starts a command with what the form's boxes hold as a job (jobs.py), and returns it at once: CantRun first,
        before anything starts, when it can't run.
        """
        args, kwargs = self._form_call(key, command, arguments)
        return self._start(key, command, args, kwargs)

    def start_call_job(self, key: str, call: str) -> Job:
        """
        Starts a call written as in a notebook ("errors('orders-etl', since='7d')") as a job.
        """
        return self._start(key, *self._parsed_call(key, call))

    def _start(self, key: str, command: str, args: tuple, kwargs: dict[str, Any]) -> Job:
        asked = _CommandRun(key, command, args, kwargs)
        job = Job(key, command, asked.call, asked.texts())
        return self.jobs.start(job, lambda started: self._run_job(started, asked))

    def _run_job(self, job: Job, asked: _CommandRun) -> None:
        """
        A job's work, on its worker thread: the command on a free View, each report sent as it's shown; a note when it
        showed nothing; stopped when Stop reached a progress tick (or the command ended after it was pressed).
        """
        state = DONE
        try:
            with self._borrowed(job.service) as lane:
                shown = self._blocks(asked, lane, job)
            if not shown:
                self._sender(job.service, job)([_Note(_NOTHING_SHOWN)])
            if job.stop.is_set():
                state = STOPPED
        except JobStopped:
            state = STOPPED
            self._sender(job.service, job)([_Note(_STOPPED, "warn")])
        except Exception:  # noqa: BLE001 - the server's guard: the job ends, the log has the traceback
            log.error("job %s (%s) failed:\n%s", job.id, job.call, traceback.format_exc())
            state = FAILED
        finally:
            job.finish(state)


def _keeper(view: Any, shown: list[list[Any]], send: Callable[[list[Any]], None]) -> Callable[[list[Any]], None]:
    """
    What the View's _show becomes while a command runs: it keeps each report, ready to draw (_prepared), instead
    of displaying it in a notebook, and sends it on (a job's report event).
    """

    def keep(blocks: list[Any]) -> None:
        prepared = view._prepared(blocks)
        shown.append(prepared)
        send(prepared)

    return keep


def _reports_html(view_class: type, max_rows: int, reports: list[list[Any]]) -> str:
    """
    A command's reports as HTML, each drawn in its service's look and the theme set_theme() chose.
    """
    if not reports:
        reports = [[_Note(_NOTHING_SHOWN)]]
    return "".join(view_class._render_html(blocks, max_rows, theme=_theme_name()) for blocks in reports)


def _box_text(value: Any) -> str:
    return value if isinstance(value, str) else repr(value)


def _form_texts(function: Callable, args: tuple, kwargs: dict[str, Any]) -> dict[str, str]:
    """
    What each named parameter was given, written as its form box shows it (a string as it is, anything else as
    Python), so a next step run with a click fills the form it opens.
    """
    signature = inspect.signature(function)
    try:
        bound = signature.bind_partial(None, *args, **kwargs)
    except TypeError:
        return {}
    after_self = list(signature.parameters.values())[1:]
    fillable = {param.name for param in after_self if param.kind not in _GATHERING}
    return {name: _box_text(value) for name, value in bound.arguments.items() if name in fillable}


def _without_receiver(call: str) -> str:
    """
    "ui.errors('etl')" -> "errors('etl')": a report's next steps name the View they run on.
    """
    name, bracket, rest = call.partition("(")
    receiver, dot, command = name.rpartition(".")
    return f"{command}{bracket}{rest}" if dot and receiver.isidentifier() else call


def _form_arguments(parameters: tuple[Parameter, ...], arguments: dict[str, str]) -> tuple[tuple, dict[str, Any]]:
    """
    The form's boxes as the call's arguments: the leading required ones in order, as you'd write them, the rest by
    name, and an empty box left out so the command's default holds (or, for a required one, so the note says
    it's missing).
    """
    unknown = set(arguments) - {parameter.name for parameter in parameters}
    if unknown:
        raise CantRun(f"No parameter called {', '.join(sorted(unknown))}.")
    given = {name for name, text in arguments.items() if text.strip()}
    missing = [parameter.name for parameter in parameters if parameter.required and parameter.name not in given]
    if missing:
        raise CantRun(f"Fill in {' and '.join(missing)} first: the command can't run without it.")
    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    for parameter in parameters:
        text = arguments.get(parameter.name, "")
        if not text.strip():
            continue
        value = argument_value(text, parameter)
        goes_in_order = parameter.required and parameter.positional and len(args) == parameters.index(parameter)
        if goes_in_order:
            args.append(value)
        else:
            kwargs[parameter.name] = value
    return tuple(args), kwargs
