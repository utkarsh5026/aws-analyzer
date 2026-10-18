"""
map_code() and map_package(): a CodeMap of a function's code, from its files or its
package's bytes.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Iterable

from ...._kit.fmt import human_size
from ...._kit.text import _clip

from ...helpers import runtime_family
from ...models import Function
from ..package import _JUNK_RE, handler_file
from .terms import _LIBRARIES, _MAP_BYTES, _MAP_FILES, _RUNTIME_MODULES
from .model import CodeMap
from .reader import _CodeReader


def _python_version(runtime: str | None) -> tuple[int, int] | None:
    """
    'python3.12' -> (3, 12).
    """
    match = re.fullmatch(r"python(\d+)\.(\d+)", runtime or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def map_code(
    files: dict[str, str | bytes],
    handler: str,
    *,
    function: Function | None = None,
    names: Iterable[str] | None = None,
    libraries: Iterable[str] = (),
) -> CodeMap:
    """
    How Python code works, read with Python's ast module and never run: files is {path: source} (the package's
    own .py files), handler the handler setting ('app.handler'). Starting at the handler, it follows the code into
    its own functions and modules, keeping track of what each name holds (a boto3 client, part of the event, an
    environment variable...), and returns a CodeMap: what runs at start-up and on each call, step by step, its AWS
    calls with the IAM permission each needs, the environment variables and event fields it reads, the libraries it
    imports and where they come from. names lists every file in the package (libraries, .so files) when files holds
    only the Python ones; libraries names packages in it that aren't its own code. function (a Function) adds what
    the findings check against: the runtime, its environment variables' names and its layers.

        map_code({"app.py": open("app.py").read()}, "app.handler")
    """
    texts = {path: text.decode("utf-8", "replace") if isinstance(text, bytes) else str(text)
             for path, text in files.items()}
    names = list(names) if names is not None else list(texts)
    cmap = CodeMap(function, handler)
    runtime = function.runtime if function is not None else None
    if not handler or "." not in handler:
        cmap.note = f"Its handler setting ({handler or 'none'}) doesn't name a file and a function, like app.handler."
        return cmap
    cmap.handler_file = handler_file(handler, runtime if runtime_family(runtime) == "python" else "python3.12", names)
    if cmap.handler_file is None:
        module = handler.rsplit(".", 1)[0].replace(".", "/")
        cmap.note = f"No file in the package matches its handler {handler} (Lambda looks for {module}.py)."
        return cmap
    stem = handler.rsplit(".", 1)[0].replace("/", ".").replace(".", "/") + ".py"
    root = cmap.handler_file[: -len(stem)] if cmap.handler_file.endswith(stem) else ""
    libs = set(libraries) | _RUNTIME_MODULES | _LIBRARIES
    for name in names:
        first = name[len(root):].split("/")[0] if name.startswith(root) else ""
        if first.endswith((".dist-info", ".egg-info")):
            libs.add(re.split(r"-\d", first.rsplit(".", 1)[0])[0].replace("-", "_"))
    libs.discard(handler.rsplit(".", 1)[0].replace("/", ".").split(".")[0])  # its own handler is never a library
    reader = _CodeReader(texts, names, root, cmap, _python_version(runtime), libs,
                         bool(function is not None and function.layers))
    try:
        reader.read(handler)
    except RecursionError:
        cmap.limited = "stopped where the code nests too deep to follow"
    except Exception as exc:  # code it can't follow: what it found so far, said to be partial, never a traceback
        cmap.limited = f"stopped partway, at code it couldn't follow ({type(exc).__name__}: {_clip(str(exc), 80)})"
    if cmap.limited.startswith("stopped"):  # keep what it found before it stopped
        cmap.functions, cmap.event = list(reader.reached.values()), list(reader.event_reads.values())
        cmap.routes = [(method, path, key) for method, path, key, _ in reader.routes]
    return cmap


def map_package(fn: Function, data: bytes | None) -> CodeMap:
    """
    How a Python function's code works, from its deployment package (the .zip as code() downloads it): its own
    .py files are read with Python's ast module and never run, libraries packed in it (found by their .dist-info
    folders) aren't read as its code. See map_code(). A container image, a runtime that isn't Python or a package
    without the handler's file gives a CodeMap with only a note saying so.
    """
    if fn.package_type == "Image":
        return CodeMap(fn, fn.handler, note=(
            f"It's a container image ({fn.image_uri or 'image URI not shown'}): its code is in the image, which this "
            "doesn't pull."))
    if runtime_family(fn.runtime) != "python":
        return CodeMap(fn, fn.handler, note=(
            f"It runs {fn.runtime or 'a custom runtime'}: how_it_works() reads Python code. code() shows its files and "
            "the handler's source."))
    if data is None:
        return CodeMap(fn, fn.handler, note="Lambda gave no link to download its code.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError(f"The package ({human_size(len(data))}) isn't a readable .zip file") from None
    with archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        names = [info.filename for info in infos]
        libraries: set[str] = set()
        for info in infos:  # what each library installed: the first part of every path its RECORD lists
            if info.filename.endswith((".dist-info/RECORD", ".egg-info/top_level.txt", ".dist-info/top_level.txt")):
                text = archive.read(info).decode("utf-8", "replace")
                libraries |= {line.split(",")[0].split("/")[0].removesuffix(".py") for line in text.splitlines()
                              if line.strip() and not line.startswith(("..", "/"))}
        libraries = {lib for lib in libraries if not lib.endswith((".dist-info", ".egg-info", ".data")) and lib}
        handler_module = (fn.handler or "").rsplit(".", 1)[0].replace("/", ".").split(".")[0]
        libraries.discard(handler_module)
        files: dict[str, str] = {}
        total = 0
        limited = ""
        for info in sorted(infos, key=lambda i: (i.filename.count("/"), i.filename)):
            path = info.filename
            top = path.split("/")[0].removesuffix(".py")
            if not path.endswith(".py") or top in libraries | _RUNTIME_MODULES | (_LIBRARIES - {handler_module}):
                continue
            if "site-packages/" in path or _JUNK_RE.search(path):
                continue
            if len(files) >= _MAP_FILES or total + info.file_size > _MAP_BYTES:
                limited = f"read {len(files):,} of its Python files ({human_size(total)})"
                break
            files[path] = archive.read(info).decode("utf-8", "replace")
            total += info.file_size
    cmap = map_code(files, fn.handler or "", function=fn, names=names, libraries=libraries)
    cmap.limited = cmap.limited or limited
    return cmap
