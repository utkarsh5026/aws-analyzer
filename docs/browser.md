---
title: In a Browser Tab (aws-analyzer serve) Guide
description: "How to open aws-analyzer's reports in a browser tab from a SageMaker notebook instance or Studio: run aws-analyzer serve in JupyterLab's terminal, open the link it prints (the notebook's own address with /proxy/8765/), and run every service's read-only commands with more room, a form per command, next steps that run with a click and Ctrl K to find a command."
---

<p class="eyebrow"><img class="aws-icon" src="images/aws/sagemaker.svg" alt="" width="32" height="32"> In a browser tab <code>aws-analyzer serve</code></p>

# The notebook's reports in a tab of their own, with room to read them

Start one command in JupyterLab's terminal, open the link it prints, and every service's reports are a click away in a full browser tab: the same reports as in a cell, with a form for each command, next steps you run with a click, and Ctrl K to find a command.
{ .lede }

<ul class="pills">
  <li>One terminal command</li>
  <li>Behind your notebook's sign-in</li>
  <li>Every service, the same reports</li>
  <li>Next steps run with a click</li>
  <li>Ctrl K finds a command</li>
  <li>Read-only</li>
  <li>Light or dark, as your browser is</li>
</ul>

![The aws-analyzer page in a browser tab: the services down the left, Lambda open with its commands grouped under Functions, When something goes wrong and Code; on the right the errors command's form with orders-etl filled in, the call errors('orders-etl') beside the Run button, and below it the report: 8 events were lost, 2 causes are still failing and 1 stopped, cards for the failed calls, calls and error rate, and a chart of failures by hour](images/browser-light.webp#only-light){ width="984" height="615" loading=lazy }
![The aws-analyzer page in a browser tab: the services down the left, Lambda open with its commands grouped under Functions, When something goes wrong and Code; on the right the errors command's form with orders-etl filled in, the call errors('orders-etl') beside the Run button, and below it the report: 8 events were lost, 2 causes are still failing and 1 stopped, cards for the failed calls, calls and error rate, and a chart of failures by hour](images/browser-dark.webp#only-dark){ width="984" height="615" loading=lazy }

`errors('orders-etl')` in a browser tab: pick Lambda, then errors, type the function's name and press Run. The report is the one `LambdaView().errors('orders-etl')` draws in a cell.
{ .muted }

## Start here { #tour }

<div class="quick" markdown>

Getting it running
{ .qgroup }

-   [How do I open it?](#setup) `aws-analyzer serve`
-   [Why is the link so long?](#link) <span class="how">the token and the proxy</span>
-   [It says "This page needs its link"](#troubleshooting) <span class="how">open the printed link</span>

Using it
{ .qgroup }

-   [How do I run a command?](#run) <span class="how">a form, or type the call</span>
-   [Can I run a report's next step?](#next) <span class="how">▶ Run beside it</span>
-   [How do I find a command?](#find) <span class="how">Ctrl K</span>

</div>

## Set up in SageMaker { #setup }

It runs in JupyterLab's terminal, on the same machine as your notebook, with the same AWS role.
{ .gist }

<div class="steps" markdown>

1. **Install aws-analyzer** in the Python environment the terminal uses. On a notebook instance the terminal starts in conda's `base`, while notebooks usually run in `python3`, so activate that one first (Studio's terminal and kernels share one).

    ```bash
    source activate python3          # notebook instances only
    pip install -U aws-analyzer
    ```

2. **Start it** in a terminal (**File → New → Terminal**):

    ```bash
    aws-analyzer serve
    ```

    It prints the address to open:

    ```text
    aws-analyzer <version> is serving its reports for your browser.

      Open: https://churn-nb.notebook.eu-west-1.sagemaker.aws/proxy/8765/?token=3f9c…
            (through Jupyter's proxy on notebook instance churn-nb: the same sign-in as your notebook)
      On this machine: http://127.0.0.1:8765/?token=3f9c…

      Reads AWS in eu-west-1, read-only, as the notebook does.
      Keep the link to yourself: its token is what lets a browser in.

    Ctrl+C stops it.
    ```

3. **Open the link** in a new browser tab. Bookmark pages as you go: the address says which command is open and with what (`#/lambda_functions/errors?name=orders-etl`), so a bookmark or a reload opens it again.

</div>

??? more "Options, and running it without the command on PATH"

    | Option | What it does |
    |---|---|
    | `--port 8800` | The port to listen on (8765 by default; the next free one when it's taken, and it says so) |
    | `--region eu-west-1` | The region to read (the notebook's own by default) |
    | `--profile dev` | An AWS profile from `~/.aws/config` |
    | `--max-rows 200` | The most rows a table shows (100 by default) |
    | `--host 0.0.0.0` | Listen beyond this machine (it warns you); SageMaker's proxy only needs the default, `127.0.0.1` |

    `python -m aws_analyzer serve` is the same command, for when the environment's scripts folder isn't on PATH. Outside SageMaker (your laptop, an EC2 machine), it prints `http://127.0.0.1:8765/?token=...`; over SSH, forward the port with `ssh -L 8765:127.0.0.1:8765 ...`.

!!! tip "Worth knowing"

    **It keeps running until you stop it**, even when you close the terminal's tab or the browser: press Ctrl+C in that terminal, or shut the terminal down from JupyterLab's running sessions. **An auto-stop script may not see it:** many notebook instances stop after an hour without kernel activity, and the page isn't a kernel, so keep a notebook busy (or the instance stops while you read).

## Why the link looks the way it does { #link }

The server listens on this machine only; JupyterLab's proxy lets your browser in through the notebook's own address and sign-in, and the token keeps everyone else out.
{ .gist }

A browser on your laptop can't reach `127.0.0.1:8765` on a SageMaker machine: that address means "this machine" to whoever uses it. What it can reach is JupyterLab, at the notebook's address. JupyterLab's server proxy (jupyter-server-proxy, which SageMaker installs; it's how TensorBoard opens there too) forwards anything under `<notebook address>/proxy/8765/` to port 8765 on the machine, and only for someone already signed in to that notebook. `aws-analyzer serve` reads SageMaker's metadata file (`/opt/ml/metadata/resource-metadata.json`) to work out that address:

| Where it runs | The link |
|---|---|
| Notebook instance | `https://<instance address>.notebook.<region>.sagemaker.aws/proxy/8765/` |
| Studio, JupyterLab space | `https://<domain id>.studio.<region>.sagemaker.aws/jupyterlab/default/proxy/8765/` |
| Studio, Code Editor | `https://<domain id>.studio.<region>.sagemaker.aws/codeeditor/default/proxy/8765/` |
| Studio Classic | `https://<domain id>.studio.<region>.sagemaker.aws/jupyter/default/proxy/8765/` |
| Anywhere else | `http://127.0.0.1:8765/` |

A notebook instance's address isn't always its name: newer ones carry a few letters after it (`test2-kbmv.notebook...` for an instance called `test2`). Only SageMaker knows them, so the command asks it (`sagemaker:DescribeNotebookInstance`, which most notebook roles have). When it can't, it guesses from the name and says so under the link.

The `?token=...` on the end is a random key made each time the server starts. The page needs it once (it keeps it in a cookie for that address after that), and every request the page sends carries it, so another website open in the same browser can't use the server to read your AWS account.

## Run a command { #run }

Pick a service on the left, then a command; fill in its form and press Run, or type the call as you would in a cell.
{ .gist }

Each command's page has its description (the same as `help('errors')`), a box per option with its default shown in grey, and the call those boxes make, as you'd write it in a notebook: `errors('orders-etl', since='7d')`. Copy puts it on the clipboard with `ui.` in front. Prefer typing? Edit the call itself and press Run: it takes the same values a cell does (strings, numbers, lists, `True`), and nothing else in it runs.

Values are read the way the notebook reads them: `7d`, `10MB`, `s3://bucket/prefix` and console links are fine as typed, and a number in a box whose default is a number becomes one (`1,000` too).

Commands that write files or change something (`download`, `invoke`, `new_env`, `to_excel`, ...) aren't on the page: run those in the notebook, where you see the code before it runs. Neither are the notebook windows (`explore()`, `chat()`), which are widgets.

## Next steps, with a click { #next }

A report's Next steps each get a ▶ Run button: it runs the step here and fills in the form it opens.
{ .gist }

![The end of the errors report in the browser: the Next box with three steps, logs('orders-etl', request_id='c0ffee00-...'), performance('orders-etl') and function_info('orders-etl'), each with a ▶ Run button and what it shows, then the Ran with line with other ways to run it](images/browser-next-light.webp#only-light){ width="984" height="615" loading=lazy }
![The end of the errors report in the browser: the Next box with three steps, logs('orders-etl', request_id='c0ffee00-...'), performance('orders-etl') and function_info('orders-etl'), each with a ▶ Run button and what it shows, then the Ran with line with other ways to run it](images/browser-next-dark.webp#only-dark){ width="984" height="615" loading=lazy }

The errors report's next steps. ▶ Run beside the first opens the run behind the newest error.
{ .muted }

Back and forward go between the reports you've opened, and show each again without asking AWS; ↻ Run again reads it afresh.

## Find a command { #find }

Ctrl K (⌘K on a Mac) opens a search over every command of every service, by name and by what it shows.
{ .gist }

![The command finder open over the page: slow typed in the search box, and the matches below it: performance for API Gateway, compare for Step Functions and performance for Lambda, each with its service's icon and what it shows](images/browser-find-light.webp#only-light){ width="984" height="615" loading=lazy }
![The command finder open over the page: slow typed in the search box, and the matches below it: performance for API Gateway, compare for Step Functions and performance for Lambda, each with its service's icon and what it shows](images/browser-find-dark.webp#only-dark){ width="984" height="615" loading=lazy }

Searching for *slow* finds the commands about run times in every service.
{ .muted }

<p class="part">Reference</p>

## A report's data, as JSON { #data }

For Lambda, S3, DynamoDB, Step Functions and API Gateway, the server also hands back the tables a report is drawn from, as JSON: what `ui.data('errors', 'orders-etl')` gives in a notebook.
{ .gist }

Ask for a command with its options as the query, or for a call as you'd write it in a cell, and send the page's token in the `X-AWS-Analyzer-Token` header (the page holds it in `<meta name="aws-analyzer-token">`):

```text title="Two ways to ask"
GET /api/data/lambda_functions/errors?name=orders-etl&since=7d
GET /api/data/lambda_functions?call=errors('orders-etl', since='7d')
```

The answer holds the call that ran and, under `data`, the report's title, verdict, cards and findings, then each table: what one row is, each column's meaning and kind (`ms`, `share` from 0 to 1, `time`, `count`, ...) and its rows, times in ISO 8601 UTC. Nothing is drawn, and nothing is written.

??? more "When there's nothing to give"

    A report that's only a note (a bucket that isn't there, a permission the role hasn't got) answers 422 with the note's words in `message`, and so does a service whose reports don't come as data yet. A command the page doesn't offer (`download`, `invoke`, ...) answers 400.

## Permissions and what it reads { #permissions }

The page runs the same Views as the notebook, so it needs the same read-only permissions: each service's guide lists them ([Lambda](lambda_functions.md#permissions), [S3](s3.md#permissions), ...). It reads with the credentials the terminal has: on SageMaker, the notebook's execution role. On a notebook instance, `aws-analyzer serve` also calls `sagemaker:DescribeNotebookInstance` once at start, for the notebook's address.

What it never does: it never writes to AWS (the commands that do aren't offered, and the Views only read), never saves files, and never sends anything off the machine except to AWS and to your browser.

## Troubleshooting { #troubleshooting }

??? question "The page says *This page needs its link*"

    Open the address `aws-analyzer serve` printed, the one ending in `?token=...`. A server started again has a new token, so an old link stops working.

??? question "The link opens a blank page, or not my notebook"

    If the terminal says the address was *guessed from its name*, SageMaker didn't say the notebook's address (the role lacks `sagemaker:DescribeNotebookInstance`). Take the address your notebook is open at, up to `.sagemaker.aws`, and add the `/proxy/8765/?token=...` the terminal printed.

??? question "The link gives *404* or *bad gateway*"

    Check the server is still running in the terminal, and that the port in the link is the one it printed (it moves to the next free port when 8765 is taken). If the address itself is wrong for your setup, take your JupyterLab address up to `/lab` and put `proxy/8765/` in place of `lab`.

??? question "*command not found: aws-analyzer*"

    The terminal's Python environment doesn't have it, or its scripts folder isn't on PATH. Activate the environment you installed it in (`source activate python3` on a notebook instance), or run `python -m aws_analyzer serve`.

??? question "*no region* or *Couldn't start Lambda*"

    boto3 found no region: start it with `--region us-east-1` (or set `AWS_DEFAULT_REGION`). On SageMaker the notebook's own region is found by itself.

??? question "The terminal shows a second link, *Checking the proxy for live updates*"

    It's a page that checks whether your notebook's proxy passes live updates, which a later version of this page needs (progress bars, Lambda runs as they happen). Open it, wait about fifteen seconds, and send the results it shows to the project ([an issue](https://github.com/utkarsh5026/aws-analyzer/issues) will do): it reads nothing from AWS, and the results hold no token or notebook name.
