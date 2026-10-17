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
    ".claude/skills/check/rules.py": 36,
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
    "src/aws_analyzer/_bedrock.py": 83,
    "src/aws_analyzer/_kit/analyzer.py": 7,
    "src/aws_analyzer/_kit/blocks.py": 28,
    "src/aws_analyzer/_kit/fmt.py": 11,
    "src/aws_analyzer/_kit/jsontree.py": 25,
    "src/aws_analyzer/_kit/progress.py": 1,
    "src/aws_analyzer/_kit/render.py": 26,
    "src/aws_analyzer/_kit/view.py": 21,
    "src/aws_analyzer/_kit/window.py": 17,
    "src/aws_analyzer/agent/analyzer.py": 10,
    "src/aws_analyzer/agent/answers.py": 18,
    "src/aws_analyzer/agent/budget.py": 10,
    "src/aws_analyzer/agent/guard.py": 7,
    "src/aws_analyzer/agent/harness.py": 7,
    "src/aws_analyzer/agent/llm.py": 2,
    "src/aws_analyzer/agent/prompt.py": 1,
    "src/aws_analyzer/agent/results.py": 3,
    "src/aws_analyzer/agent/runtime.py": 5,
    "src/aws_analyzer/agent/toolkits/__init__.py": 2,
    "src/aws_analyzer/agent/toolkits/base.py": 14,
    "src/aws_analyzer/agent/toolkits/lambda_tools.py": 10,
    "src/aws_analyzer/agent/toolkits/s3_tools.py": 36,
    "src/aws_analyzer/agent/view.py": 8,
    "src/aws_analyzer/bedrock_chat.py": 281,
    "src/aws_analyzer/bedrock_kb.py": 235,
    "src/aws_analyzer/dynamodb.py": 82,
    "src/aws_analyzer/lambda_functions.py": 522,
    "src/aws_analyzer/opensearch.py": 153,
    "src/aws_analyzer/s3.py": 320,
    "src/aws_analyzer/s3_explorer.py": 118,
    "src/aws_analyzer/sagemaker_env.py": 176,
    "tests/conftest.py": 5,
    "tests/fake_opensearch.py": 11,
    "tests/test_agent.py": 34,
    "tests/test_bedrock_chat.py": 199,
    "tests/test_bedrock_kb.py": 112,
    "tests/test_dynamodb.py": 22,
    "tests/test_kit.py": 28,
    "tests/test_lambda_functions.py": 112,
    "tests/test_opensearch.py": 20,
    "tests/test_package.py": 10,
    "tests/test_s3.py": 187,
    "tests/test_s3_explorer.py": 120,
    "tests/test_sagemaker_env.py": 36,
}


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
