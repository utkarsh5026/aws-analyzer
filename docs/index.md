---
description: "aws-analyzer reads your S3 buckets, DynamoDB tables, Bedrock knowledge bases, SageMaker notebooks, OpenSearch vector indexes and Lambda functions from a SageMaker notebook, and tells you in plain English what's there, what it costs, what's wrong and what to run next."
hide:
  - navigation
  - toc
---

<div class="home" markdown>

<div class="hero" markdown>

<div markdown>

<p class="chip"><b>Open source</b> Read-only, and boto3 is all it needs</p>

# AWS, <span class="accent">explained</span> in your notebook

You work in a SageMaker notebook, not the AWS console. aws-analyzer reads your buckets, tables, knowledge bases, notebooks, vector indexes and Lambda functions, and tells you what's there, what it costs, what's wrong, and the exact call to run next.
{ .lede }

```bash
%pip install aws-analyzer
```

<p class="actions">
<a class="md-button md-button--primary" href="s3.html">Start with your S3 buckets</a>
<a class="md-button" href="#services">Pick a service</a>
</p>

<p class="works"><span>Works with</span>
<a href="s3.html" title="Amazon S3"><img src="images/aws/s3.svg" alt="Amazon S3" width="32" height="32"></a>
<a href="dynamodb.html" title="Amazon DynamoDB"><img src="images/aws/dynamodb.svg" alt="Amazon DynamoDB" width="32" height="32"></a>
<a href="bedrock_kb.html" title="Bedrock Knowledge Bases"><img src="images/aws/bedrock.svg" alt="Bedrock Knowledge Bases" width="32" height="32"></a>
<a href="sagemaker_env.html" title="Amazon SageMaker"><img src="images/aws/sagemaker.svg" alt="Amazon SageMaker" width="32" height="32"></a>
<a href="opensearch.html" title="Amazon OpenSearch"><img src="images/aws/opensearch.svg" alt="Amazon OpenSearch" width="32" height="32"></a>
<a href="lambda_functions.html" title="AWS Lambda"><img src="images/aws/lambda.svg" alt="AWS Lambda" width="32" height="32"></a>
<a href="stepfunctions.html" title="AWS Step Functions"><img src="images/aws/stepfunctions.svg" alt="AWS Step Functions" width="32" height="32"></a>
</p>

</div>

<div class="nb" role="img" aria-label="A notebook cell running ui.instance(): the space churn-analysis on an ml.g5.2xlarge at $1.52 an hour, with idle shutdown off and the GPU idle, and a finding that an ml.m5.2xlarge without a GPU would cost up to $773.07 a month less">
<div class="nb-bar"><span class="tab">churn-analysis.ipynb</span><span class="kernel">Python 3</span></div>
<div class="nb-in"><span class="prompt">[1]:</span><span class="call" style="--aa-chars:13">ui.instance()</span></div>
<div class="nb-out">
<p class="r-title">JupyterLab space churn-analysis</p>
<p class="r-sub">us-east-1 · the notebook this code runs in · running for 2d 5h</p>
<div class="r-cards">
<div><span>Instance</span><b>ml.g5.2xlarge</b></div>
<div><span>Price</span><b>$1.52/hour</b></div>
<div class="warn"><span>Idle shutdown</span><b>off</b></div>
<div class="warn"><span>GPU</span><b>idle</b></div>
</div>
<p class="r-finding"><strong>This ml.g5.2xlarge costs $1.52/hour, and its GPU is idle right now.</strong> If this notebook doesn't use the GPU, ml.m5.2xlarge has as many vCPUs and as much memory without one: <mark>$1.06/hour less, up to $773.07/month</mark>. Switch when the notebook is stopped.</p>
<p class="r-next">Next <code>disk('/home/sagemaker-user')</code> <code>running()</code></p>
</div>
</div>

</div>

Plain English
{ .kicker }

## It reads AWS so you don't have to { #translate }

The console and the API hand you JSON, log lines and metrics. aws-analyzer turns them into sentences that say what it means for you, put a price on it, and name the next step. These are lines from its reports.
{ .section-lede }

<div class="tr">
<div class="head">What AWS gives you</div>
<div class="head">What aws-analyzer says</div>

<div class="raw"><pre><code>{
  "Sid": "PartnerRead",
  "Effect": "Allow",
  "Principal": {"AWS": "arn:aws:iam::210987654321:root"},
  "Action": ["s3:GetObject", "s3:ListBucket"],
  "Resource": ["arn:aws:s3:::acme-ml-data",
               "arn:aws:s3:::acme-ml-data/curated/*"]
}</code></pre></div>
<div class="arrow" aria-hidden="true">→</div>
<div class="said">
<p>Statement PartnerRead gives <mark>other AWS accounts access (210987654321)</mark>: read files, list files.</p>
<table class="kv">
<tr><td>Who</td><td>account 210987654321</td></tr>
<tr><td>Can</td><td>read files, list files</td></tr>
<tr><td>On</td><td>bucket acme-ml-data, s3://acme-ml-data/curated/*</td></tr>
</table>
<p class="where">From <code>bucket_info()</code> in the <a href="s3.html#buckets">S3 guide</a>. <code>policy()</code> shows every statement this way, and its JSON.</p>
</div>

<div class="raw"><pre><code>[ERROR] ClientError: An error occurred (AccessDeniedException)
when calling the PutItem operation: User: arn:aws:sts::123456789012:
assumed-role/acme-lambda-role/orders-etl is not authorized to perform:
dynamodb:PutItem on resource: arn:aws:dynamodb:us-east-1:
123456789012:table/orders</code></pre></div>
<div class="arrow" aria-hidden="true">→</div>
<div class="said">
<p>Its code was refused access twice (the last 6h ago): its role <code>acme-lambda-role</code> <mark>isn't allowed dynamodb:PutItem</mark> on <code>arn:aws:dynamodb:us-east-1:123456789012:table/orders</code>. Add the permission to the role's policy in IAM.</p>
<p class="where">From <code>errors('orders-etl')</code> in the <a href="lambda_functions.html#errors">Lambda guide</a>, which groups a function's errors by cause and links each to the run that shows it.</p>
</div>

<div class="raw"><pre><code>{"pk": {"S": "USER#0"}, "sk": {"S": "ORDER#0000"},
 "status": {"S": "shipped"}, "total": {"N": "69.83"},
 "lines": {"L": [{"M": {"sku": {"S": "SKU-046"},
                         "qty": {"N": "1"}}}]}}</code></pre></div>
<div class="arrow" aria-hidden="true">→</div>
<div class="said">
<table class="kv">
<tr><td>pk (partition key)</td><td>USER#0</td></tr>
<tr><td>status</td><td>shipped</td></tr>
<tr><td>total</td><td>69.83</td></tr>
<tr><td>lines</td><td>1 element</td></tr>
<tr><td class="in">sku</td><td>SKU-046</td></tr>
</table>
<p class="where">From <code>get('orders', 'USER#0', 'ORDER#0000')</code> in the <a href="dynamodb.html#items">DynamoDB guide</a>, with what that read cost: 1 read unit.</p>
</div>
</div>

Every guide
{ .kicker }

## Start from the question you have { #services }

Each guide sets you up in SageMaker, walks through every command with real output, and lists the IAM permissions it needs.
{ .section-lede }

<!-- One line per service. A new analyzer gets its own guide (docs/<service>.md), a line here and an entry in mkdocs.yml's nav. -->
<ul class="svc">
<li><a href="s3.html">
<img class="aws-icon" src="images/aws/s3.svg" alt="" width="40" height="40">
<span class="who"><span class="name">Amazon S3</span><code>s3.py</code></span>
<span class="asks"><span>What's in this bucket, and what is it costing me?</span><span>What's inside this Parquet, Excel or PDF file?</span><span>Someone deleted a file. Can I get it back?</span></span>
<span class="go" aria-hidden="true">›</span>
</a></li>
<li><a href="s3_explorer.html">
<img class="aws-icon" src="images/aws/s3.svg" alt="" width="40" height="40">
<span class="who"><span class="name">S3 file explorer</span><code>s3_explorer.py</code></span>
<span class="asks"><span>Can I just click through my buckets like folders?</span><span>What's in this file, without downloading it?</span></span>
<span class="go" aria-hidden="true">›</span>
</a></li>
<li><a href="dynamodb.html">
<img class="aws-icon" src="images/aws/dynamodb.svg" alt="" width="40" height="40">
<span class="who"><span class="name">Amazon DynamoDB</span><code>dynamodb.py</code></span>
<span class="asks"><span>What does an item in this table actually look like?</span><span>Which attributes do the items really have?</span><span>How much did that scan just cost?</span></span>
<span class="go" aria-hidden="true">›</span>
</a></li>
<li><a href="bedrock_kb.html">
<img class="aws-icon" src="images/aws/bedrock.svg" alt="" width="40" height="40">
<span class="who"><span class="name">Bedrock Knowledge Bases</span><code>bedrock_kb.py</code></span>
<span class="asks"><span>Why did my knowledge base answer that?</span><span>Which files failed to index, and why?</span><span>Does a search find the passage it should?</span></span>
<span class="go" aria-hidden="true">›</span>
</a></li>
<li><a href="bedrock_chat.html">
<img class="aws-icon" src="images/aws/bedrock.svg" alt="" width="40" height="40">
<span class="who"><span class="name">Bedrock chat</span><code>bedrock_chat.py</code></span>
<span class="asks"><span>Which settings give the best answers to my test questions?</span><span>What exactly is sent to Bedrock?</span></span>
<span class="go" aria-hidden="true">›</span>
</a></li>
<li><a href="sagemaker_env.html">
<img class="aws-icon" src="images/aws/sagemaker.svg" alt="" width="40" height="40">
<span class="who"><span class="name">Amazon SageMaker</span><code>sagemaker_env.py</code></span>
<span class="asks"><span>What is this notebook costing me, and does it stop when I leave?</span><span>Why is the disk full?</span><span>What did I leave running?</span></span>
<span class="go" aria-hidden="true">›</span>
</a></li>
<li><a href="opensearch.html">
<img class="aws-icon" src="images/aws/opensearch.svg" alt="" width="40" height="40">
<span class="who"><span class="name">Amazon OpenSearch</span><code>opensearch.py</code></span>
<span class="asks"><span>Do my vectors fit in the memory the nodes have?</span><span>Which documents have no vector?</span><span>What's nearest to this question?</span></span>
<span class="go" aria-hidden="true">›</span>
</a></li>
<li><a href="lambda_functions.html">
<img class="aws-icon" src="images/aws/lambda.svg" alt="" width="40" height="40">
<span class="who"><span class="name">AWS Lambda</span><code>lambda_functions.py</code></span>
<span class="asks"><span>Which function failed last night, and why?</span><span>Which runtimes lose support soon?</span><span>Is it paying for memory it never uses?</span></span>
<span class="go" aria-hidden="true">›</span>
</a></li>
<li><a href="stepfunctions.html">
<img class="aws-icon" src="images/aws/stepfunctions.svg" alt="" width="40" height="40">
<span class="who"><span class="name">AWS Step Functions</span><code>stepfunctions</code></span>
<span class="asks"><span>Which step of my workflow failed, and why?</span><span>What did that Lambda log in the run that broke it?</span><span>Can I redrive the failed ones?</span></span>
<span class="go" aria-hidden="true">›</span>
</a></li>
<li><a href="agent.html">
<img class="aws-icon" src="images/aws/lambda.svg" alt="" width="40" height="40">
<span class="who"><span class="name">Ask in plain English</span><code>agent/</code></span>
<span class="asks"><span>Were last night's uploads to acme-raw processed?</span><span>Any question about Lambda and S3, in a sentence.</span><span>Remembers where things run, next time.</span></span>
<span class="go" aria-hidden="true">›</span>
</a></li>
</ul>

One pattern
{ .kicker }

## Every report reads the same way { #pattern }

Learn one and you can read them all. Each report puts the answer first and ends with what to do about it.
{ .section-lede }

<div class="anatomy" markdown>

<div markdown>

1. **The answer**
   Cards with the few numbers that matter, in units you know: sizes, dollars a month, how long ago. A card turns amber when a warning below is about it.
2. **What needs a look**
   Findings, warnings first. Each one says what's wrong, why it matters, what it costs, and what to change.
3. **The detail**
   Tables you can sort, filter and trim, and the raw JSON folded away for when you want it.
4. **What to run next**
   Two or three calls with the arguments already filled in. One click selects a call, ready to copy.

</div>

<figure markdown>
![overview(): a table of four buckets with objects, size, estimated monthly cost, versioning, encryption and public access, and a list of warnings](images/overview-light.webp#only-light){ width="984" height="700" loading=lazy }
![overview(): a table of four buckets with objects, size, estimated monthly cost, versioning, encryption and public access, and a list of warnings](images/overview-dark.webp#only-dark){ width="984" height="700" loading=lazy }
<figcaption><code>ui.overview()</code> in the S3 guide: every bucket, what it costs, and what needs a look.</figcaption>
</figure>

</div>

Every service starts the same way. It uses the notebook's IAM role, so there's nothing to configure:

```python
from aws_analyzer import S3View  # or DynamoDBView, BedrockKBView, SageMakerView, OpenSearchView, LambdaView

ui = S3View()              # uses the notebook's IAM role
ui.help()                  # every command, grouped by task; ui.help("name") shows one in full
s3 = ui.core               # the analyzer behind the view: returns data instead of a report
```

Promises
{ .kicker }

## What it will never do { #promises }

<div class="never">
<div>
<p>Change anything in your account</p>
<p>It only reads. When a finding suggests a fix, it shows you the command and leaves running it to you.</p>
</div>
<div>
<p>Ask you to set it up</p>
<p>It uses the notebook's IAM role and region. A missing permission becomes a note naming it, and the rest of the report still shows.</p>
</div>
<div>
<p>Need more than boto3</p>
<p>SageMaker already has it. pandas, file readers and widgets are used when they're there, and a report says what to install when one is missing.</p>
</div>
<div>
<p>Pretend an estimate is a bill</p>
<p>Costs say which prices they used, partial results say where they stopped, and long scans stop early unless you ask for more.</p>
</div>
</div>

Time zones
{ .kicker }

## Times in your time zone { #timezone }

Times are in UTC until you pick a zone. Then every report and window, for every service, shows its times in your zone and says which one (`Last modified (IST)`, `since 2024-05-01 14:02 IST`), and dates you type in (`since="2024-05-01 10:00"`) are read there too:

```python
from aws_analyzer import set_timezone

set_timezone("Asia/Kolkata")   # or its city, "kolkata"; "America/New_York", "+05:30", "UTC" to go back
```

`set_timezone("local")` uses the machine's own zone, which on SageMaker is UTC, so name yours there. The `AWS_ANALYZER_TZ` environment variable picks the zone for every notebook (`AWS_ANALYZER_TZ=Asia/Kolkata`). CloudWatch's daily numbers stay in UTC days, since that's how AWS adds them up, and their columns say so (`Day (UTC)`).

Themes
{ .kicker }

## Pick how reports look { #themes }

Reports come in five themes, each made for a light or a dark notebook. `console` is the default. Pick another and every report from then on uses it, while the ones already in the notebook keep theirs:

```python
from aws_analyzer import set_theme

set_theme("soft")   # "console", "soft", "editorial", "brand" or "classic"; None goes back to the default
```

=== "Console"

    ![The top of a Lambda function's report in the console theme: its title, the cards with its numbers and its findings](images/theme-console-light.webp#only-light){ width="984" height="560" loading=lazy }
    ![The top of a Lambda function's report in the console theme: its title, the cards with its numbers and its findings](images/theme-console-dark.webp#only-dark){ width="984" height="560" loading=lazy }

    The default: boxed panels, WARN and INFO tags, and numbers in a monospace font. Dense, like the AWS console. `set_theme("console")`

=== "Soft"

    ![The top of a Lambda function's report in the soft theme: its title, the cards with its numbers and its findings](images/theme-soft-light.webp#only-light){ width="984" height="560" loading=lazy }
    ![The top of a Lambda function's report in the soft theme: its title, the cards with its numbers and its findings](images/theme-soft-dark.webp#only-dark){ width="984" height="560" loading=lazy }

    Rounded tiles on a light fill, small capitals over the numbers and columns, and the calls to run next as tiles. `set_theme("soft")`

=== "Editorial"

    ![The top of a Lambda function's report in the editorial theme: its title, the cards with its numbers and its findings](images/theme-editorial-light.webp#only-light){ width="984" height="560" loading=lazy }
    ![The top of a Lambda function's report in the editorial theme: its title, the cards with its numbers and its findings](images/theme-editorial-dark.webp#only-dark){ width="984" height="560" loading=lazy }

    Almost no boxes: a serif title, the numbers as a row of large figures, and findings as a list. `set_theme("editorial")`

=== "Brand"

    ![The top of a Lambda function's report in the brand theme: its title, the cards with its numbers and its findings](images/theme-brand-light.webp#only-light){ width="984" height="560" loading=lazy }
    ![The top of a Lambda function's report in the brand theme: its title, the cards with its numbers and its findings](images/theme-brand-dark.webp#only-dark){ width="984" height="560" loading=lazy }

    This site's look: navy and indigo, white cards with soft shadows, and an amber highlighter on what needs a look. `set_theme("brand")`

=== "Classic"

    ![The top of a Lambda function's report in the classic theme: its title, the cards with its numbers and its findings](images/theme-classic-light.webp#only-light){ width="984" height="560" loading=lazy }
    ![The top of a Lambda function's report in the classic theme: its title, the cards with its numbers and its findings](images/theme-classic-dark.webp#only-dark){ width="984" height="560" loading=lazy }

    The look before themes: thin borders and tinted boxes. `set_theme("classic")`

The `AWS_ANALYZER_THEME` environment variable picks the theme for every notebook (`AWS_ANALYZER_THEME=editorial`). The explorer and chat windows keep their own look, and text output reads the same in every theme.

<div class="cta" markdown>

<p class="cta-title">Your first report is one line away</p>

Install it in a notebook cell, make a view, and ask. Nothing to set up, and nothing in your account changes.

```bash
%pip install aws-analyzer
```

<p class="actions">
<a class="md-button md-button--primary" href="s3.html">Start with your S3 buckets</a>
<a class="md-button" href="lambda_functions.html">See which Lambda failed</a>
</p>

</div>

</div>

<script>
// This page used to be the S3 guide, so old links to its sections (e.g. /#costs) go to where they are now:
// any #section that isn't on this page.
(function () {
  var id = decodeURIComponent(location.hash.slice(1));
  if (id && !document.getElementById(id)) location.replace("s3.html" + location.hash);
})();
</script>
