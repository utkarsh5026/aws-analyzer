"""
The plan s3_explorer.py (by then s3/explorer/__init__.py) was split by: S3Explorer's mixins (mixins.py reads MIXINS),
then each module of s3/explorer/, in layer order, and the statements it holds (split.py reads MODULES, DOCS and
REEXPORT_KIT). Its imports were first written as if it sat in s3/ (`from .._kit`, `from .style import ...`), so
split.py puts them one level deeper.
"""

MIXINS = {
    "S3Explorer": [
        ("_ListPane",
         "The list on the left of S3Explorer: the path bar, the folder's entries (the rest listed in the\n"
         "background), the search box, its chips and filters, the sort, the pages and the status line.",
         ["_entry", "_source", "_listing", "_background", "_goal", "_follow", "_list_more", "_stop_listing",
          "_list_later", "_listed", "_shown_entries", "_ordered", "_stats", "_draw_crumbs", "_draw_header",
          "_draw_side", "_draw_filters", "_draw_rows", "_draw_pages", "_fill", "_row_classes", "_mark_rows",
          "_draw_foot", "_draw_status", "_on_row", "_click_file", "_on_filter", "_set_query", "_refilter", "_on_kind",
          "_on_deep", "_toggle_type", "_on_more_types", "_on_sort", "_on_page", "_on_load", "_on_lookup",
          "_toggle_path", "_on_path"]),
        ("_ReportPane",
         "The right pane of S3Explorer: what the folder holds, the chosen file's report (quick ones loaded in the\n"
         "background), the buttons above it, Download .zip, a PDF's pages and Expand all.",
         ["_report", "_keep", "_later", "_threads", "_load", "_wait", "_set_pane", "_show_folder", "_show_overview",
          "_folder_blocks", "_draw_actions", "_open_link", "_draw_expand", "_trees", "_mark_actions", "_on_action",
          "_zip_limit", "_zip", "_show_pages", "_read_pdf", "_read_text", "_page_count", "_per_report", "_page_span",
          "_draw_pager", "_on_expand"]),
        ("_SelectionPane",
         "Ticking files in S3Explorer: the checkboxes, the bar under the list, and the panel that downloads the\n"
         "selection as one .zip.",
         ["_build_picks", "_on_check", "_on_check_all", "_clear_picks", "_draw_check_all", "_picks_summary",
          "_draw_picks", "_picks_layout", "_picks_default", "_open_picks", "_close_picks", "_save_picks"]),
        ("_SettingsPane",
         "S3Explorer's ⚙ settings: the zip limits and the downloads folder.",
         ["_build_settings", "_toggle_settings", "_open_settings", "_close_settings", "_save_settings"]),
    ],
}

MODULES = {
    "navigator": ["_s3_module", "_SCHEMES", "_COMPRESSION", "_ARCHIVED", "_DIGITS", "_EPOCH", "_SAME_TYPE",
                  "_TYPE_WORD", "_KINDS", "_CACHED_ENTRIES", "_natural", "_extension", "_file_type", "_types_of",
                  "_type_word", "_parent_prefix", "Entry", "Folder", "Filter", "FolderStats", "parse_location",
                  "folder_uri", "parent_uri", "breadcrumbs", "entry_icon", "sort_entries", "parse_filter",
                  "filter_entries", "count_types", "folder_stats", "explain_list_error", "S3Navigator"],
    "frame": ["_ICON_PATHS", "_icon_rules", "_CSS", "_PAGE_BUTTONS", "_CHECK", "_Row"],
    "listing": ["_tip_time", "_CLICK_GRACE", "_TYPE_CHIPS", "_ListPane"],
    "report": ["_DOCUMENTS", "_TEXT_PAGES", "_BACKGROUND", "_WORKERS", "_ReportPane"],
    "selection": ["_SelectionPane"],
    "settings": ["_SettingsPane"],
    "window": ["S3Explorer"],
}

DOCS = {
    "navigator": "Where the explorer is and what's there, with no UI: s3:// paths and console links, entries,\n"
                 "folders, searches and their counts, and S3Navigator, which lists them a page at a time.",
    "frame": "What the window's panes share: its line drawings, its stylesheet, its buttons and the list's rows.",
    "listing": "The list on the left: its rows' tooltips, the clicks it drops, the type chips, and the pane's mixin.",
    "report": "The right pane: the files it draws as documents, the quick reports loaded in the background, and the\n"
              "pane's mixin.",
    "selection": "Ticked files and the panel that zips them: the pane's mixin.",
    "settings": "The ⚙ settings panel: the pane's mixin.",
    "window": "S3Explorer, made of its panes' mixins: what you drive it with from code, and its widgets.",
}

REEXPORT_KIT = []
