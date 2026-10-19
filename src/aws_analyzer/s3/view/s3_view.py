"""
S3View, made of its commands' mixins: its look, help()'s groups and the plumbing they share.
"""

from __future__ import annotations

from ..._kit.view import _BaseView

from ..readers.formats import _DATA_ERRORS
from ..analyzer.s3_analyzer import S3Analyzer
from ..style import _render_html
from .buckets import _BucketCommands
from .folders import _FolderCommands
from .cost import _CostCommands
from .versions import _VersionCommands
from .files import _FileCommands
from .downloads import _DownloadCommands


class S3View(_BucketCommands, _FolderCommands, _CostCommands, _VersionCommands, _FileCommands, _DownloadCommands, _BaseView):
    """
    Notebook UI over S3Analyzer. Each method renders a report and returns nothing;
    for the underlying data call the same-named method on `view.core` (an S3Analyzer).

    mode: 'auto' (HTML inside Jupyter, text elsewhere), 'html' or 'text'.
    max_rows: default cap for long tables (set to 0 for no cap).
    progress: 'auto' (a tqdm bar while long commands run, when tqdm is installed; else a line with the count,
    rate and time left), 'plain' (always that line) or 'off'.
    downloads: the folder download() and download_zip() save into without a path (it sets core.downloads):
    's3-downloads' next to the notebook by default; downloads() lists it and clean_downloads() empties it.
    """

    _render_html = staticmethod(_render_html)  # its look: the stylesheet, root class and badge
    _PROGRESS = ("Scanning", "objects")  # what _progress() says when it's given no label or unit
    _DATA_ERRORS = _DATA_ERRORS  # what a broken or mislabeled file raises while it's decoded, shown as a note
    _GROUPS = {  # help() lists the commands in these groups, in this order
        "🪣 Buckets": ("buckets", "overview", "bucket_info", "policy"),
        "📂 Explore a folder": (
            "ls",
            "tree",
            "summary",
            "find",
            "file_details",
            "largest",
            "newest",
            "oldest",
            "compare",
        ),
        "💰 Cut cost": ("duplicates", "what_if", "uploads"),
        "🕘 Versions and deleted files": ("versions", "history", "deleted"),
        "📄 Open a file": ("head", "preview", "document", "link"),
        "⬇ Download": ("download", "download_zip", "downloads", "clean_downloads"),
        "❓ Help": ("help",),
    }
    _START = (
        ("overview()", "every bucket: size, cost and warnings"),
        ("summary('s3://bucket/prefix/')", "what's in a folder and what it costs"),
    )

    def __init__(
        self,
        core: S3Analyzer | None = None,
        *,
        mode: str = "auto",
        max_rows: int = 50,
        progress: str = "auto",
        downloads: str | None = None,
    ):
        super().__init__(mode=mode, max_rows=max_rows, progress=progress)
        self.core: S3Analyzer = core or S3Analyzer()
        if downloads is not None:
            self.core.downloads = downloads

    # ------------------------------------------------------------------ plumbing

    def _explain(self, code: str, message: str) -> str:
        """
        A missing object reads as "object not found".
        """
        return "object not found" if code in ("404", "NoSuchKey", "NotFound") else message

    def _price_basis(self) -> str:
        return f"estimated at {super()._price_basis()}, storage only"
