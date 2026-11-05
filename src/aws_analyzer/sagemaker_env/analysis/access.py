"""
Who got into the notebooks: CloudTrail records of notebooks opened, started and stopped, read into AccessEvents,
grouped by person, and access()'s findings.
"""

from __future__ import annotations

import ipaddress
import json
from datetime import datetime, timezone
from typing import Any

from ..._kit.fmt import _plural, _utcnow, human_age

from ..models import AccessEvent, AccessReport, PersonAccess
from .common import _couldnt_read

EVENT_ACTIONS = {
    "CreatePresignedNotebookInstanceUrl": "opened",
    "CreatePresignedDomainUrl": "opened",
    "StartNotebookInstance": "started",
    "StopNotebookInstance": "stopped",
    "CreateApp": "started",
    "DeleteApp": "stopped",
}

HISTORY_DAYS = 90
LOOKUP_LIMIT = 2000
_UNUSUAL_IPS_SHOWN = 3
_SERVICE_NAMES = {"sagemaker.amazonaws.com": "SageMaker itself"}
_REFUSALS_SHOWN = 3


def _who(identity: dict[str, Any]) -> str:
    """
    A CloudTrail userIdentity in a few words: 'alice' (an IAM user), 'DataScientist/alice' (a role and its
    session), 'root user', or the AWS service that made the call.
    """
    kind = identity.get("type", "")
    arn = identity.get("arn", "")
    if kind == "Root":
        return "root user"
    if kind == "AWSService":
        service = identity.get("invokedBy", "")
        return _SERVICE_NAMES.get(service, service) if service else "an AWS service"
    if kind == "AssumedRole" and "assumed-role/" in arn:
        return arn.split("assumed-role/", 1)[1]
    if kind == "IAMUser":
        return identity.get("userName", "") or arn.rsplit("/", 1)[-1]
    return arn.rsplit("/", 1)[-1] if arn else (identity.get("principalId", "") or "unknown")


def _notebook_of(parameters: dict[str, Any]) -> str:
    """
    The notebook a call was about: a notebook instance's name, a Studio space, or 'user <profile>'.
    """
    if parameters.get("notebookInstanceName"):
        return parameters["notebookInstanceName"]
    if parameters.get("spaceName"):
        return parameters["spaceName"]
    if parameters.get("userProfileName"):
        return f"user {parameters['userProfileName']}"
    return ""


def _event_time(item: dict[str, Any], record: dict[str, Any]) -> datetime:
    """
    When the event happened, as an aware UTC datetime, from the LookupEvents item or its record.
    """
    when = item.get("EventTime")
    if isinstance(when, datetime):
        return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    text = str(record.get("eventTime", "")).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return _utcnow()


def parse_access_event(item: dict[str, Any]) -> AccessEvent | None:
    """
    One LookupEvents item -> AccessEvent, or None when it isn't a notebook being opened, started or stopped.
    A record that isn't valid JSON still gives what the item itself says.
    """
    name = item.get("EventName", "")
    if name not in EVENT_ACTIONS:
        return None
    try:
        record = json.loads(item.get("CloudTrailEvent") or "{}")
    except ValueError:
        record = {}
    identity = record.get("userIdentity") or {}
    parameters = record.get("requestParameters") or {}
    return AccessEvent(
        when=_event_time(item, record),
        event_name=name,
        action=EVENT_ACTIONS[name],
        notebook=_notebook_of(parameters),
        who=_who(identity) if identity else (item.get("Username", "") or "unknown"),
        who_arn=identity.get("arn", ""),
        identity_type=identity.get("type", ""),
        ip=record.get("sourceIPAddress", ""),
        user_agent=record.get("userAgent", ""),
        refused=record.get("errorCode", ""),
        domain_id=parameters.get("domainId", ""),
        username=item.get("Username", ""),
    )


def for_notebook(events: list[AccessEvent], name: str) -> list[AccessEvent]:
    """
    The events about one notebook: a notebook instance's name, a Studio space's, or 'd-abc123/space'.
    """
    wanted = name.strip()
    return [e for e in events if wanted in (e.notebook, f"{e.domain_id}/{e.notebook}")]


def _is_address(text: str) -> bool:
    """
    Whether CloudTrail's sourceIPAddress is an IP address, rather than the name of the AWS service that called.
    """
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def is_public_ip(text: str) -> bool:
    """
    Whether CloudTrail's sourceIPAddress is an address on the internet, not AWS itself ('sagemaker.amazonaws.com',
    'AWS Internal') or a private network.
    """
    try:
        return ipaddress.ip_address(text).is_global
    except ValueError:
        return False


def access_by_person(events: list[AccessEvent]) -> list[PersonAccess]:
    """
    The sign-ins and refusals grouped by who made them, the most sign-ins first.
    """
    found: dict[str, PersonAccess] = {}
    for event in events:
        if not (event.is_sign_in or event.refused):
            continue
        person = found.setdefault(event.who, PersonAccess(event.who))
        if event.refused:
            person.refused += 1
        else:
            person.sign_ins += 1
        if event.notebook and event.notebook not in person.notebooks:
            person.notebooks.append(event.notebook)
        if event.ip and event.ip not in person.ips:
            person.ips.append(event.ip)
        if person.last is None or event.when > person.last:
            person.last = event.when
    return sorted(found.values(), key=lambda p: (-p.sign_ins, -p.refused, p.who))


def _history_command(event: AccessEvent) -> str:
    """
    The AWS CLI call that lists everything that identity did, from CloudTrail.
    """
    username = event.username or event.who.rsplit("/", 1)[-1]
    return f"aws cloudtrail lookup-events --lookup-attributes AttributeKey=Username,AttributeValue={username}"


def _refusal_finding(refused: list[AccessEvent], now: datetime) -> tuple[str, str]:
    """
    The warning about tries AWS refused, naming the last one and how to see what else that identity did.
    """
    last = max(refused, key=lambda e: e.when)
    who = sorted({e.who for e in refused})
    shown = ", ".join(who[:_REFUSALS_SHOWN]) + (", …" if len(who) > _REFUSALS_SHOWN else "")
    where = f" from {last.ip}" if last.ip else ""
    about = f" on {last.notebook}" if last.notebook else ""
    return (
        "warn",
        f"{_plural(len(refused), 'attempt')} to get into a notebook {'was' if len(refused) == 1 else 'were'} "
        f"refused (the last {human_age(last.when, now)}, {last.refused}): someone without permission tried. "
        f"Who tried: {shown}. The last was {last.who}{where}{about}. If you don't know "
        f"who that is, see what else it did: {_history_command(last)}",
    )


def _times(count: int) -> str:
    """
    'once', 'twice', '5 times'.
    """
    return {1: "once", 2: "twice"}.get(count, f"{count:,} times")


def _root_finding(root: list[AccessEvent], now: datetime) -> tuple[str, str]:
    """
    The warning about the account's root user getting into a notebook.
    """
    last = max(root, key=lambda e: e.when)
    return (
        "warn",
        f"The account's root user opened or started a notebook {_times(len(root))} (the last "
        f"{human_age(last.when, now)}, {last.notebook or 'a notebook'}{' from ' + last.ip if last.ip else ''}): "
        "root can do anything in the account and shouldn't be used day to day. Sign in with an IAM role or "
        "Identity Center user instead, and make sure root has MFA.",
    )


def _addresses(count: int) -> str:
    """
    '1 internet address', '4 internet addresses'.
    """
    return f"{count:,} internet address{'' if count == 1 else 'es'}"


def _unusual_ip_finding(sign_ins: list[AccessEvent], now: datetime) -> tuple[str, str] | None:
    """
    A note naming the internet addresses only one sign-in came from, when sign-ins came from more than one.
    """
    by_ip: dict[str, list[AccessEvent]] = {}
    for event in sign_ins:
        if is_public_ip(event.ip):
            by_ip.setdefault(event.ip, []).append(event)
    once = [events[0] for events in by_ip.values() if len(events) == 1]
    has_usual_place = len(once) < len(by_ip)
    if not once or not has_usual_place:
        return None
    once.sort(key=lambda e: e.when, reverse=True)
    described = "; ".join(
        f"{e.ip} ({e.who}, {human_age(e.when, now)})" for e in once[:_UNUSUAL_IPS_SHOWN]
    )
    more = f" and {len(once) - _UNUSUAL_IPS_SHOWN} more" if len(once) > _UNUSUAL_IPS_SHOWN else ""
    return (
        "info",
        f"Notebooks were opened from {_addresses(len(by_ip))}, and {_addresses(len(once))} only once: "
        f"{described}{more}. If one isn't a place you or your team "
        "work from, check that identity's other calls in CloudTrail.",
    )


def access_findings(report: AccessReport, now: datetime | None = None) -> list[tuple[str, str]]:
    """
    Plain-language notes about who got into the notebooks: refused tries, the root user, sign-ins from an
    address seen once, and what the read couldn't cover -> [(level, message)].
    """
    now = now or _utcnow()
    found: list[tuple[str, str]] = []
    if report.refused:
        found.append(_refusal_finding(report.refused, now))
    root = [e for e in report.events if e.identity_type == "Root" and not e.refused]
    if root:
        found.append(_root_finding(root, now))
    unusual = _unusual_ip_finding(report.sign_ins, now)
    if unusual:
        found.append(unusual)
    if report.asked_days > HISTORY_DAYS:
        found.append((
            "info",
            f"CloudTrail's event history keeps {HISTORY_DAYS} days, so this covers {HISTORY_DAYS}, not "
            f"{report.asked_days}: older events are only kept by a trail that delivers to S3 "
            "(aws cloudtrail describe-trails shows yours).",
        ))
    if report.cut_short:
        found.append((
            "info",
            f"The read stopped at {report.limit:,} events, so older ones in the window aren't shown: "
            f"access(days={max(1, report.days // 4)}) narrows it, or access(limit={report.limit * 5}) reads more.",
        ))
    if report.errors:
        found.append(("info", _couldnt_read(report.errors, {"cloudtrail": "nothing could be listed"})))
    return found
