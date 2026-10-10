"""The PyPI package (pyproject.toml, src/aws_analyzer/): the wheel ships every analyzer, and the package's names
resolve to their classes. CI's Package job also builds the real wheel and imports it with only boto3."""

import importlib
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "aws_analyzer"


def test_wheel_ships_the_package_and_exports_every_analyzer():
    tomllib = pytest.importorskip("tomllib")  # stdlib from Python 3.11
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = config["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel["packages"] == ["src/aws_analyzer"] and "force-include" not in wheel
    modules = {p.stem for p in PACKAGE.glob("*.py") if not p.stem.startswith("_")}
    init = (PACKAGE / "__init__.py").read_text(encoding="utf-8")
    listed = re.search(r"^_MODULES = \(([^)]*)\)", init, re.M | re.S)  # what `aws_analyzer.<module>` can load
    assert listed and set(re.findall(r'"(\w+)"', listed.group(1))) == modules


def _loaded() -> list[str]:
    return [name for name in sys.modules if name == "aws_analyzer" or name.startswith("aws_analyzer.")]


@pytest.fixture
def package(tmp_path, monkeypatch):
    """aws_analyzer laid out the way the wheel installs it, imported afresh from a temporary folder. The copy the
    other tests imported from src/ is put back afterwards, so their modules and classes stay the ones in use."""
    shutil.copytree(PACKAGE, tmp_path / "aws_analyzer", ignore=shutil.ignore_patterns("__pycache__"))
    saved = {name: sys.modules.pop(name) for name in _loaded()}
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        yield importlib.import_module("aws_analyzer")
    finally:
        for name in _loaded():
            del sys.modules[name]
        sys.modules.update(saved)


def test_package_exports_each_analyzer_and_view(package):
    assert re.fullmatch(r"\d+\.\d+\.\d+([ab]|rc)?\d*", package.__version__)
    assert _loaded() == ["aws_analyzer"]  # nothing else loads until it's used
    for name in package.__all__[1:]:
        value = getattr(package, name)
        assert value.__name__ == name
        assert value.__module__ == f"aws_analyzer.{package._EXPORTS[name]}"
    assert set(package.__all__) <= set(dir(package))
    assert {"s3", "dynamodb", "bedrock_kb", "bedrock_chat", "sagemaker_env", "opensearch", "lambda_functions",
            "s3_explorer"} <= set(dir(package))
    assert package.s3.parse_size("10MB") == 10 * 1024**2
    with pytest.raises(AttributeError, match="no attribute 'nope'"):
        package.nope


def test_changelog_has_an_entry_for_this_version(package):
    # A release (see .claude/skills/release) moves CHANGELOG.md's Unreleased entries under the version it bumps to.
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(r"^## \[Unreleased\]$", changelog, re.M)
    version = re.escape(package.__version__)
    assert re.search(rf"^## \[{version}\] - \d{{4}}-\d{{2}}-\d{{2}}$", changelog, re.M), (
        f"CHANGELOG.md has no '## [{package.__version__}] - <date>' entry; run release.py bump instead of editing "
        "__version__ by hand"
    )
    assert re.search(rf"^\[{version}\]: https://\S+$", changelog, re.M)


def test_explorer_builds_on_the_s3_installed_with_it(package):
    # The explorer finds the s3 module installed next to it, not some other s3 on sys.path.
    assert package.s3_explorer._s3_module() is package.s3
    assert package.S3Explorer.__module__ == "aws_analyzer.s3_explorer"
