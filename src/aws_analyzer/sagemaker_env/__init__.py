"""
sagemaker_env.py - SageMaker toolkit for SageMaker / Jupyter notebooks: the notebook you're running in, what fills
its disk, its Python environments, and everything else in SageMaker that's running and billing.

Part of aws-analyzer: ``pip install aws-analyzer``, then ``from aws_analyzer import SageMakerView``. It isn't called
sagemaker.py, so it's never mixed up with the SageMaker Python SDK (``import sagemaker``).

Requirements: boto3 (required). pandas only for DataFrames, IPython only for rich
HTML output. All are preinstalled on SageMaker.

The file has two layers:

    SageMakerAnalyzer  Pure logic. Reads this machine (SageMaker's metadata file, memory,
                       disks, GPUs) and talks to AWS, and returns plain Python data
                       (dataclasses, dicts, lists, DataFrames). Never prints.
    SageMakerView      Notebook UI. Calls SageMakerAnalyzer and renders readable
                       cards and tables (HTML in Jupyter, plain text in a terminal).

Nothing in this file changes AWS: it never stops a notebook or deletes an app or an endpoint,
and it never removes a file. Where one of those would help, it shows the command to run. The
one thing it makes is what new_env() is asked for: a Python environment, in a folder that's
kept when the notebook stops, and its Jupyter kernel.

Quick start
-----------
    ui = SageMakerView()                              # or SageMakerView(SageMakerAnalyzer(region="eu-west-1"))
    ui.help()                                         # list every command
    ui.instance()                                     # this notebook: type, cost, idle shutdown, CPU / memory / disk / GPU
    ui.instance("team-notebook")                      # another notebook instance, or a Studio space by name
    ui.disk()                                         # what fills the disk, and caches or trash you can clear
    ui.disk("~/SageMaker/data")                       # one folder
    ui.envs()                                         # the Python environments and Jupyter kernels here
    ui.new_env("3.11")                                # a Python 3.11 environment with its own kernel, kept when it stops
    ui.running()                                      # everything running and billing in the region, and what looks forgotten

    sm = ui.core                                      # same analyzer, raw data
    report = sm.instance()                            # InstanceReport: .notebook (settings), .machine (right now)
    df = sm.running().to_df()                         # one row per notebook, app, endpoint and job
    folders = sm.disk().to_df()                       # folder sizes
    envs = sm.environments().to_df()                  # one row per Python environment
"""

from __future__ import annotations

from .._kit.fmt import HOURS_PER_MONTH, human_age, human_money, human_size
from .prices import INSTANCE_TYPES, SAGEMAKER_PRICES
from .helpers import (
    GB, KB, MB, TB, app_label, describe_instance, env_name, hourly_price, human_runtime, instance_spec, kernel_label,
    lifecycle_idle, new_env_call, parse_python_version, parse_pyvenv_cfg, shell_path,
)
from .models import (
    Billable, Clearable, DiskEntry, DiskReport, EnvPlan, EnvReport, EnvResult, EnvStep, Environment, GPUInfo,
    InstanceReport, KernelSpec, Machine, NotebookInfo, ProcessInfo, PythonEnv, RunningReport, Volume,
)
from .analysis.machine import parse_gpus, parse_loadavg, parse_meminfo, parse_metadata, parse_process
from .analysis.common import FULL
from .analysis.notebooks import (
    AUTOSTOP_SAMPLE, apply_studio_settings, idle_shutdown_commands, instance_findings, notebook_costs, parse_app,
    parse_notebook_instance, smaller_type, stop_command, studio_idle,
)
from .analysis.disk import clear_command, clearable_kind, disk_findings, folder_tree
from .analysis.running import LONG_RUNNING, parse_endpoint, parse_processing_job, parse_training_job, running_findings
from .analysis.envs import (
    CHANNEL, KERNELS_FOLDER, LIFECYCLE_NAME, LOW_DISK, TOOLS, env_failure, env_findings, env_steps,
    kernels_lifecycle, lost_envs, on_start_script, output_tail, package_list, parse_conda_history, plan_findings,
    step_command,
)
from .machine import METADATA_FILE
from .analyzer import SageMakerAnalyzer
from .view import SageMakerView

__all__ = [
    "AUTOSTOP_SAMPLE",
    "CHANNEL",
    "FULL",
    "GB",
    "HOURS_PER_MONTH",
    "INSTANCE_TYPES",
    "KB",
    "KERNELS_FOLDER",
    "LIFECYCLE_NAME",
    "LONG_RUNNING",
    "LOW_DISK",
    "MB",
    "METADATA_FILE",
    "SAGEMAKER_PRICES",
    "TB",
    "TOOLS",
    "Billable",
    "Clearable",
    "DiskEntry",
    "DiskReport",
    "EnvPlan",
    "EnvReport",
    "EnvResult",
    "EnvStep",
    "Environment",
    "GPUInfo",
    "InstanceReport",
    "KernelSpec",
    "Machine",
    "NotebookInfo",
    "ProcessInfo",
    "PythonEnv",
    "RunningReport",
    "SageMakerAnalyzer",
    "SageMakerView",
    "Volume",
    "app_label",
    "apply_studio_settings",
    "clear_command",
    "clearable_kind",
    "describe_instance",
    "disk_findings",
    "env_failure",
    "env_findings",
    "env_name",
    "env_steps",
    "folder_tree",
    "hourly_price",
    "human_age",
    "human_money",
    "human_runtime",
    "human_size",
    "idle_shutdown_commands",
    "instance_findings",
    "instance_spec",
    "kernel_label",
    "kernels_lifecycle",
    "lifecycle_idle",
    "lost_envs",
    "new_env_call",
    "notebook_costs",
    "on_start_script",
    "output_tail",
    "package_list",
    "parse_app",
    "parse_conda_history",
    "parse_endpoint",
    "parse_gpus",
    "parse_loadavg",
    "parse_meminfo",
    "parse_metadata",
    "parse_notebook_instance",
    "parse_process",
    "parse_processing_job",
    "parse_python_version",
    "parse_pyvenv_cfg",
    "parse_training_job",
    "plan_findings",
    "running_findings",
    "shell_path",
    "smaller_type",
    "step_command",
    "stop_command",
    "studio_idle",
]
