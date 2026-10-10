"""AWS errors: their codes, the permission a denied call needs, and `_Hint` for a question back to the user."""

from __future__ import annotations

from botocore.exceptions import BotoCoreError, ClientError


def _error_code(exc: ClientError) -> str:
    return exc.response.get("Error", {}).get("Code", "Unknown")


def _error_name(exc: ClientError | BotoCoreError) -> str:
    return _error_code(exc) if isinstance(exc, ClientError) else type(exc).__name__


def _why(code: str, permission: str) -> str:
    """('AccessDeniedException', 's3:GetObject') -> 'AccessDeniedException; needs s3:GetObject'. Other codes stay
    as they are."""
    return f"{code}; needs {permission}" if "denied" in code.lower() or code == "UnauthorizedOperation" else code


class _Hint(ValueError):
    """A question back to the user (e.g. which knowledge base), shown as a plain note rather than an error."""
