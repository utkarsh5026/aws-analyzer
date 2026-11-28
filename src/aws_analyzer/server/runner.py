"""
Runs a View's command for the web page and hands back its report as HTML, the same report a notebook cell shows, or,
for a View with reports as data (_DataCommands), the tables the report was drawn from as JSON (ReportData.to_json).

One View per service, made the first time the page asks for it and kept, like a `ui = LambdaView()` in a notebook:
its analyzer's caches and what use() / more() / follow_up() remember carry over from one command to the next. A
service runs one command at a time (its lock), since a View keeps the command running; different services run side
by side, each on the thread FastAPI gives the request.
"""

from __future__ import annotations

import inspect
import logging
import threading
import time
import traceback
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from .._kit.blocks import _Note
from .._kit.dataview import _DataCommands
from .._kit.errors import _error_code, _Hint
from .._kit.fmt import _utcnow
from .._kit.render import _call
from .._kit.themes import _theme_name
from .._kit.view import _commands, _parse_call
from .catalog import BY_KEY, Parameter, Service, argument_value, served_names, view_commands
from .resources import LISTERS, NAMES_KEPT, ResourceIndex, sorted_resources

log = logging.getLogger("aws_analyzer.server")
_GATHERING = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)


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
    parameters was given, as the form's boxes show it, the report's HTML (its own stylesheet first, as in a cell) and
    how long it took.
    """

    service: str
    command: str
    call: str
    arguments: dict[str, str]
    html: str
    seconds: float


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


@dataclass
class _Service:
    view: Any = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    names: _KeptNames | None = None


@dataclass(frozen=True)
class _KeptNames:
    index: ResourceIndex
    seconds_at: float


def _service_named(key: str) -> Service:
    if key not in BY_KEY:
        raise CantRun(f"There's no service called {key!r}.")
    return BY_KEY[key]


def _view_class(key: str) -> type:
    service = _service_named(key)
    return getattr(service.module(), service.view_name)


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
        self._services = {key: _Service(view) for key, view in (views or {}).items()}
        self._making = threading.Lock()

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

    def _view(self, key: str, held: _Service) -> Any:
        if held.view is None:
            held.view = self._make_view(key)
        return held.view

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
        with held.lock:
            kept = held.names
            is_fresh = kept is not None and time.monotonic() - kept.seconds_at < NAMES_KEPT
            if kept is not None and is_fresh and not refresh:
                return replace(kept.index, from_memory=True)
            index = self._read_names(key, held)
            held.names = _KeptNames(index, time.monotonic())
            return index

    def _read_names(self, key: str, held: _Service) -> ResourceIndex:
        title = BY_KEY[key].title
        started = time.monotonic()
        try:
            core = self._view(key, held).core
            found = LISTERS[key](core)
        except ClientError as exc:
            message = exc.response.get("Error", {}).get("Message", "")
            raise CantList(f"Couldn't list {title}: {_error_code(exc)}. {message}".strip()) from exc
        except BotoCoreError as exc:
            held.view = None
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
        held = self._service(key)
        started = time.monotonic()
        with held.lock:
            data = self._tables(key, held, command, args, kwargs)
        texts = _form_texts(_commands(_view_class(key))[command], args, kwargs)
        seconds = round(time.monotonic() - started, 2)
        return DataReport(key, command, _call(command, *args, **kwargs), texts, data.to_json(), seconds)

    def _tables(self, key: str, held: _Service, command: str, args: tuple, kwargs: dict) -> Any:
        """
        What a command read, as its View keeps it for data(): the command runs quietly, so nothing is drawn. A
        report that's only a note (an AWS or input error, which the command's own guard says) is NoData with that
        note's words; anything else that breaks is NoData too, its traceback in the server's log.
        """
        try:
            view = self._view(key, held)
        except BotoCoreError as exc:
            held.view = None
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
        held = self._service(key)
        started = time.monotonic()
        with held.lock:
            blocks = self._blocks(key, held, command, args, kwargs)
        view_class = _view_class(key)
        html = _reports_html(view_class, self.settings.max_rows, blocks)
        texts = _form_texts(_commands(view_class)[command], args, kwargs)
        seconds = round(time.monotonic() - started, 2)
        return Report(key, command, _call(command, *args, **kwargs), texts, html, seconds)

    def _blocks(self, key: str, held: _Service, command: str, args: tuple, kwargs: dict) -> list[list[Any]]:
        """
        The reports a command shows, each as its blocks. The View's own guard (@_friendly_errors) turns AWS and
        input errors into a note; what it doesn't is said as a note too, its traceback in the server's log.
        """
        try:
            view = self._view(key, held)
        except BotoCoreError as exc:
            held.view = None
            reason = str(exc).rstrip(".")
            return [[_Note(f"Couldn't start {BY_KEY[key].title}: {reason}. Check the region (--region) and the AWS "
                           "credentials this server runs with.", "warn")]]
        shown: list[list[Any]] = []
        view._show = _keeper(view, shown)
        try:
            getattr(view, command)(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - the server's guard: the page gets a note, the log the traceback
            log.error("%s.%s failed:\n%s", key, command, traceback.format_exc())
            shown.append([_Note(f"{command}() stopped with {type(exc).__name__}: {exc}. The details are in the "
                                "terminal running aws-analyzer serve.", "warn")])
        finally:
            del view._show
        return shown


def _keeper(view: Any, shown: list[list[Any]]) -> Callable[[list[Any]], None]:
    """
    What the View's _show becomes while a command runs: it keeps each report, ready to draw (_prepared), instead
    of displaying it in a notebook.
    """

    def keep(blocks: list[Any]) -> None:
        shown.append(view._prepared(blocks))

    return keep


def _reports_html(view_class: type, max_rows: int, reports: list[list[Any]]) -> str:
    """
    A command's reports as HTML, each drawn in its service's look and the theme set_theme() chose.
    """
    if not reports:
        reports = [[_Note("This command showed nothing.")]]
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
