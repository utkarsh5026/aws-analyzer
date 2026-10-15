---
title: SageMaker Guide
description: "How to see what your SageMaker notebook is, what it costs, whether it stops when idle, what fills its disk and what else is running, and how to make a Python environment with another version that survives restarts, with aws-analyzer's sagemaker_env.py."
---

<p class="eyebrow"><img class="aws-icon" src="images/aws/sagemaker.svg" alt="" width="32" height="32"> aws-analyzer · sagemaker_env.py</p>

# Know the SageMaker notebook you're working in

Install it in your notebook and see what the notebook is and what it's costing you, whether it stops when you walk away, how full its memory, disk and GPU are, what's filling the disk, and which notebooks, apps and endpoints in the region are still running. Need another Python? One call makes an environment with the version you want, kept when the notebook stops, with its own Jupyter kernel.
{ .lede }

<ul class="pills">
  <li>One install, boto3 only</li>
  <li>Read-only: never stops or deletes anything</li>
  <li>Notebook instances and Studio</li>
  <li>Plain text outside Jupyter</li>
</ul>

The examples run in a Studio JupyterLab space called `churn-analysis`, in the domain `d-acme12345678`, on an `ml.g5.2xlarge`. The same commands work on a notebook instance, where the notebook's name takes the place of the space. The screenshots are real output from the tool, run against a simulated SageMaker and machine with synthetic data.
{ .muted }

## Set up in SageMaker { #setup }

<div class="steps" markdown>

1. **Install the package**, in a notebook cell:

    ```bash
    %pip install aws-analyzer
    ```

    No internet in the notebook (VPC-only mode)? On a computer that has internet, download the package and copy it to a bucket the notebook can read (`pip download aws-analyzer --no-deps -d wheels`, then `aws s3 cp --recursive wheels/ s3://acme-ml-data/tools/wheels/`), and install it from there:

    ```bash
    !aws s3 cp --recursive s3://acme-ml-data/tools/wheels/ wheels/
    %pip install --no-index --find-links wheels aws-analyzer
    ```

2. **Import it and create the view.** It uses the notebook's IAM execution role and region, so there's nothing to configure.

    ```python
    from aws_analyzer import SageMakerView

    ui = SageMakerView()   # uses the notebook's IAM role and region
    ui.help()              # every command, grouped by task; ui.help("disk") shows one in full
    ```

3. **Optional:** another region or AWS profile, or plain-text output.

    ```python
    from aws_analyzer import SageMakerAnalyzer, SageMakerView

    ui = SageMakerView(SageMakerAnalyzer(region="eu-west-1", profile="dev"))
    ui = SageMakerView(mode="text")          # plain text, e.g. in a terminal or a script
    ui = SageMakerView(progress="plain")     # a plain progress line instead of tqdm bars ("off": none)
    ```

</div>

!!! note ""

    **Why `sagemaker_env`?** So it's never mixed up with the SageMaker Python SDK, which is imported as `sagemaker`.

## Five-minute tour { #tour }

A few commands cover most questions. Each prints a report under the cell. None of them change anything, except `new_env()`, which makes the Python environment you ask for.

```python
ui.instance()                    # this notebook: type, cost so far, idle shutdown, memory, disk
ui.disk()                        # what fills the disk, and what's safe to clear
ui.running()                     # everything billing in the region, and what looks forgotten
ui.instance("old-experiment")    # another notebook instance, or a Studio space, by name
ui.envs()                        # the Python environments and Jupyter kernels here
ui.new_env("3.11")               # a Python 3.11 environment with its own kernel, kept when the notebook stops
```

**Reading a report.** Every report puts the answer first: cards with the numbers that matter (a card turns amber when a finding is about it), then the findings, warnings first, each a bold headline with why it matters and what to do underneath. The tables of detail come after. Click a column's header to sort by it (again for the other way, a third time for the original order), pick a value in a column's **Filter** to see only those rows (a kind, a status), and untick columns under **Columns** to focus on the ones you need. It's all plain HTML and CSS, so it still works when you reopen the notebook. At the bottom is a **Next** row of two or three commands with the arguments filled in, such as `disk('/home/sagemaker-user/data')`. One click on a command anywhere in a report, or on a code block, selects all of it, ready to copy.

`instance()` and `disk()` look at the machine they run on, so run them in the notebook you want to know about. `running()` and `instance("name")` only ask AWS, so they work from anywhere, including your laptop.

## This notebook { #instance }

`instance()` finds out where it's running from SageMaker's own metadata file, `/opt/ml/metadata/resource-metadata.json`, then asks SageMaker about that notebook instance or Studio app and looks at the machine itself. The cards answer the usual questions in one line each:

- **What is it and what does it cost?** The instance type with its vCPUs, memory and GPUs, the price per hour, how long it has been running, what that cost so far, and what a month of it costs if it's never stopped.
- **Will it stop by itself?** Whether anything shuts it down when it sits idle, and after how long.
- **How busy is it?** Memory in use, the disk your notebooks live on, the CPU load over the last 15 minutes, and the GPU.

```python
ui.instance()
```

![instance(): cards for the ml.g5.2xlarge's price, 2 days 5 hours running, about $80 so far, idle shutdown off, the disk 88% full and an idle GPU; warnings that the domain doesn't shut idle apps down, that the disk is nearly full and that the GPU is idle with the CPU type that would cost $1.06 an hour less; the command that turns on idle shutdown; and a table of the machine's CPU, memory, disk, GPU and kernels](images/sagemaker-instance-light.webp#only-light){ width="984" height="1636" loading=lazy }
![instance(): cards for the ml.g5.2xlarge's price, 2 days 5 hours running, about $80 so far, idle shutdown off, the disk 88% full and an idle GPU; warnings that the domain doesn't shut idle apps down, that the disk is nearly full and that the GPU is idle with the CPU type that would cost $1.06 an hour less; the command that turns on idle shutdown; and a table of the machine's CPU, memory, disk, GPU and kernels](images/sagemaker-instance-dark.webp#only-dark){ width="984" height="1636" loading=lazy }
/// caption
`ui.instance()` in the `churn-analysis` space: the GPU has done nothing for this session, and nothing will stop the app tonight.
///

Below the findings come the details:

- **This machine right now**: CPU load, memory, each disk (and whether it survives a stop: on a notebook instance only `~/SageMaker` does, in Studio the space's home folder does), GPU memory and use, and the Jupyter kernels with the memory they hold.
- **Estimated cost**: the instance per hour and per month, and the storage volume, which is billed whether the notebook runs or not.
- **Settings**: the execution role, who AWS sees you as, internet access, lifecycle configuration, the image or platform, the storage volume, and the space, user profile and domain.
- Folded away: the biggest processes by memory (to find the kernel that holds 5 GB) and the Python and package versions.

The findings look for money spent on nothing and for trouble ahead: no idle shutdown; a disk or memory more than 85% full, and the other kernels holding memory you could free; a GPU that's idle, with the CPU type that has the same vCPUs and memory and what it saves; an instance far bigger than what it's using; Amazon Linux 1. Each finding ends in the command or setting that fixes it.

### Another notebook

Pass a name to see another notebook's settings and cost: a notebook instance, or a Studio space. When two domains have a space with the same name, write `domain-id/space`. A machine can only be read from inside it, so these reports have no "right now" section.

```python
ui.instance("old-experiment")                 # a notebook instance
ui.instance("forecasting")                    # a Studio space, running or not
ui.instance("d-acme12345678/forecasting")     # the space in one domain
```

## Stop paying for idle time { #idle }

A notebook bills for every hour it's running, whether anyone is using it or not. An `ml.g5.2xlarge` left on over a weekend costs about $100. SageMaker can stop an idle notebook, but it isn't on by default, and `instance()` tells you which case you're in:

<div class="grid cards" markdown>

- **Notebook instances**

    There's no setting. The usual way is a lifecycle configuration that runs AWS's [auto-stop-idle](https://github.com/aws-samples/amazon-sagemaker-notebook-instance-lifecycle-config-samples/tree/master/scripts/auto-stop-idle) script. `instance()` reads the notebook's lifecycle script to see whether it stops it, and after how long.

- **Studio JupyterLab and Code Editor**

    Idle shutdown is a domain setting (an admin turns it on), and a space or user profile can change the timeout. `instance()` reads all three.

- **Studio Classic**

    Kernel apps have no idle shutdown. Shut them down from the Running Terminals and Kernels panel when you're done.

</div>

When nothing stops the notebook, the report adds a **Turn on idle shutdown** (or **Turn on auto-stop**) block with the commands, ready to copy. The tool shows them and never runs them. For a Studio domain it looks like this:

```bash title="shown by instance()"
# An admin turns idle shutdown on for every JupyterLab app in the domain. The settings
# replace the domain's current JupyterLabAppSettings: check them first with
#   aws sagemaker describe-domain --domain-id d-acme12345678
# and add the ones you want to keep to this JSON.
aws sagemaker update-domain --domain-id d-acme12345678 \
    --default-user-settings '{
      "JupyterLabAppSettings": {"AppLifecycleManagement": {"IdleSettings": {
        "LifecycleManagement": "ENABLED", "IdleTimeoutInMinutes": 60}}}}'
```

Until then, stopping the notebook yourself works just as well. Your files stay: a notebook instance keeps its volume, and a Studio space keeps its home folder.

```bash
aws sagemaker stop-notebook-instance --notebook-instance-name old-experiment
aws sagemaker delete-app --domain-id d-acme12345678 --space-name churn-analysis --app-type JupyterLab --app-name default
```

## What fills the disk { #disk }

A full disk shows up as a notebook that won't save or a `pip install` that fails halfway. `disk()` measures the folder your notebooks live in (`~/SageMaker` on a notebook instance, `/home/sagemaker-user` in Studio) and shows:

- how full the disk is, and how much this folder holds;
- the biggest folders as a tree three levels deep, with a bar for each one's share;
- the biggest files and when they last changed;
- the **caches and trash that are safe to clear**, each with the command that empties it: Jupyter's trash (files you deleted in JupyterLab keep using the disk until it's emptied), pip and conda caches, Hugging Face and PyTorch downloads, and notebook checkpoints.

```python
ui.disk()                               # where your notebooks live
ui.disk("~/SageMaker/data")             # one folder
ui.disk("/", limit=None)                # the whole system disk, however many files
```

![disk(): cards for a 50 GB disk 88% used with 6.3 GB safe to clear; warnings that the disk is nearly full, with the command that makes the space's volume 100 GB, and that 6.3 GB is caches and trash; a note about two year-old parquet files; a tree of the biggest folders; the largest files; and a table of the Jupyter trash, Hugging Face cache, pip cache and notebook checkpoints with the command that clears each](images/sagemaker-disk-light.webp#only-light){ width="984" height="1668" loading=lazy }
![disk(): cards for a 50 GB disk 88% used with 6.3 GB safe to clear; warnings that the disk is nearly full, with the command that makes the space's volume 100 GB, and that 6.3 GB is caches and trash; a note about two year-old parquet files; a tree of the biggest folders; the largest files; and a table of the Jupyter trash, Hugging Face cache, pip cache and notebook checkpoints with the command that clears each](images/sagemaker-disk-dark.webp#only-dark){ width="984" height="1668" loading=lazy }
/// caption
`ui.disk()`: 3.8 GB of it is a file deleted in JupyterLab weeks ago, still sitting in the trash.
///

When the disk is nearly full, the finding also gives the command that makes the volume bigger and what the extra space costs a month. A volume can grow but never shrink. Big files that haven't changed in 90 days are cheaper in S3: about $0.023 per GB-month, against $0.112 for a Studio space's volume and $0.14 for a notebook instance's.

`disk()` reads only local files, stays on one disk and doesn't follow links. It stops after 200,000 files by default and says so; `limit=None` measures everything.

## Python environments { #envs }

To try your code on another Python, say 3.11 when the notebook has 3.12, you need an environment with that version and a Jupyter kernel that starts it. On SageMaker there's a catch: most of the machine is reset when the notebook stops. On a notebook instance only `~/SageMaker` is kept, so an environment made the usual way (`conda create -n py311 ...`, which puts it in `~/anaconda3/envs`) is gone after the next restart. In Studio the home folder is kept but `/opt/conda` isn't. `new_env()` takes care of that:

```python
ui.new_env("3.11")                                   # Python 3.11, as kernel "Python 3.11 (py311)"
ui.new_env("3.12", packages="pandas boto3")          # with packages ('-r requirements.txt' works too)
ui.new_env("3.11", name="churn-py311")               # your own name for its folder and kernel
ui.new_env("3.11", dry_run=True)                     # the commands, to run in a terminal yourself
ui.envs()                                            # every environment and kernel here
```

<div class="steps" markdown>

1. **It makes the environment where it's kept**: `~/SageMaker/envs/py311` on a notebook instance, `~/.conda/envs/py311` in Studio. It uses the first of mamba, conda, micromamba and uv on the machine (SageMaker has conda; `tool="uv"` picks uv, which is faster), with Python from conda-forge, which needs no license and no terms to accept.
2. **It installs what you list** with pip: `packages="pandas boto3"`, a list, or `"-r requirements.txt"`.
3. **It adds the kernel**, so **Python 3.11 (py311)** is in Jupyter's kernel menu. Pick it with **Kernel > Change Kernel**, or start a notebook with it from the Launcher; it can take a minute to show up. `%pip install` in a notebook on that kernel installs into the new environment.

</div>

It takes a minute or two, with a progress bar while it runs. The report then shows the Python version it got, each step and how long it took, and the commands it ran. If a step fails, it says why (no internet access, a full disk, a Python version or package that doesn't exist) and shows the end of what the step printed, with the commands to run in a terminal and see all of it. Run `new_env()` again on an environment that's already there and it doesn't make it again: it installs only the `packages=` you give and adds the kernel back.

!!! warning ""

    **After a notebook instance restarts**, the environment in `~/SageMaker/envs` is still there, but its kernel isn't: Jupyter's kernels live in `~/.local`, which is reset. `new_env("3.11")` puts it back in seconds. To have it done at every start, the report includes a lifecycle configuration (an on-start script that adds back the kernel of each environment in `~/SageMaker/envs`) and the AWS CLI commands that attach it. A notebook instance takes one lifecycle configuration, so if yours already has one (auto-stop, say), add the script's lines to its on-start script instead. Studio keeps kernels, because they're in the home folder.

`envs()` lists the environments on the machine with their Python version, where each one is and **whether it's kept when the notebook stops**, and the Jupyter kernel that starts each. It warns about:

- environments made since the machine started on the disk that's reset, which will be gone after the next stop, with the call that makes them again where they're kept;
- kernels that start a Python that isn't there any more, so picking them fails, with the command that takes them off the menu;
- environments in the kept folder that have no kernel, which is what a notebook instance looks like after a restart.

`new_env()` only writes the environment's folder and its kernel, and never removes anything: a finding that would help by removing something shows the command instead. It needs no AWS permission, but it does download Python and the packages, so a notebook without internet access needs a NAT gateway or a package mirror such as AWS CodeArtifact.

## Everything that's running { #running }

`running()` lists everything SageMaker bills by the hour in the region: notebook instances, Studio apps (JupyterLab, Code Editor and Studio Classic kernels), endpoints, and training and processing jobs in progress. Each row starts with an icon for its kind (📓 notebook instance, 🧪 Studio app, 🚀 endpoint, 🏋️ training job, ⚙️ processing job), and 📍 marks the notebook you're in. For each one it shows the instance, the price per hour, how long it has been running and whether it stops when idle. For endpoints, it adds how many requests they served over the last 7 days, from CloudWatch. Stopped notebook instances get a table of their own, because their storage is still billed.

```python
ui.running()
ui.running(days=30)          # endpoint traffic over 30 days
ui.running(metrics=False)    # skip CloudWatch
```

![running(): eight things running for $5.88 an hour; warnings about a notebook instance running for 9 days with no auto-stop, a Code Editor app running for 5 days in a domain without idle shutdown, and an endpoint with no requests in 7 days, each with the command that stops it; a table of what's running with instance, price, time running, idle shutdown and requests; and the stopped notebook instances with their storage cost](images/sagemaker-running-light.webp#only-light){ width="984" height="1193" loading=lazy }
![running(): eight things running for $5.88 an hour; warnings about a notebook instance running for 9 days with no auto-stop, a Code Editor app running for 5 days in a domain without idle shutdown, and an endpoint with no requests in 7 days, each with the command that stops it; a table of what's running with instance, price, time running, idle shutdown and requests; and the stopped notebook instances with their storage cost](images/sagemaker-running-dark.webp#only-dark){ width="984" height="1193" loading=lazy }
/// caption
`ui.running()`: a forgotten notebook, a forgotten app and an unused endpoint add up to about $590 a month.
///

The findings point at what looks forgotten, each with the command that stops it:

- notebooks and apps running for over 12 hours with nothing to stop them when idle (never the notebook you're running in), with what they've cost so far;
- endpoints that got no requests in the window, with the command that deletes one and the one that brings it back from its endpoint configuration;
- the storage that stopped notebook instances keep, and which one has been stopped longest.

## Use the data in Python { #python }

Every report has a data version on `ui.core` (a `SageMakerAnalyzer`) that returns dataclasses and DataFrames instead of printing.

```python
sm = ui.core

report = sm.instance()                                # InstanceReport: .env, .notebook, .machine, .identity
report.notebook.instance_type, report.notebook.idle   # ('ml.g5.2xlarge', 'off')
report.machine.memory_share                           # 0.37
report.machine.gpus                                   # [GPUInfo(name='NVIDIA A10G', busy=0.0, ...)]
sm.notebook("old-experiment")                         # NotebookInfo for another notebook instance or space
sm.environment()                                      # Environment: where this code runs
sm.machine()                                          # Machine: this machine right now (local reads only)

d = sm.disk("~/SageMaker", limit=None)                # DiskReport
d.largest[:5], d.clearable                            # the biggest files, caches you can clear
d.to_df()                                             # one row per folder

r = sm.running(days=30)                               # RunningReport: .resources, .stopped, .errors
r.to_df()                                             # one row per notebook, app, endpoint and job

e = sm.environments()                                 # EnvReport: .envs, .kernels, .tools, .folder
e.to_df()                                             # one row per Python environment
plan = sm.env_plan("3.11", packages="pandas")         # EnvPlan: where it goes, what makes it, .steps (the commands)
result = sm.create_env(plan)                          # EnvResult: runs them; .ok, .python, .failed
```

The analysis functions don't call AWS, so they work on responses and text you already have: `parse_metadata`, `parse_notebook_instance`, `parse_app`, `apply_studio_settings`, `studio_idle`, `lifecycle_idle`, `parse_endpoint`, `parse_training_job`, `parse_processing_job`, `parse_meminfo`, `parse_loadavg`, `parse_gpus`, `hourly_price`, `describe_instance`, `notebook_costs`, `smaller_type`, `idle_shutdown_commands`, `stop_command`, `parse_python_version`, `parse_pyvenv_cfg`, `parse_conda_history`, `env_steps`, `on_start_script`, `kernels_lifecycle`, `env_failure`, and the findings, `instance_findings`, `disk_findings`, `running_findings`, `env_findings` and `plan_findings`.

```python
from aws_analyzer.sagemaker_env import describe_instance, hourly_price, smaller_type

hourly_price("ml.g5.2xlarge")                          # 1.52
describe_instance("ml.g5.2xlarge")                     # '8 vCPU · 32 GiB · 1 GPU'
smaller_type("ml.m5.4xlarge", vcpus=2, memory_gib=6)   # 'ml.t3.large'
```

## Cost and what's measured { #cost }

<div class="grid cards" markdown>

- **Prices are list prices**

    us-east-1 on-demand, read from the AWS Price List API on 2026-09-27. An instance type costs the same per hour as a notebook, a Studio app, an endpoint or a job.

- **Cost so far is an estimate**

    The price per hour times how long it has run: since boot for this notebook instance, since the app started in Studio, and since the last change (about its last start) for another notebook instance.

- **Usage is a snapshot**

    CPU load is the 15-minute average and memory is what's in use now, so a suggestion to downsize says "if that's typical".

- **Nothing is billed to look**

    The SageMaker calls are free. CloudWatch's GetMetricData for endpoint traffic costs a fraction of a cent.

</div>

`INSTANCE_TYPES` holds the price per hour of 134 instance types, with their vCPUs, memory and GPUs, and `SAGEMAKER_PRICES` adds storage per GB-month. For another region, a discount or a Savings Plan, pass your own prices; every report says whether it used list prices or yours. A type that isn't in the table shows its cost as unknown rather than a guess.

```python
ui = SageMakerView(SageMakerAnalyzer(prices={"ml.g5.2xlarge": 1.21, "space_storage": 0.12}))
```

Spot training jobs cost less than the on-demand price shown, and `running()` says which ones use spot.

## Permissions { #permissions }

Everything is read-only. Anything the notebook's role can't read shows up as a note (or a `?` on a card) instead of an error, and the rest of the report still works. `disk()` only reads local files, and finding out who you're signed in as needs no permission. The `AmazonSageMakerFullAccess` managed policy, which many execution roles have, already includes the `sagemaker:` actions. This policy covers every command:

```json title="IAM policy"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadNotebooksAndStudio",
      "Effect": "Allow",
      "Action": [
        "sagemaker:ListNotebookInstances", "sagemaker:DescribeNotebookInstance",
        "sagemaker:DescribeNotebookInstanceLifecycleConfig", "sagemaker:ListDomains", "sagemaker:DescribeDomain",
        "sagemaker:DescribeUserProfile", "sagemaker:ListSpaces", "sagemaker:DescribeSpace",
        "sagemaker:ListApps", "sagemaker:DescribeApp"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ReadEndpointsAndJobs",
      "Effect": "Allow",
      "Action": [
        "sagemaker:ListEndpoints", "sagemaker:DescribeEndpoint", "sagemaker:DescribeEndpointConfig",
        "sagemaker:ListTrainingJobs", "sagemaker:DescribeTrainingJob",
        "sagemaker:ListProcessingJobs", "sagemaker:DescribeProcessingJob"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ReadEndpointTraffic",
      "Effect": "Allow",
      "Action": "cloudwatch:GetMetricData",
      "Resource": "*"
    }
  ]
}
```

| Command | Needs |
|---|---|
| `instance()` on a notebook instance | `DescribeNotebookInstance`, `DescribeNotebookInstanceLifecycleConfig` |
| `instance()` in Studio | `DescribeApp`, `DescribeSpace`, `DescribeDomain`, `DescribeUserProfile` |
| `instance("space name")` | also `ListDomains`, `ListSpaces`, `ListApps` |
| `running()` | the `List` and `Describe` actions for notebook instances, apps, endpoints and jobs, `DescribeDomain`, and `cloudwatch:GetMetricData` for endpoint traffic |
| `disk()` | nothing: it reads local files |
| `envs()`, `new_env()` | nothing: they read local files, and `new_env()` runs conda (or uv) and pip on the notebook |

## Troubleshooting { #troubleshooting }

??? question "“Not a SageMaker notebook”"

    There's no `/opt/ml/metadata/resource-metadata.json` on this machine, which every SageMaker notebook instance and Studio app has. You're probably on your laptop or in a training or processing container. `ui.instance("name")` and `ui.running()` still work from anywhere.

??? question "Idle shutdown shows “?”"

    The role can't read the domain (`sagemaker:DescribeDomain`) or the notebook's lifecycle configuration (`sagemaker:DescribeNotebookInstanceLifecycleConfig`). The findings say which one. Compare the role with the [policy above](#permissions).

??? question "“Auto-stop: off”, but our lifecycle script does stop the notebook"

    `instance()` recognizes AWS's auto-stop-idle sample, and scripts that mention `autostop` or call `stop-notebook-instance`. A script that stops the notebook some other way isn't recognized, so check it yourself.

??? question "There's no GPU card on a GPU instance"

    The report reads the GPU with `nvidia-smi`. If the image doesn't include it, the findings say the GPU couldn't be read, and the rest of the report is unaffected.

??? question "The disk shows “EFS, grows as needed”"

    In Studio Classic your home folder is on Amazon EFS, which has no fixed size, so it can't fill up. EFS bills for what's stored, which `disk()` still measures.

??? question "My new kernel isn't in the kernel menu"

    Jupyter reads the kernels when its menu opens, so a new one can take a minute to show up; refresh the browser tab if it doesn't. On a notebook instance, kernels are reset when it stops: run `ui.new_env("3.11")` again (it only adds the kernel back), or attach the lifecycle configuration from the report. `ui.envs()` shows which environments have a kernel.

??? question "new_env() says it couldn't download what it needs"

    The notebook can't reach conda-forge (or, with uv, PyPI and GitHub). A notebook instance or Studio domain with internet access only through your VPC needs a NAT gateway, or a package mirror such as AWS CodeArtifact. `ui.instance()` shows the notebook's internet access.

??? question "The costs don't match my bill"

    The estimates use us-east-1 on-demand list prices. Other regions, Savings Plans and discounts change them: pass your own with `SageMakerAnalyzer(prices={...})`. The "cost so far" of another notebook instance counts from its last change, which is only about when it last started.

??? question "The report lost its formatting after I reopened the notebook"

    JupyterLab strips the report's styles from saved output when a notebook is reopened. Run the cell again to get the formatted report back.

## Command reference { #reference }

Every `SageMakerView` command. `ui.help()` prints the same list grouped by task, and `ui.help("disk")` shows one command's full description.

<div class="ref" markdown>

| Command | What it shows |
|---|---|
| `instance(name=None)` | This notebook (or another notebook instance or Studio space): type, cost so far, idle shutdown, CPU, memory, disk and GPU use now, role, network, findings |
| `disk(path=None, top=20, limit="200k")` | How full the disk is, the biggest folders and files, and the caches and trash that are safe to clear |
| `envs()` | The Python environments and Jupyter kernels here: each one's Python version, where it is, whether it's kept when the notebook stops, and kernels that fail to start |
| `new_env(python="3.11", name=None, packages=None, path=None, tool="auto", dry_run=False)` | Makes a Python environment with that version where it's kept when the notebook stops, installs `packages=` in it and adds its Jupyter kernel; `dry_run=True` shows the commands instead |
| `running(metrics=True, days=7)` | Everything billing by the hour in the region, the stopped notebook instances' storage, and what looks forgotten |
| `help(command=None)` | This list, grouped by task; `help("name")` shows one command in full |

</div>
