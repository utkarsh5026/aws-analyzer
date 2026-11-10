/* The report screenshots' short clips, for tour.js. Written by .claude/skills/demo/tour_video.py --data from shots.py's figures and the guides' captions: change those, not this. */
window.AA_CLIPS = {
 "overview": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "ui.overview(): ui.overview(): sizes and costs cover all versions, from CloudWatch's daily storage metrics.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 10.1,
    "chapter": "Amazon S3",
    "label": "Amazon S3",
    "code": "ui.overview()",
    "outputs": [
     {
      "img": "overview",
      "w": 1476,
      "h": 1370
     }
    ],
    "captions": [
     [
      2.7,
      "Sizes and costs cover all versions, from CloudWatch's daily storage metrics."
     ]
    ]
   }
  ]
 },
 "bucket-info": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "ui.bucket_info(\"acme-ml-data\"): ui.bucket_info(\"acme-ml-data\")",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 15.1,
    "chapter": "Amazon S3",
    "label": "Amazon S3",
    "code": "ui.bucket_info(\"acme-ml-data\")",
    "outputs": [
     {
      "img": "bucket-info",
      "w": 1476,
      "h": 2432
     }
    ],
    "captions": [
     [
      3.14,
      "<code>ui.bucket_info(\"acme-ml-data\")</code>"
     ]
    ]
   }
  ]
 },
 "policy": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "ui.policy(\"acme-ml-data\"): ui.policy(\"acme-ml-data\")",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 8.9,
    "chapter": "Amazon S3",
    "label": "Amazon S3",
    "code": "ui.policy(\"acme-ml-data\")",
    "outputs": [
     {
      "img": "policy",
      "w": 1476,
      "h": 1153
     }
    ],
    "captions": [
     [
      2.98,
      "<code>ui.policy(\"acme-ml-data\")</code>"
     ]
    ]
   }
  ]
 },
 "tree": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "ui.tree(\"s3://acme-ml-data/curated/\", depth=3): ui.tree(\"s3://acme-ml-data/curated/\", depth=3)",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 11.0,
    "chapter": "Amazon S3",
    "label": "Amazon S3",
    "code": "ui.tree(\"s3://acme-ml-data/curated/\", depth=3)",
    "outputs": [
     {
      "img": "tree",
      "w": 1476,
      "h": 1371
     }
    ],
    "captions": [
     [
      3.64,
      "<code>ui.tree(\"s3://acme-ml-data/curated/\", depth=3)</code>"
     ]
    ]
   }
  ]
 },
 "find": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "ui.find(\"s3://acme-ml-data/training/\", pattern=\"*.tar.gz\", min_size=\"4GB\", modified_after=\"90d\"): Model artifacts over 4 GB from the last 90 days.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 8.2,
    "chapter": "Amazon S3",
    "label": "Amazon S3",
    "code": "ui.find(\"s3://acme-ml-data/training/\", pattern=\"*.tar.gz\", min_size=\"4GB\", modified_after=\"90d\")",
    "outputs": [
     {
      "img": "find",
      "w": 1476,
      "h": 789
     }
    ],
    "captions": [
     [
      4.6,
      "Model artifacts over 4 GB from the last 90 days."
     ]
    ]
   }
  ]
 },
 "summary": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "ui.summary(\"s3://acme-ml-data/\"): The top of ui.summary(\"s3://acme-ml-data/\"). Further down: size and age histograms and the largest objects.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 14.5,
    "chapter": "Amazon S3",
    "label": "Amazon S3",
    "code": "ui.summary(\"s3://acme-ml-data/\")",
    "outputs": [
     {
      "img": "summary",
      "w": 1476,
      "h": 1950
     }
    ],
    "captions": [
     [
      3.2,
      "The top of <code>ui.summary(\"s3://acme-ml-data/\")</code>. Further down: size and age histograms and the largest objects."
     ]
    ]
   }
  ]
 },
 "preview-parquet": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "ui.preview(\"s3://acme-ml-data/curated/features/churn/train.parquet\", n=8): A Parquet file: only the footer and the first row group are downloaded.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 11.6,
    "chapter": "Amazon S3",
    "label": "Amazon S3",
    "code": "ui.preview(\"s3://acme-ml-data/curated/features/churn/train.parquet\", n=8)",
    "outputs": [
     {
      "img": "preview-parquet",
      "w": 1476,
      "h": 1322
     }
    ],
    "captions": [
     [
      4.48,
      "A Parquet file: only the footer and the first row group are downloaded."
     ]
    ]
   }
  ]
 },
 "preview-model": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "ui.preview(\"s3://acme-ml-data/training/churn-xgb-2025-09-01-1030/output/model.tar.gz\"): A SageMaker model.tar.gz, listed without extracting it.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 8.2,
    "chapter": "Amazon S3",
    "label": "Amazon S3",
    "code": "ui.preview(\"s3://acme-ml-data/training/churn-xgb-2025-09-01-1030/output/model.tar.gz\")",
    "outputs": [
     {
      "img": "preview-model",
      "w": 1476,
      "h": 706
     }
    ],
    "captions": [
     [
      4.6,
      "A SageMaker <code>model.tar.gz</code>, listed without extracting it."
     ]
    ]
   }
  ]
 },
 "preview-safetensors": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "ui.preview(\"s3://acme-ml-data/models/tiny-llm/model.safetensors\"): A .safetensors model: only its header is read, so this is instant even for a multi-GB file.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 13.2,
    "chapter": "Amazon S3",
    "label": "Amazon S3",
    "code": "ui.preview(\"s3://acme-ml-data/models/tiny-llm/model.safetensors\")",
    "outputs": [
     {
      "img": "preview-safetensors",
      "w": 1476,
      "h": 1597
     }
    ],
    "captions": [
     [
      4.23,
      "A <code>.safetensors</code> model: only its header is read, so this is instant even for a multi-GB file."
     ]
    ]
   }
  ]
 },
 "preview-json": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "ui.preview(\"s3://acme-ml-data/models/tiny-llm/config.json\"): A model's config.json as a tree: each object and array folds under a click, and each line's tooltip is its path in Python.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 12.1,
    "chapter": "Amazon S3",
    "label": "Amazon S3",
    "code": "ui.preview(\"s3://acme-ml-data/models/tiny-llm/config.json\")",
    "outputs": [
     {
      "img": "preview-json",
      "w": 1476,
      "h": 1460
     }
    ],
    "captions": [
     [
      4.04,
      "A model's <code>config.json</code> as a tree: each object and array folds under a click, and each line's tooltip is its path in Python."
     ]
    ]
   }
  ]
 },
 "preview-docx": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "ui.preview(\"s3://acme-ml-data/docs/model-card-churn-xgb.docx\"): A Word model card, laid out as it reads: headings, bullets, the table and the chart where the author put them.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 15.1,
    "chapter": "Amazon S3",
    "label": "Amazon S3",
    "code": "ui.preview(\"s3://acme-ml-data/docs/model-card-churn-xgb.docx\")",
    "outputs": [
     {
      "img": "preview-docx",
      "w": 1476,
      "h": 1894
     }
    ],
    "captions": [
     [
      4.14,
      "A Word model card, laid out as it reads: headings, bullets, the table and the chart where the author put them."
     ]
    ]
   }
  ]
 },
 "preview-pptx": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "ui.preview(\"s3://acme-ml-data/docs/q3-ml-platform-review.pptx\"): A PowerPoint deck, in the order it's presented.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 7.8,
    "chapter": "Amazon S3",
    "label": "Amazon S3",
    "code": "ui.preview(\"s3://acme-ml-data/docs/q3-ml-platform-review.pptx\")",
    "outputs": [
     {
      "img": "preview-pptx",
      "w": 1476,
      "h": 743
     }
    ],
    "captions": [
     [
      4.17,
      "A PowerPoint deck, in the order it's presented."
     ]
    ]
   }
  ]
 },
 "document-pdf": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "ui.document(\"s3://acme-ml-data/docs/data-retention-policy.pdf\"): ui.document(...) on a two-page PDF: its text laid out to read, the lines of each paragraph joined back up, without the header and page numbers on every page.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 10.2,
    "chapter": "Amazon S3",
    "label": "Amazon S3",
    "code": "ui.document(\"s3://acme-ml-data/docs/data-retention-policy.pdf\")",
    "outputs": [
     {
      "img": "document-pdf",
      "w": 1476,
      "h": 1171
     }
    ],
    "captions": [
     [
      4.17,
      "Its text laid out to read, the lines of each paragraph joined back up, without the header and page numbers on every page."
     ]
    ]
   }
  ]
 },
 "preview-scan": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "ui.preview(\"s3://acme-ml-data/docs/invoices/vendor-invoice-0471.pdf\"): A scanned invoice has no text to show, so the preview draws its pages. ui.document(...) shows them full width.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 10.6,
    "chapter": "Amazon S3",
    "label": "Amazon S3",
    "code": "ui.preview(\"s3://acme-ml-data/docs/invoices/vendor-invoice-0471.pdf\")",
    "outputs": [
     {
      "img": "preview-scan",
      "w": 1476,
      "h": 1201
     }
    ],
    "captions": [
     [
      4.36,
      "A scanned invoice has no text to show, so the preview draws its pages. <code>ui.document(...)</code> shows them full width."
     ]
    ]
   }
  ]
 },
 "what-if": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "ui.what_if(\"s3://acme-ml-data/training/\", move_after={30: \"STANDARD_IA\", 180: \"GLACIER\"}): Moving training outputs to STANDARD_IA after 30 days and to GLACIER after 180.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 13.3,
    "chapter": "Amazon S3",
    "label": "Amazon S3",
    "code": "ui.what_if(\"s3://acme-ml-data/training/\", move_after={30: \"STANDARD_IA\", 180: \"GLACIER\"})",
    "outputs": [
     {
      "img": "what-if",
      "w": 1476,
      "h": 1564
     }
    ],
    "captions": [
     [
      4.6,
      "Moving training outputs to STANDARD_IA after 30 days and to GLACIER after 180."
     ]
    ]
   }
  ]
 },
 "duplicates": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "ui.duplicates(\"s3://acme-ml-data/public-samples/\"): ui.duplicates(...): groups of identical files and the space their copies take.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 15.7,
    "chapter": "Amazon S3",
    "label": "Amazon S3",
    "code": "ui.duplicates(\"s3://acme-ml-data/public-samples/\")",
    "outputs": [
     {
      "img": "duplicates",
      "w": 1476,
      "h": 2235
     }
    ],
    "captions": [
     [
      3.76,
      "Groups of identical files and the space their copies take."
     ]
    ]
   }
  ]
 },
 "deleted": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "ui.deleted(\"s3://acme-ml-data/\"): ui.deleted(\"s3://acme-ml-data/\")",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 7.6,
    "chapter": "Amazon S3",
    "label": "Amazon S3",
    "code": "ui.deleted(\"s3://acme-ml-data/\")",
    "outputs": [
     {
      "img": "deleted",
      "w": 1476,
      "h": 922
     }
    ],
    "captions": [
     [
      3.2,
      "<code>ui.deleted(\"s3://acme-ml-data/\")</code>"
     ]
    ]
   }
  ]
 },
 "dynamodb-tables": {
  "notebook": "acme-app.ipynb",
  "summary": "ui.tables(): ui.tables(): item counts and sizes are DynamoDB's own estimates, which it refreshes about every 6 hours.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.9,
    "chapter": "Amazon DynamoDB",
    "label": "Amazon DynamoDB",
    "code": "ui.tables()",
    "outputs": [
     {
      "img": "dynamodb-tables",
      "w": 1476,
      "h": 1346
     }
    ],
    "captions": [
     [
      2.7,
      "Item counts and sizes are DynamoDB's own estimates, which it refreshes about every 6 hours."
     ]
    ]
   }
  ]
 },
 "dynamodb-table-info": {
  "notebook": "acme-app.ipynb",
  "summary": "ui.table_info(\"acme-app\"): ui.table_info(\"acme-app\"): for an on-demand table the monthly cost includes reads and writes at the last 24 hours' rate.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 12.7,
    "chapter": "Amazon DynamoDB",
    "label": "Amazon DynamoDB",
    "code": "ui.table_info(\"acme-app\")",
    "outputs": [
     {
      "img": "dynamodb-table-info",
      "w": 1476,
      "h": 1710
     }
    ],
    "captions": [
     [
      2.98,
      "For an on-demand table the monthly cost includes reads and writes at the last 24 hours' rate."
     ]
    ]
   }
  ]
 },
 "dynamodb-query": {
  "notebook": "acme-app.ipynb",
  "summary": "ui.query(\"acme-app\", \"CUSTOMER#1042\", sort=(\"begins_with\", \"ORDER#\"), n=8, attributes=[\"status\", \"created_at\", \"total\", \"currency\", \"coupon\"]): ui.query(\"acme-app\", \"CUSTOMER#1042\", sort=(\"begins_with\", \"ORDER#\"), n=8, attributes=[\"status\", \"created_at\", \"total\", \"currency\", \"coupon\"])",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 8.6,
    "chapter": "Amazon DynamoDB",
    "label": "Amazon DynamoDB",
    "code": "ui.query(\"acme-app\", \"CUSTOMER#1042\", sort=(\"begins_with\", \"ORDER#\"), n=8, attributes=[\"status\", \"created_at\", \"total\", \"currency\", \"coupon\"])",
    "outputs": [
     {
      "img": "dynamodb-query",
      "w": 1476,
      "h": 841
     }
    ],
    "captions": [
     [
      4.6,
      "<code>ui.query(\"acme-app\", \"CUSTOMER#1042\", sort=(\"begins_with\", \"ORDER#\"), n=8, attributes=[\"status\", \"created_at\", \"total\", \"currency\", \"coupon\"])</code>"
     ]
    ]
   }
  ]
 },
 "dynamodb-get": {
  "notebook": "acme-app.ipynb",
  "summary": "ui.get(\"acme-app\", \"CUSTOMER#1042\", \"ORDER#2026-08-14#7731\"): ui.get(\"acme-app\", \"CUSTOMER#1042\", \"ORDER#2026-08-14#7731\")",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 12.7,
    "chapter": "Amazon DynamoDB",
    "label": "Amazon DynamoDB",
    "code": "ui.get(\"acme-app\", \"CUSTOMER#1042\", \"ORDER#2026-08-14#7731\")",
    "outputs": [
     {
      "img": "dynamodb-get",
      "w": 1476,
      "h": 1548
     }
    ],
    "captions": [
     [
      4.08,
      "<code>ui.get(\"acme-app\", \"CUSTOMER#1042\", \"ORDER#2026-08-14#7731\")</code>"
     ]
    ]
   }
  ]
 },
 "dynamodb-scan-filter": {
  "notebook": "acme-app.ipynb",
  "summary": "ui.scan(\"acme-app\", 10, where={\"status\": \"failed\", \"total\": (\">\", 300)}, attributes=[\"status\", \"created_at\", \"total\", \"failure_reason\"]): A filter runs after DynamoDB reads the items, so you pay for everything read, not just what matches.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 10.2,
    "chapter": "Amazon DynamoDB",
    "label": "Amazon DynamoDB",
    "code": "ui.scan(\"acme-app\", 10, where={\"status\": \"failed\", \"total\": (\">\", 300)}, attributes=[\"status\", \"created_at\", \"total\", \"failure_reason\"])",
    "outputs": [
     {
      "img": "dynamodb-scan-filter",
      "w": 1476,
      "h": 1075
     }
    ],
    "captions": [
     [
      4.6,
      "A filter runs after DynamoDB reads the items, so you pay for everything read, not just what matches."
     ]
    ]
   }
  ]
 },
 "dynamodb-schema": {
  "notebook": "acme-app.ipynb",
  "summary": "ui.schema(\"acme-app\"): ui.schema(\"acme-app\"): an old import stored some US zip codes as numbers, so a filter on address.zip silently misses them.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 14.8,
    "chapter": "Amazon DynamoDB",
    "label": "Amazon DynamoDB",
    "code": "ui.schema(\"acme-app\")",
    "outputs": [
     {
      "img": "dynamodb-schema",
      "w": 1476,
      "h": 4174
     }
    ],
    "captions": [
     [
      2.86,
      "An old import stored some US zip codes as numbers, so a filter on <code>address.zip</code> silently misses them."
     ]
    ]
   }
  ]
 },
 "dynamodb-value-counts": {
  "notebook": "acme-app.ipynb",
  "summary": "ui.value_counts(\"acme-app\", \"status\"): ui.value_counts(\"acme-app\", \"status\"): “(not set)” is customers and tickets, which have no status.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 7.2,
    "chapter": "Amazon DynamoDB",
    "label": "Amazon DynamoDB",
    "code": "ui.value_counts(\"acme-app\", \"status\")",
    "outputs": [
     {
      "img": "dynamodb-value-counts",
      "w": 1476,
      "h": 844
     }
    ],
    "captions": [
     [
      3.36,
      "“(not set)” is customers and tickets, which have no status."
     ]
    ]
   }
  ]
 },
 "bedrock-kbs": {
  "notebook": "support-docs.ipynb",
  "summary": "ui.kbs(): ui.kbs(): every knowledge base here has something to look at. The idle cost is estimated for OpenSearch Serverless only; the other vector stores show “-”.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 11.7,
    "chapter": "Bedrock Knowledge Bases",
    "label": "Bedrock Knowledge Bases",
    "code": "ui.kbs()",
    "outputs": [
     {
      "img": "bedrock-kbs",
      "w": 1476,
      "h": 1605
     }
    ],
    "captions": [
     [
      2.7,
      "Every knowledge base here has something to look at. The idle cost is estimated for OpenSearch Serverless only; the other vector stores show “-”."
     ]
    ]
   }
  ]
 },
 "bedrock-kb-info": {
  "notebook": "support-docs.ipynb",
  "summary": "ui.kb_info(\"support-docs\"): ui.kb_info(\"support-docs\"): the findings come first, each with what to do next. Below them, each data source's chunking, parsing and deletion policy in plain English.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 15.0,
    "chapter": "Bedrock Knowledge Bases",
    "label": "Bedrock Knowledge Bases",
    "code": "ui.kb_info(\"support-docs\")",
    "outputs": [
     {
      "img": "bedrock-kb-info",
      "w": 1476,
      "h": 2320
     }
    ],
    "captions": [
     [
      3.01,
      "The findings come first, each with what to do next. Below them, each data source's chunking, parsing and deletion policy in plain English."
     ]
    ]
   }
  ]
 },
 "bedrock-syncs": {
  "notebook": "support-docs.ipynb",
  "summary": "ui.syncs(\"support-docs\"): ui.syncs(\"support-docs\"): why a sync failed, in the same row, and the command that starts the next one. You run it; the tool never does.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.6,
    "chapter": "Bedrock Knowledge Bases",
    "label": "Bedrock Knowledge Bases",
    "code": "ui.syncs(\"support-docs\")",
    "outputs": [
     {
      "img": "bedrock-syncs",
      "w": 1476,
      "h": 1254
     }
    ],
    "captions": [
     [
      2.95,
      "Why a sync failed, in the same row, and the command that starts the next one. You run it; the tool never does."
     ]
    ]
   }
  ]
 },
 "bedrock-documents": {
  "notebook": "support-docs.ipynb",
  "summary": "ui.documents(\"support-docs\"): ui.documents(\"support-docs\"): without status= it lists only the documents that aren't indexed, with Bedrock's reason for each.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 7.8,
    "chapter": "Bedrock Knowledge Bases",
    "label": "Bedrock Knowledge Bases",
    "code": "ui.documents(\"support-docs\")",
    "outputs": [
     {
      "img": "bedrock-documents",
      "w": 1476,
      "h": 976
     }
    ],
    "captions": [
     [
      3.08,
      "Without <code>status=</code> it lists only the documents that aren't indexed, with Bedrock's reason for each."
     ]
    ]
   }
  ]
 },
 "bedrock-unsynced": {
  "notebook": "support-docs.ipynb",
  "summary": "ui.unsynced(\"support-docs\"): ui.unsynced(\"support-docs\"): two files changed after the last sync, so searches and answers don't see those changes yet.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 7.7,
    "chapter": "Bedrock Knowledge Bases",
    "label": "Bedrock Knowledge Bases",
    "code": "ui.unsynced(\"support-docs\")",
    "outputs": [
     {
      "img": "bedrock-unsynced",
      "w": 1476,
      "h": 970
     }
    ],
    "captions": [
     [
      3.04,
      "Two files changed after the last sync, so searches and answers don't see those changes yet."
     ]
    ]
   }
  ]
 },
 "bedrock-search": {
  "notebook": "support-docs.ipynb",
  "summary": "ui.use(\"support-docs\")\nui.search(\"How long do refunds take?\"): ui.search(\"How long do refunds take?\"): two passages from the refund policy, then three weaker ones. The words from the question are highlighted.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 11.0,
    "chapter": "Bedrock Knowledge Bases",
    "label": "Bedrock Knowledge Bases",
    "code": "ui.use(\"support-docs\")\nui.search(\"How long do refunds take?\")",
    "outputs": [
     {
      "img": "bedrock-search",
      "w": 1476,
      "h": 1257
     }
    ],
    "captions": [
     [
      4.11,
      "Two passages from the refund policy, then three weaker ones. The words from the question are highlighted."
     ]
    ]
   }
  ]
 },
 "bedrock-ask": {
  "notebook": "support-docs.ipynb",
  "summary": "ui.use(\"support-docs\")\nui.ask(\"How long do refunds take?\"): ui.ask(\"How long do refunds take?\"): cited spans are shaded and link to their source. The last sentence cites nothing, so the answer is 77% grounded. Tokens are an estimate here, because RetrieveAndGenerate doesn't report them.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 8.5,
    "chapter": "Bedrock Knowledge Bases",
    "label": "Bedrock Knowledge Bases",
    "code": "ui.use(\"support-docs\")\nui.ask(\"How long do refunds take?\")",
    "outputs": [
     {
      "img": "bedrock-ask",
      "w": 1476,
      "h": 908
     }
    ],
    "captions": [
     [
      4.01,
      "Cited spans are shaded and link to their source. The last sentence cites nothing, so the answer is 77% grounded."
     ],
     [
      7.61,
      "Tokens are an estimate here, because RetrieveAndGenerate doesn't report them."
     ]
    ]
   }
  ]
 },
 "bedrock-ask-converse": {
  "notebook": "support-docs.ipynb",
  "summary": "ui.use(\"support-docs\")\nui.ask(\"How long do refunds take?\", engine=\"converse\", model=\"sonnet\"): ui.ask(\"How long do refunds take?\", engine=\"converse\", model=\"sonnet\"): exact token counts, and the Cited column shows which of the passages the model used.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.2,
    "chapter": "Bedrock Knowledge Bases",
    "label": "Bedrock Knowledge Bases",
    "code": "ui.use(\"support-docs\")\nui.ask(\"How long do refunds take?\", engine=\"converse\", model=\"sonnet\")",
    "outputs": [
     {
      "img": "bedrock-ask-converse",
      "w": 1476,
      "h": 925
     }
    ],
    "captions": [
     [
      4.6,
      "Exact token counts, and the Cited column shows which of the passages the model used."
     ]
    ]
   }
  ]
 },
 "bedrock-models": {
  "notebook": "support-docs.ipynb",
  "summary": "ui.models(): ui.models(): the ID to pass as model=, how each model is called, and what it costs per million tokens.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 7.3,
    "chapter": "Bedrock Knowledge Bases",
    "label": "Bedrock Knowledge Bases",
    "code": "ui.models()",
    "outputs": [
     {
      "img": "bedrock-models",
      "w": 1476,
      "h": 953
     }
    ],
    "captions": [
     [
      2.7,
      "The ID to pass as <code>model=</code>, how each model is called, and what it costs per million tokens."
     ]
    ]
   }
  ]
 },
 "bedrock-compare": {
  "notebook": "support-docs.ipynb",
  "summary": "ui.use(\"support-docs\")\nui.compare(\"what does error E1234 mean?\", n=(2, 5)): ui.compare(\"what does error E1234 mean?\", n=(2, 5)): both search types find the same passages, but only HYBRID puts the one with the exact error code first.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 11.6,
    "chapter": "Bedrock Knowledge Bases",
    "label": "Bedrock Knowledge Bases",
    "code": "ui.use(\"support-docs\")\nui.compare(\"what does error E1234 mean?\", n=(2, 5))",
    "outputs": [
     {
      "img": "bedrock-compare",
      "w": 1476,
      "h": 1283
     }
    ],
    "captions": [
     [
      4.51,
      "Both search types find the same passages, but only HYBRID puts the one with the exact error code first."
     ]
    ]
   }
  ]
 },
 "bedrock-evaluate": {
  "notebook": "support-docs.ipynb",
  "summary": "ui.use(\"support-docs\"); cases = [\n    (\"How long do refunds take?\", \"refund-policy.pdf\"),\n    (\"How do I reset my password?\", \"account-faq\"),\n    (\"Can EU customers return an order?\", \"eu-returns.pdf\"),\n    (\"What does error E1234 mean?\", \"payment-errors\"),\n    (\"Can I return a faulty laptop?\", \"warranty.pdf\"),\n    (\"When do holiday orders ship?\", \"holiday-shipping\"),\n]\nui.evaluate(cases): ui.evaluate(cases): the miss is holiday-shipping.md, which was added after the last sync. The unsynced() screenshot above shows it.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 13.0,
    "chapter": "Bedrock Knowledge Bases",
    "label": "Bedrock Knowledge Bases",
    "code": "ui.use(\"support-docs\"); cases = [\n    (\"How long do refunds take?\", \"refund-policy.pdf\"),\n    (\"How do I reset my password?\", \"account-faq\"),\n    (\"Can EU customers return an order?\", \"eu-returns.pdf\"),\n    (\"What does error E1234 mean?\", \"payment-errors\"),\n    (\"Can I return a faulty laptop?\", \"warranty.pdf\"),\n    (\"When do holiday orders ship?\", \"holiday-shipping\"),\n]\nui.evaluate(cases)",
    "outputs": [
     {
      "img": "bedrock-evaluate",
      "w": 1476,
      "h": 1232
     }
    ],
    "captions": [
     [
      4.6,
      "The miss is <code>holiday-shipping.md</code>, which was added after the last sync. The <code>unsynced()</code> screenshot above shows it."
     ]
    ]
   }
  ]
 },
 "sagemaker-instance": {
  "notebook": "churn-analysis.ipynb",
  "summary": "ui.instance(): ui.instance() in the churn-analysis space: the GPU has done nothing for this session, and nothing will stop the app tonight.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 14.7,
    "chapter": "Amazon SageMaker",
    "label": "Amazon SageMaker",
    "code": "ui.instance()",
    "outputs": [
     {
      "img": "sagemaker-instance",
      "w": 1476,
      "h": 3004
     }
    ],
    "captions": [
     [
      2.7,
      "The GPU has done nothing for this session, and nothing will stop the app tonight."
     ]
    ]
   }
  ]
 },
 "sagemaker-disk": {
  "notebook": "churn-analysis.ipynb",
  "summary": "ui.disk(): ui.disk(): 3.8 GB of it is a file deleted in JupyterLab weeks ago, still sitting in the trash.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 14.7,
    "chapter": "Amazon SageMaker",
    "label": "Amazon SageMaker",
    "code": "ui.disk()",
    "outputs": [
     {
      "img": "sagemaker-disk",
      "w": 1476,
      "h": 3108
     }
    ],
    "captions": [
     [
      2.7,
      "3.8 GB of it is a file deleted in JupyterLab weeks ago, still sitting in the trash."
     ]
    ]
   }
  ]
 },
 "sagemaker-running": {
  "notebook": "churn-analysis.ipynb",
  "summary": "ui.running(): ui.running(): a forgotten notebook, a forgotten app and an unused endpoint add up to about $590 a month.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 14.7,
    "chapter": "Amazon SageMaker",
    "label": "Amazon SageMaker",
    "code": "ui.running()",
    "outputs": [
     {
      "img": "sagemaker-running",
      "w": 1476,
      "h": 2078
     }
    ],
    "captions": [
     [
      2.7,
      "A forgotten notebook, a forgotten app and an unused endpoint add up to about $590 a month."
     ]
    ]
   }
  ]
 },
 "sagemaker-training-job": {
  "notebook": "churn-analysis.ipynb",
  "summary": "ui.training_job(\"churn-xgb-0517\"): ui.training_job(\"churn-xgb-0517\"): ExecuteUserScriptError becomes \"add lightgbm to requirements.txt\".",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 12.1,
    "chapter": "Amazon SageMaker",
    "label": "Amazon SageMaker",
    "code": "ui.training_job(\"churn-xgb-0517\")",
    "outputs": [
     {
      "img": "sagemaker-training-job",
      "w": 1476,
      "h": 1584
     }
    ],
    "captions": [
     [
      3.23,
      "<code>ExecuteUserScriptError</code> becomes \"add lightgbm to requirements.txt\"."
     ]
    ]
   }
  ]
 },
 "sagemaker-endpoints": {
  "notebook": "churn-analysis.ipynb",
  "summary": "ui.endpoints(): ui.endpoints(): churn-v1 hasn't been called in a month and costs $83.95 a month.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 6.4,
    "chapter": "Amazon SageMaker",
    "label": "Amazon SageMaker",
    "code": "ui.endpoints()",
    "outputs": [
     {
      "img": "sagemaker-endpoints",
      "w": 1476,
      "h": 821
     }
    ],
    "captions": [
     [
      2.7,
      "Churn-v1 hasn't been called in a month and costs $83.95 a month."
     ]
    ]
   }
  ]
 },
 "sagemaker-pipeline-run": {
  "notebook": "churn-analysis.ipynb",
  "summary": "ui.pipeline_run(\"churn-pipeline\"): ui.pipeline_run(\"churn-pipeline\"): the training step broke, and the four steps after it never ran.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 15.2,
    "chapter": "Amazon SageMaker",
    "label": "Amazon SageMaker",
    "code": "ui.pipeline_run(\"churn-pipeline\")",
    "outputs": [
     {
      "img": "sagemaker-pipeline-run",
      "w": 1476,
      "h": 2239
     }
    ],
    "captions": [
     [
      3.23,
      "The training step broke, and the four steps after it never ran."
     ]
    ]
   }
  ]
 },
 "opensearch-overview": {
  "notebook": "vectors.ipynb",
  "summary": "ui.overview(): ui.overview(): what each domain runs on and costs, how much memory its nodes have for vector graphs, and what Serverless bills even when idle.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 12.2,
    "chapter": "Amazon OpenSearch",
    "label": "Amazon OpenSearch",
    "code": "ui.overview()",
    "outputs": [
     {
      "img": "opensearch-overview",
      "w": 1476,
      "h": 1680
     }
    ],
    "captions": [
     [
      2.7,
      "What each domain runs on and costs, how much memory its nodes have for vector graphs, and what Serverless bills even when idle."
     ]
    ]
   }
  ]
 },
 "opensearch-indexes": {
  "notebook": "vectors.ipynb",
  "summary": "ui.indexes(\"vectors-prod\"): ui.indexes(\"vectors-prod\"): the graphs need more memory than the nodes have, so searches load them from disk; one index uses a deprecated engine, another has documents vector search can't find.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 12.0,
    "chapter": "Amazon OpenSearch",
    "label": "Amazon OpenSearch",
    "code": "ui.indexes(\"vectors-prod\")",
    "outputs": [
     {
      "img": "opensearch-indexes",
      "w": 1476,
      "h": 1612
     }
    ],
    "captions": [
     [
      3.01,
      "The graphs need more memory than the nodes have, so searches load them from disk; one index uses a deprecated engine, another has documents vector search can't find."
     ]
    ]
   }
  ]
 },
 "opensearch-index-info": {
  "notebook": "vectors.ipynb",
  "summary": "ui.index_info(\"vectors-prod/support-docs\"): ui.index_info(\"vectors-prod/support-docs\"): 32-bit vectors that need 15.7 GB with replicas, and what on-disk mode would need instead.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 14.3,
    "chapter": "Amazon OpenSearch",
    "label": "Amazon OpenSearch",
    "code": "ui.index_info(\"vectors-prod/support-docs\")",
    "outputs": [
     {
      "img": "opensearch-index-info",
      "w": 1476,
      "h": 1875
     }
    ],
    "captions": [
     [
      3.51,
      "32-bit vectors that need 15.7 GB with replicas, and what on-disk mode would need instead."
     ]
    ]
   }
  ]
 },
 "opensearch-sample": {
  "notebook": "vectors.ipynb",
  "summary": "ui.sample(\"vectors-prod/support-docs\", n=5): ui.sample(\"vectors-prod/support-docs\"): unit-length vectors, a few repeated passages, and where each document came from.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 14.0,
    "chapter": "Amazon OpenSearch",
    "label": "Amazon OpenSearch",
    "code": "ui.sample(\"vectors-prod/support-docs\", n=5)",
    "outputs": [
     {
      "img": "opensearch-sample",
      "w": 1476,
      "h": 1826
     }
    ],
    "captions": [
     [
      3.54,
      "Unit-length vectors, a few repeated passages, and where each document came from."
     ]
    ]
   }
  ]
 },
 "opensearch-search": {
  "notebook": "vectors.ipynb",
  "summary": "ui.use(\"vectors-prod/support-docs\")\nui.search(\"how do I get my money back?\", k=8): ui.search(\"how do I get my money back?\"): refund articles at cosine 0.74 to 0.75, the question embedded with Titan Text Embeddings V2 for less than a cent.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 16.6,
    "chapter": "Amazon OpenSearch",
    "label": "Amazon OpenSearch",
    "code": "ui.use(\"vectors-prod/support-docs\")\nui.search(\"how do I get my money back?\", k=8)",
    "outputs": [
     {
      "img": "opensearch-search",
      "w": 1476,
      "h": 2165
     }
    ],
    "captions": [
     [
      4.6,
      "Refund articles at cosine 0.74 to 0.75, the question embedded with Titan Text Embeddings V2 for less than a cent."
     ]
    ]
   }
  ]
 },
 "lambda-functions": {
  "notebook": "lambda.ipynb",
  "summary": "ui.functions(regions=\"all\"): ui.functions(regions=\"all\"): two runtimes AWS no longer patches, $70 a month of provisioned concurrency for a function that never runs two at once, and 3% of calls failing in the last three days, against 0.6% over the month.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 15.0,
    "chapter": "AWS Lambda",
    "label": "AWS Lambda",
    "code": "ui.functions(regions=\"all\")",
    "outputs": [
     {
      "img": "lambda-functions",
      "w": 1476,
      "h": 2353
     }
    ],
    "captions": [
     [
      3.04,
      "Two runtimes AWS no longer patches, $70 a month of provisioned concurrency for a function that never runs two at once, and 3% of calls failing in the last three days, against 0.6% over the month."
     ]
    ]
   }
  ]
 },
 "lambda-function-info": {
  "notebook": "lambda.ipynb",
  "summary": "ui.function_info(\"orders-etl\"): ui.function_info(\"orders-etl\"): its logs cost six times its compute, and failed S3 events are dropped because nothing catches them.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 15.1,
    "chapter": "AWS Lambda",
    "label": "AWS Lambda",
    "code": "ui.function_info(\"orders-etl\")",
    "outputs": [
     {
      "img": "lambda-function-info",
      "w": 1476,
      "h": 2100
     }
    ],
    "captions": [
     [
      3.14,
      "Its logs cost six times its compute, and failed S3 events are dropped because nothing catches them."
     ]
    ]
   }
  ]
 },
 "lambda-errors": {
  "notebook": "lambda.ipynb",
  "summary": "ui.errors(\"orders-etl\"): ui.errors(\"orders-etl\"): three different reasons to fail, each with what to do and the run to look at.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 14.9,
    "chapter": "AWS Lambda",
    "label": "AWS Lambda",
    "code": "ui.errors(\"orders-etl\")",
    "outputs": [
     {
      "img": "lambda-errors",
      "w": 1476,
      "h": 2213
     }
    ],
    "captions": [
     [
      2.92,
      "Three different reasons to fail, each with what to do and the run to look at."
     ]
    ]
   }
  ]
 },
 "lambda-performance": {
  "notebook": "lambda.ipynb",
  "summary": "ui.performance(\"orders-etl\"): ui.performance(\"orders-etl\"): runs use at most 182 MB of the 1,024 MB it has, and three ran into the 60 s timeout.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 15.0,
    "chapter": "AWS Lambda",
    "label": "AWS Lambda",
    "code": "ui.performance(\"orders-etl\")",
    "outputs": [
     {
      "img": "lambda-performance",
      "w": 1476,
      "h": 2247
     }
    ],
    "captions": [
     [
      3.08,
      "Runs use at most 182 MB of the 1,024 MB it has, and three ran into the 60 s timeout."
     ]
    ]
   }
  ]
 },
 "lambda-code": {
  "notebook": "lambda.ipynb",
  "summary": "ui.code(\"orders-etl\"): ui.code(\"orders-etl\"): a .env file anyone allowed lambda:GetFunction can read, and 13 MB of boto3 the runtime already has.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 12.7,
    "chapter": "AWS Lambda",
    "label": "AWS Lambda",
    "code": "ui.code(\"orders-etl\")",
    "outputs": [
     {
      "img": "lambda-code",
      "w": 1476,
      "h": 1725
     }
    ],
    "captions": [
     [
      2.86,
      "A .env file anyone allowed <code>lambda:GetFunction</code> can read, and 13 MB of boto3 the runtime already has."
     ]
    ]
   }
  ]
 },
 "lambda-diff": {
  "notebook": "lambda.ipynb",
  "summary": "ui.diff(\"orders-etl\"): ui.diff(\"orders-etl\"): the deploy that added a DynamoDB copy of each order, the put_item line behind the KeyError: 'customer_id' in its logs, and the permission it needs.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 12.8,
    "chapter": "AWS Lambda",
    "label": "AWS Lambda",
    "code": "ui.diff(\"orders-etl\")",
    "outputs": [
     {
      "img": "lambda-diff",
      "w": 1476,
      "h": 1746
     }
    ],
    "captions": [
     [
      2.86,
      "The deploy that added a DynamoDB copy of each order, the <code>put_item</code> line behind the <code>KeyError: 'customer_id'</code> in its logs, and the permission it needs."
     ]
    ]
   }
  ]
 },
 "lambda-how-it-works": {
  "notebook": "lambda.ipynb",
  "summary": "ui._show(next(b for b in ui._how_blocks(core.code_map(\"support-agent-actions\")) if type(b).__name__ == \"_Tabs\").tabs[0][1][:1]): The graph in ui.how_it_works(\"support-agent-actions\"): open_ticket() reads TEAM_TOPIC, which isn't set, and tracking() calls the shipping API with no timeout.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 8.2,
    "chapter": "AWS Lambda",
    "label": "AWS Lambda",
    "code": "ui._show(next(b for b in ui._how_blocks(core.code_map(\"support-agent-actions\")) if type(b).__name__ == \"_Tabs\").tabs[0][1][:1])",
    "outputs": [
     {
      "img": "lambda-how-it-works",
      "w": 1476,
      "h": 661
     }
    ],
    "captions": [
     [
      4.6,
      "The graph in <code>ui.how_it_works(\"support-agent-actions\")</code>: <code>open_ticket()</code> reads <code>TEAM_TOPIC</code>, which isn't set, and <code>tracking()</code> calls the shipping API with no timeout."
     ]
    ]
   }
  ]
 },
 "apigw-apis": {
  "notebook": "apis.ipynb",
  "summary": "apigw.apis(): apigw.apis(): orders-api fails most and runs close to the time limit, partner-api has two routes anyone can call, and legacy-v1 had no calls.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 12.2,
    "chapter": "Amazon API Gateway",
    "label": "Amazon API Gateway",
    "code": "apigw.apis()",
    "outputs": [
     {
      "img": "apigw-apis",
      "w": 1476,
      "h": 1683
     }
    ],
    "captions": [
     [
      2.7,
      "Orders-api fails most and runs close to the time limit, partner-api has two routes anyone can call, and legacy-v1 had no calls."
     ]
    ]
   }
  ]
 },
 "apigw-api-info": {
  "notebook": "apis.ipynb",
  "summary": "apigw.api_info(\"orders-api\"): apigw.api_info(\"orders-api\"): POST /orders runs out of time, while orders-etl may still be working.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 13.1,
    "chapter": "Amazon API Gateway",
    "label": "Amazon API Gateway",
    "code": "apigw.api_info(\"orders-api\")",
    "outputs": [
     {
      "img": "apigw-api-info",
      "w": 1476,
      "h": 1756
     }
    ],
    "captions": [
     [
      3.08,
      "<code>POST /orders</code> runs out of time, while <code>orders-etl</code> may still be working."
     ]
    ]
   }
  ]
 },
 "msg-overview": {
  "notebook": "messaging.ipynb",
  "summary": "msg.overview(): msg.overview(): what failed first, then every queue, topic and rule, with the commands that fix what's wrong.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 14.7,
    "chapter": "SQS, SNS and EventBridge",
    "label": "SQS, SNS and EventBridge",
    "code": "msg.overview()",
    "outputs": [
     {
      "img": "msg-overview",
      "w": 1476,
      "h": 3952
     }
    ],
    "captions": [
     [
      2.7,
      "<b><code>msg.overview()</code></b>: what failed first, then every queue, topic and rule, with the commands that fix what's wrong."
     ]
    ]
   }
  ]
 },
 "msg-follow": {
  "notebook": "messaging.ipynb",
  "summary": "msg.follow(\"orders-events\"): msg.follow(\"orders-events\"): two of the five places it delivers to keep it out.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 12.5,
    "chapter": "SQS, SNS and EventBridge",
    "label": "SQS, SNS and EventBridge",
    "code": "msg.follow(\"orders-events\")",
    "outputs": [
     {
      "img": "msg-follow",
      "w": 1476,
      "h": 1674
     }
    ],
    "captions": [
     [
      3.04,
      "Two of the five places it delivers to keep it out."
     ]
    ]
   }
  ]
 },
 "msg-queue-info": {
  "notebook": "messaging.ipynb",
  "summary": "msg.queue_info(\"orders\"): msg.queue_info(\"orders\"): its dead-letter queue holds 42 failures, and its visibility timeout is short for its reader.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 14.9,
    "chapter": "SQS, SNS and EventBridge",
    "label": "SQS, SNS and EventBridge",
    "code": "msg.queue_info(\"orders\")",
    "outputs": [
     {
      "img": "msg-queue-info",
      "w": 1476,
      "h": 2426
     }
    ],
    "captions": [
     [
      2.95,
      "Its dead-letter queue holds 42 failures, and its visibility timeout is short for its reader."
     ]
    ]
   }
  ]
 },
 "lambda-how-it-works-steps": {
  "notebook": "lambda.ipynb",
  "summary": "ui._show(next(b for b in ui._how_blocks(core.code_map(\"support-agent-actions\")) if type(b).__name__ == \"_Tabs\").tabs[1][1]): The Step by step tab: it starts with what runs when Lambda loads the code, then follows the handler line by line, into each function it calls.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.3,
    "chapter": "AWS Lambda",
    "label": "AWS Lambda",
    "code": "ui._show(next(b for b in ui._how_blocks(core.code_map(\"support-agent-actions\")) if type(b).__name__ == \"_Tabs\").tabs[1][1])",
    "outputs": [
     {
      "img": "lambda-how-it-works-steps",
      "w": 1476,
      "h": 930
     }
    ],
    "captions": [
     [
      4.6,
      "The <b>Step by step</b> tab: it starts with what runs when Lambda loads the code, then follows the handler line by line, into each function it calls."
     ]
    ]
   }
  ]
 },
 "sfn-overview": {
  "notebook": "workflows.ipynb",
  "summary": "sfn.overview(): sfn.overview(): order-pipeline's 4.0% failure rate is the one warning, and the table puts the failing state machines first.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.0,
    "chapter": "AWS Step Functions",
    "label": "AWS Step Functions",
    "code": "sfn.overview()",
    "outputs": [
     {
      "img": "sfn-overview",
      "w": 1476,
      "h": 1210
     }
    ],
    "captions": [
     [
      2.7,
      "Order-pipeline's 4.0% failure rate is the one warning, and the table puts the failing state machines first."
     ]
    ]
   }
  ]
 },
 "sfn-failures": {
  "notebook": "workflows.ipynb",
  "summary": "sfn.failures(\"order-pipeline\"): sfn.failures(\"order-pipeline\"): most failures stop at LoadOrders with KeyError: 'customer_id', and the newest run's request_id is filled in.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 11.0,
    "chapter": "AWS Step Functions",
    "label": "AWS Step Functions",
    "code": "sfn.failures(\"order-pipeline\")",
    "outputs": [
     {
      "img": "sfn-failures",
      "w": 1476,
      "h": 1437
     }
    ],
    "captions": [
     [
      3.14,
      "Most failures stop at LoadOrders with <code>KeyError: 'customer_id'</code>, and the newest run's <code>request_id</code> is filled in."
     ]
    ]
   }
  ]
 },
 "sfn-execution": {
  "notebook": "workflows.ipynb",
  "summary": "sfn.execution(\"order-pipeline/ord-7f3a\"): sfn.execution(\"order-pipeline/ord-7f3a\"): the waterfall, then one log where orders-etl's traceback sits between the step that called it and the Catch that ended the execution.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 15.4,
    "chapter": "AWS Step Functions",
    "label": "AWS Step Functions",
    "code": "sfn.execution(\"order-pipeline/ord-7f3a\")",
    "outputs": [
     {
      "img": "sfn-execution",
      "w": 1476,
      "h": 2687
     }
    ],
    "captions": [
     [
      3.45,
      "The waterfall, then one log where orders-etl's traceback sits between the step that called it and the Catch that ended the execution."
     ]
    ]
   }
  ]
 },
 "sfn-state-machine": {
  "notebook": "workflows.ipynb",
  "summary": "sfn.state_machine(\"order-pipeline\"): sfn.state_machine(\"order-pipeline\"): LoadOrders has no Retry for Lambda's own errors, and the finding gives the rule to paste.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 13.2,
    "chapter": "AWS Step Functions",
    "label": "AWS Step Functions",
    "code": "sfn.state_machine(\"order-pipeline\")",
    "outputs": [
     {
      "img": "sfn-state-machine",
      "w": 1476,
      "h": 1744
     }
    ],
    "captions": [
     [
      3.29,
      "LoadOrders has no Retry for Lambda's own errors, and the finding gives the rule to paste."
     ]
    ]
   }
  ]
 },
 "sfn-graph": {
  "notebook": "workflows.ipynb",
  "summary": "sfn.graph(\"order-pipeline\", execution=\"ord-7f3a\"): sfn.graph(\"order-pipeline\", execution=\"ord-7f3a\"): the path the execution took, where it failed, and what it never reached (the top of the report; a table of the states and the Mermaid source follow).",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 10.5,
    "chapter": "AWS Step Functions",
    "label": "AWS Step Functions",
    "code": "sfn.graph(\"order-pipeline\", execution=\"ord-7f3a\")",
    "outputs": [
     {
      "img": "sfn-graph",
      "w": 1476,
      "h": 1275
     }
    ],
    "captions": [
     [
      3.73,
      "The path the execution took, where it failed, and what it never reached (the top of the report; a table of the states and the Mermaid source follow)."
     ]
    ]
   }
  ]
 },
 "athena-overview": {
  "notebook": "athena.ipynb",
  "summary": "athena.overview(): athena.overview(): one dashboard query is 90% of the bill, two tables point at nothing, and two crawlers stopped.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 14.7,
    "chapter": "Amazon Athena",
    "label": "Amazon Athena",
    "code": "athena.overview()",
    "outputs": [
     {
      "img": "athena-overview",
      "w": 1476,
      "h": 3097
     }
    ],
    "captions": [
     [
      2.73,
      "<b><code>athena.overview()</code></b>: one dashboard query is 90% of the bill, two tables point at nothing, and two crawlers stopped."
     ]
    ]
   }
  ]
 },
 "athena-expensive": {
  "notebook": "athena.ipynb",
  "summary": "athena.expensive(top=5): athena.expensive(): the dashboard's SELECT * ran 21 times and cost $22.56.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 14.5,
    "chapter": "Amazon Athena",
    "label": "Amazon Athena",
    "code": "athena.expensive(top=5)",
    "outputs": [
     {
      "img": "athena-expensive",
      "w": 1476,
      "h": 1982
     }
    ],
    "captions": [
     [
      2.92,
      "The dashboard's <code>SELECT *</code> ran 21 times and cost $22.56."
     ]
    ]
   }
  ]
 },
 "athena-failures": {
  "notebook": "athena.ipynb",
  "summary": "athena.failures(): athena.failures(): a typo'd table name, a bad value in a CSV file, and a query the scan limit stopped.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.8,
    "chapter": "Amazon Athena",
    "label": "Amazon Athena",
    "code": "athena.failures()",
    "outputs": [
     {
      "img": "athena-failures",
      "w": 1476,
      "h": 1326
     }
    ],
    "captions": [
     [
      2.73,
      "A typo'd table name, a bad value in a CSV file, and a query the scan limit stopped."
     ]
    ]
   }
  ]
 },
 "athena-catalog": {
  "notebook": "athena.ipynb",
  "summary": "athena.catalog(): athena.catalog(): marketing.campaigns points at a bucket that's gone, sales.clicks has files but no partitions.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 14.7,
    "chapter": "Amazon Athena",
    "label": "Amazon Athena",
    "code": "athena.catalog()",
    "outputs": [
     {
      "img": "athena-catalog",
      "w": 1476,
      "h": 2129
     }
    ],
    "captions": [
     [
      2.7,
      "<code>marketing.campaigns</code> points at a bucket that's gone, <code>sales.clicks</code> has files but no partitions."
     ]
    ]
   }
  ]
 },
 "athena-table-info": {
  "notebook": "athena.ipynb",
  "summary": "athena.table_info(\"sales.events\"): The top of athena.table_info(\"sales.events\"): every query reads all of it, so partitioning it by date is the fix.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 11.5,
    "chapter": "Amazon Athena",
    "label": "Amazon Athena",
    "code": "athena.table_info(\"sales.events\")",
    "outputs": [
     {
      "img": "athena-table-info",
      "w": 1476,
      "h": 1500
     }
    ],
    "captions": [
     [
      3.23,
      "The top of <code>athena.table_info(\"sales.events\")</code>: every query reads all of it, so partitioning it by date is the fix."
     ]
    ]
   }
  ]
 },
 "athena-query-code": {
  "notebook": "athena.ipynb",
  "summary": "athena.query_code(\"SELECT * FROM orders WHERE total > 100\", database=\"sales\"): athena.query_code(...): it doesn't filter on dt, so it reads every partition.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 11.9,
    "chapter": "Amazon Athena",
    "label": "Amazon Athena",
    "code": "athena.query_code(\"SELECT * FROM orders WHERE total > 100\", database=\"sales\")",
    "outputs": [
     {
      "img": "athena-query-code",
      "w": 1476,
      "h": 1349
     }
    ],
    "captions": [
     [
      4.6,
      "It doesn't filter on <code>dt</code>, so it reads every partition."
     ]
    ]
   }
  ]
 }
};
