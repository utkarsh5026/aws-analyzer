"""
A deployment package: its handler's file, the files in it, and what's wrong with it.
"""

from __future__ import annotations

import difflib
import fnmatch
import io
import os
import posixpath
import re
import zipfile
from collections import Counter
from typing import Iterable

from ..._kit.fmt import human_size

from ..helpers import KB, MB, UNZIPPED_LIMIT, runtime_family
from ..models import CodeFile, CodePackage, Function
from .text import _cli


_HANDLER_EXTENSIONS = {"python": (".py",), "nodejs": (".js", ".mjs", ".cjs"), "ruby": (".rb",)}

_SOURCE_LIMIT = 200 * KB  # the most of one file code() reads and shows

DOWNLOADS = "lambda-downloads"  # where download() saves a package without a path=: this folder, next to the notebook

_DOWNLOADS_MARK = (  # the .gitignore in a downloads folder download() makes: a package can hold secrets (.env, keys)
    "# Made by aws-analyzer for the deployment packages download() saves from Lambda.\n"
    "# A package can hold secrets, so this file keeps them out of git.\n"
    "*\n"
)

_SECRET_FILE_RE = re.compile(
    r"(^|/)(\.env(\.[\w-]+)?|credentials|[\w.-]+\.pem|id_rsa|id_ed25519|\.npmrc|\.pypirc|\.netrc)$", re.I
)

_JUNK_RE = re.compile(r"(^|/)(__pycache__|\.git|\.pytest_cache|tests?|\.venv|\.idea|\.vscode|node_modules/\.cache)/")


def handler_file(handler: str | None, runtime: str | None, names: Iterable[str]) -> str | None:
    """
    The file in a package that Lambda loads the handler from: 'app.handler' -> 'app.py',
    'pkg.module.handler' -> 'pkg/module.py', 'src/index.handler' -> 'src/index.mjs'. None when the runtime loads
    compiled code (Java, .NET, Go) or no file matches.
    """
    family = runtime_family(runtime)
    if not handler or family not in _HANDLER_EXTENSIONS:
        return None
    names = set(names)
    module = handler.rsplit(".", 1)[0] if "." in handler else handler
    stem = module.replace(".", "/") if family == "python" else module
    for extension in _HANDLER_EXTENSIONS[family]:
        if stem + extension in names:
            return stem + extension
    for extension in _HANDLER_EXTENSIONS[family]:
        nested = sorted(n for n in names if n.endswith("/" + stem + extension))
        if nested:
            return nested[0]
    return None


def package_file_name(fn: Function) -> str:
    """
    The name download() saves a function's package as: 'etl.zip', or 'etl-v3.zip' for a published version (an
    alias names the version it points to).
    """
    return f"{fn.name}.zip" if fn.version in ("", "$LATEST") else f"{fn.name}-v{fn.version}.zip"


def _local_path(path: str) -> str:
    """
    A path on the notebook's disk as a report shows it: relative to the notebook's folder when it's in it, else
    as it is.
    """
    here = os.path.abspath(os.getcwd())
    path = os.path.abspath(path)
    return os.path.relpath(path, here) if path.startswith(here.rstrip(os.sep) + os.sep) else path


def _top_folder(path: str) -> str:
    head, _, rest = path.partition("/")
    return f"{head}/" if rest else "(top level)"


def _pick_file(wanted: str, names: list[str]) -> str:
    """
    A file in the package from what the user typed: its path, its name, the end of its path, or a glob.
    """
    wanted = wanted.strip().lstrip("/")
    if wanted in names:
        return wanted
    for test in (
        lambda n: n.endswith("/" + wanted),
        lambda n: posixpath.basename(n) == wanted,
        lambda n: fnmatch.fnmatchcase(n, wanted) or fnmatch.fnmatchcase(posixpath.basename(n), wanted),
    ):
        hits = sorted((n for n in names if test(n)), key=lambda n: (n.count("/"), n))
        if hits:
            return hits[0]
    close = difflib.get_close_matches(wanted, names, n=3) or difflib.get_close_matches(
        wanted, [posixpath.basename(n) for n in names], n=3)
    hint = f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
    raise ValueError(f"No file {wanted!r} in the package.{hint} code() without file= lists them")


def read_package(fn: Function, data: bytes | None, file: str | None = None) -> CodePackage:
    """
    A deployment package (the .zip as code() downloads it) as the files in it, the file that holds the handler, and
    the text of that file, or of file= (a path, a file name or a glob like '*.py'). No AWS calls, and nothing in it is
    run. A secrets file (.env, keys, credentials) is named but its text isn't read. data=None means there's nothing to
    read: a container image, or no link to the code (the note says which).
    """
    package = CodePackage(fn)
    if fn.package_type == "Image":
        package.note = (
            f"It's a container image ({fn.image_uri or 'image URI not shown'}): its code is in the image, which "
            "this doesn't pull. docker pull it from ECR to look inside."
        )
        return package
    if data is None:
        package.note = "Lambda gave no link to download its code."
        return package
    package.size = len(data)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError(f"The package ({human_size(len(data))}) isn't a readable .zip file") from None
    with archive:
        entries = [info for info in archive.infolist() if not info.is_dir()]
        package.files = [CodeFile(info.filename, info.file_size, info.compress_size) for info in entries]
        names = [info.filename for info in entries]
        package.handler_file = handler_file(fn.handler, fn.runtime, names)
        wanted = _pick_file(file, names) if file else package.handler_file
        if wanted and _SECRET_FILE_RE.search(wanted):
            package.shown_file = wanted
            package.note = (
                f"{wanted} looks like a secrets file, so its text isn't shown: a notebook's output is easy to "
                f"share. To read it anyway, aws lambda get-function {_cli(fn)} --query Code.Location --output "
                "text prints a link to the package that works for 10 minutes."
            )
        elif wanted:
            with archive.open(wanted) as handle:
                raw = handle.read(_SOURCE_LIMIT + 1)
            package.source_truncated = len(raw) > _SOURCE_LIMIT
            raw = raw[:_SOURCE_LIMIT]
            package.shown_file = wanted
            if b"\x00" in raw[:8192]:
                package.note = f"{wanted} is a binary file, so its text isn't shown."
            else:
                package.source = raw.decode("utf-8", errors="replace")
    return package


def package_findings(pkg: CodePackage) -> list[tuple[str, str]]:
    """
    What's worth knowing about a deployment package: a handler no file matches, secrets packed in it, size near
    Lambda's limit, and what it carries that never runs.
    """
    fn = pkg.function
    found: list[tuple[str, str]] = []
    if not pkg.files:
        return found
    family = runtime_family(fn.runtime)
    if fn.handler and family in _HANDLER_EXTENSIONS and not pkg.handler_file:
        module = fn.handler.rsplit(".", 1)[0]
        expected = (module.replace(".", "/") if family == "python" else module) + _HANDLER_EXTENSIONS[family][0]
        found.append(("warn", (
            f"No file in the package matches its handler {fn.handler} (Lambda looks for {expected}): calls fail "
            "with Runtime.ImportModuleError until the handler setting or the package is fixed."
        )))
    secrets = [f.path for f in pkg.files if _SECRET_FILE_RE.search(f.path)]
    if secrets:
        found.append(("warn", (
            f"The package holds {', '.join(secrets[:5])}{', …' if len(secrets) > 5 else ''}: anyone allowed "
            f"lambda:GetFunction can download the package and read {'it' if len(secrets) == 1 else 'them'}. Leave "
            "secrets out of the package, and keep them in Secrets Manager or Parameter Store."
        )))
    total = pkg.unzipped + fn.layer_size
    if total >= 0.8 * UNZIPPED_LIMIT:
        found.append(("warn", (
            f"Its code and layers take {human_size(total)} unzipped, of the {human_size(UNZIPPED_LIMIT)} Lambda "
            "allows: the next dependency may not fit. Move big libraries into a layer, or deploy it as a container "
            "image (up to 10 GB)."
        )))
    sizes: Counter[str] = Counter()
    for f in pkg.files:
        sizes[_top_folder(f.path)] += f.size
    bundled = sizes.get("boto3/", 0) + sizes.get("botocore/", 0)
    if family == "python" and bundled:
        found.append(("info", (
            f"The package carries its own boto3 and botocore ({human_size(bundled)}), which the Python runtime "
            "already has: leaving them out makes it smaller and its cold starts shorter, unless the code needs a "
            "newer boto3 than the runtime's."
        )))
    junk = sum(f.size for f in pkg.files if _JUNK_RE.search(f.path))
    if junk >= MB:
        found.append(("info", (
            f"{human_size(junk)} of it is caches, tests or version-control files that never run in Lambda "
            "(__pycache__, tests/, .git/): leave them out to make the package smaller."
        )))
    return found
