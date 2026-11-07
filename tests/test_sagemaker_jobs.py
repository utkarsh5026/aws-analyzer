import json
from datetime import datetime, timedelta, timezone

import pytest
from fake_sagemaker import GPU_MEMORY, MISSING_MODULE, NO_CAPACITY, fake_clients

from aws_analyzer._kit.errors import _Hint
from aws_analyzer.sagemaker_env import (
    StatusChange,
    Billable,
    EndpointReport,
    EndpointUse,
    PipelineRun,
    PipelineStep,
    PipelinesReport,
    PipelineInfo,
    PipelineRunSummary,
    SageMakerAnalyzer,
    SageMakerView,
    TrainingDetail,
    TrainingJob,
    TrainingList,
    endpoint_findings,
    failure_kind,
    find_error,
    host_log,
    host_of,
    merge_steps,
    parse_definition,
    parse_pipeline_step,
    pipeline_findings,
    pipeline_layout,
    pipelines_findings,
    run_reason,
    short_reason,
    step_next,
    step_waterfall,
    training_findings,
    training_list_findings,
    why_it_failed,
)
from aws_analyzer.sagemaker_env.style import _render_html
from aws_analyzer.sagemaker_env.blocks import _PipelineGraph, _StepWaterfall


NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def ago(**kwargs):
    return NOW - timedelta(**kwargs)


def test_short_reason_and_host():
    assert short_reason(MISSING_MODULE) == "ModuleNotFoundError: No module named 'lightgbm'"
    assert short_reason(NO_CAPACITY).startswith("CapacityError: Unable to provision")
    assert short_reason("") == ""
    assert host_of("churn-xgb-0517/algo-1-1714569600") == "algo-1"
    assert host_of("job/algo-12-1714569600") == "algo-12"


def test_find_error_takes_the_last_traceback():
    lines = ["start", "Traceback (most recent call last):", '  File "a.py", line 1', "ValueError: first",
             "retrying", "Traceback (most recent call last):", '  File "b.py", line 9, in <module>', "    x()",
             "KeyError: 'label'", "ERROR    Reporting training FAILURE"]
    assert find_error(lines) == lines[5:9]
    assert find_error(["epoch 1", "ERROR: disk", "done"]) == ["ERROR: disk"]
    assert find_error(["epoch 1", "epoch 2"]) == []


def test_host_log_reads_events():
    events = [{"timestamp": int(ago(minutes=5).timestamp() * 1000), "message": "Traceback (most recent call last):\n"},
              {"timestamp": int(ago(minutes=4).timestamp() * 1000), "message": "MemoryError\n"}]
    log = host_log("job/algo-2-1714569600", events, cut_short=True)
    assert log.host == "algo-2" and log.cut_short
    assert log.error == ["Traceback (most recent call last):", "MemoryError"]
    assert log.first < log.last


def job(**kwargs):
    defaults = dict(status="Failed", instances=[("ml.m5.xlarge", 1)], created=ago(hours=2), started=ago(hours=2),
                    ended=ago(hours=1), billable_seconds=3600, volume_gb=30, max_runtime=7200)
    defaults.update(kwargs)
    return TrainingJob("train-1", **defaults)


@pytest.mark.parametrize("failure, error, kind, says", [
    (MISSING_MODULE, [], "missing package", "requirements.txt"),
    (GPU_MEMORY, [], "GPU memory", "batch size"),
    ("AlgorithmError: ExitCode 137", [], "memory", "ml.r5.xlarge has 32 GiB"),
    ("AlgorithmError: ExecuteUserScriptError", ["OSError: [Errno 28] No space left on device"], "disk",
     "from 30 GB"),
    (NO_CAPACITY, [], "capacity", "nothing in your code is wrong"),
    ("ResourceLimitExceeded: The account-level service limit", [], "quota", "service-quotas"),
    ("ClientError: Data download failed: NoSuchKey", [], "input data", "s3://acme/in/"),
    ("ClientError: AccessDenied on s3://acme/in/", [], "S3 access", "s3:GetObject"),
    ("AlgorithmError: ExecuteUserScriptError: ExitCode 1", [], "script error", "traceback"),
])
def test_why_it_failed(failure, error, kind, says):
    failed = job(failure=failure, inputs=[("train", "s3://acme/in/")])
    assert failure_kind(failed, error) == kind
    assert says in why_it_failed(failed, error)


def test_why_it_failed_stopped_and_completed():
    assert why_it_failed(job(status="Completed")) == ""
    stopped = job(status="Stopped", secondary="MaxRuntimeExceeded")
    assert failure_kind(stopped) == "time limit"
    assert "max_run" in why_it_failed(stopped)
    assert "checkpoint_s3_uri" in why_it_failed(job(status="Stopped", secondary="Interrupted", spot=True))


def test_training_job_cost():
    assert job().cost() == pytest.approx(0.23)
    assert job(instances=[("ml.m5.xlarge", 2)], billable_seconds=1800).cost() == pytest.approx(0.23)
    assert job(instances=[("ml.unknown", 1)]).cost() is None
    assert job(billable_seconds=None, status="InProgress", ended=None).cost(now=NOW) == pytest.approx(0.46)


def test_training_findings():
    detail = TrainingDetail(job(failure=MISSING_MODULE), logs_read=True)
    found = training_findings(detail, now=NOW)
    assert found[0][0] == "warn" and "lightgbm" in found[0][1]
    assert any("no logs" in text for _, text in found)

    done = job(status="Completed", started=ago(hours=3), ended=ago(hours=1), billable_seconds=7200,
               transitions=[], input_mode="File")
    done.transitions = [StatusChange("Starting", ago(hours=3), ago(hours=2, minutes=45)),
                        StatusChange("Downloading", ago(hours=2, minutes=45), ago(hours=2)),
                        StatusChange("Training", ago(hours=2), ago(hours=1))]
    texts = [text for _, text in training_findings(TrainingDetail(done, logs_read=True), now=NOW)]
    assert any("warm pool" in t for t in texts)
    assert any("FastFile" in t for t in texts)
    assert any("use_spot_instances=True" in t for t in texts)

    errors = training_findings(TrainingDetail(done, errors={"logs": "AccessDeniedException"}), now=NOW)
    assert "logs:GetLogEvents" in errors[-1][1]


def test_training_list_findings():
    jobs = [job(failure=NO_CAPACITY), job(failure=NO_CAPACITY), job(status="Completed"),
            job(status="InProgress", ended=None, started=ago(hours=2), max_runtime=7300)]
    jobs[1].name = "train-2"
    jobs[3].name = "train-4"
    report = TrainingList("us-east-1", jobs=jobs, limit=4, cut_short=True)
    texts = [text for _, text in training_list_findings(report, now=NOW)]
    assert "training_job('train-1')" in texts[0]
    assert any("2 of the failures are the same kind (capacity)" in t for t in texts)
    assert any("train-4 has run" in t for t in texts)
    assert any("limit=" in t for t in texts)


def use(name, *, requests, last_days=None, errors=0.0, instances=(("ml.m5.large", 1),), age=60, serverless=False):
    b = Billable("endpoint", name, "InService", instances=list(instances), since=ago(days=age),
                 config=f"{name}-config", variants=["AllTraffic"], serverless=serverless, invocations=requests)
    daily = {}
    if last_days is not None:
        daily[ago(days=last_days)] = requests
    return EndpointUse(b, daily=daily, requests=requests, server_errors=errors)


def test_endpoint_findings():
    report = EndpointReport("us-east-1", days=30, endpoints=[
        use("idle-a", requests=0.0, instances=[("ml.g5.xlarge", 1)]),
        use("idle-b", requests=0.0),
        use("stale", requests=900.0, last_days=12),
        use("quiet", requests=60.0, last_days=0),
        use("broken", requests=10_000.0, last_days=0, errors=300.0),
        use("busy", requests=100_000.0, last_days=0),
        use("new", requests=0.0, age=0),
        use("serverless", requests=None, serverless=True, instances=()),
    ])
    found = endpoint_findings(report, now=NOW)
    texts = [text for _, text in found]
    assert texts[0].startswith("Endpoint idle-a (ml.g5.xlarge) got no requests") and "$1,029/month" in texts[0]
    assert "aws sagemaker delete-endpoint --endpoint-name idle-a" in texts[0]
    assert "create-endpoint --endpoint-name idle-a --endpoint-config-name idle-a-config" in texts[0]
    assert any("2 idle endpoints cost" in t for t in texts)
    assert any(t.startswith("Endpoint stale's last request was 12d ago") for t in texts)
    assert any("quiet averages 2.0 requests a day" in t for t in texts)
    assert any("broken: 3.0%" in t and "/aws/sagemaker/Endpoints/broken" in t for t in texts)
    assert not any(f"Endpoint {name}" in t for t in texts for name in ("busy", "new", "serverless"))
    report.errors = {"metrics": "AccessDenied"}
    assert "cloudwatch:GetMetricData" in endpoint_findings(report, now=NOW)[-1][1]


DEFINITION = {
    "Version": "2020-12-01",
    "Steps": [
        {"Name": "Prep", "Type": "Processing", "Arguments": {}},
        {"Name": "Train", "Type": "Training", "Arguments": {
            "InputDataConfig": [{"DataSource": {"S3Uri": {"Get": "Steps.Prep.ProcessingOutputConfig.Outputs"}}}]}},
        {"Name": "Eval", "Type": "Processing", "DependsOn": ["Train"], "Arguments": {}},
        {"Name": "Check", "Type": "Condition", "Arguments": {
            "Conditions": [{"LeftValue": {"Std:JsonGet": {"PropertyFile": {"Get": "Steps.Eval.PropertyFiles.M"}}}}],
            "IfSteps": [{"Name": "Register", "Type": "RegisterModel", "Arguments": {}}],
            "ElseSteps": [{"Name": "Stop", "Type": "Fail", "Arguments": {}}]}},
    ],
}


def test_parse_definition_and_layout():
    steps = parse_definition(json.dumps(DEFINITION))
    by_name = {s.name: s for s in steps}
    assert [s.name for s in steps] == ["Prep", "Train", "Eval", "Check", "Register", "Stop"]
    assert by_name["Train"].depends_on == ["Prep"]
    assert by_name["Eval"].depends_on == ["Train"]
    assert by_name["Check"].depends_on == ["Eval"]
    assert by_name["Register"].branch == ("Check", "if") and by_name["Stop"].branch == ("Check", "else")

    layout = pipeline_layout(steps)
    layers = {n.step.name: n.layer for n in layout.nodes}
    assert layers == {"Prep": 0, "Train": 1, "Eval": 2, "Check": 3, "Register": 4, "Stop": 4}
    labels = {(e.source, e.target): e.label for e in layout.edges}
    assert labels[("Check", "Register")] == "if" and labels[("Check", "Stop")] == "else"
    register, stop = (n for n in layout.nodes if n.layer == 4)
    assert register.y == stop.y and register.x != stop.x
    assert layout.width > 0 and layout.height > 0
    assert pipeline_layout([]).nodes == []


def test_merge_steps_and_waterfall():
    defined = parse_definition(DEFINITION)
    ran = [parse_pipeline_step({"StepName": "Prep", "StepStatus": "Succeeded", "StartTime": ago(minutes=20),
                                "EndTime": ago(minutes=15), "CacheHitResult": {"SourcePipelineExecutionArn": "x"}}),
           parse_pipeline_step({"StepName": "Train", "StepStatus": "Failed", "StartTime": ago(minutes=15),
                                "EndTime": ago(minutes=10), "FailureReason": MISSING_MODULE, "AttemptCount": 2,
                                "Metadata": {"TrainingJob": {"Arn": "arn:aws:sagemaker:us-east-1:1:training-job/t-1"}}}),
           parse_pipeline_step({"StepName": "Extra", "StepStatus": "Succeeded", "StartTime": ago(minutes=9)})]
    steps = merge_steps(defined, ran)
    assert [s.name for s in steps][-1] == "Extra"
    train = next(s for s in steps if s.name == "Train")
    assert train.type == "Training" and train.job == ("training job", "t-1") and train.depends_on == ["Prep"]
    assert step_next(train) == "training_job('t-1')"
    assert step_next(PipelineStep("p", job=("processing job", "p-1"))).startswith(
        "aws logs tail /aws/sagemaker/ProcessingJobs --log-stream-name-prefix p-1")

    run = PipelineRun("churn", arn="arn:run", status="Failed", started=ago(minutes=20), modified=ago(minutes=9),
                      steps=steps)
    bars, seconds = step_waterfall(run, NOW)
    assert [b.name for b in bars] == ["Prep", "Train", "Extra"]
    assert bars[0].status == "cached" and bars[0].start == 0
    assert bars[1].status == "failed"
    assert seconds == pytest.approx(20 * 60)

    texts = [text for _, text in pipeline_findings(run, NOW)]
    assert texts[0].startswith("Step Train (training job t-1) failed after 2 tries: ModuleNotFoundError")
    assert "training_job('t-1')" in texts[0]
    assert any("retry-pipeline-execution --pipeline-execution-arn arn:run" in t for t in texts)
    assert any("reused an earlier run's result" in t for t in texts)


def test_run_reason_and_pipelines_findings():
    reason = "Step failure: One or multiple steps failed. TrainModel: AlgorithmError: ExecuteUserScriptError:"
    assert run_reason(reason) == "step TrainModel failed (AlgorithmError: ExecuteUserScriptError)"
    assert run_reason("Something else.") == "Something else"
    report = PipelinesReport("us-east-1", pipelines=[
        PipelineInfo("churn", runs=[PipelineRunSummary("a", status="Failed", failure=reason),
                                    PipelineRunSummary("b", status="Failed"), PipelineRunSummary("c", status="Succeeded")]),
        PipelineInfo("fine", runs=[PipelineRunSummary("d", status="Succeeded")]),
        PipelineInfo("never")])
    texts = [text for _, text in pipelines_findings(report)]
    assert texts == ["Pipeline churn's latest run failed (its last 2 runs all failed): step TrainModel failed "
                     "(AlgorithmError: ExecuteUserScriptError). See which step broke: pipeline_run('churn')"]


@pytest.fixture
def clients():
    return fake_clients(datetime.now(timezone.utc))


@pytest.fixture
def core(clients):
    return SageMakerAnalyzer(clients=clients, region="us-east-1")


def test_training_jobs(core):
    report = core.training_jobs(since="7d")
    assert [j.name for j in report.jobs][:2] == ["forecast-deepar-0517", "churn-xgb-0517"]
    assert all(j.described for j in report.jobs)
    failed = core.training_jobs(since=None, status="Failed", limit=2)
    assert len(failed.jobs) == 2 and failed.cut_short
    assert {j.status for j in failed.jobs} == {"Failed"}


def test_training_job_reads_its_logs(core, clients):
    detail = core.training_job("bert-finetune-0516")
    assert [h.host for h in detail.hosts] == ["algo-1", "algo-2"]
    assert detail.hosts[0].error[-1].startswith("torch.OutOfMemoryError")
    assert not detail.hosts[1].error
    assert detail.job.instances == [("ml.g5.2xlarge", 2)]
    assert clients["logs"].called("get_log_events")[0]["startFromHead"] is False

    clients["logs"].denied.add("describe_log_streams")
    denied = core.training_job("bert-finetune-0516")
    assert denied.errors == {"logs": "AccessDeniedException"} and not denied.hosts


def test_training_job_not_found_names_close_ones(core):
    with pytest.raises(_Hint, match="Close names: churn-xgb-0517, churn-xgb-0515"):
        core.training_job("churn-xgb")


def test_endpoints(core, clients):
    report = core.endpoints(days=30)
    by_name = {u.endpoint.name: u for u in report.endpoints}
    assert by_name["bert-demo"].requests == 0
    assert by_name["fraud-staging"].requests == 300 * 18
    assert by_name["faq-serverless"].endpoint.serverless and by_name["faq-serverless"].requests is None
    assert by_name["recs-endpoint"].server_errors == pytest.approx(12)
    assert by_name["churn-endpoint"].last_called is not None

    clients["cloudwatch"].denied.add("get_metric_data")
    blind = core.endpoints()
    assert blind.errors == {"metrics": "AccessDeniedException"}
    assert all(u.requests is None for u in blind.endpoints)


def test_pipelines_and_runs(core):
    report = core.pipelines(runs=3)
    assert [p.name for p in report.pipelines][0] == "forecast-pipeline"
    churn = next(p for p in report.pipelines if p.name == "churn-pipeline")
    assert [r.status for r in churn.runs] == ["Failed", "Failed", "Succeeded"]

    run = core.pipeline_run("churn-pipeline")
    assert run.name == "run-0517" and run.status == "Failed"
    train = next(s for s in run.steps if s.name == "TrainModel")
    assert train.status == "Failed" and train.job == ("training job", "churn-xgb-0517")
    assert next(s for s in run.steps if s.name == "Evaluate").status == ""
    assert core.pipeline_run("churn-pipeline", 2).name == "run-0515"
    assert core.pipeline_run("churn-pipeline", "run-0516").name == "run-0516"


def test_pipeline_run_hints(core, clients):
    with pytest.raises(_Hint, match="no pipeline named nope"):
        core.pipeline_run("nope")
    with pytest.raises(_Hint, match="pick 0 to 2"):
        core.pipeline_run("churn-pipeline", 9)
    clients["sagemaker"].denied.add("describe_pipeline_definition_for_execution")
    run = core.pipeline_run("churn-pipeline")
    assert run.errors == {"definition": "AccessDeniedException"} and run.steps


def run(capsys, fn, *args, **kwargs):
    fn(*args, **kwargs)
    return capsys.readouterr().out


@pytest.fixture
def ui(core):
    return SageMakerView(core, mode="text", progress="off")


def test_ui_training_jobs(ui, capsys):
    out = run(capsys, ui.training_jobs)
    assert "Training jobs in us-east-1 (5)" in out
    assert "Failed: 3 (!)" in out
    assert "training_job('churn-xgb-0517')" in out
    assert "missing package: ModuleNotFoundError: No module named 'lightgbm'" in out


def test_ui_training_job(ui, capsys):
    out = run(capsys, ui.training_job, "churn-xgb-0517")
    assert "The training script imports lightgbm" in out
    assert "-- The error in algo-1's log --" in out
    assert "What the job went through" in out and "Downloading" in out
    out = run(capsys, ui.training_job, "nope")
    assert "There's no training job named nope" in out and "Traceback" not in out


def test_ui_endpoints(ui, capsys):
    out = run(capsys, ui.endpoints)
    assert "Idle: 1 (!)" in out and "Idle, / month: $1,029 (!)" in out
    assert "aws sagemaker delete-endpoint --endpoint-name bert-demo" in out
    assert out.index("bert-demo  ") < out.index("churn-endpoint  ")


def test_ui_pipelines(ui, capsys):
    out = run(capsys, ui.pipelines)
    assert "Latest run failed: 1 (!)" in out and "✗ ✗ ✓" in out
    out = run(capsys, ui.pipeline_run, "churn-pipeline")
    assert "Failed step: TrainModel (!)" in out
    assert "RegisterModel (RegisterModel) [never ran] <- CheckAccuracy (if)" in out
    assert "-- When each step ran --" in out
    assert "training_job('churn-xgb-0517')" in out
    out = run(capsys, ui.pipeline_run, "forecast-pipeline")
    assert "Running for:" in out and "Nothing has gone wrong so far." in out


def test_ui_pipeline_run_html(core):
    run_ = core.pipeline_run("churn-pipeline")
    bars, seconds = step_waterfall(run_)
    html = _render_html([_PipelineGraph(pipeline_layout(run_.steps), title="Its steps"),
                         _StepWaterfall(bars, seconds, title="When")], 0)
    assert "<svg" in html and 'class="pn s-failed"' in html and 'class="pn s-none"' in html
    assert 'class="pws s-succeeded"' in html and ">if</text>" in html
    assert "<script" not in html
