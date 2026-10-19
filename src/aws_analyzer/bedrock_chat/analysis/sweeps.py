"""
Try variations: every combination of the settings, models or data sources listed, applied to a
setup, and each setup in words.
"""

from __future__ import annotations

import itertools
import json
from typing import Any, Callable, Iterable

from ..._bedrock.blocks import _plain_json
from ..._bedrock.helpers import source_name
from ..._bedrock.prices import short_model
from ..._kit.text import _clip

from ..helpers import SWEEP_LIMIT, _norm, _short
from ..models import Schema
from .filters import describe_filter
from .request import coerce_setting
from .batches import Batch


# --------------------------------------------- sweeps: many setups at once, and test runs compared and kept

_PICKED = ("model", "data_source", "files")  # what a sweep can vary besides the settings: picked, not sent as a setting

# settings that, left out, mean "none of it" (no reranking, no guardrail); any other one left out means Bedrock's default
_OFF_WHEN_UNSET = {"reranker", "rerank_n", "guardrail_id", "guardrail_version", "query_decomposition", "stop", "kms_key",
                   "filter"}

_NONE_WORDS = {"none", "off", "-", "default", "null", "unset"}  # in Try variations: leave the setting out


def _sweep_name(name: Any) -> str:
    """
    'Model', 'data source', 'dataSources' -> 'model', 'data_source'; a setting's name stays as it is.
    """
    text = str(name).strip()
    wanted = _norm(text)
    for key, names in (("model", ("model", "models")), ("data_source", ("datasource", "datasources")),
                       ("files", ("files", "file"))):
        if wanted in names:
            return key
    return text


def _alternatives(value: Any) -> list[Any]:
    """
    A sweep's values for one name: a list, tuple, set or range is the values to try; anything else is one value.
    """
    if isinstance(value, (list, tuple, range)):
        return list(value)
    if isinstance(value, (set, frozenset)):
        try:
            return sorted(value)
        except TypeError:
            return sorted(value, key=repr)
    return [value]


def _setup_key(setup: Any) -> str:
    """
    What tells two setups (or two values) apart: the same JSON, keys in any order.
    """
    return json.dumps(_plain_json(setup), sort_keys=True, ensure_ascii=False)


def sweep_setups(grid: dict[str, Any] | None = None, setups: Iterable[dict[str, Any]] | None = None, *,
                 limit: int | None = SWEEP_LIMIT) -> list[dict[str, Any]]:
    """
    Every combination to try -> [{name: value}], each one setup's changes to the settings it starts from.

    grid: {name: values}. A list means each of its values (n=[5, 10]); anything else is one value every setup uses.
    None (or 'none' in the window) leaves a setting out, so Bedrock's default applies. Names are settings (fields()
    lists them), 'model', 'data_source' and 'files'; a setting that takes a list goes in a list of its own
    (stop=[['END'], ['###']]). setups: whole setups to start from, each combined with the grid: [{'n': 5}, {'n': 10,
    'reranker': 'cohere'}]. Duplicates are dropped. A ValueError says when that's one setup (nothing to compare) or
    more than `limit`.
    """
    bases = [{} if s is None else s for s in (setups if setups is not None else [{}])]
    if not bases or not all(isinstance(s, dict) for s in bases):
        raise ValueError("setups takes a list of dicts, each one setup's settings, like [{'n': 5}, {'n': 10, "
                         "'reranker': 'cohere'}]")
    choices = []
    for name, value in (grid or {}).items():
        values = _alternatives(value)
        if not values:
            raise ValueError(f"{name} has no values to try: give it a list, like {name}=[...]")
        choices.append([(_sweep_name(name), v) for v in values])
    found: dict[str, dict[str, Any]] = {}
    for base in bases:
        for combo in itertools.product(*choices):
            setup = {_sweep_name(k): v for k, v in base.items()}
            setup.update(combo)
            found.setdefault(_setup_key(setup), setup)
    out = list(found.values())
    if len(out) < 2:
        raise ValueError("That's one setup, so there's nothing to compare: give lists of values to try, like "
                         "sweep(n=[5, 10], search_type=['SEMANTIC', 'HYBRID']). ask_all() asks with one setup.")
    if limit and len(out) > limit:
        sizes = [f"{len(c)} {c[0][0]}" for c in choices if len(c) > 1]
        sizes += [f"{len(bases)} setups"] if len(bases) > 1 else []
        raise ValueError(f"That's {len(out)} setups ({' × '.join(sizes)}), and a sweep asks up to {limit}: try fewer "
                         f"values, or pass max_setups={len(out)}.")
    return out


def apply_setup(settings: dict[str, Any], changes: dict[str, Any], schema: Schema) -> dict[str, Any]:
    """
    Settings with one setup's changes made: each change checked and converted (coerce_setting()), None leaving the
    setting out; 'model', 'data_source' and 'files' aren't settings, so they're skipped. A ValueError says what a
    change can't be.
    """
    values = dict(settings)
    for name, value in changes.items():
        if _sweep_name(name) in _PICKED:
            continue
        f = schema.find(name)
        if value is None:
            values.pop(f.key, None)
        else:
            values[f.key] = coerce_setting(f, value)
    return {key: values[key] for key in schema.fields if key in values}


def _split_values(text: str) -> list[str]:
    """
    '5, 10' -> ['5', '10']. A comma inside brackets, braces or a quoted value stays ('{"a": 1, "b": 2}, none').
    """
    parts, depth, quote, start = [], 0, "", 0
    for i, ch in enumerate(text):
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "\"'" and not text[start:i].strip():  # a quote only opens at the start of a value ("don't")
            quote = ch
        elif ch in "[{(":
            depth += 1
        elif ch in "]})":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return [p.strip() for p in parts]


def _variation_value(text: str) -> Any:
    """
    One value typed in Try variations: quotes taken off, 'none' -> None (leave the setting out).
    """
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return None if text.lower() in _NONE_WORDS else text


def parse_variations(text: str) -> dict[str, list[Any]]:
    """
    What to try, as typed in the window's Try variations box -> {name: [values]} for sweep_setups(). One setting
    (or model, data_source, files) per line, then = (or :) and the values to try, separated by commas; 'none' leaves
    the setting out. Blank lines and lines starting with # are skipped. A ValueError names the line it can't read.

        n = 5, 10, 20
        search_type = SEMANTIC, HYBRID
        reranker = none, cohere
        model = haiku, sonnet
    """
    grid: dict[str, list[Any]] = {}
    lines: dict[str, int] = {}
    for number, line in enumerate(str(text or "").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, rest = line.partition("=") if "=" in line.split(":", 1)[0] else line.partition(":")
        name = name.strip()
        if not sep or not name:
            raise ValueError(f"Line {number}: write a setting, =, then the values to try, like n = 5, 10")
        values = [_variation_value(v) for v in _split_values(rest) if v]
        if not values:
            raise ValueError(f"Line {number}: {name} has no values: put them after the =, like {name} = 5, 10")
        key = _sweep_name(name)
        if _norm(key) in lines:
            raise ValueError(f"Line {number}: {name} is on line {lines[_norm(key)]} already: put its values on one "
                             "line")
        lines[_norm(key)] = number
        grid[key] = values
    return grid


def format_variations(grid: dict[str, Any]) -> str:
    """
    The opposite of parse_variations(): 'n = 5, 10' lines, None as 'none'.
    """
    def text(v: Any) -> str:
        if v is None:
            return "none"
        shown = v if isinstance(v, str) else json.dumps(_plain_json(v), ensure_ascii=False)
        return f'"{shown}"' if isinstance(v, str) and ("," in v or v.lower() in _NONE_WORDS) else shown

    return "\n".join(f"{name} = {', '.join(text(v) for v in _alternatives(values))}" for name, values in grid.items())


def run_setup(batch: Batch) -> dict[str, Any]:
    """
    What a test run was asked with, flat, to compare runs by: 'kb' (the knowledge base's name), 'model' (unless it
    only searched), 'data_source' and 'files' (when it asked only some), and its settings.
    """
    setup: dict[str, Any] = {"kb": batch.kb_name or batch.kb_id}
    if not batch.retrieve_only:
        setup["model"] = batch.model
    if batch.data_sources:
        setup["data_source"] = sorted(name or ds_id for ds_id, name in batch.data_sources.items())
    if batch.files:
        setup["files"] = sorted(batch.files)
    setup.update(batch.settings)
    return setup


def varied_setups(batches: Iterable[Batch]) -> dict[str, list[Any]]:
    """
    What differs between test runs' setups (run_setup()) -> {name: [each value, in the order first seen]}: None
    where a run didn't send that setting, or asked every data source or file.
    """
    setups = [run_setup(b) for b in batches]
    out: dict[str, list[Any]] = {}
    for key in dict.fromkeys(k for s in setups for k in s):
        values: dict[str, Any] = {}
        for s in setups:
            values.setdefault(_setup_key(s.get(key)), s.get(key))
        if len(values) > 1:
            out[key] = list(values.values())
    return out


def _setup_value(key: str, value: Any, values: list[Any], label: Callable[[str], str] | None = None) -> str:
    """
    One value of what differs between setups, short: 10, HYBRID, Claude Sonnet 5, all (data sources), #2 (a
    prompt).
    """
    if key in ("data_source", "files"):
        names = [source_name(v) or v for v in value or []] if key == "files" else list(value or [])
        return _clip(", ".join(map(str, names)), 40) if names else "all"
    if value is None:
        return "none" if key in _OFF_WHEN_UNSET else "default"
    if key == "model":
        return (label or short_model)(str(value))
    if key == "filter":
        return describe_filter(value)
    if isinstance(value, str) and len(value) > 40:
        index = next((k for k, v in enumerate(values) if v == value), 0)
        return f"#{index + 1} ({len(value):,} characters)"
    return value if isinstance(value, str) else _short(value, 30)


def setup_label(batch: Batch, varied: dict[str, list[Any]], label: Callable[[str], str] | None = None) -> str:
    """
    What sets a run apart from those it's compared with: 'n=10 · search_type=HYBRID', from the names `varied`
    holds (varied_setups()); 'the same setup' when nothing differs. label(model) names a model (its short ID by
    default).
    """
    setup = run_setup(batch)
    parts = []
    for key, values in varied.items():
        value = setup.get(key)
        text = _setup_value(key, value, values, label)
        if key == "filter":
            parts.append(f"where {text}" if value is not None else "no filter")
        else:
            parts.append(f"{key}={text}")
    return " · ".join(parts) or "the same setup"
