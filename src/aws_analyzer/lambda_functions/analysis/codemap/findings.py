"""
What the code map finds wrong, and the one-paragraph summary of what the code does
and returns.
"""

from __future__ import annotations

import ast
import posixpath
import sys
from collections import defaultdict

from ...._kit.fmt import _plural
from ...._kit.text import _clip

from ...helpers import LATEST_RUNTIMES
from .terms import _BATCHES, _LAMBDA_ENV, _REMOVED_MODULES
from .syntax import _a, _const
from .aws_model import _event_kind, _service_name
from .model import AwsCall, CodeMap, CodeSpot, EnvRead
from .mapping import _python_version


_PIP_NAMES = {"yaml": "pyyaml", "PIL": "pillow", "sklearn": "scikit-learn", "cv2": "opencv-python-headless",
              "dateutil": "python-dateutil", "jwt": "PyJWT", "dotenv": "python-dotenv", "bs4": "beautifulsoup4",
              "psycopg2": "psycopg2-binary", "Crypto": "pycryptodome", "jose": "python-jose", "magic": "python-magic"}

_REPLACEMENTS = {"distutils": "setuptools, or shutil and sysconfig", "imp": "importlib", "asyncore": "asyncio",
                 "asynchat": "asyncio", "smtpd": "aiosmtpd", "cgi": "email.message and urllib.parse",
                 "pipes": "shlex.quote", "crypt": "hashlib", "telnetlib": "telnetlib3", "imghdr": "Pillow or filetype",
                 "uu": "base64", "xdrlib": "struct"}


def _short_name(key: str) -> str:
    """
    One of its functions as people say it: 'app.main.handler' -> 'handler', 'app.store.Orders.save' ->
    'Orders.save', 'app.store.Orders.__init__' -> 'Orders' (what you call to run it).
    """
    parts = key.split(".")
    if len(parts) >= 2 and parts[-2][:1].isupper():
        return parts[-2] if parts[-1] == "__init__" else f"{parts[-2]}.{parts[-1]}"
    return parts[-1]


def _where(file: str, line: int, function: str = "") -> str:
    """
    'etl.py line 17', or 'etl.py line 17 in handler()'.
    """
    return f"{file} line {line}" + (f" in {_short_name(function)}()" if function else "")


def _listed(items: list[str], most: int = 3) -> str:
    """
    'a', 'a and b', 'a, b and c', 'a, b, c and 2 more'.
    """
    if len(items) > most:
        return ", ".join(items[:most]) + f" and {len(items) - most} more"
    return " and ".join([", ".join(items[:-1]), items[-1]]) if len(items) > 1 else (items[0] if items else "")


def code_map_findings(cmap: CodeMap) -> list[tuple[str, str]]:
    """
    What's worth fixing in a function's code, from its CodeMap: a handler that isn't there or can't take (event,
    context), environment variables it needs that aren't set, imports Lambda can't find, HTTP requests without a
    timeout, list calls that read only their first page, errors it drops, files written outside /tmp, secrets written
    in the code, AWS calls in a loop that a batch call would do in fewer requests, and clients made on every call.
    """
    found: list[tuple[str, str]] = []
    if cmap.note:
        return found
    fn = cmap.function
    runtime = fn.runtime if fn is not None else None
    version = _python_version(runtime)
    handler_name = (cmap.handler or "").rsplit(".", 1)[-1]
    timeout = f"its {fn.timeout:,} s timeout" if fn is not None else "its timeout"
    spots: dict[str, list[CodeSpot]] = defaultdict(list)
    for spot in cmap.spots:
        spots[spot.kind].append(spot)

    for spot in spots["syntax"]:
        mine = sys.version_info[:2]
        if version and version > mine:
            found.append(("info", (
                f"{spot.file} doesn't parse with this notebook's Python {mine[0]}.{mine[1]} ({spot.detail}): it may use "
                f"syntax only Python {version[0]}.{version[1]} knows, so how_it_works() couldn't follow it. A notebook "
                f"kernel with Python {version[0]}.{version[1]} reads it."
            )))
        else:
            found.append(("warn", (
                f"{spot.file} doesn't parse ({spot.detail}): Lambda can't load it either, so calls that import it fail "
                "with Runtime.UserCodeSyntaxError. Fix that line and deploy again."
            )))
    if cmap.entry is None and cmap.handler_file and not any(s.file == cmap.handler_file for s in spots["syntax"]):
        others = f" ({_listed(cmap.handlers)} take event and context)" if cmap.handlers else ""
        found.append(("warn", (
            f"No function {handler_name}() in {cmap.handler_file}: every call fails with Runtime.HandlerNotFound before "
            f"any of its code runs. Point the handler setting at one of its functions{others}, or add it."
        )))
    elif cmap.entry is not None and cmap.is_async:
        found.append(("warn", (
            f"{handler_name}() is async def: the Python runtime calls it without awaiting it, so its code never runs "
            "and the call returns a coroutine. Wrap it in a plain function that runs it: def handler(event, "
            "context): return asyncio.run(main(event, context))."
        )))
    elif cmap.entry is not None and any(f.name == cmap.entry for f in cmap.functions):  # a function, not an object
        positional = [p for p in cmap.params if not p.startswith("*")]
        required = [p for p in positional if "=" not in p]
        star = any(p.startswith("*") and not p.startswith("**") for p in cmap.params)
        if len(required) > 2 or (len(positional) < 2 and not star):
            found.append(("warn", (
                f"{handler_name}() takes {_plural(len(required), 'argument')}, and Lambda passes two (event and "
                f"context): every call fails with TypeError. Make it def {handler_name}(event, context)."
            )))

    if fn is not None and fn.env_error is None:
        missing: dict[str, list[EnvRead]] = defaultdict(list)
        for read in cmap.env:
            if read.required and read.name not in fn.env_names and read.name not in _LAMBDA_ENV:
                missing[read.name].append(read)
        if missing:
            names = list(missing)
            first = [reads[0] for reads in missing.values()]
            startup = any(r.startup for r in first)
            where = _listed([f"{r.file} line {r.line}" for r in first], 2)
            found.append(("warn", (
                f"{_listed(names)} {'isn' if len(names) == 1 else 'aren'}'t set on {fn.name}, and its code reads "
                f"{'it' if len(names) == 1 else 'them'} with os.environ[...] ({where}): that line fails with "
                f"KeyError: '{names[0]}'" + (", when Lambda loads the code, so every call fails" if startup else "")
                + f". Add {'it' if len(names) == 1 else 'them'} to its environment variables (the console's "
                "Configuration tab, Environment variables)."
            )))

    gone = [i for i in cmap.imports if i.source == "missing" and not i.optional]
    if gone:
        tops = list(dict.fromkeys(i.top for i in gone))
        startup = any(i.startup for i in gone)
        pip = " ".join(_PIP_NAMES.get(top, top) for top in tops[:5])
        found.append(("warn", (
            f"It imports {_listed(tops)}, which {'isn' if len(tops) == 1 else 'aren'}'t in the package, Python's "
            f"standard library or the Lambda runtime ({gone[0].file} line {gone[0].line}): "
            + ("every call fails" if startup else "calls that reach that line fail")
            + f" with Runtime.ImportModuleError: No module named '{tops[0]}'. Add "
            f"{'it' if len(tops) == 1 else 'them'} to the package (pip install {pip} -t .) or a layer."
        )))
    for i in [i for i in cmap.imports if i.source == "removed"]:
        removed = _REMOVED_MODULES[i.top]
        found.append(("warn", (
            f"It imports {i.top} ({i.file} line {i.line}), which Python {removed[0]}.{removed[1]} removed: on "
            f"{runtime} that fails with Runtime.ImportModuleError. Use {_REPLACEMENTS.get(i.top, 'a replacement')} "
            "instead."
        )))
    family_latest = LATEST_RUNTIMES["python"]
    latest = _python_version(family_latest)
    later = [i for i in cmap.imports if i.source == "stdlib" and i.top in _REMOVED_MODULES and latest
             and _REMOVED_MODULES[i.top] <= latest]
    if later:
        tops = list(dict.fromkeys(i.top for i in later))
        removed = _REMOVED_MODULES[tops[0]]
        swaps = _listed([f"{top} by {_REPLACEMENTS.get(top, 'a replacement')}" for top in tops], 2)
        found.append(("info", (
            f"It imports {_listed(tops)}, which Python {removed[0]}.{removed[1]} removed: moving it to "
            f"{family_latest} breaks it with Runtime.ImportModuleError until "
            f"{'that is' if len(tops) == 1 else 'they are'} replaced ({swaps})."
        )))
    layered = [i for i in cmap.imports if i.source == "layer" and not i.optional]
    if layered and fn is not None:
        tops = list(dict.fromkeys(i.top for i in layered))
        found.append(("info", (
            f"{_listed(tops)} {'isn' if len(tops) == 1 else 'aren'}'t in the package, so "
            f"{'it has' if len(tops) == 1 else 'they have'} to come from its layers "
            f"({_listed([layer.name for layer in fn.layers], 2)}): if no layer has "
            f"{'it' if len(tops) == 1 else 'them'}, calls fail with Runtime.ImportModuleError."
        )))

    slow = [s for s in spots["http"] if not s.ok]
    if slow:
        found.append(("warn", (
            f"{_plural(len(slow), 'HTTP request')} {'has' if len(slow) == 1 else 'have'} no timeout "
            f"({_listed([f'{s.detail} at {_where(s.file, s.line)}' for s in slow], 2)}): if the server doesn't "
            f"answer, the run waits until Lambda stops it at {timeout}, and you pay for the wait. Pass timeout=10 "
            "(or what the call needs)."
        )))
    for call in cmap.aws:
        if call.pages == "one" and call.operation.startswith(("List", "Scan", "Query")) and not call.guessed:
            first = {"ListObjectsV2": "1,000 keys", "ListObjects": "1,000 keys", "Scan": "1 MB", "Query": "1 MB"}.get(
                call.operation, "the first page")
            fix = ("loop while the answer has LastEvaluatedKey, passing it back as ExclusiveStartKey"
                   if call.service == "dynamodb" else
                   f"use get_paginator('{_snake(call.operation)}').paginate(...) on its client")
            found.append(("warn", (
                f"{call.label} at {_where(call.file, call.line)} reads only its first page: past {first} the rest is "
                f"left out without an error. To read every page, {fix}."
            )))
    for spot in spots["swallow"]:
        found.append(("warn", (
            f"Errors are dropped at {_where(spot.file, spot.line, spot.function)} ({spot.code}): a run that failed "
            "reports success, so Lambda doesn't retry it, CloudWatch doesn't count the error, and its event is lost. "
            "Log it (logger.exception(...)) and raise it again, or report the failed item."
        )))
    for spot in [s for s in spots["write"] if not s.ok]:
        name = posixpath.basename(spot.detail.rstrip("/")) or "out"
        found.append(("warn", (
            f"It writes {spot.detail} at {_where(spot.file, spot.line)}, outside /tmp: Lambda's disk is read-only "
            f"there, so that line fails with OSError: [Errno 30] Read-only file system. Write under /tmp instead "
            f"(/tmp/{name})."
        )))
    secrets = spots["secret"]
    if secrets:
        where = _listed([f"{s.detail} at {s.file} line {s.line}" for s in secrets], 2)
        found.append(("warn", (
            f"Secret-looking values are written in the code ({where}; "
            "the values aren't shown here): anyone allowed lambda:GetFunction can download the package and read them. "
            "Keep them in Secrets Manager or Parameter Store, and read them when the code starts."
        )))

    batched = [c for c in cmap.aws if c.loop and (c.service, c.operation) in _BATCHES and not c.guessed]
    for call in list({(c.service, c.operation): c for c in batched}.values()):
        alternative, size = _BATCHES[(call.service, call.operation)]
        found.append(("info", (
            f"{call.label} runs once per item in {call.loop.rstrip(':')} ({_where(call.file, call.line)}): a request "
            f"for each. {alternative} sends up to {size:,} at a time, so a run takes fewer round trips."
        )))
    for spot in [s for s in spots["client"] if not s.ok]:
        service = _service_name(spot.detail) if spot.detail != "?" else "AWS"
        found.append(("info", (
            f"It makes a new {service} client on every call, at {_where(spot.file, spot.line, spot.function)}"
            + (" inside a loop" if spot.loop else "")
            + ": making one takes time, more on a cold start. Make it once at module level (above the handler), "
            "so warm calls reuse it."
        )))
    for spot in spots["sleep"]:
        if spot.startup:
            continue
        found.append(("info", (
            f"It sleeps at {_where(spot.file, spot.line, spot.function)} ({spot.code}): the function is billed for "
            "the time it waits. If it waits for something to finish, a Step Functions Wait state or an SQS delay "
            "costs nothing while it waits."
        )))
    return found


def _snake(operation: str) -> str:
    from botocore import xform_name

    return xform_name(operation)


def _code_summary(cmap: CodeMap) -> list[str]:
    """
    How the code works in a few sentences: what it does when Lambda starts it, then what each call reads from the
    event, which AWS services it reaches and how, its own functions it calls, and what it returns.
    """
    out = []
    file = cmap.handler_file or ""
    early = []
    names = list(dict.fromkeys(_service_name(s.detail) for s in cmap.spots
                               if s.kind == "client" and s.startup and s.detail != "?"))
    if names:
        early.append(f"makes {_a(names[0])} {names[0]} client" if len(names) == 1 else f"makes {_listed(names)} clients")
    env = list(dict.fromkeys(e.name for e in cmap.env if e.startup))
    if env:
        early.append(f"reads {_listed(env)} from its environment")
    early += _aws_phrase([c for c in cmap.aws if c.startup])
    if early:
        out.append(f"When Lambda starts it (once per cold start), the code {_listed(early, 4)}.")
    if cmap.entry is None:
        return out
    entry = cmap.entry.rsplit(".", 1)[-1]
    out.append(f"Each call runs {entry}() in {file}.")
    labels = [f.label for f in cmap.event]
    leaves = [label for label in labels if not any(
        other.startswith((label + "[", label + ".", label + " (")) for other in labels)]
    if leaves:
        kind = _event_kind(cmap.event)
        out.append(f"It reads {_listed(leaves, 4)} from the event" + (f", which looks like {kind}" if kind else "") + ".")
    phrase = _aws_phrase([c for c in cmap.aws if not c.startup])
    http = [s for s in cmap.spots if s.kind == "http" and not s.startup]
    more = list(phrase)
    if http:
        more.append(f"sends {_plural(len(http), 'HTTP request')}")
    if cmap.routes:
        more.append(f"answers {_plural(len(cmap.routes), 'web route')} ({_listed([f'{m} {p}' for m, p, _ in cmap.routes])})")
    if more:
        out.append(f"It {_listed(more, 6) if len(more) < 3 else '; '.join(more[:-1]) + '; and ' + more[-1]}.")
    called = [f for f in cmap.functions if f.depth == 1]
    returns = list(dict.fromkeys(code for code, _, _ in cmap.returns))
    last = []
    if called:
        last.append("calls " + _listed([f"{_short_name(f.name)}()" + (
            f" from {f.file}" if f.file != file else "") for f in called], 4))
    if returns:
        last.append(f"returns {_returned(returns[0])}" + (f" (or {_plural(len(returns) - 1, 'other value')})"
                                                          if len(returns) > 1 else ""))
    if last:
        out.append(f"It {' and '.join(last)}.")
    if not (leaves or more or last):
        out.append("It does nothing this can follow: no AWS calls, own functions, HTTP requests or event fields.")
    return out


def _returned(code: str) -> str:
    """
    What a return statement gives back, said shortly: "{'statusCode': 200, 'body': ...}" -> 'a dict with
    statusCode and body'; short code as it is.
    """
    try:
        node = ast.parse(code, mode="eval").body
    except SyntaxError:
        return _clip(code, 60)
    if isinstance(node, ast.Dict) and node.keys and all(isinstance(_const(k), str) for k in node.keys):
        keys = [str(_const(k)) for k in node.keys]
        return f"a dict with {_listed(keys, 4)}" if len(code) > 50 else code
    return _clip(code, 60)


def _aws_phrase(calls: list[AwsCall]) -> list[str]:
    """
    ['reads from S3 (GetObject) and SQS (ListQueues)', 'writes to DynamoDB (PutItem)'].
    """
    grouped: dict[str, dict[str, list[AwsCall]]] = {}
    for call in calls:
        grouped.setdefault(call.access, {}).setdefault(call.service, []).append(call)
    verbs = {"reads": "reads from", "writes": "writes to", "sends": "sends to", "runs": "calls",
             "signs": "signs links for"}
    parts = []
    for access, services in grouped.items():
        each = []
        for service, group in services.items():
            ops = list(dict.fromkeys(c.operation for c in group))
            each.append(f"{_service_name(service)} ({', '.join(ops[:3])}{', …' if len(ops) > 3 else ''})")
        parts.append(f"{verbs[access]} {_listed(each, 4)}")
    return parts
