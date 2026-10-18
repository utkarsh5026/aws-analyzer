"""
What the code reader knows: how deep it reads, which modules are libraries, what
each AWS call and common call does, and what looks like a secret.
"""

from __future__ import annotations

import re

from ...helpers import MB


# ----------------------------------------------------------------------------- how_it_works(): the code, read with ast

_MAP_DEPTH = 8  # calls followed from the handler; a function deeper than this is named, not opened

_MAP_STEPS = 2_500  # outline lines, at most: past that, the functions it calls are named, not opened

_MAP_RUNS = 3_000  # function bodies read (one function is read again for other arguments), at most

_MAP_FILES = 400  # its own Python files map_package() reads, at most...

_MAP_BYTES = 8 * MB  # ...and their size, together

# What the Python runtime gives every function, so the package needn't carry it.
_RUNTIME_MODULES = frozenset({"boto3", "botocore", "s3transfer", "jmespath", "dateutil", "urllib3", "six",
                              "awslambdaric", "bootstrap"})

# Libraries often packed without their .dist-info folder (which is how the others are told from the code's own
# files): their files aren't read as its own code.
_LIBRARIES = frozenset({
    "requests", "certifi", "idna", "charset_normalizer", "chardet", "pytz", "yaml", "attr", "attrs", "pydantic",
    "pydantic_core", "typing_extensions", "numpy", "pandas", "aws_lambda_powertools", "aws_xray_sdk", "wrapt",
    "psycopg2", "pymysql", "sqlalchemy", "jwt", "cryptography", "cffi", "_cffi_backend", "simplejson", "redis",
    "opensearchpy", "elasticsearch", "requests_aws4auth", "markupsafe", "jinja2", "httpx", "httpcore", "anyio",
    "sniffio", "h11", "openai", "anthropic", "google", "PIL", "pyarrow", "sklearn", "scipy", "joblib", "fastapi",
    "starlette", "mangum", "flask", "werkzeug", "click", "itsdangerous", "dotenv", "orjson", "ujson", "pymongo",
    "bson", "dns", "nacl", "OpenSSL", "rsa", "pyasn1", "jose", "tenacity", "backoff", "structlog", "loguru",
    "multidict", "yarl", "aiohttp", "aiosignal", "frozenlist", "async_timeout", "awswrangler", "langchain",
    "langchain_core", "langchain_aws", "tiktoken", "regex", "tqdm", "smart_open", "s3fs", "fsspec", "aiobotocore",
    "chalice", "zappa", "dateparser", "tzdata", "tzlocal", "packaging", "pkg_resources", "setuptools", "_distutils_hack",
    "pip", "wheel", "annotated_types", "jsonschema", "fastjsonschema", "pyrsistent", "referencing", "rpds",
})

# Standard-library modules a newer Python removed: (the first Python without it). Code that imports one fails with
# Runtime.ImportModuleError on that runtime, a trap when moving to a newer one.
_REMOVED_MODULES = {
    "distutils": (3, 12), "imp": (3, 12), "asynchat": (3, 12), "asyncore": (3, 12), "smtpd": (3, 12),
    "cgi": (3, 13), "cgitb": (3, 13), "pipes": (3, 13), "crypt": (3, 13), "telnetlib": (3, 13), "nntplib": (3, 13),
    "imghdr": (3, 13), "sndhdr": (3, 13), "mailcap": (3, 13), "uu": (3, 13), "xdrlib": (3, 13), "aifc": (3, 13),
    "audioop": (3, 13), "chunk": (3, 13), "msilib": (3, 13), "nis": (3, 13), "ossaudiodev": (3, 13), "spwd": (3, 13),
    "sunau": (3, 13), "lib2to3": (3, 13),
}

_ADDED_MODULES = {"tomllib": (3, 11)}  # standard-library modules a newer Python added: (the first Python with it)

_SERVICE_NAMES = {  # boto3's name -> what people call it, where botocore's serviceId reads oddly
    "stepfunctions": "Step Functions", "apigatewaymanagementapi": "API Gateway (WebSocket)", "es": "OpenSearch",
    "opensearchserverless": "OpenSearch Serverless", "iot-data": "IoT", "sesv2": "SES", "cloudwatch": "CloudWatch",
    "logs": "CloudWatch Logs", "appconfigdata": "AppConfig", "ApiGatewayManagementApi": "API Gateway (WebSocket)",
}

_IAM_PREFIXES = {"cloudwatch": "cloudwatch", "iot-data": "iot", "sesv2": "ses", "appconfigdata": "appconfig"}

_IAM_ACTIONS = {  # (service, operation) -> the IAM action, where it isn't <prefix>:<operation>
    ("s3", "ListObjectsV2"): "s3:ListBucket", ("s3", "ListObjects"): "s3:ListBucket", ("s3", "HeadBucket"):
    "s3:ListBucket", ("s3", "HeadObject"): "s3:GetObject", ("s3", "SelectObjectContent"): "s3:GetObject",
    ("s3", "CopyObject"): "s3:PutObject", ("s3", "DeleteObjects"): "s3:DeleteObject",
    ("s3", "CreateMultipartUpload"): "s3:PutObject", ("s3", "UploadPart"): "s3:PutObject",
    ("s3", "CompleteMultipartUpload"): "s3:PutObject", ("s3", "ListObjectVersions"): "s3:ListBucketVersions",
    ("s3", "ListBuckets"): "s3:ListAllMyBuckets", ("bedrock-runtime", "Converse"): "bedrock:InvokeModel",
    ("bedrock-runtime", "ConverseStream"): "bedrock:InvokeModelWithResponseStream",
    ("apigatewaymanagementapi", "PostToConnection"): "execute-api:ManageConnections",
    ("apigatewaymanagementapi", "GetConnection"): "execute-api:ManageConnections",
    ("apigatewaymanagementapi", "DeleteConnection"): "execute-api:ManageConnections",
    ("sqs", "SendMessageBatch"): "sqs:SendMessage", ("sqs", "DeleteMessageBatch"): "sqs:DeleteMessage",
    ("sqs", "ChangeMessageVisibilityBatch"): "sqs:ChangeMessageVisibility", ("sns", "PublishBatch"): "sns:Publish",
    ("dynamodb", "ExecuteStatement"): "dynamodb:PartiQL*", ("sts", "GetCallerIdentity"): "",
}

_RUNS_PREFIXES = ("Invoke", "Converse", "RetrieveAndGenerate", "Start", "Detect", "Analyze", "Translate", "Synthesize",
                  "Classify", "Recognize", "Compare", "Decrypt", "Encrypt", "GenerateDataKey", "Sign", "Verify",
                  "AssumeRole", "Rerank", "Execute", "Run")

_SENDS_PREFIXES = ("Send", "Publish", "PostToConnection", "PutEvents")  # a message or an event, sent on

_READS_PREFIXES = ("Get", "List", "Describe", "Head", "Query", "Scan", "Select", "BatchGet", "Search", "Lookup",
                   "Retrieve", "Filter", "Check", "Estimate", "Preview", "Receive", "Download")

_BATCHES = {  # a call that sends one item, made in a loop -> (the call that sends many, how many at a time)
    ("dynamodb", "PutItem"): ("table.batch_writer() (BatchWriteItem)", 25),
    ("dynamodb", "DeleteItem"): ("table.batch_writer() (BatchWriteItem)", 25),
    ("dynamodb", "GetItem"): ("batch_get_item", 100),
    ("sqs", "SendMessage"): ("send_message_batch", 10),
    ("sqs", "DeleteMessage"): ("delete_message_batch", 10),
    ("sns", "Publish"): ("publish_batch", 10),
    ("s3", "DeleteObject"): ("delete_objects", 1000),
    ("kinesis", "PutRecord"): ("put_records", 500),
    ("firehose", "PutRecord"): ("put_record_batch", 500),
    ("ssm", "GetParameter"): ("get_parameters", 10),
    ("secretsmanager", "GetSecretValue"): ("batch_get_secret_value", 20),
}

_RESOURCE_ACTIONS = {  # a boto3 resource object's methods that aren't the client's -> the operation they call
    ("s3", "Object"): {"get": "GetObject", "put": "PutObject", "delete": "DeleteObject", "load": "HeadObject",
                       "reload": "HeadObject", "copy_from": "CopyObject", "upload_file": "PutObject",
                       "upload_fileobj": "PutObject", "download_file": "GetObject", "download_fileobj": "GetObject"},
    ("s3", "ObjectSummary"): {"get": "GetObject", "put": "PutObject", "delete": "DeleteObject", "load": "HeadObject"},
    ("s3", "Bucket"): {"upload_file": "PutObject", "upload_fileobj": "PutObject", "download_file": "GetObject",
                       "download_fileobj": "GetObject"},
    ("dynamodb", "Table"): {"load": "DescribeTable", "reload": "DescribeTable"},
    ("sqs", "Queue"): {"send_messages": "SendMessageBatch", "receive_messages": "ReceiveMessage",
                       "delete_messages": "DeleteMessageBatch", "load": "GetQueueAttributes"},
    ("sqs", "Message"): {"delete": "DeleteMessage"},
    ("sns", "Topic"): {"load": "GetTopicAttributes"},
}

_COLLECTIONS = {("s3", "objects"): "ListObjects", ("s3", "object_versions"): "ListObjectVersions",
                ("s3", "buckets"): "ListBuckets", ("dynamodb", "tables"): "ListTables", ("sqs", "queues"): "ListQueues",
                ("sns", "topics"): "ListTopics"}  # a resource's collection -> the list call iterating it makes

_TRANSFERS = {"upload_file": "PutObject", "upload_fileobj": "PutObject", "download_file": "GetObject",
              "download_fileobj": "GetObject", "copy": "CopyObject"}  # an S3 client's transfer methods

_GUESSES = {  # a method name only one AWS service has, for a client it couldn't follow
    "put_item": ("dynamodb", "PutItem"), "get_item": ("dynamodb", "GetItem"), "update_item": ("dynamodb", "UpdateItem"),
    "delete_item": ("dynamodb", "DeleteItem"), "batch_write_item": ("dynamodb", "BatchWriteItem"),
    "batch_get_item": ("dynamodb", "BatchGetItem"), "transact_write_items": ("dynamodb", "TransactWriteItems"),
    "get_object": ("s3", "GetObject"), "put_object": ("s3", "PutObject"), "head_object": ("s3", "HeadObject"),
    "delete_object": ("s3", "DeleteObject"), "list_objects_v2": ("s3", "ListObjectsV2"),
    "copy_object": ("s3", "CopyObject"), "send_message": ("sqs", "SendMessage"),
    "send_message_batch": ("sqs", "SendMessageBatch"), "receive_message": ("sqs", "ReceiveMessage"),
    "get_secret_value": ("secretsmanager", "GetSecretValue"), "get_parameter": ("ssm", "GetParameter"),
    "get_parameters_by_path": ("ssm", "GetParametersByPath"), "start_execution": ("stepfunctions", "StartExecution"),
    "invoke_model": ("bedrock-runtime", "InvokeModel"), "put_events": ("events", "PutEvents"),
    "start_query_execution": ("athena", "StartQueryExecution"),
}

_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options", "request")

_HTTP_SESSIONS = {"requests.Session": "requests", "requests.session": "requests", "httpx.Client": "httpx",
                  "httpx.AsyncClient": "httpx", "urllib3.PoolManager": "urllib3", "aiohttp.ClientSession": "aiohttp",
                  "http.client.HTTPSConnection": "http.client", "http.client.HTTPConnection": "http.client"}

_DEFAULT_TIMEOUTS = {"httpx", "aiohttp"}  # HTTP libraries that stop waiting by themselves (5 s and 5 minutes)

_LOG_LEVELS = {"debug": "logs", "info": "logs", "warning": "logs a warning", "warn": "logs a warning",
               "error": "logs an error", "exception": "logs an error", "critical": "logs an error", "log": "logs"}

_JSON_LOADS = {"json.loads", "json.load", "simplejson.loads", "orjson.loads", "ujson.loads"}

_B64_DECODES = {"base64.b64decode", "base64.standard_b64decode", "base64.urlsafe_b64decode"}

_COMMANDS = {"subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.check_output",
             "subprocess.Popen", "os.system", "os.popen"}

_FILE_WRITERS = {"to_csv", "to_parquet", "to_json", "to_excel", "to_pickle", "to_feather", "savefig"}

_ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "route", "api_route"}

_DICT_METHODS = {"get", "items", "keys", "values", "copy", "pop", "setdefault", "update"}

_CACHES = {"lru_cache", "cache", "cached", "cached_property"}

_SECRET_NAME_RE = re.compile(r"passw(?:or)?d|secret|token|api_?key|apikey|private_?key|access_?key|credential", re.I)

_NOT_SECRET_NAME_RE = re.compile(
    r"(?:name|id|arn|path|env|var|url|uri|header|field|param|parameter|file|type|len|length|key_name|prefix|ttl|"
    r"expiry|expires|timeout)$", re.I)

_PLACEHOLDERS = {"password", "secret", "token", "changeme", "change-me", "test", "dummy", "example", "none", "null",
                 "redacted", "todo"}

_AWS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")

_LAMBDA_ENV = {  # environment variables Lambda sets itself: reading one needs no setting
    "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_EXECUTION_ENV", "AWS_LAMBDA_FUNCTION_NAME",
    "AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "AWS_LAMBDA_FUNCTION_VERSION", "AWS_LAMBDA_INITIALIZATION_TYPE",
    "AWS_LAMBDA_LOG_GROUP_NAME", "AWS_LAMBDA_LOG_STREAM_NAME", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN", "AWS_LAMBDA_RUNTIME_API", "LAMBDA_TASK_ROOT", "LAMBDA_RUNTIME_DIR", "_HANDLER",
    "_X_AMZN_TRACE_ID", "TZ", "LANG", "PATH", "LD_LIBRARY_PATH", "PYTHONPATH", "AWS_XRAY_CONTEXT_MISSING",
    "AWS_XRAY_DAEMON_ADDRESS", "AWS_LAMBDA_LOG_FORMAT", "AWS_LAMBDA_LOG_LEVEL", "AWS_LAMBDA_MAX_CONCURRENCY",
    "HOME", "PWD", "_AWS_XRAY_DAEMON_ADDRESS", "_AWS_XRAY_DAEMON_PORT", "AWS_LAMBDA_EXEC_WRAPPER",
}
