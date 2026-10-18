"""
The plan lambda_functions.py was split by: LambdaExplorer's mixins (mixins.py reads MIXINS), then each module, in layer
order, and the statements it holds (split.py reads MODULES, DOCS and REEXPORT_KIT). A name assigned twice at the top
level is named with its line (`_SECRET_NAME_RE@1669`).
"""

MIXINS = {
    "LambdaExplorer": [
        ("_FunctionsTab",
         "The Functions tab of LambdaExplorer: every function in the region, read in three steps so the list shows at\n"
         "once, searched, filtered by its chips and sorted by a click on a column.",
         ["_functions_page", "_fill_regions", "_region_changed", "_load_list", "_list_failed", "_listed",
          "_open_wanted", "_numbers_failed", "_got_numbers", "_extras_failed", "_got_extras", "_redraw_list",
          "_compute_facts", "_draw_list_note", "_found_typed", "_pick_chip", "_sort_by", "_in_chip", "_chip_button",
          "_show_chips", "_draw_chips", "_sort_key", "_refilter", "_draw_rows", "_mark_rows", "_clicked_function",
          "_clicked_logs"]),
        ("_FunctionTab",
         "The picked function in LambdaExplorer: opening it, its header (title, cards and alerts), and its Overview\n"
         "and Settings tabs.",
         ["_draw_title", "_draw_stats", "_draw_alerts", "_close_function", "_picked", "_typed", "_open_named",
          "_open_function", "_describe_failed", "_described", "_draw_overview", "_wiring", "_charts",
          "_draw_settings"]),
        ("_LogsTab",
         "The Logs tab of LambdaExplorer: the function's runs in a time range, newest first, filtered as you type, a\n"
         "CloudWatch search on Enter, older runs on request, and Live.",
         ["_logs_page", "_range_bar", "_set_range", "_range_text", "_range_changed", "_reload_logs", "_load_logs",
          "_logs_failed", "_got_logs", "_logs_line", "_words", "_log_typed", "_log_search", "_search_failed",
          "_got_search", "_show_run", "_source_runs", "_in_run_chip", "_visible_runs", "_pick_run_chip",
          "_draw_run_chips", "_draw_runs", "_no_runs_text", "_draw_run_head", "_jump", "_toggle_run", "_older",
          "_toggle_live", "_stop_live", "_poll_work", "_poll_now", "_live_loop", "_merge_live"]),
        ("_ErrorsTab",
         "The Errors tab of LambdaExplorer: the function's errors grouped by cause, and its failed runs to open.",
         ["_errors_page", "_errors_range_changed", "_load_errors", "_errors_failed", "_got_errors",
          "_clicked_failure"]),
        ("_PerformanceTab",
         "The Performance tab of LambdaExplorer: run times, memory and cold starts, and the slowest runs to open.",
         ["_performance_page", "_perf_range_changed", "_load_perf", "_perf_failed", "_got_perf", "_clicked_slow"]),
        ("_CodeTab",
         "The Code tab of LambdaExplorer: the package's files as a tree, the source of the one clicked, Copy,\n"
         "Download .zip, Hide files and How it works.",
         ["_code_page", "_load_code", "_code_failed", "_got_code", "_draw_saved", "_save_code", "_toggle_files",
          "_draw_files_toggle", "_reads_python", "_draw_how_button", "_renew_source", "_toggle_how", "_got_map",
          "_map_failed", "_code_lines", "_code_typed", "_draw_code_rows", "_clicked_code_line", "_open_code_file",
          "_draw_source"]),
    ],
}

MODULES = {
    "prices": ["DAYS_PER_MONTH", "LAMBDA_PRICES"],
    "helpers": ["KB, MB, GB, TB", "RUNTIMES", "LATEST_RUNTIMES", "ENDING_SOON_DAYS", "MAX_TIMEOUT",
                "CODE_STORAGE_LIMIT", "UNZIPPED_LIMIT", "MEMORY_PER_VCPU", "MEMORY_STEPS", "_FUNCTION_ARN_RE",
                "_PARTIAL_ARN_RE", "_CONSOLE_RE", "_CONSOLE_REGION_RE", "_NAME_RE", "parse_function_ref",
                "runtime_family", "_lambda_time", "_midnight", "_day", "human_ms", "_ms", "_mb", "_pct", "_in_days",
                "_SHORT_KINDS", "_ASYNC_KINDS", "_SERVICE_KINDS", "_STREAM_KINDS", "_INVOKE_ACTIONS"],
    "models": ["RuntimeStatus", "Layer", "Function", "Trigger", "DailyUsage", "FunctionMetrics", "LogGroup",
               "ProvisionedConcurrency", "Alias", "Version", "FunctionUrl", "AsyncConfig", "FunctionDetail",
               "AccountLimits", "LogEvent", "ErrorGroup", "ErrorReport", "Invocation", "Performance", "CodeFile",
               "CodePackage", "PackageDownload"],
    "analysis/text": ["_window", "_cli", "_region_flag", "_reason"],
    "analysis/functions": ["masked_config", "_unqualified", "parse_function", "runtime_status", "_policy_condition",
                           "_source_name", "parse_policy", "parse_event_source_mapping"],
    "analysis/cost": ["provisioned_monthly_cost", "log_storage_monthly_cost", "function_monthly_cost"],
    "analysis/overview": ["Overview"],
    "analysis/findings": ["_SECRET_NAME_RE@1669", "_POINTER_NAME_RE", "secret_like", "_next_memory", "_next_timeout",
                          "_nice_timeout", "_runtime_findings", "_day_age", "_last_seen", "RECENT_DAYS", "_recent",
                          "_errors_level", "_metrics_window", "_times", "_longer", "_usage_findings",
                          "function_findings", "account_findings"],
    "analysis/errors": ["_REQUEST_ID_RE", "_TIMESTAMP_RE", "_TIMED_OUT_RE", "_ERROR_TYPE_RE", "_LEVEL_RE",
                        "_REPORT_RE", "_REPORT_FIELD_RE", "_LIFECYCLE_RE", "_ACCESS_DENIED_RE", "_DENIED_ACTION_RE",
                        "_THROTTLED_RE", "_CONNECT_RE", "_LOAD_ERRORS", "request_id_of", "_typed", "_json_error",
                        "classify_error", "_phrase", "_filter_pattern", "_error_key", "group_errors",
                        "error_findings"],
    "analysis/logs": ["LogRun", "LogPage", "parse_report", "_PLATFORM_KINDS", "_STARTING", "_marker", "split_runs",
                      "_LEVEL_TAG_RE", "_LEVELS", "line_level"],
    "analysis/performance": ["percentile", "suggest_memory", "performance_findings"],
    "analysis/package": ["_HANDLER_EXTENSIONS", "_SOURCE_LIMIT", "DOWNLOADS", "_DOWNLOADS_MARK", "_SECRET_FILE_RE",
                         "_JUNK_RE", "handler_file", "package_file_name", "_local_path", "_top_folder", "_pick_file",
                         "read_package", "package_findings"],
    "analysis/codemap/terms": ["_MAP_DEPTH", "_MAP_STEPS", "_MAP_RUNS", "_MAP_FILES", "_MAP_BYTES",
                               "_RUNTIME_MODULES", "_LIBRARIES", "_REMOVED_MODULES", "_ADDED_MODULES",
                               "_SERVICE_NAMES", "_IAM_PREFIXES", "_IAM_ACTIONS", "_RUNS_PREFIXES", "_SENDS_PREFIXES",
                               "_READS_PREFIXES", "_BATCHES", "_RESOURCE_ACTIONS", "_COLLECTIONS", "_TRANSFERS",
                               "_GUESSES", "_HTTP_METHODS", "_HTTP_SESSIONS", "_DEFAULT_TIMEOUTS", "_LOG_LEVELS",
                               "_JSON_LOADS", "_B64_DECODES", "_COMMANDS", "_FILE_WRITERS", "_ROUTE_METHODS",
                               "_DICT_METHODS", "_CACHES", "_SECRET_NAME_RE@2896", "_NOT_SECRET_NAME_RE",
                               "_PLACEHOLDERS", "_AWS_KEY_RE", "_LAMBDA_ENV"],
    "analysis/codemap/syntax": ["_Hide", "_unparse", "_target", "_a", "_const", "_names_in", "_swallows",
                                "_is_main_guard", "_doc_line", "_def_line", "_secret_value", "_secret_spots"],
    "analysis/codemap/aws_model": ["_service_name", "_botocore_loader", "_tokens", "_aws_model", "_access",
                                   "_iam_action", "_event_label", "_event_kind", "_sample_event"],
    "analysis/codemap/model": ["CodeStep", "AwsCall", "EnvRead", "EventField", "CodeFunction", "ImportUse",
                               "CodeSpot", "CodeMap"],
    "analysis/codemap/reader": ["_Sym", "_Frame", "_Module", "_Collect", "_CodeReader"],
    "analysis/codemap/mapping": ["_python_version", "map_code", "map_package"],
    "analysis/codemap/findings": ["_PIP_NAMES", "_REPLACEMENTS", "_short_name", "_where", "_listed",
                                  "code_map_findings", "_snake", "_code_summary", "_returned", "_aws_phrase"],
    "analysis/codemap/graph": ["GraphNode", "GraphEdge", "CodeGraph", "_mermaid_id", "_mermaid_text", "_aws_node", "_GRAPH_FUNCTIONS",
                               "_GRAPH_DEPTH", "_GRAPH_LINES", "_listed_lines", "code_graph"],
    "analyzer": ["_NOT_ENABLED", "_ERROR_PATTERN", "_REPORT_PATTERN", "_SEARCH_HITS", "_SEARCH_WINDOWS", "_to_ms",
                 "_from_ms", "_log_group", "_pages", "_qualifier", "LambdaAnalyzer"],
    "style": ["_ROOT", "_EXTRA_CSS", "_CSS", "_BADGE", "_render_html"],
    "blocks/wiring": ["_Wiring", "_Columns", "_wiring_html", "_columns_html", "_wiring_lines", "_columns_lines"],
    "blocks/outline": ["_Outline", "_NOTE_TIPS", "_FOLDED_KINDS", "_note_text", "_chips_html", "_inside",
                       "_def_html", "_flow_html", "_outline_html", "_outline_lines"],
    "blocks/graph": ["_Graph", "_EDGE_WORDS", "_GRAPH_PAD_X", "_GRAPH_BAND", "_GRAPH_PAD_B", "_GRAPH_GAP",
                     "_GRAPH_ROW_GAP", "_GRAPH_MIN_W, _GRAPH_MAX_W, _GRAPH_MAX_IN, _GRAPH_MAX_HANDLER",
                     "_GRAPH_MAX_FLAGGED", "_GRAPH_LABEL_LINE", "_GRAPH_HEAD, _GRAPH_HEAD_BARE", "_GRAPH_ROW",
                     "_GRAPH_PROSE", "_GRAPH_ROUTE", "_GRAPH_SWEEPS", "_GRAPH_LISTS", "_G_SANS", "_G_LAMBDA",
                     "_G_ICONS", "_G_SERVICE_ICONS", "_G_LOOP", "_G_ACCESS", "_G_LEGEND", "_g_sans", "_g_mono",
                     "_g_wide", "_g_fit", "_g_wrap", "_g_icon", "_GCard", "_g_measure_label", "_g_need", "_g_route",
                     "_g_cards", "_g_size", "_graph_layout", "_g_names", "_g_rows", "_g_anchors", "_g_cubic",
                     "_g_spots", "_g_strand", "_g_head", "_g_edge_tip", "_edge_svg", "_g_sub_svg", "_node_svg",
                     "_g_captions", "_g_swatch", "_graph_html", "_graph_lines"],
    "view": ["_called_text", "_segments", "_event_rows", "_IMPORT_ORDER", "_import_rows", "_stamp", "_SECTIONS",
             "_PARTS", "_VIA", "_DURATION_BANDS", "_INFO_ORDER", "_unread", "_label", "_triggers_cell",
             "_runtime_meaning", "_handler_meaning", "_cpu", "_log_meaning", "_concurrency_cell", "_log_line",
             "_LINE_PREFIX_RE", "_lifecycle", "_since_arg", "LambdaView"],
    "explorer/frame": ["_EXPLORER_TABS", "_FUNCTION_TABS", "_PANEL", "_FILES_ICONS", "_FUNCTION_PAGE", "_RUN_PAGE",
                       "_CODE_PAGE", "_RUN_STATES", "_explorer_rules", "_EXPLORER_DARK", "_EXPLORER_CSS",
                       "_IN_WINDOW", "_WINDOW_WORDS", "_window_text", "_SENTENCE_START_RE", "_seconds", "_short_day",
                       "_run_clock"],
    "explorer/widgets": ["_FunctionField", "_Row", "_RunRow"],
    "explorer/functions": ["_COLUMNS", "_FUNCTION_CHIPS", "_Facts", "_function_facts", "_function_face",
                           "_triggers_text", "_FunctionsTab"],
    "explorer/function": ["_LOGO", "_SETUP_CARDS", "_DESTINATIONS", "_retries", "_destination", "_FunctionTab"],
    "explorer/logs": ["_RANGES", "_RUN_LINES", "_BODY_LINES", "_LIVE_SECONDS", "_LIVE_MINUTES", "_RUN_CHIPS",
                      "_offset", "_line_parts", "_LEVEL_PREFIX_RE", "_error_text_of", "_run_summary", "_run_tip",
                      "_run_face", "_run_body", "_LogsTab"],
    "explorer/errors": ["_ErrorsTab"],
    "explorer/performance": ["_PerformanceTab"],
    "explorer/code": ["_TOGGLE_GRACE", "_file_badge", "_TreeLine", "_folders_of", "_file_tree", "_tree_face",
                      "_copy_html", "_source_html", "_CodeTab"],
    "explorer/window": ["LambdaExplorer", "explore"],
}

DOCS = {
    "prices": "Lambda's price list: requests, GB-seconds (x86_64 and arm64), provisioned concurrency, /tmp and\n"
              "CloudWatch Logs. us-east-1 list prices; pass LambdaAnalyzer(prices={...}) for your own.",
    "helpers": "Parsing and formatting: runtimes and their end-of-support dates, Lambda's limits, function references\n"
               "(names, ARNs, console links) and the times, durations and sizes Lambda answers in.",
    "models": "The dataclasses LambdaAnalyzer returns: functions and their triggers, metrics, logs and runs, errors,\n"
              "performance, the deployment package and the map of its code.",
    "analysis": "Pure analysis of what Lambda, CloudWatch and a deployment package hold: no AWS calls, so it runs on\n"
                "answers you already have.",
    "analysis/text": "How findings say things: a time window in words, and the AWS CLI commands they show.",
    "analysis/functions": "Reading what Lambda returns: a function's configuration, its runtime's support, its resource\n"
                          "policy (who may call it) and its event source mappings.",
    "analysis/cost": "What a function costs a month: requests, compute, provisioned concurrency and its logs.",
    "analysis/findings": "What's wrong with a function or the account, why it matters and what to do about it.",
    "analysis/errors": "Errors in a function's logs: each line's request ID and cause, grouped, and what to do about each.",
    "analysis/logs": "A function's log lines as runs: its REPORT lines, START to REPORT for each call, and each line's\n"
                     "level.",
    "analysis/performance": "Run times and memory: percentiles, a memory size to suggest, and the findings about both.",
    "analysis/package": "A deployment package: its handler's file, the files in it, and what's wrong with it.",
    "analysis/codemap": "How a function's code works, read with ast from its package without running any of it: the\n"
                        "AWS calls it makes, what it reads, and the findings and graph drawn from that.",
    "analysis/codemap/terms": "What the code reader knows: how deep it reads, which modules are libraries, what\n"
                              "each AWS call and common call does, and what looks like a secret.",
    "analysis/codemap/aws_model": "AWS operations named from botocore's own service models (no AWS call), the IAM\n"
                                  "action each needs, and the event a handler takes.",
    "analysis/overview": "Overview: every function in one region or all of them, with what the account allows, and\n"
                         "what each costs and how its runtime stands.",
    "analysis/codemap/model": "The dataclasses a CodeMap is made of: the steps of the outline, the AWS calls, the\n"
                              "environment and event it reads, its functions and imports, and the lines findings\n"
                              "point at.",
    "analysis/codemap/syntax": "Small readings of Python syntax the code reader shares: names, constants, docstrings,\n"
                               "the lines of a definition, and secrets written into the code.",
    "analysis/codemap/reader": "The code reader: loads the handler's module the way Python imports it and follows the\n"
                               "handler, keeping what each name holds, into a CodeMap.",
    "analysis/codemap/mapping": "map_code() and map_package(): a CodeMap of a function's code, from its files or its\n"
                                "package's bytes.",
    "analysis/codemap/findings": "What the code map finds wrong, and the one-paragraph summary of what the code does\n"
                                 "and returns.",
    "analysis/codemap/graph": "The code map as a CodeGraph: its inputs, the handler and its functions, and what it\n"
                              "reaches, with .mermaid().",
    "analyzer": "LambdaAnalyzer: talks to Lambda, CloudWatch and CloudWatch Logs in one region or all of them, and\n"
                "returns the dataclasses in models. Never prints, and never invokes or changes a function.",
    "style": "The look of Lambda reports: their CSS root class, stylesheet (with the service's own blocks' rules) and\n"
             "badge.",
    "blocks": "The render blocks only Lambda's reports draw, each with its HTML and text drawings.",
    "blocks/wiring": "_Wiring (what calls a function and where its results go) and _Columns, with their drawings.",
    "blocks/outline": "_Outline: how_it_works()'s steps as a tree of code lines, each with tags for what it does.",
    "blocks/graph": "_Graph: a CodeGraph drawn as an SVG of cards in columns, with arrows routed between them.",
    "view": "LambdaView: the notebook UI, which renders what LambdaAnalyzer returns as cards and tables.",
    "explorer": "LambdaExplorer, the window to click through Lambda functions: its frame, widgets and one module per\n"
                "tab.",
    "explorer/frame": "What every tab of the window shares: the tabs, page sizes, run states, the stylesheet, how a\n"
                      "report's sentences are reworded for the window, and times as the lines show them.",
    "explorer/widgets": "The window's widgets: the function field with its searchable list, and the clickable rows.",
    "explorer/functions": "The Functions tab: each function's line, and the tab's mixin.",
    "explorer/function": "The picked function's header, Overview and Settings tabs.",
    "explorer/logs": "The Logs tab: each run's line and its lines of log, and the tab's mixin.",
    "explorer/errors": "The Errors tab's mixin.",
    "explorer/performance": "The Performance tab's mixin.",
    "explorer/code": "The Code tab: the package's files as a tree, the source shown, and the tab's mixin.",
    "explorer/window": "LambdaExplorer, made of its tabs' mixins, and explore().",
}

REEXPORT_KIT = ["HOURS_PER_MONTH", "human_age", "human_money", "human_size", "parse_size", "parse_time", "search_rank"]
