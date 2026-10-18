"""
The plan opensearch.py was split by (see split.py): each module, in layer order, and the statements it holds.
"""

MODULES = {
    "prices": ["INSTANCE_TYPES", "OPENSEARCH_PRICES", "SERVERLESS_MIN_OCUS", "EMBEDDING_MODELS", "_EMBEDDING_ALIASES"],
    "helpers": ["KB, MB, GB, TB", "_int", "_CAT_SIZE_RE", "_bytes", "_epoch_ms", "parse_engine_version",
                "version_at_least", "_ARN_RE", "_AWS_HOST_RE", "parse_location", "endpoint_service", "vector_norm",
                "vector_summary", "values_at", "_format_value"],
    "models": ["Domain", "Collection", "Endpoint", "VectorField", "IndexInfo", "ClusterHealth", "KnnStats",
               "StoreReport", "VectorCheck", "Sample", "Embedding", "Hit", "SearchResult", "Overview"],
    "analysis/parsing": ["_DOMAIN_STATES", "_SPACE_NAMES", "_SIMILARITY_NAMES", "_SCORE_MEANINGS", "similarity_name",
                         "score_meaning", "_BEST", "_BEDROCK_FIELDS", "_TEXT_FIELDS", "_TEXT_TYPES", "_SOURCE_FIELDS",
                         "parse_domain", "parse_collection", "network_access", "_vector_field", "parse_mapping",
                         "unindexed_fields", "_flat_settings", "_true", "read_settings", "guess_text_field",
                         "source_field", "source_of", "index_from_cat", "parse_knn_stats"],
    "analysis/vectors": ["vector_memory", "vector_count", "index_vector_memory", "knn_memory_limit",
                         "domain_knn_memory", "score_to_similarity", "check_vectors"],
    "analysis/cost": ["domain_monthly_cost", "_key_label", "serverless_minimum", "ocu_monthly_cost"],
    "analysis/queries": ["_RANGE_OPS", "_EQUAL_OPS", "_NOT_EQUAL_OPS", "_FILTER_OPS", "_DSL_KEYS", "_exact",
                         "_filter_clause", "_check_where", "build_filter", "describe_filter", "knn_query",
                         "query_python", "describe_vector_field"],
    "analysis/findings": ["_ref", "_is_serverless", "_open_to_anyone", "domain_findings", "collection_findings",
                          "index_findings", "_vector_index_findings", "store_findings", "knn_findings",
                          "vector_findings", "search_findings"],
    "http": ["_READ_POSTS", "OpenSearchError", "_error_details", "_path", "_store_url"],
    "embeddings": ["_as_vector", "_base_model", "_embedding_style", "_embedding_request", "_embedding_from"],
    "analyzer": ["_exists", "_drop", "OpenSearchAnalyzer"],
    "style": ["_ROOT", "_CSS", "_BADGE", "_render_html"],
    "view": ["_COLLECTION_KINDS", "_NETWORK", "_FILTER_EXAMPLES", "_state_tone", "_target_arg", "_same", "_node_label",
             "_storage_label", "_shards_label", "_vector_label", "_with_vector_cell", "_method_settings",
             "_compression_label", "_hit_text", "_flat", "_metadata_columns", "_filter_rows", "OpenSearchView"],
}

DOCS = {
    "prices": "OpenSearch's price tables: each instance type's hourly price, vCPUs and memory, storage and Serverless\n"
              "prices, and the Bedrock embedding models search() can call. us-east-1 list prices; pass\n"
              "OpenSearchAnalyzer(prices={...}) for your own.",
    "helpers": "Parsing and formatting: sizes and numbers from the _cat API, engine versions, where an index is\n"
               "(parse_location), and vector arithmetic.",
    "models": "The dataclasses OpenSearchAnalyzer returns: domains, collections, indexes and their vector fields,\n"
              "health, k-NN stats, samples and search results.",
    "analysis": "Pure analysis of what OpenSearch and AWS return: no AWS calls, so it runs on answers you already have.",
    "analysis/parsing": "Reading what AWS and OpenSearch return: domains, collections, network access, mappings,\n"
                        "settings, _cat rows and k-NN stats, and the names of similarity spaces and scores.",
    "analysis/vectors": "How much memory vectors need, how many an index holds, and how healthy a sample of them is.",
    "analysis/cost": "What a domain or a Serverless collection costs a month, at the prices given.",
    "analysis/queries": "Metadata filters (where=) as OpenSearch query DSL, and the k-NN query and Python to copy.",
    "analysis/findings": "What's wrong with a domain, collection, index, its vectors or a search, and what to do about it.",
    "http": "The REST side's errors and paths: what OpenSearchAnalyzer.request() may POST to, and its error type.",
    "embeddings": "Turning a question into a vector with a Bedrock embedding model: each model family's request and\n"
                  "answer.",
    "analyzer": "OpenSearchAnalyzer: talks to AWS and to each domain's or collection's REST API, and returns the\n"
                "dataclasses in models. Never prints.",
    "style": "The look of OpenSearch reports: their CSS root class, stylesheet and badge.",
    "view": "OpenSearchView: the notebook UI, which renders what OpenSearchAnalyzer returns as cards and tables.",
}

REEXPORT_KIT = ["HOURS_PER_MONTH", "human_age", "human_money", "human_size"]
