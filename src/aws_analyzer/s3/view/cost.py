"""
The Cut cost commands.
"""

from __future__ import annotations

import json
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Text, _Title
from ..._kit.fmt import (
    Stat, _duration, _fmt_dt, _plural, _share, _tz_label, human_age, human_money, human_size, parse_size,
)
from ..._kit.render import _call
from ..._kit.view import _friendly_errors

from ..helpers import base_prefix, parse_s3_uri, relative_key, s3_uri
from ..models import ObjectInfo
from ..analysis.cost import object_monthly_cost
from ..analysis.duplicates import _folders_text, duplicate_findings, duplicate_folders
from .cells import _folder_label


class _CostCommands:
    """
    S3View's commands to cut cost: duplicates(), what_if() and uploads().
    """

    @_friendly_errors
    def duplicates(
        self,
        uri: str,
        *,
        method: str = "hash",
        min_size: int | str = 1,
        max_read: int | str | None = "10GB",
        limit: int | None = None,
    ) -> None:
        """
        Identical files under a prefix: what the copies take and cost, and folders that hold only copies.
        Matched by size + ETag, and by SHA-256 of the content where the ETags differ, reading at most max_read.
        method='strict' reads every file that shares its size; method='etag' reads none.
        """
        with (
            self._progress("Listing", unit="files") as tick,
            self._progress("Reading to compare", unit="B") as read_tick,
        ):
            report = self.core.find_duplicates(
                uri,
                min_size=min_size,
                method=method,
                max_read=max_read,
                limit=limit,
                progress=tick,
                read_progress=read_tick,
            )
        base = base_prefix(parse_s3_uri(report.uri)[1])
        smallest = parse_size(min_size) or 0
        if report.method == "etag":
            read = "matched on size + ETag from the listing, no file read"
        else:
            read = (
                f"{_plural(report.files_read, 'file')} read to compare contents ({human_size(report.bytes_read)} "
                f"in {_plural(report.requests, 'request')})"
            )
        sub = [
            f"{report.scanned.count:,} files listed ({human_size(report.scanned.size)})",
            read,
            f"files of at least {human_size(smallest)}" if smallest > 1 else "",
            f"took {_duration(report.scan_seconds)}",
        ]
        blocks: list[Any] = [
            _Title(f"Duplicate files in {report.uri}", " · ".join(filter(None, sub)))
        ]
        findings = _Findings(duplicate_findings(report))
        if not report.scanned.count:
            blocks.append(
                _Note(
                    "No files under this prefix. Check the prefix (keys are case-sensitive)."
                )
            )
            return self._show(blocks + [findings])
        blocks.append(
            _Cards(
                [
                    ("Duplicate groups", f"{len(report.groups):,}"),
                    ("Redundant copies", f"{report.copies:,}"),
                    ("Reclaimable", human_size(report.reclaimable)),
                    ("Est. saving / month", human_money(report.monthly_cost)),
                    (
                        "Files listed",
                        f"{report.scanned.count:,}{'+' if report.truncated else ''}",
                    ),
                    (
                        "Read to compare",
                        "nothing"
                        if report.method == "etag"
                        else human_size(report.bytes_read),
                    ),
                ]
            )
        )
        if not report.candidates.count:
            blocks.append(
                _Note(
                    "No two files have the same size, so none can be a copy of another.",
                    "ok",
                )
            )
        elif not report.groups and not report.not_compared.count:
            blocks.append(
                _Note(
                    f"No duplicates: the {_plural(report.candidates.count, 'file')} that share a size "
                    "with another file all have different contents.",
                    "ok",
                )
            )
        blocks.append(findings)
        if not report.groups:
            return self._show(blocks)

        def name(folder: str) -> str:
            return _folder_label(relative_key(folder, base))

        folders = duplicate_folders(report)
        rows = []
        for folder in folders:
            others = [f for f in folder.elsewhere if f != folder.folder]
            where = [_folders_text(others, base)] if others else []
            where += ["this folder"] if folder.folder in folder.elsewhere else []
            rows.append(
                [
                    name(folder.folder),
                    f"{folder.duplicated.count:,} of {folder.files:,}",
                    human_size(folder.duplicated.size),
                    "\n".join(where),
                ]
            )
        blocks.append(
            _Table(
                ["Folder", "Files with a copy", "Their size", "The copies are in"],
                rows,
                title="Folders with duplicated files",
                bars=[_share(f.duplicated.count, f.files) for f in folders],
                bar_label="% of the folder's files",
                path_cols=(0,),
            )
        )

        def listed(objects: list[ObjectInfo], most: int = 5) -> str:
            keys = [relative_key(o.key, base) for o in objects[:most]]
            return "\n".join(
                keys
                + (
                    [f"… and {len(objects) - most:,} more"]
                    if len(objects) > most
                    else []
                )
            )

        blocks.append(
            _Table(
                [
                    "Size",
                    "Files",
                    "Reclaimable",
                    "Est. $/month",
                    "Matched by",
                    "Keep",
                    "Copies",
                ],
                [
                    [
                        human_size(g.size),
                        f"{len(g.objects):,}",
                        human_size(g.reclaimable),
                        human_money(g.monthly_cost),
                        g.matched_by,
                        relative_key(g.keep.key, base),
                        listed(g.copies),
                    ]
                    for g in report.groups
                ],
                title=f"Duplicate groups, biggest saving first (cost {self._price_basis()})",
                path_cols=(5, 6),
            )
        )
        options = {
            "method": (method, "hash"),
            "min_size": (min_size, 1),
            "max_read": (max_read, "10GB"),
            "limit": (limit, None),
        }
        args = "".join(
            f", {key}={value!r}"
            for key, (value, default) in options.items()
            if value != default
        )
        blocks.append(
            _Text(
                f"report = ui.core.find_duplicates({report.uri!r}{args})\n"
                "df = report.to_df()              # one row per file: group, role, key, size, sha256, ...\n"
                "copies = df[df.role == 'copy']   # every file but the one to keep in each group",
                title="Get the list in pandas (nothing is deleted here)",
                code=True,
            )
        )
        copied = next((f for f in folders if f.all_copies), None) or next(
            (f for f in folders if f.outside), None
        )
        if copied:
            bucket = parse_s3_uri(report.uri)[0]
            other = next(f for f in copied.elsewhere if f != copied.folder)
            blocks.append(
                _Next(
                    [
                        (
                            _call(
                                "compare",
                                s3_uri(bucket, copied.folder),
                                s3_uri(bucket, other),
                            ),
                            "whether one folder is a full copy of the other",
                        )
                    ]
                )
            )
        self._show(blocks)

    @_friendly_errors
    def uploads(self, uri: str, *, with_sizes: bool = True) -> None:
        """
        Incomplete multipart uploads (billed, but invisible in normal listings).
        """
        with self._progress("Measuring uploads", unit="uploads") as tick:
            uploads = self.core.incomplete_uploads(
                uri, with_sizes=with_sizes, progress=tick
            )
        total = sum(u.size or 0 for u in uploads)
        cost = (
            (object_monthly_cost(total, "STANDARD", self.core.prices) or 0.0)
            if with_sizes
            else None
        )
        blocks: list[Any] = [
            _Title(f"Incomplete multipart uploads under {s3_uri(*parse_s3_uri(uri))}"),
            _Cards(
                [("Uploads", f"{len(uploads):,}")]
                + (
                    [
                        ("Stored parts", human_size(total)),
                        ("Est. cost / month (STANDARD rate)", human_money(cost)),
                    ]
                    if with_sizes
                    else []
                )
            ),
        ]
        if uploads:
            blocks.append(
                _Note(
                    "These parts cost storage until aborted; add an AbortIncompleteMultipartUpload "
                    "lifecycle rule to clean them up automatically.",
                    "warn",
                )
            )
        blocks.append(
            _Table(
                ["Key", f"Started ({_tz_label()})", "Age", "Parts", "Size", "Upload id"],
                [
                    [
                        u.key,
                        _fmt_dt(u.initiated),
                        human_age(u.initiated),
                        "-" if u.parts is None else str(u.parts),
                        human_size(u.size),
                        u.upload_id[:16] + "…",
                    ]
                    for u in uploads
                ],
                path_cols=(0,),
            )
        )
        self._show(blocks)

    @_friendly_errors
    def what_if(
        self,
        uri: str,
        *,
        move_after: int | dict[int, str] | None = None,
        to: str | None = None,
        delete_after: int | None = None,
        limit: int | None = None,
    ) -> None:
        """
        Preview a lifecycle rule before adding it: what it would move or delete today and the money
        saved, e.g. what_if(uri, move_after=30, to='STANDARD_IA') or what_if(uri, delete_after=365).
        """
        with self._progress() as tick:
            impact = self.core.simulate_lifecycle(
                uri,
                move_after=move_after,
                to=to,
                delete_after=delete_after,
                limit=limit,
                progress=tick,
            )
        moved = Stat(
            sum(st.count for st in impact.moves.values()),
            sum(st.size for st in impact.moves.values()),
        )
        savings = impact.monthly_savings
        cards = [
            (
                "Files checked",
                f"{impact.scanned.count:,}{'+' if impact.truncated else ''}",
            ),
            ("Would move", f"{moved.count:,} ({human_size(moved.size)})"),
            (
                "Would delete",
                f"{impact.expired.count:,} ({human_size(impact.expired.size)})",
            ),
            ("Cost now / month", human_money(impact.cost_before)),
            ("Cost after / month", human_money(impact.cost_after)),
            ("Saving / month", human_money(savings)),
            ("One-time cost", human_money(impact.one_time_cost)),
        ]
        if savings > 0 and impact.one_time_cost > 0:
            months = impact.one_time_cost / savings
            cards.append(
                (
                    "Pays for itself in",
                    "under a month" if months < 1 else f"{months:,.1f} months",
                )
            )
        blocks: list[Any] = [
            _Title(f"Lifecycle what-if for {impact.uri}", impact.describe()),
            _Cards(cards),
        ]
        if impact.truncated:
            blocks.append(
                _Note(
                    f"Scan stopped at limit={limit:,}; numbers cover only part of the prefix.",
                    "warn",
                )
            )
        if not (moved.count or impact.expired.count or impact.too_small.count):
            blocks.append(
                _Note("Nothing under this prefix is old enough for this rule today.")
            )
        elif savings <= 0:
            blocks.append(
                _Note(
                    "This rule would not lower the monthly bill for these files.",
                    "warn",
                )
            )
        if impact.too_small.count:
            blocks.append(
                _Note(
                    f"{_plural(impact.too_small.count, 'file')} ({human_size(impact.too_small.size)}) "
                    "are old enough to move but under 128 KB. S3 lifecycle doesn't move files that small, "
                    "so they stay where they are."
                )
            )
        if impact.early_removals.count:
            blocks.append(
                _Note(
                    f"{_plural(impact.early_removals.count, 'file')} would leave their storage class before "
                    "its minimum duration (30 days for IA, 90 for Glacier IR / Glacier, 180 for Deep Archive). "
                    "S3 bills the remaining days once; that's part of the one-time cost.",
                    "warn",
                )
            )
        if moved.count:
            blocks.append(
                _Note(
                    "Savings count storage only. Reading files in IA and Glacier Instant Retrieval adds a "
                    "retrieval fee per GB, and GLACIER / DEEP_ARCHIVE files must be restored before reading."
                )
            )
        if impact.expired.count:
            try:
                versioned = self.core.versioning_status(uri) != "Disabled"
            except (ClientError, BotoCoreError):
                versioned = False
            if versioned:
                blocks.append(
                    _Note(
                        "Versioning is on: a lifecycle delete keeps the old data as a noncurrent version, "
                        "billed until a NoncurrentVersionExpiration rule removes it. The saving above "
                        "needs that rule too.",
                        "warn",
                    )
                )
        parts = [(f"move to {cls}", st) for cls, st in impact.moves.items()]
        parts += [("delete", impact.expired)] if impact.expired.count else []
        parts += (
            [("stay (under 128 KB)", impact.too_small)]
            if impact.too_small.count
            else []
        )
        parts.append(
            (
                "no change",
                Stat(
                    impact.scanned.count - sum(st.count for _, st in parts),
                    impact.scanned.size - sum(st.size for _, st in parts),
                ),
            )
        )
        blocks.append(
            _Table(
                ["What happens", "Files", "Size"],
                [[label, f"{st.count:,}", human_size(st.size)] for label, st in parts],
                title=f"If the rule ran today (cost {self._price_basis()})",
                bars=[_share(st.count, impact.scanned.count) for _, st in parts],
                bar_label="% of files",
            )
        )
        blocks.append(
            _Text(
                json.dumps({"Rules": [impact.rule()]}, indent=2),
                title="The rule: put_bucket_lifecycle_configuration replaces ALL of a bucket's rules, "
                "so add this to the existing list",
                code=True,
            )
        )
        self._show(blocks)
