"""
Step Functions' price list: state transitions for Standard workflows, requests and duration for Express ones, and
the CloudWatch reads the reports make. us-east-1 list prices, read 2026-10-31 from the AWS Price List API
(AWSStepFunctions, AmazonCloudWatch); pass StepFunctionsAnalyzer(prices={...}) for your own.
"""

from __future__ import annotations


STEPFUNCTIONS_PRICES: dict[str, float] = {
    "transition": 0.000025,
    "express_request": 0.000001,
    "express_gb_second": 0.00001667,
    "express_memory_gb": 0.0625,
    "metric_read": 0.00001,
    "logs_ingest_gb": 0.50,
}

PRICE_NOTES = {
    "transition": "Standard: per state transition ($0.025 per 1,000), after 4,000 free a month",
    "express_request": "Express: per execution ($1.00 per million)",
    "express_gb_second": "Express: per GB-second of run time, first tier",
    "express_memory_gb": "Express: memory billed per execution, at least 64 MB",
    "metric_read": "CloudWatch GetMetricData: per metric read ($0.01 per 1,000)",
    "logs_ingest_gb": "CloudWatch Logs: per GB of execution logs written",
}

FREE_TRANSITIONS = 4_000
