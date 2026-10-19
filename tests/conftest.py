import os
import sys

import pytest

# Import the analyzers the way an installed notebook does: `from aws_analyzer import s3`, from src/.
SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, SRC)

# The analyzer modules, which only ever load as aws_analyzer.<name>. A copy loaded under its bare name too (`import
# s3`, from some other folder on sys.path) would have its own classes, and isinstance() between the two would fail.
MODULES = ("s3", "s3_explorer", "dynamodb", "bedrock_kb", "bedrock_chat", "sagemaker_env", "opensearch",
           "lambda_functions", "agent")

for name, value in {
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "AWS_DEFAULT_REGION": "us-east-1",
}.items():
    os.environ[name] = value
os.environ.pop("AWS_ANALYZER_TZ", None)  # reports show times in UTC unless a test picks a zone
os.environ.pop("AWS_ANALYZER_THEME", None)
AGENT_MODEL_SETTINGS = ("AWS_ANALYZER_AGENT_MODEL", "OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY",
                        "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT")
for name in AGENT_MODEL_SETTINGS:
    os.environ.pop(name, None)


@pytest.fixture(autouse=True)
def _utc_and_the_default_theme_again():
    """
    A test that called set_timezone() or set_theme() leaves the next one in UTC and the default theme.
    """
    yield
    fmt = sys.modules.get("aws_analyzer._kit.fmt")
    if fmt is not None:  # a test that called set_timezone() leaves the next one in UTC
        fmt.set_timezone(None)
    themes = sys.modules.get("aws_analyzer._kit.themes")
    if themes is not None:
        themes.set_theme(None)


@pytest.fixture(autouse=True, scope="session")
def _one_copy_of_each_module():
    yield
    twins = sorted(name for name in MODULES if sys.modules.get(name) is not None)
    assert not twins, f"loaded outside the aws_analyzer package as well: {', '.join(twins)}"
