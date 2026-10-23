"""
Two versions of a function side by side (diff()): the files of its own that changed, line by line, the Python
functions and classes those lines are in, the libraries packed with it that were added, removed or upgraded, and
its settings. It reads the two .zip files code() downloads, makes no AWS calls and runs nothing in them: Python is
read with the ast module.
"""

from __future__ import annotations

import ast
import difflib
import io
import json
import re
import zipfile
from dataclasses import dataclass, field

from ..._kit.fmt import _fmt_dt, human_age, human_size

from ..helpers import MB, runtime_family
from ..models import FileChange, Function, LibraryChange, SettingChange, Version, VersionDiff
from .codemap.mapping import map_package
from .codemap.model import AwsCall
from .package import _SECRET_FILE_RE, handler_file
from .source import _is_library_file, _is_source_file, _library_names
from .text import _cli

_COMPARED_BYTES = 1 * MB

_MINIFIED_LINE = 2000

_BINARY_SNIFF = 8192

_PACKAGE_JSON_BYTES = 1 * MB

_CONTEXT_LINES = 3

_FILE_DIFF_LINES = 400

_DIFF_LINES = 3000

_TOP_LEVEL = "(top level)"

_RECORD_BYTES = 2 * MB

_LATEST = "$LATEST"

_DIST_INFO_RE = re.compile(r"^(?P<name>[^/]+?)-(?P<version>[^/-]+)\.(?:dist|egg)-info/")

_NODE_PACKAGE_RE = re.compile(r"^node_modules/(?P<name>(?:@[^/]+/)?[^/@]+)/package\.json$")

_LISTED_LIBRARIES = 4



def version_label(fn: Function) -> str:
    """
    How a report names the version a Function is: '$LATEST' or 'version 3'.
    """
    return fn.version if fn.version == _LATEST else f"version {fn.version}"


def version_ref(fn: Function) -> str:
    """
    The name a command takes for the version a Function is: 'etl' for $LATEST, 'etl:3' for version 3.
    """
    return fn.name if fn.version == _LATEST else f"{fn.name}:{fn.version}"


def _code_sha(fn: Function) -> str:
    return fn.raw.get("CodeSha256") or ""


def _earlier(versions: list[Version], new: Function) -> list[Version]:
    """
    The published versions before `new`, newest first: every one when `new` is $LATEST.
    """
    numbered = [v for v in versions if v.version.isdigit()]
    if new.version.isdigit():
        numbered = [v for v in numbered if int(v.version) < int(new.version)]
    return sorted(numbered, key=lambda v: int(v.version), reverse=True)


def version_before(versions: list[Version], new: Function) -> tuple[Version | None, list[str]]:
    """
    The version diff() compares `new` with when none is named, and the versions it passed over: the newest
    published version before it whose code (its SHA-256) is different, since publishing doesn't always change the
    code. When every earlier version has the same code, the newest of them (only settings can differ), with none
    passed over; (None, []) when there's no published version before it.
    """
    earlier = _earlier(versions, new)
    sha = _code_sha(new)
    passed: list[str] = []
    for version in earlier:
        if not sha or version.code_sha != sha:
            return version, passed
        passed.append(version.version)
    return (earlier[0] if earlier else None), []


def nothing_before(fn: Function) -> str:
    """
    Why diff() has nothing to compare a version with, and how to keep a copy of each deploy from now on.
    """
    changed = f" Its code last changed {human_age(fn.last_modified)} ({_fmt_dt(fn.last_modified)})." if (
        fn.last_modified) else ""
    if fn.version != _LATEST:
        return f"Version {fn.version} is the first version {fn.name} has: there's nothing before it to compare with."
    return (f"{fn.name} has no published versions, and Lambda keeps no copy of code it replaced, so there's nothing "
            f"to compare $LATEST with.{changed} To keep a copy of each deploy from now on, publish a version after it: "
            f"aws lambda publish-version {_cli(fn)} (SAM's AutoPublishAlias and CDK's currentVersion do it for you).")



def _setting_values(fn: Function) -> dict[str, str]:
    """
    The settings diff() compares, as people write them.
    """
    return {
        "Runtime": fn.runtime or "container image",
        "Handler": fn.handler or "-",
        "Memory": f"{fn.memory:,} MB",
        "Timeout": f"{fn.timeout:,} s",
        "Architecture": fn.architecture,
        "/tmp storage": f"{fn.ephemeral_storage:,} MB",
        "Layers": ", ".join(layer.name for layer in fn.layers) or "none",
        "Role": (fn.role or "-").rsplit("/", 1)[-1],
        "VPC": fn.vpc_id or "none",
        "Tracing": fn.tracing or "PassThrough",
        "SnapStart": "on" if fn.snapstart else "off",
        "Log format": fn.log_format or "Text",
        "Image": fn.image_uri or "-",
    }


def diff_settings(old: Function, new: Function) -> list[SettingChange]:
    """
    The settings that differ between two versions of a function (runtime, handler, memory, timeout, layers, role,
    VPC, ...), in that order, as ('Memory', '1,024 MB', '512 MB'). Environment variables are compared by name in
    diff_packages(); their values are never read.
    """
    before, after = _setting_values(old), _setting_values(new)
    return [SettingChange(name, before[name], after[name]) for name in after if before[name] != after[name]]



def _entries(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    return {info.filename: info for info in archive.infolist() if not info.is_dir()}


def _is_same(old: zipfile.ZipInfo | None, new: zipfile.ZipInfo | None) -> bool:
    """
    Whether a file is the same in both packages, from the zip's index (its CRC-32 and size), so unchanged files are
    never unpacked.
    """
    return old is not None and new is not None and old.CRC == new.CRC and old.file_size == new.file_size


def _node_version(archive: zipfile.ZipFile, path: str) -> str:
    """
    The version in a node_modules package's package.json, or '?' when it can't be read.
    """
    if archive.getinfo(path).file_size > _PACKAGE_JSON_BYTES:
        return "?"
    try:
        return str(json.loads(archive.read(path)).get("version") or "?")
    except (ValueError, AttributeError):
        return "?"


def _libraries(archive: zipfile.ZipFile, paths: list[str]) -> dict[str, str]:
    """
    The libraries packed in a package, name -> version: Python's from their .dist-info folders
    ('requests-2.32.3.dist-info/...'), JavaScript's from node_modules/<name>/package.json.
    """
    found: dict[str, str] = {}
    for path in paths:
        if match := _DIST_INFO_RE.match(path):
            found[match["name"].lower().replace("-", "_")] = match["version"]
        elif match := _NODE_PACKAGE_RE.match(path):
            found[match["name"]] = _node_version(archive, path)
    return found


def _recorded_folders(archive: zipfile.ZipFile, paths: list[str]) -> set[str]:
    """
    The top-level folders and modules the libraries' .dist-info RECORD files list, lowercased: a library's import
    name isn't always its own ('scikit_learn-1.4.2.dist-info' installs 'sklearn/').
    """
    folders: set[str] = set()
    for path in paths:
        if not path.endswith(".dist-info/RECORD") or archive.getinfo(path).file_size > _RECORD_BYTES:
            continue
        for line in archive.read(path).decode("utf-8", errors="replace").splitlines():
            first = line.split(",", 1)[0].split("/", 1)[0]
            if first and not first.endswith((".dist-info", ".data")) and first != "..":
                folders.add(first.removesuffix(".py").lower())
    return folders


def _library_changes(old: dict[str, str], new: dict[str, str]) -> list[LibraryChange]:
    """
    The libraries added, removed or on another version, by name.
    """
    changes = []
    for name in sorted(old.keys() | new.keys()):
        if old.get(name) != new.get(name):
            changes.append(LibraryChange(name, old.get(name), new.get(name)))
    return changes


def _read_text(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> tuple[str | None, str | None]:
    """
    A file's text to compare line by line, or why it's compared by size only: a secrets file (never read), a file
    that isn't source, one over _COMPARED_BYTES, a binary file or minified code.
    """
    path = info.filename
    if _SECRET_FILE_RE.search(path):
        return None, "a secrets file: its text isn't read"
    if not _is_source_file(path):
        return None, "not a source file: compared by size"
    if info.file_size > _COMPARED_BYTES:
        return None, f"{human_size(info.file_size)}: too big to compare line by line"
    raw = archive.read(path)
    if b"\x00" in raw[:_BINARY_SNIFF]:
        return None, "a binary file"
    text = raw.decode("utf-8", errors="replace")
    if any(len(line) > _MINIFIED_LINE for line in text.splitlines()):
        return None, "minified (lines thousands of characters long): compared by size"
    return text, None


def _hunk_range(start: int, stop: int) -> str:
    """
    A hunk's line range as unified diff writes it: '12,4' (from line 12, 4 lines), '12' for one line, '11,0' for
    none (after line 11).
    """
    length = stop - start
    first = start + 1 if length else start
    return str(first) if length == 1 else f"{first},{length}"


@dataclass
class _Hunks:
    """
    A file's unified diff as it's built: its lines, the counts of lines added and removed, and the numbers of the
    lines that changed on each side (1-based), for the definitions they're in.
    """

    lines: list[str] = field(default_factory=list)
    added: int = 0
    removed: int = 0
    old_changed: set[int] = field(default_factory=set)
    new_changed: set[int] = field(default_factory=set)

    def add(self, tag: str, old_lines: list[str], new_lines: list[str], span: tuple[int, int, int, int]) -> None:
        """
        One of difflib's opcodes: lines kept as context, removed ('-'), added ('+'), or both for a replacement.
        """
        i1, i2, j1, j2 = span
        if tag == "equal":
            self.lines += [f" {line}" for line in old_lines[i1:i2]]
            return
        self.lines += [f"-{line}" for line in old_lines[i1:i2]]
        self.lines += [f"+{line}" for line in new_lines[j1:j2]]
        self.removed += i2 - i1
        self.added += j2 - j1
        self.old_changed.update(range(i1 + 1, i2 + 1))
        self.new_changed.update(range(j1 + 1, j2 + 1))


def _hunks(old_text: str, new_text: str) -> _Hunks:
    """
    The unified diff of two texts, like diff -u without its --- / +++ header, with _CONTEXT_LINES lines around each
    change.
    """
    old_lines, new_lines = old_text.splitlines(), new_text.splitlines()
    hunks = _Hunks()
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for group in matcher.get_grouped_opcodes(_CONTEXT_LINES):
        first, last = group[0], group[-1]
        hunks.lines.append(f"@@ -{_hunk_range(first[1], last[2])} +{_hunk_range(first[3], last[4])} @@")
        for tag, i1, i2, j1, j2 in group:
            hunks.add(tag, old_lines, new_lines, (i1, i2, j1, j2))
    return hunks


def _parsed(text: str | None, path: str) -> tuple[ast.Module | None, str | None]:
    """
    A Python file's syntax tree, or why Python can't read it ('SyntaxError at line 12').
    """
    if text is None or not path.endswith(".py"):
        return None, None
    try:
        return ast.parse(text, filename=path), None
    except SyntaxError as exc:
        return None, f"SyntaxError at line {exc.lineno}"


def _definitions(tree: ast.Module) -> list[tuple[str, int, int]]:
    """
    Every function, method and class in a syntax tree as (qualified name, first line, last line), its decorators
    included: ('Orders.save', 7, 9).
    """
    found: list[tuple[str, int, int]] = []

    def visit(node: ast.AST, scope: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, scope)
                continue
            first = min([child.lineno] + [decorator.lineno for decorator in child.decorator_list])
            found.append((".".join(scope + [child.name]), first, child.end_lineno or child.lineno))
            visit(child, scope + [child.name])

    visit(tree, [])
    return found


def _enclosing(line: int, definitions: list[tuple[str, int, int]]) -> str:
    """
    The innermost definition a line is in, or '(module)' for top-level code.
    """
    around = [d for d in definitions if d[1] <= line <= d[2]]
    return max(around, key=lambda d: d[1])[0] if around else _TOP_LEVEL


def _changed_definitions(trees: tuple[ast.Module | None, ast.Module | None], hunks: _Hunks) -> list[str]:
    """
    The Python functions and classes the changed lines are in, in the order they're found: removed lines by the old
    file's definitions, added ones by the new file's.
    """
    old_tree, new_tree = trees
    names: list[str] = []
    for tree, changed in ((new_tree, hunks.new_changed), (old_tree, hunks.old_changed)):
        if tree is None:
            continue
        definitions = _definitions(tree)
        names += [_enclosing(line, definitions) for line in sorted(changed)]
    return list(dict.fromkeys(names))


@dataclass
class _Packages:
    """
    The two packages being compared, open, and how many diff lines are still to be kept.
    """

    old: zipfile.ZipFile
    new: zipfile.ZipFile
    room: int = _DIFF_LINES


def _file_change(path: str, infos: tuple[zipfile.ZipInfo | None, zipfile.ZipInfo | None], packages: _Packages,
                 diff: VersionDiff) -> FileChange:
    """
    One file that differs, with its lines compared when both sides are text (a missing side is empty), and the
    Python file noted in diff.broken when it read before and no longer does.
    """
    old_info, new_info = infos
    status = "added" if old_info is None else "removed" if new_info is None else "changed"
    change = FileChange(path, status, old_info.file_size if old_info else 0, new_info.file_size if new_info else 0)
    old_text, old_note = _read_text(packages.old, old_info) if old_info else ("", None)
    new_text, new_note = _read_text(packages.new, new_info) if new_info else ("", None)
    if old_text is None or new_text is None:
        change.note = new_note or old_note
        return change
    hunks = _hunks(old_text, new_text)
    old_tree, old_error = _parsed(old_text if old_info else None, path)
    new_tree, new_error = _parsed(new_text if new_info else None, path)
    if new_error and not old_error:
        diff.broken[path] = new_error
    change.added, change.removed = hunks.added, hunks.removed
    change.definitions = _changed_definitions((old_tree, new_tree), hunks)
    kept = min(len(hunks.lines), _FILE_DIFF_LINES, max(0, packages.room))
    change.lines, change.truncated = hunks.lines[:kept], kept < len(hunks.lines)
    packages.room -= kept
    return change


def _handler_first(paths: set[str], first: str | None) -> list[str]:
    """
    Paths in a package, the handler's file first, then by folder depth and name.
    """
    return sorted(paths, key=lambda path: (path != first, path.count("/"), path))


def _env_reads(name: str) -> re.Pattern[str]:
    """
    A read of an environment variable in code: its name in quotes (os.environ['MODE'], os.getenv("MODE")) or
    process.env.MODE.
    """
    quoted = re.escape(name)
    return re.compile(rf"""(['"]){quoted}\1|process\.env\.{quoted}\b""")


def _find_reads(diff: VersionDiff, packages: _Packages, paths: list[str]) -> None:
    """
    Where the new code still reads each environment variable that was taken out (diff.still_read: name ->
    'app.py:4'), in its own source files.
    """
    patterns = {name: _env_reads(name) for name in diff.env_removed}
    for path in paths:
        text, _ = _read_text(packages.new, packages.new.getinfo(path))
        for number, line in enumerate((text or "").splitlines(), 1):
            for name, pattern in patterns.items():
                if name not in diff.still_read and pattern.search(line):
                    diff.still_read[name] = f"{path}:{number}"


def _compare_files(diff: VersionDiff, packages: _Packages) -> None:
    """
    Fills a VersionDiff from the two packages: its own files that differ, the libraries that changed, and where the
    new code reads an environment variable that was taken out.
    """
    old, new = _entries(packages.old), _entries(packages.new)
    libraries = _library_names(list(old)) | _library_names(list(new))
    libraries |= _recorded_folders(packages.old, list(old)) | _recorded_folders(packages.new, list(new))
    diff.libraries = _library_changes(_libraries(packages.old, list(old)), _libraries(packages.new, list(new)))
    first = handler_file(diff.function.handler, diff.function.runtime, new)
    for path in _handler_first(old.keys() | new.keys(), first):
        is_library = _is_library_file(path, libraries)
        if is_library and not _is_same(old.get(path), new.get(path)):
            diff.library_files += 1
        elif is_library:
            continue
        elif _is_same(old.get(path), new.get(path)):
            diff.unchanged_files += 1
        else:
            diff.files.append(_file_change(path, (old.get(path), new.get(path)), packages, diff))
    if diff.env_removed:
        own = [path for path in _handler_first(set(new), first) if not _is_library_file(path, libraries)]
        _find_reads(diff, packages, own)


def _added_calls(old: Function, old_data: bytes, new: Function, new_data: bytes) -> list[AwsCall]:
    """
    The AWS calls a Python function's new code makes that its old code didn't (by service and operation), read with
    map_package (nothing is run), leaving out presigned links, which need no permission of their own.
    """
    if runtime_family(new.runtime) != "python":
        return []
    before = {(call.service, call.operation) for call in map_package(old, old_data).aws}
    added = []
    for call in map_package(new, new_data).aws:
        is_new = (call.service, call.operation) not in before
        if is_new and call.permission:
            added.append(call)
            before.add((call.service, call.operation))
    return added


def _open_zip(data: bytes, label: str) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError(f"The package of {label} ({human_size(len(data))}) isn't a readable .zip file") from None


def diff_packages(old: Function, old_data: bytes | None, new: Function, new_data: bytes | None) -> VersionDiff:
    """
    What changed between two versions of a function (their configurations as function() returns them, and their
    .zip files as code() downloads them): the files of its own that were added, removed or changed, each changed
    text file's unified diff and the Python functions and classes its changes are in, the libraries packed with it
    that were added, removed or upgraded (counted, not listed line by line), its settings, and the environment
    variable names added and removed (values are never read), and for Python the AWS calls the new code makes that
    the old didn't, with the permission each needs. Unchanged files are told apart by the zip's index, so they aren't
    unpacked; secrets files, binaries and minified code are compared by size. Nothing in them is run.
    """
    diff = VersionDiff(new, old, settings=diff_settings(old, new))
    diff.env_added = sorted(set(new.env_names) - set(old.env_names))
    diff.env_removed = sorted(set(old.env_names) - set(new.env_names))
    diff.same_code = bool(_code_sha(new)) and _code_sha(old) == _code_sha(new)
    if diff.same_code:
        return diff
    if "Image" in (old.package_type, new.package_type):
        diff.note = ("It runs from a container image, whose code is in the image: the image each version runs is "
                     "compared, not its files. docker pull both from ECR to compare what's inside.")
        return diff
    missing = [version_label(fn) for fn, data in ((old, old_data), (new, new_data)) if data is None]
    if missing:
        diff.note = f"Lambda gave no link to download the code of {' or '.join(missing)}."
        return diff
    with _open_zip(old_data, version_label(old)) as old_zip, _open_zip(new_data, version_label(new)) as new_zip:
        _compare_files(diff, _Packages(old_zip, new_zip))
    if diff.files:
        diff.new_calls = _added_calls(old, old_data, new, new_data)
    return diff



def _library_text(change: LibraryChange) -> str:
    if change.old is None:
        return f"{change.name} {change.new} added"
    if change.new is None:
        return f"{change.name} removed"
    return f"{change.name} {change.old} → {change.new}"


def _libraries_finding(changes: list[LibraryChange], after: str) -> str:
    """
    The libraries that changed, the first few named.
    """
    listed = ", ".join(_library_text(change) for change in changes[:_LISTED_LIBRARIES])
    more = len(changes) - _LISTED_LIBRARIES
    if more > 0:
        listed += f" and {more:,} more"
    count = "1 library" if len(changes) == 1 else f"{len(changes):,} libraries"
    return (f"{count} packed with it changed in {after}: {listed}. A library added, removed or upgraded can change "
            "how the code behaves without a line of its own changing (an import that fails, a function that works "
            "differently); a new version's changelog says what changed.")


def _calls_finding(calls: list[AwsCall], fn: Function, after: str) -> str:
    """
    The AWS calls a version adds, and the permissions its role needs for them.
    """
    listed = ", ".join(f"{call.label} at {call.file}:{call.line}" for call in calls[:_LISTED_LIBRARIES])
    more = len(calls) - _LISTED_LIBRARIES
    if more > 0:
        listed += f" and {more:,} more"
    permissions = ", ".join(dict.fromkeys(call.permission for call in calls))
    count = "an AWS call" if len(calls) == 1 else f"{len(calls):,} AWS calls"
    return (f"{after} makes {count} the code didn't make before: {listed}. Its role needs {permissions}, or the runs "
            f"that reach {'it' if len(calls) == 1 else 'them'} fail with AccessDenied; errors('{fn.name}') shows "
            "whether they do.")


def _setting_findings(diff: VersionDiff, before: str, after: str) -> list[tuple[str, str]]:
    """
    What the settings that changed mean for runs: a shorter timeout, less memory, another runtime.
    """
    fn, old = diff.function, diff.old
    found: list[tuple[str, str]] = []
    if old is None:
        return found
    if fn.timeout < old.timeout:
        found.append(("info", (
            f"Its timeout went down from {old.timeout:,} s to {fn.timeout:,} s in {after}: runs that take longer now "
            f"time out. performance('{fn.name}') shows how long its runs take.")))
    if fn.memory < old.memory:
        found.append(("info", (
            f"Its memory went down from {old.memory:,} MB to {fn.memory:,} MB in {after}: it has less CPU too, so runs "
            f"take longer, and a run that needs more runs out. performance('{fn.name}') shows what runs use.")))
    if fn.runtime and old.runtime and fn.runtime != old.runtime:
        found.append(("info", (
            f"It moved from {old.runtime} to {fn.runtime} in {after}: a new runtime can change how its code and "
            f"libraries behave, and libraries built for {old.runtime} may not load on it.")))
    return found


def diff_findings(diff: VersionDiff) -> list[tuple[str, str]]:
    """
    What a diff() means: Python files that don't read (whatever imports them fails), environment variables taken out
    that the code still reads, AWS calls the new code makes (and the permissions they need), a package built again with the same files, libraries that changed, settings that change
    how runs go, and when nothing changed at all.
    """
    if diff.note or diff.old is None:
        return []
    fn, before, after = diff.function, version_label(diff.old), version_label(diff.function)
    found: list[tuple[str, str]] = []
    added = {change.path for change in diff.files if change.status == "added"}
    for path, why in diff.broken.items():
        what = f"{path}, new in {after}, has a {why}" if path in added else (
            f"{path} has a {why} in {after} that {before} didn't have")
        found.append(("warn", (
            f"{what}: whatever imports it fails before any of it runs. code('{version_ref(fn)}', '{path}') shows "
            "the line.")))
    for name, where in diff.still_read.items():
        found.append(("warn", (
            f"{name} was taken out of its environment variables in {after}, but {where} still reads it: that read "
            f"fails or gets nothing. Set it again (aws lambda update-function-configuration {_cli(fn)} --environment "
            f"...) or change the code.")))
    if diff.same_code:
        found.append(("info", (
            f"The code is the same in {before} and {after} (the same SHA-256): only the settings below changed."
            if diff.settings or diff.env_added or diff.env_removed
            else f"Nothing changed between {before} and {after}: the same code and the same settings.")))
    if diff.new_calls:
        found.append(("info", _calls_finding(diff.new_calls, fn, after)))
    rebuilt = not diff.same_code and not diff.files and not diff.library_files
    if rebuilt:
        found.append(("info", (
            f"The packages of {before} and {after} differ (another SHA-256), but every file in them is the same: "
            "the .zip was built again (file dates or order), not the code. Any change in how it runs comes from the "
            "settings below, or from outside the code.")))
    if diff.libraries:
        found.append(("info", _libraries_finding(diff.libraries, after)))
    return found + _setting_findings(diff, before, after)
