"""
Bedrock's answers read into an Answer: a RetrieveAndGenerate response, or its stream put
back together.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from ..._bedrock.models import Citation, Passage
from ..._bedrock.parsing import _span, parse_passage

from ..models import Answer


def parse_rag(resp: dict[str, Any]) -> Answer:
    """
    A RetrieveAndGenerate response -> Answer: the text, each cited span, and the passages behind them numbered
    from 1 in the order the answer first cites them (a passage cited twice keeps one number).
    """
    text = (resp.get("output") or {}).get("text") or ""
    sources: list[Passage] = []
    numbers: dict[str, int] = {}
    citations = []
    for cite in resp.get("citations") or []:
        part = (cite.get("generatedResponsePart") or {}).get("textResponsePart") or {}
        cited = []
        for ref in cite.get("retrievedReferences") or []:
            passage = parse_passage(ref, len(sources) + 1)
            if passage.key not in numbers:
                sources.append(passage)
                numbers[passage.key] = len(sources)
            cited.append(numbers[passage.key])
        start, end = _span(text, part)
        citations.append(Citation(start, end, text[start:end], list(dict.fromkeys(cited))))
    return Answer(
        question="",
        text=text,
        citations=citations,
        sources=sources,
        session_id=resp.get("sessionId"),
        guardrail_action=resp.get("guardrailAction"),
    )


def collect_stream(events: Iterable[dict[str, Any]], on_text: Callable[[str], None] | None = None) -> dict[str, Any]:
    """
    RetrieveAndGenerateStream's events -> a response shaped like RetrieveAndGenerate's (output.text, citations,
    guardrailAction), so parse_rag() reads both. on_text gets the answer so far each time more of it arrives.
    """
    text: list[str] = []
    citations: list[dict[str, Any]] = []
    guardrail = None
    for event in events:
        if "output" in event:
            text.append((event["output"] or {}).get("text") or "")
            if on_text:
                on_text("".join(text))
        elif "citation" in event:
            body = event["citation"] or {}
            # Newer events carry the parts directly; older ones only under 'citation'.
            cite = {k: body[k] for k in ("generatedResponsePart", "retrievedReferences") if k in body}
            citations.append(cite or dict(body.get("citation") or {}))
        elif "guardrail" in event:
            guardrail = (event["guardrail"] or {}).get("action")
    resp: dict[str, Any] = {"output": {"text": "".join(text)}}
    if citations:
        resp["citations"] = citations
    if guardrail:
        resp["guardrailAction"] = guardrail
    return resp
