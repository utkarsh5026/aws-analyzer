"""
Small readings of Python syntax the code reader shares: names, constants, docstrings,
the lines of a definition, and secrets written into the code.
"""

from __future__ import annotations

import ast
import copy
import re
from typing import Any, Iterable

from ...._kit.text import _clip

from .terms import _AWS_KEY_RE, _NOT_SECRET_NAME_RE, _PLACEHOLDERS, _SECRET_NAME_RE


def _secret_spots(node: Any) -> list[tuple[str, Any]]:
    """
    The secret-looking literals in a piece of code, with the name each is given to: a password=, token= or key given
    as text (in an assignment, a call or a dict), and any AWS access key.
    """
    found: list[tuple[str, Any]] = []
    for n in ast.walk(node):
        pairs: list[tuple[Any, Any]] = []
        if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None:
            for target in n.targets if isinstance(n, ast.Assign) else [n.target]:
                pairs.append((target.id if isinstance(target, ast.Name) else target.attr
                              if isinstance(target, ast.Attribute) else _const(target.slice)
                              if isinstance(target, ast.Subscript) else None, n.value))
        elif isinstance(n, ast.Call):
            pairs += [(kw.arg, kw.value) for kw in n.keywords if kw.arg]
        elif isinstance(n, ast.Dict):
            pairs += [(_const(k), v) for k, v in zip(n.keys, n.values) if k is not None]
        for name, value in pairs:
            text = _const(value)
            if (isinstance(name, str) and isinstance(text, str) and _SECRET_NAME_RE.search(name)
                    and not _NOT_SECRET_NAME_RE.search(name) and _secret_value(text)):
                found.append((name, value))
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and _AWS_KEY_RE.search(n.value):
            found.append(("an AWS access key", n))
    return found


class _Hide(ast.NodeTransformer):
    """
    Puts '…' in place of the literals at these places (line, column).
    """

    def __init__(self, places: set[tuple[int, int]]):
        self.places = places

    def visit_Constant(self, node: ast.Constant) -> Any:
        if (getattr(node, "lineno", 0), getattr(node, "col_offset", 0)) in self.places:
            return ast.copy_location(ast.Constant("…"), node)
        return node


def _unparse(node: Any, width: int = 140) -> str:
    """
    A piece of code on one line, as Python writes it back from the tree: "s3.get_object(Bucket=b, Key=k)". A
    secret-looking literal in it shows as '…'.
    """
    try:
        if isinstance(node, ast.AST) and not isinstance(node, (ast.arguments, ast.pattern)):
            places = {(getattr(v, "lineno", 0), getattr(v, "col_offset", 0)) for _, v in _secret_spots(node)}
            if places:
                node = _Hide(places).visit(copy.deepcopy(node))
        text = ast.unparse(node)
    except (AttributeError, ValueError, TypeError, RecursionError):
        return "…"
    return _clip(" ".join(text.split()), width)


def _target(node: Any) -> str:
    """
    A loop's target as written: 'i, record', not '(i, record)'.
    """
    if isinstance(node, ast.Tuple) and node.elts:
        return ", ".join(_unparse(elt, 30) for elt in node.elts)
    return _unparse(node, 40)


def _a(name: str) -> str:
    """
    'a' or 'an' before a service's name, as it's said: an S3, an SQS, a DynamoDB, an EventBridge, a KMS.
    """
    if re.match(r"[A-Z][A-Z0-9]", name):  # said letter by letter
        return "an" if name[0] in "AEFHILMNORSX" else "a"
    return "an" if name[:1].lower() in "aeiou" else "a"


def _const(node: Any) -> Any:
    return node.value if isinstance(node, ast.Constant) else None


def _names_in(node: Any, names: Iterable[str]) -> bool:
    """
    Whether an `except` catches one of these exceptions: except ImportError, except (KeyError, Exception).
    """
    if node is None:
        return False
    if isinstance(node, ast.Tuple):
        return any(_names_in(elt, names) for elt in node.elts)
    return (node.id if isinstance(node, ast.Name) else getattr(node, "attr", "")) in set(names)


def _swallows(handler: ast.ExceptHandler) -> bool:
    """
    An `except` that catches every error and does nothing with it: no log line, no raise, nothing returned.
    """
    if not (handler.type is None or _names_in(handler.type, ("Exception", "BaseException"))):
        return False
    for stmt in handler.body:
        if isinstance(stmt, (ast.Pass, ast.Continue, ast.Break)):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue
        if isinstance(stmt, ast.Return) and (stmt.value is None or isinstance(stmt.value, ast.Constant)):
            continue
        return False
    return True


def _is_main_guard(test: Any) -> bool:
    """
    `if __name__ == "__main__":`, which never runs in Lambda.
    """
    return (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == "__name__"
            and any(_const(c) == "__main__" for c in test.comparators))


def _doc_line(node: Any) -> str:
    doc = ast.get_docstring(node) or ""
    return _clip(doc.strip().split("\n\n")[0].replace("\n", " ").strip(), 160)


def _def_line(node: Any) -> str:
    """
    'def connect(host, password):'.
    """
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({_unparse(node.args, 100)}):"


def _secret_value(value: str) -> bool:
    """
    Whether a literal looks like a real secret rather than a name, a path or a placeholder.
    """
    text = value.strip()
    return (len(text) >= 6 and not re.search(r"\s", text) and not re.fullmatch(r"[A-Z0-9_]+", text)
            and not text.startswith(("arn:", "/", "{", "$", "<", "%", "http", "s3://", "projects/"))
            and text.lower() not in _PLACEHOLDERS and "xxx" not in text.lower() and len(set(text)) > 2
            and not text.lower().startswith("your"))
