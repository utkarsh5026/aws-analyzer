"""
The plan bedrock_kb.py was split by: BedrockKBAnalyzer's and BedrockKBView's mixins (mixins.py reads MIXINS), then
each module, in layer order, and the statements it holds (split.py reads MODULES, DOCS and REEXPORT_KIT).
"""

MIXINS = {
    "BedrockKBAnalyzer": [
        ("_KnowledgeBaseReads",
         "BedrockKBAnalyzer's reads about knowledge bases and their data sources: the list of them, names to IDs,\n"
         "each one's settings, its syncs (with why they failed) and Bedrock's record of its documents.",
         ["_kb_summaries", "knowledge_base_names", "kb_name", "resolve", "list_knowledge_bases", "_safe_describe",
          "describe", "data_sources", "data_source_name", "data_source_names", "resolve_sources", "_pick_sources",
          "_recent_jobs", "_set_jobs", "_add_reasons", "ingestion_jobs", "documents"]),
        ("_SearchReads",
         "BedrockKBAnalyzer's searches and answers: Retrieve, RetrieveAndGenerate and Converse over the passages, the\n"
         "models you can answer with, and which ID or inference profile to call each with.",
         ["_runtime_client", "_rerank_arn", "_search_config", "retrieve", "_llm_client", "_bedrock_client",
          "models", "resolve_model", "retrieve_and_generate", "generate", "ask"]),
        ("_DecidingReads",
         "BedrockKBAnalyzer's reads that help decide what to change: links to files, S3 files changed since the last\n"
         "sync, two search setups side by side, and how well test questions find their sources.",
         ["_s3_client", "file_url", "unsynced", "compare", "evaluate"]),
        ("_FileReads",
         "BedrockKBAnalyzer's reads of files one by one: every file next to Bedrock's record of it, the list saved\n"
         "as text, one file's status, metadata file, text and chunks in document order, and whether a question\n"
         "finds it.",
         ["file_inventory", "_documents_of", "_objects_of", "save_file_list", "document_status", "metadata_file",
          "file_text", "document_chunks", "probe_file"]),
    ],
    "BedrockKBView": [
        ("_KnowledgeBaseCommands",
         "BedrockKBView's commands about knowledge bases: kbs(), use() and kb_info().",
         ["use", "kbs", "kb_info"]),
        ("_IndexCommands",
         "BedrockKBView's commands about what's indexed: files(), save_file_list(), file(), search_file(), syncs(),\n"
         "documents() and unsynced(), with the blocks the explorer window draws them with.",
         ["syncs", "documents", "unsynced", "_inventory", "_find_file", "_file_view", "_recheck", "files",
          "_list_files", "_files_blocks", "save_file_list", "_saved_list_blocks", "_sync_block", "file",
          "_file_blocks", "_metadata_blocks", "search_file", "_probe_blocks"]),
        ("_SearchCommands",
         "BedrockKBView's commands to search and answer: search(), chunk(), link(), ask(), follow_up() and models().",
         ["search", "chunk", "link", "_pick_source", "_cost_label", "_answer_blocks", "ask", "follow_up", "models"]),
        ("_MeasureCommands",
         "BedrockKBView's commands to measure retrieval: compare() and evaluate().",
         ["compare", "evaluate"]),
    ],
}

MODULES = {
    "helpers": ["human_duration", "_isnt", "_arn_region", "DOCUMENT_TYPES", "IMAGE_TYPES", "MAX_FILE_SIZE",
                "MAX_METADATA_SIZE", "CHUNK_LIMIT", "METADATA_SUFFIX", "_PLAIN_TYPES", "FILE_TEXT_LIMIT", "file_type",
                "sync_command", "sync_call", "human_tokens", "_code_terms"],
    "models": ["_EPOCH", "_RUNNING", "IngestionJob", "DataSourceInfo", "KnowledgeBaseInfo", "DocumentSummary",
               "Retrieval", "Answer", "FileChange", "SyncFreshness", "SearchComparison", "EvalCase", "EvalReport",
               "FILE_STATES", "KBFile", "FileInventory", "MetadataFile", "ChunkStats", "DocumentChunks", "FileProbe"],
    "analysis/common": ["_fmt_day", "_reasons_text", "_METADATA_EXAMPLE"],
    "analysis/prompting": ["SYSTEM_PROMPT", "DEFAULT_PROMPT", "_as_passages", "build_prompt", "_SENTENCE_END_RE",
                           "_marker_numbers", "parse_citation_markers", "_question_text"],
    "analysis/parsing": ["parse_knowledge_base", "_web_urls", "parse_data_source", "_JOB_STATISTICS",
                         "parse_ingestion_job", "parse_rag", "parse_converse", "_with_errors"],
    "analysis/settings": ["describe_chunking", "_PARSERS", "describe_parsing", "_STORE_NAMES", "_STORE_BILLED_BY",
                          "store_name", "describe_vector_store"],
    "analysis/filters": ["describe_filter", "_source_arg", "_outside"],
    "analysis/deciding": ["summarize_documents", "changed_since", "_pairs", "compare_retrievals", "_snippet_of",
                          "_setting", "retrieval_metrics", "_eval_pairs"],
    "analysis/cost": ["vector_store_monthly_cost", "idle_cost_label"],
    "analysis/findings": ["_SECTIONS", "_source_label", "kb_findings", "freshness_findings", "sync_findings",
                          "_search_label", "retrieval_findings", "answer_findings", "comparison_findings",
                          "eval_findings"],
    "analysis/files": ["SOURCE_KEY", "_ARCHIVED", "_INDEXING", "_same_uri", "skip_reason", "file_state",
                       "inventory_files", "_NEEDS_SYNC", "sync_needed", "_named", "_SECTION_READS",
                       "inventory_findings", "_failure_fix", "file_findings", "file_steps", "find_files",
                       "probe_findings", "_SORTS", "_STATE_WORDS", "parse_file_state", "sort_files", "pick_files"],
    "analysis/metadata": ["_METADATA_TYPES", "_metadata_kind", "_json_kind", "parse_metadata_file"],
    "analysis/chunks": ["_single_spaced", "chunk_overlap", "place_chunks", "_chained", "order_chunks",
                        "chunk_stats"],
    "analyzer/knowledge_bases": ["_KnowledgeBaseReads"],
    "analyzer/search": ["_SearchReads"],
    "analyzer/deciding": ["_DecidingReads"],
    "analyzer/files": ["_FileReads"],
    "analyzer/kb_analyzer": ["BedrockKBAnalyzer"],
    "style": ["_ROOT", "_EXTRA_CSS", "_CSS", "_BADGE", "_render_html"],
    "blocks": ["_Passage", "_Link", "_Steps", "_Pipeline", "_Chunks", "_Shares", "_link_html", "_steps_html",
               "_pipeline_html", "_raw_offset", "_meta_label", "_chunks_html", "_shares_html", "_STEP_MARKS",
               "_steps_lines", "_pipeline_lines", "_chunks_lines", "_shares_lines", "_passage_block_html",
               "_passage_block_lines", "_link_block_html", "_link_block_lines", "_link_cell_html"],
    "view/cells": ["_JOB_STATES", "_DOC_STATES", "_DOC_ORDER", "_KB_TYPES", "_DELETION", "_sync_label",
                   "_status_tone", "_sync_tone", "_job_row", "_passage_blocks", "_open_label", "_strip_markers",
                   "_turns", "_model_label", "_section", "_short_value"],
    "view/knowledge_bases": ["_KnowledgeBaseCommands"],
    "view/indexed": ["_IndexCommands"],
    "view/search": ["_SearchCommands"],
    "view/measure": ["_MeasureCommands"],
    "view/kb_view": ["BedrockKBView"],
    "explorer/frame": ["_EXPLORER_TABS", "_LOGO", "_TYPE_TONES", "_FILE_PAGE", "_CHIP_LABELS", "_RESULTS",
                       "_FIELD_ROLES", "_STATE_COLOURS", "_STATE_INKS", "_explorer_rules", "_EXPLORER_DARK",
                       "_EXPLORER_CSS", "_IN_WINDOW", "_WINDOW_WORDS", "_window_text", "_PARSER_NAMES",
                       "_parser_name", "_kb_tone", "_sync_step", "_file_face"],
    "explorer/widgets": ["_Chooser", "_FileRow"],
    "explorer/window": ["KBExplorer", "explore"],
}

DOCS = {
    "helpers": "Parsing and formatting: durations and token counts, ARNs, the file types and sizes Bedrock indexes,\n"
               "and the sync command or call to copy.",
    "models": "The dataclasses BedrockKBAnalyzer returns: knowledge bases, data sources and syncs, documents,\n"
              "searches and answers, comparisons and evaluations, and files one by one (their state, metadata and\n"
              "chunks).",
    "analysis": "Pure analysis of a knowledge base: no AWS calls, so it runs on the dicts AWS returns or on your own\n"
                "passages.",
    "analysis/common": "What several analysis modules share: a day in words, a sync's failure reasons as text, and the\n"
                       "metadata file example findings show.",
    "analysis/prompting": "Prompting: the prompt build_prompt() sends a model (passages as data inside <source> tags),\n"
                          "the [n] markers a model writes, and the question's text.",
    "analysis/parsing": "AWS responses read into the data models: knowledge bases, data sources, syncs, and the answers\n"
                        "RetrieveAndGenerate and Converse return.",
    "analysis/settings": "A knowledge base's settings in plain English: chunking, parsing and the vector store.",
    "analysis/filters": "where= filters in words, and how findings name the data sources a search was narrowed to.",
    "analysis/deciding": "What helps decide what to change: documents by status, files changed since a sync, two\n"
                         "searches side by side, retrieval metrics, and evaluate()'s test questions.",
    "analysis/cost": "What a knowledge base's vector store costs when idle.",
    "analysis/findings": "Findings: what's wrong with a knowledge base, its syncs, a search, an answer, a comparison or\n"
                         "an evaluation, why it matters, and what to do.",
    "analysis/files": "Files one by one: each file's state next to Bedrock's record of it, what needs a sync, how a file\n"
                      "was indexed, findings about the files and one file, and finding, sorting and picking them.",
    "analysis/metadata": "A file's .metadata.json read and checked: its attributes, their kinds, and what's wrong.",
    "analysis/chunks": "A file's chunks in document order: where each sits in the file's text, what it repeats from the\n"
                       "one before, and their sizes.",
    "analyzer": "BedrockKBAnalyzer: talks to Bedrock and S3, and returns the dataclasses in models. Never prints,\n"
                "and never changes a knowledge base.",
    "analyzer/knowledge_bases": "BedrockKBAnalyzer's reads about knowledge bases, data sources, syncs and documents.",
    "analyzer/search": "BedrockKBAnalyzer's searches and answers, and the models it answers with.",
    "analyzer/deciding": "BedrockKBAnalyzer's reads that help decide what to change: unsynced files, comparisons and\n"
                         "evaluations.",
    "analyzer/files": "BedrockKBAnalyzer's reads of files one by one: the inventory, metadata files, text, chunks and\n"
                      "probes.",
    "analyzer/kb_analyzer": "BedrockKBAnalyzer, made of its reads: the session, prices and clients, over the mixins\n"
                            "in this package.",
    "style": "The look of Bedrock knowledge base reports: their CSS root class, stylesheet and badge.",
    "blocks": "bedrock_kb's own render blocks and their drawings: passages, links, steps, the pipeline, a file's\n"
              "chunks and shares of a whole.",
    "view": "BedrockKBView: the notebook UI, which renders what BedrockKBAnalyzer returns as cards and tables.",
    "view/cells": "What the View's commands and the explorer window share: labels and tones for syncs and documents,\n"
                  "passages as blocks, and short values.",
    "view/knowledge_bases": "BedrockKBView's commands about knowledge bases.",
    "view/indexed": "BedrockKBView's commands about what's indexed: files, syncs, documents and unsynced files.",
    "view/search": "BedrockKBView's commands to search and answer.",
    "view/measure": "BedrockKBView's commands to measure retrieval.",
    "view/kb_view": "BedrockKBView, made of its commands: the plumbing they share, help(), and explore().",
    "explorer": "KBExplorer: the knowledge base explorer window, and explore() to open it.",
    "explorer/frame": "What the explorer window is drawn with: its tabs, stylesheet and colours, the sentences from\n"
                      "reports reworded for the window, and the faces of its lines.",
    "explorer/widgets": "The explorer window's widgets: the knowledge base field and a file's row.",
    "explorer/window": "KBExplorer, the knowledge base explorer window, and explore() to open it.",
}

REEXPORT_KIT = [
    "BEDROCK_PRICES", "Citation", "DATA_SOURCE_KEY", "DEFAULT_MODEL", "DEFAULT_RERANK_MODEL", "FILE_LIST_LINKS",
    "GLOBAL_MODEL_PRICES", "HOURS_PER_MONTH", "KBDocument", "MODEL_PRICES", "ModelInfo", "Passage", "SEARCHABLE",
    "SavedFileList", "best_snippet", "build_filter", "data_source_filter", "describe_sources", "estimate_tokens",
    "file_list_lines", "file_list_name", "generation_cost", "human_age", "human_money", "human_size",
    "match_expected", "model_price", "parse_document", "parse_kb_ref", "parse_list_link", "parse_models",
    "parse_passage", "parse_retrieve", "parse_time", "presigned_url", "query_cost", "question_terms", "search_rank",
    "short_model", "source_name", "split_metadata", "with_data_sources", "write_file_list",
]
