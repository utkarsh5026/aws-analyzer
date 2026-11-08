"""
The agent's toolkits for Step Functions, API Gateway, SageMaker, DynamoDB and OpenSearch, each run on the fakes its
own service's tests use, and through the agent's runtime: what the model reads, the Lambda run a failed step or call
leads to, what the guard lets through, and what never reaches the model.
"""

import json
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from fake_apigateway import TIMEOUT_RUN, cloudwatch as apigw_cloudwatch, now_utc, seed as seed_apis
from fake_sagemaker import fake_clients as sagemaker_clients
from fake_stepfunctions import FAILED_RUN, clients as sfn_clients, lambda_lines
from moto import mock_aws
from test_lambda_functions import create, lambda_clients, put_logs
from test_opensearch import bedrock, cloudwatch as os_cloudwatch, cluster, make_domain, serverless, sts

from aws_analyzer.agent import (
    AgentAnalyzer, AgentView, Budget, DEFAULT_TOOLKITS, ReadOnlyError, summarize_running,
)
from aws_analyzer.agent.toolkits.apigateway_tools import APIGatewayToolkit
from aws_analyzer.agent.window.chat import _SERVICES, _tool_words
from aws_analyzer.agent.window.tool_words import _TOOL_WORDS
from aws_analyzer.agent.answers import ToolCall
from aws_analyzer.apigateway import APIGatewayAnalyzer
from aws_analyzer.lambda_functions import LambdaAnalyzer
from aws_analyzer.opensearch import OpenSearchAnalyzer
from aws_analyzer.sagemaker_env import Billable, RunningReport, SageMakerAnalyzer
from aws_analyzer.stepfunctions import StepFunctionsAnalyzer

REGION = "us-east-1"
NEW_KITS = ("stepfunctions", "apigateway", "sagemaker", "dynamodb", "opensearch")


def tool(core, name):
    return next(t for t in core.tools() if t.__name__ == name)


def test_every_agent_gets_the_new_toolkits_by_default():
    assert DEFAULT_TOOLKITS[:3] == ("lambda", "s3", "bedrock_kb") and set(NEW_KITS) <= set(DEFAULT_TOOLKITS)
    core = AgentAnalyzer(region=REGION)
    names = [fn.__name__ for fn in core.tools()]
    assert len(names) == len(set(names))
    for expected in ("find_state_machines", "execution_details", "api_errors", "training_job", "table_shape",
                     "check_vectors"):
        assert expected in names
    reads = core.reads
    assert reads["api_errors"][0] == "apigateway:GET" and "states:GetExecutionHistory" in reads["execution_details"]
    assert "dynamodb:Scan" in reads["table_shape"]
    with pytest.raises(ValueError, match="cores= has no toolkit 'stepfunction'. Did you mean 'stepfunctions'"):
        AgentAnalyzer(region=REGION, cores={"stepfunction": object()})


def test_each_toolkit_has_a_badge_and_words_in_the_window():
    assert _SERVICES["states"].title == "AWS Step Functions" and _SERVICES["apigateway"].title == "Amazon API Gateway"
    for name in NEW_KITS:
        kit = AgentAnalyzer(region=REGION, toolkits=[name]).toolkit(name)
        assert all(fn.__name__ in _TOOL_WORDS for fn in kit.tools())
        assert {read.split(":", 1)[0] for reads in kit.reads.values() for read in reads} <= set(_SERVICES)
    running = ToolCall("execution_details", {"execution": "order-pipeline/ord-7f3a"}, first="execution")
    assert _tool_words(running, running=True) == "following order-pipeline/ord-7f3a step by step"
    assert _tool_words(ToolCall("api_errors", {"api": "orders-api"}, first="api"), running=False) == \
        "found why orders-api's calls fail"





@pytest.fixture
def sfn_world():
    """
    acme's state machines on fake Step Functions clients, and orders-etl in moto with the run that broke ord-7f3a.
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with mock_aws():
        role = boto3.client("iam").create_role(RoleName="etl", AssumeRolePolicyDocument="{}")["Role"]["Arn"]
        create("orders-etl", role)
        lam = LambdaAnalyzer(region=REGION, clients={"lambda": lambda_clients()})
        sfn = StepFunctionsAnalyzer(region=REGION, clients=sfn_clients(now))
        sfn.max_workers = 1
        scheduled = sfn.execution("order-pipeline/ord-7f3a", logs=False).failed_step.attempts[-1].scheduled
        put_logs("/aws/lambda/orders-etl", lambda_lines(scheduled))
        yield AgentAnalyzer(region=REGION, toolkits=["lambda", "stepfunctions"], lambda_core=lam,
                            cores={"stepfunctions": sfn})


def test_a_failed_workflow_leads_to_the_lambda_run_that_broke_it(sfn_world):
    core = sfn_world
    found = tool(core, "find_state_machines")()
    assert found["complete"] and found["state_machines"][0]["numbers"]["unsuccessful"] >= 1
    pipeline = next(m for m in found["state_machines"] if m["name"] == "order-pipeline")
    assert pipeline["numbers"]["unsuccessful_pct"] == 4.0 and "orders-etl" in pipeline["lambda_functions"]

    failures = tool(core, "state_machine_failures")("order-pipeline")
    top = failures["groups"][0]
    assert (top["state"], top["error"], top["share_pct"]) == ("LoadOrders", "KeyError", 60.0)
    assert top["lambda_run"] == {"function": "orders-etl", "request_id": FAILED_RUN}
    assert top["newest"]["execution"] == "order-pipeline/ord-7f3a"
    assert failures["findings"][0]["level"] == "warn"

    detail = tool(core, "execution_details")("order-pipeline/ord-7f3a")
    assert detail["failed_step"]["state"] == "LoadOrders" and detail["status"] == "FAILED"
    run = detail["lambda_run"]
    assert run["request_id"] == FAILED_RUN and run["found"] == "found by its request ID" and run["status"] == "failed"
    assert run["in_code"][0]["file"] == "etl.py" and run["in_code"][0]["line"] == 22
    assert any("KeyError: 'customer_id'" in line for line in run["lines"])
    json.dumps(detail)


def test_the_stepfunctions_toolkit_reads_lambda_through_the_lambda_toolkit():
    core = AgentAnalyzer(region=REGION, toolkits=["lambda", "stepfunctions"])
    kit = core.toolkit("stepfunctions")
    assert kit.analyzer.lambda_core is core.toolkit("lambda").analyzer
    prompt = kit.instructions(core.budget)
    assert "function_logs(function,\n  search=request_id)" in prompt
    alone = AgentAnalyzer(region=REGION, toolkits=["stepfunctions"]).toolkit("stepfunctions")
    assert "function_logs" not in alone.instructions(Budget())


def test_an_execution_that_isnt_there_names_the_close_machines(sfn_world):
    out = tool(sfn_world, "state_machine_details")("order-pipe")
    assert out["complete"] is False and "No state machine named 'order-pipe'" in out["message"]
    assert "order-pipeline" in out["message"]





@pytest.fixture
def apigw_world():
    with mock_aws():
        ids = seed_apis(now_utc())
        apis = APIGatewayAnalyzer(region=REGION, clients={"cloudwatch": apigw_cloudwatch(ids)},
                                  lambda_core=LambdaAnalyzer(region=REGION))
        apis.max_workers = 1
        yield AgentAnalyzer(region=REGION, toolkits=["lambda", "apigateway"], cores={"apigateway": apis})


def test_a_504_leads_to_the_lambda_run_that_took_too_long(apigw_world):
    core = apigw_world
    found = tool(core, "find_apis")()
    assert found["apis"][0]["name"] == "orders-api" and found["apis"][0]["share_of_5xx_pct"] > 90

    errors = tool(core, "api_errors")("orders-api")
    top = errors["groups"][0]
    assert (top["status"], top["route"]) == (504, "POST /orders") and top["newest"]["lambda_request_id"] == TIMEOUT_RUN
    run = errors["lambda_run"]
    assert run["function"] == "orders-etl" and run["found"] and run["duration_ms"] > 29_000
    assert run["lines"][0].endswith(f"START RequestId: {TIMEOUT_RUN} Version: $LATEST")

    one = tool(core, "api_calls")("orders-api", since="24h", search=TIMEOUT_RUN)
    assert one["matching"] == 1 and one["lambda_run"]["request_id"] == TIMEOUT_RUN
    failing = tool(core, "api_calls")("orders-api", since="24h", status="5")
    assert failing["matching"] == sum(n for status, n in failing["statuses"].items() if status.startswith("5"))

    details = tool(core, "api_details")("orders-api")
    post = next(r for r in details["routes"] if r["route"] == "POST /orders")
    assert post["lambda_function"] == "orders-etl" and post["timeout_ms"] == 29_000


def test_api_gateway_reads_need_apigateway_get():
    from botocore.exceptions import ClientError

    denied = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "GetRestApis")
    assert APIGatewayToolkit().permission_for(denied) == "apigateway:GET"
    logs = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "FilterLogEvents")
    assert APIGatewayToolkit().permission_for(logs) == "logs:FilterLogEvents"





@pytest.fixture
def sagemaker():
    core = SageMakerAnalyzer(clients=sagemaker_clients(datetime.now(timezone.utc)), region=REGION)
    return AgentAnalyzer(region=REGION, toolkits=["sagemaker"], cores={"sagemaker": core})


def test_a_failed_pipeline_leads_to_its_training_job_and_why_it_failed(sagemaker):
    jobs = tool(sagemaker, "training_jobs")()
    assert jobs["statuses"]["Failed"] == 3
    churn = next(j for j in jobs["jobs"] if j["name"] == "churn-xgb-0517")
    assert churn["failure_kind"] == "missing package" and "lightgbm" in churn["failure"]

    run = tool(sagemaker, "pipeline_run")("churn-pipeline")
    train = next(s for s in run["steps"] if s["step"] == "TrainModel")
    assert run["failed_steps"] == ["TrainModel"] and train["job"] == {"kind": "training job", "name": "churn-xgb-0517"}
    assert next(s for s in run["steps"] if s["step"] == "Evaluate")["status"] == "not run"
    assert tool(sagemaker, "pipeline_run")("churn-pipeline", run="1")["run"] == "run-0516"

    bert = tool(sagemaker, "training_job")("bert-finetune-0516")
    assert bert["failure_kind"] == "GPU memory" and "batch size" in bert["why_it_failed"]
    assert [h["host"] for h in bert["hosts"]] == ["algo-1", "algo-2"]
    assert bert["hosts"][0]["error"][-1].startswith("torch.OutOfMemoryError")

    endpoints = tool(sagemaker, "sagemaker_endpoints")()
    assert endpoints["endpoints"][0]["name"] == "bert-demo" and endpoints["findings"][0]["level"] == "warn"


def test_whats_running_puts_the_most_expensive_first():
    now = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
    report = RunningReport(REGION, resources=[
        Billable("endpoint", "cheap", status="InService", instances=[("ml.t3.medium", 1)], since=now,
                 invocations=10.0),
        Billable("notebook instance", "big-gpu", status="InService", instances=[("ml.g5.xlarge", 1)],
                 since=now - timedelta(days=3), idle="off"),
    ], stopped=[Billable("notebook instance", "old", status="Stopped", volume_gb=50)])
    out = summarize_running(report, SageMakerAnalyzer(region=REGION).prices, now)
    assert [r["name"] for r in out["resources"]] == ["big-gpu", "cheap"] and out["running"] == 2
    assert out["stopped_notebooks"][0]["name"] == "old" and out["stopped_notebooks"][0]["usd_per_hour"] == 0.0
    assert out["findings"][0]["level"] == "warn" and "big-gpu" in out["findings"][0]["message"]





@pytest.fixture
def tables():
    with mock_aws():
        client = boto3.client("dynamodb", region_name=REGION)
        client.create_table(TableName="orders", BillingMode="PAY_PER_REQUEST",
                            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"},
                                       {"AttributeName": "sk", "KeyType": "RANGE"}],
                            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                                  {"AttributeName": "sk", "AttributeType": "S"}])
        for user in range(4):
            for order in range(5):
                total = {"S": "zero"} if (user, order) == (0, 0) else {"N": str(order * 3)}
                client.put_item(TableName="orders", Item={
                    "pk": {"S": f"USER#{user}"}, "sk": {"S": f"ORDER#{order:04d}"}, "total": total,
                    "email": {"S": f"dana{user}@example.com"}})
        yield client


def test_dynamodb_tools_describe_tables_and_sample_their_shape_without_values(tables):
    core = AgentAnalyzer(region=REGION, toolkits=["dynamodb"])
    found = tool(core, "find_tables")()
    assert [t["name"] for t in found["tables"]] == ["orders"] and found["tables"][0]["billing"] == "on-demand"

    details = tool(core, "table_details")("orders")
    assert details["query"] == "query('orders', <pk>, sort=<sk>)" and details["partition_key"] == "pk"
    assert details["findings"][0]["level"] == "warn"

    shape = tool(core, "table_shape")("orders")
    total = next(a for a in shape["attributes"] if a["attribute"] == "total")
    assert total["types"] == {"number": 19, "string": 1} and shape["key_shapes"]["pk"] == {"USER#<number>": 20}
    assert "different types" in shape["findings"][0]["message"]
    text = json.dumps(shape)
    assert "dana0@example.com" not in text and "zero" not in text
    assert core._guard.counts["dynamodb:Scan"] >= 1

    capped = tool(core, "table_shape")("orders", items=5_000)
    assert "capped_at" in capped and capped["items_read"] == 20


def test_the_guard_lets_a_dynamodb_scan_through_and_nothing_that_writes(tables):
    core = AgentAnalyzer(region=REGION, toolkits=["dynamodb"])
    client = core.toolkit("dynamodb").analyzer.client
    client.scan(TableName="orders", Limit=1)
    with pytest.raises(ReadOnlyError, match="dynamodb:PutItem isn't a read"):
        client.put_item(TableName="orders", Item={"pk": {"S": "x"}, "sk": {"S": "y"}})
    with pytest.raises(ReadOnlyError, match="dynamodb:ExecuteStatement isn't a read"):
        client.execute_statement(Statement="DELETE FROM orders WHERE pk='x'")


def test_a_table_that_isnt_there_names_the_close_ones(tables):
    out = tool(AgentAnalyzer(region=REGION, toolkits=["dynamodb"]), "table_details")("order")
    assert out["close_names"] == ["orders"] and out["complete"] is False


def test_the_dynamodb_budget_is_in_the_tools_report(tables, capsys):
    view = AgentView(AgentAnalyzer(region=REGION, toolkits=["dynamodb"], budget={"dynamodb_items": 50}),
                     mode="text", progress="off")
    view.tools()
    out = capsys.readouterr().out
    assert "DynamoDB items" in out and "table_shape samples no more in one scan" in out





@pytest.fixture
def opensearch():
    with mock_aws():
        make_domain(boto3.client("opensearch", region_name=REGION))
        core = OpenSearchAnalyzer(region=REGION, http=cluster(), clients={
            "opensearchserverless": serverless(), "cloudwatch": os_cloudwatch(), "sts": sts(),
            "bedrock-runtime": bedrock()})
        yield AgentAnalyzer(region=REGION, toolkits=["opensearch"], cores={"opensearch": core})


def test_opensearch_tools_explain_vector_indexes_and_check_their_vectors(opensearch):
    found = tool(opensearch, "find_vector_stores")()
    assert found["stores"][0]["name"] == "vectors-prod" and found["stores"][0]["kind"] == "domain"

    indexes = tool(opensearch, "store_indexes")("vectors-prod")
    assert indexes["indexes"][0]["name"] == "docs" and indexes["vector_indexes"] == 1
    assert indexes["indexes"][0]["documents_with_vector_pct"] == 96.7

    docs = tool(opensearch, "index_details")("vectors-prod/docs")
    vector = docs["vector_fields"][0]
    assert (vector["field"], vector["dimension"], vector["similarity"]) == ("embedding", 16, "Cosine")
    assert docs["findings"][0]["level"] == "warn" and "have no 'embedding'" in docs["findings"][0]["message"]

    checked = tool(opensearch, "check_vectors")("vectors-prod/docs")
    field = checked["fields"][0]
    assert field["documents_without_one"] == 2 and field["normalized"] is True and field["repeated_vectors"] == 1
    assert "text" not in {k for row in checked["fields"] for k in row}

    nameless = tool(opensearch, "index_details")("vectors-prod")
    assert nameless["complete"] is False and "store_indexes('vectors-prod')" in nameless["message"]
