"""
AWS responses read into the data models: knowledge bases, data sources, syncs, and the answers
RetrieveAndGenerate and Converse return.
"""

from __future__ import annotations

from typing import Any, Iterable

from ..._bedrock.helpers import _model_id
from ..._bedrock.models import Citation, Passage
from ..._bedrock.parsing import _span, parse_passage

from ..models import Answer, DataSourceInfo, IngestionJob, KnowledgeBaseInfo
from .prompting import _as_passages, parse_citation_markers


# ---------------------------- parsers: AWS responses -> the data models above


def parse_knowledge_base(desc: dict[str, Any]) -> KnowledgeBaseInfo:
    """
    A GetKnowledgeBase 'knowledgeBase' dict (or a ListKnowledgeBases summary) -> KnowledgeBaseInfo.
    Data sources, syncs and tags need their own calls: see BedrockKBAnalyzer.describe.
    """
    cfg = desc.get("knowledgeBaseConfiguration") or {}
    kind = cfg.get("type", "")
    info = KnowledgeBaseInfo(
        id=desc["knowledgeBaseId"],
        name=desc.get("name", ""),
        arn=desc.get("knowledgeBaseArn", ""),
        status=desc.get("status", ""),
        kb_type=kind,
        description=desc.get("description", ""),
        role_arn=desc.get("roleArn", ""),
        created=desc.get("createdAt"),
        updated=desc.get("updatedAt"),
        failure_reasons=list(desc.get("failureReasons") or []),
        raw=desc,
    )
    embedding = (
        cfg.get("vectorKnowledgeBaseConfiguration")
        or cfg.get("managedKnowledgeBaseConfiguration")
        or {}
    )
    info.embedding_model = _model_id(embedding.get("embeddingModelArn")) or (
        "managed by Bedrock" if kind == "MANAGED" else ""
    )
    model_cfg = (embedding.get("embeddingModelConfiguration") or {}).get(
        "bedrockEmbeddingModelConfiguration"
    ) or {}
    info.embedding_dims = model_cfg.get("dimensions")
    storage = desc.get("storageConfiguration") or {}
    if storage:
        info.vector_store, info.vector_store_detail = storage.get("type", ""), storage
    elif kind == "KENDRA":
        info.vector_store = "KENDRA"
        info.vector_store_detail = {
            "type": "KENDRA",
            **(cfg.get("kendraKnowledgeBaseConfiguration") or {}),
        }
    elif kind == "SQL":
        info.vector_store = "REDSHIFT"
        info.vector_store_detail = {
            "type": "REDSHIFT",
            **(cfg.get("sqlKnowledgeBaseConfiguration") or {}),
        }
    elif kind == "MANAGED":
        info.vector_store, info.vector_store_detail = "MANAGED", {"type": "MANAGED"}
    return info


def _web_urls(cfg: dict[str, Any]) -> list[str]:
    source = cfg.get("sourceConfiguration") or {}
    return [
        seed.get("url", "")
        for seed in (source.get("urlConfiguration") or {}).get("seedUrls", [])
    ]


def parse_data_source(desc: dict[str, Any]) -> DataSourceInfo:
    """
    A GetDataSource 'dataSource' dict -> DataSourceInfo (its syncs need their own call).
    """
    cfg = desc.get("dataSourceConfiguration") or {}
    kind = cfg.get("type", "")
    ds = DataSourceInfo(
        id=desc["dataSourceId"],
        name=desc.get("name", ""),
        status=desc.get("status", ""),
        kb_id=desc.get("knowledgeBaseId", ""),
        description=desc.get("description", ""),
        source_type=kind,
        deletion_policy=desc.get("dataDeletionPolicy"),
        created=desc.get("createdAt"),
        updated=desc.get("updatedAt"),
        failure_reasons=list(desc.get("failureReasons") or []),
        raw=desc,
    )
    if kind == "S3":
        s3cfg = cfg.get("s3Configuration") or {}
        bucket = (s3cfg.get("bucketArn") or "").rsplit(":", 1)[
            -1
        ]  # arn:aws:s3:::bucket
        ds.bucket, ds.prefixes = (
            bucket or None,
            list(s3cfg.get("inclusionPrefixes") or []),
        )
        ds.location = (
            ", ".join(f"s3://{bucket}/{p}" for p in ds.prefixes) or f"s3://{bucket}/"
        )
    elif kind == "WEB":
        ds.location = ", ".join(_web_urls(cfg.get("webConfiguration") or {}))
    elif kind in ("CONFLUENCE", "SALESFORCE"):
        source = (cfg.get(f"{kind.lower()}Configuration") or {}).get(
            "sourceConfiguration"
        ) or {}
        ds.location = source.get("hostUrl", "")
    elif kind == "SHAREPOINT":
        source = (cfg.get("sharePointConfiguration") or {}).get(
            "sourceConfiguration"
        ) or {}
        ds.location = ", ".join(source.get("siteUrls") or []) or source.get(
            "domain", ""
        )
    elif kind == "CUSTOM":
        ds.location = "documents sent through the API"
    elif kind == "REDSHIFT_METADATA":
        ds.location = "Redshift table descriptions"
    ingestion = desc.get("vectorIngestionConfiguration") or {}
    ds.chunking = ingestion.get("chunkingConfiguration") or {}
    ds.parsing = ingestion.get("parsingConfiguration") or {}
    steps = [
        f"Lambda {t['transformationFunction']['transformationLambdaConfiguration']['lambdaArn'].rsplit(':', 1)[-1]}"
        " after chunking"
        for t in (ingestion.get("customTransformationConfiguration") or {}).get(
            "transformations", []
        )
    ]
    enrichment = (ingestion.get("contextEnrichmentConfiguration") or {}).get(
        "bedrockFoundationModelConfiguration"
    )
    if enrichment:
        steps.append(f"entity extraction with {_model_id(enrichment.get('modelArn'))}")
    ds.transformation = "; ".join(steps) or None
    return ds


_JOB_STATISTICS = {
    "numberOfDocumentsScanned": "scanned",
    "numberOfMetadataDocumentsScanned": "metadata_scanned",
    "numberOfNewDocumentsIndexed": "new",
    "numberOfModifiedDocumentsIndexed": "modified",
    "numberOfMetadataDocumentsModified": "metadata_modified",
    "numberOfDocumentsDeleted": "deleted",
    "numberOfDocumentsFailed": "failed",
    "numberOfDocumentsSkipped": "skipped",
}


def parse_ingestion_job(desc: dict[str, Any]) -> IngestionJob:
    """
    A GetIngestionJob 'ingestionJob' dict or a ListIngestionJobs summary -> IngestionJob. Only GetIngestionJob
    returns failure_reasons.
    """
    job = IngestionJob(
        id=desc.get("ingestionJobId", ""),
        data_source_id=desc.get("dataSourceId", ""),
        status=desc.get("status", ""),
        started=desc.get("startedAt"),
        updated=desc.get("updatedAt"),
        failure_reasons=list(desc.get("failureReasons") or []),
    )
    stats = desc.get("statistics") or {}
    for key, attribute in _JOB_STATISTICS.items():
        setattr(job, attribute, int(stats.get(key) or 0))
    return job


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
        citations.append(
            Citation(start, end, text[start:end], list(dict.fromkeys(cited)))
        )
    return Answer(
        question="",
        text=text,
        citations=citations,
        sources=sources,
        engine="kb",
        session_id=resp.get("sessionId"),
        guardrail_action=resp.get("guardrailAction"),
    )


def parse_converse(resp: dict[str, Any], sources: Iterable[Passage | str]) -> Answer:
    """
    A Converse response -> Answer with exact token counts. Only text blocks count as the answer (reasoning and
    other blocks are skipped); its [n] markers become citations of `sources`.
    """
    sources = _as_passages(sources)
    blocks = ((resp.get("output") or {}).get("message") or {}).get("content") or []
    text = "".join(
        block["text"] for block in blocks if isinstance(block.get("text"), str)
    )
    usage = resp.get("usage") or {}
    stop = resp.get("stopReason")
    return Answer(
        question="",
        text=text,
        citations=parse_citation_markers(text, len(sources)),
        sources=sources,
        engine="converse",
        input_tokens=int(usage.get("inputTokens") or 0),
        output_tokens=int(usage.get("outputTokens") or 0),
        stop_reason=stop,
        guardrail_action="INTERVENED" if stop == "guardrail_intervened" else None,
        seconds=((resp.get("metrics") or {}).get("latencyMs") or 0) / 1000,
    )


def _with_errors(ds: DataSourceInfo, errors: dict[str, str]) -> DataSourceInfo:
    ds.errors.update(errors)
    return ds
