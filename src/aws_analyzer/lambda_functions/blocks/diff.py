"""
diff()'s report: _Diff, one file's changes line by line (added and removed lines coloured in HTML, + and - in
text), and _diff_blocks, the whole report from a VersionDiff.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..._kit.blocks import _Cards, _Findings, _Next, _Note, _Report, _Table, _Title, _Tone, _draws
from ..._kit.fmt import _fmt_dt, _plural, human_size
from ..._kit.render import _call
from ..._kit.text import _esc

from ..models import FileChange, Function, VersionDiff
from ..analysis.versions import diff_findings, version_label, version_ref

_SHOWN_FILES = 12

_LISTED_DEFINITIONS = 4

_STATUS_TONES = {"added": "ok", "removed": "bad", "changed": "warn"}

_LINE_CLASSES = {"+": "dfa", "-": "dfr", "@": "dfh"}


@dataclass
class _Diff:
    """
    One file that changed between two versions, line by line: `open` shows it unfolded in HTML.
    """

    change: FileChange
    open: bool = False


def _change_size(change: FileChange) -> str:
    """
    '+12 −3', or the sizes when its lines weren't compared ('2.1 KB → 2.4 KB').
    """
    if change.note:
        return f"{human_size(change.old_size)} → {human_size(change.new_size)}"
    return f"+{change.added:,} −{change.removed:,}"


def _changed_in(change: FileChange) -> str:
    """
    The functions its changes are in: 'in load_orders, handler and 2 more'.
    """
    names = change.definitions[:_LISTED_DEFINITIONS]
    if not names:
        return ""
    more = len(change.definitions) - len(names)
    return "in " + ", ".join(names) + (f" and {more:,} more" if more > 0 else "")


def _line_html(line: str) -> str:
    return f'<span class="{_LINE_CLASSES.get(line[:1], "dfc")}">{_esc(line)}</span>'


@_draws(_Diff, "html")
def _diff_html(block: _Diff, report: _Report) -> str:
    change = block.change
    summary = (f'<code>{_esc(change.path)}</code> <span class="dfs dfs-{change.status}">{change.status}</span> '
               f'<span class="dfn">{_esc(_change_size(change))}</span> <span class="dfd">{_esc(_changed_in(change))}</span>')
    body = ""
    if change.lines:
        body = '<pre class="dfp">' + "\n".join(_line_html(line) for line in change.lines) + "</pre>"
    notes = [change.note] if change.note else []
    if change.truncated:
        notes.append("Only the start of its changes is shown.")
    body += "".join(f'<div class="dfx">{_esc(note)}</div>' for note in notes)
    opened = " open" if block.open else ""
    return f'<details class="dif"{opened}><summary>{summary}</summary>{body}</details>'


@_draws(_Diff, "text")
def _diff_lines(block: _Diff, report: _Report) -> list[str]:
    change = block.change
    head = ", ".join(filter(None, [change.status, _change_size(change), _changed_in(change)]))
    lines = ["", f"-- {change.path} ({head}) --"] + change.lines
    if change.note:
        lines.append(f"({change.note})")
    if change.truncated:
        lines.append("(only the start of its changes is shown)")
    return lines


def _when(fn: Function) -> str:
    return f"{version_label(fn)} ({_fmt_dt(fn.last_modified)})" if fn.last_modified else version_label(fn)


def _calls(region: str | None) -> Any:
    """
    A maker of next steps about a function, with region= when it isn't in the view's region.
    """
    def call(command: str, ref: str, *args: Any, **kwargs: Any) -> str:
        return _call(command, ref, *args, **({"region": region} if region else {}), **kwargs)
    return call


def _setting_rows(diff: VersionDiff) -> list[list[Any]]:
    rows: list[list[Any]] = [[change.setting, change.old, change.new] for change in diff.settings]
    rows += [[f"Environment variable {name}", "-", "set"] for name in diff.env_added]
    rows += [[f"Environment variable {name}", "set", _Tone("removed", "warn" if name in diff.still_read else "")]
             for name in diff.env_removed]
    return rows


def _library_rows(diff: VersionDiff) -> list[list[Any]]:
    return [[change.name, change.old or "-", change.new or "-"] for change in diff.libraries]


def _file_rows(diff: VersionDiff) -> list[list[Any]]:
    return [[change.path, _Tone(change.status, _STATUS_TONES[change.status]), _change_size(change), _changed_in(change)]
            for change in diff.files]


def _cards(diff: VersionDiff, before: str, after: str) -> _Cards:
    """
    The cards: what was compared, files and lines that changed, libraries and settings.
    """
    settings = len(diff.settings) + len(diff.env_added) + len(diff.env_removed)
    return _Cards([
        ("Compared", f"{before} → {after}"),
        ("Files changed", "none" if diff.same_code else f"{len(diff.files):,} of {len(diff.files) + diff.unchanged_files:,}",
         "warn" if diff.broken else ""),
        ("Lines", f"+{diff.added:,} −{diff.removed:,}"),
        ("Libraries", f"{len(diff.libraries):,} changed" if diff.libraries else "same"),
        ("Settings", f"{settings:,} changed" if settings else "same", "warn" if diff.still_read else ""),
    ])


def _tables(diff: VersionDiff, before: str, after: str) -> list[Any]:
    blocks: list[Any] = []
    if diff.settings or diff.env_added or diff.env_removed:
        blocks.append(_Table(["Setting", before, after], _setting_rows(diff), title="Settings that changed",
                             max_rows=0))
    if diff.libraries:
        title = "Libraries that changed" + (f" ({_plural(diff.library_files, 'file')} of theirs differ)"
                                            if diff.library_files else "")
        blocks.append(_Table(["Library", before, after], _library_rows(diff), title=title))
    if diff.files:
        blocks.append(_Table(["File", "Change", "Lines", "Where"], _file_rows(diff), title="Its own files that changed",
                             path_cols=(0,)))
    return blocks


def _next_steps(diff: VersionDiff, region: str | None) -> _Next:
    """
    The changed file in full, the change before this one, and the errors.
    """
    call, fn, old = _calls(region), diff.function, diff.old
    steps = []
    if diff.files and diff.files[0].status != "removed":
        steps.append((call("code", version_ref(fn), diff.files[0].path), "the first changed file in full"))
    earlier = [v.version for v in diff.versions if old and old.version.isdigit() and v.version.isdigit()
               and int(v.version) < int(old.version)]
    if earlier:
        steps.append((call("diff", fn.name, int(earlier[-1]), int(old.version)), "the change before this one"))
    steps.append((call("errors", fn.name), "whether its errors started with this change"))
    return _Next(steps)


def _diff_blocks(diff: VersionDiff, *, region: str | None = None) -> list[Any]:
    """
    diff()'s report, as blocks: what was compared, the cards, findings, the settings, libraries and files that
    changed, and each changed file line by line. region= goes into the next steps when the function isn't in the
    view's region.
    """
    fn, call = diff.function, _calls(region)
    after = version_label(fn)
    if diff.old is None:
        return [_Title(f"What changed in {fn.name}", f"Nothing to compare {after} with"), _Note(diff.note or ""),
                _Next([(call("code", version_ref(fn)), "its code as it is"),
                       (call("function_info", fn.name), "its versions and aliases")])]
    before = version_label(diff.old)
    sub = f"{_when(diff.old)} → {_when(fn)}"
    if diff.skipped:
        sub += f" · skipped {', '.join(diff.skipped)}: the same code"
    blocks: list[Any] = [_Title(f"What changed in {fn.name}", sub), _cards(diff, before, after)]
    if diff.note:
        blocks.append(_Note(diff.note))
    blocks.append(_Findings(diff_findings(diff), empty="Nothing in the change stands out: every Python file reads, and "
                                                      "no environment variable it reads was taken out."))
    blocks += _tables(diff, before, after)
    shown = diff.files[:_SHOWN_FILES]
    blocks += [_Diff(change, open=i == 0) for i, change in enumerate(shown)]
    if len(diff.files) > len(shown):
        blocks.append(_Note(f"{_plural(len(diff.files) - len(shown), 'more file')} changed: the table above lists "
                            f"them, and code('{version_ref(fn)}', 'path') shows one."))
    blocks.append(_next_steps(diff, region))
    return blocks
