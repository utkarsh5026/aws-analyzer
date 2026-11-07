"""
The price list for SQS, SNS and EventBridge: requests to a queue, messages published to a topic and what it delivers
by protocol, events published to a bus, and the CloudWatch reads the reports make. us-east-1 list prices (the first
tier, after the free tier), read 2026-11-07 from the AWS Price List API (AWSQueueService, published 2026-09-11;
AmazonSNS, 2026-11-04; AWSEvents, 2026-09-24; AmazonCloudWatch); pass MessagingAnalyzer(prices={...}) for your own.
"""

from __future__ import annotations


MESSAGING_PRICES: dict[str, float] = {
    "sqs_request": 0.0000004,
    "sqs_fifo_request": 0.0000005,
    "sns_publish": 0.0000005,
    "sns_http": 0.0000006,
    "sns_email": 0.00002,
    "sns_sms": 0.00645,
    "sns_firehose": 0.00000019,
    "events_custom": 0.000001,
    "metric_read": 0.00001,
}

PRICE_NOTES = {
    "sqs_request": "SQS standard queues: per request ($0.40 per million; a batch of up to 10 is one request)",
    "sqs_fifo_request": "SQS FIFO queues: per request ($0.50 per million)",
    "sns_publish": "SNS: per message published ($0.50 per million)",
    "sns_http": "SNS: per HTTP/HTTPS delivery ($0.60 per million); SQS and Lambda deliveries are free",
    "sns_email": "SNS: per email delivery ($2.00 per 100,000)",
    "sns_sms": "SNS: per SMS to the US (it varies by country)",
    "sns_firehose": "SNS: per delivery to Data Firehose ($0.19 per million)",
    "events_custom": "EventBridge: per custom event published ($1.00 per million); AWS services' events are free",
    "metric_read": "CloudWatch GetMetricData: per metric read ($0.01 per 1,000)",
}

DELIVERY_PRICE_KEYS = {"http": "sns_http", "https": "sns_http", "email": "sns_email", "email-json": "sns_email",
                       "sms": "sns_sms", "firehose": "sns_firehose"}

DAYS_PER_MONTH = 30
