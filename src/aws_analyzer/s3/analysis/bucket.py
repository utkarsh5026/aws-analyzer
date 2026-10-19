"""
A bucket's settings in words (public access, encryption, lifecycle rules) and what's wrong
with them.
"""

from __future__ import annotations

from datetime import date, datetime

from ..._kit.fmt import human_size

from ..models import BucketConfig


def bucket_findings(
    cfg: BucketConfig, account_block: dict[str, bool] | None = None
) -> list[tuple[str, str]]:
    """
    Plain-language risks / cost notes for a bucket config -> [(level, message)].
    account_block: the account-level Block Public Access settings ({} = not set, None = unknown).
    """
    found: list[tuple[str, str]] = []
    pab = cfg.public_access_block
    account_on = bool(account_block) and all(account_block.values())
    if (
        not account_on
        and "public_access_block" not in cfg.errors
        and not (pab and all(pab.values()))
    ):
        where = (
            "this bucket or the account"
            if account_block is not None
            else "this bucket (account-level Block Public Access may still apply)"
        )
        found.append(("warn", f"Block Public Access is not fully on for {where}."))
    if cfg.policy_is_public:
        found.append(("warn", "The bucket policy grants public access."))
    if "encryption" not in cfg.errors and not cfg.encryption:
        found.append(("warn", "No default encryption configured."))
    if "lifecycle" not in cfg.errors:
        enabled = [
            rule for rule in cfg.lifecycle_rules if rule.get("Status") == "Enabled"
        ]
        if cfg.versioning == "Enabled" and not any(
            "NoncurrentVersionExpiration" in rule for rule in enabled
        ):
            found.append(
                (
                    "warn",
                    "Versioning is on but no lifecycle rule expires noncurrent versions: every "
                    "overwritten or deleted object is kept (and billed) forever.",
                )
            )
        if not any("AbortIncompleteMultipartUpload" in rule for rule in enabled):
            found.append(
                (
                    "info",
                    "No lifecycle rule aborts incomplete multipart uploads; leftover parts are billed "
                    f"until aborted: uploads('s3://{cfg.name}/') lists them.",
                )
            )
    if cfg.errors:
        found.append(
            (
                "info",
                "Couldn't read: "
                + ", ".join(f"{k} ({v})" for k, v in cfg.errors.items()),
            )
        )
    return found


def public_exposure(cfg: BucketConfig, account_block: dict[str, bool] | None) -> str:
    """
    One-word public access status for the overview table.
    """
    if cfg.policy_is_public:
        return "PUBLIC (policy)"
    if any(
        settings and all(settings.values())
        for settings in (account_block, cfg.public_access_block)
    ):
        return "blocked"
    if "public_access_block" in cfg.errors:
        return f"? ({cfg.errors['public_access_block']})"
    return "not blocked"


def block_label(settings: dict[str, bool] | None) -> str:
    """
    Block Public Access settings -> 'all on' / '2/4 on' / 'not set'.
    """
    if not settings:
        return "not set"
    on = sum(bool(v) for v in settings.values())
    return "all on" if on == len(settings) else f"{on}/{len(settings)} on"


def encryption_label(cfg: BucketConfig) -> str:
    if "encryption" in cfg.errors:
        return f"? ({cfg.errors['encryption']})"
    labels = {"AES256": "SSE-S3", "aws:kms": "SSE-KMS", "aws:kms:dsse": "DSSE-KMS"}
    label = labels.get(cfg.encryption or "", cfg.encryption or "none")
    return label + (" + bucket key" if cfg.bucket_key_enabled else "")


def rule_scope(rule: dict) -> str:
    filt = rule.get("Filter")
    if filt is None:
        return rule.get("Prefix") or "whole bucket"  # legacy rule format
    filt = filt.get("And", filt)
    bits = [f"prefix '{filt['Prefix']}'"] if filt.get("Prefix") else []
    tags = filt.get("Tags") or ([filt["Tag"]] if "Tag" in filt else [])
    bits += [f"tag {t['Key']}={t['Value']}" for t in tags]
    if "ObjectSizeGreaterThan" in filt:
        bits.append(f"size > {human_size(filt['ObjectSizeGreaterThan'])}")
    if "ObjectSizeLessThan" in filt:
        bits.append(f"size < {human_size(filt['ObjectSizeLessThan'])}")
    return ", ".join(bits) or "whole bucket"


def rule_actions(rule: dict) -> str:
    def when(spec: dict) -> str:
        if "Days" in spec:
            return f"{spec['Days']} days"
        moment = spec.get("Date")
        return (
            f"{moment:%Y-%m-%d}"
            if isinstance(moment, (date, datetime))
            else str(moment)
        )

    actions = [
        f"to {t['StorageClass']} after {when(t)}" for t in rule.get("Transitions", [])
    ]
    expiration = rule.get("Expiration", {})
    if "Days" in expiration or "Date" in expiration:
        actions.append(f"expire after {when(expiration)}")
    if expiration.get("ExpiredObjectDeleteMarker"):
        actions.append("remove expired delete markers")
    actions += [
        f"noncurrent to {t['StorageClass']} after {t.get('NoncurrentDays')} days"
        for t in rule.get("NoncurrentVersionTransitions", [])
    ]
    if "NoncurrentVersionExpiration" in rule:
        spec = rule["NoncurrentVersionExpiration"]
        keep = (
            f" (keep newest {spec['NewerNoncurrentVersions']})"
            if spec.get("NewerNoncurrentVersions")
            else ""
        )
        actions.append(
            f"delete noncurrent after {spec.get('NoncurrentDays')} days{keep}"
        )
    if "AbortIncompleteMultipartUpload" in rule:
        actions.append(
            f"abort incomplete uploads after {rule['AbortIncompleteMultipartUpload']['DaysAfterInitiation']} days"
        )
    return "\n".join(actions) or "-"
