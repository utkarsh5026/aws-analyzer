"""
Runs a View's command for the web page and hands back its report as HTML, the same report a notebook cell shows.

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
from dataclasses import dataclass, field
from typing import Any, Callable

from botocore.exceptions import BotoCoreError

from .._kit.blocks import _Note
from .._kit.render import _call
from .._kit.themes import _theme_name
from .._kit.view import _commands, _parse_call
from .catalog import BY_KEY, Parameter, Service, argument_value, served_names, view_commands

log = logging.getLogger("aws_analyzer.server")
_GATHERING = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)


class CantRun(LookupError):
    """
    What the page asked for can't run: a service or command it doesn't offer, a parameter the command hasn't got, or
    one it needs left empty. The message says which, in words for the page.
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
class _Service:
    view: Any = None
    lock: threading.Lock = field(default_factory=threading.Lock)


def _service_named(key: str) -> Service:
    if key not in BY_KEY:
        raise CantRun(f"There's no service called {key!r}.")
    return BY_KEY[key]


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
        service = _service_named(key)
        return view_commands(getattr(service.module(), service.view_name))

    def run(self, key: str, command: str, arguments: dict[str, str] | None = None) -> Report:
        """
        Runs a command with what the form's boxes hold ({'name': 'orders-etl', 'since': '7d'}).
        """
        offered = {each.name: each for each in self.commands(key)}
        if command not in offered:
            raise CantRun(f"The page can't run {command}() for {BY_KEY[key].title}.")
        args, kwargs = _form_arguments(offered[command].parameters, arguments or {})
        return self._run(key, command, args, kwargs)

    def run_call(self, key: str, call: str) -> Report:
        """
        Runs a call written as in a notebook, "errors('orders-etl', since='7d')" or "ui.errors(...)" (a report's
        next step). Its arguments are read as Python literals and nothing in it is run (_parse_call).
        """
        parsed = _parse_call(_without_receiver(call.strip()))
        if parsed is None:
            raise CantRun(f"{call!r} isn't one command with plain values, like errors('orders-etl', "
                                 "since='7d').")
        service = _service_named(key)
        if parsed.name not in served_names(getattr(service.module(), service.view_name)):
            raise CantRun(f"The page can't run {parsed.name}() for {service.title}.")
        return self._run(key, parsed.name, tuple(parsed.args), parsed.kwargs)

    def _run(self, key: str, command: str, args: tuple, kwargs: dict[str, Any]) -> Report:
        held = self._service(key)
        started = time.monotonic()
        with held.lock:
            blocks = self._blocks(key, held, command, args, kwargs)
        service = BY_KEY[key]
        view_class = getattr(service.module(), service.view_name)
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
