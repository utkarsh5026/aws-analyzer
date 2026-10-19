"""
bedrock_kb.py - Amazon Bedrock Knowledge Bases toolkit for SageMaker / Jupyter notebooks.

Part of aws-analyzer: ``pip install aws-analyzer``, then ``from aws_analyzer import BedrockKBView``.

Requirements: boto3 (required). pandas only for DataFrames, IPython only for rich
HTML output. All are preinstalled on SageMaker.

The file has two layers:

    BedrockKBAnalyzer  Pure logic. Talks to AWS and returns plain Python data
                       (dataclasses, dicts, lists, DataFrames). Never prints.
    BedrockKBView      Notebook UI. Calls BedrockKBAnalyzer and renders readable
                       cards and tables (HTML in Jupyter, plain text in a terminal).

Nothing in this file changes a knowledge base: it never starts a sync (it shows the
command to run instead) and never adds or removes documents.

Quick start
-----------
    ui = BedrockKBView()                              # or BedrockKBView(kb="support-docs")
    ui.help()                                         # list every command
    ui.kbs()                                          # every knowledge base: status, store, model, last sync, warnings
    ui.kb_info("support-docs")                        # settings in plain English, data sources, syncs, findings
    ui.use("support-docs")                            # later commands use this knowledge base
    ui.syncs()                                        # sync history, with why syncs failed
    ui.documents(status="FAILED")                     # documents that failed to index, and why
    ui.search("how do refunds work?")                 # ranked passages, highlighted, with source and page
    ui.chunk(2)                                       # full text and metadata of result #2
    ui.search("error E1234", where={"team": "billing", "year": (">=", 2024)}, search_type="HYBRID")
    ui.ask("How long do refunds take?")               # answer with [1][2] citations, sources, grounded %
    ui.follow_up("And for digital goods?")            # same session
    ui.ask("How long do refunds take?", data_source="faq")   # answer from one data source only (name or ID)
    ui.follow_up("And in the policies?", data_source="policies")   # move the conversation to another one
    ui.ask("...", engine="converse", model="sonnet")  # exact tokens and cost, your own prompt=
    ui.models()                                       # models you can use here, and their price
    ui.unsynced()                                     # S3 files changed since the last sync
    ui.compare("refund window for EU orders")         # SEMANTIC vs HYBRID, n=5 vs n=10
    ui.evaluate([("refund window?", "refund-policy.pdf"), ("reset password", "account-faq")])  # hit rate, MRR

    kb = ui.core                                      # same analyzer, raw data
    info = kb.describe("support-docs")                # KnowledgeBaseInfo
    r = kb.retrieve("support-docs", "refund window", n=10)    # Retrieval: r.passages, r.to_df()
    a = kb.generate("refund window?", r.passages, model="opus", prompt=MY_TEMPLATE)   # Answer: a.text, a.citations
"""

from __future__ import annotations

from .._bedrock.estimates import estimate_tokens, generation_cost, query_cost
from .._bedrock.files import (
    FILE_LIST_LINKS, SavedFileList, file_list_lines, file_list_name, parse_list_link, presigned_url, write_file_list,
)
from .._bedrock.filters import DATA_SOURCE_KEY, build_filter, data_source_filter, describe_sources, with_data_sources
from .._bedrock.foundation_models import parse_models
from .._bedrock.helpers import SEARCHABLE, parse_kb_ref, source_name, split_metadata
from .._bedrock.models import Citation, KBDocument, ModelInfo, Passage
from .._bedrock.parsing import match_expected, parse_document, parse_passage, parse_retrieve
from .._bedrock.prices import (
    BEDROCK_PRICES, DEFAULT_MODEL, DEFAULT_RERANK_MODEL, GLOBAL_MODEL_PRICES, MODEL_PRICES, model_price, short_model,
)
from .._bedrock.words import best_snippet, question_terms
from .._kit.fmt import HOURS_PER_MONTH, human_age, human_money, human_size, parse_time
from .._kit.text import search_rank
from .helpers import (
    CHUNK_LIMIT, DOCUMENT_TYPES, FILE_TEXT_LIMIT, IMAGE_TYPES, MAX_FILE_SIZE, MAX_METADATA_SIZE, METADATA_SUFFIX,
    file_type, human_duration, human_tokens, sync_call, sync_command,
)
from .models import (
    FILE_STATES, Answer, ChunkStats, DataSourceInfo, DocumentChunks, DocumentSummary, EvalCase, EvalReport,
    FileChange, FileInventory, FileProbe, IngestionJob, KBFile, KnowledgeBaseInfo, MetadataFile, Retrieval,
    SearchComparison, SyncFreshness,
)
from .analysis.prompting import DEFAULT_PROMPT, SYSTEM_PROMPT, build_prompt, parse_citation_markers
from .analysis.parsing import parse_converse, parse_data_source, parse_ingestion_job, parse_knowledge_base, parse_rag
from .analysis.settings import describe_chunking, describe_parsing, describe_vector_store, store_name
from .analysis.filters import describe_filter
from .analysis.deciding import changed_since, compare_retrievals, retrieval_metrics, summarize_documents
from .analysis.cost import idle_cost_label, vector_store_monthly_cost
from .analysis.findings import (
    answer_findings, comparison_findings, eval_findings, freshness_findings, kb_findings, retrieval_findings,
    sync_findings,
)
from .analysis.files import (
    SOURCE_KEY, file_findings, file_state, file_steps, find_files, inventory_files, inventory_findings,
    parse_file_state, pick_files, probe_findings, skip_reason, sort_files, sync_needed,
)
from .analysis.metadata import parse_metadata_file
from .analysis.chunks import chunk_overlap, chunk_stats, order_chunks, place_chunks
from .analyzer.kb_analyzer import BedrockKBAnalyzer
from .view.kb_view import BedrockKBView
from .explorer.window import KBExplorer, explore

__all__ = [
    "BEDROCK_PRICES",
    "CHUNK_LIMIT",
    "DATA_SOURCE_KEY",
    "DEFAULT_MODEL",
    "DEFAULT_PROMPT",
    "DEFAULT_RERANK_MODEL",
    "DOCUMENT_TYPES",
    "FILE_LIST_LINKS",
    "FILE_STATES",
    "FILE_TEXT_LIMIT",
    "GLOBAL_MODEL_PRICES",
    "HOURS_PER_MONTH",
    "IMAGE_TYPES",
    "MAX_FILE_SIZE",
    "MAX_METADATA_SIZE",
    "METADATA_SUFFIX",
    "MODEL_PRICES",
    "SEARCHABLE",
    "SOURCE_KEY",
    "SYSTEM_PROMPT",
    "Answer",
    "BedrockKBAnalyzer",
    "BedrockKBView",
    "ChunkStats",
    "Citation",
    "DataSourceInfo",
    "DocumentChunks",
    "DocumentSummary",
    "EvalCase",
    "EvalReport",
    "FileChange",
    "FileInventory",
    "FileProbe",
    "IngestionJob",
    "KBDocument",
    "KBExplorer",
    "KBFile",
    "KnowledgeBaseInfo",
    "MetadataFile",
    "ModelInfo",
    "Passage",
    "Retrieval",
    "SavedFileList",
    "SearchComparison",
    "SyncFreshness",
    "answer_findings",
    "best_snippet",
    "build_filter",
    "build_prompt",
    "changed_since",
    "chunk_overlap",
    "chunk_stats",
    "compare_retrievals",
    "comparison_findings",
    "data_source_filter",
    "describe_chunking",
    "describe_filter",
    "describe_parsing",
    "describe_sources",
    "describe_vector_store",
    "estimate_tokens",
    "eval_findings",
    "explore",
    "file_findings",
    "file_list_lines",
    "file_list_name",
    "file_state",
    "file_steps",
    "file_type",
    "find_files",
    "freshness_findings",
    "generation_cost",
    "human_age",
    "human_duration",
    "human_money",
    "human_size",
    "human_tokens",
    "idle_cost_label",
    "inventory_files",
    "inventory_findings",
    "kb_findings",
    "match_expected",
    "model_price",
    "order_chunks",
    "parse_citation_markers",
    "parse_converse",
    "parse_data_source",
    "parse_document",
    "parse_file_state",
    "parse_ingestion_job",
    "parse_kb_ref",
    "parse_knowledge_base",
    "parse_list_link",
    "parse_metadata_file",
    "parse_models",
    "parse_passage",
    "parse_rag",
    "parse_retrieve",
    "parse_time",
    "pick_files",
    "place_chunks",
    "presigned_url",
    "probe_findings",
    "query_cost",
    "question_terms",
    "retrieval_findings",
    "retrieval_metrics",
    "search_rank",
    "short_model",
    "skip_reason",
    "sort_files",
    "source_name",
    "split_metadata",
    "store_name",
    "summarize_documents",
    "sync_call",
    "sync_command",
    "sync_findings",
    "sync_needed",
    "vector_store_monthly_cost",
    "with_data_sources",
    "write_file_list",
]
