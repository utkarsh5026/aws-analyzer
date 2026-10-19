"""
Test runs as JSON lines, and read back from them.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from typing import Any, Iterable

from ..._bedrock.blocks import _plain_json
from ..._bedrock.models import Citation, Passage

from ..helpers import RUNS_FORMAT
from ..models import Answer
from .batches import Batch, BatchItem


def run_record(batch: Batch) -> dict[str, Any]:
    """
    A test run as plain JSON data, one line of save_runs()'s file: when it ran, its setup, and each question with
    its answer (text, citations and sources) or why it failed, its cost and the request sent. Bedrock's raw responses
    are left out, to keep the file small.
    """
    def answer(a: Answer | None) -> dict[str, Any] | None:
        if a is None:
            return None
        data = dataclasses.asdict(a)
        data.pop("response", None)
        return data

    record = {
        "format": RUNS_FORMAT, "id": batch.id, "label": batch.label, "sweep": batch.sweep,
        "started": batch.started.isoformat() if batch.started else None,
        "kb_id": batch.kb_id, "kb_name": batch.kb_name, "model": batch.model, "settings": batch.settings,
        "data_sources": batch.data_sources, "files": batch.files, "retrieve_only": batch.retrieve_only,
        "request": batch.request, "seconds": batch.seconds, "skipped": batch.skipped, "stopped": batch.stopped,
        "items": [{"question": i.question, "expected": i.expected, "error": i.error, "error_code": i.error_code,
                   "cost": i.cost, "request": i.request, "answer": answer(i.answer)} for i in batch.items],
    }
    return _plain_json(record)


def _known(cls: Any, data: dict[str, Any]) -> dict[str, Any]:
    """
    The keys of `data` that are fields of the dataclass `cls`: what a newer file adds is left out.
    """
    names = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in data.items() if k in names}


def run_from_record(record: Any) -> Batch:
    """
    The opposite of run_record(): a test run read back from save_runs()'s file. A ValueError says what's wrong
    with a record it can't read.
    """
    if not isinstance(record, dict) or record.get("format") != RUNS_FORMAT:
        what = record.get("format") if isinstance(record, dict) else type(record).__name__
        raise ValueError(f"not a test run saved by save_runs() (format {what!r}, not {RUNS_FORMAT!r})")

    def answer(data: Any) -> Answer | None:
        if data is None:
            return None
        a = Answer(**{k: v for k, v in _known(Answer, data).items() if k not in ("citations", "sources")})
        a.citations = [Citation(**_known(Citation, c)) for c in data.get("citations") or []]
        a.sources = [Passage(**_known(Passage, p)) for p in data.get("sources") or []]
        return a

    try:
        batch = Batch(**{k: v for k, v in _known(Batch, record).items() if k not in ("items", "started")})
        started = record.get("started")
        batch.started = datetime.fromisoformat(started) if started else None
        batch.items = [BatchItem(**{**_known(BatchItem, item), "answer": answer(item.get("answer"))})
                       for item in record.get("items") or []]
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"a test run that can't be read back ({type(exc).__name__}: {exc})") from None
    if not batch.id:
        raise ValueError("a test run without an id")
    return batch


def read_runs(lines: Iterable[str]) -> tuple[list[Batch], list[str]]:
    """
    The test runs in save_runs()'s file, JSON Lines with one run per line -> (runs, [why a line couldn't be
    read, with its number]). Blank lines are skipped.
    """
    runs: list[Batch] = []
    problems: list[str] = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            runs.append(run_from_record(json.loads(line)))
        except json.JSONDecodeError as exc:
            problems.append(f"line {number} isn't JSON ({exc.msg})")
        except ValueError as exc:
            problems.append(f"line {number} is {exc}")
    return runs, problems
