"""
What the two API Gateway clients return, read into the models: REST APIs come from apigateway (resources, each
with its methods; stages with method settings), HTTP and WebSocket APIs from apigatewayv2 (a flat list of routes
pointing at integrations by ID). Everything after this reads both the same way.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..helpers import lambda_of_uri
from ..models import Api, Authorizer, Integration, Route, Stage


ALL_METHODS = "*/*"


def _log_group(arn: str) -> str:
    """
    A log group's name from its ARN ('arn:aws:logs:us-east-1:123:log-group:/apigw/orders:*' -> '/apigw/orders').
    """
    if ":log-group:" not in (arn or ""):
        return arn or ""
    name = arn.split(":log-group:", 1)[1]
    return name[:-2] if name.endswith(":*") else name


def parse_rest_api(item: dict[str, Any], region: str) -> Api:
    """
    One item of GetRestApis.
    """
    types = (item.get("endpointConfiguration") or {}).get("types") or []
    return Api(id=item["id"], name=item.get("name", item["id"]), kind="REST", region=region,
               created=item.get("createdDate"), description=item.get("description", ""),
               endpoint_type=types[0] if types else "", api_key_source=item.get("apiKeySource", ""),
               default_endpoint_disabled=bool(item.get("disableExecuteApiEndpoint")))


def parse_v2_api(item: dict[str, Any], region: str) -> Api:
    """
    One item of GetApis (an HTTP or WebSocket API).
    """
    return Api(id=item["ApiId"], name=item.get("Name", item["ApiId"]), kind=item.get("ProtocolType", "HTTP"),
               region=region, created=item.get("CreatedDate"), description=item.get("Description", ""),
               endpoint_type="REGIONAL", endpoint=item.get("ApiEndpoint", ""),
               default_endpoint_disabled=bool(item.get("DisableExecuteApiEndpoint")))


def parse_rest_authorizer(item: dict[str, Any]) -> Authorizer:
    """
    One item of GetAuthorizers (REST): a Lambda (TOKEN or REQUEST) or Cognito authorizer.
    """
    function, _ = lambda_of_uri(item.get("authorizerUri", ""))
    pools = [arn.rsplit("/", 1)[-1] for arn in item.get("providerARNs") or []]
    return Authorizer(id=item["id"], name=item.get("name", ""), type=item.get("type", ""),
                      identity_source=item.get("identitySource", ""), function=function, issuer=", ".join(pools))


def parse_v2_authorizer(item: dict[str, Any]) -> Authorizer:
    """
    GetAuthorizer's answer (HTTP or WebSocket): a JWT authorizer or a Lambda (REQUEST) one.
    """
    function, _ = lambda_of_uri(item.get("AuthorizerUri", ""))
    sources = item.get("IdentitySource") or []
    issuer = (item.get("JwtConfiguration") or {}).get("Issuer", "")
    return Authorizer(id=item["AuthorizerId"], name=item.get("Name", ""), type=item.get("AuthorizerType", ""),
                      identity_source=", ".join(sources), function=function, issuer=issuer)


def parse_rest_integration(item: dict[str, Any] | None) -> Integration | None:
    """
    A REST method's methodIntegration, or None when it has none.
    """
    if not item:
        return None
    uri = item.get("uri", "")
    function, region = lambda_of_uri(uri)
    return Integration(type=item.get("type", ""), uri=uri, method=item.get("httpMethod", ""), function=function,
                       function_region=region, timeout_ms=item.get("timeoutInMillis"),
                       connection=item.get("connectionType", "INTERNET"))


def parse_v2_integration(item: dict[str, Any]) -> Integration:
    """
    One item of GetIntegrations.
    """
    uri = item.get("IntegrationUri", "")
    function, region = lambda_of_uri(uri)
    return Integration(type=item.get("IntegrationType", ""), uri=uri, method=item.get("IntegrationMethod", ""),
                       function=function, function_region=region, timeout_ms=item.get("TimeoutInMillis"),
                       connection=item.get("ConnectionType", "INTERNET"),
                       payload_version=item.get("PayloadFormatVersion", ""), subtype=item.get("IntegrationSubtype", ""))


def _rest_auth_type(method: dict[str, Any]) -> str:
    """
    A REST method's authorization: CUSTOM (a Lambda authorizer) and COGNITO_USER_POOLS as they are, NONE / AWS_IAM.
    """
    return method.get("authorizationType") or "NONE"


def parse_rest_routes(resources: list[dict[str, Any]], authorizers: dict[str, Authorizer]) -> list[Route]:
    """
    GetResources (with its methods embedded) as routes: one per method of each resource path.
    """
    routes = []
    for resource in resources:
        path = resource.get("path", "/")
        for verb, method in (resource.get("resourceMethods") or {}).items():
            method = method or {}
            routes.append(Route(method=verb, path=path, auth_type=_rest_auth_type(method),
                                authorizer=authorizers.get(method.get("authorizerId", "")),
                                api_key_required=bool(method.get("apiKeyRequired")),
                                integration=parse_rest_integration(method.get("methodIntegration")),
                                validator=method.get("requestValidatorId", ""), route_id=resource.get("id", ""),
                                scopes=list(method.get("authorizationScopes") or [])))
    return routes


def parse_v2_routes(items: list[dict[str, Any]], integrations: dict[str, Integration],
                    authorizers: dict[str, Authorizer]) -> list[Route]:
    """
    GetRoutes as routes, each with the integration its Target names ('integrations/<id>') and its authorizer.
    """
    routes = []
    for item in items:
        key = item.get("RouteKey", "")
        method, _, path = key.partition(" ") if " " in key else ("", "", key)
        target = (item.get("Target") or "").removeprefix("integrations/")
        routes.append(Route(method=method, path=path, auth_type=item.get("AuthorizationType") or "NONE",
                            authorizer=authorizers.get(item.get("AuthorizerId", "")),
                            api_key_required=bool(item.get("ApiKeyRequired")), integration=integrations.get(target),
                            route_id=item.get("RouteId", ""), scopes=list(item.get("AuthorizationScopes") or [])))
    return routes


def _when(value: Any) -> datetime | None:
    return value if isinstance(value, datetime) else None


def parse_rest_stage(item: dict[str, Any]) -> Stage:
    """
    One item of GetStages (REST): the */* method settings are the stage's defaults.
    """
    defaults = (item.get("methodSettings") or {}).get(ALL_METHODS) or {}
    access = item.get("accessLogSettings") or {}
    return Stage(name=item["stageName"], deployment_id=item.get("deploymentId", ""), created=_when(item.get("createdDate")),
                 updated=_when(item.get("lastUpdatedDate")), description=item.get("description", ""),
                 throttle_rate=defaults.get("throttlingRateLimit"), throttle_burst=defaults.get("throttlingBurstLimit"),
                 logging=defaults.get("loggingLevel") or "OFF", data_trace=bool(defaults.get("dataTraceEnabled")),
                 access_log_group=_log_group(access.get("destinationArn", "")),
                 access_log_format=access.get("format", ""), tracing=bool(item.get("tracingEnabled")),
                 cache_enabled=bool(item.get("cacheClusterEnabled")), cache_size=item.get("cacheClusterSize", ""),
                 waf=(item.get("webAclArn") or "").rsplit("/", 2)[-2] if item.get("webAclArn") else "",
                 detailed_metrics=bool(defaults.get("metricsEnabled")), variables=sorted(item.get("variables") or {}))


def parse_v2_stage(item: dict[str, Any]) -> Stage:
    """
    One item of GetStages (HTTP or WebSocket): DefaultRouteSettings are the stage's defaults.
    """
    defaults = item.get("DefaultRouteSettings") or {}
    access = item.get("AccessLogSettings") or {}
    return Stage(name=item["StageName"], deployment_id=item.get("DeploymentId", ""), created=_when(item.get("CreatedDate")),
                 updated=_when(item.get("LastUpdatedDate")), description=item.get("Description", ""),
                 throttle_rate=defaults.get("ThrottlingRateLimit"), throttle_burst=defaults.get("ThrottlingBurstLimit"),
                 logging=defaults.get("LoggingLevel") or "OFF", data_trace=bool(defaults.get("DataTraceEnabled")),
                 access_log_group=_log_group(access.get("DestinationArn", "")),
                 access_log_format=access.get("Format", ""), detailed_metrics=bool(defaults.get("DetailedMetricsEnabled")),
                 auto_deploy=bool(item.get("AutoDeploy")), variables=sorted(item.get("StageVariables") or {}))
