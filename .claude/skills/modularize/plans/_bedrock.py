"""
The plan _bedrock.py was split by: each module, in layer order, and the statements it holds (split.py reads MODULES,
DOCS and REEXPORT_KIT). No class needed mixins.
"""

MODULES = {
    "prices": ["BEDROCK_PRICES", "MODEL_PRICES", "GLOBAL_MODEL_PRICES", "DEFAULT_MODEL", "_MODEL_ALIASES",
               "_NOT_A_VERSION", "_family_match", "model_price", "short_model", "DEFAULT_RERANK_MODEL",
               "_AMAZON_RERANK_MODEL", "_RERANK_ALIASES", "_per_million"],
    "helpers": ["_KB_ID_RE", "_KB_ARN_RE", "parse_kb_ref", "_model_id", "source_name", "SEARCHABLE",
                "_BEDROCK_META_PREFIX", "split_metadata", "_REFUSAL", "_match_kb", "_match_sources",
                "_session_expired"],
    "words": ["_STOPWORDS", "_TERM_RE", "question_terms", "_terms_regex", "best_snippet", "_highlight"],
    "models": ["KBDocument", "Passage", "Citation", "ModelInfo"],
    "parsing": ["parse_document", "_LOCATIONS", "_page_number", "parse_passage", "parse_retrieve", "_span",
                "match_expected"],
    "foundation_models": ["_GEOGRAPHY_PROFILES", "parse_models", "_is_text_model", "_served_models",
                          "_profile_preference", "_model_info", "_application_profile_model"],
    "filters": ["_FILTER_OPERATORS", "_FILTER_KEYS", "_filter_value", "_is_bedrock_filter", "_conditions",
                "_operator_conditions", "build_filter", "DATA_SOURCE_KEY", "data_source_filter",
                "with_data_sources", "describe_sources"],
    "estimates": ["estimate_tokens", "query_cost", "generation_cost"],
    "files": ["_TEXT_EXTENSIONS", "_browser_type", "FILE_LIST_LINKS", "_LINK_WORDS", "parse_list_link",
              "file_list_lines", "file_list_name", "_link_seconds", "_link_life", "_looks_like_path", "_list_target",
              "presigned_url", "SavedFileList", "_notebook_href", "_data_href", "write_file_list",
              "_LIST_LINK_BYTES"],
    "markdown": ["_MARKER_RE", "_split_marks", "_with_markers", "_MD_FENCE_RE", "_MD_HEADING_RE", "_MD_RULE_RE",
                 "_MD_ITEM_RE", "_MD_QUOTE_RE", "_MD_SETEXT_RE", "_MD_TABLE_RULE_RE", "_MD_URL_RE",
                 "_MD_AUTOLINK_RE", "_MD_PUNCT", "_MD_TAGS", "_MD_MAX_DEPTH", "_Line", "_md_indent", "_md_dedent",
                 "_md_lstrip", "_md_cells", "_md_table_start", "_md_starts_block", "_md_blocks", "_md_block",
                 "_md_code", "_md_quote", "_md_align", "_md_table", "_md_paragraph", "_md_list", "_md_source",
                 "_md_closing_bracket", "_md_link_target_after", "_md_link_target", "_Markdown", "_markdown_html",
                 "_answer_lines"],
    "blocks": ["_Answer", "_Json", "_plain_json", "_json_html", "_json_block_html", "_json_block_lines",
               "_answer_html", "_answer_block_html", "_answer_block_lines", "_Download", "_download_html",
               "_download_lines", "_download_css", "_file_list_blocks"],
}

DOCS = {
    "prices": "Bedrock's list prices: the knowledge base's own charges (OpenSearch Serverless, reranking, embedding\n"
              "questions) and each model's price per million tokens, with DEFAULT_MODEL, the rerank models, and\n"
              "model_price() / short_model() to look a model up and name it.",
    "helpers": "Parsing and matching: knowledge base IDs and ARNs, a source's file name, Bedrock's own metadata keys,\n"
               "the names of knowledge bases and data sources, and the errors that mean a session expired.",
    "words": "The question's words: what counts as one, a snippet of a passage around them, and the passage as HTML\n"
             "with them marked.",
    "models": "The dataclasses Bedrock's answers are read into: a knowledge base's document, a retrieved passage, a\n"
              "citation and a foundation model.",
    "parsing": "What Bedrock returns, read into models: documents, retrieved passages, the span of the answer a\n"
               "citation covers, and whether a passage is the one a test question expects.",
    "foundation_models": "The region's foundation models and inference profiles, read into ModelInfo: which answer\n"
                         "text, the profile to call each with, and its price.",
    "filters": "Metadata filters: build_filter() turns where= into Bedrock's filter, with_data_sources() narrows it\n"
               "to data sources, and describe_sources() names them.",
    "estimates": "Token and cost estimates: a text's tokens, what searches cost (embedding and reranking) and what an\n"
                 "answer costs at its model's price.",
    "files": "A knowledge base's files: links that open them (presigned_url), the list of them saved as a text file\n"
             "(write_file_list, SavedFileList), and the links that download that list.",
    "markdown": "An answer's markdown laid out as HTML, stdlib only, keeping each character's offset in the answer,\n"
                "since citations are offsets into the raw text; and the answer as text lines with its [n] markers.",
    "blocks": "The render blocks both Bedrock modules draw (and the agent's answers): _Answer, _Json and _Download, with\n"
              "their HTML and text drawings, and the file list's blocks.",
}

REEXPORT_KIT = ["human_size"]
