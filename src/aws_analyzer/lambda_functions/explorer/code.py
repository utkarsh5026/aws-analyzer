"""
The Code tab: the package's files as a tree, the source shown, and the tab's mixin.
"""

from __future__ import annotations

import posixpath
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable

from ..._kit.blocks import _Note, _Table
from ..._kit.fmt import _plural, human_size
from ..._kit.highlight import _json_source_html, _python_html
from ..._kit.text import _esc, _marked, search_rank
from ..._kit.window import _class_if, _skeleton

from ..helpers import runtime_family
from ..models import CodeFile, CodePackage, Function
from ..analysis.package import _JUNK_RE, _SECRET_FILE_RE, _SOURCE_LIMIT, _local_path, read_package
from ..analysis.codemap.model import CodeMap
from ..analysis.codemap.mapping import map_package
from ..analysis.codemap.findings import code_map_findings
from .frame import _CODE_PAGE
from .widgets import _Row


_TOGGLE_GRACE = 0.4  # seconds: a second click on a folder this soon is a double click's, not a fold


def _file_badge(path: str) -> tuple[str, str]:
    """
    A file's type as the Code tab's tree shows it: (its badge, its colour), 'KEY' for a secrets file.
    """
    name = path.rpartition("/")[2]
    if _SECRET_FILE_RE.search(path):
        return "KEY", "key"
    kind = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return {"py": ("PY", "py"), "js": ("JS", "js"), "mjs": ("JS", "js"), "cjs": ("JS", "js"), "ts": ("TS", "js"),
            "rb": ("RB", "py"), "json": ("JSON", "cfg"), "yaml": ("YAML", "cfg"), "yml": ("YAML", "cfg"),
            "toml": ("TOML", "cfg"), "txt": ("TXT", ""), "md": ("MD", "")}.get(kind, ((kind or "file")[:4].upper(), ""))


@dataclass
class _TreeLine:
    """
    A line of the Code tab's tree: a folder or a file, how deep it sits, and what it shows.
    """

    path: str  # 'boto3/' for a folder (its deepest, when the line holds a chain), 'boto3/__init__.py' for a file
    label: str  # the name shown: '__init__.py', 'boto3/', or 'botocore/data/' for a folder that holds only a folder
    depth: int
    file: CodeFile | None = None  # None for a folder
    files: int = 1  # a folder's files, at any depth
    size: int = 0  # unzipped: the file's, or every file's in the folder
    open: bool = False  # a folder whose lines show under it

    @property
    def folder(self) -> bool:
        return self.file is None


def _folders_of(path: str) -> list[str]:
    """
    The folders a file is in, outermost first: 'a/b/c.py' -> ['a/', 'a/b/'].
    """
    parts = path.split("/")[:-1]
    return ["/".join(parts[:i]) + "/" for i in range(1, len(parts) + 1)]


def _file_tree(files: Iterable[CodeFile], opened: set[str], query: str = "", shut: set[str] | None = None
               ) -> list[_TreeLine]:
    """
    The Code tab's tree, as the lines it shows: in each folder its folders, then its files, A to Z; a folder's
    lines under it when `opened` holds its path ('boto3/'), and a folder that holds only a folder on one line with it
    ('botocore/data/'). With a query, only the files it finds, inside the folders that hold them, open unless `shut`
    holds them.
    """
    files = list(files)
    if query:
        files = [f for f in files if search_rank(query, (f.path, posixpath.basename(f.path))) is not None]
    root: dict[str, Any] = {}
    for f in files:
        node = root
        *folders, name = f.path.split("/")
        for part in folders:
            node = node.setdefault(part + "/", {})
        node[name] = f
    sums: dict[int, tuple[int, int]] = {}

    def total(node: dict[str, Any]) -> tuple[int, int]:
        count = size = 0
        for child in node.values():
            c, z = total(child) if isinstance(child, dict) else (1, child.size)
            count, size = count + c, size + z
        sums[id(node)] = (count, size)
        return count, size

    total(root)
    lines: list[_TreeLine] = []

    def walk(node: dict[str, Any], prefix: str, depth: int) -> None:
        for key in sorted((k for k, v in node.items() if isinstance(v, dict)), key=lambda k: (k.lower(), k)):
            child, label = node[key], key
            while len(child) == 1 and isinstance(next(iter(child.values())), dict):  # a folder holding one folder
                only = next(iter(child))
                child, label = child[only], label + only
            path = prefix + label
            is_open = path not in (shut or set()) if query else path in opened
            count, size = sums[id(child)]
            lines.append(_TreeLine(path, label, depth, None, count, size, is_open))
            if is_open:
                walk(child, path, depth + 1)
        for key in sorted((k for k, v in node.items() if not isinstance(v, dict)), key=lambda k: (k.lower(), k)):
            f = node[key]
            lines.append(_TreeLine(f.path, key, depth, f, 1, f.size))

    walk(root, "", 0)
    return lines


def _tree_face(line: _TreeLine, words: list[str], handler: str | None) -> str:
    """
    A line of the Code tab's tree: indented by its depth (with a guide for each folder it's in), a folder's chevron
    and icon or a file's type, its name, and its size.
    """
    junk = " junk" if _JUNK_RE.search(line.path) else ""
    style = f' style="--d:{line.depth}"'
    size = f'<span class="tz">{_esc(human_size(line.size))}</span>'
    if line.folder:
        return (f'<div class="ftl d{" open" if line.open else ""}{junk}"{style}><span class="tv"></span>'
                f'<span class="ti"></span><span class="tn">{_marked(line.label, words)}</span>{size}</div>')
    badge, tone = _file_badge(line.path)
    hb = '<span class="hb">HANDLER</span>' if line.path == handler else ""
    return (f'<div class="ftl{junk}"{style}><span class="tv"></span><span class="ct {tone}">{_esc(badge)}</span>'
            f'<span class="tn">{_marked(line.label, words)}{hb}</span>{size}</div>')


def _copy_html(name: str, text: str, part: bool = False) -> str:
    """
    A file's Copy button: a click selects the text it holds (hidden: the button shows only its label), so Ctrl+C
    copies the file, with no JavaScript (the window has none).
    """
    what = f"the part of {name} shown" if part else f"all of {name}"
    held = text + "\n" if text.endswith("\n") else text  # a browser copies a text without its last line break
    return (f'<span class="cpy" tabindex="0" role="button" aria-label="Copy {_esc(name)}" '
            f'title="Click, then press Ctrl+C (⌘C on a Mac) to copy {_esc(what)}"><span class="cpt">{_esc(held)}</span>'
            "</span>")


def _source_html(path: str, text: str) -> str:
    """
    A file's text with line numbers, Python and JSON in colour (every piece escaped).
    """
    lines = text.count("\n") + (0 if text.endswith("\n") else 1)
    body = (_python_html(text) if path.endswith(".py") else _json_source_html(text) if path.endswith(".json")
            else _esc(text))
    gutter = "\n".join(str(i) for i in range(1, lines + 1))
    return f'<div class="src"><pre class="gut">{gutter}</pre><pre class="txt">{body}</pre></div>'


class _CodeTab:
    """
    The Code tab of LambdaExplorer: the package's files as a tree, the source of the one clicked, Copy,
    Download .zip, Hide files and How it works.
    """

    def _code_page(self) -> Any:
        w, layout = self._w, self._w.Layout
        self.code_view = w.HTML(layout=layout(width="100%"))
        self.code_more = w.Button(layout=layout(width="auto", display="none"), button_style="primary")
        self.code_more.on_click(self._safely(lambda _button: self._load_code(unlimited=True)))
        self.code_save = w.Button(description="⬇ Download .zip", layout=layout(width="auto"),
                                  tooltip="Save the package, the .zip as Lambda stores it, on the notebook's disk")
        self.code_save.add_class("lmx-small")
        self.code_save.on_click(self._safely(lambda _button: self._save_code()))
        self.code_saved = w.HTML(layout=layout(flex="1 1 auto", width="auto"))
        self.files_toggle = w.Button(layout=layout(width="auto"))
        self.files_toggle.add_class("lmx-small")
        self.files_toggle.add_class("lmx-side")
        self.files_toggle.on_click(self._safely(lambda _button: self._toggle_files()))
        self.how_button = w.Button(description="How it works", layout=layout(width="auto", display="none"))
        for css in ("lmx-small", "lmx-side", "lmx-i-how"):
            self.how_button.add_class(css)
        self.how_button.on_click(self._safely(lambda _button: self._toggle_how()))
        self.code_bar = w.HBox([self.files_toggle, self.how_button, self.code_save, self.code_saved],
                               layout=layout(width="100%", display="none"))
        self.code_bar.add_class("lmx-codebar")
        self.code_find = w.Text(placeholder="Find a file", continuous_update=True,
                                layout=layout(flex="1 1 auto", width="auto"))
        self.code_find.add_class("lmx-find")
        self.code_find.observe(self._safely(self._code_typed), names="value")
        self.files_box = w.VBox(layout=layout(width="100%"))
        self.files_box.add_class("lmx-files")
        pager = w.HBox(self._make_pager("files"), layout=layout(width="100%"))
        pager.add_class("lmx-pager")
        self.code_left = w.VBox([self.code_find, self.files_box, pager])
        self.code_left.add_class("lmx-left")
        self.source_view = w.HTML(layout=layout(width="100%"))
        self.code_right = w.VBox([self.source_view])
        self.code_right.add_class("lmx-right")
        self.code_split = w.HBox([self.code_left, self.code_right], layout=layout(display="none"))
        self.code_split.add_class("lmx-split")
        head = w.VBox([self.code_view, self.code_more], layout=layout(width="100%"))
        head.add_class("lmx-codehead")
        self._draw_files_toggle()
        self._draw_how_button()
        return w.VBox([head, self.code_bar, self.code_split])

    # ------------------------------------------------------------------ code

    def _load_code(self, unlimited: bool = False) -> None:
        fn = self.function
        if fn is None:
            return
        self._asked.add("code")
        most = None if unlimited else "50MB"
        self.package = self.code_map = None
        self._how = False
        self._drop("map")
        self._draw_how_button()
        self.code_more.layout.display = "none"
        self.code_split.layout.display = self.code_bar.layout.display = "none"
        self._set(self.code_view, _skeleton(f"Downloading {fn.name}'s deployment package"
                                            + ("" if unlimited else " (up to 50 MB)") + "…"))
        self._status(f"Downloading {fn.name}'s deployment package…", busy=True, owner="code")

        def work() -> tuple[Function, bytes | None]:
            full = self.core.function(fn.name, region=fn.region or None)  # a fresh link to the package
            return full, self.core._package(full, most)

        self._later("code", work, self._got_code, self._code_failed)

    def _code_failed(self, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._set(self.code_view, self._html([_Note(text, "warn")]))
        if isinstance(exc, ValueError) and "max_size" in str(exc):
            size = re.search(r"package is ([\d.]+ [KMGT]?B)", str(exc))
            self.code_more.description = f"Download all {size.group(1)} anyway" if size else "Download it anyway"
            self.code_more.layout.display = ""
            said = (f"The package is {size.group(1)}" if size else "The package is over 50 MB") + (
                ", more than the 50 MB the window downloads at first: the button under this downloads all of it.")
            self._set(self.code_view, self._html([_Note(said, "warn")]))
            self._status(said, "warn")
        else:
            self._status(text, "warn")

    def _got_code(self, got: tuple[Function, bytes | None]) -> None:
        full, data = got
        self._code_fn, self._package_data = full, data
        self.package = package = read_package(full, data)
        blocks = [b for b in self.ui._code_blocks(package, source=False) if not isinstance(b, _Table)]
        self._draw("code", self.code_view, blocks)
        self.code_bar.layout.display = "" if data is not None else "none"
        self.files_toggle.layout.display = "" if package.files else "none"
        self._draw_how_button()
        self._draw_saved()
        if not package.files:
            self.code_split.layout.display = "none"
            self._finish("code", package.note or "No files in the package.", tabs=("code",))
            return
        self.code_split.layout.display = ""
        self._code_file = package.shown_file
        self._code_open = {folder for path in (package.handler_file, package.shown_file) if path
                           for folder in _folders_of(path)}
        self._draw_code_rows(renew=True)
        self._draw_source(package)
        self._finish("code", self._tab_line("code"), tabs=("code",))

    def _draw_saved(self) -> None:
        """
        The line beside Download .zip: where it saves, or where it saved.
        """
        saved = self.saved
        if saved is None:
            where = _local_path(self.core.downloads_folder())
            self._set(self.code_saved, f'<div class="cbs">saves it in <code>{_esc(where)}/</code>, on the '
                                       "notebook's disk</div>")
            return
        self._set(self.code_saved, f'<div class="cbs ok"><b>✓ Saved as <code>{_esc(_local_path(saved.path))}</code>'
                                   "</b> · to get it on your computer, right-click it in JupyterLab's file browser "
                                   "and pick Download</div>")

    def _save_code(self) -> None:
        """
        Download .zip: the package the tab downloaded, saved where download() would (no new download).
        """
        fn, data = self._code_fn, self._package_data
        if fn is None or data is None:
            return
        try:
            saved = self.core._save_package(fn, data)
        except ValueError as exc:
            self._status(str(exc), "warn")
            return
        self.saved = saved
        self._draw_saved()
        where = _local_path(saved.path)
        if saved.sha256_matches is False:
            self._status(f"Saved {where}, but its SHA-256 differs from the one Lambda reports: the code was probably "
                         "deployed again since this tab read it. Refresh reads it again.", "warn")
            return
        self._status(f"Saved {fn.name}'s package as {where} ({human_size(saved.size)})"
                     + (", replacing the one there." if saved.replaced else "."), "ok")

    def _toggle_files(self) -> None:
        """
        Hide files / Show files: folds the tree away so the source has the tab's whole width, or brings it back.
        """
        self._files_shown = not self._files_shown
        self._draw_files_toggle()
        if not self._said[2] and self._said[1] != "warn":
            self._status(self._tab_line("code"))

    def _draw_files_toggle(self) -> None:
        shown = self._files_shown
        self.code_left.layout.display = "" if shown else "none"
        self.files_toggle.description = "Hide files" if shown else "Show files"
        self.files_toggle.tooltip = ("Hide the files, so the source has the whole width" if shown
                                     else "Show the package's files beside the source")
        _class_if(self.files_toggle, "lmx-i-files-hide", shown)
        _class_if(self.files_toggle, "lmx-i-files-show", not shown)

    def _reads_python(self) -> bool:
        """
        Whether How it works can read the code: a Python function's .zip, downloaded, with files in it.
        """
        fn = self._code_fn
        return (fn is not None and runtime_family(fn.runtime) == "python" and self._package_data is not None
                and self.package is not None and bool(self.package.files))

    def _draw_how_button(self) -> None:
        self.how_button.layout.display = "" if self._reads_python() else "none"
        _class_if(self.how_button, "lmx-on", self._how)
        self.how_button.tooltip = (
            "Back to the source of the file picked in the tree" if self._how else
            "How the code works, read from its files with nothing run: each step from the handler, the AWS calls it "
            "makes, the environment variables and event fields it reads, and what to fix")

    def _renew_source(self) -> None:
        """
        The right side in a new box, which starts at the top.
        """
        self.code_right = self._w.VBox([self.source_view])
        self.code_right.add_class("lmx-right")
        self.code_split.children = [self.code_left, self.code_right]

    def _toggle_how(self) -> None:
        """
        How it works: the code read with ast, in place of the source; again, back to the source.
        """
        fn, data = self._code_fn, self._package_data
        if fn is None or data is None:
            return
        if self._how:
            self._how = False
            self._drop("map")
            self._draw_how_button()
            self._draw_source(read_package(fn, data, file=self._code_file) if self._code_file else self.package)
            self._renew_source()
            self._status(self._tab_line("code"))
            return
        self._how = True
        self._draw_how_button()
        if self.code_map is not None:
            self._got_map(self.code_map)
            return
        self._set(self.source_view, _skeleton(f"Reading {fn.name}'s code, starting at its handler…"))
        self._renew_source()
        self._status(f"Reading {fn.name}'s code, starting at its handler…", busy=True, owner="map")
        self._later("map", lambda: map_package(fn, data), self._got_map, self._map_failed)

    def _got_map(self, cmap: CodeMap) -> None:
        self.code_map = cmap
        if not self._how:
            return
        self._draw("how", self.source_view, self.ui._how_blocks(cmap))
        self._renew_source()
        warnings = sum(level == "warn" for level, _ in code_map_findings(cmap))
        line = self._tab_line("code") + (f" · {_plural(warnings, 'warning')}" if warnings else "")
        self._finish("map", line, tabs=("code",))

    def _map_failed(self, exc: BaseException) -> None:
        self._how = False
        self._draw_how_button()
        if self.package is not None and self._code_fn is not None:
            self._draw_source(read_package(self._code_fn, self._package_data, file=self._code_file)
                              if self._code_file else self.package)
        self._finish("map", f"Couldn't read how the code works: {self._error_text(exc)}", "warn", tabs=("code",))

    def _code_lines(self) -> list[_TreeLine]:
        package = self.package
        if package is None:
            return []
        return _file_tree(package.files, self._code_open, self._code_query, self._code_shut)

    def _code_typed(self, change: dict[str, Any]) -> None:
        if self.quiet:
            return
        self._code_query = str(self.code_find.value or "").strip()
        self._code_offset, self._code_shut = 0, set()
        self._draw_code_rows(renew=True)

    def _draw_code_rows(self, renew: bool = False) -> None:
        lines = self._code_lines()
        self._code_offset = min(self._code_offset, max(0, (len(lines) - 1) // _CODE_PAGE * _CODE_PAGE))
        page = lines[self._code_offset:self._code_offset + _CODE_PAGE]
        while len(self._code_rows) < len(page):
            row = _Row(self, self._clicked_code_line, short=True)
            row.box.add_class("lmx-tree")
            self._code_rows.append(row)
        words = self._code_query.split()
        handler = self.package.handler_file if self.package is not None else None
        for row, line in zip(self._code_rows, page):
            row.item = line
            self._set(row.face, _tree_face(line, words, handler))
            if line.folder:
                tip = (f"{line.path} · {_plural(line.files, 'file')}, {human_size(line.size)} unzipped · click to "
                       + ("fold it" if line.open else "open it"))
            else:
                tip = f"{line.path} · {human_size(line.size)} unzipped · click to see it"
            if row.button.tooltip != tip:
                row.button.tooltip = tip
            _class_if(row.box, "lmx-on", not line.folder and line.path == self._code_file)
        children: list[Any] = [row.box for row in self._code_rows[:len(page)]]
        if not page:
            children = [self._empty("files", "No file matches.")]
        if renew:
            box = self._w.VBox(children, layout=self._w.Layout(width="100%"))
            box.add_class("lmx-files")
            self.code_left.children = [box if child is self.files_box else child for child in self.code_left.children]
            self.files_box = box
        else:
            self.files_box.children = children
        files = self.package.files if self.package is not None else []
        if self._code_query:
            found = sum(1 for f in files if search_rank(self._code_query, (f.path, posixpath.basename(f.path)))
                        is not None)
            label = f"<b>{found:,}</b> of {_plural(len(files), 'file')} match"
        else:
            folders = len({folder for f in files for folder in _folders_of(f.path)})
            label = f"<b>{len(files):,}</b> {'file' if len(files) == 1 else 'files'}" + (
                f" in <b>{folders:,}</b> {'folder' if folders == 1 else 'folders'}" if folders else "")
        if len(lines) > _CODE_PAGE:
            label += (f" · lines {self._code_offset + 1:,}–{min(self._code_offset + _CODE_PAGE, len(lines)):,} of "
                      f"{len(lines):,}")
        self._draw_pager("files", self._code_offset, len(lines), _CODE_PAGE, "lines", label=label)

    def _clicked_code_line(self, line: _TreeLine) -> None:
        if not line.folder:
            if line.file is not None:
                self._open_code_file(line.file)
            return
        last, when = self._toggled
        if last == line.path and time.monotonic() - when < _TOGGLE_GRACE:
            return  # a double click's second click: the first one opened (or folded) it already
        self._toggled = (line.path, time.monotonic())
        folded = self._code_shut if self._code_query else self._code_open
        if line.path in folded:
            folded.discard(line.path)
        else:
            folded.add(line.path)
        self._draw_code_rows()

    def _open_code_file(self, f: CodeFile) -> None:
        if self._code_fn is None:
            return
        self._code_file = f.path
        if self._how:
            self._how = False
            self._drop("map")
            self._draw_how_button()
            if not self._said[2] and self._said[1] != "warn":
                self._status(self._tab_line("code"))
        self._code_open.update(_folders_of(f.path))  # so it's still in sight when the search is cleared
        for row in self._code_rows:
            _class_if(row.box, "lmx-on", row.item is not None and not row.item.folder and row.item.path == f.path)
        self._draw_source(read_package(self._code_fn, self._package_data, file=f.path))
        self.code_right = self._w.VBox([self.source_view])  # a new box, which starts at the top
        self.code_right.add_class("lmx-right")
        self.code_split.children = [self.code_left, self.code_right]

    def _draw_source(self, package: CodePackage) -> None:
        path = package.shown_file
        if path is None:
            self._set(self.source_view, '<div class="lmx-hint">👈 <div>Click a file to see its source.</div></div>')
            return
        size = next((f.size for f in package.files if f.path == path), None)
        about = " · ".join(filter(None, [human_size(size) if size is not None else "",
                                         "the handler's file" if path == package.handler_file else ""]))
        copy = (_copy_html(posixpath.basename(path), package.source, package.source_truncated)
                if package.source is not None else "")
        head = f'<div class="srch"><b>{_esc(path)}</b><span>{_esc(about)}</span>{copy}</div>'
        if package.source is not None:
            more = (self._html([_Note(f"Only the first {human_size(_SOURCE_LIMIT)} of it is shown.")])
                    if package.source_truncated else "")
            self._set(self.source_view, head + _source_html(path, package.source) + more)
        else:
            self._set(self.source_view, head + self._html([_Note(package.note or "Nothing to show.")]))
