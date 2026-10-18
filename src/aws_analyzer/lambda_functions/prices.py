"""
Lambda's price list: requests, GB-seconds (x86_64 and arm64), provisioned concurrency, /tmp and
CloudWatch Logs. us-east-1 list prices; pass LambdaAnalyzer(prices={...}) for your own.
"""

from __future__ import annotations

from .._kit.fmt import HOURS_PER_MONTH


DAYS_PER_MONTH = HOURS_PER_MONTH / 24  # 30.4 days: what a month's estimate scales a daily rate by

# USD, us-east-1 list prices from the AWS Price List API (the AWSLambda and AmazonCloudWatch offer files) on
# 2026-10-05, before the free tier (1M requests and 400,000 GB-seconds a month for the whole account). Compute is the
# first pricing tier: the first 6 billion GB-seconds a month on x86_64, 7.5 billion on arm64. Other regions differ;
# pass LambdaAnalyzer(prices={...}) to use your own.
LAMBDA_PRICES: dict[str, float] = {
    "request": 0.20,  # per million requests, x86_64 and arm64 alike
    "gb_second": 0.0000166667,  # per GB-second of compute (memory x run time), x86_64
    "gb_second_arm": 0.0000133334,  # the same on arm64 (Graviton): 20% less
    "provisioned": 0.0000041667,  # per GB-second of provisioned concurrency kept ready, used or not, x86_64
    "provisioned_arm": 0.0000033334,  # the same on arm64
    "ephemeral_storage": 0.0000000309,  # per GB-second of /tmp above the 512 MB every function gets free
    "log_ingestion": 0.50,  # per GB a function logs to CloudWatch Logs (the first 10 TB a month)
    "log_storage": 0.03,  # per GB-month of logs CloudWatch Logs keeps
    "metric_request": 0.01,  # per 1,000 metrics read with CloudWatch GetMetricData (what these reports read)
}
