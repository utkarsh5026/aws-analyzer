"""
What LambdaAnalyzer reads about the things that call a function, so their own limits can be held against its
settings (analysis/limits.py): each SQS trigger's queue (its visibility timeout and redrive policy), the load balancer
in front of a target group (its idle timeout), and how long each API Gateway API waits (its integrations' timeouts).
Nothing here changes them, and nothing receives a message.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from .._kit.errors import _error_code

from .helpers import _pages
from .models import ApiWait, LoadBalancerWait, QueueState, Trigger
from .analysis.limits import LOAD_BALANCER_IDLE_S, http_api_wait, rest_api_wait

_MOST_REST_PAGES = 20


class _CallerReads:
    """
    The reads describe() makes about what calls the function; a mixin of LambdaAnalyzer, which gives it _service,
    _map and _one_queue.
    """

    def _trigger_queues(self, triggers: list[Trigger]) -> dict[str, QueueState]:
        """
        Each SQS trigger's queue as it is now (GetQueueUrl, GetQueueAttributes), by ARN, side by side; a queue that
        can't be read comes back with its error code in .error.
        """
        arns = list(dict.fromkeys(t.arn for t in triggers if t.kind == "SQS queue" and t.arn))
        return dict(zip(arns, self._map(self._one_queue, arns)))

    def _trigger_load_balancers(self, triggers: list[Trigger]) -> dict[str, LoadBalancerWait]:
        """
        For each load balancer target group that calls the function (its resource policy's SourceArn), the load
        balancer in front of it that waits the least (DescribeTargetGroups, then DescribeLoadBalancerAttributes'
        idle_timeout.timeout_seconds), by target group ARN.
        """
        groups = list(dict.fromkeys(t.arn for t in triggers
                                    if t.kind == "Load balancer" and t.arn and ":targetgroup/" in t.arn))
        found: dict[str, LoadBalancerWait] = {}
        for group in groups:
            region = group.split(":")[3]
            elbv2 = self._service("elbv2", region)
            described = elbv2.describe_target_groups(TargetGroupArns=[group]).get("TargetGroups") or [{}]
            waits = [self._load_balancer_wait(elbv2, arn) for arn in described[0].get("LoadBalancerArns") or []]
            if waits:
                found[group] = min(waits, key=lambda wait: wait.idle_timeout)
        return found

    def _trigger_apis(self, function: str, triggers: list[Trigger]) -> dict[str, ApiWait]:
        """
        For each API Gateway API that calls the function (its resource policy's execute-api SourceArn), how long it
        waits for it: an HTTP or WebSocket API's integrations and routes (GetIntegrations, GetRoutes), else a REST
        API's methods (GetResources with its methods), by API ID. An API none of whose routes call it is left out.
        """
        apis = {t.source: t.arn.split(":")[3] for t in triggers
                if t.kind == "API Gateway" and t.source and t.arn and t.arn.count(":") >= 5}
        found: dict[str, ApiWait] = {}
        for api_id, region in apis.items():
            wait = self._http_api_wait(api_id, function, region)
            if wait is None:
                wait = self._rest_api_wait(api_id, function, region)
            if wait is not None:
                found[api_id] = wait
        return found

    def _http_api_wait(self, api_id: str, function: str, region: str) -> ApiWait | None:
        """
        An HTTP or WebSocket API's wait for the function; None for a REST API, which apigatewayv2 doesn't know.
        """
        client = self._service("apigatewayv2", region)
        try:
            integrations = _pages(client, "get_integrations", "Items", ApiId=api_id)
        except ClientError as exc:
            if _error_code(exc) == "NotFoundException":
                return None
            raise
        routes = _pages(client, "get_routes", "Items", ApiId=api_id)
        return http_api_wait(api_id, integrations, routes, function)

    def _rest_api_wait(self, api_id: str, function: str, region: str) -> ApiWait | None:
        client = self._service("apigateway", region)
        resources: list[dict[str, Any]] = []
        position: dict[str, str] = {}
        for _ in range(_MOST_REST_PAGES):
            page = client.get_resources(restApiId=api_id, embed=["methods"], limit=500, **position)
            resources += page.get("items") or []
            if not page.get("position"):
                break
            position = {"position": page["position"]}
        return rest_api_wait(api_id, resources, function)

    @staticmethod
    def _load_balancer_wait(elbv2: Any, arn: str) -> LoadBalancerWait:
        attributes = elbv2.describe_load_balancer_attributes(LoadBalancerArn=arn).get("Attributes") or []
        values = {a.get("Key"): a.get("Value") for a in attributes}
        idle = values.get("idle_timeout.timeout_seconds")
        return LoadBalancerWait(arn.split("/")[-2] if arn.count("/") >= 2 else arn, arn,
                                int(idle) if idle and idle.isdigit() else LOAD_BALANCER_IDLE_S)
