"""
API Gateway's price list: requests for REST and HTTP APIs, messages and connection minutes for WebSocket APIs, the
hourly price of each cache size, and the CloudWatch reads the reports make. us-east-1 list prices (the first tier),
read 2026-11-01 from the AWS Price List API (AmazonApiGateway, published 2026-10-13, and AmazonCloudWatch); pass
APIGatewayAnalyzer(prices={...}) for your own.
"""

from __future__ import annotations


APIGATEWAY_PRICES: dict[str, float] = {
    "rest_request": 0.0000035,
    "http_request": 0.000001,
    "websocket_message": 0.000001,
    "websocket_minute": 0.00000025,
    "cache_0.5": 0.02,
    "cache_1.6": 0.038,
    "cache_6.1": 0.20,
    "cache_13.5": 0.25,
    "cache_28.4": 0.50,
    "cache_58.2": 1.00,
    "cache_118": 1.90,
    "cache_237": 3.80,
    "metric_read": 0.00001,
    "logs_ingest_gb": 0.50,
}

PRICE_NOTES = {
    "rest_request": "REST APIs: per request ($3.50 per million, the first 333 million a month)",
    "http_request": "HTTP APIs: per request ($1.00 per million, the first 300 million a month)",
    "websocket_message": "WebSocket APIs: per message ($1.00 per million, the first billion a month)",
    "websocket_minute": "WebSocket APIs: per connection minute ($0.25 per million)",
    "cache_0.5": "REST API cache: per hour for each size in GB (0.5 GB shown)",
    "metric_read": "CloudWatch GetMetricData: per metric read ($0.01 per 1,000)",
    "logs_ingest_gb": "CloudWatch Logs: per GB of access log written",
}

DAYS_PER_MONTH = 30
