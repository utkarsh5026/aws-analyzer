"""
The model the agent answered with last, kept between kernels so it needn't be set up again: one JSON file
(MODEL_FILE in aws-analyzer's cache folder, AWS_ANALYZER_MODEL_FILE's path, or the file
AgentAnalyzer(remember_model=...) names) holding the provider, the model or Azure deployment, the endpoint, the API
version and the API key it was given, if any. A key read from the environment isn't saved: the environment gives it
again.

The key is kept as it was typed, so the file is written readable by its owner only (to <name>.part first, then
renamed over it), like the agent's mcp.json. A chat model passed in (chat_model=) or an Azure token provider can't be
written down, so they're never saved. forget_saved_model() deletes the file, and only that file.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

MODEL_FILE = Path("~/.cache/aws-analyzer/agent/model.json")
MODEL_FILE_ENV = "AWS_ANALYZER_MODEL_FILE"
_OFF_WORDS = frozenset({"off", "0", "false", "no", "none"})
_OWNER_ONLY = 0o600


@dataclass(frozen=True)
class SavedModel:
    """
    The model settings a file keeps.

        provider     'azure', 'openai', 'bedrock', or any LangChain provider
        model        the model ID, or Azure's deployment
        deployment   Azure OpenAI's deployment ('' for other providers)
        endpoint     the endpoint or base URL given ('' for the provider's own)
        api_version  Azure OpenAI's API version ('' for its default)
        api_key      the key given ('' when the environment gives it)
        saved_at     when it was saved, ISO UTC
    """

    provider: str
    model: str = ""
    deployment: str = ""
    endpoint: str = ""
    api_version: str = ""
    api_key: str = ""
    saved_at: str = ""

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)


def saved_model_path(remember: bool | str | os.PathLike | None) -> Path | None:
    """
    The file the agent keeps its model in: MODEL_FILE for True (or the path in AWS_ANALYZER_MODEL_FILE, which 'off'
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


def _from_data(data: Any) -> SavedModel | None:
    """
    The SavedModel a file's JSON holds, its unknown keys ignored; None when it names no provider.
    """
    if not isinstance(data, dict):
        return None
    known = {item.name for item in fields(SavedModel)}
    values = {name: str(value) for name, value in data.items() if name in known and value is not None}
    if not values.get("provider"):
        return None
    return SavedModel(**values)


def read_saved_model(path: Path | None) -> tuple[SavedModel | None, str]:
    """
    (the model the file keeps, '') or (None, what kept it from being read). No file, or no path, keeps none, and
    that isn't a problem: the file is made the first time a model is used.
    """
    if path is None or not path.exists():
        return None, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"The saved model in {path} can't be read: {exc}"
    except json.JSONDecodeError as exc:
        return None, (f"The saved model in {path} isn't JSON ({exc.msg} at line {exc.lineno}): forget_model() "
                      "deletes it, and the next model you use is saved anew")
    return _from_data(data), ""


def write_saved_model(path: Path, saved: SavedModel) -> None:
    """
    Writes the model settings to `path`, replacing what it held: to <name>.part first, then renamed over it,
    readable by its owner only, since it can hold a key.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f"{path.name}.part")
    descriptor = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _OWNER_ONLY)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(saved), indent=2) + "\n")
    os.chmod(part, _OWNER_ONLY)
    os.replace(part, path)


def forget_saved_model(path: Path | None) -> bool:
    """
    Deletes the saved model's file; whether there was one to delete.
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
    What the reports and the ⚙ model panel say about the saved model: what kept it from being read or written,
    where it's kept and whether with a key, or '' when nothing is saved.
    """
    if problem:
        return problem
    if saved is None or path is None:
        return ""
    with_key = ", with its API key (the file is readable by you only)" if saved.has_key else ""
    return (f"Saved in {home_relative(path)}{with_key}: the next agent you start answers with it, and forget_model() "
            "deletes it.")
