"""
Pictures, audio and video: their size, length and codecs, from the first bytes of the file.
"""

from __future__ import annotations

import struct
from typing import Any, Iterator

from ..helpers import MB


def _image_info(data: bytes) -> dict[str, Any] | None:
    """
    {'kind': 'PNG', 'width': .., 'height': ..} from the first bytes of a PNG, JPEG, GIF, BMP, WebP or TIFF
    (a JPEG with a big thumbnail can need more than 64 KB); None when they don't say.
    """
    kind, size = None, None
    if data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR" and len(data) >= 24:
        kind, size = "PNG", struct.unpack(">II", data[16:24])
    elif data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        kind, size = "GIF", struct.unpack("<HH", data[6:10])
    elif data.startswith(b"BM") and len(data) >= 26:
        if int.from_bytes(data[14:18], "little") == 12:  # the old OS/2 header
            kind, size = "BMP", struct.unpack("<HH", data[18:22])
        else:
            width, height = struct.unpack("<ii", data[18:26])
            kind, size = "BMP", (width, abs(height))
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
        chunk = data[12:16]
        if chunk == b"VP8 ":
            width, height = struct.unpack("<HH", data[26:30])
            kind, size = "WebP", (width & 0x3FFF, height & 0x3FFF)
        elif chunk == b"VP8L":
            bits = int.from_bytes(data[21:25], "little")
            kind, size = "WebP", ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
        elif chunk == b"VP8X":
            kind = "WebP"
            size = (int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1)
    elif data.startswith(b"\xff\xd8"):
        pos = 2
        while pos + 9 <= len(data) and data[pos] == 0xFF:
            marker = data[pos + 1]
            if marker == 0xFF:  # fill byte
                pos += 1
                continue
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):  # start of frame: the picture's size
                height, width = struct.unpack(">HH", data[pos + 5 : pos + 9])
                kind, size = "JPEG", (width, height)
                break
            if marker in (0xD9, 0xDA):  # end of image / start of the pixels, with no size before it
                break
            pos += 2 + int.from_bytes(data[pos + 2 : pos + 4], "big")
    elif data[:4] in (b"II*\x00", b"MM\x00*") and len(data) >= 8:
        order: Any = "little" if data[:2] == b"II" else "big"
        pos = int.from_bytes(data[4:8], order)
        found: dict[int, int] = {}
        if pos + 2 <= len(data):
            for i in range(min(int.from_bytes(data[pos : pos + 2], order), 200)):
                entry = data[pos + 2 + 12 * i : pos + 14 + 12 * i]
                if len(entry) < 12:
                    break
                tag, kind_of = int.from_bytes(entry[:2], order), int.from_bytes(entry[2:4], order)
                if tag in (256, 257):  # ImageWidth, ImageLength: a SHORT or a LONG
                    found[tag] = int.from_bytes(entry[8:10] if kind_of == 3 else entry[8:12], order)
        if 256 in found and 257 in found:
            kind, size = "TIFF", (found[256], found[257])
    if kind is None or size is None or not all(size):
        return None
    return {"kind": kind, "width": size[0], "height": size[1]}


def _audio_info(data: bytes) -> dict[str, Any] | None:
    """
    {'kind', 'duration' (seconds), 'sample_rate', 'channels'} from the first bytes of a WAV or FLAC file.
    """
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        info: dict[str, Any] = {"kind": "WAV"}
        pos, byte_rate = 12, 0
        while pos + 8 <= len(data):
            chunk, size = data[pos : pos + 4], int.from_bytes(data[pos + 4 : pos + 8], "little")
            if chunk == b"fmt " and pos + 20 <= len(data):
                channels, rate, byte_rate = struct.unpack("<HII", data[pos + 10 : pos + 20])
                info.update(channels=channels, sample_rate=rate)
            elif chunk == b"data":
                if byte_rate and 0 < size < 0xFFFFFFFF:  # streamed WAVs leave the size unset
                    info["duration"] = size / byte_rate
                break
            pos += 8 + size + (size & 1)
        return info
    if data[:4] == b"fLaC" and len(data) >= 26 and data[4] & 0x7F == 0:  # STREAMINFO comes first
        bits = int.from_bytes(data[18:26], "big")
        rate, channels, samples = bits >> 44, ((bits >> 41) & 0x7) + 1, bits & ((1 << 36) - 1)
        info = {"kind": "FLAC", "sample_rate": rate, "channels": channels}
        if rate and samples:
            info["duration"] = samples / rate
        return info
    return None


def _boxes(data: bytes) -> Iterator[tuple[bytes, bytes]]:
    """
    (type, contents) of each box in an MP4 / MOV box list.
    """
    pos = 0
    while pos + 8 <= len(data):
        size, kind, start = int.from_bytes(data[pos : pos + 4], "big"), data[pos + 4 : pos + 8], pos + 8
        if size == 1:
            size, start = int.from_bytes(data[pos + 8 : pos + 16], "big"), pos + 16
        elif size == 0:
            size = len(data) - pos
        if size < start - pos or pos + size > len(data):
            return
        yield kind, data[start : pos + size]
        pos += size


def _mp4_info(source: Any, size: int) -> dict[str, Any] | None:
    """
    {'duration', 'width', 'height'} of an MP4 / MOV / M4A from its 'moov' box, found by hopping from box header
    to box header (the media data in between isn't read). None when there's no moov box.
    """
    pos = 0
    while pos + 8 <= size:
        source.seek(pos)
        header = source.read(16)
        length, kind, start = int.from_bytes(header[:4], "big"), header[4:8], pos + 8
        if length == 1 and len(header) == 16:
            length, start = int.from_bytes(header[8:16], "big"), pos + 16
        elif length == 0:
            length = size - pos
        if length < start - pos:
            return None
        if kind == b"moov":
            if length > 64 * MB:
                return None
            source.seek(start)
            moov = source.read(length - (start - pos))
            break
        pos += length
    else:
        return None
    info: dict[str, Any] = {}
    for kind, body in _boxes(moov):
        if kind == b"mvhd" and len(body) >= 32:
            timescale, duration = (struct.unpack(">IQ", body[20:32]) if body[0] == 1
                                   else struct.unpack(">II", body[12:20]))
            if timescale:
                info["duration"] = duration / timescale
        elif kind == b"trak" and "width" not in info:
            for sub, header in _boxes(body):
                if sub == b"tkhd" and len(header) >= 84:
                    width, height = int.from_bytes(header[-8:-4], "big") >> 16, int.from_bytes(header[-4:], "big") >> 16
                    if width and height:
                        info.update(width=width, height=height)
    return info
