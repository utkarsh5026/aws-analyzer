"""
The AgentView commands about its model, from a cell: use_model() switches to one (and saves it under a name),
saved_models() lists the saved ones, use_saved_model() switches to one by its name, model_names() suggests names made
from a model's words, rename_model() relabels one and forget_model() forgets one or every one. The ⚙ model panel
(model_panel.py) does the same by clicking. _ModelCommands is a mixin of AgentView, kept here because the View's own
module is full; it draws with the View's blocks and asks the windows to redraw through the View's _tell_windows.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..._kit.blocks import _Cards, _Next, _Note, _Table, _Title
from ..._kit.errors import _Hint
from ..._kit.fmt import _plural, human_age
from ..._kit.view import _friendly_errors
from ..llm import model_place, provider_info
from ..saved_model import SavedModel, home_relative, saved_model_note

if TYPE_CHECKING:
    from ..analyzer import AgentAnalyzer

_NO_TIME = ""


def _saved_age(saved: SavedModel) -> str:
    """
    When a saved model was last used, as an age ('3d ago'); '' when the file doesn't say.
    """
    if saved.saved_at == _NO_TIME:
        return ""
    try:
        return human_age(datetime.fromisoformat(saved.saved_at))
    except ValueError:
        return ""


def _saved_row(saved: SavedModel, *, label: str) -> list[Any]:
    """
    A saved model's line in saved_models(): its name (● when it's the one in use), provider, model, where it goes,
    whether its key is saved, and when it was last used.
    """
    mark = "● " if saved.name == label else ""
    where = saved.endpoint or "the provider's own"
    key = "saved" if saved.has_key else "from the environment"
    return [f"{mark}{saved.name}", provider_info(saved.provider).title, saved.model or saved.deployment or "default",
            where, key, _saved_age(saved)]


class _ModelCommands:
    """
    AgentView's model commands, as a mixin: core, _busy_window, _tell_windows and _show are AgentView's.
    """

    core: AgentAnalyzer
    _busy_window: Any
    _tell_windows: Any
    _show: Any

    def _refuse_while_busy(self) -> None:
        if self._busy_window() is not None:
            raise _Hint("The chat window is answering a question: wait for it, or press its Stop, then switch.")

    def _now_answering(self) -> list[Any]:
        """
        What a switch says: the model it answers with now, where what it sends goes, and the name it's saved under.
        """
        core = self.core
        place = model_place(core.provider, core.model, core.endpoint)
        named = f"{core.model_label!r}, " if core.model_label else ""
        blocks = [_Note(f"Now answering with {named}{core.model} ({provider_info(core.provider).title}): what it "
                        f"sends goes to {place}. A new conversation starts with the next question.", "ok")]
        saved = saved_model_note(core.saved_model, core.model_file, core.saved_model_problem)
        if saved:
            blocks.append(_Note(saved))
        return blocks

    @_friendly_errors
    def use_model(  # noqa: PLR0913
        self,
        provider: str | None = None,
        model: str | None = None,
        *,
        endpoint: str | None = None,
        api_key: str | None = None,
        api_version: str | None = None,
        chat_model: Any = None,
        name: str | None = None,
    ) -> None:
        """
        Switches the model the agent answers with, then says where what it sends goes and how its tokens are priced:
        use_model('bedrock', 'sonnet'), use_model('openai', 'gpt-4.1', api_key=...), use_model('azure', 'prod-gpt-4o',
        endpoint=...), or any LangChain provider. The chat window's ⚙ model does the same by clicking. A new
        conversation starts, and a key or package that's missing is said here, keeping the model it had. The model
        and the key given are saved for the next agent you start: name='work gpt' saves it under that name, else it
        gets one made from its words ('Haiku Hummingbird'; model_names() suggests more).
        """
        self._refuse_while_busy()
        self.core.use_model(provider, model, endpoint=endpoint, api_key=api_key, api_version=api_version,
                            chat_model=chat_model, name=name)
        self._tell_windows("model_changed")
        self._show(self._now_answering())

    @_friendly_errors
    def use_saved_model(self, name: str) -> None:
        """
        Switches to a model saved earlier, by its name (any case): use_saved_model('work gpt'). Its key comes with
        it, a new conversation starts, and the next agent you start answers with it too. saved_models() lists them.
        """
        self._refuse_while_busy()
        self.core.use_saved_model(name)
        self._tell_windows("model_changed")
        self._show(self._now_answering())

    @_friendly_errors
    def saved_models(self) -> None:
        """
        The models you've saved, by name: the provider, the model, where it goes, whether its key is saved and when
        it was last used, the one in use marked ●. The next agent you start answers with the one used last.
        """
        core = self.core
        if core.model_file is None:
            self._show([_Title("Saved models"), _Note("Models aren't saved: this agent was started with "
                                                       "remember_model=False.")])
            return
        shelf = core.saved_models
        blocks: list[Any] = [_Title("Saved models", home_relative(core.model_file))]
        if core.saved_model_problem:
            blocks.append(_Note(core.saved_model_problem, "warn"))
        if not shelf.models:
            blocks.append(_Note("None yet: use_model('bedrock', 'sonnet', name='my sonnet') saves the model it "
                                "switches to, and so does the chat window's ⚙ model."))
            self._show(blocks)
            return
        in_use = shelf.in_use()
        with_keys = [saved for saved in shelf.models if saved.has_key]
        blocks.append(_Cards([("Saved", f"{len(shelf.models):,}"),
                              ("Next agent starts with", in_use.name if in_use else "none"),
                              ("Keys kept", f"{len(with_keys):,}")]))
        rows = [_saved_row(saved, label=core.model_label) for saved in reversed(shelf.models)]
        blocks.append(_Table(["Name", "Provider", "Model", "Endpoint", "API key", "Used"], rows, sortable=False))
        if with_keys:
            blocks.append(_Note(f"{_plural(len(with_keys), 'key')} kept in {home_relative(core.model_file)}, a file "
                                "readable by you only: forget_model('name') forgets one model and its key."))
        first = shelf.models[-1].name
        blocks.append(_Next([(f"use_saved_model({first!r})", "answer with it"),
                             (f"rename_model({first!r}, 'new name')", "give it a name of your own"),
                             (f"forget_model({first!r})", "forget it and its key")]))
        self._show(blocks)

    @_friendly_errors
    def model_names(self, provider: str | None = None, model: str | None = None) -> None:
        """
        Names to save a model under, made from its words and not taken yet: model_names() for the model it answers
        with, model_names('openai', 'gpt-4o') for another. use_model(..., name=...) or rename_model() gives one.
        """
        core = self.core
        names = core.model_names(provider, model, most=5)
        about = model or core.model_id or "the model"
        current = core.model_label or "the model it answers with"
        lines = [[name] for name in names]
        self._show([_Title("Names for the model", about), _Table(["Suggested"], lines, sortable=False),
                    _Next([(f"rename_model({current!r}, {names[0]!r})", "save the model in use under it"),
                           ("saved_models()", "the names you've saved")])])

    @_friendly_errors
    def rename_model(self, old: str, new: str) -> None:
        """
        Renames a saved model: rename_model('Haiku Hummingbird', 'work haiku'). Any case finds it; a name another
        saved model has is refused.
        """
        name = self.core.rename_model(old, new)
        self._tell_windows("models_saved")
        self._show([_Note(f"Renamed {old!r} to {name!r}: use_saved_model({name!r}) answers with it.", "ok")])

    @_friendly_errors
    def forget_model(self, name: str | None = None) -> None:
        """
        Forgets saved models and their keys: forget_model('work gpt') one, forget_model() every one (the file,
        ~/.cache/aws-analyzer/agent/model.json, is deleted). The model it answers with now stays.
        """
        core = self.core
        path = core.model_file
        if path is None:
            self._show([_Note("No model is saved (remember_model=False): nothing to forget.")])
            return
        forgotten = core.forget_model(name)
        self._tell_windows("models_saved")
        if not forgotten:
            self._show([_Note(f"No model is saved in {home_relative(path)}: nothing to forget.")])
            return
        if name is None:
            self._show([_Note(f"Forgot {_plural(len(forgotten), 'saved model')} and their keys: {path} is deleted. "
                              "The next AgentView() answers with the model it's given, or the one the environment "
                              "names.", "ok")])
            return
        self._show([_Note(f"Forgot {forgotten[0]!r} and its key. saved_models() lists the rest.", "ok")])
