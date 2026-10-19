"""
A knowledge base's settings in plain English: chunking, parsing and the vector store.
"""

from __future__ import annotations

from typing import Any

from ..._bedrock.helpers import _model_id


# -------------------------------------------------- settings in plain English


def describe_chunking(cfg: dict[str, Any] | None) -> str:
    """
    chunkingConfiguration -> plain English: 'Fixed size: 300 tokens per chunk, 20% overlap'.
    """
    cfg = cfg or {}
    strategy = cfg.get("chunkingStrategy")
    if not strategy:
        return "Default: up to about 300 tokens per chunk, split at sentence ends"
    if strategy == "FIXED_SIZE":
        fixed = cfg.get("fixedSizeChunkingConfiguration") or {}
        return (
            f"Fixed size: {fixed.get('maxTokens', 0):,} tokens per chunk, "
            f"{fixed.get('overlapPercentage', 0)}% overlap"
        )
    if strategy == "HIERARCHICAL":
        levels = [
            level.get("maxTokens", 0)
            for level in (cfg.get("hierarchicalChunkingConfiguration") or {}).get(
                "levelConfigurations", []
            )
        ] + [0, 0]
        overlap = (cfg.get("hierarchicalChunkingConfiguration") or {}).get(
            "overlapTokens", 0
        )
        return (
            f"Hierarchical: {levels[0]:,}-token parents, {levels[1]:,}-token children, {overlap:,}-token overlap "
            "(search matches children, answers get the parent)"
        )
    if strategy == "SEMANTIC":
        semantic = cfg.get("semanticChunkingConfiguration") or {}
        return f"Semantic: up to {semantic.get('maxTokens', 0):,} tokens, split where the topic changes"
    if strategy == "NONE":
        return "None: each file is one chunk"
    return strategy


_PARSERS = {
    "BEDROCK_FOUNDATION_MODEL": "a foundation model reads text, tables, charts and images",
    "BEDROCK_DATA_AUTOMATION": "Bedrock Data Automation reads text, tables, figures and images (billed per page)",
    "SMART_PARSING": "smart parsing picks a parser for each file",
    "MULTI_MODAL_EMBEDDINGS": "images are embedded as images",
}


def describe_parsing(cfg: dict[str, Any] | None) -> str:
    """
    parsingConfiguration -> plain English: 'Default: the text only (...)'.
    """
    cfg = cfg or {}
    strategy: str | None = cfg.get("parsingStrategy")
    if not strategy:
        return "Default: the text only (images and charts inside files are skipped)"
    text = _PARSERS.get(strategy, strategy)
    text = text[0].upper() + text[1:]
    model = _model_id(
        (cfg.get("bedrockFoundationModelConfiguration") or {}).get("modelArn")
    )
    if strategy == "BEDROCK_FOUNDATION_MODEL" and model:
        text = text.replace("A foundation model", model)
    if (cfg.get("bedrockFoundationModelConfiguration") or {}).get("parsingPrompt"):
        text += ", with a custom parsing prompt"
    return text


_STORE_NAMES = {
    "OPENSEARCH_SERVERLESS": "OpenSearch Serverless",
    "OPENSEARCH_MANAGED_CLUSTER": "OpenSearch Service",
    "PINECONE": "Pinecone",
    "REDIS_ENTERPRISE_CLOUD": "Redis Enterprise Cloud",
    "RDS": "Aurora PostgreSQL",
    "MONGO_DB_ATLAS": "MongoDB Atlas",
    "NEPTUNE_ANALYTICS": "Neptune Analytics",
    "S3_VECTORS": "S3 Vectors",
    "KENDRA": "Kendra",
    "REDSHIFT": "Redshift (SQL)",
    "MANAGED": "managed by Bedrock",
}


_STORE_BILLED_BY = {
    "PINECONE": "Pinecone",
    "REDIS_ENTERPRISE_CLOUD": "Redis",
    "MONGO_DB_ATLAS": "MongoDB Atlas",
    "RDS": "Aurora",
    "OPENSEARCH_MANAGED_CLUSTER": "OpenSearch Service",
    "S3_VECTORS": "S3 Vectors",
    "NEPTUNE_ANALYTICS": "Neptune Analytics",
    "KENDRA": "Kendra",
    "REDSHIFT": "Redshift",
    "MANAGED": "Bedrock",
}


def store_name(kind: str) -> str:
    """
    'OPENSEARCH_SERVERLESS' -> 'OpenSearch Serverless'.
    """
    return _STORE_NAMES.get(kind, kind.replace("_", " ").title() if kind else "-")


def describe_vector_store(cfg: dict[str, Any] | None) -> str:
    """
    storageConfiguration -> plain English: 'OpenSearch Serverless collection abc123, index kb-index'.
    """
    cfg = cfg or {}
    kind = cfg.get("type", "")
    name = store_name(kind)
    if kind == "OPENSEARCH_SERVERLESS":
        c = cfg.get("opensearchServerlessConfiguration") or {}
        return f"{name} collection {c.get('collectionArn', '').rsplit('/', 1)[-1]}, index {c.get('vectorIndexName')}"
    if kind == "OPENSEARCH_MANAGED_CLUSTER":
        c = cfg.get("opensearchManagedClusterConfiguration") or {}
        return f"{name} domain {c.get('domainArn', '').rsplit('/', 1)[-1]}, index {c.get('vectorIndexName')}"
    if kind == "PINECONE":
        c = cfg.get("pineconeConfiguration") or {}
        namespace = f", namespace {c['namespace']}" if c.get("namespace") else ""
        return f"{name} index {c.get('connectionString', '').split('//')[-1].split('.')[0]}{namespace}"
    if kind == "REDIS_ENTERPRISE_CLOUD":
        c = cfg.get("redisEnterpriseCloudConfiguration") or {}
        return f"{name} at {c.get('endpoint')}, index {c.get('vectorIndexName')}"
    if kind == "RDS":
        c = cfg.get("rdsConfiguration") or {}
        return (
            f"{name} cluster {c.get('resourceArn', '').rsplit(':', 1)[-1]}, table "
            f"{c.get('databaseName')}.{c.get('tableName')} (pgvector)"
        )
    if kind == "MONGO_DB_ATLAS":
        c = cfg.get("mongoDbAtlasConfiguration") or {}
        return f"{name} collection {c.get('databaseName')}.{c.get('collectionName')}, index {c.get('vectorIndexName')}"
    if kind == "NEPTUNE_ANALYTICS":
        c = cfg.get("neptuneAnalyticsConfiguration") or {}
        return f"{name} graph {c.get('graphArn', '').rsplit('/', 1)[-1]} (GraphRAG)"
    if kind == "S3_VECTORS":
        c = cfg.get("s3VectorsConfiguration") or {}
        index = c.get("indexName") or (c.get("indexArn") or "").rsplit("/", 1)[-1]
        return f"{name} index {index} in bucket {(c.get('vectorBucketArn') or '').rsplit('/', 1)[-1] or '?'}"
    if kind == "KENDRA":
        return f"{name} index {(cfg.get('kendraIndexArn') or '').rsplit('/', 1)[-1]}"
    if kind == "REDSHIFT":
        engine = (
            (cfg.get("redshiftConfiguration") or {}).get("queryEngineConfiguration")
            or {}
        ).get("type", "")
        return f"{name}: questions become SQL queries" + (
            f" on {engine.lower()} Redshift" if engine else ""
        )
    return name
