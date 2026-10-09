---
description: "Guides for aws-analyzer: one Python file per AWS service that explains your S3 buckets, DynamoDB tables, Bedrock knowledge bases, SageMaker notebooks, OpenSearch vector indexes and Lambda functions from a SageMaker notebook, a file explorer for S3, and an explorer and a chat window for a knowledge base."
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

-   ![](images/aws/s3.svg){ .aws-icon width="40" height="40" } `s3.py`

    **Amazon S3**

    Buckets and the files in them.
    { .what }

    - Every bucket's size, monthly cost and risks
    - Folder trees, and search by name, size or date
    - Preview CSV, Parquet, JSON, Excel, PDF, Word and more
    - Cut storage costs and recover deleted files

    [Open the S3 guide →](s3.md)

-   ![](images/aws/s3.svg){ .aws-icon width="40" height="40" } `s3_explorer.py`

    **S3 file explorer**

    Click through buckets and folders, and see inside each file.
    { .what }

    - Your buckets and folders, one click at a time
    - What's inside a file, as soon as you click it
    - PDFs page by page, Word files with their pictures
    - A folder as one .zip, after checking it fits

    [Open the explorer guide →](s3_explorer.md)

-   ![](images/aws/dynamodb.svg){ .aws-icon width="40" height="40" } `dynamodb.py`

    **Amazon DynamoDB**

    Tables and the items in them.
    { .what }

    - Every table's key, size, billing and cost
    - Scan, query and get items as plain tables
    - Which attributes the items hold, and their types
    - The read units each report used; scans stop early

    [Open the DynamoDB guide →](dynamodb.md)

-   ![](images/aws/bedrock.svg){ .aws-icon width="40" height="40" } `bedrock_kb.py`

    **Amazon Bedrock Knowledge Bases**

    Knowledge bases, what they retrieve, and the answers built on them.
    { .what }

    - An explorer window: every file, and how it was indexed
    - Settings in plain English, sync health and failed files
    - Search with highlighted passages; answers with each claim linked to its source
    - Compare search settings and measure retrieval hit rate

    [Open the Knowledge Bases guide →](bedrock_kb.md)

-   ![](images/aws/bedrock.svg){ .aws-icon width="40" height="40" } `bedrock_chat.py`

    **Bedrock knowledge base chat**

    A chat window on a knowledge base, with every setting in reach.
    { .what }

    - Pick the knowledge base and the model; answers stream in
    - Each answer's citations, sources, request and response, or only the search behind it
    - Any RetrieveAndGenerate setting, and the setup as Python, JSON or an AWS CLI command
    - Test a list of questions, and see which did better after a change

    [Open the chat guide →](bedrock_chat.md)

-   ![](images/aws/sagemaker.svg){ .aws-icon width="40" height="40" } `sagemaker_env.py`

    **Amazon SageMaker**

    The notebook you're working in, and everything else SageMaker bills you for.
    { .what }

    - This notebook's type, cost so far and idle shutdown
    - Its CPU, memory, disk and GPU use right now
    - What fills the disk, and what's safe to clear
    - What's running in the region, and what looks forgotten

    [Open the SageMaker guide →](sagemaker_env.md)

-   ![](images/aws/opensearch.svg){ .aws-icon width="40" height="40" } `opensearch.py`

    **Amazon OpenSearch**

    Vector indexes in OpenSearch Service domains and Serverless collections.
    { .what }

    - Every vector field in plain English: size, engine, similarity
    - Whether the graphs fit in the memory the nodes have
    - Documents without a vector; zero or repeated vectors
    - The nearest neighbours of a question, a vector or a document

    [Open the OpenSearch guide →](opensearch.md)

-   ![](images/aws/lambda.svg){ .aws-icon width="40" height="40" } `lambda_functions.py`

    **AWS Lambda**

    Lambda functions, in one region or all of them.
    { .what }

    - Every function's runtime, triggers, calls, errors and cost
    - Runtimes losing support, and functions anyone can call
    - Errors grouped by cause, from the function's own logs
    - Memory used and cold starts, and the size that would do

    [Open the Lambda guide →](lambda_functions.md)

</div>

## Every service works the same way { #pattern }

The files don't depend on each other, so copy only the ones you need. Each guide shows how to get the file into SageMaker: upload it, fetch it from a cell, or copy it from S3 when the notebook has no internet access.

```python
from s3 import S3View      # or DynamoDBView from dynamodb, BedrockKBView from bedrock_kb, SageMakerView from sagemaker_env,
                           # OpenSearchView from opensearch, LambdaView from lambda_functions

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
