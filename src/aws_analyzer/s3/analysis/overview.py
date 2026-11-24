"""
The all-buckets overview as checks and fixes, with no AWS calls: each bucket's state per check (bucket_checks, the
cells of the overview's grid), its warnings grouped by the one change that clears them (overview_fixes: one account
setting can clear a warning in every bucket), and the sentence that leads the report when a bucket is open to anyone
on the internet (overview_verdict).

A check counts the same warnings bucket_findings and policy_findings give, through the same rules
(block_public_access_off, versions_kept_forever, policy_findings itself), so the fixes clear exactly the warnings
the overview has.
"""

from __future__ import annotations

import json
from collections import Counter

from ..._kit.render import _call

from ..models import BucketChecks, BucketConfig, Check, Fix, OverviewVerdict
from .bucket import block_public_access_off, encryption_label, versions_kept_forever
from .policy import explain_policy, policy_findings


BAD, WARN, UNKNOWN, OK = "bad", "warn", "unknown", "ok"

POLICY, BLOCK, ENCRYPTION, OLD_VERSIONS = "Bucket policy", "Block Public Access", "Encryption", "Old versions"

CHECK_COLUMNS = (POLICY, BLOCK, ENCRYPTION, OLD_VERSIONS)

ACCOUNT_BLOCK, BUCKET_BLOCK, ENCRYPT, EXPIRE = "account-block", "bucket-block", "encryption", "versions"

POLICY_FIX = "policy:"

_STATE_ORDER = (BAD, WARN, UNKNOWN, OK)

_UNREAD = "?"

_BLOCK_ALL = "'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true'"

_SSE_S3 = json.dumps(
    {"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}, separators=(",", ":")
)

_NO_ACCOUNT = "<account-id>"

_NAMED_IN_VERDICT = 3


def state_rank(state: str) -> int:
    """
    'bad' -> 0, 'warn' -> 1, 'unknown' -> 2, 'ok' -> 3: worst first.
    """
    return _STATE_ORDER.index(state)


def _policy_check(cfg: BucketConfig, account: str | None) -> Check:
    """
    Whether the bucket policy lets anyone in. Bad when it's public (S3's own verdict, or a statement that is);
    a warning for a policy that's broad without being public (Allow with NotPrincipal).
    """
    fix = f"{POLICY_FIX}{cfg.name}"
    statements = explain_policy(cfg.policy, account)
    warnings = [message for level, message in policy_findings(statements) if level == "warn"]
    public_by_s3 = 1 if cfg.policy_is_public else 0
    count = public_by_s3 + len(warnings)
    reason = warnings[0] if warnings else "The bucket policy grants public access."
    is_public = cfg.policy_is_public or any(statement.public for statement in statements)
    if is_public:
        return Check(POLICY, BAD, "Public", fix=fix, warnings=count, reason=reason)
    if warnings:
        return Check(POLICY, WARN, "Too broad", fix=fix, warnings=count, reason=reason)
    if "policy" in cfg.errors and "policy_document" in cfg.errors:
        return Check(POLICY, UNKNOWN, _UNREAD, reason=f"Couldn't read the policy ({cfg.errors['policy']}).")
    if cfg.policy is None and not cfg.has_policy:
        return Check(POLICY, OK, "No policy")
    return Check(POLICY, OK, "Not public")


def _block_check(cfg: BucketConfig, account_block: dict[str, bool] | None) -> Check:
    """
    Whether Block Public Access is on for the bucket or the account. When it's off and the account's setting could
    be read, the fix is the account's: one setting for every bucket.
    """
    if block_public_access_off(cfg, account_block):
        fix = BUCKET_BLOCK if account_block is None else ACCOUNT_BLOCK
        settings = cfg.public_access_block or {}
        word = "Partly on" if any(settings.values()) else "Off"
        return Check(BLOCK, WARN, word, fix=fix, warnings=1, reason="Block Public Access is not fully on.")
    if account_block and all(account_block.values()):
        return Check(BLOCK, OK, "On (account)")
    if "public_access_block" in cfg.errors:
        error = cfg.errors["public_access_block"]
        return Check(BLOCK, UNKNOWN, _UNREAD, reason=f"Couldn't read Block Public Access ({error}).")
    return Check(BLOCK, OK, "On")


def _encryption_check(cfg: BucketConfig) -> Check:
    """
    The bucket's default encryption: a warning when there's none.
    """
    if "encryption" in cfg.errors:
        return Check(ENCRYPTION, UNKNOWN, _UNREAD, reason=f"Couldn't read encryption ({cfg.errors['encryption']}).")
    if not cfg.encryption:
        return Check(ENCRYPTION, WARN, "None", fix=ENCRYPT, warnings=1, reason="No default encryption.")
    return Check(ENCRYPTION, OK, encryption_label(cfg).removesuffix(" + bucket key"))


def _versions_check(cfg: BucketConfig) -> Check:
    """
    What happens to old versions: a warning when versioning keeps them forever.
    """
    if versions_kept_forever(cfg):
        reason = "Versioning keeps every overwritten and deleted file, and no lifecycle rule expires them."
        return Check(OLD_VERSIONS, WARN, "Kept forever", fix=EXPIRE, warnings=1, reason=reason)
    unread = [section for section in ("versioning", "lifecycle") if section in cfg.errors]
    if unread:
        error = cfg.errors[unread[0]]
        return Check(OLD_VERSIONS, UNKNOWN, _UNREAD, reason=f"Couldn't read {unread[0]} ({error}).")
    words = {"Enabled": "Expired", "Suspended": "Suspended"}
    return Check(OLD_VERSIONS, OK, words.get(cfg.versioning or "", "Not versioned"))


def bucket_checks(
    cfg: BucketConfig, account_block: dict[str, bool] | None = None, *, account: str | None = None
) -> BucketChecks:
    """
    One bucket's row in the overview: its bucket policy, Block Public Access, encryption and old versions, each
    'bad', 'warn', 'unknown' (couldn't be read) or 'ok', with a word for its cell and the fix a problem needs.
    account_block: the account's Block Public Access settings ({} = not set, None = unknown); account: your
    account id, so a policy that shares with it isn't counted as shared with another account.
    """
    checks = [
        _policy_check(cfg, account),
        _block_check(cfg, account_block),
        _encryption_check(cfg),
        _versions_check(cfg),
    ]
    return BucketChecks(bucket=cfg.name, region=cfg.region, checks=checks)


def _fix_words(key: str, buckets: list[str], *, account: str | None, shares_publicly: bool) -> tuple[str, str, str]:
    """
    The fix's title, why it matters and the call or command that does it, for the buckets it covers (the first one
    named in the command when it takes one bucket).
    """
    first = buckets[0]
    for_each = f" The command is for {first}: run it for each." if len(buckets) > 1 else ""
    if key.startswith(POLICY_FIX):
        why = (
            "Remove or narrow the statement that lets everyone in, or move what it shares to a bucket meant to be "
            "public."
        )
        return f"Stop sharing {first} with the internet", why, _call("policy", first)
    if key == ACCOUNT_BLOCK:
        caution = (
            " It also stops what a bucket's policy shares with everyone, so check first that nothing relies on that."
            if shares_publicly else ""
        )
        command = (
            f"aws s3control put-public-access-block --account-id {account or _NO_ACCOUNT} "
            f"--public-access-block-configuration {_BLOCK_ALL}"
        )
        why = f"One setting covers every bucket, including ones made later.{caution}"
        return "Turn on Block Public Access for the account", why, command
    if key == BUCKET_BLOCK:
        command = f"aws s3api put-public-access-block --bucket {first} --public-access-block-configuration {_BLOCK_ALL}"
        why = f"The account's own setting couldn't be read, so this sets it per bucket.{for_each}"
        return "Turn on Block Public Access", why, command
    if key == ENCRYPT:
        command = f"aws s3api put-bucket-encryption --bucket {first} --server-side-encryption-configuration '{_SSE_S3}'"
        why = f"New files are stored unencrypted. SSE-S3 is free, and nothing changes for readers.{for_each}"
        return "Turn on default encryption", why, command
    why = (
        "Versioning keeps every overwritten and deleted file, billed until a lifecycle rule expires it. See what "
        "they cost first."
    )
    return "Expire old versions", why, _call("versions", f"s3://{first}/")


def overview_fixes(buckets: list[BucketChecks], *, account: str | None = None) -> list[Fix]:
    """
    The overview's warnings grouped by the change that clears them, most urgent first: a public bucket policy,
    then the rest by how many warnings each clears. Each Fix says what to change, why, the call or command, the
    buckets it covers (in the order given, so pass the biggest first) and how many warnings it clears.
    """
    grouped: dict[str, list[tuple[str, Check]]] = {}
    for row in buckets:
        for check in row.problems:
            if check.fix:
                grouped.setdefault(check.fix, []).append((row.bucket, check))
    shares_publicly = any(row.check(POLICY).state == BAD for row in buckets)

    fixes = []
    for key, found in grouped.items():
        names = [bucket for bucket, _ in found]
        title, why, call = _fix_words(key, names, account=account, shares_publicly=shares_publicly)
        severity = BAD if any(check.state == BAD for _, check in found) else WARN
        clears = sum(check.warnings for _, check in found)
        fixes.append(Fix(key=key, title=title, why=why, call=call, severity=severity, buckets=names, clears=clears))
    return sorted(fixes, key=lambda fix: (state_rank(fix.severity), -fix.clears))


def overview_verdict(buckets: list[BucketChecks]) -> OverviewVerdict | None:
    """
    The sentence that leads the overview when a bucket policy lets anyone on the internet in: the bucket and the
    statement that does it, or the buckets when there are several. None when no bucket is public.
    """
    public = [row for row in buckets if row.check(POLICY).state == BAD]
    if not public:
        return None
    if len(public) == 1:
        row = public[0]
        return OverviewVerdict(f"{row.bucket} is open to anyone on the internet", row.check(POLICY).reason)

    names = [row.bucket for row in public[:_NAMED_IN_VERDICT]]
    others = len(public) - len(names)
    named = ", ".join(names) + (f" and {others} more" if others else "")
    detail = f"{named}: each bucket's policy lets anyone in. policy(name) shows which statement does it."
    return OverviewVerdict(f"{len(public)} buckets are open to anyone on the internet", detail)


def overview_counts(buckets: list[BucketChecks]) -> Counter:
    """
    How many buckets are worst at each state: Counter({'bad': 1, 'warn': 2, 'ok': 1}).
    """
    return Counter(row.worst for row in buckets)
