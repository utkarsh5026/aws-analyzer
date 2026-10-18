"""
opensearch.py - Amazon OpenSearch toolkit for vector (k-NN) indexes, for SageMaker / Jupyter notebooks.

Part of aws-analyzer: ``pip install aws-analyzer``, then ``from aws_analyzer import OpenSearchView``.

Requirements: boto3 (required). pandas only for DataFrames, IPython only for rich
HTML output. All are preinstalled on SageMaker. No OpenSearch client library is
needed: requests to a domain or collection are signed with your AWS credentials
by botocore, the way boto3 signs its own.

The file has two layers:

    OpenSearchAnalyzer  Pure logic. Talks to AWS and returns plain Python data
                        (dataclasses, dicts, lists, DataFrames). Never prints.
    OpenSearchView      Notebook UI. Calls OpenSearchAnalyzer and renders readable
                        cards and tables (HTML in Jupyter, plain text in a terminal).

It covers OpenSearch Service domains, OpenSearch Serverless collections and any
OpenSearch you pass by URL. Nothing in this file writes: it only sends GET requests,
and POSTs to _search and _count (searches with a body), and refuses anything else.

Quick start
-----------
    ui = OpenSearchView()                             # or OpenSearchView(OpenSearchAnalyzer(region="eu-west-1"))
    ui.help()                                         # list every command
    ui.overview()                                     # every domain and Serverless collection: size, cost, warnings
    ui.indexes("vectors-prod")                        # its indexes: documents, size, vector fields, memory, warnings
    ui.index_info("vectors-prod/docs")                # one vector index in plain English, and the query to copy
    ui.use("vectors-prod/docs")                       # later commands use this index
    ui.sample()                                       # a few documents, and how healthy their vectors are
    ui.search("how do refunds work?")                 # nearest neighbours of a question (embedded with Bedrock)
    ui.search(like="doc-17")                          # the documents closest to doc-17 (no model needed)
    ui.search(vector=my_model.encode("refunds"), where={"lang": "en", "year": (">=", 2024)})

    aos = ui.core                                     # same analyzer, raw data
    info = aos.index("vectors-prod", "docs")          # IndexInfo: info.vectors[0].dimension, .engine, ...
    hits = aos.search("vectors-prod", "docs", vector=v, k=20).to_df()
"""

from __future__ import annotations

from .._kit.fmt import HOURS_PER_MONTH, human_age, human_money, human_size
from .prices import EMBEDDING_MODELS, INSTANCE_TYPES, OPENSEARCH_PRICES, SERVERLESS_MIN_OCUS
from .helpers import (
    GB, KB, MB, TB, endpoint_service, parse_engine_version, parse_location, values_at, vector_norm, vector_summary,
    version_at_least,
)
from .models import (
    ClusterHealth, Collection, Domain, Embedding, Endpoint, Hit, IndexInfo, KnnStats, Overview, Sample, SearchResult,
    StoreReport, VectorCheck, VectorField,
)
from .analysis.parsing import (
    guess_text_field, index_from_cat, network_access, parse_collection, parse_domain, parse_knn_stats, parse_mapping,
    read_settings, score_meaning, similarity_name, source_field, source_of, unindexed_fields,
)
from .analysis.vectors import (
    check_vectors, domain_knn_memory, index_vector_memory, knn_memory_limit, score_to_similarity, vector_count,
    vector_memory,
)
from .analysis.cost import domain_monthly_cost, ocu_monthly_cost, serverless_minimum
from .analysis.queries import build_filter, describe_filter, describe_vector_field, knn_query, query_python
from .analysis.findings import (
    collection_findings, domain_findings, index_findings, knn_findings, search_findings, store_findings,
    vector_findings,
)
from .http import OpenSearchError
from .analyzer import OpenSearchAnalyzer
from .view import OpenSearchView

__all__ = [
    "EMBEDDING_MODELS",
    "GB",
    "HOURS_PER_MONTH",
    "INSTANCE_TYPES",
    "KB",
    "MB",
    "OPENSEARCH_PRICES",
    "SERVERLESS_MIN_OCUS",
    "TB",
    "ClusterHealth",
    "Collection",
    "Domain",
    "Embedding",
    "Endpoint",
    "Hit",
    "IndexInfo",
    "KnnStats",
    "OpenSearchAnalyzer",
    "OpenSearchError",
    "OpenSearchView",
    "Overview",
    "Sample",
    "SearchResult",
    "StoreReport",
    "VectorCheck",
    "VectorField",
    "build_filter",
    "check_vectors",
    "collection_findings",
    "describe_filter",
    "describe_vector_field",
    "domain_findings",
    "domain_knn_memory",
    "domain_monthly_cost",
    "endpoint_service",
    "guess_text_field",
    "human_age",
    "human_money",
    "human_size",
    "index_findings",
    "index_from_cat",
    "index_vector_memory",
    "knn_findings",
    "knn_memory_limit",
    "knn_query",
    "network_access",
    "ocu_monthly_cost",
    "parse_collection",
    "parse_domain",
    "parse_engine_version",
    "parse_knn_stats",
    "parse_location",
    "parse_mapping",
    "query_python",
    "read_settings",
    "score_meaning",
    "score_to_similarity",
    "search_findings",
    "serverless_minimum",
    "similarity_name",
    "source_field",
    "source_of",
    "store_findings",
    "unindexed_fields",
    "values_at",
    "vector_count",
    "vector_findings",
    "vector_memory",
    "vector_norm",
    "vector_summary",
    "version_at_least",
]
