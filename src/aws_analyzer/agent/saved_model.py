"""
The models the agent has answered with, kept between kernels under names, so none needs setting up again: one JSON
file (MODEL_FILE in aws-analyzer's cache folder, AWS_ANALYZER_MODEL_FILE's path, or the file
AgentAnalyzer(remember_model=...) names) holding each saved model by its name (the provider, the model or Azure
deployment, the endpoint, the API version and the API key it was given, if any) and which one is in use, which the
next agent starts with. A key read from the environment isn't saved: the environment gives it again.

A model gets the name it's given (use_model(..., name='work gpt')) or one model_names.suggest_names() makes from its
words ('Haiku Hummingbird'); using the same model again keeps its name. SavedModels is the file's content as data, and
with_model(), without_model() and renamed() return a changed copy, so everything but reading and writing is pure.

The keys are kept as they were typed, so the file is written readable by its owner only (to <name>.part first, then
renamed over it), like the agent's mcp.json. A chat model passed in (chat_model=) or an Azure token provider can't be
written down, so they're never saved. forget_saved_models() deletes the file, and only that file.
"""

from __future__ import annotations

import difflib
import json
import os
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any

from .model_names import clean_model_name, suggest_names

MODEL_FILE = Path("~/.cache/aws-analyzer/agent/model.json")
MODEL_FILE_ENV = "AWS_ANALYZER_MODEL_FILE"
_OFF_WORDS = frozenset({"off", "0", "false", "no", "none"})
_OWNER_ONLY = 0o600
_CLOSE_NAMES = 3


@dataclass(frozen=True)
class SavedModel:
    """
    A saved model's settings.

        provider     'azure', 'openai', 'bedrock', or any LangChain provider
        model        the model ID, or Azure's deployment
        deployment   Azure OpenAI's deployment ('' for other providers)
        endpoint     the endpoint or base URL given ('' for the provider's own)
        api_version  Azure OpenAI's API version ('' for its default)
        api_key      the key given ('' when the environment gives it)
        saved_at     when it was last used, ISO UTC
        name         the label it's kept under: 'work gpt', 'Haiku Hummingbird'
    """

    provider: str
    model: str = ""
    deployment: str = ""
    endpoint: str = ""
    api_version: str = ""
    api_key: str = ""
    saved_at: str = ""
    name: str = ""

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)

    def same_model(self, other: SavedModel) -> bool:
        """
        Whether two saved models call the same model in the same place (their keys and names aside).
        """
        where = ("provider", "model", "deployment", "endpoint", "api_version")
        return all(getattr(self, part) == getattr(other, part) for part in where)


@dataclass(frozen=True)
class SavedModels:
    """
    What the file holds: the saved models, oldest first, and the name of the one in use.
    """

    models: tuple[SavedModel, ...] = ()
    current: str = ""

    def names(self) -> list[str]:
        return [saved.name for saved in self.models]

    def get(self, name: str) -> SavedModel | None:
        """
        The saved model of that name, in any case; None when none has it.
        """
        wanted = " ".join(str(name).split()).casefold()
        return next((saved for saved in self.models if saved.name.casefold() == wanted), None)

    def in_use(self) -> SavedModel | None:
        """
        The model the next agent starts with: the current one, else the one used last.
        """
        current = self.get(self.current) if self.current else None
        if current is not None or not self.models:
            return current
        return max(self.models, key=lambda saved: saved.saved_at)

    def find(self, name: str) -> SavedModel:
        """
        The saved model of that name; ValueError naming the closest ones when none has it.
        """
        found = self.get(name)
        if found is not None:
            return found
        if not self.models:
            raise ValueError("No model is saved yet: use_model(...) saves the model it switches to.")
        lowered = {saved.name.casefold(): saved.name for saved in self.models}
        close = difflib.get_close_matches(name.casefold(), list(lowered), n=_CLOSE_NAMES, cutoff=0.4)
        shown = close or list(lowered)
        names = ", ".join(repr(lowered[key]) for key in shown)
        raise ValueError(f"No saved model is called {name!r}. {'Did you mean' if close else 'Saved'}: {names}?")

    def matching(self, settings: SavedModel) -> SavedModel | None:
        return next((saved for saved in self.models if saved.same_model(settings)), None)


def name_for(shelf: SavedModels, settings: SavedModel, wanted: str = "") -> str:
    """
    The name `settings` is saved under: the one wanted, else the name it's already saved under, else the first name
    suggest_names() offers that no other saved model has.
    """
    if wanted:
        return clean_model_name(wanted)
    known = shelf.matching(settings)
    if known is not None:
        return known.name
    return suggest_names(settings.provider, settings.model, taken=shelf.names(), most=1)[0]


def with_model(shelf: SavedModels, settings: SavedModel, *, name: str = "") -> SavedModels:
    """
    The shelf with `settings` saved and in use: the saved model of the same name, or else of the same model, is
    replaced (a key it had is kept when none is given now), and a new one is added last.
    """
    chosen = name_for(shelf, settings, name)
    replaced = shelf.get(chosen) or shelf.matching(settings)
    if replaced is not None and not settings.api_key and replaced.same_model(settings):
        settings = replace(settings, api_key=replaced.api_key)
    saved = replace(settings, name=chosen)
    kept = tuple(model for model in shelf.models if model is not replaced)
    return SavedModels(kept + (saved,), current=chosen)


def without_model(shelf: SavedModels, name: str) -> SavedModels:
    """
    The shelf without the saved model of that name (ValueError when none has it); the one in use stays unless it's
    the one dropped.
    """
    dropped = shelf.find(name)
    kept = tuple(saved for saved in shelf.models if saved is not dropped)
    current = "" if dropped.name == shelf.current else shelf.current
    return SavedModels(kept, current=current)


def renamed(shelf: SavedModels, old: str, new: str) -> SavedModels:
    """
    The shelf with a saved model renamed; ValueError when none has the old name or another has the new one.
    """
    model = shelf.find(old)
    name = clean_model_name(new)
    other = shelf.get(name)
    if other is not None and other is not model:
        raise ValueError(f"Another saved model is already called {other.name!r}: pick another name.")
    models = tuple(replace(saved, name=name) if saved is model else saved for saved in shelf.models)
    current = name if shelf.current == model.name else shelf.current
    return SavedModels(models, current=current)


def in_use(shelf: SavedModels, name: str) -> SavedModels:
    """
    The shelf with the saved model of that name in use (ValueError when none has it).
    """
    return replace(shelf, current=shelf.find(name).name)


def saved_model_path(remember: bool | str | os.PathLike | None) -> Path | None:
    """
    The file the agent keeps its models in: MODEL_FILE for True (or the path in AWS_ANALYZER_MODEL_FILE, which 'off'
    turns off), the path given ('~' expanded), or None when remembering is off (False or None).
    """
    if remember is None or remember is False:
        return None
    if remember is True:
        chosen = os.environ.get(MODEL_FILE_ENV, "").strip()
        if chosen.lower() in _OFF_WORDS:
            return None
        return Path(chosen or MODEL_FILE).expanduser()
    return Path(remember).expanduser()


def _model_from(data: Any, name: str = "") -> SavedModel | None:
    """
    The SavedModel one entry of the file holds, its unknown keys ignored; None when it names no provider.
    """
    if not isinstance(data, dict):
        return None
    known = {item.name for item in fields(SavedModel)}
    values = {key: str(value) for key, value in data.items() if key in known and value is not None}
    if not values.get("provider"):
        return None
    if name:
        values["name"] = name
    return SavedModel(**values)


def shelf_from(data: Any) -> SavedModels:
    """
    The SavedModels a file's JSON holds: {"current": name, "models": {name: settings}}, or one model's settings as
    the first version of the file kept them (named then as suggest_names() names it).
    """
    if not isinstance(data, dict):
        return SavedModels()
    if "models" not in data:
        single = _model_from(data)
        return with_model(SavedModels(), single, name=single.name) if single else SavedModels()
    entries = data["models"] if isinstance(data["models"], dict) else {}
    models = []
    for name, entry in entries.items():
        model = _model_from(entry, clean_model_name(name) if str(name).strip() else "")
        if model is not None and model.name:
            models.append(model)
    return SavedModels(tuple(models), current=str(data.get("current") or ""))


def read_saved_models(path: Path | None) -> tuple[SavedModels, str]:
    """
    (the models the file keeps, '') or (none, what kept it from being read). No file, or no path, keeps none, and
    that isn't a problem: the file is made the first time a model is used.
    """
    if path is None or not path.exists():
        return SavedModels(), ""
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, UnicodeDecodeError) as exc:
        return SavedModels(), f"The saved models in {path} can't be read: {exc}"
    except json.JSONDecodeError as exc:
        return SavedModels(), (f"The saved models in {path} aren't JSON ({exc.msg} at line {exc.lineno}): "
                               "forget_model() deletes the file, and the next model you use is saved anew")
    return shelf_from(data), ""


def shelf_data(shelf: SavedModels) -> dict[str, Any]:
    """
    What the file holds for a shelf: the model in use, and each model's settings by its name.
    """
    models = {}
    for saved in shelf.models:
        settings = asdict(saved)
        settings.pop("name")
        models[saved.name] = settings
    return {"current": shelf.current, "models": models}


def write_saved_models(path: Path, shelf: SavedModels) -> None:
    """
    Writes the saved models to `path`, replacing what it held: to <name>.part first, then renamed over it, readable
    by its owner only, since it can hold keys.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f"{path.name}.part")
    descriptor = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _OWNER_ONLY)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(shelf_data(shelf), indent=2) + "\n")
    os.chmod(part, _OWNER_ONLY)
    os.replace(part, path)


def forget_saved_models(path: Path | None) -> bool:
    """
    Deletes the saved models' file; whether there was one to delete.
    """
    if path is None or not path.exists():
        return False
    path.unlink()
    return True


def home_relative(path: Path) -> str:
    """
    A path from the home folder (~/...) when it's under it.
    """
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


def saved_model_note(saved: SavedModel | None, path: Path | None, problem: str = "") -> str:
    """
    What the reports and the ⚙ model panel say about the model in use: what kept it from being read or written,
    the name it's saved under, where and whether with a key, or '' when nothing is saved.
    """
    if problem:
        return problem
    if saved is None or path is None:
        return ""
    with_key = ", with its API key (the file is readable by you only)" if saved.has_key else ""
    return (f"Saved as {saved.name!r} in {home_relative(path)}{with_key}: the next agent you start answers with it. "
            f"rename_model({saved.name!r}, 'new name') renames it, saved_models() lists every one, and "
            "forget_model() deletes them.")

