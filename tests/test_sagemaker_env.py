import base64
import json
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from botocore.stub import Stubber
from moto import mock_aws

import sagemaker_env as smmod
from sagemaker_env import (
    GB,
    KB,
    MB,
    SAGEMAKER_PRICES,
    Billable,
    Clearable,
    DiskEntry,
    DiskReport,
    Environment,
    GPUInfo,
    InstanceReport,
    Machine,
    NotebookInfo,
    ProcessInfo,
    RunningReport,
    SageMakerAnalyzer,
    SageMakerView,
    Volume,
    apply_studio_settings,
    clear_command,
    describe_instance,
    disk_findings,
    folder_tree,
    hourly_price,
    human_runtime,
    idle_shutdown_commands,
    instance_findings,
    lifecycle_idle,
    notebook_costs,
    parse_app,
    parse_endpoint,
    parse_gpus,
    parse_loadavg,
    parse_meminfo,
    parse_metadata,
    parse_notebook_instance,
    parse_process,
    parse_processing_job,
    parse_training_job,
    running_findings,
    shell_path,
    smaller_type,
    stop_command,
    studio_idle,
)

ACCOUNT = "123456789012"
REGION = "us-east-1"
DOMAIN = "d-abc123def456"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/service-role/AmazonSageMaker-ExecutionRole-2024"
NOW = datetime.now(timezone.utc)


def ago(**kwargs):
    return NOW - timedelta(**kwargs)


NOTEBOOK_META = {
    "ResourceArn": f"arn:aws:sagemaker:{REGION}:{ACCOUNT}:notebook-instance/acme-nb",
    "ResourceName": "acme-nb",
}
SPACE_META = {
    "AppType": "JupyterLab",
    "DomainId": DOMAIN,
    "SpaceName": "churn-analysis",
    "UserProfileName": "",
    "ExecutionRoleArn": ROLE,
    "ResourceArn": f"arn:aws:sagemaker:{REGION}:{ACCOUNT}:app/{DOMAIN}/churn-analysis/JupyterLab/default",
    "ResourceName": "default",
    "AppImageVersion": "latest",
}


# ----------------------------------------------------------------------------- helpers


def test_human_runtime():
    assert human_runtime(None) == "-"
    assert human_runtime(42) == "42s"
    assert human_runtime(3000) == "50m"
    assert human_runtime(7500) == "2h 05m"
    assert human_runtime(273600) == "3d 4h"
    assert human_runtime(-5) == "0s"


def test_prices_and_instance_specs():
    assert hourly_price("ml.m5.xlarge") == 0.23
    assert hourly_price("system") == 0.0
    assert hourly_price("ml.x9.huge") is None
    assert hourly_price("ml.m5.xlarge", {**SAGEMAKER_PRICES, "ml.m5.xlarge": 0.2}) == 0.2
    assert describe_instance("ml.g5.xlarge") == "4 vCPU · 16 GiB · 1 GPU"
    assert describe_instance("ml.t3.medium") == "2 vCPU · 4 GiB"
    assert describe_instance("ml.x9.huge") == ""
    assert "no charge" in describe_instance("system")
    assert SAGEMAKER_PRICES["notebook_storage"] == 0.14


def test_parse_metadata_notebook_instance_and_local():
    env = parse_metadata(NOTEBOOK_META)
    assert (env.kind, env.name, env.region) == ("notebook instance", "acme-nb", REGION)
    assert env.label == "notebook instance acme-nb" and env.on_sagemaker
    assert parse_metadata({}).kind == "local" and not parse_metadata({}).on_sagemaker


def test_parse_metadata_studio_apps():
    env = parse_metadata(SPACE_META)
    assert (env.kind, env.domain_id, env.space, env.app_type, env.name) == (
        "studio", DOMAIN, "churn-analysis", "JupyterLab", "default")
    assert env.role_arn == ROLE and env.label == "JupyterLab space churn-analysis"
    # Space-based apps that don't write SpaceName: it comes from the ARN.
    arn_only = parse_metadata({"ResourceArn": SPACE_META["ResourceArn"], "AppType": "JupyterLab"})
    assert arn_only.space == "churn-analysis" and arn_only.domain_id == DOMAIN
    classic = parse_metadata({
        "AppType": "KernelGateway", "DomainId": DOMAIN, "UserProfileName": "alice", "ResourceName": "datascience-1-0",
        "ResourceArn": f"arn:aws:sagemaker:{REGION}:{ACCOUNT}:app/{DOMAIN}/alice/KernelGateway/datascience-1-0",
    })
    assert classic.user_profile == "alice" and classic.space == ""
    assert classic.label == "Studio Classic kernel app datascience-1-0"


def test_parse_local_files():
    mem = parse_meminfo("MemTotal:       16000000 kB\nMemAvailable:    4000000 kB\nHugePages_Total: 0\n")
    assert mem["MemTotal"] == 16000000 * KB and mem["MemAvailable"] == 4000000 * KB and mem["HugePages_Total"] == 0
    assert parse_loadavg("0.52 0.40 1.25 2/300 4242\n") == (0.52, 0.40, 1.25)
    gpus = parse_gpus("NVIDIA A10G, 0, 3, 23028\nTesla T4, [N/A], 100, 15360\n")
    assert gpus[0] == GPUInfo("NVIDIA A10G", 0.0, 3 * MB, 23028 * MB) and gpus[0].idle
    assert gpus[1].busy is None and gpus[1].memory_total == 15360 * MB
    assert not GPUInfo("x", 80.0, 10 * GB, 24 * GB).idle
    process = parse_process(42, "Name:\tpython\nVmRSS:\t  2048 kB\n", b"python\0-m\0ipykernel_launcher\0-f\0k.json\0")
    assert process == ProcessInfo(42, "python", "python -m ipykernel_launcher -f k.json", 2048 * KB, kernel=True)
    assert parse_process(2, "Name:\tkthreadd\n", b"") is None  # kernel threads hold no memory


def test_lifecycle_idle():
    sample = "#!/bin/bash\nset -e\nIDLE_TIME=3600\nwget https://.../auto-stop-idle/autostop.py\n"
    assert lifecycle_idle([sample]) == (True, 60)
    assert lifecycle_idle(["python autostop.py --time 1800 --ignore-connections"]) == (True, 30)
    assert lifecycle_idle(["aws sagemaker stop-notebook-instance --notebook-instance-name x"]) == (True, None)
    assert lifecycle_idle(["pip install -r requirements.txt"]) == (False, None)
    assert lifecycle_idle([]) == (False, None)


def idle_block(state="ENABLED", minutes=None):
    settings = {"LifecycleManagement": state}
    if minutes:
        settings["IdleTimeoutInMinutes"] = minutes
    return {"JupyterLabAppSettings": {"AppLifecycleManagement": {"IdleSettings": settings}}}


def domain_desc(*, idle=None, network="PublicInternetOnly", space_idle=None):
    user = {"ExecutionRole": ROLE, **(idle or {})}
    return {"DomainId": DOMAIN, "DomainName": "acme", "DefaultUserSettings": user,
            "DefaultSpaceSettings": {"ExecutionRole": ROLE + "-spaces", **(space_idle or {})},
            "AppNetworkAccessType": network}


def space_desc(*, minutes=None, shared=False, volume=50):
    settings = {"AppType": "JupyterLab", "SpaceStorageSettings": {"EbsStorageSettings": {"EbsVolumeSizeInGb": volume}},
                "JupyterLabAppSettings": {
                    "DefaultResourceSpec": {"InstanceType": "ml.t3.medium"},
                    "CodeRepositories": [{"RepositoryUrl": "https://github.com/acme/churn.git"}]}}
    if minutes:
        settings["JupyterLabAppSettings"]["AppLifecycleManagement"] = {"IdleSettings": {"IdleTimeoutInMinutes": minutes}}
    return {"DomainId": DOMAIN, "SpaceName": "churn-analysis", "Status": "InService", "SpaceSettings": settings,
            "OwnershipSettings": {"OwnerUserProfileName": "alice"},
            "SpaceSharingSettings": {"SharingType": "Shared" if shared else "Private"},
            "Url": "https://d-abc123def456.studio.us-east-1.sagemaker.aws/jupyterlab/default"}


def test_studio_idle():
    assert studio_idle("JupyterLab", domain_desc()) == ("off", None, "domain")
    assert studio_idle("JupyterLab", domain_desc(idle=idle_block(minutes=120))) == ("on", 120, "domain")
    assert studio_idle("JupyterLab", domain_desc(idle=idle_block(minutes=120)), space=space_desc(minutes=90)) == (
        "on", 90, "space")
    profile = {"UserSettings": idle_block("ENABLED", 90)}
    assert studio_idle("JupyterLab", domain_desc(), profile) == ("on", 90, "user profile")
    assert studio_idle("JupyterLab", None) == ("unknown", None, "")
    assert studio_idle("KernelGateway", domain_desc()) == ("off", None, "Studio Classic")
    assert studio_idle("JupyterServer", None)[0] == "n/a"
    shared = domain_desc(space_idle=idle_block(minutes=30))
    assert studio_idle("JupyterLab", shared, space=space_desc(shared=True)) == ("on", 30, "domain")
    assert studio_idle("CodeEditor", domain_desc(idle=idle_block(minutes=120)))[0] == "off"  # JupyterLab's only


def app_desc(*, instance="ml.m5.xlarge", status="InService", created=None, space="churn-analysis", app="JupyterLab"):
    desc = {"AppArn": f"arn:aws:sagemaker:{REGION}:{ACCOUNT}:app/{DOMAIN}/{space}/{app}/default",
            "AppType": app, "AppName": "default", "DomainId": DOMAIN, "SpaceName": space, "Status": status,
            "CreationTime": created or ago(hours=30),
            "ResourceSpec": {"InstanceType": instance,
                             "SageMakerImageArn": f"arn:aws:sagemaker:{REGION}:081325390199:image/sagemaker-distribution-cpu",
                             "SageMakerImageVersionAlias": "2.6.0"}}
    return desc


def test_parse_app_and_studio_settings():
    nb = parse_app(app_desc())
    assert (nb.kind, nb.name, nb.space, nb.instance_type, nb.platform) == (
        "studio app", "default", "churn-analysis", "ml.m5.xlarge", "sagemaker-distribution-cpu 2.6.0")
    profile = {"UserSettings": {"ExecutionRole": ROLE + "-alice"}}
    apply_studio_settings(nb, domain=domain_desc(network="VpcOnly"), profile=profile, space=space_desc())
    assert nb.volume_gb == 50 and nb.shared is False and nb.user_profile == "alice"
    assert nb.role_arn == ROLE + "-alice" and nb.internet == "through your VPC only"
    assert nb.code_repositories == ["https://github.com/acme/churn.git"] and nb.idle == "off"
    assert nb.label == "JupyterLab space churn-analysis" and nb.billing
    shared = apply_studio_settings(parse_app(app_desc()), domain=domain_desc(), space=space_desc(shared=True))
    assert shared.role_arn == ROLE + "-spaces"
    stopped = apply_studio_settings(NotebookInfo("studio app", "", status="Stopped", domain_id=DOMAIN,
                                                 space="churn-analysis", app_type="JupyterLab"),
                                    domain=domain_desc(), space=space_desc())
    assert stopped.instance_type == "ml.t3.medium" and not stopped.billing  # the type it would start with


def nb_desc(name="acme-nb", *, instance="ml.m5.xlarge", status="InService", lifecycle=None, changed=None,
            volume=50, platform="notebook-al2023-v1"):
    desc = {"NotebookInstanceArn": f"arn:aws:sagemaker:{REGION}:{ACCOUNT}:notebook-instance/{name}",
            "NotebookInstanceName": name, "NotebookInstanceStatus": status, "InstanceType": instance,
            "RoleArn": ROLE, "DirectInternetAccess": "Enabled", "VolumeSizeInGB": volume, "RootAccess": "Enabled",
            "PlatformIdentifier": platform, "CreationTime": ago(days=90), "LastModifiedTime": changed or ago(days=3),
            "Url": f"{name}.notebook.{REGION}.sagemaker.aws"}
    if lifecycle:
        desc["NotebookInstanceLifecycleConfigName"] = lifecycle
    return desc


def test_parse_notebook_instance_and_costs():
    nb = parse_notebook_instance({**nb_desc(lifecycle="autostop"), "DefaultCodeRepository": "repo-a",
                                  "AdditionalCodeRepositories": ["repo-b"]})
    assert (nb.kind, nb.name, nb.status, nb.instance_type, nb.volume_gb) == (
        "notebook instance", "acme-nb", "InService", "ml.m5.xlarge", 50)
    assert nb.internet == "direct" and nb.root_access is True and nb.lifecycle_configs == ["autostop"]
    assert nb.code_repositories == ["repo-a", "repo-b"]
    costs = notebook_costs(nb)
    assert costs["hourly"] == 0.23 and costs["month_if_on"] == pytest.approx(167.9) and costs["storage"] == pytest.approx(7.0)
    space = NotebookInfo("studio app", "default", instance_type="ml.t3.medium", volume_gb=10)
    assert notebook_costs(space)["storage"] == pytest.approx(1.12)
    assert notebook_costs(NotebookInfo("studio app", "x"))["hourly"] is None


def test_parse_endpoint_and_jobs():
    desc = {"EndpointName": "churn-v2", "EndpointArn": "arn:e", "EndpointConfigName": "churn-v2-config",
            "EndpointStatus": "InService", "CreationTime": ago(days=40),
            "ProductionVariants": [{"VariantName": "AllTraffic", "CurrentInstanceCount": 2}]}
    config = {"EndpointConfigName": "churn-v2-config", "ProductionVariants": [
        {"VariantName": "AllTraffic", "ModelName": "m", "InstanceType": "ml.m5.large", "InitialInstanceCount": 1}]}
    b = parse_endpoint(desc, config)
    assert b.instances == [("ml.m5.large", 2)] and b.variants == ["AllTraffic"] and b.config == "churn-v2-config"
    assert b.hourly() == pytest.approx(0.23) and b.instance_label == "ml.m5.large × 2"
    serverless = parse_endpoint(
        {**desc, "ProductionVariants": [{"VariantName": "v", "CurrentServerlessConfig": {"MemorySizeInMB": 2048}}]},
        {"ProductionVariants": [{"VariantName": "v", "ServerlessConfig": {"MemorySizeInMB": 2048}}]})
    assert serverless.serverless and serverless.instance_label == "serverless" and serverless.hourly() == 0.0
    training = parse_training_job({"TrainingJobName": "xgb-7", "TrainingJobArn": f"arn:aws:sagemaker:{REGION}:{ACCOUNT}:training-job/xgb-7", "TrainingJobStatus": "InProgress",
                                   "SecondaryStatus": "Training", "EnableManagedSpotTraining": True,
                                   "TrainingStartTime": ago(hours=2),
                                   "ResourceConfig": {"InstanceType": "ml.g5.2xlarge", "InstanceCount": 2}})
    assert training.instances == [("ml.g5.2xlarge", 2)] and training.spot and training.status == "Training"
    processing = parse_processing_job({"ProcessingJobName": "prep", "ProcessingJobStatus": "InProgress",
                                       "ProcessingResources": {"ClusterConfig": {
                                           "InstanceType": "ml.m5.xlarge", "InstanceCount": 1, "VolumeSizeInGB": 30}}})
    assert processing.instances == [("ml.m5.xlarge", 1)] and processing.kind == "processing job"
    assert Billable("endpoint", "x", instances=[("ml.x9.huge", 1)]).hourly() is None


def test_smaller_type():
    assert smaller_type("ml.m5.4xlarge", vcpus=2, memory_gib=6) == "ml.t3.large"
    assert smaller_type("ml.t3.medium", vcpus=2, memory_gib=4) is None  # nothing cheaper fits
    assert smaller_type("ml.g5.xlarge", vcpus=4, memory_gib=16) == "ml.t3.xlarge"  # no GPU
    assert smaller_type("ml.g5.xlarge", vcpus=4, memory_gib=16, burstable=False) == "ml.m5.xlarge"
    assert smaller_type("ml.m5.4xlarge", vcpus=2, memory_gib=6, allowed={"ml.m5.large", "ml.m5.4xlarge"}) == "ml.m5.large"
    assert smaller_type("ml.x9.huge", vcpus=2, memory_gib=4) is None


def test_stop_and_idle_commands():
    nb = parse_notebook_instance(nb_desc())
    assert stop_command(nb) == "aws sagemaker stop-notebook-instance --notebook-instance-name acme-nb"
    app = apply_studio_settings(parse_app(app_desc()), domain=domain_desc())
    assert stop_command(app) == (f"aws sagemaker delete-app --domain-id {DOMAIN} --space-name churn-analysis "
                                 "--app-type JupyterLab --app-name default")
    assert "delete-endpoint --endpoint-name e1" in stop_command(Billable("endpoint", "e1"))
    commands = idle_shutdown_commands(nb)
    assert "create-notebook-instance-lifecycle-config" in commands and "--lifecycle-config-name auto-stop-idle" in commands
    studio = idle_shutdown_commands(app, minutes=90)
    assert f"update-domain --domain-id {DOMAIN}" in studio and '"IdleTimeoutInMinutes": 90' in studio
    assert json.loads(studio.rsplit("'", 2)[1])["JupyterLabAppSettings"]["AppLifecycleManagement"]
    classic = NotebookInfo("studio app", "x", app_type="KernelGateway", domain_id=DOMAIN, user_profile="alice")
    assert idle_shutdown_commands(classic) == "" and "--user-profile-name alice" in stop_command(classic)


def machine(**kwargs):
    base = dict(cpus=4, load=(0.2, 0.3, 0.4), memory_total=16 * GB, memory_available=10 * GB,
                volumes=[Volume("/home/ec2-user/SageMaker", "notebook volume", 50 * GB, 10 * GB, 40 * GB)])
    return Machine(**{**base, **kwargs})


def report_for(nb, m=None, **kwargs):
    return InstanceReport(env=parse_metadata(NOTEBOOK_META), notebook=nb, machine=m, **kwargs)


def test_instance_findings_idle_shutdown():
    nb = parse_notebook_instance(nb_desc())
    nb.idle = "off"
    found = instance_findings(report_for(nb, machine()))
    assert [level for level, _ in found] == ["warn"]
    assert "bills $0.23/hour, up to $167.90/month" in found[0][1]
    assert "aws sagemaker stop-notebook-instance --notebook-instance-name acme-nb" in found[0][1]
    nb.idle, nb.idle_minutes = "on", 60
    assert instance_findings(report_for(nb, machine())) == []
    app = apply_studio_settings(parse_app(app_desc()), domain=domain_desc())
    found = instance_findings(report_for(app))
    assert found[0][0] == "warn" and f"Domain {DOMAIN} doesn't shut idle JupyterLab apps down" in found[0][1]
    assert "delete-app" in found[0][1]


def test_instance_findings_machine():
    nb = parse_notebook_instance(nb_desc(instance="ml.g5.xlarge"))
    nb.idle = "on"
    kernels = [ProcessInfo(1, "python", "k1", 6 * GB, kernel=True, this=True),
               ProcessInfo(2, "python", "k2", 5 * GB, kernel=True)]
    m = machine(memory_available=1 * GB, processes=kernels, kernels=2, kernels_memory=11 * GB, this_memory=6 * GB,
                volumes=[Volume("/home/ec2-user/SageMaker", "notebook volume", 50 * GB, 46 * GB, 4 * GB)],
                gpus=[GPUInfo("NVIDIA A10G", 0.0, 0, 22 * GB)])
    text = " ".join(message for _, message in instance_findings(report_for(nb, m)))
    assert "is 92% full: 4.0 GB free of 50.0 GB" in text and "disk('/home/ec2-user/SageMaker')" in text
    assert "Memory is 94% used" in text and "1 other notebook kernel holds 5.0 GB" in text
    assert "its GPU is idle right now" in text and "ml.m5.xlarge (4 vCPU · 16 GiB) has as many vCPUs and as much memory without one, for $0.23/hour" in text
    assert "$1.18/hour less" in text


def test_instance_findings_oversized_and_errors():
    nb = parse_notebook_instance(nb_desc(instance="ml.m5.4xlarge"))
    nb.idle = "on"
    nb.errors = {"lifecycle": "AccessDeniedException"}
    m = machine(cpus=16, load=(0.1, 0.1, 0.2), memory_total=64 * GB, memory_available=62 * GB,
                errors={"GPUs": "nvidia-smi timed out"})
    found = instance_findings(report_for(nb, m, errors={"identity": "ExpiredToken"}))
    text = " ".join(message for _, message in found)
    assert "is mostly idle" in text and "ml.t3.medium (2 vCPU · 4 GiB) costs $0.05/hour" in text
    assert "needs sagemaker:DescribeNotebookInstanceLifecycleConfig), so auto-stop shows as unknown" in text
    assert "Couldn't read this machine's GPUs (nvidia-smi timed out)" in text
    assert "who you're signed in as (ExpiredToken)" in text
    unknown = parse_notebook_instance(nb_desc(instance="ml.x9.huge"))
    assert any("isn't in the price table" in m for _, m in instance_findings(report_for(unknown)))
    al1 = parse_notebook_instance(nb_desc(platform="notebook-al1-v1"))
    assert any("Amazon Linux 1" in m for _, m in instance_findings(report_for(al1)))


def disk_report(**kwargs):
    base = dict(path="/home/ec2-user/SageMaker",
                volume=Volume("/home/ec2-user/SageMaker", "notebook volume", 50 * GB, 45 * GB, 5 * GB),
                total=DiskEntry("", 40 * GB, 1000),
                folders={"data": DiskEntry("data", 30 * GB, 10), "data/raw": DiskEntry("data/raw", 25 * GB, 5),
                         "models": DiskEntry("models", 6 * GB, 3)},
                largest=[DiskEntry("data/raw/2023.parquet", 12 * GB, 1, NOW - timedelta(days=200))],
                clearable=[Clearable("Jupyter trash", "why", ["/home/ec2-user/SageMaker/.Trash-1000"], 3 * GB, 40)])
    return DiskReport(**{**base, **kwargs})


def test_disk_findings():
    found = disk_findings(disk_report(), parse_metadata(NOTEBOOK_META))
    text = " ".join(m for _, m in found)
    assert [level for level, _ in found] == ["warn", "warn", "info"]
    assert "The disk is 90% full" in text and "disk('/home/ec2-user/SageMaker/data')" in text
    assert "--notebook-instance-name acme-nb --volume-size-in-gb 100" in text
    assert "3.0 GB is caches and trash that are safe to empty (Jupyter trash 3.0 GB)" in text
    assert "1 file over 1 GB hasn't changed in 90 days" in text and "frees 12.0 GB on this disk" in text
    assert "about $0.28/month rather than $1.68/month here" in text
    studio = disk_findings(disk_report(), parse_metadata(SPACE_META))[0][1]
    assert f"update-space --domain-id {DOMAIN} --space-name churn-analysis" in studio
    assert "SpaceStorageSettings={EbsStorageSettings={EbsVolumeSizeInGb=100}}" in studio
    roomy = disk_report(volume=Volume("/x", "disk", 50 * GB, 5 * GB, 45 * GB), clearable=[], largest=[],
                        truncated=True)
    assert disk_findings(roomy) == [("info", "Stopped after 1,000 files, so the sizes are at least these. "
                                             "disk('/home/ec2-user/SageMaker', limit=None) measures everything.")]
    efs = disk_report(volume=Volume("/root", "home", 8 * 2**60, 3 * GB, 8 * 2**60), clearable=[], largest=[])
    assert disk_findings(efs) == []


def test_folder_tree_and_shell_paths():
    rows = [(depth, entry.path) for depth, entry in folder_tree(disk_report())]
    assert rows == [(0, "data"), (1, "data/raw"), (0, "models")]
    assert shell_path("/home/u/SageMaker/my data", "/home/u") == "~/'SageMaker/my data'"
    assert shell_path("/home/u", "/home/u") == "~" and shell_path("/opt/x", "/home/u") == "/opt/x"
    trash = Clearable("Jupyter trash", "", ["/home/u/SageMaker/.Trash-1000"])
    assert clear_command(trash, "/home/u/SageMaker", "/home/u") == "rm -rf ~/SageMaker/.Trash-1000/*"
    checkpoints = Clearable("notebook checkpoints", "", ["/home/u/a/.ipynb_checkpoints"])
    assert clear_command(checkpoints, "/home/u", "/home/u") == (
        "find ~ -name .ipynb_checkpoints -type d -prune -exec rm -rf {} +")
    assert clear_command(Clearable("pip cache", ""), "/x") == "pip cache purge"


def running_report(**kwargs):
    busy = Billable("notebook instance", "old-experiment", "InService", [("ml.m5.2xlarge", 1)], since=ago(days=6),
                    idle="off", volume_gb=20)
    mine = Billable("notebook instance", "acme-nb", "InService", [("ml.t3.medium", 1)], since=ago(days=6),
                    idle="off", this=True)
    app = Billable("Studio app", "churn-analysis", "InService", [("ml.g5.xlarge", 1)], since=ago(days=2), idle="off",
                   domain_id=DOMAIN, space="churn-analysis", app_type="JupyterLab", app_name="default")
    endpoint = Billable("endpoint", "churn-v1", "InService", [("ml.m5.large", 1)], since=ago(days=60),
                        config="churn-v1-config", variants=["AllTraffic"], invocations=0.0)
    new_endpoint = Billable("endpoint", "churn-v2", "InService", [("ml.m5.large", 1)], since=ago(hours=3),
                            variants=["AllTraffic"], invocations=0.0)
    stopped = [Billable("notebook instance", "archive", "Stopped", [("ml.t3.medium", 1)], since=ago(days=120),
                        volume_gb=200)]
    base = dict(region=REGION, resources=[busy, mine, app, endpoint, new_endpoint], stopped=stopped)
    return RunningReport(**{**base, **kwargs})


def test_running_findings():
    found = running_findings(running_report(), now=NOW)
    warns = [m for level, m in found if level == "warn"]
    assert len(warns) == 3  # the app, the notebook instance, the idle endpoint (not this notebook, not the new one)
    assert warns[0].startswith("JupyterLab app in space churn-analysis (ml.g5.xlarge) has been running for 2d 0h")
    assert "delete-app --domain-id" in warns[0] and "the files in the space stay" in warns[0]
    assert "Notebook instance old-experiment (ml.m5.2xlarge) has been running for 6d 0h, about $66.38 so far" in warns[1]
    assert "stop-notebook-instance --notebook-instance-name old-experiment" in warns[1]
    assert "Endpoint churn-v1 (ml.m5.large) got no requests in the last 7 days" in warns[2]
    assert "create-endpoint --endpoint-name churn-v1 --endpoint-config-name churn-v1-config" in warns[2]
    text = " ".join(m for _, m in found)
    assert "1 stopped notebook instance keeps its storage volume (200 GB), costing $28.00/month" in text
    assert "delete-notebook-instance --notebook-instance-name archive" in text


def test_running_findings_many_and_errors():
    many = [Billable("notebook instance", f"nb-{i}", "InService", [("ml.t3.medium", 1)], since=ago(days=2), idle="off")
            for i in range(7)]
    found = running_findings(RunningReport(REGION, resources=many, errors={"metrics": "AccessDenied"}), now=NOW)
    assert sum(level == "warn" for level, _ in found) == 6
    assert "2 more notebooks (nb-5, nb-6) run with nothing to stop them when idle: $0.10/hour together." in found[5][1]
    assert found[-1] == ("info", "Couldn't read endpoint traffic (AccessDenied; needs cloudwatch:GetMetricData), "
                                 "so endpoints show unknown traffic.")
    assert running_findings(RunningReport(REGION)) == []


# ----------------------------------------------------------------------------- this machine (a fake root)


def write_root(tmp_path, metadata=NOTEBOOK_META, *, home="home/ec2-user/SageMaker", memory=(16_000_000, 4_000_000)):
    """A folder that looks like a SageMaker notebook's /: metadata, /proc and a home folder."""
    if metadata is not None:
        meta = tmp_path / "opt/ml/metadata"
        meta.mkdir(parents=True)
        (meta / "resource-metadata.json").write_text(json.dumps(metadata))
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "loadavg").write_text("0.20 0.30 0.40 1/200 999\n")
    (proc / "meminfo").write_text(f"MemTotal: {memory[0]} kB\nMemFree: 1000 kB\nMemAvailable: {memory[1]} kB\n")
    (proc / "uptime").write_text("273600.25 900.0\n")
    for pid, rss, cmd in ((100, 3_000_000, b"python\0-m\0ipykernel_launcher\0-f\0a.json\0"),
                          (101, 5_000_000, b"python\0-m\0ipykernel_launcher\0-f\0b.json\0"),
                          (102, 200_000, b"jupyter-lab\0--ip=0.0.0.0\0"), (2, None, b"")):
        (proc / str(pid)).mkdir()
        (proc / str(pid) / "status").write_text("Name:\tpython\n" + (f"VmRSS:\t{rss} kB\n" if rss else ""))
        (proc / str(pid) / "cmdline").write_bytes(cmd)
    (proc / "self").mkdir()  # not a process id
    (tmp_path / home).mkdir(parents=True)
    return tmp_path


def fake_machine(core, *, total=50 * GB, used=20 * GB, gpus=None):
    core.pid = 100
    core._cpu_count = lambda: 4
    core._disk_usage = lambda path: (total, used, total - used)
    core._gpu_query = lambda: gpus
    return core


def test_machine_reads_this_machine(tmp_path):
    core = fake_machine(SageMakerAnalyzer(root=str(write_root(tmp_path))), gpus="NVIDIA A10G, 0, 3, 23028\n")
    m = core.machine()
    assert m.cpus == 4 and m.load == (0.2, 0.3, 0.4) and m.cpu_share == pytest.approx(0.1)
    assert m.memory_total == 16_000_000 * KB and m.memory_share == pytest.approx(0.75)
    assert m.booted is not None and 3.1 < (NOW - m.booted).total_seconds() / 86400 < 3.2
    assert m.kernels == 2 and m.kernels_memory == 8_000_000 * KB and m.this_memory == 3_000_000 * KB
    assert [p.pid for p in m.processes] == [101, 100, 102] and m.processes[1].this
    assert m.gpus[0].name == "NVIDIA A10G" and m.errors == {}
    assert [v.title for v in m.volumes] == ["notebook volume (kept when the instance stops)"]  # same disk once
    assert m.python and "boto3" in m.packages


def test_machine_notes_what_it_cannot_read(tmp_path):
    core = fake_machine(SageMakerAnalyzer(root=str(tmp_path)))

    def broken():
        raise OSError("nvidia-smi timed out")

    core._gpu_query = broken
    m = core.machine()
    assert set(m.errors) == {"load", "memory", "uptime", "processes", "GPUs"}
    assert m.errors["GPUs"] == "nvidia-smi timed out" and m.memory_total is None and m.load is None


def test_environment(tmp_path):
    assert SageMakerAnalyzer(root=str(tmp_path)).environment().kind == "local"
    (tmp_path / "opt/ml/metadata").mkdir(parents=True)
    (tmp_path / "opt/ml/metadata/resource-metadata.json").write_text("{not json")
    env = SageMakerAnalyzer(root=str(tmp_path)).environment()
    assert env.kind == "local" and "couldn't be read (JSONDecodeError" in env.error
    root = write_root(tmp_path / "studio", SPACE_META, home="home/sagemaker-user")
    core = SageMakerAnalyzer(root=str(root))
    assert core.environment().space == "churn-analysis" and core.home() == root / "home/sagemaker-user"


def write_tree(home):
    files = {
        "data/raw/2023.parquet": 400 * KB,
        "data/raw/2024.parquet": 300 * KB,
        "data/clean/train.csv": 100 * KB,
        "models/xgb/model.tar.gz": 50 * KB,
        ".Trash-1000/files/old-export.csv": 250 * KB,
        ".Trash-1000/info/old-export.csv.trashinfo": 1 * KB,
        "notebooks/.ipynb_checkpoints/eda-checkpoint.ipynb": 20 * KB,
        "notebooks/eda.ipynb": 20 * KB,
        ".cache/pip/http/wheel.whl": 80 * KB,
        "README.md": 2 * KB,
    }
    for rel, size in files.items():
        path = home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
    (home / "link-to-data").symlink_to(home / "data")  # links aren't followed
    return files


def test_disk_walk(tmp_path):
    root = write_root(tmp_path)
    home = root / "home/ec2-user/SageMaker"
    files = write_tree(home)
    core = fake_machine(SageMakerAnalyzer(root=str(root)), total=50 * GB, used=46 * GB)
    counts = []
    report = core.disk(top=3, progress=counts.append)
    assert report.total.files == len(files) and report.total.size == sum(files.values())
    assert report.folders["data"].size == 800 * KB and report.folders["data/raw"].files == 2
    assert "data/raw/2023.parquet" not in report.folders  # folders only, 3 levels deep
    assert [e.path for e in report.largest] == ["data/raw/2023.parquet", "data/raw/2024.parquet",
                                                ".Trash-1000/files/old-export.csv"]
    kinds = {c.what: c for c in report.clearable}
    assert set(kinds) == {"Jupyter trash", "pip cache", "notebook checkpoints"}
    assert kinds["Jupyter trash"].size == 251 * KB and kinds["Jupyter trash"].files == 2
    assert kinds["Jupyter trash"].command == "rm -rf ~/SageMaker/.Trash-1000/*"  # as the machine sees it
    assert report.path == "/home/ec2-user/SageMaker" and kinds["Jupyter trash"].paths == [
        "/home/ec2-user/SageMaker/.Trash-1000"]
    assert core.disk("/home/ec2-user/SageMaker/data").total.files == 3  # a path as shown maps to the fake root
    assert core.disk("~/SageMaker/models").total.size == 50 * KB
    assert report.volume.share == pytest.approx(0.92) and report.volume.label == "notebook volume"
    assert not report.truncated and counts == []  # fewer than 1,000 files: no progress ticks
    limited = core.disk(home, limit=3)
    assert limited.truncated and limited.total.files == 3
    with pytest.raises(ValueError, match="There's no folder"):
        core.disk(home / "nope")
    with pytest.raises(ValueError, match="is a file"):
        core.disk(home / "README.md")


# ----------------------------------------------------------------------------- AWS (moto)


@pytest.fixture
def aws():
    with mock_aws():
        yield boto3.client("sagemaker", region_name=REGION)


def autostop_config(sm, name="autostop", idle_time=1800):
    script = f"#!/bin/bash\nIDLE_TIME={idle_time}\nwget https://.../auto-stop-idle/autostop.py\n"
    sm.create_notebook_instance_lifecycle_config(
        NotebookInstanceLifecycleConfigName=name, OnStart=[{"Content": base64.b64encode(script.encode()).decode()}])


def test_notebook_instance_reads_autostop(aws):
    autostop_config(aws)
    aws.create_notebook_instance(NotebookInstanceName="team-nb", InstanceType="ml.c5.2xlarge", RoleArn=ROLE,
                                 LifecycleConfigName="autostop", VolumeSizeInGB=100)
    aws.create_notebook_instance(NotebookInstanceName="plain", InstanceType="ml.t3.medium", RoleArn=ROLE)
    core = SageMakerAnalyzer(region=REGION)
    nb = core.notebook("team-nb")
    assert (nb.idle, nb.idle_minutes, nb.idle_source) == ("on", 30, "lifecycle configuration 'autostop'")
    assert nb.volume_gb == 100 and nb.role_arn == ROLE and nb.status == "InService"
    assert core.notebook("plain").idle == "off"
    assert core.notebook(f"arn:aws:sagemaker:{REGION}:{ACCOUNT}:notebook-instance/plain").name == "plain"
    with pytest.raises(ValueError, match="Did you mean 'plain'"):
        core.notebook("Plain")


def test_instance_of_this_notebook_instance(aws, tmp_path):
    aws.create_notebook_instance(NotebookInstanceName="acme-nb", InstanceType="ml.m5.xlarge", RoleArn=ROLE,
                                 VolumeSizeInGB=50)
    core = fake_machine(SageMakerAnalyzer(region=REGION, root=str(write_root(tmp_path))))
    report = core.instance()
    assert report.current and report.account == ACCOUNT and report.env.kind == "notebook instance"
    assert report.notebook.name == "acme-nb" and report.notebook.idle == "off"
    assert report.machine is not None and report.machine.cpus == 4
    assert core.region == REGION and "ml.m5.xlarge" in core.allowed_types("notebook instance")
    assert "ml.t3.medium" in core.allowed_types("studio app") and "system" not in core.allowed_types("studio app")


def test_instance_outside_sagemaker(aws, tmp_path):
    report = fake_machine(SageMakerAnalyzer(region=REGION, root=str(tmp_path))).instance()
    assert report.env.kind == "local" and report.notebook is None and report.machine is None


# ----------------------------------------------------------------------------- AWS (Stubber)


def denied(stub, operation, action=None):
    action = action or "sagemaker:" + "".join(word.title() for word in operation.split("_"))
    stub.add_client_error(operation, service_error_code="AccessDeniedException", http_status_code=403,
                          service_message=f"User: arn:aws:sts::{ACCOUNT}:assumed-role/r/s is not authorized to "
                                          f"perform: {action}")


class Stubs:
    """Real boto3 clients with a botocore Stubber on each: every call must be queued, and its parameters are
    checked against the service model. moto has no Studio apps, spaces or ListApps."""

    def __init__(self):
        self.clients = {name: boto3.client(name, region_name=REGION) for name in ("sagemaker", "sts", "cloudwatch")}
        self.stubs = {name: Stubber(client) for name, client in self.clients.items()}
        self.sm, self.sts, self.cw = self.stubs["sagemaker"], self.stubs["sts"], self.stubs["cloudwatch"]
        for stub in self.stubs.values():
            stub.activate()

    def analyzer(self, root, **kwargs):
        core = SageMakerAnalyzer(clients=dict(self.clients), root=str(root), **kwargs)
        core.max_workers = 1  # a Stubber answers in order
        return fake_machine(core)

    def done(self):
        for stub in self.stubs.values():
            stub.assert_no_pending_responses()
            stub.deactivate()

    def identity(self):
        self.sts.add_response("get_caller_identity", {
            "UserId": "AROA:SageMaker", "Account": ACCOUNT,
            "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/AmazonSageMaker-ExecutionRole-2024/SageMaker"}, {})

    def studio(self, *, app=None, domain=None, space=None, profile=True):
        """What instance() reads for the churn-analysis space: the app, the domain, the space, its owner."""
        self.sm.add_response("describe_app", app or app_desc(), {
            "DomainId": DOMAIN, "AppType": "JupyterLab", "AppName": "default", "SpaceName": "churn-analysis"})
        self.sm.add_response("describe_domain", domain or domain_desc(), {"DomainId": DOMAIN})
        self.sm.add_response("describe_space", space or space_desc(), {"DomainId": DOMAIN,
                                                                        "SpaceName": "churn-analysis"})
        if profile:
            self.sm.add_response("describe_user_profile", {
                "DomainId": DOMAIN, "UserProfileName": "alice", "UserSettings": {"ExecutionRole": ROLE}},
                {"DomainId": DOMAIN, "UserProfileName": "alice"})

    def running(self, *, apps_denied=False, metrics=True):
        self.sm.add_response("list_notebook_instances", {"NotebookInstances": [
            {k: v for k, v in nb_desc("old-experiment", instance="ml.m5.2xlarge", changed=ago(days=6)).items()
             if k in ("NotebookInstanceName", "NotebookInstanceArn", "NotebookInstanceStatus", "InstanceType",
                      "CreationTime", "LastModifiedTime", "Url")},
            {"NotebookInstanceName": "archive", "NotebookInstanceArn": f"arn:aws:sagemaker:{REGION}:{ACCOUNT}:notebook-instance/archive", "NotebookInstanceStatus": "Stopped",
             "InstanceType": "ml.t3.medium", "LastModifiedTime": ago(days=120)},
            {"NotebookInstanceName": "gone", "NotebookInstanceArn": f"arn:aws:sagemaker:{REGION}:{ACCOUNT}:notebook-instance/gone", "NotebookInstanceStatus": "Deleting"},
        ]}, {})
        self.sm.add_response("describe_notebook_instance", nb_desc("old-experiment", instance="ml.m5.2xlarge",
                                                                   changed=ago(days=6)),
                             {"NotebookInstanceName": "old-experiment"})
        self.sm.add_response("describe_notebook_instance", nb_desc("archive", instance="ml.t3.medium",
                                                                   status="Stopped", volume=200,
                                                                   changed=ago(days=120)),
                             {"NotebookInstanceName": "archive"})
        if apps_denied:
            denied(self.sm, "list_apps")
        else:
            self.sm.add_response("list_apps", {"Apps": [
                {k: v for k, v in app_desc(instance="ml.g5.xlarge", created=ago(days=2)).items() if k != "AppArn"},
                {"DomainId": DOMAIN, "UserProfileName": "alice", "AppType": "JupyterServer", "AppName": "default",
                 "Status": "InService", "ResourceSpec": {"InstanceType": "system"}},
                {**{k: v for k, v in app_desc(space="old").items() if k != "AppArn"}, "Status": "Deleted"},
            ]}, {})
            self.sm.add_response("describe_domain", domain_desc(), {"DomainId": DOMAIN})
        self.sm.add_response("list_endpoints", {"Endpoints": [
            {"EndpointName": "churn-v1", "EndpointArn": f"arn:aws:sagemaker:{REGION}:{ACCOUNT}:endpoint/churn-v1", "CreationTime": ago(days=60),
             "LastModifiedTime": ago(days=60), "EndpointStatus": "InService"},
            {"EndpointName": "broken", "EndpointArn": f"arn:aws:sagemaker:{REGION}:{ACCOUNT}:endpoint/broken", "CreationTime": ago(days=1),
             "LastModifiedTime": ago(days=1), "EndpointStatus": "Failed"},
        ]}, {})
        self.sm.add_response("describe_endpoint", {
            "EndpointName": "churn-v1", "EndpointArn": f"arn:aws:sagemaker:{REGION}:{ACCOUNT}:endpoint/churn-v1", "EndpointConfigName": "churn-v1-config",
            "EndpointStatus": "InService", "CreationTime": ago(days=60), "LastModifiedTime": ago(days=60),
            "ProductionVariants": [{"VariantName": "AllTraffic", "CurrentInstanceCount": 1}]},
            {"EndpointName": "churn-v1"})
        self.sm.add_response("describe_endpoint_config", {
            "EndpointConfigName": "churn-v1-config", "EndpointConfigArn": f"arn:aws:sagemaker:{REGION}:{ACCOUNT}:endpoint-config/c", "CreationTime": ago(days=60),
            "ProductionVariants": [{"VariantName": "AllTraffic", "ModelName": "churn", "InstanceType": "ml.m5.large",
                                    "InitialInstanceCount": 1}]}, {"EndpointConfigName": "churn-v1-config"})
        self.sm.add_response("list_training_jobs", {"TrainingJobSummaries": [
            {"TrainingJobName": "xgb-7", "TrainingJobArn": f"arn:aws:sagemaker:{REGION}:{ACCOUNT}:training-job/xgb-7", "CreationTime": ago(hours=2),
             "TrainingJobStatus": "InProgress"}]}, {"StatusEquals": "InProgress"})
        self.sm.add_response("describe_training_job", {
            "TrainingJobName": "xgb-7", "TrainingJobArn": f"arn:aws:sagemaker:{REGION}:{ACCOUNT}:training-job/xgb-7", "TrainingJobStatus": "InProgress",
            "SecondaryStatus": "Training", "CreationTime": ago(hours=2), "TrainingStartTime": ago(hours=2),
            "AlgorithmSpecification": {"TrainingInputMode": "File"}, "EnableManagedSpotTraining": True,
            "ResourceConfig": {"InstanceType": "ml.g5.2xlarge", "InstanceCount": 2, "VolumeSizeInGB": 30},
            "StoppingCondition": {"MaxRuntimeInSeconds": 86400}, "ModelArtifacts": {"S3ModelArtifacts": ""}},
            {"TrainingJobName": "xgb-7"})
        self.sm.add_response("list_processing_jobs", {"ProcessingJobSummaries": []}, {"StatusEquals": "InProgress"})
        if metrics:
            self.cw.add_response("get_metric_data", {"MetricDataResults": [
                {"Id": "q0", "Label": "Invocations", "Timestamps": [], "Values": [], "StatusCode": "Complete"}]}, None)


@pytest.fixture
def stubs():
    s = Stubs()
    yield s
    s.done()


def test_instance_of_this_studio_space(stubs, tmp_path):
    core = stubs.analyzer(write_root(tmp_path, SPACE_META, home="home/sagemaker-user"))
    stubs.identity()
    stubs.studio(domain=domain_desc(idle=idle_block(minutes=120)), space=space_desc(minutes=90))
    report = core.instance()
    nb = report.notebook
    assert (nb.kind, nb.space, nb.instance_type, nb.volume_gb) == ("studio app", "churn-analysis", "ml.m5.xlarge", 50)
    assert (nb.idle, nb.idle_minutes, nb.idle_source) == ("on", 90, "space")
    assert nb.role_arn == ROLE and nb.user_profile == "alice" and nb.errors == {}
    assert report.role_name == "AmazonSageMaker-ExecutionRole-2024"
    assert [v.title for v in report.machine.volumes] == ["space volume (kept when the app stops)"]


def test_instance_studio_without_permissions(stubs, tmp_path):
    core = stubs.analyzer(write_root(tmp_path, SPACE_META, home="home/sagemaker-user"))
    stubs.identity()
    denied(stubs.sm, "describe_app")
    denied(stubs.sm, "describe_domain")
    stubs.sm.add_response("describe_space", space_desc(), {"DomainId": DOMAIN, "SpaceName": "churn-analysis"})
    stubs.sm.add_response("describe_user_profile", {"DomainId": DOMAIN, "UserProfileName": "alice"},
                          {"DomainId": DOMAIN, "UserProfileName": "alice"})
    nb = core.instance().notebook
    assert nb.errors == {"app": "AccessDeniedException", "domain": "AccessDeniedException"}
    assert nb.idle == "unknown" and nb.role_arn == ROLE and nb.volume_gb == 50


def test_notebook_by_space_name(stubs, tmp_path):
    core = stubs.analyzer(tmp_path)
    stubs.sm.add_client_error("describe_notebook_instance", service_error_code="ValidationException",
                              service_message="RecordNotFound")
    stubs.sm.add_response("list_domains", {"Domains": [{"DomainId": DOMAIN, "DomainName": "acme"}]}, {})
    stubs.sm.add_response("list_spaces", {"Spaces": [
        {"DomainId": DOMAIN, "SpaceName": "churn-analysis", "SpaceSettingsSummary": {"AppType": "JupyterLab"}},
        {"DomainId": DOMAIN, "SpaceName": "forecasting"}]}, {"DomainIdEquals": DOMAIN})
    stubs.sm.add_response("list_apps", {"Apps": []}, {"DomainIdEquals": DOMAIN, "SpaceNameEquals": "churn-analysis"})
    stubs.sm.add_response("describe_domain", domain_desc(), {"DomainId": DOMAIN})
    stubs.sm.add_response("describe_space", space_desc(), {"DomainId": DOMAIN, "SpaceName": "churn-analysis"})
    stubs.sm.add_response("describe_user_profile", {"DomainId": DOMAIN, "UserProfileName": "alice"},
                          {"DomainId": DOMAIN, "UserProfileName": "alice"})
    nb = core.notebook("churn-analysis")
    assert nb.status == "Stopped" and nb.instance_type == "ml.t3.medium" and not nb.billing


def test_running(stubs, tmp_path):
    core = stubs.analyzer(write_root(tmp_path, SPACE_META, home="home/sagemaker-user"))
    stubs.running()
    ticks = []
    report = core.running(progress=lambda done, total: ticks.append((done, total)))
    kinds = [(b.kind, b.name) for b in report.resources]
    assert kinds == [("notebook instance", "old-experiment"), ("Studio app", "churn-analysis"),
                     ("endpoint", "churn-v1"), ("training job", "xgb-7")]
    notebook, app, endpoint, job = report.resources
    assert notebook.idle == "off" and notebook.volume_gb == 50 and notebook.hourly() == 0.461
    assert app.this and app.instances == [("ml.g5.xlarge", 1)] and app.idle == "off"
    assert endpoint.invocations == 0.0 and endpoint.config == "churn-v1-config"
    assert job.spot and job.hourly() == pytest.approx(3.04)
    assert [b.name for b in report.stopped] == ["archive"] and report.stopped[0].volume_gb == 200
    assert report.errors == {} and ticks[-1] == (5, 5)
    df = report.to_df()
    assert list(df["name"]) == ["old-experiment", "churn-analysis", "churn-v1", "xgb-7", "archive"]


def test_running_without_permissions(stubs, tmp_path):
    core = stubs.analyzer(tmp_path)
    denied(stubs.sm, "list_notebook_instances")
    denied(stubs.sm, "list_apps")
    denied(stubs.sm, "list_endpoints")
    denied(stubs.sm, "list_training_jobs")
    stubs.sm.add_response("list_processing_jobs", {"ProcessingJobSummaries": []}, {"StatusEquals": "InProgress"})
    report = core.running()
    assert report.resources == [] and set(report.errors) == {"notebook instances", "apps", "endpoints",
                                                             "training jobs"}


def test_no_region_is_a_readable_error(tmp_path, monkeypatch):
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "none"))
    with pytest.raises(ValueError, match="No AWS region is set"):
        SageMakerAnalyzer(root=str(tmp_path)).client
    # On SageMaker, the notebook's own region is used.
    core = SageMakerAnalyzer(root=str(write_root(tmp_path / "nb")))
    assert core.region == REGION



def run(capsys, fn, *args, **kwargs):
    fn(*args, **kwargs)
    return capsys.readouterr().out


def test_ui_instance_of_this_notebook_instance(aws, tmp_path, capsys):
    aws.create_notebook_instance(NotebookInstanceName="acme-nb", InstanceType="ml.m5.xlarge", RoleArn=ROLE,
                                 VolumeSizeInGB=50)
    core = fake_machine(SageMakerAnalyzer(region=REGION, root=str(write_root(tmp_path, memory=(16_000_000, 1_000_000)))),
                        used=46 * GB)
    out = run(capsys, SageMakerView(core, mode="text").instance)
    for expected in (
        "Notebook instance acme-nb",
        "us-east-1 · the notebook this code runs in · cost at us-east-1 list prices",
        "Instance: ml.m5.xlarge   Size: 4 vCPU · 16 GiB   Price: $0.23/hour",
        "Running for: 3d 4h   Est. cost since start: $17.48",
        "Idle shutdown: off (!)",
        "Disk: 92% of 50.0 GB used (!)",
        "Memory: 14.3 GB of 15.3 GB used (!)",
        "-- Findings: 3 warnings --",
        "stop-notebook-instance --notebook-instance-name acme-nb",
        "1 other notebook kernel holds 4.8 GB",
        "-- Turn on auto-stop --",
        "-- This machine right now --",
        "Storage (50 GB volume)",
        "$7.00, running or stopped",
        "AmazonSageMaker-ExecutionRole-2024",
        "-- Biggest processes by memory --",
        "disk('",
        "running()",
    ):
        assert expected in out, expected


def test_ui_instance_of_a_studio_space(stubs, tmp_path, capsys):
    core = stubs.analyzer(write_root(tmp_path, SPACE_META, home="home/sagemaker-user"))
    stubs.identity()
    stubs.studio()
    out = run(capsys, SageMakerView(core, mode="text").instance)
    for expected in (
        "JupyterLab space churn-analysis",
        "Running for: 1d 6h",
        f"Domain {DOMAIN} doesn't shut idle JupyterLab apps down",
        "-- Turn on idle shutdown --",
        f"aws sagemaker update-domain --domain-id {DOMAIN}",
        "Space                    churn-analysis (private)",
        "sagemaker-distribution-cpu 2.6.0",
    ):
        assert expected in out, expected


def test_ui_instance_outside_sagemaker_and_unknown_names(aws, tmp_path, capsys):
    ui = SageMakerView(fake_machine(SageMakerAnalyzer(region=REGION, root=str(tmp_path))), mode="text")
    out = run(capsys, ui.instance)
    assert "Not a SageMaker notebook" in out and "instance('name')" in out
    out = run(capsys, ui.instance, "no-such-notebook")
    assert "ValueError: No notebook instance or Studio space named 'no-such-notebook'" in out
    assert "Traceback" not in out


def test_ui_disk(tmp_path, capsys):
    root = write_root(tmp_path)
    write_tree(root / "home/ec2-user/SageMaker")
    core = fake_machine(SageMakerAnalyzer(root=str(root)), used=46 * GB)
    ui = SageMakerView(core, mode="text")
    out = run(capsys, ui.disk)
    for expected in (
        "notebook volume (kept when the instance stops) · sizes add up the files in each folder",
        "Used: 46.0 GB (92%) (!)",
        "Files: 10",
        "The disk is 92% full",
        "--volume-size-in-gb 100",
        "-- Biggest folders --",
        "data/",
        "    raw/",
        "-- Largest files --",
        "-- Caches and trash that are safe to clear --",
        "Jupyter trash",
        "pip cache purge",
        "disk('",
    ):
        assert expected in out, expected
    assert "limit takes a number of items" in run(capsys, ui.disk, limit="lots")
    assert "There's no folder" in run(capsys, ui.disk, str(root / "missing"))


def test_ui_running(stubs, tmp_path, capsys):
    core = stubs.analyzer(write_root(tmp_path, SPACE_META, home="home/sagemaker-user"))
    stubs.running()
    out = run(capsys, SageMakerView(core, mode="text").running)
    for expected in (
        "Running in SageMaker, us-east-1 (4)",
        "Running now: 4",
        "Est. cost / hour: $5.03",
        "Warnings: 2 (!)",
        "Stopped notebooks' storage: $28.00/month",
        "Notebook instance old-experiment (ml.m5.2xlarge) has been running for 6d 0h",
        "Endpoint churn-v1 (ml.m5.large) got no requests in the last 7 days",
        "churn-analysis  (this notebook)",
        "ml.g5.2xlarge × 2 (spot)",
        "space churn-analysis · d-abc123def456",
        "-- Stopped notebook instances (their volumes are still billed) --",
        "instance('old-experiment')",
        "running(days=30)",
    ):
        assert expected in out, expected


def test_ui_running_with_nothing_readable(stubs, tmp_path, capsys):
    core = stubs.analyzer(tmp_path)
    denied(stubs.sm, "list_notebook_instances")
    stubs.sm.add_response("list_apps", {"Apps": []}, {})
    stubs.sm.add_response("list_endpoints", {"Endpoints": []}, {})
    stubs.sm.add_response("list_training_jobs", {"TrainingJobSummaries": []}, {"StatusEquals": "InProgress"})
    stubs.sm.add_response("list_processing_jobs", {"ProcessingJobSummaries": []}, {"StatusEquals": "InProgress"})
    out = run(capsys, SageMakerView(core, mode="text").running)
    assert "Nothing in SageMaker bills by the hour in us-east-1 right now" in out
    assert "Couldn't read notebook instances (AccessDeniedException; needs sagemaker:ListNotebookInstances)" in out


def test_ui_friendly_errors(stubs, tmp_path, capsys):
    core = stubs.analyzer(tmp_path)
    stubs.sts.add_response("get_caller_identity", {"Account": ACCOUNT, "Arn": "arn:aws:sts::1:assumed-role/r/s"}, {})
    denied(stubs.sm, "describe_notebook_instance")
    out = run(capsys, SageMakerView(core, mode="text").instance, "team-nb")
    assert "AccessDeniedException:" in out and "README lists the read-only IAM permissions" in out
    assert "[instance]" in out and "Traceback" not in out


def test_render_html_and_badge():
    blocks = [smmod._Title("Notebook instance x"), smmod._Cards([("Idle shutdown", "off", "warn")]),
              smmod._Findings([("warn", "stop it: aws sagemaker stop-notebook-instance --notebook-instance-name x")]),
              smmod._Text("aws sagemaker update-domain --domain-id d", title="Turn on idle shutdown", code=True)]
    rendered = smmod._render_html(blocks, 50)
    assert '<div class="smk">' in rendered and '<span class="badge">SageMaker</span>' in rendered
    assert ">aws sagemaker stop-notebook-instance --notebook-instance-name x</code>" in rendered
    assert 'class="card warn"' in rendered and '<pre class="code"' in rendered


def test_ui_help_groups_every_command(tmp_path, capsys):
    ui = SageMakerView(SageMakerAnalyzer(root=str(tmp_path)), mode="text")
    out = run(capsys, ui.help)
    commands = {name for name in vars(SageMakerView)
                if not name.startswith("_") and callable(getattr(SageMakerView, name))}
    assert commands == {name for names in SageMakerView._GROUPS.values() for name in names}
    assert "Start here:" in out and "-- This notebook --" in out and "instance(name=None)" in out
    assert "Did you mean 'disk'" in run(capsys, ui.help, "dsk")
    assert "Its machine can only be read from inside it" in run(capsys, ui.help, "instance")


def test_view_validates_its_options(tmp_path):
    with pytest.raises(ValueError, match="mode must be"):
        SageMakerView(SageMakerAnalyzer(root=str(tmp_path)), mode="pdf")
    with pytest.raises(ValueError, match="progress must be"):
        SageMakerView(SageMakerAnalyzer(root=str(tmp_path)), progress="loud")


def test_environment_label_for_other_kinds():
    assert Environment(kind="other", name="x").label == "x"
    assert Environment().label == "this machine"
