"""
The chat window's model settings (⚙ model in its head): which provider and model the agent answers with, picked in
the window instead of in code. The provider comes from a list (the ones reports name by title, then every other
provider LangChain's init_chat_model knows), the model from a box that suggests names (the region's Bedrock models,
read when Bedrock is picked; the provider's default and an example otherwise), and the panel asks only for what that
provider takes: a base URL or Azure endpoint, an API key (a password box, never shown back) and Azure's API version.

Under them it says where what the agent sends would go and how its tokens are priced, and Use this model calls
AgentAnalyzer.use_model(), which makes the model at once: a missing key, setting or package is said in the panel,
and the agent keeps the model it had. A switch starts a new conversation.

_FORMS holds what each provider asks for (_Form); a provider it doesn't name asks for a model, a base URL and a key.
_PANEL_CSS is the panel's look, which the window adds to its stylesheet.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from botocore.exceptions import BotoCoreError, ClientError

from ..._bedrock.models import ModelInfo
from ..._bedrock.prices import _MODEL_ALIASES, _per_million, short_model
from ..._kit.errors import _error_name
from ..._kit.render import _prose
from ..._kit.text import _esc
from ..._kit.window import _class_if, _running_loop
from ..llm import AZURE_API_VERSION, model_name, model_place, model_rates, provider_info, provider_names
from ..view import _ROOT

if TYPE_CHECKING:
    from ..analyzer import AgentAnalyzer
    from .chat import _AgentChat

_GIVEN_MODEL = ""
_LABEL_WIDTH = "104px"


@dataclass(frozen=True)
class _Form:
    """
    What a provider takes, as the panel asks for it.

        model     the model box's label ('deployment' for Azure OpenAI; '' asks for none)
        endpoint  the endpoint box's label ('' when the provider takes none)
        key       whether it takes an API key
        version   whether it takes an API version (Azure OpenAI)
    """

    model: str = "model"
    endpoint: str = "base URL"
    key: bool = True
    version: bool = False


_FORMS = {
    "bedrock": _Form(endpoint="", key=False),
    "azure": _Form(model="deployment", endpoint="endpoint", version=True),
    "ollama": _Form(key=False),
    _GIVEN_MODEL: _Form(model="", endpoint="", key=False),
}
_ENDPOINT_HINTS = {
    "azure": ("AZURE_OPENAI_ENDPOINT", "https://<resource>.openai.azure.com"),
    "openai": ("OPENAI_BASE_URL", "optional: a LiteLLM, vLLM or OpenRouter server"),
    "ollama": ("", "optional: http://localhost:11434"),
}
_OTHER_ENDPOINT = "optional: the provider's base URL"

_PANEL_CSS = f"""\
.agc-app.agc-app .agc-set{{gap:6px;padding:10px 16px 12px;border-bottom:1px solid var(--ag-line)}}
.agc-app.agc-app .agc-set>*{{margin:0}}
.agc-app.agc-app .agc-set .agc-field{{align-items:center;gap:10px}}
.agc-app.agc-app .agc-set .agc-field>*{{margin:0}}
.agc-app .agc-set .ag-lbl{{display:block;width:{_LABEL_WIDTH};color:var(--ag-comment)}}
.agc-app.agc-app .agc-set select,.agc-app.agc-app .agc-set input{{font-family:var(--ag-mono);font-size:12.5px;height:28px;padding:0 8px;border:1px solid var(--ag-line);border-radius:6px;background-color:transparent;color:inherit;box-shadow:none}}
.agc-app.agc-app .agc-set select option{{background:var(--ag-bg);color:inherit}}
.agc-app.agc-app .agc-set select:focus,.agc-app.agc-app .agc-set input:focus{{border-color:var(--ag-accent);outline:none}}
.agc-app .agc-set .ag-sn{{margin:4px 0 2px calc({_LABEL_WIDTH} + 10px);font-size:12px;line-height:1.55;color:var(--ag-dim)}}
.agc-app .agc-set .ag-sn>div{{display:flex;gap:8px}}
.agc-app .agc-set .ag-sn .ag-k{{flex:0 0 auto}}
.agc-app .agc-set .ag-sn .warn{{color:var(--ag-warn)}}
.agc-app.agc-app .agc-set .agc-acts{{gap:8px;margin-left:calc({_LABEL_WIDTH} + 10px)}}
.agc-app.agc-app .agc-new.agc-on{{border-color:var(--ag-accent);color:var(--ag-accent)}}
.agc-app.agc-app .agc-use{{height:26px;line-height:24px;padding:0 10px;margin:0;border-radius:6px;font-size:12px;border:1px solid var(--ag-accent);background:transparent;box-shadow:none;color:var(--ag-accent)}}
.agc-app.agc-app .agc-use:hover:enabled{{background:var(--ag-hover)}}
.agc-app.agc-app .agc-use:disabled{{opacity:.5}}
.agt .ag-switch{{display:flex;align-items:center;gap:10px;margin:16px 0 6px;font-size:12px;color:var(--ag-dim)}}
.agt .ag-switch::before,.agt .ag-switch::after{{content:"";flex:1 1 auto;border-top:1px dashed var(--ag-line)}}"""


def form_for(provider: str) -> _Form:
    """
    What the panel asks for when `provider` is picked.
    """
    return _FORMS.get(provider, _Form())


def provider_options(given_chat_model: Any = None) -> list[tuple[str, str]]:
    """
    The provider list's lines as (label, provider=): the ones reports name by title, then the other LangChain
    providers by name, and first the chat model passed in, when there is one.
    """
    options = []
    for name in provider_names():
        title = provider_info(name).title
        options.append((title if title != name else f"{name} (LangChain)", name))
    if given_chat_model is not None:
        options.insert(0, (f"your chat model ({type(given_chat_model).__name__})", _GIVEN_MODEL))
    return options


def shown_model(provider: str, model: str) -> str:
    """
    A model as the panel and the window's head name it: a Bedrock model by its family ('claude-haiku-4-5', which
    finds the region's ID or profile again), any other as it was given.
    """
    return short_model(model) if provider == "bedrock" and model else model


def bedrock_choices(models: list[ModelInfo]) -> list[str]:
    """
    The model families a region's Bedrock text models offer, A to Z, without the ones only provisioned throughput
    serves.
    """
    usable = [info for info in models if info.via != "provisioned only"]
    return sorted({short_model(info.id) for info in usable})


def model_choices(provider: str, bedrock: list[str] | None) -> list[str]:
    """
    What the model box suggests: the region's Bedrock models (Bedrock's short names until they're read), else the
    provider's default and an example.
    """
    if provider == "bedrock":
        return bedrock or list(_MODEL_ALIASES)
    info = provider_info(provider)
    return list(dict.fromkeys(name for name in (info.default, info.example) if name))


def model_placeholder(provider: str, environ: Mapping[str, str]) -> str:
    """
    What the model box says while empty: the model it answers with then, or an example to follow.
    """
    info = provider_info(provider)
    if provider == "azure":
        deployment = environ.get("AZURE_OPENAI_DEPLOYMENT")
        return f"from AZURE_OPENAI_DEPLOYMENT: {deployment}" if deployment else f"your deployment, e.g. {info.example}"
    if info.default:
        return f"{shown_model(provider, info.default)} (the default)"
    return f"e.g. {info.example}" if info.example else "the model's name"


def endpoint_placeholder(provider: str, environ: Mapping[str, str]) -> str:
    """
    What the endpoint box says while empty: the environment variable read instead, or what to put there.
    """
    variable, hint = _ENDPOINT_HINTS.get(provider, ("", _OTHER_ENDPOINT))
    if variable and environ.get(variable):
        return f"from {variable}: {environ[variable]}"
    return hint


def key_placeholder(provider: str, *, has_key: bool, environ: Mapping[str, str]) -> str:
    """
    What the key box says while empty: that the key given before is kept, or which environment variable is read.
    """
    if has_key:
        return "the key you gave is kept: type another to replace it"
    variable = provider_info(provider).key_env
    if not variable:
        return "optional: else the provider's own environment variable"
    if environ.get(variable):
        return f"from {variable}: leave empty to use it"
    return f"paste a key, or set {variable}"


def price_text(provider: str, model: str, prices: dict[str, tuple[float, float]] | None) -> str:
    """
    How the model's tokens would be priced: the prices passed in model_prices=, Amazon Bedrock's list prices, or
    unknown (and how to give them).
    """
    priced_as = _MODEL_ALIASES.get(model.lower(), model) if provider == "bedrock" else model
    rates = model_rates(priced_as, prices, provider)
    if rates is None:
        return ("Its cost shows as unknown: AgentView(model_prices={'<model>': (input, output)}) prices it, in USD "
                "per 1M tokens.")
    whose = "Amazon Bedrock's list prices" if rates.source == "list" else "the prices you passed"
    return f"Priced at {whose}: {_per_million(rates.price_in)} in, {_per_million(rates.price_out)} out per 1M tokens."


def _text(widget: Any) -> str:
    return str(widget.value or "").strip()


class _ModelPanel:
    """
    The model settings, under the window's head while open. Its widgets are the provider list (provider_pick), the
    model box (a Combobox: it suggests, and takes any name), the endpoint, key and API version boxes (rows, shown
    for the providers that take them, _FORMS), the note under them, and Use this model.

        bedrock       the region's Bedrock model families, once read (None before)
        listing       Bedrock's models are being read
        list_problem  why they couldn't be read
        failed        why the last Use this model didn't switch
        task          the asyncio task reading Bedrock's models, in a notebook (tests await it)
    """

    def __init__(self, window: _AgentChat, widgets: Any, environ: Mapping[str, str]):
        self.window, self.w, self.environ = window, widgets, environ
        self.bedrock: list[str] | None = None
        self.listing = False
        self.list_problem = ""
        self.failed = ""
        self.task: Any = None
        self.root = self._build()
        self.refresh()

    @property
    def core(self) -> AgentAnalyzer:
        return self.window.view.core

    @property
    def is_open(self) -> bool:
        return self.root.layout.display != "none"

    def _build(self) -> Any:
        w, layout, safely = self.w, self.w.Layout, self.window._safely
        grow = {"flex": "1 1 auto", "width": "auto", "max_width": "520px"}
        self.provider_pick = w.Dropdown(options=provider_options(), layout=layout(**grow))
        self.provider_pick.observe(safely(self._picked), names="value")
        self.model_box = w.Combobox(ensure_option=False, continuous_update=True, layout=layout(**grow))
        self.model_box.observe(safely(self._typed), names="value")
        self.endpoint_box = w.Text(continuous_update=True, layout=layout(**grow))
        self.endpoint_box.observe(safely(self._typed), names="value")
        self.key_box = w.Password(layout=layout(**grow))
        self.version_box = w.Text(placeholder=f"{AZURE_API_VERSION} (the default)", layout=layout(**grow))
        for box in (self.model_box, self.endpoint_box, self.key_box, self.version_box):
            box.on_msg(self.window._on_enter(self.apply))
        self.labels = {name: w.HTML(layout=layout(flex="0 0 auto"))
                       for name in ("provider", "model", "endpoint", "key", "version")}
        self.labels["provider"].value = '<span class="ag-lbl">provider</span>'
        self.labels["key"].value = '<span class="ag-lbl">API key</span>'
        self.labels["version"].value = '<span class="ag-lbl">API version</span>'
        boxes = {"provider": self.provider_pick, "model": self.model_box, "endpoint": self.endpoint_box,
                 "key": self.key_box, "version": self.version_box}
        self.rows = {name: self._row(self.labels[name], box) for name, box in boxes.items()}
        self.note = w.HTML(layout=layout(width="100%"))
        self.use_button = self._button("use this model", "Answer with this model from the next question",
                                       self.apply, "agc-use")
        close = self._button("close", "Close the model settings, changing nothing", self.close, "agc-new")
        actions = w.HBox([self.use_button, close])
        actions.add_class("agc-acts")
        root = w.VBox([*self.rows.values(), self.note, actions], layout=layout(display="none", width="100%"))
        root.add_class("agc-set")
        return root

    def _row(self, label: Any, box: Any) -> Any:
        row = self.w.HBox([label, box], layout=self.w.Layout(width="100%"))
        row.add_class("agc-field")
        return row

    def _button(self, label: str, tooltip: str, on_click: Any, css: str) -> Any:
        button = self.w.Button(description=label, tooltip=tooltip,
                               layout=self.w.Layout(width="auto", flex="0 0 auto"))
        button.add_class(css)
        button.on_click(self.window._safely(lambda _button: on_click()))
        return button

    def open(self) -> None:
        """
        Shows the settings, filled in with the model the agent answers with now.
        """
        self.failed = ""
        self.refresh()
        self.root.layout.display = ""
        _class_if(self.window.model_button, "agc-on", True)
        if self.provider_pick.value == "bedrock":
            self._list_bedrock()

    def close(self) -> None:
        self.root.layout.display = "none"
        _class_if(self.window.model_button, "agc-on", False)
        self.window._quietly(self.key_box, value="")

    def _uses_given(self) -> bool:
        """
        Whether the agent answers with the chat model passed in (chat_model=).
        """
        given = self.core.given_chat_model
        return given is not None and self.core.chat_model is given

    def _current(self) -> str:
        """
        The provider the list starts on: the agent's, the chat model passed in, or Amazon Bedrock when there's
        none (it needs no key: the notebook's AWS credentials call it).
        """
        core = self.core
        if self._uses_given():
            return _GIVEN_MODEL
        return core.provider or "bedrock"

    def refresh(self) -> None:
        """
        The list and boxes as the agent stands: its provider picked, its model and settings filled in.
        """
        provider = self._current()
        self.window._quietly(self.provider_pick, options=provider_options(self.core.given_chat_model),
                             value=provider)
        self._fill(provider)

    def _picked(self, change: dict[str, Any]) -> None:
        if self.window.quiet:
            return
        self.failed = ""
        self._fill(change["new"])
        if change["new"] == "bedrock":
            self._list_bedrock()

    def _typed(self, change: dict[str, Any]) -> None:
        if not self.window.quiet:
            self._draw_note()

    def _fill(self, provider: str) -> None:
        """
        The boxes `provider` takes, shown with what to type in each; the agent's own settings when it's the
        provider it answers with now, else empty.
        """
        core, form, environ = self.core, form_for(provider), self.environ
        is_current = provider == core.provider and provider != _GIVEN_MODEL
        model = core.model if provider == "bedrock" and core.chat_model is not None else core.model_id
        self.window._quietly(self.model_box, options=model_choices(provider, self.bedrock),
                             placeholder=model_placeholder(provider, environ),
                             value=shown_model(provider, model) if is_current else "")
        self.window._quietly(self.endpoint_box, placeholder=endpoint_placeholder(provider, environ),
                             value=(core.endpoint or "") if is_current and form.endpoint else "")
        self.window._quietly(self.version_box, value=(core.api_version or "") if is_current and form.version else "")
        has_key = is_current and bool(core._api_key or core._token_provider)
        self.window._quietly(self.key_box, value="",
                             placeholder=key_placeholder(provider, has_key=has_key, environ=environ))
        self.labels["model"].value = f'<span class="ag-lbl">{_esc(form.model)}</span>'
        self.labels["endpoint"].value = f'<span class="ag-lbl">{_esc(form.endpoint)}</span>'
        shown = {"model": bool(form.model), "endpoint": bool(form.endpoint), "key": form.key, "version": form.version}
        for name, is_shown in shown.items():
            self.rows[name].layout.display = "" if is_shown else "none"
        self._draw_note()

    def _wanted(self) -> tuple[str, str]:
        """
        (provider, model) as the panel stands: the model typed, else the one the provider answers with by default.
        """
        provider = self.provider_pick.value
        if provider == _GIVEN_MODEL:
            return provider, model_name(self.core.given_chat_model)
        return provider, _text(self.model_box) or provider_info(provider).default

    def _draw_note(self) -> None:
        """
        Under the boxes: where what the agent sends would go, how its tokens would be priced, that a switch starts a
        new conversation, and what went wrong (reading Bedrock's models, or the last switch).
        """
        provider, model = self._wanted()
        place = model_place(provider, shown_model(provider, model), _text(self.endpoint_box) or None)
        lines = [("→", _esc(f"What the agent sends goes to {place}.")),
                 ("$", _esc(price_text(provider, model, self.core.prices)))]
        if provider == "bedrock" and self.listing:
            lines.append(("…", _esc(f"Listing Amazon Bedrock's models in {self._bedrock_region()}…")))
        elif provider == "bedrock" and self.list_problem:
            lines.append(("!", _prose(self.list_problem)))
        if any(answer.thread == self.core.thread for answer in self.core.history):
            lines.append(("↺", "Using it starts a new conversation: the one above stays on screen."))
        if self.failed:
            lines.append(("✗", f'<span class="warn">{_prose(self.failed)}</span>'))
        rows = "".join(f'<div><span class="ag-k">{mark}</span><span>{text}</span></div>' for mark, text in lines)
        self.window._set(self.note, f'<div class="{_ROOT}"><div class="ag-sn">{rows}</div></div>')

    def _bedrock_region(self) -> str:
        try:
            return self.core._bedrock_control().meta.region_name or "this region"
        except BotoCoreError:
            return "this region"

    def _list_bedrock(self) -> None:
        """
        Reads the region's Bedrock models for the model box, once: on a worker thread in a notebook, so the window
        keeps answering clicks, and right away without a running loop.
        """
        if self.bedrock is not None or self.listing:
            return
        self.listing = True
        self._draw_note()
        loop = _running_loop()
        if loop is not None:
            self.task = loop.create_task(self._list_later())
            return
        try:
            choices, problem = self._read_bedrock()
        finally:
            self.listing = False
        self._listed(choices, problem)

    async def _list_later(self) -> None:
        """
        Bedrock's models read on a worker thread, then put in the model box here, on the loop. Whatever the read
        raised is said in the window (_safely), and the note stops saying it's listing either way.
        """
        reading = asyncio.get_running_loop().run_in_executor(None, self._read_bedrock)
        await asyncio.wait([reading])
        self.listing = False
        self._draw_note()
        self.window._safely(lambda: self._listed(*reading.result()))()

    def _read_bedrock(self) -> tuple[list[str], str]:
        """
        (the region's model families, '') from ListFoundationModels and ListInferenceProfiles, or ([], what kept
        them from being read).
        """
        try:
            return bedrock_choices(self.core._bedrock_models()), ""
        except (ClientError, BotoCoreError) as exc:
            return [], (f"Couldn't list Amazon Bedrock's models here ({_error_name(exc)}): type a model ID, an "
                        "inference profile or a short name like sonnet.")

    def _listed(self, choices: list[str], problem: str) -> None:
        self.bedrock, self.list_problem = choices, problem
        if self.provider_pick.value == "bedrock":
            self.window._quietly(self.model_box, options=model_choices("bedrock", choices))
        self._draw_note()

    def apply(self) -> None:
        """
        Use this model (or Enter in a box): the agent answers with the provider and model picked from the next
        question, in a new conversation. When the model can't be made (a key, a setting or a package missing), the
        panel says why and the agent keeps the model it had.
        """
        if self.window.busy:
            self.failed = "Still answering a question: wait for it, or press stop, then switch."
            self._draw_note()
            return
        try:
            self._use(self.provider_pick.value)
        except (ValueError, ImportError, ClientError, BotoCoreError) as exc:
            self.failed = self.window._error_text(exc)
            self._draw_note()
            return
        self.failed = ""
        self.close()
        self.window.view._tell_windows("model_changed")

    def _use(self, provider: str) -> None:
        core = self.core
        if provider == _GIVEN_MODEL:
            core.use_model(chat_model=core.given_chat_model)
            return
        form = form_for(provider)
        core.use_model(provider, _text(self.model_box) or None,
                       endpoint=(_text(self.endpoint_box) or None) if form.endpoint else None,
                       api_key=(_text(self.key_box) or None) if form.key else None,
                       api_version=(_text(self.version_box) or None) if form.version else None)
