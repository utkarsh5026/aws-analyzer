"""
S3Analyzer: talks to S3, CloudWatch and STS, and returns the dataclasses in models. Never prints,
and never writes to a bucket.
"""

from __future__ import annotations
