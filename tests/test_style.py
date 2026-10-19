"""
The code style rules in CLAUDE.md that ruff can't check: every docstring has its quotes on their own lines, and no
file gains `#` comments.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKED_FOLDERS = ["src", "tests", ".claude/skills"]

ALLOWED_COMMENT = re.compile(r"#(!| =+$| \d+\. | read-only:| noqa\b| type: ignore| pragma: )")

COMMENTS_LEFT = {
    ".claude/skills/check/rules.py": 33,
    ".claude/skills/check/run.py": 6,
    ".claude/skills/check/snapshot.py": 23,
    ".claude/skills/demo/chat_shots.py": 25,
    ".claude/skills/demo/demo.py": 61,
    ".claude/skills/demo/explorer_shots.py": 17,
    ".claude/skills/demo/kb_explorer_shots.py": 5,
    ".claude/skills/demo/lambda_explorer_shots.py": 5,
    ".claude/skills/demo/shots.py": 23,
    ".claude/skills/release/release.py": 4,
    "src/aws_analyzer/__init__.py": 3,
    "src/aws_analyzer/bedrock_chat/analysis/batches.py": 26,
    "src/aws_analyzer/bedrock_chat/analysis/code.py": 2,
    "src/aws_analyzer/bedrock_chat/analysis/filters.py": 2,
    "src/aws_analyzer/bedrock_chat/analysis/findings.py": 1,
    "src/aws_analyzer/bedrock_chat/analysis/parsing.py": 1,
    "src/aws_analyzer/bedrock_chat/analysis/ranking.py": 10,
    "src/aws_analyzer/bedrock_chat/analysis/request.py": 4,
    "src/aws_analyzer/bedrock_chat/analysis/schema.py": 22,
    "src/aws_analyzer/bedrock_chat/analysis/sweeps.py": 5,
    "src/aws_analyzer/bedrock_chat/analyzer.py": 23,
    "src/aws_analyzer/bedrock_chat/blocks.py": 13,
    "src/aws_analyzer/bedrock_chat/helpers.py": 11,
    "src/aws_analyzer/bedrock_chat/models.py": 31,
    "src/aws_analyzer/bedrock_chat/style.py": 7,
    "src/aws_analyzer/bedrock_chat/view/chat.py": 1,
    "src/aws_analyzer/bedrock_chat/view/chat_view.py": 20,
    "src/aws_analyzer/bedrock_chat/view/knowledge_bases.py": 2,
    "src/aws_analyzer/bedrock_chat/view/runs.py": 3,
    "src/aws_analyzer/bedrock_chat/view/settings.py": 2,
    "src/aws_analyzer/bedrock_chat/view/tests.py": 4,
    "src/aws_analyzer/bedrock_chat/window/app.py": 40,
    "src/aws_analyzer/bedrock_chat/window/conversation.py": 4,
    "src/aws_analyzer/bedrock_chat/window/pickers.py": 6,
    "src/aws_analyzer/bedrock_chat/window/request.py": 4,
    "src/aws_analyzer/bedrock_chat/window/runs.py": 3,
    "src/aws_analyzer/bedrock_chat/window/settings.py": 5,
    "src/aws_analyzer/bedrock_chat/window/test.py": 13,
    "src/aws_analyzer/bedrock_chat/window/widgets.py": 13,
    "src/aws_analyzer/bedrock_kb/analysis/chunks.py": 2,
    "src/aws_analyzer/bedrock_kb/analysis/cost.py": 1,
    "src/aws_analyzer/bedrock_kb/analysis/deciding.py": 1,
    "src/aws_analyzer/bedrock_kb/analysis/files.py": 7,
    "src/aws_analyzer/bedrock_kb/analysis/filters.py": 1,
    "src/aws_analyzer/bedrock_kb/analysis/findings.py": 3,
    "src/aws_analyzer/bedrock_kb/analysis/metadata.py": 2,
    "src/aws_analyzer/bedrock_kb/analysis/parsing.py": 2,
    "src/aws_analyzer/bedrock_kb/analysis/prompting.py": 1,
    "src/aws_analyzer/bedrock_kb/analysis/settings.py": 1,
    "src/aws_analyzer/bedrock_kb/analyzer/deciding.py": 1,
    "src/aws_analyzer/bedrock_kb/analyzer/files.py": 3,
    "src/aws_analyzer/bedrock_kb/analyzer/kb_analyzer.py": 6,
    "src/aws_analyzer/bedrock_kb/analyzer/knowledge_bases.py": 6,
    "src/aws_analyzer/bedrock_kb/analyzer/search.py": 5,
    "src/aws_analyzer/bedrock_kb/blocks.py": 16,
    "src/aws_analyzer/bedrock_kb/explorer/frame.py": 12,
    "src/aws_analyzer/bedrock_kb/explorer/widgets.py": 1,
    "src/aws_analyzer/bedrock_kb/explorer/window.py": 30,
    "src/aws_analyzer/bedrock_kb/helpers.py": 7,
    "src/aws_analyzer/bedrock_kb/models.py": 101,
    "src/aws_analyzer/bedrock_kb/style.py": 2,
    "src/aws_analyzer/bedrock_kb/view/indexed.py": 5,
    "src/aws_analyzer/bedrock_kb/view/kb_view.py": 8,
    "src/aws_analyzer/bedrock_kb/view/knowledge_bases.py": 4,
    "src/aws_analyzer/bedrock_kb/view/measure.py": 1,
    "src/aws_analyzer/bedrock_kb/view/search.py": 4,
    "src/aws_analyzer/dynamodb/analysis.py": 7,
    "src/aws_analyzer/dynamodb/analyzer.py": 10,
    "src/aws_analyzer/dynamodb/helpers.py": 5,
    "src/aws_analyzer/dynamodb/models.py": 40,
    "src/aws_analyzer/dynamodb/prices.py": 9,
    "src/aws_analyzer/dynamodb/style.py": 2,
    "src/aws_analyzer/dynamodb/view.py": 9,
    "src/aws_analyzer/lambda_functions/analysis/codemap/findings.py": 1,
    "src/aws_analyzer/lambda_functions/analysis/codemap/graph.py": 33,
    "src/aws_analyzer/lambda_functions/analysis/codemap/mapping.py": 4,
    "src/aws_analyzer/lambda_functions/analysis/codemap/model.py": 56,
    "src/aws_analyzer/lambda_functions/analysis/codemap/reader.py": 30,
    "src/aws_analyzer/lambda_functions/analysis/codemap/syntax.py": 1,
    "src/aws_analyzer/lambda_functions/analysis/codemap/terms.py": 22,
    "src/aws_analyzer/lambda_functions/analysis/errors.py": 4,
    "src/aws_analyzer/lambda_functions/analysis/findings.py": 2,
    "src/aws_analyzer/lambda_functions/analysis/functions.py": 4,
    "src/aws_analyzer/lambda_functions/analysis/logs.py": 15,
    "src/aws_analyzer/lambda_functions/analysis/overview.py": 9,
    "src/aws_analyzer/lambda_functions/analysis/package.py": 3,
    "src/aws_analyzer/lambda_functions/analysis/performance.py": 1,
    "src/aws_analyzer/lambda_functions/analyzer.py": 31,
    "src/aws_analyzer/lambda_functions/blocks/graph.py": 50,
    "src/aws_analyzer/lambda_functions/blocks/outline.py": 3,
    "src/aws_analyzer/lambda_functions/blocks/wiring.py": 8,
    "src/aws_analyzer/lambda_functions/explorer/code.py": 14,
    "src/aws_analyzer/lambda_functions/explorer/errors.py": 1,
    "src/aws_analyzer/lambda_functions/explorer/frame.py": 13,
    "src/aws_analyzer/lambda_functions/explorer/function.py": 6,
    "src/aws_analyzer/lambda_functions/explorer/functions.py": 5,
    "src/aws_analyzer/lambda_functions/explorer/logs.py": 16,
    "src/aws_analyzer/lambda_functions/explorer/widgets.py": 1,
    "src/aws_analyzer/lambda_functions/explorer/window.py": 38,
    "src/aws_analyzer/lambda_functions/helpers.py": 17,
    "src/aws_analyzer/lambda_functions/models.py": 97,
    "src/aws_analyzer/lambda_functions/prices.py": 14,
    "src/aws_analyzer/lambda_functions/style.py": 2,
    "src/aws_analyzer/lambda_functions/view.py": 20,
    "src/aws_analyzer/opensearch/analysis/parsing.py": 9,
    "src/aws_analyzer/opensearch/analyzer.py": 14,
    "src/aws_analyzer/opensearch/embeddings.py": 1,
    "src/aws_analyzer/opensearch/http.py": 1,
    "src/aws_analyzer/opensearch/models.py": 88,
    "src/aws_analyzer/opensearch/prices.py": 25,
    "src/aws_analyzer/opensearch/style.py": 2,
    "src/aws_analyzer/opensearch/view.py": 13,
    "src/aws_analyzer/s3/analysis/bucket.py": 1,
    "src/aws_analyzer/s3/analysis/cost.py": 3,
    "src/aws_analyzer/s3/analysis/details.py": 22,
    "src/aws_analyzer/s3/analysis/documents.py": 16,
    "src/aws_analyzer/s3/analysis/downloads.py": 16,
    "src/aws_analyzer/s3/analysis/duplicates.py": 4,
    "src/aws_analyzer/s3/analysis/lifecycle.py": 1,
    "src/aws_analyzer/s3/analysis/notifications.py": 7,
    "src/aws_analyzer/s3/analysis/policy.py": 2,
    "src/aws_analyzer/s3/analysis/summary.py": 3,
    "src/aws_analyzer/s3/analyzer/buckets.py": 2,
    "src/aws_analyzer/s3/analyzer/downloads.py": 15,
    "src/aws_analyzer/s3/analyzer/files.py": 9,
    "src/aws_analyzer/s3/analyzer/listing.py": 10,
    "src/aws_analyzer/s3/analyzer/preview.py": 12,
    "src/aws_analyzer/s3/analyzer/s3_analyzer.py": 2,
    "src/aws_analyzer/s3/analyzer/streams.py": 4,
    "src/aws_analyzer/s3/blocks.py": 2,
    "src/aws_analyzer/s3/explorer/frame.py": 2,
    "src/aws_analyzer/s3/explorer/listing.py": 14,
    "src/aws_analyzer/s3/explorer/navigator.py": 32,
    "src/aws_analyzer/s3/explorer/report.py": 14,
    "src/aws_analyzer/s3/explorer/selection.py": 2,
    "src/aws_analyzer/s3/explorer/window.py": 54,
    "src/aws_analyzer/s3/helpers.py": 3,
    "src/aws_analyzer/s3/models.py": 101,
    "src/aws_analyzer/s3/prices.py": 11,
    "src/aws_analyzer/s3/readers/avro.py": 5,
    "src/aws_analyzer/s3/readers/data.py": 10,
    "src/aws_analyzer/s3/readers/formats.py": 6,
    "src/aws_analyzer/s3/readers/media.py": 7,
    "src/aws_analyzer/s3/readers/office.py": 12,
    "src/aws_analyzer/s3/readers/pdf.py": 12,
    "src/aws_analyzer/s3/style.py": 3,
    "src/aws_analyzer/s3/view/buckets.py": 2,
    "src/aws_analyzer/s3/view/downloads.py": 2,
    "src/aws_analyzer/s3/view/files.py": 7,
    "src/aws_analyzer/s3/view/folders.py": 3,
    "src/aws_analyzer/s3/view/s3_view.py": 5,
    "src/aws_analyzer/sagemaker_env/analysis/common.py": 2,
    "src/aws_analyzer/sagemaker_env/analysis/disk.py": 4,
    "src/aws_analyzer/sagemaker_env/analysis/envs.py": 11,
    "src/aws_analyzer/sagemaker_env/analysis/machine.py": 2,
    "src/aws_analyzer/sagemaker_env/analysis/notebooks.py": 3,
    "src/aws_analyzer/sagemaker_env/analysis/running.py": 1,
    "src/aws_analyzer/sagemaker_env/analyzer.py": 8,
    "src/aws_analyzer/sagemaker_env/envs.py": 9,
    "src/aws_analyzer/sagemaker_env/helpers.py": 1,
    "src/aws_analyzer/sagemaker_env/machine.py": 7,
    "src/aws_analyzer/sagemaker_env/models.py": 100,
    "src/aws_analyzer/sagemaker_env/prices.py": 16,
    "src/aws_analyzer/sagemaker_env/style.py": 2,
    "src/aws_analyzer/sagemaker_env/view.py": 10,
    "tests/conftest.py": 5,
    "tests/fake_opensearch.py": 11,
    "tests/test_agent.py": 34,
    "tests/test_bedrock_chat.py": 198,
    "tests/test_bedrock_kb.py": 112,
    "tests/test_dynamodb.py": 22,
    "tests/test_kit.py": 28,
    "tests/test_lambda_functions.py": 112,
    "tests/test_opensearch.py": 20,
    "tests/test_package.py": 10,
    "tests/test_s3.py": 187,
    "tests/test_s3_explorer.py": 117,
    "tests/test_sagemaker_env.py": 36,
}


MAX_MODULE_LINES = 1500
PACKAGE = ROOT / "src" / "aws_analyzer"
LONG_FILES: set[str] = set()


def _python_files() -> list[Path]:
    """
    Every Python file the style rules cover.
    """
    return sorted(path for folder in CHECKED_FOLDERS for path in (ROOT / folder).rglob("*.py")
                  if "__pycache__" not in path.parts)


def _docstring_owners(tree: ast.Module) -> list[ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef]:
    """
    The module and each class and function in it.
    """
    return [tree] + [node for node in ast.walk(tree)
                     if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]


def _cramped_docstrings(path: Path) -> list[int]:
    """
    The lines of the file's docstrings that have text on the line of their opening or closing quotes.
    """
    source = path.read_text()
    tree = ast.parse(source)
    cramped = []
    for owner in _docstring_owners(tree):
        if ast.get_docstring(owner, clean=False) is None:
            continue
        string = owner.body[0].value
        written = ast.get_source_segment(source, string) or ""
        closing = "\n" + " " * string.col_offset + '"""'
        if not (written.lstrip("rR").startswith('"""\n') and written.endswith(closing)):
            cramped.append(string.lineno)
    return cramped


def _comment_count(path: Path) -> int:
    """
    How many `#` comments the file has, leaving out the ones CLAUDE.md allows.
    """
    tokens = tokenize.generate_tokens(io.StringIO(path.read_text()).readline)
    return sum(1 for token in tokens
               if token.type == tokenize.COMMENT and not ALLOWED_COMMENT.match(token.string))


def test_docstrings_have_their_quotes_on_their_own_lines():
    cramped = [f"{path.relative_to(ROOT)}:{line}" for path in _python_files() for line in _cramped_docstrings(path)]
    assert not cramped, (
        'Write each docstring as """, its text, then """ on its own line (see "Code style" in CLAUDE.md): '
        + ", ".join(cramped))


def test_no_file_gains_comments():
    counts = {str(path.relative_to(ROOT)): _comment_count(path) for path in _python_files()}
    gained = {name: count for name, count in counts.items() if count > COMMENTS_LEFT.get(name, 0)}
    assert not gained, (
        "Say it in a name or a docstring instead of a # comment (see \"Code style\" in CLAUDE.md). "
        + ", ".join(f"{name} has {count}, at most {COMMENTS_LEFT.get(name, 0)}" for name, count in gained.items()))


def test_comment_allowance_shrinks_with_the_code():
    counts = {str(path.relative_to(ROOT)): _comment_count(path) for path in _python_files()}
    loose = {name: (allowed, counts.get(name, 0)) for name, allowed in COMMENTS_LEFT.items()
             if counts.get(name, 0) < allowed}
    assert not loose, (
        "Comments were removed: lower COMMENTS_LEFT in tests/test_style.py to match, so they can't come back. "
        + ", ".join(f"{name}: {allowed} -> {count}" for name, (allowed, count) in loose.items()))


def _module_lengths() -> dict[str, int]:
    """
    How many lines each module of the package has, by its path from the repository's root.
    """
    return {str(path.relative_to(ROOT)): len(path.read_text().splitlines()) for path in sorted(PACKAGE.rglob("*.py"))
            if "__pycache__" not in path.parts}


def test_no_module_runs_past_the_limit():
    lengths = _module_lengths()
    long = {name: count for name, count in lengths.items() if count > MAX_MODULE_LINES and name not in LONG_FILES}
    assert not long, (
        f"A module of the package stays under {MAX_MODULE_LINES} lines: split it by what its parts do (see "
        ".claude/skills/modularize). " + ", ".join(f"{name} has {count}" for name, count in long.items()))


def test_long_files_list_shrinks_with_the_code():
    lengths = _module_lengths()
    done = sorted(name for name in LONG_FILES if lengths.get(name, 0) <= MAX_MODULE_LINES)
    assert not done, (
        "These are gone or now fit the limit: take them off LONG_FILES in tests/test_style.py, so they can't grow "
        "back. " + ", ".join(done))
