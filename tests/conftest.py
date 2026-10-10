import os
import sys

import pytest

# Import the analyzers the way an installed notebook does: `from aws_analyzer import s3`, from src/.
SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, SRC)

# The analyzer modules, which only ever load as aws_analyzer.<name>. A copy loaded under its bare name too (`import
# s3`, from some other folder on sys.path) would have its own classes, and isinstance() between the two would fail.
MODULES = ("s3", "s3_explorer", "dynamodb", "bedrock_kb", "bedrock_chat", "sagemaker_env", "opensearch",
           "lambda_functions")

for name, value in {
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "AWS_DEFAULT_REGION": "us-east-1",
}.items():
    os.environ[name] = value


@pytest.fixture(autouse=True, scope="session")
def _one_copy_of_each_module():
    yield
    twins = sorted(name for name in MODULES if sys.modules.get(name) is not None)
    assert not twins, f"loaded outside the aws_analyzer package as well: {', '.join(twins)}"
