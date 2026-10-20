# SPDX-License-Identifier: LGPL-2.1-or-later
# SolidWorks Import for FreeCAD - https://github.com/djbarrow/freecad_solidworks_plugin
# Written by Denis Joseph Barrow <barrow_dj@yahoo.com> with the help of Claude Code.
# If you find this project useful, please support it with a 10 euro tip
# via PayPal to barrow_dj@yahoo.com.

"""Locate the Parasolid transmit data embedded in a SolidWorks part.

Partition-bearing streams are a chain of sections:

    u32le size (= compressed + 32)
    16-byte GUID 231dd571-da81-48a2-a858-98b21b89ef99
    u32le inflated length
    u32le compressed length
    zlib data
    8 zero bytes
"""

import struct
import zlib

GUID = bytes.fromhex("231dd571da8148a2a85898b21b89ef99")


class PartitionError(ValueError):
    pass


def sections(stream):
    """Yield inflated section payloads of a GUID-framed stream (any prefix is skipped)."""
    out = []
    pos = stream.find(GUID)
    if pos < 4:
        return out
    pos -= 4
    while pos + 28 <= len(stream):
        size = struct.unpack_from("<I", stream, pos)[0]
        if stream[pos + 4:pos + 20] != GUID:
            nxt = stream.find(GUID, pos + 1)
            if nxt < 4:
                break
            pos = nxt - 4
            continue
        inflated, compressed = struct.unpack_from("<II", stream, pos + 20)
        try:
            body = zlib.decompress(stream[pos + 28:pos + 28 + compressed])
        except zlib.error:
            break
        if len(body) != inflated:
            break
        # Large sections continue with further chunks after the first zlib member:
        #   u32le inflated, u32le compressed, zlib ...   up to the section end
        end = pos + 4 + size
        chunks = [body]
        q = pos + 28 + compressed
        while q + 8 <= end - 8:
            ci, cc = struct.unpack_from("<II", stream, q)
            if not cc or q + 8 + cc > end:
                break
            try:
                part = zlib.decompress(stream[q + 8:q + 8 + cc])
            except zlib.error:
                break
            if len(part) != ci:
                break
            chunks.append(part)
            q += 8 + cc
        out.append(b"".join(chunks))
        pos += 4 + max(size, compressed + 32)
    return out


def kind_of(section):
    head = section[:96].decode("latin-1", "replace")
    if "TRANSMIT FILE (partition)" in head:
        return "partition"
    if "TRANSMIT FILE (deltas)" in head:
        return "deltas"
    if "TRANSMIT FILE" in head:
        return "part"
    return "unknown"


def candidates(streams):
    """Every Parasolid section found, best candidates first.

    Returns a list of (stream_name, index, kind, payload).
    """
    found = []
    for name, data in streams.items():
        if GUID not in data:
            continue
        for i, sec in enumerate(sections(data)):
            k = kind_of(sec)
            if k in ("partition", "part"):
                found.append((name, i, k, sec))

    def rank(item):
        name, i, k, _ = item
        base = name.rsplit("/", 1)[-1]
        if base == "Config-0-Partition":
            r = 0
        elif "LocalBodies" in name:
            r = 1
        elif base.startswith("Config-") and base.endswith("-Partition"):
            r = 2
        elif "GhostPartition" in base:
            r = 5
        elif "ResolvedFeatures" in base:
            r = 9
        else:
            r = 4
        return (r, name, i)

    found.sort(key=rank)
    return found
