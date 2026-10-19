"""
What the findings of more than one report share: the section each permission reads, how full is
too full, and prices per hour in words.
"""

from __future__ import annotations

from ..._kit.errors import _why
from ..._kit.fmt import HOURS_PER_MONTH, human_money


_SECTIONS = {  # section -> (what it is, the permission that reads it)
    "identity": ("who you're signed in as", "sts:GetCallerIdentity"),
    "notebook": ("the notebook instance", "sagemaker:DescribeNotebookInstance"),
    "lifecycle": (
        "its lifecycle configuration",
        "sagemaker:DescribeNotebookInstanceLifecycleConfig",
    ),
    "app": ("the Studio app", "sagemaker:DescribeApp"),
    "space": ("the space", "sagemaker:DescribeSpace"),
    "profile": ("the user profile", "sagemaker:DescribeUserProfile"),
    "domain": ("the domain", "sagemaker:DescribeDomain"),
    "notebook instances": ("notebook instances", "sagemaker:ListNotebookInstances"),
    "notebook details": (
        "some notebook instances' settings",
        "sagemaker:DescribeNotebookInstance",
    ),
    "apps": ("Studio apps", "sagemaker:ListApps"),
    "endpoints": ("endpoints", "sagemaker:ListEndpoints"),
    "endpoint details": ("some endpoints' instances", "sagemaker:DescribeEndpoint"),
    "training jobs": ("training jobs", "sagemaker:ListTrainingJobs"),
    "training details": (
        "some training jobs' instances",
        "sagemaker:DescribeTrainingJob",
    ),
    "processing jobs": ("processing jobs", "sagemaker:ListProcessingJobs"),
    "processing details": (
        "some processing jobs' instances",
        "sagemaker:DescribeProcessingJob",
    ),
    "metrics": ("endpoint traffic", "cloudwatch:GetMetricData"),
}

FULL = 0.85  # a disk or memory this full gets a warning


def _couldnt_read(
    errors: dict[str, str], consequences: dict[str, str] | None = None
) -> str:
    """
    "Couldn't read the domain (AccessDeniedException; needs sagemaker:DescribeDomain), so ...".
    """
    parts = [
        f"{_SECTIONS.get(k, (k, ''))[0]} ({_why(v, _SECTIONS[k][1]) if k in _SECTIONS else v})"
        for k, v in errors.items()
    ]
    after = [consequences[k] for k in errors if consequences and k in consequences]
    return (
        "Couldn't read "
        + ", ".join(parts)
        + (f", so {' and '.join(after)}" if after else "")
        + "."
    )


def _money_per_hour(hourly: float | None) -> str:
    """
    '$0.23/hour, up to $168/month' (or 'an unknown price per hour').
    """
    if hourly is None:
        return "an unknown price per hour"
    return f"{human_money(hourly)}/hour, up to {human_money(hourly * HOURS_PER_MONTH)}/month"


def _full(share: float | None) -> bool:
    return share is not None and share >= FULL
