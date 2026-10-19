"""
A bucket policy in plain English, statement by statement, and what's risky in it.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..helpers import s3_uri
from ..models import PolicyStatement


# ---- bucket policies in plain English

_POLICY_ACTIONS = {
    name.lower(): text
    for name, text in {
        "*": "everything",
        "s3:*": "everything in S3",
        "s3:Get*": "all read actions",
        "s3:List*": "all list actions",
        "s3:Put*": "all write actions",
        "s3:Delete*": "all delete actions",
        "s3:GetObject": "read files",
        "s3:GetObjectVersion": "read old versions",
        "s3:PutObject": "upload / overwrite files",
        "s3:DeleteObject": "delete files",
        "s3:DeleteObjectVersion": "permanently delete versions",
        "s3:ListBucket": "list files",
        "s3:ListBucketVersions": "list versions",
        "s3:GetBucketLocation": "look up the region",
        "s3:GetObjectAcl": "read file ACLs",
        "s3:PutObjectAcl": "change file ACLs",
        "s3:GetObjectTagging": "read file tags",
        "s3:PutObjectTagging": "change file tags",
        "s3:AbortMultipartUpload": "cancel uploads",
        "s3:ListMultipartUploadParts": "list upload parts",
        "s3:ListBucketMultipartUploads": "list unfinished uploads",
        "s3:RestoreObject": "restore archived files",
        "s3:PutBucketPolicy": "change the bucket policy",
        "s3:DeleteBucketPolicy": "delete the bucket policy",
        "s3:PutBucketAcl": "change the bucket ACL",
        "s3:DeleteBucket": "delete the bucket",
        "s3:PutLifecycleConfiguration": "change lifecycle rules",
        "s3:PutBucketVersioning": "change versioning",
        "s3:ReplicateObject": "replicate files in",
        "s3:ReplicateDelete": "replicate deletes in",
    }.items()
}

_WRITE_VERBS = (
    "put",
    "delete",
    "replicate",
    "restore",
    "abort",
    "create",
    "bypass",
    "update",
)

_CONDITION_KEYS = {
    "aws:securetransport": "HTTPS",
    "aws:sourcevpce": "VPC endpoint",
    "aws:sourcevpc": "VPC",
    "aws:sourceip": "source IP",
    "aws:vpcsourceip": "VPC source IP",
    "aws:principalorgid": "caller's organization",
    "aws:principalorgpaths": "caller's organization path",
    "aws:principalaccount": "caller's account",
    "aws:sourceaccount": "source account",
    "aws:sourcearn": "source ARN",
    "aws:principalarn": "caller ARN",
    "aws:userid": "caller user id",
    "aws:username": "caller user name",
    "s3:tlsversion": "TLS version",
    "s3:x-amz-server-side-encryption": "upload encryption",
    "s3:x-amz-acl": "upload ACL",
}

# Condition keys that narrow who can use an Allow (when compared positively, not with a Not... operator).
_RESTRICTING_KEYS = {
    "aws:sourcevpce",
    "aws:sourcevpc",
    "aws:sourceip",
    "aws:vpcsourceip",
    "aws:principalorgid",
    "aws:principalorgpaths",
    "aws:principalaccount",
    "aws:sourceaccount",
    "aws:sourcearn",
    "aws:principalarn",
    "aws:userid",
    "aws:username",
    "aws:sourceowner",
}

_CONDITION_OPERATORS = {
    "stringequals": "=",
    "stringequalsignorecase": "=",
    "stringnotequals": "≠",
    "stringnotequalsignorecase": "≠",
    "stringlike": "matches",
    "stringnotlike": "doesn't match",
    "arnequals": "=",
    "arnlike": "matches",
    "arnnotequals": "≠",
    "arnnotlike": "doesn't match",
    "ipaddress": "in",
    "notipaddress": "not in",
    "numericequals": "=",
    "numericnotequals": "≠",
    "numericlessthan": "<",
    "numericlessthanequals": "≤",
    "numericgreaterthan": ">",
    "numericgreaterthanequals": "≥",
    "bool": "=",
    "dateequals": "=",
    "datelessthan": "before",
    "dategreaterthan": "after",
}

_PRINCIPAL_ARN_RE = re.compile(r"^arn:aws[\w-]*:(?:iam|sts)::([^:]*):(.+)$")


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else [value]


def _describe_principal(kind: str, value: str) -> tuple[str, str | None]:
    """
    One principal -> (plain text, its account id or None).
    """
    if kind == "AWS":
        if value == "*":
            return "anyone (public)", None
        if value.isdigit():
            return f"account {value}", value
        match = _PRINCIPAL_ARN_RE.match(value)
        if match:
            account, resource = match.groups()
            if account == "cloudfront":
                return (
                    f"CloudFront origin access identity {resource.rsplit(' ', 1)[-1]}",
                    None,
                )
            if resource == "root":
                return f"account {account}", account
            kind_, _, name = resource.partition("/")
            name = (
                name.split("/")[0]
                if kind_ == "assumed-role"
                else name.rsplit("/", 1)[-1]
            )
            label = {"assumed-role": "role session of"}.get(
                kind_, kind_.replace("-", " ")
            )
            return f"{label} {name} (account {account})", account
        return value, None
    if kind == "Service":
        return f"AWS service {value}", None
    if kind == "CanonicalUser":
        return f"canonical user {value[:12]}…", None
    if kind == "Federated":
        return f"users signed in through {value}", None
    return f"{kind} {value}", None


def _describe_resource(arn: str) -> str:
    if arn == "*":
        return "any resource"
    if ":s3:::" not in arn:
        return arn
    bucket, _, key = arn.split(":::", 1)[1].partition("/")
    if not key:
        return f"bucket {bucket}"
    return f"all files in {bucket}" if key == "*" else s3_uri(bucket, key)


def _describe_condition(operator: str, key: str, values: Any) -> tuple[str, bool]:
    """
    One condition -> (plain text, whether it narrows down who can use the statement).
    """
    base = operator.split(":")[-1]
    if_exists = base.lower().endswith("ifexists")
    base = base[:-8] if if_exists else base
    lowered, texts = key.lower(), [str(v) for v in _as_list(values)]
    label = _CONDITION_KEYS.get(lowered, key)
    restricts = (
        lowered in _RESTRICTING_KEYS
        and "not" not in base.lower()
        and base.lower() != "null"
    )
    if lowered == "aws:securetransport" and base.lower() == "bool":
        text = "over HTTPS" if texts[0].lower() == "true" else "not over HTTPS"
    elif base.lower() == "null":
        text = f"{label} {'not set' if texts[0].lower() == 'true' else 'is set'}"
    else:
        text = (
            f"{label} {_CONDITION_OPERATORS.get(base.lower(), base)} {', '.join(texts)}"
        )
    return text + (" (when present)" if if_exists else ""), restricts


def explain_policy(
    policy: dict | str | None, own_account: str | None = None
) -> list[PolicyStatement]:
    """
    Bucket policy (dict or JSON text) -> one PolicyStatement per statement, in plain English.
    With own_account, principals from any other account are listed in `other_accounts`.
    """
    if not policy:
        return []
    doc = json.loads(policy) if isinstance(policy, str) else policy
    explained = []
    for i, stmt in enumerate(_as_list(doc.get("Statement", []))):
        who, accounts, anyone = [], [], False
        principal_key = "NotPrincipal" if "NotPrincipal" in stmt else "Principal"
        principal = stmt.get(principal_key, {})
        entries = (
            [("AWS", "*")]
            if principal == "*"
            else [
                (kind, value)
                for kind, values in principal.items()
                for value in _as_list(values)
            ]
        )
        for kind, value in entries:
            text, account = _describe_principal(kind, value)
            who.append(text)
            anyone = anyone or (kind == "AWS" and value == "*")
            if account and account != own_account and own_account is not None:
                accounts.append(account)
        if principal_key == "NotPrincipal":
            who, anyone = ["everyone except " + ", ".join(who)], True

        action_key = "NotAction" if "NotAction" in stmt else "Action"
        raw_actions = [str(a) for a in _as_list(stmt.get(action_key, []))]
        actions = [_POLICY_ACTIONS.get(a.lower(), a) for a in raw_actions]
        writes = action_key == "NotAction" or any(
            "*" in a or a.lower().split(":", 1)[-1].startswith(_WRITE_VERBS)
            for a in raw_actions
        )
        if action_key == "NotAction":
            actions = ["everything except " + ", ".join(actions)]

        resource_key = "NotResource" if "NotResource" in stmt else "Resource"
        resources = [
            _describe_resource(str(r)) for r in _as_list(stmt.get(resource_key, []))
        ]
        if resource_key == "NotResource":
            resources = ["everything except " + ", ".join(resources)]

        conditions, restricted = [], False
        for operator, pairs in (stmt.get("Condition") or {}).items():
            for key, values in pairs.items():
                text, restricts = _describe_condition(operator, key, values)
                conditions.append(text)
                restricted = restricted or restricts
        explained.append(
            PolicyStatement(
                sid=str(stmt.get("Sid") or f"#{i + 1}"),
                effect=stmt.get("Effect", "Allow"),
                who=who,
                actions=actions,
                resources=resources,
                conditions=conditions,
                anyone=anyone,
                restricted=restricted,
                writes=writes,
                other_accounts=sorted(set(accounts)),
            )
        )
    return explained


def policy_findings(statements: list[PolicyStatement]) -> list[tuple[str, str]]:
    """
    Plain-language risks in an explained bucket policy -> [(level, message)].
    """
    found: list[tuple[str, str]] = []
    for st in statements:
        if st.effect != "Allow":
            continue
        name = f"Statement {st.sid}"
        if st.public:
            change = " That includes changing or deleting data." if st.writes else ""
            found.append(
                (
                    "warn",
                    f"{name} lets anyone on the internet: {', '.join(st.actions)} "
                    f"(on {', '.join(st.resources)}).{change}",
                )
            )
        elif st.anyone:
            found.append(
                (
                    "info",
                    f"{name} is open to everyone, but only when: {'; '.join(st.conditions)}.",
                )
            )
        if any(w.startswith("everyone except") for w in st.who):
            found.append(
                (
                    "warn",
                    f"{name} uses Allow with NotPrincipal: everyone except the listed principals "
                    "gets access.",
                )
            )
        if st.other_accounts:
            found.append(
                (
                    "info",
                    f"{name} gives other AWS accounts access ({', '.join(st.other_accounts)}): "
                    f"{', '.join(st.actions)}.",
                )
            )
    if statements and not any(
        st.effect == "Deny" and "not over HTTPS" in st.conditions for st in statements
    ):
        found.append(
            (
                "info",
                "No statement blocks plain HTTP. A Deny when aws:SecureTransport is false "
                "makes every request use HTTPS.",
            )
        )
    return found
