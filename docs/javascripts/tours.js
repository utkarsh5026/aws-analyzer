/* The video tours' scenes, for tour.js. Written by .claude/skills/demo/tour_video.py --data: edit the tours there, not here. */
window.AA_TOURS = {
 "s3": {
  "eyebrow": "Amazon S3",
  "notebook": "acme-ml-data.ipynb",
  "summary": "A notebook tour of aws-analyzer's S3 view: every bucket with its size and cost, one bucket's settings and findings, a summary of a prefix, a search for large model files, a Parquet preview and a lifecycle what-if.",
  "guide": "s3.html",
  "video": "videos/s3-tour-light.mp4",
  "scenes": [
   {
    "kind": "title",
    "dur": 3.2,
    "chapter": "Intro",
    "eyebrow": "Amazon S3",
    "icon": "images/aws/s3.svg",
    "heading": "See what's in your buckets, and what it's costing you",
    "text": "Every bucket, one bucket in plain English, what fills it, and a file read without downloading it.",
    "ahead": [
     "Every bucket",
     "One bucket",
     "What fills it",
     "Find files",
     "Preview",
     "What if"
    ]
   },
   {
    "kind": "cell",
    "dur": 3.8,
    "chapter": "Install",
    "label": "Install",
    "runs": 1.2,
    "code": "%pip install aws-analyzer",
    "outputs": [
     {
      "lines": [
       {
        "at": 0.05,
        "html": "Collecting aws-analyzer"
       },
       {
        "at": 0.2,
        "html": "&nbsp;&nbsp;Downloading aws_analyzer-0.56.0-py3-none-any.whl"
       },
       {
        "at": 0.3,
        "html": "&nbsp;&nbsp;<span class=\"bar\"></span>",
        "bar": true
       },
       {
        "at": 0.8,
        "html": "Installing collected packages: aws-analyzer"
       },
       {
        "at": 1.05,
        "html": "<span class=\"ok\">✓</span> Successfully installed aws-analyzer-0.56.0"
       }
      ]
     }
    ],
    "captions": [
     [
      0,
      "<b>Install it once</b>, in any SageMaker or Jupyter notebook. Read-only: it never changes anything in AWS."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 12.2,
    "chapter": "Every bucket",
    "step": 1,
    "steps": 6,
    "label": "1 of 6 · Every bucket",
    "code": "from aws_analyzer import S3View\n\nui = S3View()\nui.overview()",
    "outputs": [
     {
      "img": "overview",
      "w": 1476,
      "h": 1370
     }
    ],
    "captions": [
     [
      0,
      "<b>Every bucket at once</b>, with the notebook's own role. Nothing to set up."
     ],
     [
      4.08,
      "Four buckets, 4.3 TB, about <b>$86.50 a month</b>, and three with something to fix."
     ],
     [
      8.6,
      "Sizes come from CloudWatch's daily storage metrics, so no bucket is listed."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 11.6,
    "chapter": "One bucket",
    "step": 2,
    "steps": 6,
    "label": "2 of 6 · One bucket",
    "code": "ui.bucket_info(\"acme-ml-data\")",
    "outputs": [
     {
      "img": "bucket-info",
      "w": 1476,
      "h": 2432,
      "panMax": 700
     }
    ],
    "captions": [
     [
      0,
      "<b>One bucket in plain English</b>: who can read it and how it's set up."
     ],
     [
      3.14,
      "Encryption, versioning, public access and lifecycle rules as cards, not JSON."
     ],
     [
      8.0,
      "Each finding says what's wrong and <b>what to do about it</b>."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 14.5,
    "chapter": "What fills it",
    "step": 3,
    "steps": 6,
    "label": "3 of 6 · What fills it",
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
      0,
      "<b>What's in there?</b> A summary of everything under a prefix."
     ],
     [
      3.2,
      "91% of the objects are under 1 MB, and 500 are in Glacier: <b>each finding is priced</b>."
     ],
     [
      10.9,
      "Then where the space goes, folder by folder."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 11.8,
    "chapter": "Find files",
    "step": 4,
    "steps": 6,
    "label": "4 of 6 · Find files",
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
      0,
      "<b>Find</b> model files over 4 GB from the last 90 days, by pattern, size and age."
     ],
     [
      4.6,
      "Five matches, 34.7 GB, with their storage class and age."
     ],
     [
      8.2,
      "And the next call to run, <b>filled in</b> for the first one."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 11.7,
    "chapter": "Preview",
    "step": 5,
    "steps": 6,
    "label": "5 of 6 · Preview",
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
      0,
      "<b>Look inside a file</b> without downloading it."
     ],
     [
      4.48,
      "A Parquet file: only its footer and first row group are read."
     ],
     [
      8.1,
      "Rows, columns, row groups and the schema, <b>in the notebook</b>."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 13.3,
    "chapter": "What if",
    "step": 6,
    "steps": 6,
    "label": "6 of 6 · What if",
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
      0,
      "<b>What would a lifecycle rule save?</b> Try it before you write it."
     ],
     [
      4.6,
      "Moving training outputs to cheaper storage saves <b>$11.95 a month</b>, and pays for itself in under a month."
     ],
     [
      9.7,
      "Then the rule's JSON to copy. <b>The tool never changes the bucket.</b>"
     ]
    ]
   },
   {
    "kind": "end",
    "dur": 6.5,
    "chapter": "Your turn",
    "eyebrow": "Amazon S3",
    "icon": "images/aws/s3.svg",
    "heading": "Your turn: one line each.",
    "calls": [
     "ui.overview()",
     "ui.bucket_info(…)",
     "ui.summary(…)",
     "ui.find(…)",
     "ui.preview(…)",
     "ui.what_if(…)",
     "ui.help()"
    ],
    "url": "utkarsh5026.github.io/aws-analyzer/s3.html"
   }
  ]
 },
 "dynamodb": {
  "eyebrow": "Amazon DynamoDB",
  "notebook": "acme-app.ipynb",
  "summary": "A notebook tour of aws-analyzer's DynamoDB view: every table with its cost, one table's keys and indexes, a query on one customer's orders, a filtered scan with what it read, and the attributes the items really hold.",
  "guide": "dynamodb.html",
  "video": "videos/dynamodb-tour-light.mp4",
  "scenes": [
   {
    "kind": "title",
    "dur": 3.2,
    "chapter": "Intro",
    "eyebrow": "Amazon DynamoDB",
    "icon": "images/aws/dynamodb.svg",
    "heading": "Read your DynamoDB tables as plain tables",
    "text": "No more {\"S\": ...}: items as rows, the shape of a table, and what each read cost.",
    "ahead": [
     "Every table",
     "One table",
     "Query",
     "Scan",
     "Schema"
    ]
   },
   {
    "kind": "cell",
    "dur": 3.8,
    "chapter": "Install",
    "label": "Install",
    "runs": 1.2,
    "code": "%pip install aws-analyzer",
    "outputs": [
     {
      "lines": [
       {
        "at": 0.05,
        "html": "Collecting aws-analyzer"
       },
       {
        "at": 0.2,
        "html": "&nbsp;&nbsp;Downloading aws_analyzer-0.56.0-py3-none-any.whl"
       },
       {
        "at": 0.3,
        "html": "&nbsp;&nbsp;<span class=\"bar\"></span>",
        "bar": true
       },
       {
        "at": 0.8,
        "html": "Installing collected packages: aws-analyzer"
       },
       {
        "at": 1.05,
        "html": "<span class=\"ok\">✓</span> Successfully installed aws-analyzer-0.56.0"
       }
      ]
     }
    ],
    "captions": [
     [
      0,
      "<b>Install it once</b>, in any SageMaker or Jupyter notebook. Read-only: it never changes anything in AWS."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 12.3,
    "chapter": "Every table",
    "step": 1,
    "steps": 5,
    "label": "1 of 5 · Every table",
    "code": "from aws_analyzer import DynamoDBView\n\nui = DynamoDBView()\nui.tables()",
    "outputs": [
     {
      "img": "dynamodb-tables",
      "w": 1476,
      "h": 1346,
      "panMax": 650
     }
    ],
    "captions": [
     [
      0,
      "<b>Every table in the region</b>: items, size, billing and cost."
     ],
     [
      4.39,
      "Four tables, about <b>$264 a month</b>, and three with warnings."
     ],
     [
      8.7,
      "Each warning comes with the command that fixes it."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 12.7,
    "chapter": "One table",
    "step": 2,
    "steps": 5,
    "label": "2 of 5 · One table",
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
      0,
      "<b>One table</b>: its keys, indexes, capacity and what it costs."
     ],
     [
      2.98,
      "For an on-demand table, the cost includes reads and writes at the last day's rate."
     ],
     [
      9.1,
      "Each index comes with the <b><code>query(...)</code> call</b> that uses it."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 11.8,
    "chapter": "Query",
    "step": 3,
    "steps": 5,
    "label": "3 of 5 · Query",
    "code": "ui.query(\"acme-app\", \"CUSTOMER#1042\", sort=(\"begins_with\", \"ORDER#\"), n=8,\n         attributes=[\"status\", \"created_at\", \"total\", \"currency\", \"coupon\"])",
    "outputs": [
     {
      "img": "dynamodb-query",
      "w": 1476,
      "h": 841
     }
    ],
    "captions": [
     [
      0,
      "<b>Query</b> one customer's orders: the key's value as you'd say it."
     ],
     [
      4.6,
      "Items as rows, not <code>{\"S\": ...}</code>, with the <b>read units it cost</b>."
     ],
     [
      8.2,
      "Filters on each column, and <code>more()</code> for the next page."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 11.8,
    "chapter": "Scan",
    "step": 4,
    "steps": 5,
    "label": "4 of 5 · Scan",
    "code": "ui.scan(\"acme-app\", 10, where={\"status\": \"failed\", \"total\": (\">\", 300)},\n        attributes=[\"status\", \"created_at\", \"total\", \"failure_reason\"])",
    "outputs": [
     {
      "img": "dynamodb-scan-filter",
      "w": 1476,
      "h": 1075
     }
    ],
    "captions": [
     [
      0,
      "<b>Scan with a filter</b>: failed orders over $300."
     ],
     [
      4.6,
      "It read 1,619 items to return 10, and <b>says so</b>: a filter runs after the read."
     ],
     [
      8.2,
      "So you know what a query on a key or index would save."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 14.8,
    "chapter": "Schema",
    "step": 5,
    "steps": 5,
    "label": "5 of 5 · Schema",
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
      0,
      "<b>What do the items really hold?</b> Every attribute, its types and how often it's set."
     ],
     [
      2.86,
      "An old import stored some zip codes as numbers, so a filter on them <b>silently misses</b> those."
     ],
     [
      11.2,
      "Examples of each attribute, without reading the whole table."
     ]
    ]
   },
   {
    "kind": "end",
    "dur": 6.5,
    "chapter": "Your turn",
    "eyebrow": "Amazon DynamoDB",
    "icon": "images/aws/dynamodb.svg",
    "heading": "Your turn: one line each.",
    "calls": [
     "ui.tables()",
     "ui.table_info(…)",
     "ui.query(…)",
     "ui.scan(…)",
     "ui.schema(…)",
     "ui.help()"
    ],
    "url": "utkarsh5026.github.io/aws-analyzer/dynamodb.html"
   }
  ]
 },
 "bedrock_kb": {
  "eyebrow": "Bedrock Knowledge Bases",
  "notebook": "support-docs.ipynb",
  "summary": "A notebook tour of aws-analyzer's Bedrock Knowledge Bases view: every knowledge base, one in plain English, its syncs and why one failed, files changed since the last sync, a search and an answer with its sources.",
  "guide": "bedrock_kb.html",
  "video": "videos/bedrock_kb-tour-light.mp4",
  "scenes": [
   {
    "kind": "title",
    "dur": 3.2,
    "chapter": "Intro",
    "eyebrow": "Bedrock Knowledge Bases",
    "icon": "images/aws/bedrock.svg",
    "heading": "Find out why your knowledge base answered that",
    "text": "What failed to index, what a question really retrieves, and how well an answer is backed by its sources.",
    "ahead": [
     "Every KB",
     "One KB",
     "Syncs",
     "Unsynced",
     "Search",
     "Ask"
    ]
   },
   {
    "kind": "cell",
    "dur": 3.8,
    "chapter": "Install",
    "label": "Install",
    "runs": 1.2,
    "code": "%pip install aws-analyzer",
    "outputs": [
     {
      "lines": [
       {
        "at": 0.05,
        "html": "Collecting aws-analyzer"
       },
       {
        "at": 0.2,
        "html": "&nbsp;&nbsp;Downloading aws_analyzer-0.56.0-py3-none-any.whl"
       },
       {
        "at": 0.3,
        "html": "&nbsp;&nbsp;<span class=\"bar\"></span>",
        "bar": true
       },
       {
        "at": 0.8,
        "html": "Installing collected packages: aws-analyzer"
       },
       {
        "at": 1.05,
        "html": "<span class=\"ok\">✓</span> Successfully installed aws-analyzer-0.56.0"
       }
      ]
     }
    ],
    "captions": [
     [
      0,
      "<b>Install it once</b>, in any SageMaker or Jupyter notebook. Read-only: it never changes anything in AWS."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 12.4,
    "chapter": "Every KB",
    "step": 1,
    "steps": 6,
    "label": "1 of 6 · Every KB",
    "code": "from aws_analyzer import BedrockKBView\n\nui = BedrockKBView()\nui.kbs()",
    "outputs": [
     {
      "img": "bedrock-kbs",
      "w": 1476,
      "h": 1605,
      "panMax": 650
     }
    ],
    "captions": [
     [
      0,
      "<b>Every knowledge base in the region</b>, with its status and vector store."
     ],
     [
      4.36,
      "One is FAILED, one data source never synced, and idle Serverless costs <b>$350 a month</b>."
     ],
     [
      8.8,
      "Every warning, one knowledge base at a time."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 12.4,
    "chapter": "One KB",
    "step": 2,
    "steps": 6,
    "label": "2 of 6 · One KB",
    "code": "ui.kb_info(\"support-docs\")",
    "outputs": [
     {
      "img": "bedrock-kb-info",
      "w": 1476,
      "h": 2320,
      "panMax": 800
     }
    ],
    "captions": [
     [
      0,
      "<b>One knowledge base in plain English.</b>"
     ],
     [
      3.01,
      "The findings first: two documents <b>failed to index</b>, and the call that lists them."
     ],
     [
      8.8,
      "Then each data source's chunking, parsing and deletion policy as sentences."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 10.2,
    "chapter": "Syncs",
    "step": 3,
    "steps": 6,
    "label": "3 of 6 · Syncs",
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
      0,
      "<b>Its syncs</b>: when each ran and what it did."
     ],
     [
      2.95,
      "Why a sync failed, <b>in the same row</b>."
     ],
     [
      6.6,
      "And the command that starts the next one. <b>You run it; the tool never does.</b>"
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 10.2,
    "chapter": "Unsynced",
    "step": 4,
    "steps": 6,
    "label": "4 of 6 · Unsynced",
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
      0,
      "<b>Which files changed since the last sync?</b>"
     ],
     [
      3.04,
      "Two files changed after it, so searches and answers <b>don't see those changes yet</b>."
     ],
     [
      6.6,
      "So you know when to sync, and what it would pick up."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 11.3,
    "chapter": "Search",
    "step": 5,
    "steps": 6,
    "label": "5 of 6 · Search",
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
      0,
      "<b>Search it</b> the way your app does, and see what comes back."
     ],
     [
      4.11,
      "Two passages from the refund policy, then three weaker ones, each with its score."
     ],
     [
      7.7,
      "The question's words are <b>highlighted</b> in each passage."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 11.2,
    "chapter": "Ask",
    "step": 6,
    "steps": 6,
    "label": "6 of 6 · Ask",
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
      0,
      "<b>Ask a question</b> and read the answer with its sources."
     ],
     [
      4.01,
      "Each sentence is marked with <b>the passage it came from</b>."
     ],
     [
      7.6,
      "And what the answer cost, with the next call to try."
     ]
    ]
   },
   {
    "kind": "end",
    "dur": 6.5,
    "chapter": "Your turn",
    "eyebrow": "Bedrock Knowledge Bases",
    "icon": "images/aws/bedrock.svg",
    "heading": "Your turn: one line each.",
    "calls": [
     "ui.kbs()",
     "ui.kb_info(…)",
     "ui.syncs(…)",
     "ui.unsynced(…)",
     "ui.search(…)",
     "ui.ask(…)",
     "ui.help()"
    ],
    "url": "utkarsh5026.github.io/aws-analyzer/bedrock_kb.html"
   }
  ]
 },
 "sagemaker_env": {
  "eyebrow": "Amazon SageMaker",
  "notebook": "churn-analysis.ipynb",
  "summary": "A notebook tour of aws-analyzer's SageMaker view: this notebook's cost and idle GPU, what fills its disk, everything still running and billing, why a training job failed, idle endpoints and a broken pipeline run.",
  "guide": "sagemaker_env.html",
  "video": "videos/sagemaker_env-tour-light.mp4",
  "scenes": [
   {
    "kind": "title",
    "dur": 3.2,
    "chapter": "Intro",
    "eyebrow": "Amazon SageMaker",
    "icon": "images/aws/sagemaker.svg",
    "heading": "Know what this notebook costs, and stop paying for idle",
    "text": "This notebook, its disk, what else is still running, and why a training job or a pipeline broke.",
    "ahead": [
     "This notebook",
     "Disk",
     "Still running",
     "Training job",
     "Endpoints",
     "Pipeline"
    ]
   },
   {
    "kind": "cell",
    "dur": 3.8,
    "chapter": "Install",
    "label": "Install",
    "runs": 1.2,
    "code": "%pip install aws-analyzer",
    "outputs": [
     {
      "lines": [
       {
        "at": 0.05,
        "html": "Collecting aws-analyzer"
       },
       {
        "at": 0.2,
        "html": "&nbsp;&nbsp;Downloading aws_analyzer-0.56.0-py3-none-any.whl"
       },
       {
        "at": 0.3,
        "html": "&nbsp;&nbsp;<span class=\"bar\"></span>",
        "bar": true
       },
       {
        "at": 0.8,
        "html": "Installing collected packages: aws-analyzer"
       },
       {
        "at": 1.05,
        "html": "<span class=\"ok\">✓</span> Successfully installed aws-analyzer-0.56.0"
       }
      ]
     }
    ],
    "captions": [
     [
      0,
      "<b>Install it once</b>, in any SageMaker or Jupyter notebook. Read-only: it never changes anything in AWS."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 13.0,
    "chapter": "This notebook",
    "step": 1,
    "steps": 6,
    "label": "1 of 6 · This notebook",
    "code": "from aws_analyzer import SageMakerView\n\nui = SageMakerView()\nui.instance()",
    "outputs": [
     {
      "img": "sagemaker-instance",
      "w": 1476,
      "h": 3004,
      "panMax": 700
     }
    ],
    "captions": [
     [
      0,
      "<b>What is this notebook costing?</b> Ask it from inside."
     ],
     [
      4.51,
      "An ml.g5.2xlarge at <b>$1.52 an hour</b>, its GPU idle, and nothing will stop it tonight."
     ],
     [
      9.4,
      "Each warning has the command or setting that fixes it."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 14.7,
    "chapter": "Disk",
    "step": 2,
    "steps": 6,
    "label": "2 of 6 · Disk",
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
      0,
      "<b>What's filling the disk?</b>"
     ],
     [
      2.7,
      "88% full, and <b>3.8 GB is a file deleted weeks ago</b>, still in the trash."
     ],
     [
      11.1,
      "What's safe to clear, folder by folder. <b>The tool deletes nothing.</b>"
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 14.7,
    "chapter": "Still running",
    "step": 3,
    "steps": 6,
    "label": "3 of 6 · Still running",
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
      0,
      "<b>What else is still running and billing?</b>"
     ],
     [
      2.7,
      "A forgotten notebook, a forgotten app and an unused endpoint:"
     ],
     [
      11.1,
      "about <b>$590 a month</b>, with the command to stop each."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 12.1,
    "chapter": "Training job",
    "step": 4,
    "steps": 6,
    "label": "4 of 6 · Training job",
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
      0,
      "<b>Why did a training job fail?</b> Read from its own logs."
     ],
     [
      3.23,
      "<code>ExecuteUserScriptError</code> becomes <b>\"add lightgbm to requirements.txt\"</b>."
     ],
     [
      8.5,
      "With the traceback it came from, and what it cost."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 9.9,
    "chapter": "Endpoints",
    "step": 5,
    "steps": 6,
    "label": "5 of 6 · Endpoints",
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
      0,
      "<b>Which endpoints does nobody call?</b>"
     ],
     [
      2.7,
      "churn-v1 hasn't been called in a month and costs <b>$83.95 a month</b>."
     ],
     [
      6.3,
      "Its traffic and errors, day by day."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 15.2,
    "chapter": "Pipeline",
    "step": 6,
    "steps": 6,
    "label": "6 of 6 · Pipeline",
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
      0,
      "<b>Where did a pipeline run break?</b>"
     ],
     [
      3.23,
      "The training step broke, and <b>the four steps after it never ran</b>."
     ],
     [
      11.6,
      "Each step's time, drawn as a graph and a waterfall."
     ]
    ]
   },
   {
    "kind": "end",
    "dur": 6.5,
    "chapter": "Your turn",
    "eyebrow": "Amazon SageMaker",
    "icon": "images/aws/sagemaker.svg",
    "heading": "Your turn: one line each.",
    "calls": [
     "ui.instance()",
     "ui.disk()",
     "ui.running()",
     "ui.training_job(…)",
     "ui.endpoints()",
     "ui.pipeline_run(…)",
     "ui.help()"
    ],
    "url": "utkarsh5026.github.io/aws-analyzer/sagemaker_env.html"
   }
  ]
 },
 "opensearch": {
  "eyebrow": "Amazon OpenSearch",
  "notebook": "vectors.ipynb",
  "summary": "A notebook tour of aws-analyzer's OpenSearch view: every domain and collection with its cost, a domain's vector indexes, one index's memory, a sample of its documents and a vector search.",
  "guide": "opensearch.html",
  "video": "videos/opensearch-tour-light.mp4",
  "scenes": [
   {
    "kind": "title",
    "dur": 3.2,
    "chapter": "Intro",
    "eyebrow": "Amazon OpenSearch",
    "icon": "images/aws/opensearch.svg",
    "heading": "See what your vector indexes really hold",
    "text": "Every domain and collection, whether the graphs fit in memory, and what a question finds.",
    "ahead": [
     "Everything",
     "Indexes",
     "One index",
     "Sample",
     "Search"
    ]
   },
   {
    "kind": "cell",
    "dur": 3.8,
    "chapter": "Install",
    "label": "Install",
    "runs": 1.2,
    "code": "%pip install aws-analyzer",
    "outputs": [
     {
      "lines": [
       {
        "at": 0.05,
        "html": "Collecting aws-analyzer"
       },
       {
        "at": 0.2,
        "html": "&nbsp;&nbsp;Downloading aws_analyzer-0.56.0-py3-none-any.whl"
       },
       {
        "at": 0.3,
        "html": "&nbsp;&nbsp;<span class=\"bar\"></span>",
        "bar": true
       },
       {
        "at": 0.8,
        "html": "Installing collected packages: aws-analyzer"
       },
       {
        "at": 1.05,
        "html": "<span class=\"ok\">✓</span> Successfully installed aws-analyzer-0.56.0"
       }
      ]
     }
    ],
    "captions": [
     [
      0,
      "<b>Install it once</b>, in any SageMaker or Jupyter notebook. Read-only: it never changes anything in AWS."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 14.8,
    "chapter": "Everything",
    "step": 1,
    "steps": 5,
    "label": "1 of 5 · Everything",
    "code": "from aws_analyzer import OpenSearchView\n\nui = OpenSearchView()\nui.overview()",
    "outputs": [
     {
      "img": "opensearch-overview",
      "w": 1476,
      "h": 1680
     }
    ],
    "captions": [
     [
      0,
      "<b>Every domain and Serverless collection</b>, with what each costs."
     ],
     [
      4.58,
      "What each domain runs on, and <b>how much memory</b> its nodes have for vector graphs."
     ],
     [
      11.2,
      "And what Serverless bills even when it's idle."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 12.0,
    "chapter": "Indexes",
    "step": 2,
    "steps": 5,
    "label": "2 of 5 · Indexes",
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
      0,
      "<b>A domain's vector indexes</b>, each in plain English."
     ],
     [
      3.01,
      "The graphs need more memory than the nodes have, so <b>searches read them from disk</b>."
     ],
     [
      8.4,
      "One index uses a deprecated engine; another has documents search can't find."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 14.3,
    "chapter": "One index",
    "step": 3,
    "steps": 5,
    "label": "3 of 5 · One index",
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
      0,
      "<b>One index</b>: its vector field, engine and memory."
     ],
     [
      3.51,
      "32-bit vectors that need <b>15.7 GB</b> with replicas,"
     ],
     [
      10.7,
      "and what on-disk mode would need instead."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 14.0,
    "chapter": "Sample",
    "step": 4,
    "steps": 5,
    "label": "4 of 5 · Sample",
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
      0,
      "<b>What do the documents look like?</b> A random sample."
     ],
     [
      3.54,
      "Unit-length vectors, none all zeros, and <b>a few passages repeated</b>."
     ],
     [
      10.4,
      "Each document's text, source and vector, side by side."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 16.6,
    "chapter": "Search",
    "step": 5,
    "steps": 5,
    "label": "5 of 5 · Search",
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
      0,
      "<b>Search it with a question</b>, the way your app does."
     ],
     [
      4.6,
      "Refund articles at cosine <b>0.74 to 0.75</b>,"
     ],
     [
      13.0,
      "the question embedded with Titan Text Embeddings V2 for less than a cent."
     ]
    ]
   },
   {
    "kind": "end",
    "dur": 6.5,
    "chapter": "Your turn",
    "eyebrow": "Amazon OpenSearch",
    "icon": "images/aws/opensearch.svg",
    "heading": "Your turn: one line each.",
    "calls": [
     "ui.overview()",
     "ui.indexes(…)",
     "ui.index_info(…)",
     "ui.sample(…)",
     "ui.search(…)",
     "ui.help()"
    ],
    "url": "utkarsh5026.github.io/aws-analyzer/opensearch.html"
   }
  ]
 },
 "lambda_functions": {
  "eyebrow": "AWS Lambda",
  "notebook": "lambda.ipynb",
  "summary": "A notebook tour of aws-analyzer's Lambda view: every function in every region with its findings, one function in full, its errors grouped by cause, how close its runs come to the limits, and its code.",
  "guide": "lambda_functions.html",
  "video": "videos/lambda_functions-tour-light.mp4",
  "scenes": [
   {
    "kind": "title",
    "dur": 3.2,
    "chapter": "Intro",
    "eyebrow": "AWS Lambda",
    "icon": "images/aws/lambda.svg",
    "heading": "Find out which Lambda failed, and why, without the console",
    "text": "Every function in every region, its errors read from its own logs, its runs and its code.",
    "ahead": [
     "Every function",
     "One function",
     "Errors",
     "Performance",
     "Code"
    ]
   },
   {
    "kind": "cell",
    "dur": 3.8,
    "chapter": "Install",
    "label": "Install",
    "runs": 1.2,
    "code": "%pip install aws-analyzer",
    "outputs": [
     {
      "lines": [
       {
        "at": 0.05,
        "html": "Collecting aws-analyzer"
       },
       {
        "at": 0.2,
        "html": "&nbsp;&nbsp;Downloading aws_analyzer-0.56.0-py3-none-any.whl"
       },
       {
        "at": 0.3,
        "html": "&nbsp;&nbsp;<span class=\"bar\"></span>",
        "bar": true
       },
       {
        "at": 0.8,
        "html": "Installing collected packages: aws-analyzer"
       },
       {
        "at": 1.05,
        "html": "<span class=\"ok\">✓</span> Successfully installed aws-analyzer-0.56.0"
       }
      ]
     }
    ],
    "captions": [
     [
      0,
      "<b>Install it once</b>, in any SageMaker or Jupyter notebook. Read-only: it never changes anything in AWS."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 14.8,
    "chapter": "Every function",
    "step": 1,
    "steps": 5,
    "label": "1 of 5 · Every function",
    "code": "from aws_analyzer import LambdaView\n\nui = LambdaView()\nui.functions(regions=\"all\")",
    "outputs": [
     {
      "img": "lambda-functions",
      "w": 1476,
      "h": 2353,
      "panMax": 900
     }
    ],
    "captions": [
     [
      0,
      "<b>Every function, in every region</b>, from one line."
     ],
     [
      4.6,
      "Two runtimes AWS no longer patches, and <b>3% of orders-etl's calls failing</b> in the last three days."
     ],
     [
      11.2,
      "Every warning names the function, says why it matters and <b>gives the fix</b>."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 15.1,
    "chapter": "One function",
    "step": 2,
    "steps": 5,
    "label": "2 of 5 · One function",
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
      0,
      "<b>One function in full</b>: its triggers, settings, calls and cost."
     ],
     [
      3.14,
      "Its findings first: an ended runtime, and calls failing <b>since its last change</b>."
     ],
     [
      11.5,
      "Further down: its logs cost <b>six times its compute</b>."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 14.9,
    "chapter": "Errors",
    "step": 3,
    "steps": 5,
    "label": "3 of 5 · Errors",
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
      0,
      "<b>Why does it fail?</b> Its errors, read from its own logs."
     ],
     [
      2.92,
      "Three different reasons, <b>grouped by cause</b>,"
     ],
     [
      11.3,
      "each with what to do and the run to look at."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 15.0,
    "chapter": "Performance",
    "step": 4,
    "steps": 5,
    "label": "4 of 5 · Performance",
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
      0,
      "<b>How close do its runs come to the limits?</b>"
     ],
     [
      3.08,
      "They use at most 182 MB of the 1,024 MB it has,"
     ],
     [
      11.4,
      "and <b>three ran into the 60 s timeout</b>."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 12.7,
    "chapter": "Code",
    "step": 5,
    "steps": 5,
    "label": "5 of 5 · Code",
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
      0,
      "<b>What's in its package?</b> Read without running it."
     ],
     [
      2.86,
      "A <b>.env file</b> anyone allowed lambda:GetFunction can read,"
     ],
     [
      9.1,
      "and 13 MB of boto3 the runtime already has."
     ]
    ]
   },
   {
    "kind": "end",
    "dur": 6.5,
    "chapter": "Your turn",
    "eyebrow": "AWS Lambda",
    "icon": "images/aws/lambda.svg",
    "heading": "Your turn: one line each.",
    "calls": [
     "ui.functions(…)",
     "ui.function_info(…)",
     "ui.errors(…)",
     "ui.performance(…)",
     "ui.code(…)",
     "ui.help()"
    ],
    "url": "utkarsh5026.github.io/aws-analyzer/lambda_functions.html"
   }
  ]
 },
 "apigateway": {
  "eyebrow": "Amazon API Gateway",
  "notebook": "apis.ipynb",
  "summary": "A notebook tour of aws-analyzer's API Gateway view: every REST, HTTP and WebSocket API with its findings, then one API's routes, where each sends a request, and why one runs out of time.",
  "guide": "apigateway.html",
  "video": "videos/apigateway-tour-light.mp4",
  "scenes": [
   {
    "kind": "title",
    "dur": 3.2,
    "chapter": "Intro",
    "eyebrow": "Amazon API Gateway",
    "icon": "images/aws/apigateway.svg",
    "heading": "See which API calls fail and why",
    "text": "Every API with its errors and open routes, then one API route by route.",
    "ahead": [
     "Every API",
     "One API"
    ]
   },
   {
    "kind": "cell",
    "dur": 3.8,
    "chapter": "Install",
    "label": "Install",
    "runs": 1.2,
    "code": "%pip install aws-analyzer",
    "outputs": [
     {
      "lines": [
       {
        "at": 0.05,
        "html": "Collecting aws-analyzer"
       },
       {
        "at": 0.2,
        "html": "&nbsp;&nbsp;Downloading aws_analyzer-0.56.0-py3-none-any.whl"
       },
       {
        "at": 0.3,
        "html": "&nbsp;&nbsp;<span class=\"bar\"></span>",
        "bar": true
       },
       {
        "at": 0.8,
        "html": "Installing collected packages: aws-analyzer"
       },
       {
        "at": 1.05,
        "html": "<span class=\"ok\">✓</span> Successfully installed aws-analyzer-0.56.0"
       }
      ]
     }
    ],
    "captions": [
     [
      0,
      "<b>Install it once</b>, in any SageMaker or Jupyter notebook. Read-only: it never changes anything in AWS."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 14.0,
    "chapter": "Every API",
    "step": 1,
    "steps": 2,
    "label": "1 of 2 · Every API",
    "code": "from aws_analyzer import APIGatewayView\n\napigw = APIGatewayView()\napigw.apis()",
    "outputs": [
     {
      "img": "apigw-apis",
      "w": 1476,
      "h": 1683,
      "panMax": 800
     }
    ],
    "captions": [
     [
      0,
      "<b>Every REST, HTTP and WebSocket API</b> in the region."
     ],
     [
      4.6,
      "orders-api fails most and runs <b>close to its time limit</b>,"
     ],
     [
      10.4,
      "partner-api has <b>two routes anyone can call</b>, and legacy-v1 had no calls."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 13.1,
    "chapter": "One API",
    "step": 2,
    "steps": 2,
    "label": "2 of 2 · One API",
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
      0,
      "<b>One API</b>, route by route."
     ],
     [
      3.08,
      "Where each route sends a request, and who's allowed to call it."
     ],
     [
      9.5,
      "<code>POST /orders</code> <b>runs out of time</b> while orders-etl may still be working."
     ]
    ]
   },
   {
    "kind": "end",
    "dur": 6.5,
    "chapter": "Your turn",
    "eyebrow": "Amazon API Gateway",
    "icon": "images/aws/apigateway.svg",
    "heading": "Your turn: one line each.",
    "calls": [
     "apigw.apis()",
     "apigw.api_info(…)",
     "apigw.help()"
    ],
    "url": "utkarsh5026.github.io/aws-analyzer/apigateway.html"
   }
  ]
 },
 "messaging": {
  "eyebrow": "SQS, SNS and EventBridge",
  "notebook": "messaging.ipynb",
  "summary": "A notebook tour of aws-analyzer's messaging view: every SQS queue, SNS topic and EventBridge rule with what failed first, a topic followed to everything it delivers to, and one queue with its dead-letter queue.",
  "guide": "messaging.html",
  "video": "videos/messaging-tour-light.mp4",
  "scenes": [
   {
    "kind": "title",
    "dur": 3.2,
    "chapter": "Intro",
    "eyebrow": "SQS, SNS and EventBridge",
    "icon": "images/aws/messaging.svg",
    "heading": "Find out where your message went",
    "text": "Every queue, topic and rule as one map, a message followed hop by hop, and one queue in full.",
    "ahead": [
     "Everything",
     "Follow it",
     "One queue"
    ]
   },
   {
    "kind": "cell",
    "dur": 3.8,
    "chapter": "Install",
    "label": "Install",
    "runs": 1.2,
    "code": "%pip install aws-analyzer",
    "outputs": [
     {
      "lines": [
       {
        "at": 0.05,
        "html": "Collecting aws-analyzer"
       },
       {
        "at": 0.2,
        "html": "&nbsp;&nbsp;Downloading aws_analyzer-0.56.0-py3-none-any.whl"
       },
       {
        "at": 0.3,
        "html": "&nbsp;&nbsp;<span class=\"bar\"></span>",
        "bar": true
       },
       {
        "at": 0.8,
        "html": "Installing collected packages: aws-analyzer"
       },
       {
        "at": 1.05,
        "html": "<span class=\"ok\">✓</span> Successfully installed aws-analyzer-0.56.0"
       }
      ]
     }
    ],
    "captions": [
     [
      0,
      "<b>Install it once</b>, in any SageMaker or Jupyter notebook. Read-only: it never changes anything in AWS."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 14.8,
    "chapter": "Everything",
    "step": 1,
    "steps": 3,
    "label": "1 of 3 · Everything",
    "code": "from aws_analyzer import MessagingView\n\nmsg = MessagingView()\nmsg.overview()",
    "outputs": [
     {
      "img": "msg-overview",
      "w": 1476,
      "h": 3952,
      "panMax": 900
     }
    ],
    "captions": [
     [
      0,
      "<b>Every queue, topic and rule</b> in the region, read as one map."
     ],
     [
      4.58,
      "What failed first: <b>42 messages</b> in a dead-letter queue, deliveries that fail."
     ],
     [
      11.2,
      "Each finding ends in the <b>command that fixes it</b>."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 12.5,
    "chapter": "Follow it",
    "step": 2,
    "steps": 3,
    "label": "2 of 3 · Follow it",
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
      0,
      "<b>Where does a message go?</b> Follow a topic hop by hop."
     ],
     [
      3.04,
      "Five places it delivers to, down to the Lambda that reads it,"
     ],
     [
      8.9,
      "and <b>two of them keep it out</b>, with the policy that does."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 14.9,
    "chapter": "One queue",
    "step": 3,
    "steps": 3,
    "label": "3 of 3 · One queue",
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
      0,
      "<b>One queue in full</b>: its backlog, readers and settings."
     ],
     [
      2.95,
      "Its dead-letter queue holds <b>42 failures</b>,"
     ],
     [
      11.3,
      "and its visibility timeout is short for its reader."
     ]
    ]
   },
   {
    "kind": "end",
    "dur": 6.5,
    "chapter": "Your turn",
    "eyebrow": "SQS, SNS and EventBridge",
    "icon": "images/aws/messaging.svg",
    "heading": "Your turn: one line each.",
    "calls": [
     "msg.overview()",
     "msg.follow(…)",
     "msg.queue_info(…)",
     "msg.help()"
    ],
    "url": "utkarsh5026.github.io/aws-analyzer/messaging.html"
   }
  ]
 },
 "stepfunctions": {
  "eyebrow": "AWS Step Functions",
  "notebook": "workflows.ipynb",
  "summary": "A notebook tour of aws-analyzer's Step Functions view: every state machine with its failure rate, order-pipeline's failures grouped by cause, one execution's waterfall and log, its definition and its graph.",
  "guide": "stepfunctions.html",
  "video": "videos/stepfunctions-tour-light.mp4",
  "scenes": [
   {
    "kind": "title",
    "dur": 3.2,
    "chapter": "Intro",
    "eyebrow": "AWS Step Functions",
    "icon": "images/aws/stepfunctions.svg",
    "heading": "See which step of a workflow failed, and read the Lambda run that broke it",
    "text": "Every state machine, its failures by cause, one execution step by step, and its graph.",
    "ahead": [
     "Every workflow",
     "Failures",
     "One execution",
     "Definition",
     "Graph"
    ]
   },
   {
    "kind": "cell",
    "dur": 3.8,
    "chapter": "Install",
    "label": "Install",
    "runs": 1.2,
    "code": "%pip install aws-analyzer",
    "outputs": [
     {
      "lines": [
       {
        "at": 0.05,
        "html": "Collecting aws-analyzer"
       },
       {
        "at": 0.2,
        "html": "&nbsp;&nbsp;Downloading aws_analyzer-0.56.0-py3-none-any.whl"
       },
       {
        "at": 0.3,
        "html": "&nbsp;&nbsp;<span class=\"bar\"></span>",
        "bar": true
       },
       {
        "at": 0.8,
        "html": "Installing collected packages: aws-analyzer"
       },
       {
        "at": 1.05,
        "html": "<span class=\"ok\">✓</span> Successfully installed aws-analyzer-0.56.0"
       }
      ]
     }
    ],
    "captions": [
     [
      0,
      "<b>Install it once</b>, in any SageMaker or Jupyter notebook. Read-only: it never changes anything in AWS."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 11.8,
    "chapter": "Every workflow",
    "step": 1,
    "steps": 5,
    "label": "1 of 5 · Every workflow",
    "code": "from aws_analyzer import StepFunctionsView\n\nsfn = StepFunctionsView()\nsfn.overview()",
    "outputs": [
     {
      "img": "sfn-overview",
      "w": 1476,
      "h": 1210
     }
    ],
    "captions": [
     [
      0,
      "<b>Every state machine</b> with its executions, failures and cost."
     ],
     [
      4.6,
      "order-pipeline's <b>4.0% failure rate</b> is the one warning,"
     ],
     [
      8.2,
      "and the failing state machines come first."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 11.0,
    "chapter": "Failures",
    "step": 2,
    "steps": 5,
    "label": "2 of 5 · Failures",
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
      0,
      "<b>Why do its executions fail?</b> Grouped by where and why."
     ],
     [
      3.14,
      "Most stop at LoadOrders with <b><code>KeyError: 'customer_id'</code></b>,"
     ],
     [
      7.4,
      "and the newest run's request ID is filled in."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 14.5,
    "chapter": "One execution",
    "step": 3,
    "steps": 5,
    "label": "3 of 5 · One execution",
    "code": "sfn.execution(\"order-pipeline/ord-7f3a\")",
    "outputs": [
     {
      "img": "sfn-execution",
      "w": 1476,
      "h": 2687,
      "panMax": 1000
     }
    ],
    "captions": [
     [
      0,
      "<b>One execution, step by step.</b>"
     ],
     [
      3.45,
      "A waterfall of each step's time, then one log:"
     ],
     [
      10.9,
      "<b>orders-etl's traceback</b> between the step that called it and the Catch that ended it."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 13.2,
    "chapter": "Definition",
    "step": 4,
    "steps": 5,
    "label": "4 of 5 · Definition",
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
      0,
      "<b>Its definition, in plain English.</b>"
     ],
     [
      3.29,
      "LoadOrders has <b>no Retry</b> for Lambda's own errors,"
     ],
     [
      9.6,
      "and the finding gives the rule to paste."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 10.9,
    "chapter": "Graph",
    "step": 5,
    "steps": 5,
    "label": "5 of 5 · Graph",
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
      0,
      "<b>The workflow as a graph</b>, with an execution drawn on it."
     ],
     [
      3.73,
      "The path ord-7f3a took, <b>where it failed</b>,"
     ],
     [
      7.3,
      "and the states it never reached."
     ]
    ]
   },
   {
    "kind": "end",
    "dur": 6.5,
    "chapter": "Your turn",
    "eyebrow": "AWS Step Functions",
    "icon": "images/aws/stepfunctions.svg",
    "heading": "Your turn: one line each.",
    "calls": [
     "sfn.overview()",
     "sfn.failures(…)",
     "sfn.execution(…)",
     "sfn.state_machine(…)",
     "sfn.graph(…)",
     "sfn.help()"
    ],
    "url": "utkarsh5026.github.io/aws-analyzer/stepfunctions.html"
   }
  ]
 },
 "athena": {
  "eyebrow": "Amazon Athena",
  "notebook": "athena.ipynb",
  "summary": "A notebook tour of aws-analyzer's Athena view: a week of queries and what they cost, the most expensive ones, why queries failed, the Glue catalog's broken tables, one table in full and the code for a query.",
  "guide": "athena.html",
  "video": "videos/athena-tour-light.mp4",
  "scenes": [
   {
    "kind": "title",
    "dur": 3.2,
    "chapter": "Intro",
    "eyebrow": "Amazon Athena",
    "icon": "images/aws/athena.svg",
    "heading": "See what your SQL over S3 costs, why it fails, and which tables to fix",
    "text": "What queries cost, why they failed, and which Glue tables point at nothing.",
    "ahead": [
     "Overview",
     "Expensive",
     "Failures",
     "Catalog",
     "One table",
     "Query code"
    ]
   },
   {
    "kind": "cell",
    "dur": 3.8,
    "chapter": "Install",
    "label": "Install",
    "runs": 1.2,
    "code": "%pip install aws-analyzer",
    "outputs": [
     {
      "lines": [
       {
        "at": 0.05,
        "html": "Collecting aws-analyzer"
       },
       {
        "at": 0.2,
        "html": "&nbsp;&nbsp;Downloading aws_analyzer-0.56.0-py3-none-any.whl"
       },
       {
        "at": 0.3,
        "html": "&nbsp;&nbsp;<span class=\"bar\"></span>",
        "bar": true
       },
       {
        "at": 0.8,
        "html": "Installing collected packages: aws-analyzer"
       },
       {
        "at": 1.05,
        "html": "<span class=\"ok\">✓</span> Successfully installed aws-analyzer-0.56.0"
       }
      ]
     }
    ],
    "captions": [
     [
      0,
      "<b>Install it once</b>, in any SageMaker or Jupyter notebook. Read-only: it never changes anything in AWS."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 14.8,
    "chapter": "Overview",
    "step": 1,
    "steps": 6,
    "label": "1 of 6 · Overview",
    "code": "from aws_analyzer import AthenaView\n\nathena = AthenaView()\nathena.overview()",
    "outputs": [
     {
      "img": "athena-overview",
      "w": 1476,
      "h": 3097,
      "panMax": 900
     }
    ],
    "captions": [
     [
      0,
      "<b>A week of queries</b> and the catalog, in one report."
     ],
     [
      4.58,
      "One dashboard query is <b>90% of the bill</b>, two tables point at nothing,"
     ],
     [
      11.2,
      "and two crawlers stopped."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 14.5,
    "chapter": "Expensive",
    "step": 2,
    "steps": 6,
    "label": "2 of 6 · Expensive",
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
      0,
      "<b>Which queries cost the most?</b>"
     ],
     [
      2.92,
      "The dashboard's <code>SELECT *</code> ran 21 times and cost <b>$22.56</b>,"
     ],
     [
      10.9,
      "grouped by query shape, whatever the literal values."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 9.9,
    "chapter": "Failures",
    "step": 3,
    "steps": 6,
    "label": "3 of 6 · Failures",
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
      0,
      "<b>Why did queries fail?</b> In plain words."
     ],
     [
      2.73,
      "A typo'd table name, a bad value in a CSV file,"
     ],
     [
      6.3,
      "and a query the scan limit stopped, <b>each with its fix</b>."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 12.9,
    "chapter": "Catalog",
    "step": 4,
    "steps": 6,
    "label": "4 of 6 · Catalog",
    "code": "athena.catalog()",
    "outputs": [
     {
      "img": "athena-catalog",
      "w": 1476,
      "h": 2129,
      "panMax": 900
     }
    ],
    "captions": [
     [
      0,
      "<b>The Glue Data Catalog</b>: every table checked against S3."
     ],
     [
      2.7,
      "<code>marketing.campaigns</code> points at a <b>bucket that's gone</b>,"
     ],
     [
      9.3,
      "and <code>sales.clicks</code> has files but no partitions."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 11.5,
    "chapter": "One table",
    "step": 5,
    "steps": 6,
    "label": "5 of 6 · One table",
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
      0,
      "<b>One table in full.</b>"
     ],
     [
      3.23,
      "Every query reads all of it,"
     ],
     [
      7.9,
      "so <b>partitioning it by date</b> is the fix, with the SQL to run."
     ]
    ]
   },
   {
    "kind": "cell",
    "dur": 11.9,
    "chapter": "Query code",
    "step": 6,
    "steps": 6,
    "label": "6 of 6 · Query code",
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
      0,
      "<b>The code to run a query</b>, and what it would read."
     ],
     [
      4.6,
      "It doesn't filter on <code>dt</code>, so it <b>reads every partition</b>."
     ],
     [
      8.3,
      "The tool never starts a query: it shows the code to copy."
     ]
    ]
   },
   {
    "kind": "end",
    "dur": 6.5,
    "chapter": "Your turn",
    "eyebrow": "Amazon Athena",
    "icon": "images/aws/athena.svg",
    "heading": "Your turn: one line each.",
    "calls": [
     "athena.overview()",
     "athena.expensive(…)",
     "athena.failures()",
     "athena.catalog()",
     "athena.table_info(…)",
     "athena.query_code(…)",
     "athena.help()"
    ],
    "url": "utkarsh5026.github.io/aws-analyzer/athena.html"
   }
  ]
 }
};
