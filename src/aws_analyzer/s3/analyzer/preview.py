"""
S3Analyzer's preview(), one method per format.
"""

from __future__ import annotations

import io
import json
import mimetypes
import zipfile
from typing import Any

from ..._kit.deps import _require
from ..._kit.fmt import _plural, human_size
from ..._kit.render import _call

from ..helpers import KB, MB, _PREVIEW_PAGES, _count_words, _restore_note, parse_s3_uri, s3_uri
from ..prices import ARCHIVE_CLASSES
from ..models import Document, Preview
from ..readers.formats import _IMAGE_MIMES, _READ_ERRORS, _looks_binary, detect_format, sniff_format
from ..readers.avro import _avro_type_name
from ..readers.office import _OFFICE_PARTS, _old_office_note, parse_docx, parse_pptx
from ..readers.pdf import parse_pdf, render_pdf_pages
from ..readers.data import _columnar_info, _json_lines_start, _json_prefix, _json_shape, _npy_header, _read_arrow_table


class _PreviewReads:
    """
    S3Analyzer's preview(): what a file holds, read by its format, as a Preview the View draws.
    """

    def preview(self, uri: str, n: int = 20, *, max_bytes: int = 512 * KB) -> Preview:
        """
        Best-effort look at an object: a DataFrame for tables (csv, tsv, psv, json, jsonl, parquet, orc,
        feather, avro, excel, npy), the files in an archive (zip, tar, tar.gz, model.tar.gz, npz, PyTorch
        checkpoints), tensors in a .safetensors file, notebook cells, parsed JSON, text lines, an image,
        an audio / video player or PDF link, or a binary sample. Files without an extension are recognised
        by their first bytes. Downloads only what it needs.
        """
        bucket, key = parse_s3_uri(uri)
        meta = self.client.head_object(Bucket=bucket, Key=key)
        fmt, compression = detect_format(key)
        p = Preview(
            uri=s3_uri(bucket, key),
            kind="text",
            size=meta["ContentLength"],
            format=fmt,
            compression=compression,
            content_type=meta.get("ContentType"),
        )
        storage_class = meta.get("StorageClass", "STANDARD")
        if (
            storage_class in ARCHIVE_CLASSES
            and 'ongoing-request="false"' not in meta.get("Restore", "")
        ):
            p.kind, p.note = "unavailable", _restore_note(bucket, key, storage_class, meta.get("Restore", ""))
            return p
        sniffed = False
        if p.size:
            fmt, p.compression, sniffed, p.note = self._confirm_format(
                bucket, key, fmt, compression
            )
        if fmt is None:
            content_type = p.content_type or ""
            fmt = next(
                (
                    kind
                    for kind in ("image", "audio", "video")
                    if content_type.startswith(kind + "/")
                ),
                "pdf" if content_type == "application/pdf" else None,
            )
        p.format = fmt
        codec = p.compression or ""
        try:
            handler = getattr(self, f"_preview_{fmt}", None) if fmt else None
            if handler is None or not handler(p, uri, n, codec):
                self._preview_as_text(p, uri, n, codec, max_bytes, sniffed)
        except _READ_ERRORS as exc:  # broken or mislabeled file, missing optional package: show raw content
            try:
                data, p.truncated = self._read_head(uri, min(max_bytes, 64 * KB), codec)
            except _READ_ERRORS:
                data, p.truncated = self._read_head(uri, min(max_bytes, 64 * KB), "")
            p.kind = "binary" if _looks_binary(data) else "text"
            p.data = (
                data[:512]
                if p.kind == "binary"
                else data.decode("utf-8", "replace").splitlines()[:n]
            )
            p.note = " ".join(
                filter(
                    None,
                    [p.note, f"Couldn't read it as {fmt or 'a known format'}: {exc}"],
                )
            )
        return p

    def _confirm_format(
        self, bucket: str, key: str, fmt: str | None, codec: str | None
    ) -> tuple[str | None, str | None, bool, str]:
        """
        Check the name's guess against the first bytes -> (format, compression, sniffed, note).
        """
        uri = s3_uri(bucket, key)
        sniffed_fmt, sniffed_codec = sniff_format(self.read_bytes(uri, 0, 511))
        note = ""
        if codec and sniffed_codec != codec:
            note = f"The name says .{codec} but the content isn't {codec}-compressed; reading it as-is."
            codec = None
        elif sniffed_codec and not codec:
            codec = sniffed_codec
        if (
            fmt is None and codec
        ):  # 'logs.gz', or a compressed file with no extension: look inside
            try:
                sniffed_fmt = sniff_format(self._read_head(uri, 512, codec)[0])[0]
            except _READ_ERRORS:
                sniffed_fmt = None
        return fmt or sniffed_fmt, codec, fmt is None and sniffed_fmt is not None, note

    # One _preview_<format> per format; each fills in the Preview and returns True (False = show as text).

    def _preview_csv(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        p.kind, p.data = (
            "table",
            self.read_df(uri, nrows=n, fmt=p.format, compression=codec),
        )
        return True

    _preview_tsv = _preview_psv = _preview_jsonl = _preview_csv

    def _preview_parquet(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        p.kind, p.data = (
            "table",
            self.read_df(uri, nrows=n, fmt="parquet", compression=codec),
        )
        if not codec:
            p.info = self.parquet_info(uri)
        return True

    def _preview_orc(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        bucket, key = parse_s3_uri(uri)
        fmt = p.format or "orc"  # preview() sets it before picking this handler
        with self._random_access(bucket, key, codec) as handle:
            p.info = _columnar_info(fmt, handle)
            handle.seek(0)
            p.kind, p.data = (
                "table",
                _read_arrow_table(fmt, handle, n, None).to_pandas(),
            )
        return True

    _preview_arrow = _preview_orc

    def _preview_avro(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        schema, avro_codec, records, _ = self._avro(uri, n, codec)
        fields = schema.get("fields", []) if isinstance(schema, dict) else []
        p.info = {
            "codec": avro_codec,
            "columns": [(f["name"], _avro_type_name(f["type"])) for f in fields],
        }
        pd = _require("pandas", "Table preview")
        rows = (
            records
            if all(isinstance(r, dict) for r in records)
            else [{"value": r} for r in records]
        )
        p.kind, p.data = (
            "table",
            pd.json_normalize(rows)
            if rows
            else pd.DataFrame(columns=[f["name"] for f in fields]),
        )
        return True

    def _preview_excel(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        pd = _require("pandas", "Reading Excel")
        bucket, key = parse_s3_uri(uri)
        with (
            self._random_access(bucket, key, codec) as handle,
            pd.ExcelFile(handle) as workbook,
        ):
            sheets = workbook.sheet_names
            p.info = {"sheets": sheets, "sheet": sheets[0]}
            p.kind, p.data = "table", workbook.parse(sheets[0], nrows=n)
        return True

    def _preview_npy(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        bucket, key = parse_s3_uri(uri)
        with self._random_access(bucket, key, codec, buffer_size=64 * KB) as handle:
            shape, fortran, dtype = _npy_header(handle)
        p.info = {"shape": shape, "dtype": str(dtype)}
        array = self.read_npy(uri, nrows=n, compression=codec)
        if array.ndim <= 2:
            p.kind, p.data = (
                "table",
                _require("pandas", "Table preview").DataFrame(array),
            )
        else:
            p.kind, p.data = "text", repr(array[: min(n, 2)]).splitlines()[:n]
        return True

    def _preview_zip(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        listing = self.list_archive(uri, limit=max(n, 200), compression=codec)
        names = {e.name for e in listing.entries}
        kind = next(
            (kind for part, kind in _OFFICE_PARTS.items() if part in names), None
        )
        if kind and p.format in (
            "zip",
            None,
        ):  # a Word / PowerPoint / Excel file without its extension
            p.format = kind
            return getattr(self, f"_preview_{kind}")(p, uri, n, codec)
        p.kind = "listing"
        p.data = [
            {"name": e.name, "size": e.size, "modified": e.modified}
            for e in listing.entries
            if not e.is_dir
        ]
        files = sum(not e.is_dir for e in listing.entries)
        p.info = {
            "files": f"{files:,}" + ("" if listing.complete else "+"),
            "unpacked_size": sum(e.size for e in listing.entries),
        }
        if not listing.complete:
            read = (
                f" after reading {human_size(listing.bytes_read)}"
                if listing.bytes_read
                else ""
            )
            p.note = f"Listing stopped{read}; there are more files (S3Analyzer.list_archive has limit= / max_bytes=)."
        return True

    _preview_tar = _preview_zip

    def _preview_torch(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        head = self.read_bytes(uri, 0, 3)
        if head.startswith(b"PK"):
            self._preview_zip(p, uri, n, codec)
        else:
            p.kind, p.data = "binary", self.read_bytes(uri, 0, 511)
        p.note = (
            "PyTorch checkpoint. Not loaded: torch.load unpickles, which can run code from the file. "
            "If you trust it: torch.load(ui.core.open(uri), weights_only=True)."
        )
        return True

    def _preview_pickle(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        p.kind, p.data = "binary", self.read_bytes(uri, 0, 511)
        p.note = (
            "Pickle file. Not opened: unpickling can run code from the file. "
            "If you trust it: pickle.load(ui.core.open(uri))."
        )
        return True

    def _preview_npz(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        bucket, key = parse_s3_uri(uri)
        arrays = []
        with (
            self._random_access(bucket, key, codec, buffer_size=256 * KB) as handle,
            zipfile.ZipFile(handle) as archive,
        ):
            for info in archive.infolist()[: max(n, 200)]:
                with archive.open(info) as member:
                    shape, _, dtype = _npy_header(io.BytesIO(member.read(16 * KB)))
                arrays.append(
                    {
                        "array": info.filename.removesuffix(".npy"),
                        "shape": shape,
                        "dtype": str(dtype),
                        "size": info.file_size,
                    }
                )
        p.kind, p.data, p.info = "listing", arrays, {"arrays": len(arrays)}
        return True

    def _preview_safetensors(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        details = self.safetensors_info(uri)
        tensors = details["tensors"]
        p.kind, p.data = "listing", tensors[: max(n, 100)]
        p.info = {
            "tensors": len(tensors),
            "parameters": sum(t["parameters"] for t in tensors),
            "dtypes": sorted({t["dtype"] for t in tensors if t["dtype"]}),
            "metadata": details["metadata"],
        }
        return True

    def _preview_notebook(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        if p.size > 50 * MB:
            raise ValueError("notebook is over 50 MB")
        notebook = self.read_json(uri, compression=codec)
        cells, meta = notebook.get("cells", []), notebook.get("metadata", {})
        rows = []
        for i, cell in enumerate(cells[: max(n, 50)]):
            source = cell.get("source", "")
            lines = (
                "".join(source) if isinstance(source, list) else source
            ).splitlines()
            first = next((line.strip() for line in lines if line.strip()), "")
            rows.append(
                {
                    "#": i + 1,
                    "type": cell.get("cell_type", "?"),
                    "starts with": first[:100],
                    "lines": len(lines),
                    "outputs": len(cell.get("outputs", [])),
                }
            )
        p.kind, p.data = "listing", rows
        p.info = {
            k: v
            for k, v in {
                "kernel": meta.get("kernelspec", {}).get("display_name"),
                "language": meta.get("language_info", {}).get("name"),
                "cells": len(cells),
            }.items()
            if v is not None
        }
        return True

    def _preview_image(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        if p.size > 10 * MB:
            return False
        p.kind, p.data = "image", self.read_bytes(uri)
        mime = p.content_type if (p.content_type or "").startswith("image/") else None
        p.info["mime"] = mime or next(
            (m for magic, m in _IMAGE_MIMES if p.data.startswith(magic)), "image/png"
        )
        return True

    def _preview_audio(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        p.kind, p.data = "media", self.presigned_url(uri)
        mime = (
            p.content_type
            if (p.content_type or "").startswith(f"{p.format}/")
            else None
        )
        p.info = {
            "media": p.format,
            "mime": mime or mimetypes.guess_type(uri)[0] or f"{p.format}/*",
        }
        return True

    _preview_video = _preview_audio

    def _preview_pdf(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        url, (bucket, key) = self.presigned_url(uri), parse_s3_uri(uri)
        text_error: Exception | None = None
        picture_error: Exception | None = None
        with self._open_document(bucket, key, codec, whole_under=16 * MB) as handle:
            try:
                doc = parse_pdf(handle, p.uri, pages=[1])
            except _READ_ERRORS as exc:  # a broken file, or no pypdf
                doc, text_error = Document(uri=p.uri, kind="pdf"), exc
            try:
                handle.seek(0)
                doc.pictures = render_pdf_pages(handle, max_pages=_PREVIEW_PAGES)
            except _READ_ERRORS as exc:  # or no pypdfium2 / pillow
                picture_error = exc
        if not doc.parts and not doc.pictures:  # neither its text nor its pages: the link is all there is
            p.kind, p.data, p.info = "media", url, {"media": "pdf"}
            broken = [exc for exc in (text_error, picture_error) if not isinstance(exc, ImportError)]
            p.note = (f"Couldn't read the PDF ({broken[0]}); the link may still open it." if broken
                      else " ".join(f"{exc}." for exc in (text_error, picture_error)))
            return True
        text = doc.parts[0] if doc.parts else ""
        p.kind, p.data, p.document = "document", text, doc
        p.info = {
            "pages": doc.page_count,
            "title": doc.title,
            "author": doc.author,
            "url": url,
            "excerpt": "Page 1"
            + (f" of {doc.page_count}" if (doc.page_count or 0) > 1 else ""),
        }
        notes = []
        if doc.parts and not text.strip():
            notes.append("Page 1 has no text layer (probably a scanned image), so its words can't be searched "
                         "or copied without OCR.")
        for exc, what in ((text_error, "read its text"), (picture_error, "draw its pages")):
            if exc is not None:
                notes.append(f"{exc}." if isinstance(exc, ImportError) else f"Couldn't {what} ({exc}).")
        p.note = " ".join(notes)
        return True

    def _preview_docx(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        bucket, key = parse_s3_uri(uri)
        with self._open_document(bucket, key, codec) as handle:
            doc = parse_docx(handle, p.uri, pictures=True)
        shown = doc.parts[:n]
        p.kind, p.data, p.truncated = "document", "\n\n".join(shown), len(doc.parts) > n
        p.document = doc
        p.info = {
            "words": doc.word_count,
            "paragraphs": len(doc.parts) - len(doc.tables),
            "headings": len(doc.headings) or None,
            "tables": len(doc.tables) or None,
            "pictures": len(doc.pictures) or None,
            "title": doc.title,
            "author": doc.author,
            "excerpt": f"First {len(shown)} paragraphs" if p.truncated else "Document",
            "outline": [
                {"level": level, "heading": text} for level, text in doc.headings[:50]
            ],
            "table": doc.tables[0] if doc.tables else None,
        }
        if not doc.parts:
            p.note = (f"The document has no text, only {_plural(len(doc.pictures), 'picture')} (probably scanned "
                      "pages), so its words can't be searched or copied without OCR." if doc.pictures
                      else "The document has no text.")
        return True

    def _preview_pptx(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        bucket, key = parse_s3_uri(uri)
        with self._open_document(bucket, key, codec) as handle:
            doc = parse_pptx(handle, p.uri)
        rows = []
        for number, title, text, notes in list(
            zip(doc.numbers, doc.slide_titles, doc.parts, doc.notes)
        )[: max(n, 50)]:
            body = (
                text.split("\n", 1)[1]
                if title and text.startswith(title) and "\n" in text
                else text
            )
            body = " · ".join(
                " ".join(line.split()) for line in body.splitlines() if line.strip()
            )
            rows.append(
                {
                    "slide": number,
                    "title": title or "",
                    "text": body[:120] + ("…" if len(body) > 120 else ""),
                    "words": _count_words(text),
                    "notes": "yes" if notes else "",
                }
            )
        p.kind, p.data = "listing", rows
        p.info = {
            "slides": doc.page_count,
            "words": doc.word_count,
            "tables": len(doc.tables) or None,
            "title": doc.title,
            "author": doc.author,
        }
        return True

    def _preview_oldoffice(self, p: Preview, uri: str, n: int, codec: str) -> bool:
        p.kind, p.data = "binary", self.read_bytes(uri, 0, 511)
        p.note = _old_office_note(uri)
        return True

    def _preview_as_text(
        self, p: Preview, uri: str, n: int, codec: str, max_bytes: int, sniffed: bool
    ) -> None:
        """
        JSON, text lines or a binary sample (also the fallback for everything else).
        """
        data, p.truncated = self._read_head(uri, max_bytes, codec)
        if p.format == "json":
            if not p.truncated:
                try:
                    parsed = json.loads(data)
                except ValueError:
                    pass  # maybe JSON lines with a .json name - tried below
                else:
                    self._show_json(p, parsed, n=n)
                    return
            elif not _json_lines_start(data):  # one big document, not a record per line: show its start
                start = _json_prefix(data.decode("utf-8", errors="replace"))
                if start is not None:
                    self._show_json(p, *start, n=n)
                    p.note = (f"The file is bigger than the {human_size(max_bytes)} preview window, so this is "
                              f"its start; {_call('download', p.uri)} gets all of it.")
                    return
            try:  # Firehose / Spark often write JSON lines into '.json' files
                p.kind, p.data = (
                    "table",
                    self.read_df(uri, nrows=n, fmt="jsonl", compression=codec),
                )
                p.format = "jsonl"
                return
            except ValueError:
                p.kind = "text"
                if (
                    not sniffed
                ):  # a '[...' log line without an extension isn't worth a warning
                    p.note = (
                        f"JSON is larger than the {human_size(max_bytes)} preview window; showing raw text."
                        if p.truncated
                        else "Not valid JSON; showing raw text."
                    )
        if _looks_binary(data):
            p.kind, p.data = "binary", data[:512]
            return
        lines = data.decode("utf-8", errors="replace").splitlines()
        p.kind = "text"
        p.truncated = p.truncated or len(lines) > n
        p.data = lines[:n]

    def _show_json(self, p: Preview, value: Any, cut: int = 0, *, n: int = 20) -> None:
        """
        A list of records as a table, anything else as JSON. `cut`: the objects and arrays along the end of a
        document that was cut off (_json_prefix), so a record cut in half isn't a row.
        """
        records = value[:-1] if isinstance(value, list) and cut > 1 else value
        if isinstance(records, list) and records and all(isinstance(r, dict) for r in records):
            pd = _require("pandas", "Table preview")
            p.kind, p.data = "table", pd.json_normalize(records[:n])
            p.info["records"] = f"{len(records):,}+" if cut else len(records)
            return
        p.kind, p.data = "json", value
        p.info.update(_json_shape(value, cut))
        if cut:
            p.info["cut"] = cut
