"""
The plan s3.py was split by: S3Analyzer's and S3View's mixins (mixins.py reads MIXINS), then each module, in layer
order, and the statements it holds (split.py reads MODULES, DOCS and REEXPORT_KIT).
"""

MIXINS = {
    "S3Analyzer": [
        ("_BucketReads",
         "S3Analyzer's reads about buckets: the list of them, each one's settings, policy and event notifications,\n"
         "its size from CloudWatch, and the account's Block Public Access.",
         ["list_buckets", "account_public_access_block", "bucket_policy", "notifications", "versioning_status",
          "bucket_config", "bucket_metrics", "bucket_reports"]),
        ("_ListingReads",
         "S3Analyzer's listings: the objects under a prefix, one level or all of them, and what's worked out from\n"
         "them (summary, folder tree, find, duplicates, compare, a lifecycle rule's effect).",
         ["iter_objects", "list_objects", "ls", "_ls", "summarize", "folder_tree", "find", "largest", "newest",
          "oldest", "_files", "find_duplicates", "compare", "simulate_lifecycle"]),
        ("_VersionReads",
         "S3Analyzer's reads about versions: how many there are and what they cost, deleted files you can still\n"
         "restore, one key's history, and multipart uploads left unfinished.",
         ["version_stats", "deleted_files", "object_versions", "incomplete_uploads"]),
        ("_FileReads",
         "S3Analyzer's reads of one file: its headers and tags, its bytes, text, lines, JSON, tables, arrays,\n"
         "archives and documents, a link to it, and file_details() over many files.",
         ["file_details", "describe_objects", "_describe_object", "head", "object_tags", "exists", "open",
          "read_bytes", "_read_head", "read_text", "read_lines", "read_json", "read_jsonl", "read_avro", "_avro",
          "read_npy", "read_df", "_seekable", "_random_access", "parquet_info", "list_archive", "safetensors_info",
          "_open_document", "read_pdf", "render_pdf", "read_docx", "read_pptx", "read_document", "presigned_url"]),
        ("_PreviewReads",
         "S3Analyzer's preview(): what a file holds, read by its format, as a Preview the View draws.",
         ["preview", "_confirm_format", "_preview_csv", "_preview_tsv", "_preview_parquet", "_preview_orc",
          "_preview_arrow", "_preview_avro", "_preview_excel", "_preview_npy", "_preview_zip", "_preview_tar",
          "_preview_torch", "_preview_pickle", "_preview_npz", "_preview_safetensors", "_preview_notebook",
          "_preview_image", "_preview_audio", "_preview_video", "_preview_pdf", "_preview_docx", "_preview_pptx",
          "_preview_oldoffice", "_preview_as_text", "_show_json"]),
        ("_DownloadReads",
         "S3Analyzer's downloads: a file or folder, or one .zip of them, into the downloads folder, and what's\n"
         "there. clean_downloads() is the one method in aws-analyzer that deletes files, and only from a downloads\n"
         "folder it made.",
         ["download", "download_folder", "_fetch", "plan_zip", "_zip_picks", "download_zip", "_read_object",
          "_stream_into", "downloads_folder", "_make_downloads_folder", "list_downloads", "clean_downloads",
          "_download_name"]),
    ],
    "S3View": [
        ("_BucketCommands",
         "S3View's commands about buckets: buckets(), overview(), bucket_info() and policy().",
         ["_account_block", "buckets", "bucket_info", "overview", "policy"]),
        ("_FolderCommands",
         "S3View's commands to explore a folder: ls(), tree(), summary(), find(), file_details(), largest(),\n"
         "newest(), oldest() and compare().",
         ["ls", "summary", "tree", "find", "file_details", "largest", "newest", "oldest", "_top", "compare"]),
        ("_CostCommands",
         "S3View's commands to cut cost: duplicates(), what_if() and uploads().",
         ["duplicates", "uploads", "what_if"]),
        ("_VersionCommands",
         "S3View's commands about versions and deleted files: versions(), history() and deleted().",
         ["versions", "history", "deleted"]),
        ("_FileCommands",
         "S3View's commands to open a file: head(), preview(), document() and link().",
         ["head", "preview", "document", "link"]),
        ("_DownloadCommands",
         "S3View's commands to download: download(), download_zip(), downloads() and clean_downloads().",
         ["download", "_downloads_next", "download_zip", "downloads", "clean_downloads"]),
    ],
}

MODULES = {
    "helpers": ["KB, MB, GB, TB", "parse_s3_uri", "s3_uri", "base_prefix", "relative_key", "_PREVIEW_PAGES",
                "_PREVIEW_PICTURES", "_MAX_PICTURES", "_count_words", "_restore_note"],
    "prices": ["S3_PRICES", "S3_TRANSITION_PRICES", "MIN_BILLABLE_SIZE", "MIN_STORAGE_DAYS", "ARCHIVE_INDEX_BYTES",
               "MIN_TRANSITION_SIZE", "TRANSITION_ORDER", "ARCHIVE_CLASSES"],
    "models": ["ObjectInfo", "BucketInfo", "Listing", "BucketConfig", "BucketMetrics", "PrefixSummary", "FolderTree",
               "CompareResult", "DuplicateGroup", "DuplicateReport", "DuplicateFolder", "VersionStats",
               "ObjectVersion", "MultipartUpload", "DeletedObject", "DeletedFiles", "LifecycleImpact",
               "PolicyStatement", "BucketReport", "FolderDownload", "ZipDownload", "LocalDownload", "DownloadsFolder",
               "DownloadsCleanup", "ArchiveEntry", "ArchiveListing", "Picture", "PdfLine", "PdfPage", "Document",
               "Preview", "FileDetailsReport", "objects_to_df"],
    "readers/formats": ["_TEXT_EXTS", "_FORMAT_BY_EXT", "_CSV_SEPARATORS", "_zstd_reader", "_DECOMPRESSORS",
                        "_CODEC_ALIASES", "detect_format", "_browser_type", "_MAGIC_CODECS", "_MAGIC_FORMATS",
                        "_IMAGE_MIMES", "sniff_format", "_looks_binary", "_DATA_ERRORS", "_READ_ERRORS",
                        "_ReadLimit"],
    "readers/avro": ["_avro_long", "_avro_names", "_avro_read", "_avro_decompress", "_avro_header", "_avro_rows",
                     "parse_avro", "_avro_type_name"],
    "readers/office": ["_W", "_A", "_WP", "_V", "_P", "_R", "_REL", "_OFFICE_PARTS", "_OLD_OFFICE",
                       "_old_office_note", "_local", "_zip_xml", "_zip_rels", "_office_properties", "office_kind",
                       "_word_text", "_picture_mime", "_word_pictures", "parse_docx", "_drawing_text", "_slide_text",
                       "parse_pptx", "_SS", "_column_number", "_xlsx_sheets"],
    "readers/pdf": ["parse_pdf", "_BOLD_FONT", "_LIST_MARK", "_pdf_page_layout", "_encode_picture",
                    "render_pdf_pages", "_PAPER_SIZES", "_PDF_TEXT_PAGES", "_paper_size", "_meta_text", "_pdf_facts"],
    "readers/media": ["_image_info", "_audio_info", "_boxes", "_mp4_info"],
    "readers/data": ["_safetensors_length", "_parse_safetensors", "_JSON_PREFIX_RE", "_JSON_CUT_RE", "_json_prefix",
                     "_json_lines_start", "_json_shape", "_read_arrow_table", "_columnar_info", "_zip_time_of",
                     "_zip_time", "_npy_header"],
    "analysis/cost": ["object_monthly_cost", "_STORAGE_TYPE_PREFIXES", "storage_type_class", "cloudwatch_cost"],
    "analysis/summary": ["SIZE_BANDS", "AGE_BANDS", "_by_size", "folder_of", "summarize_objects",
                         "build_folder_tree", "make_filter", "compare_objects", "summary_findings"],
    "analysis/bucket": ["bucket_findings", "public_exposure", "block_label", "encryption_label", "rule_scope",
                        "rule_actions"],
    "analysis/duplicates": ["find_duplicate_groups", "DUPLICATE_METHODS", "HASH_HEAD_BYTES", "_etag_clusters",
                            "files_to_hash", "_root", "group_duplicates", "duplicate_folders", "_folders_text",
                            "_UNREADABLE_REASONS", "duplicate_findings"],
    "analysis/downloads": ["DOWNLOADS", "_DOWNLOADS_MARK", "ZipPlan", "_ZIP_SMALL_FILE", "_ALREADY_COMPRESSED",
                           "zip_checks", "_nice_size", "zip_findings", "downloads_findings"],
    "analysis/lifecycle": ["_lifecycle_steps", "simulate_lifecycle_objects"],
    "analysis/policy": ["_POLICY_ACTIONS", "_WRITE_VERBS", "_CONDITION_KEYS", "_RESTRICTING_KEYS",
                        "_CONDITION_OPERATORS", "_PRINCIPAL_ARN_RE", "_as_list", "_describe_principal",
                        "_describe_resource", "_describe_condition", "explain_policy", "policy_findings"],
    "analysis/notifications": ["EventNotification", "_NOTIFICATION_TARGETS", "_EVENT_WORDS", "_event_words",
                               "parse_notifications"],
    "analysis/documents": ["_PDF_BULLET", "_LIST_NUMBER", "_PAGE_NUMBER", "_SENTENCE_END", "_PDF_LIGATURES",
                           "_pdf_pages", "_pdf_furniture", "pdf_furniture", "pdf_flow"],
    "analysis/details": ["FileDetails", "_SAMPLE_BYTES", "_UNPACK_LIMIT", "_JSON_LIMIT", "_MAGIC_NAMED", "_ZIP_KINDS",
                         "_TEXT_KINDS", "_TABLE_KINDS", "_KIND_NAMES", "describe_file", "_describe_into",
                         "_error_text", "_identify", "_unpacked", "_stream", "_sample", "_line_count", "_put_count",
                         "_media_count", "_details_pdf", "_details_docx", "_details_pptx", "_details_excel",
                         "_details_parquet", "_details_columnar", "_details_avro", "_details_csv", "_details_jsonl",
                         "_details_json", "_details_notebook", "_details_text", "_details_image", "_details_media",
                         "_details_archive", "_details_npz", "_details_npy", "_details_safetensors",
                         "_details_torch", "_details_pickle", "_details_oldoffice", "_DETAIL_READERS", "_count_text",
                         "_short_count", "_clock", "_sheets_text", "_details_summary", "_changed_columns",
                         "file_details_findings"],
    "analyzer/threads": ["_run_in_threads", "_pool_size"],
    "analyzer/streams": ["_BodyReader", "_RangeReader", "DETAILS_BLOCK", "DETAILS_FILE_MAX", "_stopped",
                         "_ReadBudget", "_BlockReader"],
    "analyzer/buckets": ["_BucketReads"],
    "analyzer/listing": ["_ContentHasher", "_ListingReads"],
    "analyzer/versions": ["_VersionReads"],
    "analyzer/files": ["_FileReads"],
    "analyzer/preview": ["_PreviewReads"],
    "analyzer/downloads": ["_free_space", "_protected_folder", "_made_for_downloads", "_local_download",
                           "_memory_available", "_zip_layout", "_zip_name", "_check_disk_space", "_DownloadReads"],
    "analyzer/s3_analyzer": ["S3Analyzer"],
    "style": ["_ROOT", "_EXTRA_CSS", "_CSS", "_BADGE", "_render_html"],
    "blocks": ["_Frame", "_Image", "_Link", "_Media", "_Pages", "_Flow", "_data_img", "_Zoom", "_flow_html",
               "_flow_text", "_frame_html", "_frame_lines", "_image_html", "_image_lines", "_pages_html",
               "_pages_lines", "_flow_block_html", "_flow_lines", "_link_block_html", "_link_block_lines",
               "_media_html", "_media_lines"],
    "view/cells": ["_folder_label", "_FORMAT_LABELS"],
    "view/buckets": ["_policy_table", "_public_access_label", "_section", "_BucketCommands"],
    "view/folders": ["_stat_table", "_objects_table", "_file_steps", "_DETAIL_TABLES", "_details_table_of",
                     "_fact_cell", "_details_note", "_details_tables", "_details_cards", "_FolderCommands"],
    "view/cost": ["_CostCommands"],
    "view/versions": ["_VersionCommands"],
    "view/files": ["_INFO_CARDS", "_LISTING_TITLES", "_card_value", "_document_table", "_web_picture", "_flow_items",
                   "_pdf_blocks", "_pages_title", "_page_numbers", "_listing_table", "_hexdump", "_FileCommands"],
    "view/downloads": ["_PANDAS_READERS", "_DownloadCommands"],
    "view/s3_view": ["S3View"],
}

DOCS = {
    "helpers": "Parsing and formatting: s3:// paths and keys, the units sizes are counted in, and the limits on what a\n"
               "preview draws.",
    "prices": "S3's price list: storage per class, lifecycle transitions, and the minimum sizes and days S3 bills.\n"
              "us-east-1 list prices; pass S3Analyzer(prices={...}) for your own.",
    "models": "The dataclasses S3Analyzer returns: objects and listings, buckets and their settings, summaries,\n"
              "duplicates, versions, downloads, archives, documents and previews.",
    "readers": "Reading files without the packages that usually do it: formats told by name and by their first\n"
               "bytes, and stdlib-only parsers for Avro, Word, PowerPoint, Excel, PDF, pictures, audio and video.",
    "readers/formats": "What format a file is, by its name and by its first bytes, how it's compressed, and the\n"
                       "errors decoding a broken file raises.",
    "readers/avro": "Avro container files read with the standard library: the header, the schema and the rows.",
    "readers/office": "Word, PowerPoint and Excel files read with the standard library: their text, pictures,\n"
                      "properties and sheets, and what to say about the old binary formats.",
    "readers/pdf": "PDFs read with pypdf and drawn with pypdfium2: each page's lines and pictures, the pages as\n"
                   "images, and the facts file_details() shows.",
    "readers/media": "Pictures, audio and video: their size, length and codecs, from the first bytes of the file.",
    "readers/data": "Data files: safetensors headers, the start of a big JSON file, Arrow tables (parquet, ORC,\n"
                    "Feather), .npy headers and the times of zip entries.",
    "analysis": "Pure analysis of S3 objects and settings: no AWS calls, so it runs on S3 Inventory rows or any list\n"
                "of ObjectInfo.",
    "analysis/cost": "What objects cost a month to store, from their size and storage class or from CloudWatch's\n"
                     "numbers.",
    "analysis/summary": "What's under a prefix: sizes, ages and storage classes, the folder tree, filters, two\n"
                        "prefixes compared, and the findings about them.",
    "analysis/bucket": "A bucket's settings in words (public access, encryption, lifecycle rules) and what's wrong\n"
                       "with them.",
    "analysis/duplicates": "Identical files: which to hash, the groups they make, the folders that repeat, and what\n"
                           "the copies cost.",
    "analysis/downloads": "The downloads folder, and the checks a .zip download passes before it's made.",
    "analysis/lifecycle": "What a lifecycle rule would do to a prefix's objects, and what it would save or cost.",
    "analysis/policy": "A bucket policy in plain English, statement by statement, and what's risky in it.",
    "analysis/notifications": "A bucket's event notifications: what sends which events where.",
    "analysis/documents": "A PDF's text laid out to read: headings, paragraphs and lists, without the running headers\n"
                          "and footers.",
    "analysis/details": "file_details(): what each file is and holds (rows, columns, pages, sheets, duration, ...),\n"
                        "read from a few blocks of it, and what's wrong with a folder of them.",
    "analyzer": "S3Analyzer: talks to S3, CloudWatch and STS, and returns the dataclasses in models. Never prints,\n"
                "and never writes to a bucket.",
    "analyzer/threads": "Work spread over threads, with progress reported from the calling thread only.",
    "analyzer/streams": "File-like readers over an S3 object: its streamed body, ranged reads, and the blocks\n"
                        "file_details() reads within its budget.",
    "analyzer/buckets": "S3Analyzer's reads about buckets.",
    "analyzer/listing": "S3Analyzer's listings, and the hasher that tells duplicates apart.",
    "analyzer/versions": "S3Analyzer's reads about versions, deleted files and unfinished uploads.",
    "analyzer/files": "S3Analyzer's reads of one file, and file_details().",
    "analyzer/preview": "S3Analyzer's preview(), one method per format.",
    "analyzer/downloads": "S3Analyzer's downloads and the downloads folder, with the guards that keep\n"
                          "clean_downloads() to a folder it made: the one place aws-analyzer deletes files.",
    "analyzer/s3_analyzer": "S3Analyzer, made of its mixins: the session, the clients per region and the account.",
    "style": "The look of S3 reports: their CSS root class, stylesheet (with the service's own blocks' rules) and\n"
             "badge.",
    "blocks": "The render blocks only S3's reports draw (frames, pictures, links, media, PDF pages, Word documents),\n"
              "each with its HTML and text drawings.",
    "view": "S3View: the notebook UI, which renders what S3Analyzer returns as cards and tables, made of one mixin per\n"
            "group of commands in help().",
    "view/cells": "What more than one group of commands shows: a folder's label and the names of file formats.",
    "view/buckets": "The Buckets commands, and the tables and cells they share.",
    "view/folders": "The Explore a folder commands, and the tables file_details() draws.",
    "view/cost": "The Cut cost commands.",
    "view/versions": "The Versions and deleted files commands.",
    "view/files": "The Open a file commands, and how a preview's parts are drawn.",
    "view/downloads": "The Download commands.",
    "view/s3_view": "S3View, made of its commands' mixins: its look, help()'s groups and the plumbing they share.",
}

REEXPORT_KIT = ["Stat", "file_extension", "human_age", "human_money", "human_size", "parse_size", "parse_time"]
