"""The PyPI package (pyproject.toml, src/aws_analyzer/): the wheel ships every analyzer, and the package's names
resolve to their classes. CI's Package job also builds the real wheel and imports it with only boto3."""

import importlib
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_wheel_ships_every_analyzer():
    tomllib = pytest.importorskip("tomllib")  # stdlib from Python 3.11
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    shipped = config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    expected = {f"analyzers/{p.name}": f"aws_analyzer/{p.name}" for p in (ROOT / "analyzers").glob("*.py")}
    assert shipped == expected


@pytest.fixture
def package(tmp_path, monkeypatch):
    """aws_analyzer laid out the way the wheel installs it, imported from a temporary folder."""
    target = tmp_path / "aws_analyzer"
    shutil.copytree(ROOT / "src" / "aws_analyzer", target)
    for path in (ROOT / "analyzers").glob("*.py"):
        shutil.copy(path, target / path.name)
    monkeypatch.syspath_prepend(str(tmp_path))
    yield importlib.import_module("aws_analyzer")
    for name in [n for n in sys.modules if n == "aws_analyzer" or n.startswith("aws_analyzer.")]:
        del sys.modules[name]


def test_package_exports_each_analyzer_and_view(package):
    assert re.fullmatch(r"\d+\.\d+\.\d+([ab]|rc)?\d*", package.__version__)
    assert not any(name.startswith("aws_analyzer.") for name in sys.modules)  # nothing loads until it's used
    for name in package.__all__[1:]:
        value = getattr(package, name)
        assert value.__name__ == name
        assert value.__module__ == f"aws_analyzer.{package._EXPORTS[name]}"
    assert set(package.__all__) <= set(dir(package))
    assert {"s3", "dynamodb", "bedrock_kb", "sagemaker_env", "s3_explorer"} <= set(dir(package))
    assert package.s3.parse_size("10MB") == 10 * 1024**2
    with pytest.raises(AttributeError, match="no attribute 'nope'"):
        package.nope


def test_explorer_builds_on_the_s3_installed_with_it(package):
    # A top-level `import s3` works here too (conftest puts analyzers/ on sys.path); the package's own wins.
    assert package.s3_explorer._s3_module() is package.s3
    assert package.S3Explorer.__module__ == "aws_analyzer.s3_explorer"
