---
title: Lambda Functions Guide
description: "How to see every AWS Lambda function, in one region or all of them, from a SageMaker notebook with aws-analyzer's lambda_functions.py: runtimes and their end of support, triggers and public access, calls, errors and cost, errors grouped by cause, logs, memory and cold starts, and the code, with examples."
---

<p class="eyebrow">aws-analyzer · lambda_functions.py</p>

# See every Lambda function from a SageMaker notebook

One Python file. Drop it next to your notebook and see your Lambda functions, in one region or every one your account uses: what each one runs and when its runtime loses support, what triggers it and who else can call it, how often it ran, failed and was throttled, what it costs, and why it fails, read from its own logs, without leaving Jupyter.
{ .lede }

<ul class="pills">
  <li>One file, boto3 only</li>
  <li>Read-only: never invokes or changes a function</li>
  <li>Every region at once</li>
  <li>Plain text outside Jupyter</li>
</ul>

Every example uses acme's functions: `orders-etl`, which loads each day's orders from S3 into the warehouse, `churn-scoring`, which scores customers with a model trained in SageMaker, and `report-api`, which serves a sales report over a function URL. Use your own names. The screenshots are real output from the tool, run against [moto](https://github.com/getmoto/moto) with synthetic data.
{ .muted }

## Set up in SageMaker { #setup }

<div class="steps" markdown>

1. **Get `lambda_functions.py` next to your notebook.** Pick whichever works in your environment:

    - **Upload it.** Download [lambda_functions.py](https://raw.githubusercontent.com/utkarsh5026/aws-analyzer/main/analyzers/lambda_functions.py), then drag it into JupyterLab's file browser, in the same folder as your notebook.

    - **Fetch it from a cell**, if the notebook can reach the internet:

        ```bash
        !curl -sO https://raw.githubusercontent.com/utkarsh5026/aws-analyzer/main/analyzers/lambda_functions.py
        ```

    - **Copy it from S3**, for a notebook with no internet access (VPC-only mode). Upload it to a bucket once, then:

        ```bash
        !aws s3 cp s3://acme-ml-data/tools/lambda_functions.py .
        ```

    - Or paste the whole file into a notebook cell and run it.

2. **Import it and create the view.** It uses the notebook's IAM execution role and region, so there's nothing to configure.

    ```python
    from lambda_functions import LambdaView

    ui = LambdaView()   # uses the notebook's IAM role and region
    ui.help()           # every command, grouped by task; ui.help("errors") shows one in full
    ```

3. **Optional:** another region or AWS profile, or plain-text output.

    ```python
    from lambda_functions import LambdaAnalyzer, LambdaView

    ui = LambdaView(LambdaAnalyzer(region="eu-west-1", profile="dev"))
    ui = LambdaView(mode="text")    # plain text, e.g. in a terminal or a script
    ```

</div>

!!! note ""

    **Only boto3 is required.** The file isn't called `lambda.py` because `lambda` is a Python keyword, so `import lambda` can't work. Installed with pip, it's `from aws_analyzer import LambdaView`.

!!! note ""

    **Nothing is invoked, changed or deleted.** Where a change would help, the report shows the AWS CLI command to run instead. Environment variable values never appear in a report: only their names do, because the values often hold passwords and keys.

## Five-minute tour { #tour }

The commands you'll use most. Each one prints a report under the cell; none of them change anything.

```python
ui.functions()                              # every function in the region: runtime, triggers, calls, errors, cost
ui.functions(regions="all")                 # ...in every region your account has turned on
ui.function_info("orders-etl")              # one function in plain English, and its last 30 days
ui.errors("orders-etl")                     # its errors in the last 24 hours, grouped by cause
ui.logs("orders-etl", search="KeyError")    # the newest lines it logged
ui.performance("orders-etl")                # run times, memory used and cold starts, and the memory it needs
ui.code("orders-etl")                       # the files in its package, and the handler's source
```

**Reading a report.** Every report puts the answer first: cards with the numbers that matter (a card turns amber or red when a finding is about it), then the findings, warnings first, each ending in what to do. The tables of detail come after, and at the bottom a **Next** row of two or three commands with the arguments filled in from this report, such as `errors('orders-etl')`. One click on a command anywhere in a report, or on a code block, selects all of it, ready to copy.

**Naming a function.** Commands that work on one function take its name, `"name:alias"` or `"name:version"`, its ARN, or a link to it in the Lambda console, so you can paste what you have. Functions are regional: pass `region="eu-west-1"` for one outside the view's region (an ARN or a link already says where it is), and after `functions(regions="all")`, a name found in only one other region is looked for there.

## Every function { #functions }

`functions()` lists every function in the region with what CloudWatch counted for it over the last 30 days. It's the place to start: one table answers what you have, what's failing, what costs money and what needs attention.

```python
ui.functions(regions="all")
```

![functions(regions="all"): six functions in two of 24 regions with runtime, memory, timeout, triggers, calls, error rate, average run time, when each was last called, estimated monthly cost and warnings; a table by region with code storage and the most runs at once; and warnings about idle provisioned concurrency, runtimes past or near the end of support, rising errors, runs close to the timeout, throttled calls and a public function URL](images/lambda-functions-light.webp#only-light){ width="984" height="1247" loading=lazy }
![functions(regions="all"): six functions in two of 24 regions with runtime, memory, timeout, triggers, calls, error rate, average run time, when each was last called, estimated monthly cost and warnings; a table by region with code storage and the most runs at once; and warnings about idle provisioned concurrency, runtimes past or near the end of support, rising errors, runs close to the timeout, throttled calls and a public function URL](images/lambda-functions-dark.webp#only-dark){ width="984" height="1247" loading=lazy }
/// caption
`ui.functions(regions="all")`: two runtimes AWS no longer patches, $70 a month of provisioned concurrency for a function that never runs two at once, and 3% of calls failing in the last three days, against 0.6% over the month.
///

- **Runtime**, marked when AWS has ended its support (no more security patches) or ends it within 90 days. The dates come from AWS's published schedule (`RUNTIMES` in the file); [Runtime support](#runtimes) explains them.
- **Triggers**: the queues and streams Lambda reads for it (event source mappings), the services and accounts its resource policy lets call it (S3, EventBridge, API Gateway, a Bedrock agent...), and its function URL. **Anyone** or **URL (public)**, in amber, means anyone on the internet can run it.
- **Calls, error rate, average run time** over the last 30 days, and when it was **last called**, from CloudWatch.
- **Est. $/month**: calls and run time scaled to a month, plus provisioned concurrency and what its logs cost. [Cost](#cost) has the details.
- **Warnings** (listed under the table) cover runtimes past or near the end of support, functions anyone can invoke, provisioned concurrency that sits idle, errors that are frequent or rising, throttled calls, runs close to the timeout, and failed updates. `function_info()` shows every finding for one function.

`match="etl-*"` keeps the functions whose names match. `metrics=False` skips CloudWatch, and `details=False` skips the reads made for each function (its resource policy and provisioned concurrency), which makes accounts with hundreds of functions quicker.

### Across regions { #regions }

`functions(regions="all")` reads every region your account has turned on (from EC2's `DescribeRegions`; without that permission, every region Lambda is offered in, skipping the ones that answer that they aren't turned on). `regions=["us-east-1", "eu-west-1"]` or `regions="us-east-1, eu-west-1"` reads just those. The table gains a Region column, and a **By region** table shows each region's functions, cost, code storage against its limit, and the most runs that happened at once against the region's concurrency limit.

## One function { #function-info }

`function_info()` explains one function: everything Lambda knows about it, in plain English, and what CloudWatch counted for it, day by day.

```python
ui.function_info("orders-etl")
```

![function_info("orders-etl"): cards for its runtime, memory, timeout, architecture, calls, error rate, average and longest run, estimated cost and triggers; findings about its runtime, rising errors, a run close to the timeout, logs costing more than its compute, failed S3 events being dropped, a log group kept forever and a password in an environment variable; and tables of what it runs, what triggers it, what happens to events that fail and what it can reach](images/lambda-function-info-light.webp#only-light){ width="984" height="1400" loading=lazy }
![function_info("orders-etl"): cards for its runtime, memory, timeout, architecture, calls, error rate, average and longest run, estimated cost and triggers; findings about its runtime, rising errors, a run close to the timeout, logs costing more than its compute, failed S3 events being dropped, a log group kept forever and a password in an environment variable; and tables of what it runs, what triggers it, what happens to events that fail and what it can reach](images/lambda-function-info-dark.webp#only-dark){ width="984" height="1400" loading=lazy }
/// caption
`ui.function_info("orders-etl")`: its logs cost six times its compute, and failed S3 events are dropped because nothing catches them.
///

The report's tables:

- **What it runs**: the runtime and its support dates, the handler (which file and function Lambda calls), the package's size and layers, memory and the CPU it buys (a full vCPU at 1,769 MB), timeout, `/tmp` storage, architecture, reserved concurrency, tracing, where its logs go and how long they're kept, and whether Lambda patches its runtime itself.
- **What triggers it**: each event source mapping with its batch size, state and last result, each service or account its resource policy allows, and its function URL with its auth type.
- **When an asynchronous call fails**: for functions that S3, SNS or EventBridge call asynchronously, how many times Lambda retries a failed event, how old an event may get, and where it goes when it fails for good. With no on-failure destination or dead-letter queue, it's dropped.
- **What it can reach**: its execution role (what its code may do in AWS), its network (a VPC reaches the internet only through a NAT gateway or VPC endpoints), and the names of its environment variables.
- **Versions, aliases and provisioned concurrency**, with what each provisioned copy costs.
- **The last 30 days**, one row per day: calls, errors, throttles, average and longest run.
- **The estimated monthly cost**, by part, and the configuration as Lambda returns it, folded away, with environment values hidden.

`days=7` reads a shorter window. The findings each end in the command to run: the `update-function-configuration` call that moves it to a supported runtime or raises its timeout, the `put-retention-policy` call that stops its logs being kept forever, the `put-function-event-invoke-config` call that catches failed events.

### Runtime support { #runtimes }

AWS supports each managed runtime (`python3.12`, `nodejs22.x`, `java21`...) for a while after the language version is released, then:

| Date | What happens |
|---|---|
| End of support | AWS stops applying security patches. Functions keep running |
| Block function create | No new functions on that runtime |
| Block function update | Existing functions on it can't be updated |

The report marks a runtime **ended** after its end of support, and warns 90 days before it. It names the newest runtime for the same language, and the `update-function-configuration` command that moves the function to it: test the code on the new runtime first. A container image has no managed runtime: rebuild it on a current base image now and then.

### Who can call it { #triggers }

Lambda functions get called three ways, and the report reads all three:

| How | Where it's set | Examples |
|---|---|---|
| Lambda reads a queue or stream for it | Event source mappings | SQS, Kinesis, DynamoDB streams, Kafka |
| A service or account calls it | The function's resource policy | S3, EventBridge, API Gateway, SNS, a Bedrock agent, another account |
| Anyone calls its HTTPS address | Its function URL | With auth type `NONE`, anyone who has the URL |

**Anyone** in the triggers means its resource policy lets any AWS account invoke it, and **URL (public)** means its function URL needs no sign-in: both are warnings, since anyone can run it and you pay for every call. A service allowed without a source (an S3 permission with no `SourceArn`) is a note: that service could call it for other accounts' resources too. Functions called directly (an SDK, Step Functions, a service using its own role) leave no trace here, since their permission is on the caller's role.

## When something goes wrong { #errors }

### Errors, grouped by cause

`errors()` reads the function's own logs and groups the errors in them by cause, next to CloudWatch's count of failed and throttled calls.

```python
ui.errors("orders-etl")
ui.errors("orders-etl", since="7d")            # further back
```

![errors("orders-etl"): cards for 66 failed calls of 2,226, error lines, causes, 3 timeouts and the last error; findings that runs timed out at the 60 s limit, that the role isn't allowed dynamodb:PutItem, and that KeyError: 'customer_id' happened 8 times; and tables of errors by cause and the newest error lines with their request IDs](images/lambda-errors-light.webp#only-light){ width="984" height="1263" loading=lazy }
![errors("orders-etl"): cards for 66 failed calls of 2,226, error lines, causes, 3 timeouts and the last error; findings that runs timed out at the 60 s limit, that the role isn't allowed dynamodb:PutItem, and that KeyError: 'customer_id' happened 8 times; and tables of errors by cause and the newest error lines with their request IDs](images/lambda-errors-dark.webp#only-dark){ width="984" height="1263" loading=lazy }
/// caption
`ui.errors("orders-etl")`: three different reasons to fail, each with what to do and the run to look at.
///

Each cause gets its own advice:

- **Timeouts**: raise the timeout, or find what's slow with `performance()`. At 900 seconds there's no more room: split the work, or run it somewhere without the limit.
- **Out of memory**: Lambda killed the run. Give it more memory.
- **Code that can't load** (`Runtime.ImportModuleError`): a package missing from the deployment package or a layer, or a handler setting that doesn't match a file. `code()` shows what's in the package.
- **Access denied**: the function's role isn't allowed something its code does, named with the action and the resource.
- **Throttled or unreachable services**, and otherwise the most common error, with an example.

Each error line is matched to its run, by its request ID, or by the REPORT line that ends the run in the same log stream (Python's tracebacks don't carry the ID). `logs("orders-etl", request_id="...")` then shows that whole run.

### Logs

`logs()` shows the newest lines the function logged, 50 from the last hour, with each run's REPORT line summed up: run time, memory used and cold start. START and END lines are left out.

```python
ui.logs("orders-etl")
ui.logs("orders-etl", search="KeyError")                  # lines with that text (case-sensitive)
ui.logs("orders-etl", search='?ERROR ?WARN')              # or any CloudWatch Logs filter pattern
ui.logs("orders-etl", request_id="c0ffee00-1d2e-4f3a-9b8c-7d6e5f4a3b2c")   # one run, start to end
ui.logs("orders-etl", since="24h", n=200)
```

`request_id=` shows every line of one run, including what the code printed without the ID: everything its log stream recorded between the run's first and last line. When the window has no lines, the report says when the newest one was, and the call that reaches back that far.

### Run times, memory and cold starts { #performance }

`performance()` reads the REPORT line Lambda logs at the end of every run: how long it ran and was billed for, the memory it used, and how long a cold start spent starting up.

```python
ui.performance("orders-etl")
```

![performance("orders-etl"): cards for 70 runs, the median and slowest run times against the 60 s timeout, memory used of 1,024 MB, cold starts and timeouts; findings that 3 runs timed out, that 256 MB would do instead of 1,024 MB, and that 11% of runs were cold starts; and tables of run time and memory percentiles, how long runs take and the slowest runs](images/lambda-performance-light.webp#only-light){ width="984" height="1074" loading=lazy }
![performance("orders-etl"): cards for 70 runs, the median and slowest run times against the 60 s timeout, memory used of 1,024 MB, cold starts and timeouts; findings that 3 runs timed out, that 256 MB would do instead of 1,024 MB, and that 11% of runs were cold starts; and tables of run time and memory percentiles, how long runs take and the slowest runs](images/lambda-performance-dark.webp#only-dark){ width="984" height="1074" loading=lazy }
/// caption
`ui.performance("orders-etl")`: runs use at most 182 MB of the 1,024 MB it has, and three ran into the 60 s timeout.
///

**Choosing memory.** Lambda bills memory × run time, and memory also sets the CPU (a full vCPU at 1,769 MB). When runs use far less memory than the function has, the report suggests a smaller size that still leaves 30% room, never less than a quarter of what it has in one step, and what that would save if the run time stays the same. A function held back by its CPU may run slower with less memory, which can cost more, so try it, and run `performance()` again a day later. When runs come close to the memory or the timeout, it says so and gives the command that raises them.

## Look at the code { #code }

`code()` downloads the deployment package from the link Lambda gives (a short-lived S3 link), lists its files and folders by size, and shows the source of the file that holds the handler. Nothing in it is run, and a secrets file (`.env`, keys, credentials) is listed but its text isn't shown, because a notebook's output is easy to share.

```python
ui.code("orders-etl")
ui.code("orders-etl", "db.py")           # another file: a path, a name, or a glob like "*.py"
ui.code("orders-etl", max_size="200MB")  # packages up to 50 MB are read by default
```

![code("orders-etl"): cards for the package's size zipped and unzipped, its files, layers and handler file; a warning that the package holds a .env file and a note that it carries its own boto3 and botocore; the folders that take the space; and the source of etl.py](images/lambda-code-light.webp#only-light){ width="984" height="990" loading=lazy }
![code("orders-etl"): cards for the package's size zipped and unzipped, its files, layers and handler file; a warning that the package holds a .env file and a note that it carries its own boto3 and botocore; the folders that take the space; and the source of etl.py](images/lambda-code-dark.webp#only-dark){ width="984" height="990" loading=lazy }
/// caption
`ui.code("orders-etl")`: a .env file anyone allowed `lambda:GetFunction` can read, and 13 MB of boto3 the runtime already has.
///

The findings: a handler setting no file in the package matches, secrets files packed in it (`.env`, keys, credentials), code and layers near Lambda's 250 MB limit, boto3 bundled although the runtime has it, and caches or tests that never run. A container image isn't pulled: the report names the image instead.

## Use the data in Python { #python }

Every report has a data version on `ui.core` (a `LambdaAnalyzer`) that returns dataclasses and DataFrames instead of printing.

```python
lam = ui.core

ov = lam.overview(regions="all")                  # Overview: .functions, .metrics, .triggers, .accounts
df = ov.to_df()                                   # one row per function: settings, calls, errors, cost
detail = lam.describe("orders-etl")               # FunctionDetail: .function, .triggers, .aliases, .metrics, ...
detail.metrics.daily                              # DailyUsage per day: invocations, errors, throttles, run time

lam.errors("orders-etl", since="7d").groups       # ErrorGroup: kind, message, count, first, last, request_ids
lam.performance("orders-etl").to_df()             # one row per run: duration, billed, memory used, cold start
lam.log_events("orders-etl", since="1h", pattern='"KeyError"').to_df()
lam.code("orders-etl").files                      # CodeFile: path, size, compressed
```

The analysis functions don't call AWS, so they also work on data you already have: `parse_function`, `parse_policy`, `parse_event_source_mapping`, `runtime_status`, `function_monthly_cost`, `provisioned_monthly_cost`, `classify_error`, `group_errors`, `parse_report`, `percentile`, `suggest_memory`, `handler_file`, `secret_like`, and the findings: `function_findings`, `account_findings`, `error_findings`, `performance_findings`, `package_findings`.

```python
from lambda_functions import parse_report, runtime_status, suggest_memory

runtime_status("python3.9")                       # RuntimeStatus: state 'deprecated', upgrade 'python3.14'
parse_report(report_line).max_memory              # a REPORT line from a log export
suggest_memory(max_used=180, memory=1024)         # 256
```

## Cost { #cost }

<div class="grid cards" markdown>

- **Calls and compute**

    $0.20 per million requests, and $0.0000166667 per GB-second of memory × run time on x86_64 ($0.0000133334 on arm64). `/tmp` above 512 MB is $0.0000000309 per GB-second.

- **Provisioned concurrency**

    $0.0000041667 per GB-second for every copy kept ready, used or not ($0.0000033334 on arm64): 4 copies of a 2 GB function cost about $70 a month on arm64.

- **Logs**

    $0.50 per GB a function logs to CloudWatch Logs, and $0.03 per GB-month kept. Chatty functions often pay more for logs than for compute.

- **The tool itself**

    `functions()` reads 7 CloudWatch metrics per function, plus one per region ($0.01 per 1,000 metrics), and says how many. Reading logs (`FilterLogEvents`) and downloading code are free.

</div>

Costs are estimates at us-east-1 list prices from the AWS Price List API (`LAMBDA_PRICES`), before the free tier (1 million requests and 400,000 GB-seconds a month for the whole account), at the first pricing tier, and with compute that runs on provisioned concurrency billed at the on-demand rate (it's a little cheaper). Usage is what CloudWatch counted over the window, scaled to a month. API Gateway, SQS and data transfer aren't included. For another region or a discount, pass your own:

```python
ui = LambdaView(LambdaAnalyzer(prices={"gb_second": 0.0000183, "request": 0.22}))
```

## Permissions { #permissions }

Everything is read-only. Anything the notebook's role can't read shows up as a note instead of an error, so you can start with less and add what you need. This IAM policy covers every command:

```json title="IAM policy"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "Functions",
      "Effect": "Allow",
      "Action": [
        "lambda:ListFunctions", "lambda:GetFunction", "lambda:GetPolicy", "lambda:ListEventSourceMappings",
        "lambda:GetFunctionUrlConfig", "lambda:GetFunctionEventInvokeConfig", "lambda:ListVersionsByFunction",
        "lambda:ListAliases", "lambda:ListProvisionedConcurrencyConfigs", "lambda:GetRuntimeManagementConfig",
        "lambda:GetAccountSettings"
      ],
      "Resource": "*"
    },
    {
      "Sid": "UsageAndLogs",
      "Effect": "Allow",
      "Action": [
        "cloudwatch:GetMetricData", "logs:DescribeLogGroups", "logs:DescribeLogStreams", "logs:FilterLogEvents"
      ],
      "Resource": "*"
    },
    {
      "Sid": "EveryRegion",
      "Effect": "Allow",
      "Action": "ec2:DescribeRegions",
      "Resource": "*"
    }
  ]
}
```

`lambda:GetFunction` also returns the link `code()` downloads the package from, and the environment variables' values, which the reports never show. Leave it out and `function_info()`, `errors()`, `logs()`, `performance()` and `code()` can't start; give it to roles that may read the code. `ec2:DescribeRegions` is only for `regions="all"`.

## Troubleshooting { #troubleshooting }

??? question "“No Lambda functions in us-east-1”"

    Functions are regional, and the view reads the notebook's region unless told otherwise. `functions(regions="all")` looks in every region your account has turned on, and `LambdaView(LambdaAnalyzer(region="eu-west-1"))` makes another region the default.

??? question "“Couldn't read … (AccessDeniedException; needs lambda:…)”"

    The notebook's role is missing that permission. The note names it; add it from [Permissions](#permissions). The rest of the report still shows what could be read.

??? question "“… has no log group yet”"

    The function hasn't logged anything: it hasn't run, or its role can't write logs (`logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents`, in the `AWSLambdaBasicExecutionRole` managed policy). A function set to log to its own log group is read from there.

??? question "CloudWatch counts failed calls, but `errors()` finds no error lines"

    The code fails without logging the error (an unhandled promise rejection, a process exit), or its logging is turned down: with JSON logs, a log level above ERROR drops them. `logs()` shows what it did log.

??? question "“Couldn't download the code”"

    The package comes from S3 over HTTPS, so a notebook in a VPC without internet access needs an S3 gateway endpoint. The link Lambda gives expires after 10 minutes: run `code()` again.

??? question "The cost doesn't match my bill"

    The estimate is before the free tier, which covers small accounts entirely, and leaves out what other services charge for the same calls (API Gateway, SQS, data transfer). It scales the last 30 days to a month, so a function whose traffic changed lately is estimated from its old traffic too.

??? question "The report lost its formatting after I reopened the notebook"

    JupyterLab strips the report's styles from saved output when a notebook is reopened. Run the cell again to get the formatted report back.

## Command reference { #reference }

Every `LambdaView` command. `ui.help()` prints the same list grouped by task, and `ui.help("logs")` shows one command's full description.

<div class="ref" markdown>

| Command | What it shows |
|---|---|
| `functions(match=None, regions=None, days=30, metrics=True, details=True)` | Every function in the region (or regions, `"all"`): runtime and its support, memory, timeout, triggers, calls, error rate, run time, last called, estimated cost, warnings |
| `function_info(name, region=None, days=30)` | One function: what it runs, what triggers it and who may call it, failed asynchronous events, what it can reach, versions and aliases, its last 30 days, cost, findings |
| `errors(name, since="24h", region=None, limit=10000)` | Its errors grouped by cause, with what to do about each and the run to look at |
| `logs(name, since=None, search=None, request_id=None, n=50, region=None)` | The newest lines it logged, or one run from start to end |
| `performance(name, since="24h", region=None, limit=5000)` | Run times, memory used, cold starts, timeouts, the slowest runs, and the memory size that would do |
| `code(name, file=None, region=None, max_size="50MB")` | The files in its deployment package, findings about them, and the handler's source |
| `help(command=None)` | This list, grouped by task; `help("name")` shows one command in full |

</div>
