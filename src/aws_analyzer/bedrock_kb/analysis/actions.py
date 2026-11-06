"""
What to do about a knowledge base's files, grouped by what does it: a sync per data source, files to fix, files to
convert. Also the sentence that sums the files up, the findings the groups leave, and where in a data source's
pipeline each problem happens.
"""

from __future__ import annotations

from typing import Callable

from ..._kit.fmt import _plural, human_age
from ..._kit.text import _clip

from ..helpers import MAX_FILE_SIZE, sync_command
from ..models import FileAction, FileInventory, KBFile
from .files import _failure_fix, _named, listing_findings, skip_reason, state_findings

FILES_SHOWN = 3

SYNCED_BY_A_SYNC = ("changed", "new", "deleted")
TO_FIX = ("failed", "partial")
TO_CONVERT = ("skipped", "ignored")
ACTED_ON = SYNCED_BY_A_SYNC + TO_FIX + TO_CONVERT

NEEDS_YOU = ("failed", "partial", "changed", "new", "deleted", "skipped")

_REASON_CHARS = 110
_GENERIC_FIX = "Fix or replace the file."
_SAYS_WHAT_TO_DO = ("use a", "use the", "try ", "convert", "save it")
_ACCESS_WORDS = ("denied", "permission", "not authorized", "kms")


def _reason(f: KBFile) -> str:
    """
    Bedrock's reason for a file, on one line, without its full stop and clipped.
    """
    return _clip(" ".join(f.reason.split()).rstrip("."), _REASON_CHARS)


def _sync_line(f: KBFile) -> str:
    """
    What a sync would change about a file, in a few words: 'changed in S3 5h ago: answers use the old version'.
    """
    if f.state == "changed":
        is_metadata = f.modified is None or (f.indexed is not None and f.modified <= f.indexed)
        if is_metadata:
            return "its metadata file changed: filters use the old values"
        return f"changed in S3 {human_age(f.modified)}: answers use the old version"
    if f.state == "new":
        when = f" {human_age(f.modified)}" if f.modified else " after the last sync"
        return f"added{when}: not searchable yet"
    if f.searchable:
        return "deleted from S3: answers can still cite it"
    return "deleted from S3: the next sync forgets it"


def _fix_line(f: KBFile) -> str:
    """
    What's wrong with a file that failed or was only partly indexed, and what usually fixes it.
    """
    reason = _reason(f)
    if f.state == "partial":
        return f"only partly indexed{': ' + reason if reason else ''}"
    said = f"{reason}." if reason else "Bedrock gave no reason."
    fix = _failure_fix(f.reason)
    already_says = any(words in reason.lower() for words in _SAYS_WHAT_TO_DO)
    if fix == _GENERIC_FIX or already_says:
        return said
    return f"{said} {fix}"


def _convert_line(f: KBFile) -> str:
    """
    Why Bedrock passed a file by: 'skipped: .pptx isn't a type Bedrock reads'.
    """
    if f.state == "skipped":
        return f"skipped: {skip_reason(f) or 'Bedrock kept no record of why'}"
    return f"ignored: {_reason(f) or 'usually because of its type'}"


def _listed(files: list[KBFile], line_of: Callable[[KBFile], str]) -> list[tuple[str, str]]:
    return [(f.name, line_of(f)) for f in files[:FILES_SHOWN]]


def _sync_action(inv: FileInventory, ds_id: str, files: list[KBFile], region: str) -> FileAction:
    """
    Syncing one data source: the files whose change it would bring into the index.
    """
    name = inv.sources.get(ds_id) or ds_id
    job = inv.last_sync.get(ds_id)
    if job is not None and job.started is not None:
        why = f"Searches use what it indexed {human_age(job.started)}."
    else:
        why = "It has never finished a sync, so none of these is searchable."
    ordered = sorted(files, key=lambda f: SYNCED_BY_A_SYNC.index(f.state))
    return FileAction("sync", f"Sync {name}", why, _listed(ordered, _sync_line), len(files), "warn",
                      sync_command(inv.kb_id, ds_id, region), name)


def _fix_action(files: list[KBFile], sources: dict[str, str]) -> FileAction:
    """
    Files that failed to index, or were only partly indexed: a sync alone won't change them.
    """
    ordered = sorted(files, key=lambda f: TO_FIX.index(f.state))
    has_failed = ordered[0].state == "failed"
    them = "it" if len(files) == 1 else "them"
    if has_failed:
        why = f"Nothing in {them} is searchable, and syncing again won't change that."
    else:
        why = f"Only part of {them} is searchable."
    names = {sources.get(f.data_source_id, "") for f in files}
    title = f"Fix {_plural(len(files), 'file')}, then sync"
    return FileAction("fix", title, why, _listed(ordered, _fix_line), len(files), "bad" if has_failed else "warn",
                      source=names.pop() if len(names) == 1 else "")


def _convert_action(files: list[KBFile], sources: dict[str, str]) -> FileAction:
    """
    Files Bedrock passes by, usually for their type: they need converting, or moving out of the data source.
    """
    ordered = sorted(files, key=lambda f: TO_CONVERT.index(f.state))
    has_skipped = ordered[0].state == "skipped"
    why = (f"Bedrock reads PDF, Word, Excel, CSV, HTML, Markdown and text files of up to "
           f"{MAX_FILE_SIZE // 2**20} MB, so every sync passes these by.")
    names = {sources.get(f.data_source_id, "") for f in files}
    title = f"Convert or remove {_plural(len(files), 'file')}"
    return FileAction("convert", title, why, _listed(ordered, _convert_line), len(files),
                      "warn" if has_skipped else "", source=names.pop() if len(names) == 1 else "")


def file_actions(inv: FileInventory, region: str = "") -> list[FileAction]:
    """
    What to do about a knowledge base's files, grouped by what does it -> [FileAction]: a sync of each data source
    whose files changed, were added or were deleted since its last sync (with the command), the files that failed to
    index to fix, and the files Bedrock can't read to convert. Each lists its first few files; [] when every file is
    indexed and up to date.
    """
    by_source: dict[str, list[KBFile]] = {}
    to_fix: list[KBFile] = []
    to_convert: list[KBFile] = []
    for f in inv.files:
        if f.state in SYNCED_BY_A_SYNC:
            by_source.setdefault(f.data_source_id, []).append(f)
        elif f.state in TO_FIX:
            to_fix.append(f)
        elif f.state in TO_CONVERT:
            to_convert.append(f)

    in_order = [ds_id for ds_id in inv.sources if ds_id in by_source]
    in_order += [ds_id for ds_id in by_source if ds_id not in inv.sources]
    actions = [_sync_action(inv, ds_id, by_source[ds_id], region) for ds_id in in_order]
    if to_fix:
        actions.append(_fix_action(to_fix, inv.sources))
    if to_convert:
        actions.append(_convert_action(to_convert, inv.sources))
    return actions


def _sync_clause(actions: list[FileAction], needs_you: int) -> str:
    """
    ', and one sync of docs-s3 fixes 3 of them', or '' when no sync would help.
    """
    syncs = [a for a in actions if a.kind == "sync"]
    fixed = sum(a.count for a in syncs if a.tone)
    if not syncs or not fixed:
        return ""
    names = " and ".join(a.source for a in syncs)
    doing = f"one sync of {names}" if len(syncs) == 1 else f"syncing {names}"
    if fixed >= needs_you:
        return f", and {doing} fixes {'it' if needs_you == 1 else 'them all'}"
    return f", and {doing} fixes {fixed:,} of them"


def files_verdict(inv: FileInventory, actions: list[FileAction] | None = None) -> str:
    """
    A knowledge base's files in one sentence: how many are indexed and up to date, how many need you, and how many
    of those a sync fixes. 'Every file is ...' when nothing needs doing; '' when it has no files listed.
    actions: file_actions(inv), when already made.
    """
    total = len(inv.files)
    if not total:
        return ""
    actions = file_actions(inv) if actions is None else actions
    current = sum(1 for f in inv.files if f.state == "indexed")
    needs_you = sum(1 for f in inv.files if f.state in NEEDS_YOU)
    if needs_you == 0 and current == total:
        return "Its one file is indexed and up to date." if total == 1 else (
            f"All {total:,} files are indexed and up to date.")
    head = f"{current:,} of {_plural(total, 'file')} {'is' if current == 1 else 'are'} indexed and up to date."
    if needs_you == 0:
        return f"{head} None of the others needs you."
    verb = "needs" if needs_you == 1 else "need"
    return f"{head} {needs_you:,} {verb} you{_sync_clause(actions, needs_you)}."


def other_findings(inv: FileInventory) -> list[tuple[str, str]]:
    """
    inventory_findings() without the files file_actions() covers -> [(level, message)]: files being indexed or not
    checked, and what couldn't be listed.
    """
    left = [f for f in inv.files if f.state not in ACTED_ON]
    return state_findings(left, limit=inv.limit) + listing_findings(inv)


def _is_access_problem(f: KBFile) -> bool:
    """
    Whether Bedrock's reason for a failed file says the knowledge base's role couldn't read it.
    """
    text = f.reason.lower()
    return any(word in text for word in _ACCESS_WORDS)


def _waiting_counts(waiting: list[KBFile]) -> str:
    """
    '1 changed, 1 new, 1 deleted': the files a sync would change, by state, leaving out the states none is in.
    """
    counts = [(state, sum(1 for f in waiting if f.state == state)) for state in SYNCED_BY_A_SYNC]
    return ", ".join(f"{count:,} {state}" for state, count in counts if count)


def stage_problems(files: list[KBFile]) -> dict[str, list[tuple[str, str, str]]]:
    """
    Where in a data source's pipeline its files' problems happen -> {stage: [(tone, headline, detail)]}, stage one
    of 'Source', 'Parser' and 'Last sync': files waiting for a sync and files Bedrock passes by at the source, files
    that failed to parse at the parser (or at the source when the role can't read them), and partly indexed files
    at the last sync.
    """
    problems: dict[str, list[tuple[str, str, str]]] = {}

    def pin(stage: str, tone: str, what: str, picked: list[KBFile], detail: str) -> None:
        if picked:
            problems.setdefault(stage, []).append((tone, f"{_plural(len(picked), 'file')} {what}", detail))

    waiting = [f for f in files if f.state in SYNCED_BY_A_SYNC]
    failed = [f for f in files if f.state == "failed"]
    unread = [f for f in failed if _is_access_problem(f)]
    unparsed = [f for f in failed if not _is_access_problem(f)]
    skipped = [f for f in files if f.state == "skipped"]
    partial = [f for f in files if f.state == "partial"]

    pin("Source", "warn", "waiting for a sync", waiting, _waiting_counts(waiting))
    pin("Source", "bad", "the role can't read", unread, _named(unread) if unread else "")
    pin("Source", "warn", "skipped", skipped, _named(skipped) if skipped else "")
    pin("Parser", "bad", "failed here", unparsed, _named(unparsed) if unparsed else "")
    pin("Last sync", "warn", "only partly indexed", partial, _named(partial) if partial else "")
    return problems
