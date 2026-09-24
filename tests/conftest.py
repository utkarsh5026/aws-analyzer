import os
import sys

# Import analyzers the way a notebook would: the single file sitting next to it (`import s3`).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "analyzers"))

for name, value in {
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "AWS_DEFAULT_REGION": "us-east-1",
}.items():
    os.environ[name] = value
