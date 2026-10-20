# SPDX-License-Identifier: LGPL-2.1-or-later
# SolidWorks Import for FreeCAD - https://github.com/djbarrow/SolidWorksImport
# Written by Denis Joseph Barrow <barrow_dj@yahoo.com> with the help of Claude Code.
# If you find this project useful, please support it with a 10 euro tip
# via PayPal to barrow_dj@yahoo.com.

"""Unpack a SolidWorks document (.SLDPRT / .SLDASM / .SLDDRW) into named streams.

Two container layouts exist:

* SolidWorks 2015 and later: a sequence of ZIP-like local headers, each
  followed by raw-DEFLATE data. Stream names are stored bit-rotated.
* SolidWorks 2014 and earlier: an OLE2 compound file (see cfb.py).
"""

import struct
import zlib

from . import cfb

SIGNATURE = b"\x14\x00\x06\x00\x08\x00"
MAX_INFLATED = 512 * 1024 * 1024


class ContainerError(ValueError):
    pass


def _rotr(b, n):
    return ((b >> n) | (b << (8 - n))) & 0xFF


def _decode_name(raw, rot):
    for n in (rot, 8 - rot if rot else 0):
        try:
            s = bytes(_rotr(b, n) for b in raw).decode("latin-1")
        except Exception:
            continue
        if s and all(32 <= ord(c) < 127 for c in s):
            return s
    return None


def _inflate_raw(data, expect):
    d = zlib.decompressobj(-15)
    out = d.decompress(data, MAX_INFLATED)
    if expect is not None and len(out) != expect:
        raise ContainerError("inflated size mismatch")
    return out


def read_modern(data):
    """Streams of a SolidWorks 2015+ file as {name: bytes}."""
    rot = data[7] & 7 if len(data) > 8 else 4
    out = {}
    pos = data.find(SIGNATURE, 0)
    while pos >= 0:
        h = pos - 4
        if h >= 0 and h + 30 <= len(data):
            crc, csize, usize, nlen, xlen = struct.unpack_from("<IIIHH", data, h + 14)
            name_at = h + 30
            if (crc >= 65536 and csize and nlen and xlen == 0
                    and name_at + nlen + csize <= len(data) and usize < MAX_INFLATED):
                name = _decode_name(data[name_at:name_at + nlen], rot)
                if name and name not in out:
                    try:
                        body = _inflate_raw(data[name_at + nlen:name_at + nlen + csize], usize)
                    except (zlib.error, ContainerError):
                        body = None
                    if body is not None:
                        if zlib.crc32(body) & 0xFFFFFFFF != crc:
                            raise ContainerError("CRC mismatch in stream %r" % name)
                        out[name] = body
                        pos = data.find(SIGNATURE, name_at + nlen + csize)
                        continue
        pos = data.find(SIGNATURE, pos + 1)
    if not out:
        raise ContainerError("no streams found; not a SolidWorks 2015+ file")
    return out


ZLB_GUID = bytes.fromhex("231dd571da8148a2a85898b21b89ef99")


def _unwrap_zlb(data):
    """Legacy `__ZLB` streams: GUID, u32 inflated size, u32 compressed size, zlib."""
    if len(data) < 24 or data[:16] != ZLB_GUID:
        return None
    usize, csize = struct.unpack_from("<II", data, 16)
    try:
        out = zlib.decompress(data[24:24 + csize])
    except zlib.error:
        return None
    return out if len(out) == usize else None


def read_streams(data):
    """Return (kind, {name: bytes}); kind is 'modern' or 'ole'.

    Legacy `name__ZLB` streams are also exposed, inflated, as `name`.
    """
    if cfb.is_cfb(data):
        streams = cfb.CompoundFile(data).streams()
        for name in list(streams):
            if name.endswith("__ZLB"):
                body = _unwrap_zlb(streams[name])
                if body is not None:
                    streams.setdefault(name[:-5], body)
        return "ole", streams
    return "modern", read_modern(data)


def find_stream(streams, suffix):
    """Find a stream whose path ends with `suffix` (paths differ by layout)."""
    if suffix in streams:
        return streams[suffix]
    for k, v in streams.items():
        if k.endswith("/" + suffix) or k.endswith(suffix):
            return v
    return None
