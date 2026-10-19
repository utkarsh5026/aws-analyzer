"""
What fills the disk: the caches and trash you can clear, the commands that clear them, the
folder tree, and disk()'s findings.
"""

from __future__ import annotations

import math
import os
import re
from datetime import datetime

from ..._kit.fmt import _plural, _utcnow, human_money, human_size

from ..prices import SAGEMAKER_PRICES
from ..helpers import GB, MB, shell_path
from ..models import Clearable, DiskEntry, DiskReport, Environment
from .common import _full


# Caches and trash a disk() walk recognizes: (pattern on the folder's path, what, why it's safe, command).
# {path} is the folder, {root} the folder measured; the tool shows the command and never runs it.
_CLEARABLE = [
    (
        re.compile(r"/\.Trash-\d+$|/\.local/share/Trash$"),
        "Jupyter trash",
        "Files you deleted in Jupyter. They take up the disk until the trash is emptied.",
        "rm -rf {path}/*",
    ),
    (
        re.compile(r"/\.cache/pip$"),
        "pip cache",
        "Package downloads pip keeps to reinstall faster.",
        "pip cache purge",
    ),
    (
        re.compile(r"/\.conda/pkgs$|/(?:ana|mini)conda3?/pkgs$"),
        "conda package cache",
        "Package files conda keeps after installing them.",
        "conda clean --all --yes",
    ),
    (
        re.compile(r"/\.cache/huggingface$"),
        "Hugging Face cache",
        "Models and datasets transformers and datasets downloaded; they download again when needed.",
        "huggingface-cli delete-cache",
    ),
    (
        re.compile(r"/\.cache/torch$"),
        "PyTorch cache",
        "Pretrained weights torch.hub and torchvision downloaded; they download again when needed.",
        "rm -rf {path}",
    ),
    (
        re.compile(r"/\.ipynb_checkpoints$"),
        "notebook checkpoints",
        "Jupyter's autosaved copies of notebooks, big when notebooks hold large outputs.",
        "find {root} -name .ipynb_checkpoints -type d -prune -exec rm -rf {{}} +",
    ),
]


def clearable_kind(path: str) -> int | None:
    """
    Index into _CLEARABLE of the cache or trash folder at `path`, or None.
    """
    for i, (pattern, *_rest) in enumerate(_CLEARABLE):
        if pattern.search(path.replace(os.sep, "/")):
            return i
    return None


def clear_command(item: Clearable, root: str, home: str | None = None) -> str:
    """
    The shell command that empties one kind of cache or trash (shown, never run).
    """
    template = next((t for _p, what, _w, t in _CLEARABLE if what == item.what), "")
    where = shell_path(root, home)
    if "{path}" not in template:
        return template.format(root=where)
    return "; ".join(
        template.format(path=shell_path(p, home), root=where) for p in item.paths[:3]
    )


def folder_tree(
    report: DiskReport, *, top: int = 15, min_share: float = 0.1
) -> list[tuple[int, DiskEntry]]:
    """
    The folders worth showing, as (depth, entry) in tree order: the `top` biggest top-level folders, and inside
    any folder holding at least `min_share` of the total, its biggest few (5, then 3).
    """
    total = report.total.size or 1
    children: dict[str, list[DiskEntry]] = {}
    for entry in report.folders.values():
        parent = entry.path.rsplit("/", 1)[0] if "/" in entry.path else ""
        children.setdefault(parent, []).append(entry)
    rows: list[tuple[int, DiskEntry]] = []

    def walk(parent: str, depth: int, limit: int) -> None:
        for entry in sorted(children.get(parent, []), key=lambda e: -e.size)[:limit]:
            rows.append((depth, entry))
            if depth < 2 and entry.size / total >= min_share:
                walk(entry.path, depth + 1, 5 if depth == 0 else 3)

    walk("", 0, top)
    return rows


def disk_findings(
    report: DiskReport,
    env: Environment | None = None,
    prices: dict[str, float] | None = None,
    now: datetime | None = None,
) -> list[tuple[str, str]]:
    """
    Plain-language notes about what fills a folder and its disk, each with what to do -> [(level, message)].
    env: where this runs, to show the right command for a bigger volume.
    """
    prices = SAGEMAKER_PRICES if prices is None else prices
    found: list[tuple[str, str]] = []
    volume = report.volume
    full = volume is not None and not volume.elastic and _full(volume.share)
    if full and volume is not None:  # full implies a volume; type checkers need it spelled out
        biggest = max(
            (e for e in report.folders.values() if "/" not in e.path),
            key=lambda e: e.size,
            default=None,
        )
        text = (
            f"The disk is {volume.share:.0%} full: {human_size(volume.free)} free of {human_size(volume.total)}. "
            "When it fills, saving notebooks and installing packages fail."
        )
        if biggest is not None and biggest.size:
            where = os.path.join(report.path, biggest.path)
            text += f" The biggest folder here is {biggest.path} ({human_size(biggest.size)}): disk({where!r})."
        size = max(
            math.ceil(volume.total / GB * 2 / 10) * 10, 10
        )  # twice the size, in whole tens of GB
        if env is not None and env.kind == "notebook instance" and env.name:
            extra = (size - volume.total / GB) * prices.get("notebook_storage", 0.0)
            text += (
                f" To make it {size} GB (about {human_money(extra)}/month more; it can't shrink later), stop the "
                f"notebook instance, then: aws sagemaker update-notebook-instance --notebook-instance-name "
                f"{env.name} --volume-size-in-gb {size}"
            )
        elif env is not None and env.kind == "studio" and env.space and env.domain_id:
            extra = (size - volume.total / GB) * prices.get("space_storage", 0.0)
            text += (
                f" To make it {size} GB (about {human_money(extra)}/month more, up to the domain's maximum), "
                f"stop the app, then: aws sagemaker update-space --domain-id {env.domain_id} --space-name "
                f"{env.space} --space-settings SpaceStorageSettings={{EbsStorageSettings={{EbsVolumeSizeInGb="
                f"{size}}}}}"
            )
        found.append(("warn", text))
    clearable = sum(c.size for c in report.clearable)
    if clearable >= 100 * MB:
        parts = ", ".join(
            f"{c.what} {human_size(c.size)}"
            for c in sorted(report.clearable, key=lambda c: -c.size)
        )
        found.append(
            (
                "warn" if full else "info",
                (
                    f"{human_size(clearable)} is caches and trash that are safe to empty ({parts}). The command for each "
                    "is in the table below."
                ),
            )
        )
    now = now or _utcnow()
    stale = [
        e
        for e in report.largest
        if e.size >= GB and e.modified and (now - e.modified).days >= 90
    ]
    if stale:
        size = sum(e.size for e in stale)
        on_disk = prices.get(
            (
                "notebook_storage"
                if env is not None and env.kind == "notebook instance"
                else "space_storage"
            ),
            0.0,
        )
        in_s3 = prices.get("s3_storage", 0.0)
        example = max(stale, key=lambda e: e.size)
        found.append(
            (
                "info",
                (
                    f"{_plural(len(stale), 'file')} over 1 GB {'hasn' if len(stale) == 1 else 'haven'}'t changed in 90 days "
                    f"({human_size(size)}; the biggest is {example.path}, {human_size(example.size)}). Moving them to S3 "
                    f"(aws s3 cp, then delete the copy here) frees {human_size(size)} on this disk, and S3 keeps them for "
                    f"about {human_money(size / GB * in_s3)}/month rather than {human_money(size / GB * on_disk)}/month "
                    "here."
                ),
            )
        )
    if report.truncated:
        found.append(
            (
                "info",
                (
                    f"Stopped after {report.total.files:,} files, so the sizes are at least these. "
                    f"disk({report.path!r}, limit=None) measures everything."
                ),
            )
        )
    return found
