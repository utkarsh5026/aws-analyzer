"""
The plan sagemaker_env.py was split by: SageMakerAnalyzer's mixins (mixins.py reads MIXINS), then each module, in layer
order, and the statements it holds (split.py reads MODULES, DOCS and REEXPORT_KIT).
"""

MIXINS = {
    "SageMakerAnalyzer": [
        ("_MachineReads",
         "SageMakerAnalyzer's reads of the machine it runs on: SageMaker's metadata file, /proc (memory, load,\n"
         "processes, uptime), the disks, nvidia-smi and what fills a folder. `root` points all of it at a folder of\n"
         "fake files, and _shown() / _real() make paths look and work as the machine sees them.",
         ["_path", "_shown", "_user_home", "_real", "environment", "home", "_volume_labels", "_cpu_count",
          "_disk_usage", "volume", "volumes", "_gpu_query", "_processes", "machine", "disk", "_booted"]),
        ("_EnvReads",
         "SageMakerAnalyzer's reads of the Python environments here: the tools that make them, the environments and\n"
         "Jupyter kernels found by their files (never by running them), and the plan new_env() follows.",
         ["tools", "envs_folder", "_kept", "_env_python", "_env_made", "_env_at", "_env_prefixes",
          "_kernel_folders", "_kernel_at", "environments", "env_plan"]),
        ("_EnvMaking",
         "SageMakerAnalyzer's create_env(): it runs an env_plan()'s commands, through _run (a subprocess without a\n"
         "shell). The one place in sagemaker_env that makes anything: a Python environment and its Jupyter kernel.",
         ["_run", "create_env"]),
    ],
}

MODULES = {
    "prices": ["INSTANCE_TYPES", "SAGEMAKER_PRICES"],
    "helpers": ["KB, MB, GB, TB", "_APP_LABELS", "_APP_SETTINGS", "human_runtime", "_since", "_arn_part",
                "_arn_name", "instance_spec", "describe_instance", "hourly_price", "app_label", "_image_label",
                "_AUTOSTOP_RE", "_IDLE_TIME_RE", "lifecycle_idle", "_VERSION_RE", "_ENV_NAME_RE",
                "parse_python_version", "env_name", "new_env_call", "_minor", "kernel_label", "parse_pyvenv_cfg",
                "_clean_version", "shell_path", "_tilde"],
    "models": ["Environment", "_BILLED", "NotebookInfo", "GPUInfo", "Volume", "ProcessInfo", "Machine",
               "InstanceReport", "DiskEntry", "Clearable", "DiskReport", "Billable", "RunningReport", "PythonEnv",
               "KernelSpec", "EnvReport", "EnvStep", "EnvPlan", "EnvResult"],
    "analysis/machine": ["parse_meminfo", "parse_loadavg", "parse_gpus", "parse_process", "parse_metadata"],
    "analysis/common": ["_SECTIONS", "FULL", "_couldnt_read", "_money_per_hour", "_full"],
    "analysis/notebooks": ["parse_notebook_instance", "parse_app", "_idle_settings", "studio_idle",
                           "apply_studio_settings", "notebook_costs", "smaller_type", "stop_command",
                           "AUTOSTOP_SAMPLE", "idle_shutdown_commands", "_gpu_idle", "_other_kernels",
                           "instance_findings"],
    "analysis/disk": ["_CLEARABLE", "clearable_kind", "clear_command", "folder_tree", "disk_findings"],
    "analysis/running": ["parse_endpoint", "parse_training_job", "parse_processing_job", "LONG_RUNNING",
                         "_forgotten", "_unused_endpoint", "running_findings"],
    "analysis/envs": ["TOOLS", "CHANNEL", "LOW_DISK", "_SUGGESTED", "KERNELS_FOLDER", "LIFECYCLE_NAME", "_ANACONDA",
                      "_CONDA_ROOTS", "_HOME_CONDA_ROOTS", "_TOOL_FOLDERS", "_HOME_TOOL_FOLDERS", "_JUPYTER_SERVERS",
                      "_HISTORY_RE", "parse_conda_history", "package_list", "env_steps", "step_command",
                      "output_tail", "on_start_script", "kernels_lifecycle", "_disks", "_same_python",
                      "plan_findings", "lost_envs", "env_findings", "_FAILURES", "env_failure"],
    "machine": ["METADATA_FILE", "_HOMES", "_PACKAGES", "_MachineReads"],
    "envs": ["_EnvReads", "_EnvMaking"],
    "analyzer": ["_missing", "SageMakerAnalyzer", "_is_current"],
    "style": ["_ROOT", "_CSS", "_BADGE", "_render_html"],
    "view": ["_idle_label", "_KIND_ICONS", "_kind_label", "_where", "_notebook_ref", "SageMakerView"],
}

DOCS = {
    "prices": "SageMaker's price list: every instance type's price per hour, with its vCPUs, memory and GPUs, and\n"
              "storage per GB-month. us-east-1 list prices; pass SageMakerAnalyzer(prices={...}) for your own.",
    "helpers": "Parsing and formatting: sizes, run times, ARNs, instance types and their prices, app labels, idle\n"
               "shutdown in lifecycle scripts, Python versions and environment names, and paths for a shell.",
    "models": "The dataclasses SageMakerAnalyzer returns: the notebook and this machine, the disk, what's running and\n"
              "billing, and the Python environments, kernels and the plan to make one.",
    "analysis": "Pure analysis of a SageMaker notebook, its disk, what's running and its Python environments: no AWS\n"
                "calls and no reads of the machine, so it runs on data you pass it.",
    "analysis/machine": "What this machine's files say: /proc's memory, load and processes, nvidia-smi's GPUs and\n"
                        "SageMaker's metadata file.",
    "analysis/common": "What the findings of more than one report share: the section each permission reads, how full is\n"
                       "too full, and prices per hour in words.",
    "analysis/notebooks": "Notebook instances and Studio apps: their settings and idle shutdown, what they cost, a\n"
                          "smaller type, the commands to stop them, and instance()'s findings.",
    "analysis/disk": "What fills the disk: the caches and trash you can clear, the commands that clear them, the\n"
                     "folder tree, and disk()'s findings.",
    "analysis/running": "Everything running and billing: endpoints and jobs as Billables, what looks forgotten, and\n"
                        "running()'s findings.",
    "analysis/envs": "Python environments: the steps that make one, the lifecycle configuration that adds its kernel\n"
                     "back at each start, what's wrong with a plan or the environments here, and what a failed step\n"
                     "means.",
    "machine": "SageMakerAnalyzer's reads of the machine it runs on: the metadata file, /proc, the disks, the GPUs and\n"
               "what fills a folder.",
    "envs": "SageMakerAnalyzer's Python environments: the ones here and their kernels, the plan to make one, and\n"
            "create_env(), which runs it: the only code in sagemaker_env that makes anything.",
    "analyzer": "SageMakerAnalyzer: talks to SageMaker, CloudWatch and STS, and returns the dataclasses in models.\n"
                "Never prints, and never stops, deletes or changes anything in AWS.",
    "style": "The look of SageMaker reports: their CSS root class, stylesheet and badge.",
    "view": "SageMakerView: the notebook UI, which renders what SageMakerAnalyzer returns as cards and tables.",
}

REEXPORT_KIT = ["HOURS_PER_MONTH", "human_age", "human_money", "human_size"]
