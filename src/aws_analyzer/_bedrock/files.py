"""
A knowledge base's files: links that open them (presigned_url), the list of them saved as a text file
(write_file_list, SavedFileList), and the links that download that list.
"""

from __future__ import annotations

import base64
import mimetypes
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable
from urllib.parse import quote

from .._kit.fmt import _as_int, _plural


_TEXT_EXTENSIONS = {"txt", "md", "markdown", "csv", "tsv", "log", "rst", "yaml", "yml", "xml", "jsonl"}


def _browser_type(name: str) -> str | None:
    """
    The Content-Type that makes a browser show a file in its tab rather than save it: 'a.pdf' ->
    'application/pdf', 'a.md' or 'a.csv' -> plain text, 'a.docx' -> None (Word and Excel files download). The
    _TEXT_EXTENSIONS are shown as text only when they're sent as text: a .md or .csv sent as its own type is saved.
    """
    name = name.rsplit("/", 1)[-1]
    if "." in name and name.rsplit(".", 1)[-1].lower() in _TEXT_EXTENSIONS:
        return "text/plain; charset=utf-8"
    guess = mimetypes.guess_type(name)[0] or ""
    if guess == "text/html":
        return "text/html; charset=utf-8"
    if guess in ("application/pdf", "application/json") or guess.split("/")[0] in ("image", "audio", "video"):
        return guess
    return None


FILE_LIST_LINKS = {
    "s3": "s3:// path",
    "https": "link to the file",
    "key": "path in the bucket",
}

_LINK_WORDS = {"s3://": "s3", "uri": "s3", "url": "https", "http": "https", "link": "https", "links": "https",
               "presigned": "https", "path": "key", "keys": "key"}


def parse_list_link(text: str) -> str:
    """
    link= for save_file_list(): 's3', 'https' or 'key' (FILE_LIST_LINKS: what it writes on each line), or a word
    for one ('url', 'path', the rest of _LINK_WORDS), in any case -> the key.
    """
    word = str(text or "").strip().lower()
    if word in FILE_LIST_LINKS:
        return word
    if word in _LINK_WORDS:
        return _LINK_WORDS[word]
    raise ValueError(f"link= is 's3' (each file's s3:// path), 'https' (a link that opens it) or 'key' (its path in "
                     f"the bucket); got {text!r}")


def file_list_lines(files: Iterable[Any], link: str = "s3",
                    sign: Callable[[str], str | None] | None = None) -> tuple[list[str], int]:
    """
    save_file_list()'s lines, one per S3 file in the order given (each file once): its s3:// path (link='s3'), its
    path in the bucket ('key') or sign(uri), a link that opens it ('https'). files: anything with a .uri (KBFile,
    KBDocument) or s3:// paths. -> (the lines, how many files have no line of that kind: a custom data source's
    documents have no s3:// path, a file deleted from S3 (state 'deleted') has no link to open, and sign may give
    none).
    """
    link = parse_list_link(link)
    lines: list[str] = []
    seen: set[str] = set()
    left_out = 0
    for f in files:
        uri = str(getattr(f, "uri", f) or "").strip()
        if uri in seen:
            continue
        seen.add(uri)
        line = None
        if uri.startswith("s3://"):
            if link == "s3":
                line = uri
            elif link == "key":
                line = uri[5:].partition("/")[2]
            elif getattr(f, "state", "") != "deleted" and sign is not None:
                line = sign(uri)
        if line:
            lines.append(line)
        else:
            left_out += 1
    return lines, left_out


def file_list_name(kb_name: str, state: str = "") -> str:
    """
    The name save_file_list() gives its file: 'support-docs-files.txt', 'support-docs-failed-files.txt'.
    """
    stem = re.sub(r"[^\w.-]+", "-", str(kb_name or "").strip()).strip("-.") or "knowledge-base"
    return f"{stem}{'-' + state if state else ''}-files.txt"


def _link_seconds(expires: Any) -> int:
    """
    expires= for a presigned link, checked: whole seconds, from 1 to 604,800 (7 days, the longest S3 allows).
    """
    seconds = _as_int(expires, "expires")
    if not 1 <= seconds <= 604_800:
        raise ValueError(f"expires is in seconds, from 1 to 604,800 (7 days, the longest S3 allows); got {seconds:,}")
    return seconds


def _link_life(expires: int) -> str:
    """
    How long a presigned link works, in the largest whole unit: '1 hour', '7 days', '90 minutes'.
    """
    for size, unit in ((86_400, "day"), (3600, "hour"), (60, "minute"), (1, "second")):
        if expires % size == 0:
            break
    return _plural(expires // size, unit)


def _looks_like_path(text: Any) -> bool:
    """
    Whether a knowledge base argument is a file or folder instead: names, IDs and ARNs have no dot, slash or ~
    (an ARN's slash aside).
    """
    text = str(text or "").strip()
    return bool(text) and not text.lower().startswith("arn:") and any(c in text for c in "./\\~")


def _list_target(path: str | None, default: str) -> str:
    """
    Where save_file_list() writes (absolute): `path`, from the notebook's folder, or `default` inside it when it's a
    folder; `default` beside the notebook without one.
    """
    if path is None or not str(path).strip():
        return os.path.abspath(default)
    text = str(path).strip()
    target = os.path.abspath(os.path.expanduser(text))
    if os.path.isdir(target) or text.endswith(("/", os.sep)):
        target = os.path.join(target, default)
    return target


def presigned_url(s3: Callable[[], Any], uri: str, *, page: int | None = None, expires: int = 3600) -> str | None:
    """
    A link that opens a file in a browser tab: for an s3:// file a presigned GetObject link, signed by the client
    s3() returns (SigV4, with its credentials: no AWS call), that works for `expires` seconds and makes the browser
    show a PDF (at `page`), picture, text or HTML file instead of saving it; an http(s) address as it is; None for
    anything else (a custom document's ID).
    """
    expires = _link_seconds(expires)
    uri = str(uri or "").strip()
    if uri.lower().startswith(("https://", "http://")):
        return uri
    bucket, _, key = uri[5:].partition("/") if uri.startswith("s3://") else ("", "", "")
    if not bucket or not key:
        return None
    params = {"Bucket": bucket, "Key": key, "ResponseContentDisposition": "inline"}
    shown = _browser_type(key)
    if shown:
        params["ResponseContentType"] = shown
    url = s3().generate_presigned_url("get_object", Params=params, ExpiresIn=expires)
    return f"{url}#page={page}" if page and shown == "application/pdf" else url


@dataclass
class SavedFileList:
    """
    A text file of files that save_file_list() wrote: one line per file, each one's s3:// path, its path in the
    bucket or a link that opens it (`link`, a FILE_LIST_LINKS key).

        path      where it was written (absolute)
        count     its lines: one per file
        size      bytes written
        replaced  a file of that name was there, and this one took its place
        left_out  files with no line of that kind (a custom document has no s3:// path)
        expires   link='https': how long each link works, in seconds
        text      what was written
    """

    path: str
    count: int
    link: str = "s3"
    size: int = 0
    replaced: bool = False
    left_out: int = 0
    expires: int | None = None
    text: str = field(default="", repr=False)

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def lines(self) -> list[str]:
        return self.text.splitlines()


def _notebook_href(path: str) -> str:
    """
    A saved file's path from the notebook's folder, as a link Jupyter downloads it from ('' outside that folder,
    which Jupyter may not serve, or on another drive, on Windows).
    """
    try:
        relative = os.path.relpath(path, os.getcwd())
    except ValueError:
        return ""
    if relative == ".." or relative.startswith((".." + os.sep, "../")) or os.path.isabs(relative):
        return ""
    return quote(relative.replace(os.sep, "/"))


def _data_href(text: str, limit: int = 1_000_000) -> str:
    """
    Text as a data: URL that a link can download (the explorer's widgets can't link to the notebook's files); ''
    when it's over `limit` bytes, which browsers may refuse in a link. A window passes _LIST_LINK_BYTES.
    """
    data = text.encode("utf-8")
    if len(data) > limit:
        return ""
    return "data:text/plain;charset=utf-8;base64," + base64.b64encode(data).decode("ascii")


def write_file_list(files: Iterable[Any], target: str, *, link: str = "s3",
                    sign: Callable[[str], str | None] | None = None, expires: int | None = None) -> SavedFileList:
    """
    Writes file_list_lines(files, link, sign) to `target` (absolute; its folder is made when it isn't there), a line
    each: to <target>.part, then renamed over a file of that name. An OSError becomes a ValueError that says why.
    """
    link = parse_list_link(link)
    lines, left_out = file_list_lines(files, link, sign)
    text = "".join(line + "\n" for line in lines)
    folder = os.path.dirname(target)
    saved = SavedFileList(target, len(lines), link, size=len(text.encode("utf-8")), replaced=os.path.exists(target),
                          left_out=left_out, expires=expires if link == "https" else None, text=text)
    part = target + ".part"
    try:
        os.makedirs(folder, exist_ok=True)
        with open(part, "w", encoding="utf-8", newline="\n") as out:
            out.write(text)
        os.replace(part, target)
    except OSError as exc:
        if os.path.exists(part):
            os.remove(part)
        raise ValueError(f"Couldn't save {os.path.basename(target)} in {folder}: {exc.strerror or exc}") from None
    return saved


_LIST_LINK_BYTES = 1_000_000
