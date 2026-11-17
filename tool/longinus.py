"""Longinus.dat reader (天之圣杯汉化组 patch container).

Layout:
    +0   char[12]  "Longinus\\x01\\x04\\x00\\x01"
    +12  u32       entry count
    +16  { u32 name_hash, u32 size, u32 offset }[count]
    ...  entry data, contiguous, in table order

name_hash (Longinus.dll 0x100015B0):
    h = 0; for byte in utf16le(name.upper()): h = h * 131 + byte   (u32 wrapping)
    key = h & 0x7FFFFFFF

Script entries hold an already-decoded PS2A *memory image*: the 48 byte header
keeps the original key/compressed_size fields, but the payload after it is the
plain (transformed + decompressed) bytes, so `ps3.Script.decode` must not be
used on them.  `read_script` below builds a `ps3.Script` directly.
"""
from __future__ import annotations

import struct

from ps3 import HEADER_SIZE, Script

MAGIC = b"Longinus\x01\x04\x00\x01"


def name_hash(name: str) -> int:
    h = 0
    for byte in name.upper().encode("utf-16-le"):
        h = (h * 131 + byte) & 0xFFFFFFFF
    return h & 0x7FFFFFFF


class Patch:
    def __init__(self, raw: bytes):
        if raw[:12] != MAGIC:
            raise ValueError("unsupported Longinus header")
        count = struct.unpack_from("<I", raw, 12)[0]
        end = 16 + count * 12
        if end > len(raw):
            raise ValueError("truncated index")
        self.entries = {}
        offset = end
        for at in range(16, end, 12):
            key, size, off = struct.unpack_from("<III", raw, at)
            if key in self.entries:
                raise ValueError("duplicate key %08x" % key)
            if off != offset or off + size > len(raw):
                raise ValueError("entry %08x out of order or truncated" % key)
            self.entries[key] = raw[off:off + size]
            offset = off + size
        if offset != len(raw):
            raise ValueError("trailing bytes in Longinus.dat")

    @classmethod
    def open(cls, path: str) -> "Patch":
        with open(path, "rb") as stream:
            return cls(stream.read())

    def scripts(self):
        return {key: data for key, data in self.entries.items() if data[:4] == b"PS2A"}

    def pictures(self):
        return {key: data for key, data in self.entries.items() if data[:4] != b"PS2A"}


def read_script(data: bytes) -> Script:
    """Parse a PS2A memory image (plain payload, stale key/compressed_size)."""
    if data[:4] != b"PS2A":
        raise ValueError("not a PS2A image")
    header = list(struct.unpack_from("<11I", data, 4))
    if header[0] != HEADER_SIZE:
        raise ValueError("unexpected header length %d" % header[0])
    body = data[HEADER_SIZE:]
    total = header[3] * 4 + header[4] + header[5] + header[6]
    if header[3] * 4 + header[4] + header[5] > len(body):
        raise ValueError("image shorter than its segment table")
    if len(body) != total:
        # 24 pinned scripts omit one or two trailing empty-string terminators.
        delta = total - len(body)
        if delta not in (1, 2) or body[-1:] != b"\0":
            raise ValueError("image %d bytes, header declares %d" % (len(body), total))
        body += b"\0" * delta
        header[6] = total - header[3] * 4 - header[4] - header[5]
    # normalise the stale file fields so the image re-encodes as-is
    header[8] = 0
    header[9] = total
    return Script(header, body)
