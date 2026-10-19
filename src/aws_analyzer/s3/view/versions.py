"""
The Versions and deleted files commands.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.blocks import _Cards, _Next, _Note, _Table, _Text, _Title
from ..._kit.fmt import _fmt_dt, _plural, _share, _tz_label, human_age, human_money, human_size, parse_time
from ..._kit.render import _call
from ..._kit.view import _friendly_errors

from ..helpers import base_prefix, parse_s3_uri, relative_key, s3_uri


class _VersionCommands:
    """
    S3View's commands about versions and deleted files: versions(), history() and deleted().
    """

    @_friendly_errors
    def versions(self, uri: str, *, limit: int | None = None) -> None:
        """
        Current vs noncurrent versions and delete markers (hidden storage in versioned buckets).
        """
        with self._progress("Listing versions") as tick:
            v = self.core.version_stats(uri, limit=limit, progress=tick)
        blocks: list[Any] = [
            _Title(f"Versions under {v.uri}"),
            _Cards(
                [
                    ("Current objects", f"{v.current.count:,}"),
                    ("Current size", human_size(v.current.size)),
                    ("Noncurrent versions", f"{v.noncurrent.count:,}"),
                    ("Noncurrent size", human_size(v.noncurrent.size)),
                    ("Delete markers", f"{v.delete_markers:,}"),
                    ("Deleted keys (still billed)", f"{v.deleted_keys:,}"),
                    ("Noncurrent est. cost / month", human_money(v.noncurrent_cost)),
                ]
            ),
        ]
        if v.truncated:
            blocks.append(
                _Note(
                    f"Stopped at limit={limit:,} versions; numbers are partial.", "warn"
                )
            )
        if v.noncurrent.size:
            extra = _share(v.noncurrent.size, v.current.size)
            blocks.append(
                _Note(
                    f"Old versions add {human_size(v.noncurrent.size)} ({extra:.0%} on top of current data). "
                    "A NoncurrentVersionExpiration lifecycle rule would clean them up.",
                    "warn" if extra > 0.25 else "info",
                )
            )
        if v.top_noncurrent:
            blocks.append(
                _Table(
                    ["Key", "Old versions", "Old versions size"],
                    [
                        [k, f"{st.count:,}", human_size(st.size)]
                        for k, st in v.top_noncurrent
                    ],
                    title="Keys with the most noncurrent data",
                    path_cols=(0,),
                )
            )
        steps = (
            [
                (
                    _call("deleted", v.uri),
                    f"the {_plural(v.deleted_keys, 'deleted file')} you may be able to restore",
                )
            ]
            if v.deleted_keys
            else []
        )
        steps += (
            [
                (
                    _call(
                        "history",
                        s3_uri(parse_s3_uri(v.uri)[0], v.top_noncurrent[0][0]),
                    ),
                    "every version of the key with the most old data",
                )
            ]
            if v.top_noncurrent
            else []
        )
        blocks.append(_Next(steps))
        self._show(blocks)

    @_friendly_errors
    def history(self, uri: str) -> None:
        """
        Version history of a single object.
        """
        history = self.core.object_versions(uri)
        rows = [
            [
                "delete marker" if v.is_delete_marker else human_size(v.size),
                _fmt_dt(v.last_modified),
                human_age(v.last_modified),
                "latest" if v.is_latest else "",
                v.version_id,
            ]
            for v in history
        ]
        self._show(
            [
                _Title(
                    f"History of {s3_uri(*parse_s3_uri(uri))}",
                    f"{len(history)} versions",
                ),
                _Table(["Size", f"Modified ({_tz_label()})", "Age", "", "Version id"], rows),
            ]
        )

    @_friendly_errors
    def deleted(
        self, uri: str, *, deleted_after: Any = None, limit: int | None = None
    ) -> None:
        """
        Deleted files you can still bring back (versioned buckets), most recent first,
        e.g. deleted(uri, deleted_after='7d'). Shows the call that restores one; never changes anything.
        """
        with self._progress("Listing versions") as tick:
            d = self.core.deleted_files(
                uri, deleted_after=deleted_after, limit=limit, progress=tick
            )
        bucket, prefix = parse_s3_uri(d.uri)
        restorable = [f for f in d.files if f.restorable]
        since = (
            f"deleted since {_fmt_dt(parse_time(deleted_after), zone=True)}"
            if deleted_after is not None
            else ""
        )
        blocks: list[Any] = [
            _Title(f"Deleted files under {d.uri}", since),
            _Cards(
                [
                    ("Deleted files", f"{len(d.files):,}"),
                    ("Can be restored", f"{len(restorable):,}"),
                    (
                        "Size to restore",
                        human_size(
                            sum(f.last_version.size for f in d.files if f.last_version)
                        ),
                    ),
                    (
                        "Old versions kept",
                        human_size(sum(f.old_versions.size for f in d.files)),
                    ),
                    (
                        "Their est. cost / month",
                        human_money(sum(f.monthly_cost for f in d.files)),
                    ),
                ]
            ),
        ]
        if d.truncated:
            blocks.append(
                _Note(
                    f"Stopped at limit={limit:,} versions; the list is partial.", "warn"
                )
            )
        if not d.files:
            try:
                status = self.core.versioning_status(bucket)
            except (ClientError, BotoCoreError):
                status = None
            if status == "Disabled":
                blocks.append(
                    _Note(
                        "Versioning is off for this bucket, so deleted files are gone for good. "
                        "Turning versioning on protects future deletes.",
                        "warn",
                    )
                )
            else:
                blocks.append(_Note("No deleted files here."))
            return self._show(blocks)
        if len(restorable) < len(d.files):
            blocks.append(
                _Note(
                    f"{_plural(len(d.files) - len(restorable), 'delete marker')} have no versions left, "
                    "so there's nothing to restore. A lifecycle rule with ExpiredObjectDeleteMarker "
                    "removes them."
                )
            )
        base = base_prefix(prefix)
        blocks.append(
            _Table(
                [
                    "Key",
                    f"Deleted ({_tz_label()})",
                    "When",
                    "Last version size",
                    "Versions kept",
                    "Delete marker version id",
                ],
                [
                    [
                        relative_key(f.key, base),
                        _fmt_dt(f.deleted),
                        human_age(f.deleted),
                        human_size(f.last_version.size)
                        if f.last_version
                        else "nothing to restore",
                        f"{f.old_versions.count:,}",
                        f.marker_version_id,
                    ]
                    for f in d.files
                ],
                title="Deleted files",
                path_cols=(0,),
            )
        )
        if restorable:
            example = restorable[0]
            blocks.append(
                _Text(
                    "import boto3\n\n"
                    "# Deleting the delete marker brings the last version back (needs s3:DeleteObjectVersion).\n"
                    f"boto3.client('s3').delete_object(\n    Bucket={bucket!r},\n    Key={example.key!r},\n"
                    f"    VersionId={example.marker_version_id!r},\n)",
                    title="How to restore a file",
                    code=True,
                )
            )
            blocks.append(
                _Next(
                    [
                        (
                            _call("history", s3_uri(bucket, example.key)),
                            "every version of that file, to pick the one to bring back",
                        )
                    ]
                )
            )
        self._show(blocks)
