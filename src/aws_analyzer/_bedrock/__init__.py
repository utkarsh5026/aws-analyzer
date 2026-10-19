"""
What bedrock_kb.py and bedrock_chat.py share, written once: Bedrock's list prices and model helpers, the
passages, documents and models Bedrock returns, metadata filters, token and cost estimates, the answer's markdown
with its citations, the _Answer and _Json blocks with their drawings, and files: the links that open them
(presigned_url) and their list saved as a text file (write_file_list, SavedFileList, the _Download block).

Only those two import it (rules.py checks): it's Bedrock's, not every service's, so it isn't in _kit. Each imports
the names it uses, so `bedrock_kb.MODEL_PRICES` and `bedrock_chat.Passage` still work, and the price tables are one
dict for both: change prices with BedrockKBAnalyzer(prices=..., model_prices=...) or BedrockChatAnalyzer(...), which
copy them, rather than by editing the tables.

The prices are USD at us-east-1 list prices, read from aws.amazon.com/bedrock/pricing and
aws.amazon.com/opensearch-service/pricing on 2026-09-25 and checked against the AWS Price List API on 2026-09-27.
Other regions differ: pass prices={...} (and model_prices={...}) to either analyzer to use your own.

    BEDROCK_PRICES
        opensearch_ocu_hour            per OpenSearch Compute Unit hour; indexing and search OCUs cost the same
        opensearch_min_ocus            a classic vector collection bills 1 indexing + 1 search OCU even when idle
        rerank_per_1k_queries          Cohere Rerank 3.5, the default reranker (DEFAULT_RERANK_MODEL)
        amazon_rerank_per_1k_queries   Amazon Rerank 1.0 (amazon.rerank-v1:0; us-west-2's price, it isn't offered in
                                       us-east-1)
        embedding_per_million_tokens   Amazon Titan Text Embeddings V2, to embed each question
    MODEL_PRICES
        USD per million input / output tokens, on demand (in-region and US cross-region inference profiles). Keys
        are pieces of Bedrock model IDs, and the longest matching key wins.
    GLOBAL_MODEL_PRICES
        The same through a global cross-region profile ('global.' IDs), for the models where it costs less.
        model_price() uses these for a 'global.' ID unless model_prices= changed that model's price, in which case
        your price applies to every profile.

DEFAULT_MODEL is Claude Haiku 4.5; resolve_model() finds the ID or profile to call it with. SEARCHABLE are the
document states that have chunks to search.
"""

from __future__ import annotations

from .._kit.fmt import human_size
from .prices import (
    BEDROCK_PRICES, DEFAULT_MODEL, DEFAULT_RERANK_MODEL, GLOBAL_MODEL_PRICES, MODEL_PRICES, model_price, short_model,
)
from .helpers import SEARCHABLE, parse_kb_ref, source_name, split_metadata
from .words import best_snippet, question_terms
from .models import Citation, KBDocument, ModelInfo, Passage
from .parsing import match_expected, parse_document, parse_passage, parse_retrieve
from .foundation_models import parse_models
from .filters import DATA_SOURCE_KEY, build_filter, data_source_filter, describe_sources, with_data_sources
from .estimates import estimate_tokens, generation_cost, query_cost
from .files import (
    FILE_LIST_LINKS, SavedFileList, file_list_lines, file_list_name, parse_list_link, presigned_url, write_file_list,
)

__all__ = [
    "BEDROCK_PRICES",
    "DATA_SOURCE_KEY",
    "DEFAULT_MODEL",
    "DEFAULT_RERANK_MODEL",
    "FILE_LIST_LINKS",
    "GLOBAL_MODEL_PRICES",
    "MODEL_PRICES",
    "SEARCHABLE",
    "Citation",
    "KBDocument",
    "ModelInfo",
    "Passage",
    "SavedFileList",
    "best_snippet",
    "build_filter",
    "data_source_filter",
    "describe_sources",
    "estimate_tokens",
    "file_list_lines",
    "file_list_name",
    "generation_cost",
    "human_size",
    "match_expected",
    "model_price",
    "parse_document",
    "parse_kb_ref",
    "parse_list_link",
    "parse_models",
    "parse_passage",
    "parse_retrieve",
    "presigned_url",
    "query_cost",
    "question_terms",
    "short_model",
    "source_name",
    "split_metadata",
    "with_data_sources",
    "write_file_list",
]
