"""
🐍 Use in Python: the lines that load a file the explorer shows (or the files ticked in its list) into a notebook
cell, with the same AWS credentials, written for its format. Pure: a path in, code out.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..readers.formats import detect_format

_TABLE_READERS = {
    "csv": "pd.read_csv(f)",
    "tsv": "pd.read_csv(f, sep='\\t')",
    "psv": "pd.read_csv(f, sep='|')",
    "jsonl": "pd.read_json(f, lines=True)",
}

_SEEKING_READERS = {
    "parquet": "pd.read_parquet",
    "orc": "pd.read_orc",
    "arrow": "pd.read_feather",
    "excel": "pd.read_excel",
}

_OTHER_READERS = {
    "avro": (("import fastavro", "import pandas as pd"), "df = pd.DataFrame(fastavro.reader(f))"),
    "json": (("import json",), "data = json.load(f)"),
    "notebook": (("import json",), "notebook = json.load(f)"),
    "text": ((), "text = f.read().decode('utf-8')"),
    "image": (("import io", "from PIL import Image"), "picture = Image.open(io.BytesIO(f.read()))"),
    "pdf": (("import io", "from pypdf import PdfReader"),
            "pages = [page.extract_text() for page in PdfReader(io.BytesIO(f.read())).pages]"),
    "docx": (("import io", "import docx  # pip install python-docx"),
             "paragraphs = [p.text for p in docx.Document(io.BytesIO(f.read())).paragraphs]"),
    "zip": (("import io", "import zipfile"), "archive = zipfile.ZipFile(io.BytesIO(f.read()))"),
    "tar": (("import io", "import tarfile"), "archive = tarfile.open(fileobj=io.BytesIO(f.read()))"),
    "npy": (("import io", "import numpy as np"), "array = np.load(io.BytesIO(f.read()))"),
    "npz": (("import io", "import numpy as np"), "arrays = np.load(io.BytesIO(f.read()))"),
    "safetensors": (("from safetensors.numpy import load",), "tensors = load(f.read())"),
    "torch": (("import io", "import torch"), "state = torch.load(io.BytesIO(f.read()), weights_only=True)"),
    "pickle": (("import pickle",), "obj = pickle.load(f)"),
}

_FORMAT_NOTES = {
    "pickle": "Only load a pickle you trust: loading one runs whatever code its writer put in it.",
    "avro": "Avro needs fastavro (pip install fastavro).",
    "pdf": "PDFs need pypdf (pip install pypdf).",
}

_BYTES_LINE = "data = f.read()  # the file's bytes"


@dataclass
class Snippet:
    """
    The code to copy, and a sentence about it (what it needs, or what to watch out for); `note` may be empty.
    """

    code: str
    note: str = ""


def _analyzer_line(profile: str) -> str:
    """
    How the snippet makes its S3Analyzer: with the explorer's profile, when it isn't the default one.
    """
    if profile and profile != "default":
        return f"s3 = S3Analyzer(profile={profile!r})"
    return "s3 = S3Analyzer()"


def _reading(fmt: str | None) -> tuple[tuple[str, ...], str]:
    """
    The imports and the one line that reads an open file `f` of this format into a variable.
    """
    if fmt in _TABLE_READERS:
        return ("import pandas as pd",), f"df = {_TABLE_READERS[fmt]}"
    if fmt in _SEEKING_READERS:
        return ("import io", "import pandas as pd"), f"df = {_SEEKING_READERS[fmt]}(io.BytesIO(f.read()))"
    if fmt in _OTHER_READERS:
        return _OTHER_READERS[fmt]
    return (), _BYTES_LINE


def _note(fmt: str | None, compression: str | None) -> str:
    """
    What the snippet needs, or what to watch for, in one sentence.
    """
    parts = []
    if compression:
        parts.append(f"s3.open() undoes the .{compression} compression as it reads.")
    if fmt in _SEEKING_READERS:
        parts.append(f"{fmt.capitalize()} is read whole into memory first, because pandas jumps around in the file.")
    if fmt in _FORMAT_NOTES:
        parts.append(_FORMAT_NOTES[fmt])
    return " ".join(parts)


def _imports(extra: tuple[str, ...]) -> list[str]:
    """
    The import lines, standard library first, each once, then aws-analyzer's.
    """
    lines = sorted(set(extra), key=lambda line: (line.startswith("from"), line))
    return lines + ["from aws_analyzer import S3Analyzer"]


def file_snippet(uri: str, *, profile: str = "") -> Snippet:
    """
    The lines that load one file into a variable named for what it holds (df for a table, data for JSON, ...).

    file_snippet("s3://lake/raw/day.csv.gz").code ->
        import pandas as pd
        from aws_analyzer import S3Analyzer

        s3 = S3Analyzer()
        path = "s3://lake/raw/day.csv.gz"
        with s3.open(path) as f:
            df = pd.read_csv(f)
    """
    fmt, compression = detect_format(uri)
    imports, read = _reading(fmt)
    lines = _imports(imports) + ["", _analyzer_line(profile), f'path = "{uri}"', "with s3.open(path) as f:",
                                 f"    {read}"]
    if read.startswith("df ="):
        lines.append("df.head()")
    return Snippet("\n".join(lines), _note(fmt, compression))


def _table_format(uris: list[str]) -> str | None:
    """
    The one table format every path is in, so they can go in one DataFrame; None when they're mixed or aren't
    tables (or a path is a folder).
    """
    formats = {detect_format(uri)[0] for uri in uris}
    if any(uri.endswith("/") for uri in uris) or len(formats) != 1:
        return None
    fmt = formats.pop()
    return fmt if fmt in _TABLE_READERS or fmt in _SEEKING_READERS else None


def files_snippet(uris: list[str], *, profile: str = "") -> Snippet:
    """
    The lines for several files: one DataFrame when they're all tables of the same format, else the list of their
    paths and a loop that reads each one's bytes. A folder's path lists its files with s3.iter_objects.
    """
    paths = ["paths = ["] + [f'    "{uri}",' for uri in uris] + ["]"]
    fmt = _table_format(uris)
    head = ["", _analyzer_line(profile)] + paths
    if fmt is not None:
        imports, read = _reading(fmt)
        reader = read.removeprefix("df = ")
        body = ["frames = []", "for path in paths:", "    with s3.open(path) as f:",
                f"        frames.append({reader})", "df = pd.concat(frames, ignore_index=True)", "df.head()"]
        return Snippet("\n".join(_imports(imports) + head + body), _note(fmt, None))
    has_folders = any(uri.endswith("/") for uri in uris)
    body = ["contents = {}", "for path in paths:"]
    if has_folders:
        body += ["    if path.endswith('/'):  # a folder: every file below it",
                 "        for obj in s3.iter_objects(path):",
                 "            with s3.open(obj.uri) as f:",
                 "                contents[obj.uri] = f.read()",
                 "        continue"]
    body += ["    with s3.open(path) as f:", "        contents[path] = f.read()  # each file's bytes"]
    note = ("They aren't all tables of one format, so this reads each file's bytes: "
            "🐍 Use in Python on one file shows how to read its format.")
    return Snippet("\n".join(_imports(()) + head + body), note)
