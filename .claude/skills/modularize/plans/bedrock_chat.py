"""
The plan bedrock_chat.py was split by: BedrockChatView's and _ChatApp's mixins (mixins.py reads MIXINS), then each
module, in layer order, and the statements it holds (split.py reads MODULES, DOCS and REEXPORT_KIT). Before
splitting, the scratch copy had BedrockChatView's class line wrapped and app()'s lazy import of _ChatApp added.
"""

MIXINS = {
    "BedrockChatView": [
        ("_ChatCommands",
         "BedrockChatView's commands to chat: app(), ask(), retrieve(), new_chat(), transcript() and last(), with the\n"
         "blocks an answer or a search is drawn with.",
         ["_turn", "_search_blocks", "_answer_blocks", "app", "ask", "retrieve", "new_chat", "transcript", "last"]),
        ("_TestCommands",
         "BedrockChatView's commands to test a list of questions: ask_all(), sweep() and results(), with the blocks a\n"
         "test run is drawn with.",
         ["_prepare", "_prepare_sweep", "_sweep_cost", "_too_costly", "_item_error", "_result_rows", "_old_items",
          "_result_row", "_batch_cost_text", "_batch_cards", "_batch_findings", "_batch_blocks", "_batch_steps",
          "_next_sweep", "ask_all", "sweep", "_keep", "results"]),
        ("_RunsCommands",
         "BedrockChatView's commands to keep and compare test runs: runs(), compare_runs(), use_run(), save_runs() and\n"
         "load_runs(), with how runs are numbered, named, saved and ranked.",
         ["_run_number", "_run", "_sweep_number", "_run_name", "_family", "_before", "_setup_now", "_now_in",
          "_saved_ids", "_append_runs", "_unsaved", "_memory_findings", "_saved_text", "_log_runs", "_money",
          "_ranked_by", "_compare_blocks", "_ranking_table", "_matrix_table", "_families", "_places", "_result_text",
          "_runs_blocks", "_setup_text", "runs", "compare_runs", "use_run", "_switch_to", "save_runs", "_save",
          "load_runs", "_load"]),
        ("_SettingsCommands",
         "BedrockChatView's commands about what's sent: settings(), set(), unset(), fields(), request() and code(),\n"
         "with the request they preview and an edited request read back.",
         ["_unsent", "_preview", "_marks", "_json_notes", "_setup_call", "_apply_request", "_model_from_arn",
          "_next_set", "_settings_rows", "_code_questions", "_about", "settings", "set", "unset", "fields",
          "request", "code"]),
        ("_KnowledgeBaseCommands",
         "BedrockChatView's commands about the knowledge base, its files and the model: use(), files(),\n"
         "save_file_list(), kbs() and models().",
         ["use", "files", "save_file_list", "kbs", "models"]),
    ],
    "_ChatApp": [
        ("_PickersPane",
         "The chat window's header: the knowledge base, data source, files and model fields, and what picking or\n"
         "typing in each does.",
         ["_kb_picker", "_kb_choice", "_source_picker", "_fill_sources", "_files_picker", "_files_face",
          "_draw_files", "_list_files", "_save_file_list", "_file_typed", "_add_file", "_remove_file", "_set_files",
          "_model_picker", "_model_choice", "_kb_picked", "_kb_typed", "_source_picked", "_model_picked",
          "_model_typed"]),
        ("_ConversationPane",
         "The chat window's conversation: the greeting, Send, the Answer / Retrieve only switch and New chat.",
         ["_hello", "_send", "_show_mode", "_mode_changed", "_new_chat", "_stream_changed"]),
        ("_SettingsTab",
         "The chat window's Settings tab: a line per setting sent, and Add a setting with its search.",
         ["_input", "_as_text", "_row", "_off", "_note_row", "_sync_rows", "_show_value", "_sync_chips", "_edited",
          "_remove", "_add_setting", "_show_adding", "_mentions", "_pick", "_show_matches", "_typed", "_browse",
          "_add_listed", "_add_typed"]),
        ("_RequestTab",
         "The chat window's Request, Response and Code tabs: the JSON sent and returned, Edit JSON, and the setup as\n"
         "code to copy.",
         ["_code_tab", "_refresh", "_render_request", "_render_response", "_request_text", "_fill_editor", "_edit",
          "_restart_edit", "_cancel_edit", "_follow_edit", "_apply", "_render_code"]),
        ("_TestTab",
         "The chat window's Test tab: the test questions, Try variations, running them in the background, and drawing\n"
         "a run or a sweep.",
         ["_test_tab", "_tests_typed", "_show_varying", "_varying", "_vary_key", "_variation_line", "_data_sources",
          "_draw_vary_chips", "_add_variation", "_plan", "_draw_test_note", "_run_tests", "_run_sweep",
          "_show_stop", "_start", "_run_later", "_drain", "_show_progress", "_show_count", "_tested",
          "_swept_item", "_stop_tests", "_tests_done", "_sweep_done", "_sweep_index", "_show_run", "_hide_sweep",
          "_draw_sweep", "_setup_picked", "_use_shown", "_draw_batch"]),
        ("_RunsTab",
         "The chat window's Runs tab: every test run, newest first, and Show, Use this setup, Compare, Save and Load.",
         ["_runs_tab", "_tab_changed", "_draw_runs", "_run_line", "_run_option", "_picked", "_show_picked",
          "_use_picked", "_compare_picked", "_save_runs", "_load_runs"]),
    ],
}

MODULES = {
    "helpers": ["DEFAULT_SETTINGS", "FILE_LIMIT", "MAX_QUESTION_CHARS", "BATCH_LIMIT", "BATCH_WORKERS", "SWEEP_LIMIT",
                "SWEEP_MAX_COST", "RUNS_FILE", "RUNS_FORMAT", "DEFAULT_PROMPT", "_plain_doc", "_sentences",
                "_words_of", "_norm", "_number", "_loads", "_short", "_diff"],
    "models": ["KnowledgeBase", "DataSource", "FileList", "Schema", "Answer", "RunScore"],
    "analysis/parsing": ["parse_rag", "collect_stream"],
    "analysis/schema": ["Field", "_KB_CONFIG", "_VECTOR", "_FILTER_PATH", "_RERANKING", "_GENERATION", "_INFERENCE",
                        "_ORCHESTRATION", "_KNOWN", "_GROUP_OF", "_GROUP_ORDER", "_NOT_SETTINGS", "_known_path",
                        "_children", "_recursive", "_kind", "_default_for", "request_schema", "_check_range"],
    "analysis/filters": ["_SYMBOLIC", "_where_from_json", "as_filter", "_FILTER_WORDS", "describe_filter",
                         "SOURCE_URI_KEY", "files_filter", "with_files", "split_condition", "split_data_sources"],
    "analysis/choices": ["file_path", "file_labels", "match_files", "describe_files", "kb_search_text", "match_kbs",
                         "rerank_arn"],
    "analysis/request": ["coerce_setting", "normalize_settings", "_put", "_get", "build_request", "_RETRIEVAL",
                         "_is_retrieve", "_retrieve_path", "retrieve_settings", "build_retrieve_request",
                         "_retrieve_as_rag", "settings_from_request", "validate_request", "describe_setting"],
    "analysis/findings": ["settings_findings", "_outside_picks", "_outside_finding", "_search_findings",
                          "answer_findings", "_same_passage", "cited_ranks", "compare_findings", "answer_cost"],
    "analysis/batches": ["BatchItem", "Batch", "_LIST_MARK_RE", "_THROTTLED", "_GRADES", "_filled", "parse_questions", "_split_case",
                         "format_questions", "question_list", "expected_at", "_unhelpful", "item_verdict",
                         "_examples", "_out_of", "_tries", "_avg_grounded", "batch_findings", "_answer_batch_findings",
                         "_search_batch_findings", "_key", "_settings_text", "batch_changes", "batch_estimate",
                         "_question_cap", "_question_text"],
    "analysis/sweeps": ["_PICKED", "_OFF_WHEN_UNSET", "_NONE_WORDS", "_sweep_name", "_alternatives", "_setup_key",
                        "sweep_setups", "apply_setup", "_split_values", "_variation_value", "parse_variations",
                        "format_variations", "run_setup", "varied_setups", "_setup_value", "setup_label"],
    "analysis/ranking": ["Sweep", "_came_back", "shared_questions", "run_score", "_quality", "rank_runs", "_index_of",
                         "_score_text", "_versus", "_cost_gap", "ranking_findings", "sweep_estimate"],
    "analysis/records": ["run_record", "_known", "run_from_record", "read_runs"],
    "analysis/code": ["_py_literal", "python_call", "_QUESTION_KEYS", "config_of", "config_json",
                      "_request_question", "python_script", "cli_command"],
    "analyzer": ["BedrockChatAnalyzer"],
    "style": ["_TABS", "_TAB_TITLES", "_tab_rules", "_ROOT", "_THEME_CSS", "_RESTYLE", "_EXTRA_CSS", "_PRE_HL", "_CSS",
              "_BADGE", "_render_html"],
    "blocks": ["_Turn", "_Code", "_ResultRow", "_Results", "_json_text_html", "_SHELL_RE", "_shell_html",
               "_code_html", "_code_block_html", "expr@4291", "_SHOWN_ELSEWHERE", "_META_VALUE_CHARS",
               "_NO_OWN_METADATA", "_meta_value", "_bedrock_keys", "_meta_text", "_passage_meta_html",
               "_sources_html", "_AVATAR", "_YOU", "_SEARCH", "_meta_html", "_score", "_search_html", "_turn_html",
               "_writing_html", "_question_html", "_row_stats", "_expected_text", "_result_html", "_waiting_html",
               "_result_lines", "_results_html", "_results_lines", "_turn_block_html", "_turn_block_lines",
               "_passage_lines", "_kind_text", "_wrap", "_waiting", "_setting_marks", "_QUICK"],
    "view/chat": ["_ChatCommands"],
    "view/tests": ["_TestCommands"],
    "view/runs": ["_RunsCommands"],
    "view/settings": ["_SettingsCommands"],
    "view/knowledge_bases": ["_KnowledgeBaseCommands"],
    "view/chat_view": ["BedrockChatView", "chat"],
    "window/widgets": ["_PICKER_ROWS", "_Choice", "_ChoicePicker"],
    "window/pickers": ["_PickersPane"],
    "window/conversation": ["_ConversationPane"],
    "window/settings": ["_SHOWN_MATCHES", "_BESIDE", "_SettingsTab"],
    "window/request": ["_PYTHON_WIDTH", "_RequestTab"],
    "window/test": ["_TestTab"],
    "window/runs": ["_RunsTab"],
    "window/app": ["_SIDE_MAX_WIDTH", "_CHAT_MARGIN", "_CHAT_ALONE_MARGIN", "_ChatApp"],
}


DOCS = {
    "helpers": "Parsing and formatting: the defaults and limits every chat starts with, botocore's documentation as\n"
               "plain text, setting names compared, JSON or Python literals read, and a setting's value (or what\n"
               "changed between two sets of them) on one short line.",
    "models": "The dataclasses BedrockChatAnalyzer returns: knowledge bases, data sources and files to pick from,\n"
              "the request's settings, an answer, and a test run's score. A Field, a Batch and a Sweep are next to\n"
              "the functions their properties call, in analysis.",
    "analysis": "Pure analysis of a chat: no AWS calls, so it runs on the requests and responses you have.",
    "analysis/parsing": "Bedrock's answers read into an Answer: a RetrieveAndGenerate response, or its stream put\n"
                        "back together.",
    "analysis/schema": "RetrieveAndGenerate's settings, from botocore's service model: each Field's path, kind,\n"
                       "range and docs, with the short names and plain-English docs the chat knows.",
    "analysis/filters": "Filters: where= as JSON or Bedrock's filter, in words, and the data sources and files a\n"
                        "request is narrowed to, put in and taken back out.",
    "analysis/choices": "What the pickers list: files by name or path, knowledge bases by search, and the reranker's\n"
                        "ARN.",
    "analysis/request": "The request: settings coerced to their field's kind, placed in RetrieveAndGenerate's (or\n"
                        "Retrieve's) request, read back from an edited one, validated, and described.",
    "analysis/findings": "Findings about the settings, an answer or a search, and what one answer cites of the search\n"
                         "behind it, with what an answer cost.",
    "analysis/batches": "Test questions and runs of them (Batch): questions read from text and checked against what\n"
                        "each should cite, and a run's findings, changes since the last run and estimate.",
    "analysis/sweeps": "Try variations: every combination of the settings, models or data sources listed, applied to a\n"
                       "setup, and each setup in words.",
    "analysis/ranking": "Runs ranked by expected sources cited, answers and grounding, a Sweep's setups among them,\n"
                        "and findings about which setup does best and why.",
    "analysis/records": "Test runs as JSON lines, and read back from them.",
    "analysis/code": "This setup as code to copy: a Python script (boto3 only), the config as JSON, or an AWS CLI\n"
                     "command.",
    "analyzer": "BedrockChatAnalyzer: talks to Bedrock and S3, and returns the dataclasses in models. Never prints,\n"
                "and never changes a knowledge base.",
    "style": "The look of Bedrock chat reports and its window: the side tabs' rules, the theme, the CSS root class,\n"
             "stylesheet and badge.",
    "blocks": "bedrock_chat's own render blocks and their drawings: a turn of the conversation, code to copy and a\n"
              "test run's results, with the HTML pieces the View and the window share.",
    "view": "BedrockChatView: the chat window and the reports of what BedrockChatAnalyzer returns.",
    "view/chat": "BedrockChatView's commands to chat.",
    "view/tests": "BedrockChatView's commands to test a list of questions.",
    "view/runs": "BedrockChatView's commands to keep and compare test runs.",
    "view/settings": "BedrockChatView's commands about what's sent.",
    "view/knowledge_bases": "BedrockChatView's commands about the knowledge base, its files and the model.",
    "view/chat_view": "BedrockChatView, made of its commands: the plumbing they share and help(), and chat() to open\n"
                      "the window.",
    "window": "_ChatApp: the chat window, made of its header, conversation and side tabs.",
    "window/widgets": "The chat window's header fields: a picker with a searchable list, and one line of it.",
    "window/pickers": "The chat window's header: the knowledge base, data source, files and model fields.",
    "window/conversation": "The chat window's conversation and its composer.",
    "window/settings": "The chat window's Settings tab.",
    "window/request": "The chat window's Request, Response and Code tabs.",
    "window/test": "The chat window's Test tab: test questions, Try variations, and runs drawn as they come back.",
    "window/runs": "The chat window's Runs tab.",
    "window/app": "_ChatApp, the chat window: its layout, what the View tells it, and its header, conversation and\n"
                  "tabs from the mixins in this package.",
}

REEXPORT_KIT = [
    "BEDROCK_PRICES", "Citation", "DATA_SOURCE_KEY", "DEFAULT_MODEL", "DEFAULT_RERANK_MODEL", "FILE_LIST_LINKS",
    "GLOBAL_MODEL_PRICES", "KBDocument", "MODEL_PRICES", "ModelInfo", "Passage", "SEARCHABLE", "SavedFileList",
    "best_snippet", "build_filter", "data_source_filter", "describe_sources", "estimate_tokens", "file_list_lines",
    "file_list_name", "generation_cost", "human_age", "human_money", "match_expected", "model_price",
    "parse_document", "parse_kb_ref", "parse_list_link", "parse_models", "parse_passage", "parse_retrieve",
    "presigned_url", "query_cost", "question_terms", "search_rank", "short_model", "source_name", "split_metadata",
    "with_data_sources", "write_file_list",
]
