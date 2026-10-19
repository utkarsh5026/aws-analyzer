"""
File-like readers over an S3 object: its streamed body, ranged reads, and the blocks
file_details() reads within its budget.
"""

from __future__ import annotations

import io
import threading
from collections import OrderedDict
from typing import Any

from ..._kit.fmt import human_size

from ..helpers import KB, MB
from ..models import ObjectInfo
from ..readers.formats import _ReadLimit


class _BodyReader(io.RawIOBase):
    """
    Raw stream over a botocore StreamingBody, so io/gzip/pandas can wrap it.
    """

    def __init__(self, body: Any):
        self._body = body
        self.bytes_read = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        data = self._body.read(len(buffer))
        buffer[: len(data)] = data
        self.bytes_read += len(data)
        return len(data)

    def close(self) -> None:
        if not self.closed:
            self._body.close()
        super().close()


class _RangeReader(io.RawIOBase):
    """
    Seekable reader over one object using ranged GETs, so pyarrow can fetch
    just the parquet footer / needed row groups instead of the whole file.
    """

    def __init__(self, client: Any, bucket: str, key: str, size: int):
        self._client, self._bucket, self._key, self._size, self._pos = (
            client,
            bucket,
            key,
            size,
            0,
        )

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        origin = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self._size}[
            whence
        ]
        self._pos = max(0, origin + offset)
        return self._pos

    def readinto(self, buffer: Any) -> int:
        if self._pos >= self._size or not len(buffer):
            return 0
        end = min(self._pos + len(buffer), self._size) - 1
        data = self._client.get_object(
            Bucket=self._bucket, Key=self._key, Range=f"bytes={self._pos}-{end}"
        )["Body"].read()
        buffer[: len(data)] = data
        self._pos += len(data)
        return len(data)


DETAILS_BLOCK = 256 * KB  # file_details reads files in blocks of this size, with ranged GETs

DETAILS_FILE_MAX = 128 * MB  # and stops reading any one file after this much


def _stopped(why: str) -> str:
    """
    Why _BlockReader stopped ('max_read' or 'file'), as words.
    """
    if why == "max_read":
        return "stopped when max_read was reached"
    return f"stopped after reading {human_size(DETAILS_FILE_MAX)} of it, the most read from one file"


class _ReadBudget:
    """
    Bytes and requests one file_details call has read, against its max_read (None = no cap).
    """

    def __init__(self, limit: int | None):
        self.limit, self.used, self.requests = limit, 0, 0
        self._lock = threading.Lock()

    def exhausted(self) -> bool:
        return self.limit is not None and self.used >= self.limit

    def add(self, size: int) -> None:
        with self._lock:
            self.used += size
            self.requests += 1


class _BlockReader(io.RawIOBase):
    """
    Seekable reader over one object for file_details: ranged GETs of 256 KB blocks, fetched only when a parser
    reads into them and kept (up to 64), so a PDF's page tree or a parquet footer costs a few requests. Stops with
    _ReadLimit at the shared budget or after DETAILS_FILE_MAX of this file, and says why in `stopped`.
    """

    def __init__(self, client: Any, obj: ObjectInfo, budget: _ReadBudget, stop: threading.Event):
        self._client, self._obj, self._budget, self._stop = client, obj, budget, stop
        self._pos = 0
        self._blocks: OrderedDict[int, bytes] = OrderedDict()
        self.bytes_read = 0
        self.stopped = ""  # why it stopped reading: 'max_read' or 'file' (DETAILS_FILE_MAX)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        origin = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self._obj.size}[whence]
        self._pos = max(0, origin + offset)
        return self._pos

    def readinto(self, buffer: Any) -> int:
        if self._pos >= self._obj.size or not len(buffer):
            return 0
        number, offset = divmod(self._pos, DETAILS_BLOCK)
        block = self._block(number)
        count = min(len(buffer), len(block) - offset)
        if count <= 0:
            return 0
        buffer[:count] = block[offset : offset + count]
        self._pos += count
        return count

    def _block(self, number: int) -> bytes:
        if number in self._blocks:
            self._blocks.move_to_end(number)
            return self._blocks[number]
        if self._stop.is_set():
            raise _ReadLimit("stopped")
        if self.bytes_read >= DETAILS_FILE_MAX:
            self.stopped = "file"
            raise _ReadLimit(_stopped("file"))
        if self._budget.exhausted():
            self.stopped = "max_read"
            raise _ReadLimit(_stopped("max_read"))
        start = number * DETAILS_BLOCK
        end = min(start + DETAILS_BLOCK, self._obj.size) - 1
        match = {"IfMatch": f'"{self._obj.etag}"'} if self._obj.etag else {}  # the file listed, not one written since
        body = self._client.get_object(
            Bucket=self._obj.bucket, Key=self._obj.key, Range=f"bytes={start}-{end}", **match
        )["Body"]
        try:
            data = body.read()
        finally:
            body.close()
        self.bytes_read += len(data)
        self._budget.add(len(data))
        self._blocks[number] = data
        if len(self._blocks) > 64:
            self._blocks.popitem(last=False)
        return data
