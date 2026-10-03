---
description: "Guides for aws-analyzer: one Python file per AWS service that explains your S3 buckets, DynamoDB tables, Bedrock knowledge bases and SageMaker notebooks from a SageMaker notebook."
hide:
  - toc
---

# Understand your AWS data from a SageMaker notebook

One Python file per AWS service. Drop it next to your notebook and get readable reports: what's there, what it costs, and what to do next, without leaving Jupyter.
{ .lede }

<ul class="pills">
  <li>One file per service, boto3 only</li>
  <li>Read-only: never changes your data</li>
  <li>Plain text outside Jupyter</li>
</ul>

## Guides { #services }

Each guide covers setting up in SageMaker, every command with examples of its output, and the IAM permissions it needs.

<!-- One card per service. A new analyzer gets its own guide (docs/<service>.md), a card here and an entry in mkdocs.yml's nav. -->
<div class="grid cards services" markdown>

-   `s3.py`

    **Amazon S3**

    Buckets and the files in them.
    { .what }

    - Every bucket's size, monthly cost and risks
    - Click through folders like a file explorer, or search them
    - Preview CSV, Parquet, JSON, Excel, PDF, Word and more
    - Cut storage costs and recover deleted files

    [Open the S3 guide →](s3.md)

-   `dynamodb.py`

    **Amazon DynamoDB**

    Tables and the items in them.
    { .what }

    - Every table's key, size, billing and cost
    - Scan, query and get items as plain tables
    - Which attributes the items hold, and their types
    - The read units each report used; scans stop early

    [Open the DynamoDB guide →](dynamodb.md)

-   `bedrock_kb.py`

    **Amazon Bedrock Knowledge Bases**

    Knowledge bases, what they retrieve, and the answers built on them.
    { .what }

    - Settings in plain English, sync health and failed documents
    - Search with sources, pages and highlighted passages
    - Answers with each claim linked to its source
    - Compare search settings and measure retrieval hit rate

    [Open the Knowledge Bases guide →](bedrock_kb.md)

-   `sagemaker_env.py`

    **Amazon SageMaker**

    The notebook you're working in, and everything else SageMaker bills you for.
    { .what }

    - This notebook's type, cost so far and idle shutdown
    - Its CPU, memory, disk and GPU use right now
    - What fills the disk, and what's safe to clear
    - What's running in the region, and what looks forgotten

    [Open the SageMaker guide →](sagemaker_env.md)

</div>

## Every service works the same way { #pattern }

The files don't depend on each other, so copy only the ones you need. Each guide shows how to get the file into SageMaker: upload it, fetch it from a cell, or copy it from S3 when the notebook has no internet access.

```python
from s3 import S3View      # or DynamoDBView from dynamodb, BedrockKBView from bedrock_kb, SageMakerView from sagemaker_env

ui = S3View()              # uses the notebook's IAM role; nothing to configure
ui.help()                  # every command, grouped by task; ui.help("name") shows one in full
s3 = ui.core               # the analyzer behind the view: returns data instead of a report
```

Every report reads the same way: cards with the numbers that matter (amber or red when something needs a look), findings that each end in what to do, the tables of detail, and a **Next** row of the commands worth running next, with the arguments already filled in. One click on any command in a report selects all of it, ready to copy.

<script>
// This page used to be the S3 guide, so old links to its sections (e.g. /#costs) go to where they are now:
// any #section that isn't on this page.
(function () {
  var id = decodeURIComponent(location.hash.slice(1));
  if (id && !document.getElementById(id)) location.replace("s3.html" + location.hash);
})();
</script>
