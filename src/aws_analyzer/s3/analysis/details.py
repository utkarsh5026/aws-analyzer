"""
file_details(): what each file is and holds (rows, columns, pages, sheets, duration, ...),
read from a few blocks of it, and what's wrong with a folder of them.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import posixpath
import tarfile
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from ..._kit.deps import _require
from ..._kit.fmt import _plural, file_extension, human_size
from ..._kit.render import _call

from ..helpers import GB, KB, MB, base_prefix, parse_s3_uri, relative_key, s3_uri
from ..models import FileDetailsReport
from ..readers.formats import (
    _CSV_SEPARATORS, _DECOMPRESSORS, _READ_ERRORS, _ReadLimit, _looks_binary, detect_format, sniff_format,
)
from ..readers.avro import _avro_header, _avro_rows
from ..readers.office import _office_properties, _old_office_note, _xlsx_sheets, office_kind, parse_docx, parse_pptx
from ..readers.pdf import _pdf_facts
from ..readers.media import _audio_info, _image_info, _mp4_info
from ..readers.data import _columnar_info, _npy_header, _parse_safetensors, _safetensors_length


@dataclass
class FileDetails:
    """
    What's inside one file (see describe_file and S3Analyzer.file_details). `facts` holds what its format says:
    'pages', 'title', 'author', 'made_with', 'page_size', 'text' (does a PDF's first pages have text; False =
    scanned), 'locked' (needs a password); 'words', 'headings', 'tables', 'pictures' (Word); 'slides', 'notes'
    (PowerPoint); 'sheets' (Excel: name, rows, columns); 'rows', 'columns' (column names), 'row_groups', 'codec'
    (tables, CSV, JSON lines); 'width', 'height' (pictures, video); 'duration' in seconds, 'sample_rate',
    'channels' (audio, video); 'files', 'unpacked' (archives); 'tensors', 'arrays', 'parameters', 'dtypes',
    'shape' (model and array files); 'cells', 'code_cells', 'kernel' (notebooks); 'lines' (text). A count read
    from the start of the file only has '<name>_estimate' set: 'estimate' (≈) or 'at least' (+).
    """

    key: str  # the object key, or the name given to describe_file
    size: int
    bucket: str = ""
    format: str | None = None  # 'pdf', 'docx', 'parquet', 'csv', 'image', ...: by name, or by the first bytes
    compression: str | None = None  # 'gz', 'bz2', 'xz', 'zst' around the whole file
    facts: dict[str, Any] = field(default_factory=dict)
    named: str | None = None  # the format the name says, when the content turned out to be something else
    notes: list[str] = field(default_factory=list)  # what's worth knowing that isn't a fact
    error: str = ""  # why it couldn't be read, or not to the end; facts found before that stay
    skipped: str = ""  # why it wasn't read at all: 'empty', 'archived' (GLACIER / DEEP_ARCHIVE), 'max_read'
    storage_class: str = "STANDARD"
    last_modified: datetime | None = None
    bytes_read: int = 0

    @property
    def uri(self) -> str:
        return s3_uri(self.bucket, self.key) if self.bucket else self.key

    @property
    def summary(self) -> str:
        """
        One line: '12 pages · A4 · "Q3 report"', '1,200 rows × 14 columns', 'PNG 1920 × 1080'.
        """
        return _details_summary(self)


# ---- What's inside a file (describe_file, file_details_findings)

_SAMPLE_BYTES = 256 * KB  # a CSV's / text file's lines are counted in this much of its start

_UNPACK_LIMIT = 64 * MB  # a compressed PDF, Office file or parquet file is unpacked in memory up to this size

_JSON_LIMIT = 16 * MB  # a .json file is parsed whole up to this size

_MAGIC_NAMED = {"pdf", "parquet", "orc", "arrow", "avro", "npy", "zip", "docx", "pptx", "npz", "oldoffice"}

_ZIP_KINDS = {"zip", "docx", "pptx", "excel", "npz", "torch"}  # formats that are zip files inside

_TEXT_KINDS = {"csv", "tsv", "psv", "json", "jsonl", "notebook", "text"}

_TABLE_KINDS = ("parquet", "orc", "arrow", "avro", "csv", "tsv", "psv", "jsonl", "json")

_KIND_NAMES = {  # a format in a sentence: "it's a PDF", "it holds text"
    "pdf": "a PDF",
    "docx": "a Word document",
    "pptx": "a PowerPoint deck",
    "excel": "an Excel workbook",
    "oldoffice": "an Office 97-2003 file",
    "parquet": "parquet",
    "orc": "ORC",
    "arrow": "Feather / Arrow",
    "avro": "Avro",
    "csv": "CSV",
    "tsv": "TSV",
    "psv": "pipe-separated text",
    "json": "JSON",
    "jsonl": "JSON lines",
    "notebook": "a Jupyter notebook",
    "text": "text",
    "zip": "a zip archive",
    "tar": "a tar archive",
    "image": "a picture",
    "audio": "audio",
    "video": "video",
    "npy": "a NumPy array",
    "npz": "NumPy arrays",
    "safetensors": "safetensors",
    "torch": "a PyTorch checkpoint",
    "pickle": "a pickle",
}


def describe_file(
    source: Any, name: str = "", *, size: int | None = None, password: str | None = None
) -> FileDetails:
    """
    What's inside a file, from as little of it as its format needs: a PDF's pages, title, author, page size and
    whether its first pages have text (scanned pages don't); a Word file's words, headings, tables and pictures; a
    deck's slides; each Excel sheet's rows and columns; a table's rows and column names (parquet, ORC, Feather,
    Avro, CSV, JSON lines); a picture's width and height; an audio or video file's length; an archive's files; a
    model file's tensors and parameters; a notebook's cells; a text file's lines. The rows of a CSV or the lines
    of a text file over 256 KB are estimated from its first 256 KB.

    source: a path or a seekable binary file (open(path, 'rb'), io.BytesIO(data)); name: the file name, whose
    extension says the format (the first bytes are checked too, and win when they disagree: d.named then keeps
    what the name said). PDFs need pypdf, and parquet / ORC / Feather need pyarrow. What can't be read goes in
    d.error instead of raising. S3Analyzer.file_details does this for S3 objects, with ranged GETs.
    """
    if isinstance(source, (str, os.PathLike)):
        path = os.fspath(source)
        with open(path, "rb") as handle:
            return describe_file(handle, name or path, size=os.path.getsize(path), password=password)
    if size is None:
        size = source.seek(0, io.SEEK_END)
    details = FileDetails(key=name, size=size or 0)
    _describe_into(details, source, password)
    return details


def _describe_into(d: FileDetails, source: Any, password: str | None) -> None:
    d.format, d.compression = detect_format(d.key)
    if not d.size:
        d.skipped = "empty"
        return
    try:
        source.seek(0)
        _identify(d, source)
        reader = _DETAIL_READERS.get(d.format or "")
        if reader is not None:
            source.seek(0)
            reader(d, source, password)
    except _READ_ERRORS as exc:
        d.error = _error_text(exc)


def _error_text(exc: BaseException) -> str:
    text = str(exc).strip()
    if text and type(exc) in (ValueError, ImportError, OSError, EOFError) or isinstance(exc, _ReadLimit):
        return text
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _identify(d: FileDetails, f: Any) -> None:
    """
    Settle d.format and d.compression from the name and the first bytes. When they disagree the bytes win,
    d.named keeps what the name said, and d.facts['starts_with'] the first line of a file that turned out to be text.
    """
    named = d.format
    head = f.read(512)
    sniffed, codec = sniff_format(head)
    if d.compression and codec != d.compression:
        d.notes.append(f"The name says .{d.compression}, but it isn't {d.compression}-compressed; read as it is.")
        d.compression = None
    elif codec and not d.compression:
        d.compression = codec
    if d.compression:
        f.seek(0)
        head = _DECOMPRESSORS[d.compression](f).read(512)
        sniffed = sniff_format(head)[0]
    if sniffed is None and _image_info(head):  # WebP, BMP and TIFF, which sniff_format doesn't know
        sniffed = "image"
    fits = (
        sniffed == named
        or (sniffed is None and named not in _MAGIC_NAMED)
        or (sniffed == "zip" and named in _ZIP_KINDS)
        or (sniffed == "oldoffice" and named == "excel")
        or (sniffed == "json" and named in _TEXT_KINDS)
    )
    if named is not None and fits:
        return
    d.named = named
    d.format = sniffed or (None if _looks_binary(head) else "text")
    if d.format == "zip" and not d.compression:  # a Word / PowerPoint / Excel file without its extension
        f.seek(0)
        d.format = office_kind(f) or "zip"
    if named is not None and d.format == "text":
        d.facts["starts_with"] = head.split(b"\n", 1)[0].strip()[:60].decode("utf-8", "replace")


def _unpacked(d: FileDetails, f: Any) -> Any:
    """
    The file itself, or when the whole of it is compressed, its unpacked bytes in memory (up to 64 MB).
    """
    if not d.compression:
        return f
    data = _DECOMPRESSORS[d.compression](f).read(_UNPACK_LIMIT + 1)
    if len(data) > _UNPACK_LIMIT:
        raise ValueError(f"it unpacks to more than {human_size(_UNPACK_LIMIT)}, too much to open here")
    return io.BytesIO(data)


def _stream(d: FileDetails, f: Any) -> Any:
    return _DECOMPRESSORS[d.compression](f) if d.compression else f


def _sample(d: FileDetails, f: Any) -> tuple[bytes, bool]:
    """
    The first 256 KB of the (unpacked) file, and whether that's all of it.
    """
    if not d.compression:  # the size says whether there's more, so no byte past the sample is fetched
        return f.read(_SAMPLE_BYTES), d.size <= _SAMPLE_BYTES
    data = _stream(d, f).read(_SAMPLE_BYTES + 1)
    return data[:_SAMPLE_BYTES], len(data) <= _SAMPLE_BYTES


def _line_count(d: FileDetails, data: bytes, complete: bool) -> tuple[int, str | None]:
    """
    Lines in the file -> (count, how): exact (how=None) when `data` is all of it; else estimated from the bytes
    per line of its first lines ('estimate'), or for a compressed file, whose unpacked size isn't known, the lines
    seen so far ('at least').
    """
    if complete:
        return (data.count(b"\n") + (not data.endswith(b"\n"))) if data else 0, None
    whole = data[: data.rfind(b"\n") + 1]
    lines = whole.count(b"\n")
    if d.compression or not whole:
        return lines, "at least"
    return round(lines * d.size / len(whole)), "estimate"


def _put_count(d: FileDetails, key: str, count: int, how: str | None) -> None:
    d.facts[key] = max(count, 0)
    if how:
        d.facts[f"{key}_estimate"] = how


def _media_count(names: list[str], folder: str) -> int:
    return sum(name.startswith(folder) and not name.endswith("/") for name in names)


def _details_pdf(d: FileDetails, f: Any, password: str | None) -> None:
    _pdf_facts(_unpacked(d, f), d.facts, password)


def _details_docx(d: FileDetails, f: Any, password: str | None) -> None:
    g = _unpacked(d, f)
    doc = parse_docx(g)
    with zipfile.ZipFile(g) as archive:
        names, props = archive.namelist(), _office_properties(archive)
    d.facts.update(words=doc.word_count, pages=doc.page_count, headings=len(doc.headings), tables=len(doc.tables),
                   pictures=_media_count(names, "word/media/"), title=doc.title, author=doc.author,
                   modified=props["modified"])


def _details_pptx(d: FileDetails, f: Any, password: str | None) -> None:
    g = _unpacked(d, f)
    doc = parse_pptx(g)
    with zipfile.ZipFile(g) as archive:
        names = archive.namelist()
    d.facts.update(slides=doc.page_count, words=doc.word_count, notes=sum(bool(n) for n in doc.notes),
                   tables=len(doc.tables), pictures=_media_count(names, "ppt/media/"), title=doc.title,
                   author=doc.author)


def _details_excel(d: FileDetails, f: Any, password: str | None) -> None:
    g = _unpacked(d, f)
    if g.read(4) != b"PK\x03\x04":
        d.notes.append("An Excel 97-2003 workbook (.xls): its sheet sizes aren't read here; pandas.read_excel "
                       "opens it (with the xlrd package).")
        return
    g.seek(0)
    with zipfile.ZipFile(g) as archive:
        d.facts["sheets"] = _xlsx_sheets(archive)
        props = _office_properties(archive)
    d.facts.update(title=props["title"], author=props["author"])


def _details_parquet(d: FileDetails, f: Any, password: str | None) -> None:
    pq = _require("pyarrow.parquet", "Reading a parquet file's row count")
    parquet = pq.ParquetFile(_unpacked(d, f))
    meta = parquet.metadata
    d.facts.update(rows=meta.num_rows, columns=list(parquet.schema_arrow.names), row_groups=meta.num_row_groups,
                   codec=meta.row_group(0).column(0).compression if meta.num_row_groups and meta.num_columns
                   else None, created_by=meta.created_by)


def _details_columnar(d: FileDetails, f: Any, password: str | None) -> None:
    g = _unpacked(d, f)
    info = _columnar_info(d.format or "", g)
    if not info:
        d.notes.append("A Feather V1 file (written before 2020): its size isn't read here; pandas.read_feather "
                       "opens it.")
        return
    d.facts["columns"] = [name for name, _ in info.get("columns", [])]
    for key, fact in (("rows", "rows"), ("compression", "codec"), ("stripes", "stripes"), ("batches", "batches")):
        if info.get(key) is not None:
            d.facts[fact] = info[key]
    if "rows" not in d.facts and d.size <= _UNPACK_LIMIT:  # Feather keeps the row count in each batch
        g.seek(0)
        reader = _require("pyarrow.ipc", "Reading Feather / Arrow").open_file(g)
        d.facts["rows"] = sum(reader.get_batch(i).num_rows for i in range(reader.num_record_batches))


def _details_avro(d: FileDetails, f: Any, password: str | None) -> None:
    whole = d.compression is not None or d.size <= _UNPACK_LIMIT
    data = _stream(d, f).read(_UNPACK_LIMIT + 1 if whole else MB)
    schema, codec, _, start = _avro_header(data)
    fields = schema.get("fields", []) if isinstance(schema, dict) else []
    d.facts.update(columns=[field["name"] for field in fields if isinstance(field, dict)], codec=codec)
    rows, complete = _avro_rows(data, start)
    _put_count(d, "rows", rows, None if complete else "at least")


def _details_csv(d: FileDetails, f: Any, password: str | None) -> None:
    data, complete = _sample(d, f)
    text = data.decode("utf-8-sig", "replace")
    if not complete:
        text = text[: text.rfind("\n") + 1] or text
    separator = _CSV_SEPARATORS.get(d.format or "", ",")
    try:
        rows = [row for row in csv.reader(io.StringIO(text), delimiter=separator) if row]
    except csv.Error:  # a field cut off at the end of the sample, or one over csv's size limit
        rows = [line.split(separator) for line in text.splitlines() if line]
    d.facts["columns"] = rows[0] if rows else []
    count, how = (len(rows), None) if complete else _line_count(d, data, complete)
    _put_count(d, "rows", count - 1, how)


def _details_jsonl(d: FileDetails, f: Any, password: str | None) -> None:
    data, complete = _sample(d, f)
    lines = (data if complete else data[: data.rfind(b"\n") + 1]).splitlines()
    records = [line for line in lines if line.strip()]
    first = json.loads(records[0]) if records else None
    if isinstance(first, dict):
        d.facts["columns"] = list(first)
    count, how = (len(records), None) if complete else _line_count(d, data, complete)
    _put_count(d, "rows", count, how)


def _details_json(d: FileDetails, f: Any, password: str | None) -> None:
    data = _stream(d, f).read(_JSON_LIMIT + 1)
    complete = len(data) <= _JSON_LIMIT
    if complete:
        try:
            value = json.loads(data)
        except ValueError:
            pass
        else:
            if isinstance(value, list):
                _put_count(d, "rows", len(value), None)
                if value and all(isinstance(v, dict) for v in value[:100]):
                    d.facts["columns"] = list(dict.fromkeys(k for v in value[:100] for k in v))
            elif isinstance(value, dict):
                d.facts["keys"] = list(value)
            return
    lines = [line for line in data[:_SAMPLE_BYTES].splitlines() if line.strip()][:2]
    try:
        records = [json.loads(line) for line in lines]
    except ValueError:
        records = []
    named = detect_format(d.key)[0] == "json"  # else it only started with '{' or '['
    if records and all(isinstance(r, dict) for r in records) and (len(records) > 1 or not complete):
        if named:
            d.facts["json_lines"] = True  # a .json file holding JSON lines (Firehose, Spark)
        d.format = "jsonl"
    elif not named:
        d.format = "text"
    elif complete:
        raise ValueError("it isn't valid JSON")
    else:
        d.notes.append(f"It's over {human_size(_JSON_LIMIT)}, so it wasn't parsed.")
        return
    f.seek(0)
    _DETAIL_READERS[d.format](d, f, password)


def _details_notebook(d: FileDetails, f: Any, password: str | None) -> None:
    data = _stream(d, f).read(50 * MB + 1)
    if len(data) > 50 * MB:
        raise ValueError("it's over 50 MB, too big to open here")
    notebook = json.loads(data)
    if not isinstance(notebook, dict):
        raise ValueError("it isn't a Jupyter notebook")
    cells = [c for c in notebook.get("cells", []) if isinstance(c, dict)]
    meta = notebook.get("metadata") or {}
    d.facts.update(cells=len(cells), code_cells=sum(c.get("cell_type") == "code" for c in cells),
                   outputs=sum(len(c.get("outputs") or []) for c in cells),
                   kernel=(meta.get("kernelspec") or {}).get("display_name"),
                   language=(meta.get("language_info") or {}).get("name"))


def _details_text(d: FileDetails, f: Any, password: str | None) -> None:
    data, complete = _sample(d, f)
    if _looks_binary(data):
        d.format = None
        return
    _put_count(d, "lines", *_line_count(d, data, complete))


def _details_image(d: FileDetails, f: Any, password: str | None) -> None:
    stream = _stream(d, f)
    data = stream.read(64 * KB)
    info = _image_info(data)
    if info is None and len(data) == 64 * KB:  # a JPEG whose size comes after a big thumbnail
        data += stream.read(MB)
        info = _image_info(data)
    if info is None:
        raise ValueError("its width and height aren't where the format keeps them; the file may be damaged")
    d.facts.update(info)


def _details_media(d: FileDetails, f: Any, password: str | None) -> None:
    head = f.read(64 * KB)
    info = _audio_info(head)
    extension = file_extension(d.key).split(".")[0]
    boxes = head[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip")
    if info is None and boxes:
        info = _mp4_info(f, d.size)
        if info is None:
            raise ValueError("it has no 'moov' box, which holds its length: the upload may have been cut off")
        info["kind"] = {b"qt  ": "MOV", b"M4A ": "M4A"}.get(head[8:12], "MP4")
    elif info is None and extension in ("mp4", "mov", "m4a", "m4v", "wav", "flac"):
        raise ValueError(f"it doesn't start the way a .{extension} file does; it may be damaged")
    if info:
        d.facts.update(info)
    else:
        d.notes.append(f"Its length isn't read from .{extension} files here, only from WAV, FLAC, MP4, MOV and M4A.")


def _details_archive(d: FileDetails, f: Any, password: str | None) -> None:
    if d.format != "tar":
        with zipfile.ZipFile(_unpacked(d, f)) as archive:
            files = [i for i in archive.infolist() if not i.is_dir()]
        d.facts.update(files=len(files), unpacked=sum(i.file_size for i in files))
        return
    count = unpacked = 0
    try:  # a plain tar: hop from header to header; a compressed one has to be read from the start
        with tarfile.open(fileobj=_stream(d, f), mode="r|" if d.compression else "r:") as archive:
            for member in archive:
                if member.isfile():
                    count, unpacked = count + 1, unpacked + member.size
    except _READ_ERRORS:
        if count:
            d.facts.update(files=count, unpacked=unpacked, files_estimate="at least", unpacked_estimate="at least")
        raise
    d.facts.update(files=count, unpacked=unpacked)


def _details_npz(d: FileDetails, f: Any, password: str | None) -> None:
    with zipfile.ZipFile(_unpacked(d, f)) as archive:
        infos = [i for i in archive.infolist() if not i.is_dir()]
        d.facts.update(arrays=len(infos), unpacked=sum(i.file_size for i in infos))
        parameters, dtypes = 0, set()
        for info in infos[:200]:
            with archive.open(info) as member:
                shape, _, dtype = _npy_header(io.BytesIO(member.read(16 * KB)))
            parameters, dtypes = parameters + math.prod(shape), dtypes | {str(dtype)}
    _put_count(d, "parameters", parameters, "at least" if len(infos) > 200 else None)
    d.facts["dtypes"] = sorted(dtypes)


def _details_npy(d: FileDetails, f: Any, password: str | None) -> None:
    shape, _, dtype = _npy_header(_stream(d, f))
    d.facts.update(shape=tuple(shape), dtype=str(dtype), parameters=math.prod(shape))


def _details_safetensors(d: FileDetails, f: Any, password: str | None) -> None:
    info = _parse_safetensors(f.read(_safetensors_length(f.read(8))))
    tensors = info["tensors"]
    d.facts.update(tensors=len(tensors), parameters=sum(t["parameters"] for t in tensors),
                   dtypes=sorted({t["dtype"] for t in tensors if t["dtype"]}))
    if info["metadata"]:
        d.facts["metadata"] = info["metadata"]


def _details_torch(d: FileDetails, f: Any, password: str | None) -> None:
    if f.read(2) == b"PK":
        f.seek(0)
        with zipfile.ZipFile(f) as archive:
            files = [i for i in archive.infolist() if not i.is_dir()]
        d.facts.update(files=len(files), unpacked=sum(i.file_size for i in files))
    d.notes.append("Not loaded: torch.load unpickles, which can run code from the file.")


def _details_pickle(d: FileDetails, f: Any, password: str | None) -> None:
    d.notes.append("Not opened: unpickling can run code from the file.")


def _details_oldoffice(d: FileDetails, f: Any, password: str | None) -> None:
    d.notes.append(_old_office_note(d.key))


_DETAIL_READERS: dict[str, Callable[[FileDetails, Any, str | None], None]] = {
    "pdf": _details_pdf,
    "docx": _details_docx,
    "pptx": _details_pptx,
    "excel": _details_excel,
    "parquet": _details_parquet,
    "orc": _details_columnar,
    "arrow": _details_columnar,
    "avro": _details_avro,
    "csv": _details_csv,
    "tsv": _details_csv,
    "psv": _details_csv,
    "jsonl": _details_jsonl,
    "json": _details_json,
    "notebook": _details_notebook,
    "text": _details_text,
    "image": _details_image,
    "audio": _details_media,
    "video": _details_media,
    "zip": _details_archive,
    "tar": _details_archive,
    "npz": _details_npz,
    "npy": _details_npy,
    "safetensors": _details_safetensors,
    "torch": _details_torch,
    "pickle": _details_pickle,
    "oldoffice": _details_oldoffice,
}


def _count_text(facts: dict[str, Any], key: str) -> str:
    """
    A count with its accuracy: '1,234', '≈ 12,000' (estimated), '1,234+' (at least).
    """
    value, how = facts.get(key), facts.get(f"{key}_estimate")
    if value is None:
        return ""
    if how == "estimate":
        return f"≈ {int(float(f'{value:.2g}')) if value >= 100 else value:,}"
    return f"{value:,}{'+' if how == 'at least' else ''}"


def _short_count(value: int) -> str:
    """
    7_241_732_096 -> '7.2B', 124_000 -> '124K'.
    """
    for size, unit in ((10**12, "T"), (10**9, "B"), (10**6, "M"), (10**3, "K")):
        if value >= size:
            return f"{value / size:.1f}".rstrip("0").rstrip(".") + unit
    return f"{value:,}"


def _clock(seconds: float) -> str:
    """
    192.4 -> '3:12', 3723 -> '1:02:03'.
    """
    whole = round(seconds)
    hours, rest = divmod(whole, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def _sheets_text(sheets: list[dict[str, Any]], most: int = 3) -> str:
    """
    'Sales 1,200 × 11, Notes 40 × 2 and 3 more sheets' (rows × columns, header row included).
    """
    parts = [
        f"{s['sheet']} {s['rows']:,} × {s['columns']:,}" if s.get("rows") else f"{s['sheet']} (size not saved)"
        for s in sheets[:most]
    ]
    more = f" and {len(sheets) - most:,} more" if len(sheets) > most else ""
    return ", ".join(parts) + more


def _details_summary(d: FileDetails) -> str:
    if d.skipped:
        return {
            "empty": "empty file",
            "archived": f"not read: in {d.storage_class}, restore it first",
            "max_read": "not read: max_read reached",
        }.get(d.skipped, f"not read: {d.skipped}")
    facts, parts = d.facts, []

    def count(key: str, word: str, *, zero: bool = True) -> None:
        text = _count_text(facts, key)
        if text and (zero or facts[key]):
            exact = facts[key] == 1 and not facts.get(f"{key}_estimate")
            parts.append(f"{text} {word}{'' if exact else 's'}")

    if d.named:
        parts.append(f"{_KIND_NAMES.get(d.format or '', 'binary data')}, not {_KIND_NAMES.get(d.named, d.named)}")
    if d.format == "pdf":
        count("pages", "page")
        if facts.get("locked"):
            parts.append("needs a password")
        elif facts.get("text") is False:
            parts.append("no text (scanned?)")
        if facts.get("page_size"):
            parts.append(facts["page_size"])
    elif d.format == "docx":
        count("words", "word")
        for key in ("headings", "tables", "pictures"):
            count(key, key[:-1], zero=False)
    elif d.format == "pptx":
        count("slides", "slide")
        count("words", "word")
    elif "sheets" in facts:
        parts.append(f"{_plural(len(facts['sheets']), 'sheet')}: {_sheets_text(facts['sheets'], 2)}")
    elif "rows" in facts or "columns" in facts:
        rows, columns = _count_text(facts, "rows"), facts.get("columns")
        if rows:
            parts.append(f"{rows} {'row' if rows == '1' else 'rows'}"
                         + (f" × {_plural(len(columns), 'column')}" if columns is not None else ""))
        elif columns is not None:
            parts.append(_plural(len(columns), "column"))
    elif "width" in facts and d.format == "image":
        parts.append(f"{facts.get('kind', '')} {facts['width']:,} × {facts['height']:,}".strip())
    elif d.format in ("audio", "video"):
        parts.append(f"{facts.get('kind') or file_extension(d.key).split('.')[0]} {d.format}")
        if "duration" in facts:
            parts.append(_clock(facts["duration"]))
        if "width" in facts:
            parts.append(f"{facts['width']:,} × {facts['height']:,}")
        if facts.get("sample_rate"):
            parts.append(f"{facts['sample_rate'] / 1000:g} kHz")
        if facts.get("channels"):
            parts.append({1: "mono", 2: "stereo"}.get(facts["channels"], f"{facts['channels']} channels"))
    elif "tensors" in facts:
        count("tensors", "tensor")
        parts.append(f"{_short_count(facts['parameters'])} parameters")
        parts += facts.get("dtypes", [])[:3]
    elif "arrays" in facts:
        count("arrays", "array")
        parts.append(f"{_short_count(facts['parameters'])} values")
    elif "shape" in facts:
        parts.append(f"shape {facts['shape']} {facts.get('dtype', '')}".strip())
    elif "files" in facts:
        count("files", "file")
        parts.append(f"{human_size(facts['unpacked'])} unpacked")
    elif "cells" in facts:
        count("cells", "cell")
        if facts.get("kernel"):
            parts.append(facts["kernel"])
    elif "lines" in facts:
        count("lines", "line")
    elif "keys" in facts:
        parts.append(f"an object with {_plural(len(facts['keys']), 'key')}")
    if facts.get("title"):
        title = facts["title"]
        parts.append(f'"{title[:40]}…"' if len(title) > 40 else f'"{title}"')
    if d.error:
        parts.append(f"couldn't read: {d.error[:80]}{'…' if len(d.error) > 80 else ''}")
    elif not parts and d.notes:
        parts.append(d.notes[0][:80] + ("…" if len(d.notes[0]) > 80 else ""))
    return " · ".join(parts) or _KIND_NAMES.get(d.format or "", "binary data of an unknown kind")


def _changed_columns(columns: list[str], usual: tuple[str, ...]) -> str:
    """
    How a file's columns differ from the usual ones: 'adds discount; lacks region'.
    """
    added = [c for c in columns if c not in usual]
    lacking = [c for c in usual if c not in columns]
    parts = [f"{verb} {', '.join(names[:3])}{' and more' if len(names) > 3 else ''}"
             for verb, names in (("adds", added), ("lacks", lacking)) if names]
    return "; ".join(parts) or "the same columns in another order"


def file_details_findings(report: FileDetailsReport) -> list[tuple[str, str]]:
    """
    What stands out in what's inside the files -> [(level, message)], level 'warn' or 'info': scanned PDFs
    without text, files that need a password, files that aren't what their name says, table files in one folder
    with different columns, pictures of mixed sizes, and what wasn't read or couldn't be (archived, over max_read,
    a package to install, a damaged file).
    """
    found: list[tuple[str, str]] = []
    files = report.files
    base = base_prefix(parse_s3_uri(report.uri)[1]) if report.uri else ""

    def name(d: FileDetails) -> str:
        return relative_key(d.key, base) or d.key.rsplit("/", 1)[-1]

    def names(group: list[FileDetails], most: int = 3) -> str:
        shown = ", ".join(name(d) for d in group[:most])
        return shown + (f" and {len(group) - most:,} more" if len(group) > most else "")

    def are(group: list[Any]) -> str:
        return "is" if len(group) == 1 else "are"

    def their(group: list[Any]) -> str:
        return "Its" if len(group) == 1 else "Their"

    if report.truncated and report.limit is not None:
        found.append(("warn", f"Stopped at limit={report.limit:,}: only the first {_plural(len(files), 'file')} (in "
                              "key order) were looked into. Pass a bigger limit= to look at more."))
    unread = [d for d in files if d.skipped == "max_read"]
    if unread:
        done = max(1, len(files) - len(unread))
        suggest = max(2, math.ceil(report.bytes_read * len(files) / done / GB))
        found.append(("warn", f"Stopped reading at max_read={human_size(report.max_read)}: "
                              f"{_plural(len(unread), 'file')} {'was' if len(unread) == 1 else 'were'}n't looked "
                              f"into ({names(unread)}). Pass "
                              f"max_read='{suggest}GB' to read them too."))
    scanned = [d for d in files if d.format == "pdf" and d.facts.get("text") is False]
    if scanned:
        found.append(("warn", f"{_plural(len(scanned), 'PDF')} {'has' if len(scanned) == 1 else 'have'} no text on "
                              f"the first pages, probably scanned: {names(scanned)}. {their(scanned)} words can't be "
                              "searched, copied or sent to a model until OCR (Amazon Textract, for one) adds a text "
                              "layer; "
                              f"{_call('document', scanned[0].uri)} shows the pages as pictures."))
    pictures_only = [d for d in files if d.format == "docx" and d.facts.get("words") == 0 and d.facts.get("pictures")]
    if pictures_only:
        found.append(("warn", f"{_plural(len(pictures_only), 'Word file')} {are(pictures_only)} only pictures, no "
                              f"text (scanned pages?): {names(pictures_only)}. Their words can't be searched without "
                              f"OCR; {_call('document', pictures_only[0].uri)} shows them."))
    locked = [d for d in files if d.facts.get("locked")]
    if locked:
        found.append(("warn", f"{_plural(len(locked), 'PDF')} {'needs' if len(locked) == 1 else 'need'} a password "
                              f"to open: {names(locked)}. Without it the text, title and author can't be read; "
                              f"{_call('document', locked[0].uri, password='...')} opens one."))
    wrong = [d for d in files if d.named]
    if wrong:
        shown = []
        for d in wrong[:3]:
            start = d.facts.get("starts_with")
            shown.append(f"{name(d)} is {_KIND_NAMES.get(d.format or '', 'binary data')}, not "
                         f"{_KIND_NAMES.get(d.named or '', d.named)}" + (f" (it starts {start!r})" if start else ""))
        more = f"; and {len(wrong) - 3:,} more" if len(wrong) > 3 else ""
        found.append(("warn", f"{_plural(len(wrong), 'file')} {are(wrong)}n't what the name says: "
                              f"{'; '.join(shown)}{more}. Tools that go by the extension (pandas, Spark, Athena) fail "
                              f"on them; {_call('preview', wrong[0].uri)} shows what's inside."))
    missing: dict[str, list[FileDetails]] = defaultdict(list)
    for d in files:
        if d.error and "pip install" in d.error:
            missing[d.error].append(d)
    for error, group in missing.items():
        found.append(("warn", f"{_plural(len(group), 'file')} {'was' if len(group) == 1 else 'were'}n't looked "
                              f"into ({names(group)}): {error}."))
    failed = [d for d in files if d.error and "pip install" not in d.error]
    if failed:
        shown = "; ".join(f"{name(d)} ({d.error[:100]})" for d in failed[:3])
        more = f"; and {len(failed) - 3:,} more" if len(failed) > 3 else ""
        found.append(("warn", f"{_plural(len(failed), 'file')} couldn't be read, or not to the end: {shown}{more}. "
                              f"{_call('preview', failed[0].uri)} shows what's in the first one."))
    archived = [d for d in files if d.skipped == "archived"]
    if archived:
        first = archived[0]
        restore = (f" aws s3api restore-object --bucket {first.bucket} --key {first.key} --restore-request Days=7"
                   if first.bucket and " " not in first.key else "")
        found.append(("info", f"{_plural(len(archived), 'file')} in GLACIER / DEEP_ARCHIVE weren't read "
                              f"({names(archived)}): they need a restore first{':' if restore else '.'}{restore}"))
    old = [d for d in files if d.format == "oldoffice"]
    if old:
        them = "it" if len(old) == 1 else "them"
        found.append(("info", f"{_plural(len(old), 'Office 97-2003 file')} ({names(old)}): {their(old).lower()} text "
                              f"can't be read here. Save {them} as .docx / .pptx in Office, or convert {them} with "
                              "LibreOffice (soffice --headless --convert-to docx FILE)."))
    tables: dict[tuple[str, str], list[FileDetails]] = defaultdict(list)
    for d in files:
        if d.format in _TABLE_KINDS and isinstance(d.facts.get("columns"), list) and not d.error:
            tables[(posixpath.dirname(d.key), d.format or "")].append(d)
    drifts = 0
    for (folder, fmt), group in tables.items():
        shapes = Counter(tuple(d.facts["columns"]) for d in group)
        if len(group) < 2 or len(shapes) < 2 or drifts >= 3:
            continue
        drifts += 1
        usual = shapes.most_common(1)[0][0]
        odd = [d for d in group if tuple(d.facts["columns"]) != usual]
        where = relative_key(folder + "/", base) if folder else ""
        found.append(("warn", f"The {_KIND_NAMES.get(fmt, fmt)} files in {where or 'this folder'} don't all have "
                              f"the same columns: {len(odd):,} of {len(group):,} "
                              f"{'differs' if len(odd) == 1 else 'differ'} from the rest ({name(odd[0])} "
                              f"{_changed_columns(odd[0].facts['columns'], usual)}). Athena, Glue and Spark read a "
                              "folder as one table, so those columns come out empty or the query fails; "
                              f"{_call('preview', odd[0].uri)} shows that file."))
    lines = [d for d in files if d.facts.get("json_lines")]
    if lines:
        found.append(("info", f"{_plural(len(lines), '.json file')} ({names(lines)}) {'holds' if len(lines) == 1 else 'hold'} "
                              "JSON lines, one record per line, not one JSON document: pandas reads them with "
                              f"read_json(path, lines=True), and {_call('ui.core.read_df', lines[0].uri, fmt='jsonl')} "
                              "here."))
    sized = [d for d in files if d.format == "image" and d.facts.get("width")]
    sizes = Counter((d.facts["width"], d.facts["height"]) for d in sized)
    if len(sized) >= 3 and len(sizes) > 1:
        (width, height), common = sizes.most_common(1)[0]
        small, big = min(sizes, key=lambda s: s[0] * s[1]), max(sizes, key=lambda s: s[0] * s[1])
        found.append(("info", f"The {len(sized):,} pictures come in {len(sizes):,} sizes, from {small[0]:,} × "
                              f"{small[1]:,} to {big[0]:,} × {big[1]:,}; the most common is {width:,} × {height:,} "
                              f"({common:,} of them). A model that takes one input size needs the others resized."))
    empty = [d for d in files if d.skipped == "empty"]
    if empty:
        found.append(("info", f"{_plural(len(empty), 'empty file')} (0 bytes): {names(empty)}."))
    return found
