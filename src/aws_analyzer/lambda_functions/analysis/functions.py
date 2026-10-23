"""
Reading what Lambda returns: a function's configuration, its runtime's support, its resource
policy (who may call it) and its event source mappings.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from ..._kit.fmt import _plural, _utcnow

from ..helpers import (
    ENDING_SOON_DAYS, LATEST_RUNTIMES, RUNTIMES, _INVOKE_ACTIONS, _SERVICE_KINDS, _STREAM_KINDS, _day, _lambda_time,
    runtime_family,
)
from ..models import Function, Layer, RuntimeStatus, Trigger, Version


def masked_config(config: dict[str, Any]) -> dict[str, Any]:
    """
    A copy of a function's configuration with environment variable values replaced by '(hidden)', so it can be
    shown or shared: the values often hold passwords and keys.
    """
    shown = json.loads(json.dumps(config, default=str))
    shown.pop("ResponseMetadata", None)
    variables = (shown.get("Environment") or {}).get("Variables")
    if isinstance(variables, dict):
        shown["Environment"]["Variables"] = dict.fromkeys(variables, "(hidden)")
    return shown


def _unqualified(arn: str) -> str:
    """
    'arn:aws:lambda:r:a:function:etl:prod' -> 'arn:aws:lambda:r:a:function:etl'.
    """
    return ":".join(arn.split(":")[:7]) if arn.startswith("arn:") else arn


def parse_function(config: dict[str, Any], region: str = "") -> Function:
    """
    One function's configuration (an entry of ListFunctions, or GetFunction's Configuration) -> Function.
    Environment variable values are left out (only their names are kept), and hidden in .raw.
    """
    arn = config.get("FunctionArn") or ""
    name = config.get("FunctionName") or (arn.split(":")[6] if arn.count(":") >= 6 else "")
    environment = config.get("Environment") or {}
    env_error = environment.get("Error") or {}
    vpc = config.get("VpcConfig") or {}
    logging = config.get("LoggingConfig") or {}
    return Function(
        name=name,
        arn=_unqualified(arn),
        region=region or (arn.split(":")[3] if arn.count(":") >= 6 else ""),
        runtime=config.get("Runtime") or None,
        handler=config.get("Handler") or None,
        description=config.get("Description") or "",
        memory=int(config.get("MemorySize") or 128),
        timeout=int(config.get("Timeout") or 3),
        architecture=(config.get("Architectures") or ["x86_64"])[0],
        package_type=config.get("PackageType") or "Zip",
        code_size=int(config.get("CodeSize") or 0),
        last_modified=_lambda_time(config.get("LastModified")),
        ephemeral_storage=int((config.get("EphemeralStorage") or {}).get("Size") or 512),
        layers=[Layer(layer.get("Arn", ""), layer.get("CodeSize")) for layer in config.get("Layers") or []],
        env_names=sorted(environment.get("Variables") or {}),
        env_error=env_error.get("Message") or env_error.get("ErrorCode") or None,
        role=config.get("Role") or None,
        vpc_id=vpc.get("VpcId") or None,
        subnets=list(vpc.get("SubnetIds") or []),
        security_groups=list(vpc.get("SecurityGroupIds") or []),
        tracing=(config.get("TracingConfig") or {}).get("Mode"),
        snapstart=(config.get("SnapStart") or {}).get("ApplyOn") == "PublishedVersions",
        state=config.get("State"),
        state_reason=config.get("StateReason"),
        last_update=config.get("LastUpdateStatus"),
        last_update_reason=config.get("LastUpdateStatusReason"),
        log_group=logging.get("LogGroup") or f"/aws/lambda/{name}",
        log_format=logging.get("LogFormat"),
        log_level=logging.get("ApplicationLogLevel"),
        dead_letter=(config.get("DeadLetterConfig") or {}).get("TargetArn"),
        kms_key=config.get("KMSKeyArn"),
        file_systems=[
            f"{fs.get('Arn', '?').split('/')[-1]} at {fs.get('LocalMountPath', '?')}"
            for fs in config.get("FileSystemConfigs") or []
        ],
        version=config.get("Version") or "$LATEST",
        raw=masked_config(config),
    )


def parse_version(config: dict[str, Any]) -> Version:
    """
    One entry of ListVersionsByFunction -> Version: its number, code size and SHA-256, when it was published, its
    description and runtime.
    """
    return Version(config.get("Version", ""), int(config.get("CodeSize") or 0), _lambda_time(config.get("LastModified")),
                   config.get("Description") or "", config.get("Runtime"), config.get("CodeSha256") or "")


def runtime_status(runtime: str | None, *, package_type: str = "Zip", today: date | None = None) -> RuntimeStatus:
    """
    Where a runtime stands in AWS's support schedule (RUNTIMES) on `today`: supported, ending within
    ENDING_SOON_DAYS, deprecated (no more patches), or blocked (functions on it can't be updated).
    """
    if not runtime:
        return RuntimeStatus(None, "image" if package_type == "Image" else "unknown")
    upgrade = LATEST_RUNTIMES.get(runtime_family(runtime) or "")
    upgrade = upgrade if upgrade != runtime else None
    dates = RUNTIMES.get(runtime)
    if dates is None:
        return RuntimeStatus(runtime, "unknown", upgrade=upgrade)
    deprecated, block_create, block_update = (_day(d) for d in dates)
    today = today or _utcnow().date()
    days_left = (deprecated - today).days if deprecated else None
    if block_update and today >= block_update:
        state = "blocked"
    elif deprecated and today >= deprecated:
        state = "deprecated"
    elif days_left is not None and days_left <= ENDING_SOON_DAYS:
        state = "ending"
    else:
        state = "supported"
    return RuntimeStatus(runtime, state, deprecated, block_create, block_update, days_left, upgrade)


def _policy_condition(conditions: Any, key: str) -> str | None:
    """
    The first value a policy statement's Condition gives `key` (any operator, keys compared without case).
    """
    if not isinstance(conditions, dict):
        return None
    for block in conditions.values():
        if not isinstance(block, dict):
            continue
        for name, value in block.items():
            if name.lower() == key.lower():
                values = value if isinstance(value, list) else [value]
                return str(values[0]) if values else None
    return None


def _source_name(arn: str | None) -> tuple[str, str]:
    """
    (name, detail) from a trigger's source ARN: a bucket, queue, topic, rule, stream, API (with its route) or
    log group.
    """
    if not arn:
        return "", ""
    parts = arn.split(":", 5)
    if len(parts) < 6:
        return arn, ""
    service, resource = parts[2], parts[5]
    if service == "execute-api":  # a1b2c3/prod/GET/orders
        api, _, route = resource.partition("/")
        stage, _, rest = route.partition("/")
        method, _, path = rest.partition("/")
        if not route:
            return api, ""
        stage_text = "any stage" if stage in ("*", "") else f"stage {stage}"
        call = f"{'any method' if method in ('*', '') else method} /{path}".rstrip("/") if rest else ""
        return api, ", ".join(filter(None, [stage_text, call.replace("/*", "/…") if call else ""]))
    if service == "logs" and resource.startswith("log-group:"):
        return resource.split(":")[1], ""
    if service == "dynamodb" and resource.startswith("table/"):
        return resource.split("/")[1], ""
    if service == "kafka" and resource.startswith("cluster/"):
        return resource.split("/")[1], ""
    if service == "mq":
        return resource.split(":")[1] if resource.startswith("broker:") else resource, ""
    if service == "rds":
        return resource.split(":")[-1], ""
    return resource.rstrip("/").split("/")[-1] or resource, ""


def parse_policy(policy: dict[str, Any] | str | None) -> list[Trigger]:
    """
    A function's resource-based policy (GetPolicy) as the callers it allows: services (S3, EventBridge, API
    Gateway...) with the bucket, rule or API they're limited to, other accounts, and anyone (public).
    """
    if not policy:
        return []
    document = json.loads(policy) if isinstance(policy, str) else policy
    statements = document.get("Statement") or []
    statements = [statements] if isinstance(statements, dict) else statements
    triggers: list[Trigger] = []
    for st in statements:
        if not isinstance(st, dict) or st.get("Effect", "Allow") != "Allow":
            continue
        actions = st.get("Action") or []
        actions = [actions] if isinstance(actions, str) else actions
        actions = [str(a).lower() for a in actions]
        if not any(a in _INVOKE_ACTIONS for a in actions):
            continue
        conditions = st.get("Condition") or {}
        source_arn = _policy_condition(conditions, "AWS:SourceArn")
        source_account = _policy_condition(conditions, "AWS:SourceAccount")
        org = _policy_condition(conditions, "aws:PrincipalOrgID")
        token = _policy_condition(conditions, "lambda:EventSourceToken")  # Alexa skills
        auth_type = _policy_condition(conditions, "lambda:FunctionUrlAuthType")
        via_url = _policy_condition(conditions, "lambda:InvokedViaFunctionUrl")
        sid = st.get("Sid")
        principal = st.get("Principal")
        if isinstance(principal, dict):
            services = principal.get("Service") or []
            services = [services] if isinstance(services, str) else list(services)
            accounts = principal.get("AWS") or []
            accounts = [accounts] if isinstance(accounts, str) else list(accounts)
        else:
            services, accounts = [], [principal] if principal else []
        if "*" in accounts:
            if auth_type or via_url or all(a == "lambda:invokefunctionurl" for a in actions):
                public = (auth_type or "").upper() == "NONE" or (via_url or "").lower() == "true"
                detail = "no sign-in (auth type NONE)" if public else "callers sign with IAM (auth type AWS_IAM)"
                triggers.append(Trigger("Function URL", "", via="function URL", detail=detail, public=public,
                                        statement=sid, principal="*"))
            elif source_arn or source_account or org:
                kind = _SERVICE_KINDS.get(f"{source_arn.split(':')[2]}.amazonaws.com", "AWS service") if (
                    source_arn and source_arn.count(":") >= 5) else ("AWS organization" if org else "AWS account")
                name, detail = _source_name(source_arn)
                triggers.append(Trigger(kind, name or org or source_account or "", arn=source_arn,
                                        via="resource policy", detail=detail, statement=sid, principal="*"))
            elif conditions:  # limited some other way (a VPC endpoint, an IP range...): not open to everyone
                keys = sorted({k for block in conditions.values() if isinstance(block, dict) for k in block})
                triggers.append(Trigger("Any caller meeting its conditions", "", via="resource policy",
                                        statement=sid, principal="*", detail="when " + ", ".join(keys)))
            else:
                triggers.append(Trigger("Anyone", "any AWS account", via="resource policy", public=True,
                                        statement=sid, principal="*",
                                        detail="Principal \"*\" with no condition"))
        for service in services:
            name, detail = _source_name(source_arn)
            kind = _SERVICE_KINDS.get(service, service.split(".")[0])
            triggers.append(Trigger(kind, name or (f"account {source_account}" if source_account else ""),
                                    arn=source_arn, via="resource policy", detail=detail, statement=sid,
                                    principal=service, unscoped=not (source_arn or source_account or token or org)))
        for account in accounts:
            if account == "*":
                continue
            who = account.split(":")[4] if account.startswith("arn:") and account.count(":") >= 5 else account
            role = account.split("/")[-1] if ":role/" in account or ":user/" in account else ""
            triggers.append(Trigger("AWS account", who, via="resource policy", detail=role and f"only {role}",
                                    statement=sid, principal=account))
    unique: dict[tuple[Any, ...], Trigger] = {}
    for t in triggers:  # a public URL takes two statements (InvokeFunctionUrl and InvokeFunction): show it once
        unique.setdefault((t.kind, t.source, t.detail, t.public), t)
    return list(unique.values())


def parse_event_source_mapping(mapping: dict[str, Any]) -> Trigger:
    """
    One event source mapping (ListEventSourceMappings): the queue or stream Lambda polls for the function.
    """
    arn = mapping.get("EventSourceArn")
    service = arn.split(":")[2] if arn and arn.count(":") >= 5 else ""
    kind = _STREAM_KINDS.get(service, service or "event source")
    name, _ = _source_name(arn)
    if not arn and mapping.get("SelfManagedEventSource"):
        kind = "Kafka (self-managed)"
        servers = (mapping["SelfManagedEventSource"].get("Endpoints") or {}).get("KAFKA_BOOTSTRAP_SERVERS") or []
        name = servers[0] if servers else ""
    if mapping.get("Topics"):
        name = f"{name} ({', '.join(mapping['Topics'])})" if name else ", ".join(mapping["Topics"])
    parts = []
    if mapping.get("BatchSize"):
        parts.append(f"batches of up to {mapping['BatchSize']:,}")
    if mapping.get("MaximumBatchingWindowInSeconds"):
        parts.append(f"waits up to {mapping['MaximumBatchingWindowInSeconds']} s to fill one")
    filters = (mapping.get("FilterCriteria") or {}).get("Filters") or []
    if filters:
        parts.append(_plural(len(filters), "filter"))
    most = (mapping.get("ScalingConfig") or {}).get("MaximumConcurrency")
    if most:
        parts.append(f"at most {most} at once")
    failure = ((mapping.get("DestinationConfig") or {}).get("OnFailure") or {}).get("Destination")
    if failure:
        parts.append(f"failed batches go to {_source_name(failure)[0]}")
    return Trigger(kind, name, arn=arn, via="event source mapping", state=mapping.get("State"),
                   detail=", ".join(parts), last_result=mapping.get("LastProcessingResult"),
                   uuid=mapping.get("UUID"))
