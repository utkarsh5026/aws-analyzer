/* The screenshots' short clips, for tour.js. Written by .claude/skills/demo/tour_video.py --data from shots.py's figures and the guides' captions: change those, not this. */
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
    "captions": []
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
    "captions": []
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
    "captions": []
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
      "On a two-page PDF: its text laid out to read, the lines of each paragraph joined back up, without the header and page numbers on every page."
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
    "captions": []
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
    "captions": []
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
    "captions": []
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
      "In the <code>churn-analysis</code> space: the GPU has done nothing for this session, and nothing will stop the app tonight."
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
 },
 "agent-chat": {
  "notebook": "agent.ipynb",
  "summary": "agent.chat(): The answer first, with its plan as it left it and the steps folded above it into one line that names the services they read.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 8.5,
    "chapter": "Ask in plain English",
    "label": "Ask in plain English",
    "code": "agent.chat()",
    "outputs": [
     {
      "img": "agent-chat",
      "w": 1476,
      "h": 1140
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "<b>The answer first</b>, with its plan as it left it and the steps folded above it into one line that names the services they read."
     ]
    ]
   }
  ]
 },
 "agent-chat-details": {
  "notebook": "agent.ipynb",
  "summary": "agent.chat(): Check its work: each call opens to the operations it used, what it covered and the exact data the model read.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 8.5,
    "chapter": "Ask in plain English",
    "label": "Ask in plain English",
    "code": "agent.chat()",
    "outputs": [
     {
      "img": "agent-chat-details",
      "w": 1476,
      "h": 1140
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "<b>Check its work</b>: each call opens to the operations it used, what it covered and the exact data the model read."
     ]
    ]
   }
  ]
 },
 "agent-chat-memory": {
  "notebook": "agent.ipynb",
  "summary": "agent.chat(): agent.chat() with 🧠 memory open: a ✕ forgets one note; forget all asks for a second click.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 8.0,
    "chapter": "Ask in plain English",
    "label": "Ask in plain English",
    "code": "agent.chat()",
    "outputs": [
     {
      "img": "agent-chat-memory",
      "w": 1476,
      "h": 1058
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "With 🧠 memory open: a ✕ forgets one note; <b>forget all</b> asks for a second click."
     ]
    ]
   }
  ]
 },
 "agent-chat-model": {
  "notebook": "agent.ipynb",
  "summary": "agent.chat(): ⚙ model: pick the provider and model; the panel asks only for what that provider takes, and says where your questions go.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.9,
    "chapter": "Ask in plain English",
    "label": "Ask in plain English",
    "code": "agent.chat()",
    "outputs": [
     {
      "img": "agent-chat-model",
      "w": 1476,
      "h": 1341
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "<b>⚙ model</b>: pick the provider and model; the panel asks only for what that provider takes, and says where your questions go."
     ]
    ]
   }
  ]
 },
 "agent-chat-plan": {
  "notebook": "agent.ipynb",
  "summary": "agent.chat(): Its plan: two steps ticked off, the third under way, and the calls that did the first two under it.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 8.5,
    "chapter": "Ask in plain English",
    "label": "Ask in plain English",
    "code": "agent.chat()",
    "outputs": [
     {
      "img": "agent-chat-plan",
      "w": 1476,
      "h": 1140
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "<b>Its plan</b>: two steps ticked off, the third under way, and the calls that did the first two under it."
     ]
    ]
   }
  ]
 },
 "agent-chat-start": {
  "notebook": "agent.ipynb",
  "summary": "agent.chat(): Start here: the badges show which AWS services the agent reads; every question to start from shows the ones it needs.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 8.5,
    "chapter": "Ask in plain English",
    "label": "Ask in plain English",
    "code": "agent.chat()",
    "outputs": [
     {
      "img": "agent-chat-start",
      "w": 1476,
      "h": 1140
     }
    ],
    "captions": [
     [
      2.7,
      "<b>Start here</b>: the badges show which AWS services the agent reads; every question to start from shows the ones it needs."
     ]
    ]
   }
  ]
 },
 "agent-chat-working": {
  "notebook": "agent.ipynb",
  "summary": "agent.chat(): While it works: its plan, ticking off, what the model says before it acts (●), each call with its services and what it found, a question you typed meanwhile waiting its turn, and stop to end it with what it found so far.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 8.5,
    "chapter": "Ask in plain English",
    "label": "Ask in plain English",
    "code": "agent.chat()",
    "outputs": [
     {
      "img": "agent-chat-working",
      "w": 1476,
      "h": 1140
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "<b>While it works</b>: its plan, ticking off, what the model says before it acts (<code>●</code>), each call with its services and what it found, a question you typed meanwhile waiting its turn, and <b>stop</b> to end it with what it found so far."
     ]
    ]
   }
  ]
 },
 "agent-mcp": {
  "notebook": "agent.ipynb",
  "summary": "agent.chat(): 🔌 mcp: a line per server, its dot saying how it stands, and why one failed.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.7,
    "chapter": "Ask in plain English",
    "label": "Ask in plain English",
    "code": "agent.chat()",
    "outputs": [
     {
      "img": "agent-mcp",
      "w": 1476,
      "h": 1313
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "<b>🔌 mcp</b>: a line per server, its dot saying how it stands, and why one failed."
     ]
    ]
   }
  ]
 },
 "agent-mcp-add": {
  "notebook": "agent.ipynb",
  "summary": "agent.chat(): + add a server: a command, a URL or a pasted mcp.json; it guesses the name and connects before it saves.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 12.2,
    "chapter": "Ask in plain English",
    "label": "Ask in plain English",
    "code": "agent.chat()",
    "outputs": [
     {
      "img": "agent-mcp-add",
      "w": 1476,
      "h": 1680
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "<b>+ add a server</b>: a command, a URL or a pasted mcp.json; it guesses the name and connects before it saves."
     ]
    ]
   }
  ]
 },
 "agent-mcp-answer": {
  "notebook": "agent.ipynb",
  "summary": "agent.chat(): Once it ran: the calls, each with the 🔌 badge, and the answer citing them.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 7.7,
    "chapter": "Ask in plain English",
    "label": "Ask in plain English",
    "code": "agent.chat()",
    "outputs": [
     {
      "img": "agent-mcp-answer",
      "w": 1476,
      "h": 1020
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "Once it ran: the calls, each with the 🔌 badge, and the answer citing them."
     ]
    ]
   }
  ]
 },
 "agent-mcp-ask": {
  "notebook": "agent.ipynb",
  "summary": "agent.chat(): A tool that may change something asks run or skip first; one that only reads runs at once.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 7.7,
    "chapter": "Ask in plain English",
    "label": "Ask in plain English",
    "code": "agent.chat()",
    "outputs": [
     {
      "img": "agent-mcp-ask",
      "w": 1476,
      "h": 1020
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "A tool that may change something asks <b>run</b> or <b>skip</b> first; one that only reads runs at once."
     ]
    ]
   }
  ]
 },
 "agent-mcp-tools": {
  "notebook": "agent.ipynb",
  "summary": "agent.chat(): tools ▸: when it asks first, and a box per tool to turn one off.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 11.3,
    "chapter": "Ask in plain English",
    "label": "Ask in plain English",
    "code": "agent.chat()",
    "outputs": [
     {
      "img": "agent-mcp-tools",
      "w": 1476,
      "h": 1541
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "<b>tools ▸</b>: when it asks first, and a box per tool to turn one off."
     ]
    ]
   }
  ]
 },
 "agent-memory": {
  "notebook": "agent.ipynb",
  "summary": "agent.chat(): agent.memory(): each note and when it learned it, the ones old enough to check, and how full it is.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 8.3,
    "chapter": "Ask in plain English",
    "label": "Ask in plain English",
    "code": "agent.chat()",
    "outputs": [
     {
      "img": "agent-memory",
      "w": 1476,
      "h": 1100
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "Each note and when it learned it, the ones old enough to check, and how full it is."
     ]
    ]
   }
  ]
 },
 "apigw-explorer": {
  "notebook": "apis.ipynb",
  "summary": "apigw.explore(): apigw.explore(): every API, the failing ones first; search them, pick a chip (Failing, Open routes, Unused, ...) or click a column to sort.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.6,
    "chapter": "API Gateway explorer",
    "label": "API Gateway explorer",
    "code": "apigw.explore()",
    "outputs": [
     {
      "img": "apigw-explorer",
      "w": 1476,
      "h": 1299
     }
    ],
    "captions": [
     [
      2.7,
      "Every API, the failing ones first; search them, pick a chip (Failing, Open routes, Unused, ...) or click a column to sort."
     ]
    ]
   }
  ]
 },
 "apigw-explorer-call": {
  "notebook": "apis.ipynb",
  "summary": "apigw.explore(): A call that timed out: API Gateway gave up at 29 seconds, and the orders-etl run behind it finished at 41.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.6,
    "chapter": "API Gateway explorer",
    "label": "API Gateway explorer",
    "code": "apigw.explore()",
    "outputs": [
     {
      "img": "apigw-explorer-call",
      "w": 1476,
      "h": 1299
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "A call that timed out: API Gateway gave up at 29 seconds, and the <code>orders-etl</code> run behind it finished at 41."
     ]
    ]
   }
  ]
 },
 "apigw-explorer-routes": {
  "notebook": "apis.ipynb",
  "summary": "apigw.explore(): POST /orders in the Routes tab: the integration is where it fails.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.6,
    "chapter": "API Gateway explorer",
    "label": "API Gateway explorer",
    "code": "apigw.explore()",
    "outputs": [
     {
      "img": "apigw-explorer-routes",
      "w": 1476,
      "h": 1299
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "<code>POST /orders</code> in the Routes tab: the integration is where it fails."
     ]
    ]
   }
  ]
 },
 "athena-explorer-catalog": {
  "notebook": "athena.ipynb",
  "summary": "athena.explore(): The Catalog tab, To fix: sales.clicks has files in S3 but no partitions in the catalog.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.6,
    "chapter": "Athena explorer",
    "label": "Athena explorer",
    "code": "athena.explore()",
    "outputs": [
     {
      "img": "athena-explorer-catalog",
      "w": 1476,
      "h": 1296
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "The Catalog tab, To fix: <code>sales.clicks</code> has files in S3 but no partitions in the catalog."
     ]
    ]
   }
  ]
 },
 "athena-explorer-costs": {
  "notebook": "athena.ipynb",
  "summary": "athena.explore(): athena.explore(), the Costs tab: a click on a kind of query shows its newest run.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.6,
    "chapter": "Athena explorer",
    "label": "Athena explorer",
    "code": "athena.explore()",
    "outputs": [
     {
      "img": "athena-explorer-costs",
      "w": 1476,
      "h": 1296
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "The Costs tab: a click on a kind of query shows its newest run."
     ]
    ]
   }
  ]
 },
 "chat-add": {
  "notebook": "support-docs.ipynb",
  "summary": "chat(\"support-docs\"): Searching the settings: every field whose name, path or description mentions \"rerank\", with a button to add each.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.5,
    "chapter": "Bedrock chat",
    "label": "Bedrock chat",
    "code": "chat(\"support-docs\")",
    "outputs": [
     {
      "img": "chat-add",
      "w": 1476,
      "h": 1262
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.83,
      "Searching the settings: every field whose name, path or description mentions \"rerank\", with a button to add each."
     ]
    ]
   }
  ]
 },
 "chat-code": {
  "notebook": "support-docs.ipynb",
  "summary": "chat(\"support-docs\"): Python: a script that asks the test questions with this setup, using only boto3.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.2,
    "chapter": "Bedrock chat",
    "label": "Bedrock chat",
    "code": "chat(\"support-docs\")",
    "outputs": [
     {
      "img": "chat-code",
      "w": 1476,
      "h": 1223
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.83,
      "<b>Python</b>: a script that asks the test questions with this setup, using only boto3."
     ]
    ]
   }
  ]
 },
 "chat-edit": {
  "notebook": "support-docs.ipynb",
  "summary": "chat(\"support-docs\"): A field Bedrock doesn't take is refused before anything is sent, with the fields that are allowed there and where model-specific ones go.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.5,
    "chapter": "Bedrock chat",
    "label": "Bedrock chat",
    "code": "chat(\"support-docs\")",
    "outputs": [
     {
      "img": "chat-edit",
      "w": 1476,
      "h": 1262
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.83,
      "A field Bedrock doesn't take is refused before anything is sent, with the fields that are allowed there and where model-specific ones go."
     ]
    ]
   }
  ]
 },
 "chat-files": {
  "notebook": "support-docs.ipynb",
  "summary": "chat(\"support-docs\"): Two files ticked: the next answers come only from them, and the conversation goes on.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.5,
    "chapter": "Bedrock chat",
    "label": "Bedrock chat",
    "code": "chat(\"support-docs\")",
    "outputs": [
     {
      "img": "chat-files",
      "w": 1476,
      "h": 1269
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.83,
      "Two files ticked: the next answers come only from them, and the conversation goes on."
     ]
    ]
   }
  ]
 },
 "chat-pick": {
  "notebook": "support-docs.ipynb",
  "summary": "chat(\"support-docs\"): The knowledge base list, open. Type part of a name or an ID to narrow it; Enter picks the first.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.0,
    "chapter": "Bedrock chat",
    "label": "Bedrock chat",
    "code": "chat(\"support-docs\")",
    "outputs": [
     {
      "img": "chat-pick",
      "w": 1476,
      "h": 1197
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.83,
      "The knowledge base list, open. Type part of a name or an ID to narrow it; Enter picks the first."
     ]
    ]
   }
  ]
 },
 "chat-python": {
  "notebook": "support-docs.ipynb",
  "summary": "chat(\"support-docs\"): Python: the same request as a boto3 call, highlighted, to paste into your own code.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.0,
    "chapter": "Bedrock chat",
    "label": "Bedrock chat",
    "code": "chat(\"support-docs\")",
    "outputs": [
     {
      "img": "chat-python",
      "w": 1476,
      "h": 1197
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.83,
      "<b>Python</b>: the same request as a boto3 call, highlighted, to paste into your own code."
     ]
    ]
   }
  ]
 },
 "chat-request": {
  "notebook": "support-docs.ipynb",
  "summary": "chat(\"support-docs\"): The next request, continuing the conversation. Highlighted keys are your settings.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.5,
    "chapter": "Bedrock chat",
    "label": "Bedrock chat",
    "code": "chat(\"support-docs\")",
    "outputs": [
     {
      "img": "chat-request",
      "w": 1476,
      "h": 1262
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.83,
      "The next request, continuing the conversation. Highlighted keys are your settings."
     ]
    ]
   }
  ]
 },
 "chat-retrieve": {
  "notebook": "support-docs.ipynb",
  "summary": "chat(\"support-docs\"): The same question asked both ways: the search found five passages, and the answer used the top two.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.0,
    "chapter": "Bedrock chat",
    "label": "Bedrock chat",
    "code": "chat(\"support-docs\")",
    "outputs": [
     {
      "img": "chat-retrieve",
      "w": 1476,
      "h": 1197
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.83,
      "The same question asked both ways: the search found five passages, and the answer used the top two."
     ]
    ]
   }
  ]
 },
 "chat-runs": {
  "notebook": "support-docs.ipynb",
  "summary": "chat(\"support-docs\"): Every test run so far, the test run and the sweep's four setups, each ranked against the others of the same questions.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.5,
    "chapter": "Bedrock chat",
    "label": "Bedrock chat",
    "code": "chat(\"support-docs\")",
    "outputs": [
     {
      "img": "chat-runs",
      "w": 1476,
      "h": 1262
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.83,
      "Every test run so far, the test run and the sweep's four setups, each ranked against the others of the same questions."
     ]
    ]
   }
  ]
 },
 "chat-settings": {
  "notebook": "support-docs.ipynb",
  "summary": "chat(\"support-docs\"): A filter, a reranker and a temperature added with one click each, every value explained in a sentence.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.5,
    "chapter": "Bedrock chat",
    "label": "Bedrock chat",
    "code": "chat(\"support-docs\")",
    "outputs": [
     {
      "img": "chat-settings",
      "w": 1476,
      "h": 1262
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.83,
      "A filter, a reranker and a temperature added with one click each, every value explained in a sentence."
     ]
    ]
   }
  ]
 },
 "chat-sweep": {
  "notebook": "support-docs.ipynb",
  "summary": "chat(\"support-docs\"): Try variations with n = 3, 8 and reranker = none, cohere: the four setups ranked, how the best compares with the setup in use, and what each setting changed.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.5,
    "chapter": "Bedrock chat",
    "label": "Bedrock chat",
    "code": "chat(\"support-docs\")",
    "outputs": [
     {
      "img": "chat-sweep",
      "w": 1476,
      "h": 1262
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.83,
      "Try variations with <code>n = 3, 8</code> and <code>reranker = none, cohere</code>: the four setups ranked, how the best compares with the setup in use, and what each setting changed."
     ]
    ]
   }
  ]
 },
 "chat-test": {
  "notebook": "support-docs.ipynb",
  "summary": "chat(\"support-docs\"): Six test questions asked with the window's settings: four answered, two Bedrock couldn't help with, and what to try next.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.5,
    "chapter": "Bedrock chat",
    "label": "Bedrock chat",
    "code": "chat(\"support-docs\")",
    "outputs": [
     {
      "img": "chat-test",
      "w": 1476,
      "h": 1262
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.83,
      "Six test questions asked with the window's settings: four answered, two Bedrock couldn't help with, and what to try next."
     ]
    ]
   }
  ]
 },
 "chat-wide": {
  "notebook": "support-docs.ipynb",
  "summary": "chat(\"support-docs\"): Settings at the top right hides the side tabs, and the conversation takes the whole width. Chat does the same the other way.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 8.9,
    "chapter": "Bedrock chat",
    "label": "Bedrock chat",
    "code": "chat(\"support-docs\")",
    "outputs": [
     {
      "img": "chat-wide",
      "w": 1476,
      "h": 1172
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.83,
      "<b>Settings</b> at the top right hides the side tabs, and the conversation takes the whole width. <b>Chat</b> does the same the other way."
     ]
    ]
   }
  ]
 },
 "chat-window": {
  "notebook": "support-docs.ipynb",
  "summary": "chat(\"support-docs\"): Two questions about refunds, in the same Bedrock session. The second asks for a list, and the answer's markdown is laid out: bullets, bold, and a citation after each cited span.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.0,
    "chapter": "Bedrock chat",
    "label": "Bedrock chat",
    "code": "chat(\"support-docs\")",
    "outputs": [
     {
      "img": "chat-window",
      "w": 1476,
      "h": 1197
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.83,
      "Two questions about refunds, in the same Bedrock session."
     ],
     [
      6.43,
      "The second asks for a list, and the answer's markdown is laid out: bullets, bold, and a citation after each cited span."
     ]
    ]
   }
  ]
 },
 "explorer": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "x = S3Explorer(\"s3://acme-ml-data/\"): The folder on the left, the file you clicked on the right. One click on the path at the bottom selects it, ready to copy.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 7.3,
    "chapter": "S3 file explorer",
    "label": "S3 file explorer",
    "code": "x = S3Explorer(\"s3://acme-ml-data/\")",
    "outputs": [
     {
      "img": "explorer",
      "w": 1476,
      "h": 872
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      3.33,
      "The folder on the left, the file you clicked on the right. One click on the path at the bottom selects it, ready to copy."
     ]
    ]
   }
  ]
 },
 "explorer-buckets": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "x = S3Explorer(\"s3://acme-ml-data/\"): S3Explorer() starts from your buckets; 🪣 Every bucket compares their size, cost and security settings.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 7.3,
    "chapter": "S3 file explorer",
    "label": "S3 file explorer",
    "code": "x = S3Explorer(\"s3://acme-ml-data/\")",
    "outputs": [
     {
      "img": "explorer-buckets",
      "w": 1476,
      "h": 869
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      3.33,
      "Starts from your buckets; <b>🪣 Every bucket</b> compares their size, cost and security settings."
     ]
    ]
   }
  ]
 },
 "explorer-changes": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "x = S3Explorer(\"s3://acme-ml-data/\"): A folder you were in two days ago: one file replaced, one added.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 7.3,
    "chapter": "S3 file explorer",
    "label": "S3 file explorer",
    "code": "x = S3Explorer(\"s3://acme-ml-data/\")",
    "outputs": [
     {
      "img": "explorer-changes",
      "w": 1476,
      "h": 872
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      3.33,
      "A folder you were in two days ago: one file replaced, one added."
     ]
    ]
   }
  ]
 },
 "explorer-docx": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "x = S3Explorer(\"s3://acme-ml-data/\"): A Word file's first paragraphs; 📖 Read all shows the whole document, pictures in place.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 7.3,
    "chapter": "S3 file explorer",
    "label": "S3 file explorer",
    "code": "x = S3Explorer(\"s3://acme-ml-data/\")",
    "outputs": [
     {
      "img": "explorer-docx",
      "w": 1476,
      "h": 872
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      3.33,
      "A Word file's first paragraphs; <b>📖 Read all</b> shows the whole document, pictures in place."
     ]
    ]
   }
  ]
 },
 "explorer-more": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "x = S3Explorer(\"s3://acme-ml-data/\"): More ▾ → 🐘 Largest files: where a bucket's space goes, without leaving the explorer.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 7.3,
    "chapter": "S3 file explorer",
    "label": "S3 file explorer",
    "code": "x = S3Explorer(\"s3://acme-ml-data/\")",
    "outputs": [
     {
      "img": "explorer-more",
      "w": 1476,
      "h": 872
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      3.33,
      "<b>More ▾</b> → <b>🐘 Largest files</b>: where a bucket's space goes, without leaving the explorer."
     ]
    ]
   }
  ]
 },
 "explorer-pdf-text": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "x = S3Explorer(\"s3://acme-ml-data/\"): 📄 Text on a two-page PDF: the words to read, without the header and page numbers repeated on every page.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 7.3,
    "chapter": "S3 file explorer",
    "label": "S3 file explorer",
    "code": "x = S3Explorer(\"s3://acme-ml-data/\")",
    "outputs": [
     {
      "img": "explorer-pdf-text",
      "w": 1476,
      "h": 872
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      3.33,
      "<b>📄 Text</b> on a two-page PDF: the words to read, without the header and page numbers repeated on every page."
     ]
    ]
   }
  ]
 },
 "explorer-python": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "x = S3Explorer(\"s3://acme-ml-data/\"): 🐍 Use in Python on a Parquet file: copy the lines, run them, and the table is a DataFrame.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 7.3,
    "chapter": "S3 file explorer",
    "label": "S3 file explorer",
    "code": "x = S3Explorer(\"s3://acme-ml-data/\")",
    "outputs": [
     {
      "img": "explorer-python",
      "w": 1476,
      "h": 872
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      3.33,
      "<b>🐍 Use in Python</b> on a Parquet file: copy the lines, run them, and the table is a DataFrame."
     ]
    ]
   }
  ]
 },
 "explorer-recent": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "x = S3Explorer(\"s3://acme-ml-data/\"): S3Explorer(): the folder you were in last, then the ones you open most. Point at a chip for its whole path.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 7.3,
    "chapter": "S3 file explorer",
    "label": "S3 file explorer",
    "code": "x = S3Explorer(\"s3://acme-ml-data/\")",
    "outputs": [
     {
      "img": "explorer-recent",
      "w": 1476,
      "h": 869
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      3.33,
      "The folder you were in last, then the ones you open most. Point at a chip for its whole path."
     ]
    ]
   }
  ]
 },
 "explorer-search": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "x = S3Explorer(\"s3://acme-ml-data/\"): .tar with Include subfolders: every model artifact below training/, each under the run it came from.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 7.3,
    "chapter": "S3 file explorer",
    "label": "S3 file explorer",
    "code": "x = S3Explorer(\"s3://acme-ml-data/\")",
    "outputs": [
     {
      "img": "explorer-search",
      "w": 1476,
      "h": 872
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      3.33,
      "<code>.tar</code> with <b>Include subfolders</b>: every model artifact below <code>training/</code>, each under the run it came from."
     ]
    ]
   }
  ]
 },
 "explorer-zip": {
  "notebook": "acme-ml-data.ipynb",
  "summary": "x = S3Explorer(\"s3://acme-ml-data/\"): Five files ticked, one .zip with a name that says what's in it.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 7.3,
    "chapter": "S3 file explorer",
    "label": "S3 file explorer",
    "code": "x = S3Explorer(\"s3://acme-ml-data/\")",
    "outputs": [
     {
      "img": "explorer-zip",
      "w": 1476,
      "h": 872
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      3.33,
      "Five files ticked, one <code>.zip</code> with a name that says what's in it."
     ]
    ]
   }
  ]
 },
 "kb-explorer": {
  "notebook": "support-docs.ipynb",
  "summary": "ui.explore(\"support-docs\"): Overview: a sentence on the files, then what to do about them, a card for each fix: a sync per data source (with its command), the files to fix, and the files to convert. Below, out of the picture: how each data source becomes vectors, with each problem pinned under the step where it happens, and the recent syncs.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.7,
    "chapter": "Knowledge base explorer",
    "label": "Knowledge base explorer",
    "code": "ui.explore(\"support-docs\")",
    "outputs": [
     {
      "img": "kb-explorer",
      "w": 1476,
      "h": 1269
     }
    ],
    "captions": [
     [
      3.01,
      "<b>Overview</b>: a sentence on the files, then what to do about them, a card for each fix: a sync per data source (with its command), the files to fix, and the files to convert."
     ],
     [
      6.61,
      "Below, out of the picture: how each data source becomes vectors, with each problem pinned under the step where it happens, and the recent syncs."
     ]
    ]
   }
  ]
 },
 "kb-explorer-ask": {
  "notebook": "support-docs.ipynb",
  "summary": "ui.explore(\"support-docs\"): The file holds the answer, but five passages of other files rank above it.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.7,
    "chapter": "Knowledge base explorer",
    "label": "Knowledge base explorer",
    "code": "ui.explore(\"support-docs\")",
    "outputs": [
     {
      "img": "kb-explorer-ask",
      "w": 1476,
      "h": 1269
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      3.01,
      "The file holds the answer, but five passages of other files rank above it."
     ]
    ]
   }
  ]
 },
 "kb-explorer-chunks": {
  "notebook": "support-docs.ipynb",
  "summary": "ui.explore(\"support-docs\"): Its chunks in document order, one of them open: the green start is the text it shares with the chunk before it.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.7,
    "chapter": "Knowledge base explorer",
    "label": "Knowledge base explorer",
    "code": "ui.explore(\"support-docs\")",
    "outputs": [
     {
      "img": "kb-explorer-chunks",
      "w": 1476,
      "h": 1269
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      3.01,
      "Its chunks in document order, one of them open: the green start is the text it shares with the chunk before it."
     ]
    ]
   }
  ]
 },
 "kb-explorer-compare": {
  "notebook": "support-docs.ipynb",
  "summary": "ui.explore(\"support-docs\"): Compare: where each passage ranks with each search type and number of passages.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.7,
    "chapter": "Knowledge base explorer",
    "label": "Knowledge base explorer",
    "code": "ui.explore(\"support-docs\")",
    "outputs": [
     {
      "img": "kb-explorer-compare",
      "w": 1476,
      "h": 1269
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      3.01,
      "<b>Compare</b>: where each passage ranks with each search type and number of passages."
     ]
    ]
   }
  ]
 },
 "kb-explorer-failed": {
  "notebook": "support-docs.ipynb",
  "summary": "ui.explore(\"support-docs\"): A failed file: Bedrock's reason, what usually fixes it, and the step that failed.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.7,
    "chapter": "Knowledge base explorer",
    "label": "Knowledge base explorer",
    "code": "ui.explore(\"support-docs\")",
    "outputs": [
     {
      "img": "kb-explorer-failed",
      "w": 1476,
      "h": 1269
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      3.01,
      "A failed file: Bedrock's reason, what usually fixes it, and the step that failed."
     ]
    ]
   }
  ]
 },
 "kb-explorer-file": {
  "notebook": "support-docs.ipynb",
  "summary": "ui.explore(\"support-docs\"): A file's page: each step from S3 into the vector store, here all of them green.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.7,
    "chapter": "Knowledge base explorer",
    "label": "Knowledge base explorer",
    "code": "ui.explore(\"support-docs\")",
    "outputs": [
     {
      "img": "kb-explorer-file",
      "w": 1476,
      "h": 1269
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      3.01,
      "A file's page: each step from S3 into the vector store, here all of them green."
     ]
    ]
   }
  ]
 },
 "kb-explorer-filter": {
  "notebook": "support-docs.ipynb",
  "summary": "ui.explore(\"support-docs\"): A metadata filter under the options: only the files whose .metadata.json matches.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.7,
    "chapter": "Knowledge base explorer",
    "label": "Knowledge base explorer",
    "code": "ui.explore(\"support-docs\")",
    "outputs": [
     {
      "img": "kb-explorer-filter",
      "w": 1476,
      "h": 1269
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      3.01,
      "A <b>metadata filter</b> under the options: only the files whose <code>.metadata.json</code> matches."
     ]
    ]
   }
  ]
 },
 "kb-explorer-search": {
  "notebook": "support-docs.ipynb",
  "summary": "ui.explore(\"support-docs\"): Search: each passage's How … was indexed › opens its file's page.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.7,
    "chapter": "Knowledge base explorer",
    "label": "Knowledge base explorer",
    "code": "ui.explore(\"support-docs\")",
    "outputs": [
     {
      "img": "kb-explorer-search",
      "w": 1476,
      "h": 1269
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      3.01,
      "<b>Search</b>: each passage's <b>How … was indexed ›</b> opens its file's page."
     ]
    ]
   }
  ]
 },
 "kb-explorer-syncs": {
  "notebook": "support-docs.ipynb",
  "summary": "ui.explore(\"support-docs\"): Syncs: the history as a timeline, with why a sync failed.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.7,
    "chapter": "Knowledge base explorer",
    "label": "Knowledge base explorer",
    "code": "ui.explore(\"support-docs\")",
    "outputs": [
     {
      "img": "kb-explorer-syncs",
      "w": 1476,
      "h": 1269
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      3.01,
      "<b>Syncs</b>: the history as a timeline, with why a sync failed."
     ]
    ]
   }
  ]
 },
 "lambda-explorer": {
  "notebook": "lambda.ipynb",
  "summary": "ui.explore(): Functions: every function with its last 14 days, what's wrong with it, its calls, error rate, run time and cost, the ones that need attention first.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.6,
    "chapter": "Lambda explorer",
    "label": "Lambda explorer",
    "code": "ui.explore()",
    "outputs": [
     {
      "img": "lambda-explorer",
      "w": 1476,
      "h": 1295
     }
    ],
    "captions": [
     [
      2.7,
      "<b>Functions</b>: every function with its last 14 days, what's wrong with it, its calls, error rate, run time and cost, the ones that need attention first."
     ]
    ]
   }
  ]
 },
 "lambda-explorer-changed": {
  "notebook": "lambda.ipynb",
  "summary": "ui.explore(): Changed: the functions that are different from when you last looked.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.6,
    "chapter": "Lambda explorer",
    "label": "Lambda explorer",
    "code": "ui.explore()",
    "outputs": [
     {
      "img": "lambda-explorer-changed",
      "w": 1476,
      "h": 1295
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "<b>Changed</b>: the functions that are different from when you last looked."
     ]
    ]
   }
  ]
 },
 "lambda-explorer-changes": {
  "notebook": "lambda.ipynb",
  "summary": "ui.explore(): What changed in orders-etl since three days ago, among its findings.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.1,
    "chapter": "Lambda explorer",
    "label": "Lambda explorer",
    "code": "ui.explore()",
    "outputs": [
     {
      "img": "lambda-explorer-changes",
      "w": 1476,
      "h": 1217
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "What changed in orders-etl since three days ago, among its findings."
     ]
    ]
   }
  ]
 },
 "lambda-explorer-code": {
  "notebook": "lambda.ipynb",
  "summary": "ui.explore(): Code: the package as a tree of folders, the handler's source to copy, and a .env file that shouldn't be in the package.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.1,
    "chapter": "Lambda explorer",
    "label": "Lambda explorer",
    "code": "ui.explore()",
    "outputs": [
     {
      "img": "lambda-explorer-code",
      "w": 1476,
      "h": 1217
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "<b>Code</b>: the package as a tree of folders, the handler's source to copy, and a <code>.env</code> file that shouldn't be in the package."
     ]
    ]
   }
  ]
 },
 "lambda-explorer-errors": {
  "notebook": "lambda.ipynb",
  "summary": "ui.explore(): Errors: three causes, each a click from the runs it failed in, then what to do about each.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.1,
    "chapter": "Lambda explorer",
    "label": "Lambda explorer",
    "code": "ui.explore()",
    "outputs": [
     {
      "img": "lambda-explorer-errors",
      "w": 1476,
      "h": 1217
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "<b>Errors</b>: three causes, each a click from the runs it failed in, then what to do about each."
     ]
    ]
   }
  ]
 },
 "lambda-explorer-failed-line": {
  "notebook": "lambda.ipynb",
  "summary": "ui.explore(): Code › on a cause, or the button under a failed run: the line its traceback names, in the package's own code.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.1,
    "chapter": "Lambda explorer",
    "label": "Lambda explorer",
    "code": "ui.explore()",
    "outputs": [
     {
      "img": "lambda-explorer-failed-line",
      "w": 1476,
      "h": 1217
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "<b>Code ›</b> on a cause, or the button under a failed run: the line its traceback names, in the package's own code."
     ]
    ]
   }
  ]
 },
 "lambda-explorer-json": {
  "notebook": "lambda.ipynb",
  "summary": "ui.explore(): report-api logs JSON: each line's message, its level, and its other fields a click away.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.1,
    "chapter": "Lambda explorer",
    "label": "Lambda explorer",
    "code": "ui.explore()",
    "outputs": [
     {
      "img": "lambda-explorer-json",
      "w": 1476,
      "h": 1217
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "report-api logs JSON: each line's message, its level, and its other fields a click away."
     ]
    ]
   }
  ]
 },
 "lambda-explorer-live": {
  "notebook": "lambda.ipynb",
  "summary": "ui.explore(): explore(tab=\"live\"): three functions ran in the last 10 minutes, two of them running now.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.2,
    "chapter": "Lambda explorer",
    "label": "Lambda explorer",
    "code": "ui.explore()",
    "outputs": [
     {
      "img": "lambda-explorer-live",
      "w": 1476,
      "h": 1242
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "Three functions ran in the last 10 minutes, two of them running now."
     ]
    ]
   }
  ]
 },
 "lambda-explorer-live-follow": {
  "notebook": "lambda.ipynb",
  "summary": "ui.explore(): Following orders-etl: its run going, near its timeout, its lines as they come.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.1,
    "chapter": "Lambda explorer",
    "label": "Lambda explorer",
    "code": "ui.explore()",
    "outputs": [
     {
      "img": "lambda-explorer-live-follow",
      "w": 1476,
      "h": 1217
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "Following orders-etl: its run going, near its timeout, its lines as they come."
     ]
    ]
   }
  ]
 },
 "lambda-explorer-logs": {
  "notebook": "lambda.ipynb",
  "summary": "ui.explore(): Logs: orders-etl's last 24 hours, run by run, with a failed run open: its traceback, and its REPORT line summed up.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.1,
    "chapter": "Lambda explorer",
    "label": "Lambda explorer",
    "code": "ui.explore()",
    "outputs": [
     {
      "img": "lambda-explorer-logs",
      "w": 1476,
      "h": 1217
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "<b>Logs</b>: orders-etl's last 24 hours, run by run, with a failed run open: its traceback, and its REPORT line summed up."
     ]
    ]
   }
  ]
 },
 "lambda-explorer-overview": {
  "notebook": "lambda.ipynb",
  "summary": "ui.explore(): Overview, under the findings: failed S3 events go nowhere, its logs are kept forever, and its longest runs come close to the 60 s timeout.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.1,
    "chapter": "Lambda explorer",
    "label": "Lambda explorer",
    "code": "ui.explore()",
    "outputs": [
     {
      "img": "lambda-explorer-overview",
      "w": 1476,
      "h": 1217
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "<b>Overview</b>, under the findings: failed S3 events go nowhere, its logs are kept forever, and its longest runs come close to the 60 s timeout."
     ]
    ]
   }
  ]
 },
 "lambda-explorer-recent": {
  "notebook": "lambda.ipynb",
  "summary": "ui.explore(): Recent: the functions you've opened, in this session or an earlier one.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.1,
    "chapter": "Lambda explorer",
    "label": "Lambda explorer",
    "code": "ui.explore()",
    "outputs": [
     {
      "img": "lambda-explorer-recent",
      "w": 1476,
      "h": 1217
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "<b>Recent</b>: the functions you've opened, in this session or an earlier one."
     ]
    ]
   }
  ]
 },
 "lambda-explorer-search": {
  "notebook": "lambda.ipynb",
  "summary": "ui.explore(): AccessDenied, searched in CloudWatch: two runs in the last 24 hours, each brought back whole, the matching line marked.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.1,
    "chapter": "Lambda explorer",
    "label": "Lambda explorer",
    "code": "ui.explore()",
    "outputs": [
     {
      "img": "lambda-explorer-search",
      "w": 1476,
      "h": 1217
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "<b>AccessDenied</b>, searched in CloudWatch: two runs in the last 24 hours, each brought back whole, the matching line marked."
     ]
    ]
   }
  ]
 },
 "msg-explorer": {
  "notebook": "messaging.ipynb",
  "summary": "msg.explore(): msg.explore(): every queue, topic and rule, the ones that need attention first; search them, pick a chip or click a column to sort.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.6,
    "chapter": "Messaging explorer",
    "label": "Messaging explorer",
    "code": "msg.explore()",
    "outputs": [
     {
      "img": "msg-explorer",
      "w": 1476,
      "h": 1299
     }
    ],
    "captions": [
     [
      2.7,
      "Every queue, topic and rule, the ones that need attention first; search them, pick a chip or click a column to sort."
     ]
    ]
   }
  ]
 },
 "msg-explorer-dead": {
  "notebook": "messaging.ipynb",
  "summary": "msg.explore(): The Dead letters tab: orders-dlq holds 42 failed orders, and the command that moves them back once the cause is fixed.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.6,
    "chapter": "Messaging explorer",
    "label": "Messaging explorer",
    "code": "msg.explore()",
    "outputs": [
     {
      "img": "msg-explorer-dead",
      "w": 1476,
      "h": 1299
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "The Dead letters tab: orders-dlq holds 42 failed orders, and the command that moves them back once the cause is fixed."
     ]
    ]
   }
  ]
 },
 "msg-explorer-live": {
  "notebook": "messaging.ipynb",
  "summary": "msg.explore(): Live: email-jobs draining after its reader was turned back on.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.6,
    "chapter": "Messaging explorer",
    "label": "Messaging explorer",
    "code": "msg.explore()",
    "outputs": [
     {
      "img": "msg-explorer-live",
      "w": 1476,
      "h": 1299
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "Live: email-jobs draining after its reader was turned back on."
     ]
    ]
   }
  ]
 },
 "msg-explorer-map": {
  "notebook": "messaging.ipynb",
  "summary": "msg.explore(): The Map tab: where orders-events' messages go, and the two queues that keep them out.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.6,
    "chapter": "Messaging explorer",
    "label": "Messaging explorer",
    "code": "msg.explore()",
    "outputs": [
     {
      "img": "msg-explorer-map",
      "w": 1476,
      "h": 1299
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "The Map tab: where orders-events' messages go, and the two queues that keep them out."
     ]
    ]
   }
  ]
 },
 "os-explorer": {
  "notebook": "vectors.ipynb",
  "summary": "ui.explore(): ui.explore(): every domain and collection, the ones with warnings first; search them, pick a chip (With warnings, Domains, Vector collections, ...) or click a column to sort.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.6,
    "chapter": "OpenSearch explorer",
    "label": "OpenSearch explorer",
    "code": "ui.explore()",
    "outputs": [
     {
      "img": "os-explorer",
      "w": 1476,
      "h": 1299
     }
    ],
    "captions": [
     [
      2.7,
      "Every domain and collection, the ones with warnings first; search them, pick a chip (With warnings, Domains, Vector collections, ...) or click a column to sort."
     ]
    ]
   }
  ]
 },
 "os-explorer-documents": {
  "notebook": "vectors.ipynb",
  "summary": "ui.explore(): A document the search can never find: it has no embedding.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.6,
    "chapter": "OpenSearch explorer",
    "label": "OpenSearch explorer",
    "code": "ui.explore()",
    "outputs": [
     {
      "img": "os-explorer-documents",
      "w": 1476,
      "h": 1299
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "A document the search can never find: it has no <code>embedding</code>."
     ]
    ]
   }
  ]
 },
 "os-explorer-index": {
  "notebook": "vectors.ipynb",
  "summary": "ui.explore(): support-docs in the Indexes tab: 27,000 documents have no vector, and the graphs don't fit in the k-NN memory.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.6,
    "chapter": "OpenSearch explorer",
    "label": "OpenSearch explorer",
    "code": "ui.explore()",
    "outputs": [
     {
      "img": "os-explorer-index",
      "w": 1476,
      "h": 1299
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "<code>support-docs</code> in the Indexes tab: 27,000 documents have no vector, and the graphs don't fit in the k-NN memory."
     ]
    ]
   }
  ]
 },
 "os-explorer-search": {
  "notebook": "vectors.ipynb",
  "summary": "ui.explore(): \"how do I get a refund?\" in the Search tab: the nearest articles, and two of them indexed twice.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.6,
    "chapter": "OpenSearch explorer",
    "label": "OpenSearch explorer",
    "code": "ui.explore()",
    "outputs": [
     {
      "img": "os-explorer-search",
      "w": 1476,
      "h": 1299
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "\"how do I get a refund?\" in the Search tab: the nearest articles, and two of them indexed twice."
     ]
    ]
   }
  ]
 },
 "sfn-explorer": {
  "notebook": "workflows.ipynb",
  "summary": "sfn.explore(): sfn.explore(): every state machine, the failing ones first; search them, pick a chip (Failing, Express, Logging off, ...) or click a column to sort. The cards say how the one picked is doing.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.6,
    "chapter": "Step Functions explorer",
    "label": "Step Functions explorer",
    "code": "sfn.explore()",
    "outputs": [
     {
      "img": "sfn-explorer",
      "w": 1476,
      "h": 1299
     }
    ],
    "captions": [
     [
      2.7,
      "Every state machine, the failing ones first; search them, pick a chip (Failing, Express, Logging off, ...) or click a column to sort."
     ],
     [
      6.3,
      "The cards say how the one picked is doing."
     ]
    ]
   }
  ]
 },
 "sfn-explorer-graph": {
  "notebook": "workflows.ipynb",
  "summary": "sfn.explore(): The Graph tab with ord-7f3a open: its path in green, the failure in red, and what it never reached dashed.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.6,
    "chapter": "Step Functions explorer",
    "label": "Step Functions explorer",
    "code": "sfn.explore()",
    "outputs": [
     {
      "img": "sfn-explorer-graph",
      "w": 1476,
      "h": 1299
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "The Graph tab with ord-7f3a open: its path in green, the failure in red, and what it never reached dashed."
     ]
    ]
   }
  ]
 },
 "sfn-explorer-log": {
  "notebook": "workflows.ipynb",
  "summary": "sfn.explore(): Lower on the same page: Step Functions' events and each function's lines, one log in time order.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.6,
    "chapter": "Step Functions explorer",
    "label": "Step Functions explorer",
    "code": "sfn.explore()",
    "outputs": [
     {
      "img": "sfn-explorer-log",
      "w": 1476,
      "h": 1299
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "Lower on the same page: Step Functions' events and each function's lines, one log in time order."
     ]
    ]
   }
  ]
 },
 "sfn-explorer-run": {
  "notebook": "workflows.ipynb",
  "summary": "sfn.explore(): ord-7f3a: two throttled tries on ChargeCard, then LoadOrders fails with KeyError.",
  "video": "",
  "scenes": [
   {
    "kind": "cell",
    "dur": 9.6,
    "chapter": "Step Functions explorer",
    "label": "Step Functions explorer",
    "code": "sfn.explore()",
    "outputs": [
     {
      "img": "sfn-explorer-run",
      "w": 1476,
      "h": 1299
     }
    ],
    "captions": [
     [
      0,
      "<b>Opens the window</b>, shown here a few clicks in."
     ],
     [
      2.7,
      "ord-7f3a: two throttled tries on ChargeCard, then LoadOrders fails with <code>KeyError</code>."
     ]
    ]
   }
  ]
 }
};
