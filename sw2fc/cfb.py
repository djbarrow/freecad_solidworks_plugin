# SPDX-License-Identifier: LGPL-2.1-or-later
# SolidWorks Import for FreeCAD - https://github.com/djbarrow/freecad_solidworks_plugin
# Written by Denis Joseph Barrow <barrow_dj@yahoo.com> with the help of Claude Code.
# If you find this project useful, please support it with a 10 euro tip
# via PayPal to barrow_dj@yahoo.com.

"""Minimal reader for Microsoft Compound File Binary (OLE2) containers.

SolidWorks used this container up to the 2014 release. Only reading is
supported, and every sector reference is bounds- and cycle-checked so that a
damaged file raises CFBError instead of looping or reading garbage.
"""

import struct

MAGIC = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"

FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
NOSTREAM = 0xFFFFFFFF

MAX_STREAM = 512 * 1024 * 1024


class CFBError(ValueError):
    pass


def is_cfb(data):
    return data[:8] == MAGIC


class CompoundFile:
    def __init__(self, data):
        if not is_cfb(data):
            raise CFBError("not an OLE2 compound file")
        self.data = data
        (minor, major, bom, sec_shift, mini_shift) = struct.unpack_from("<HHHHH", data, 0x18)
        if bom != 0xFFFE:
            raise CFBError("bad byte-order mark")
        if sec_shift not in (9, 12) or mini_shift != 6:
            raise CFBError("unsupported sector sizes")
        self.sector_size = 1 << sec_shift
        self.mini_size = 1 << mini_shift
        (n_fat, first_dir, _sig, self.mini_cutoff, first_minifat, n_minifat,
         first_difat, n_difat) = struct.unpack_from("<IIIIIIII", data, 0x2C)
        self.n_sectors = max(0, (len(data) - self.sector_size) // self.sector_size)

        difat = list(struct.unpack_from("<109I", data, 0x4C))
        sect = first_difat
        seen = set()
        per = self.sector_size // 4 - 1
        for _ in range(n_difat):
            if sect in (ENDOFCHAIN, FREESECT):
                break
            if sect in seen or sect >= self.n_sectors:
                raise CFBError("bad DIFAT chain")
            seen.add(sect)
            vals = struct.unpack_from("<%dI" % (per + 1), data, self._offset(sect))
            difat.extend(vals[:per])
            sect = vals[per]
        fat_sectors = [s for s in difat if s not in (FREESECT, ENDOFCHAIN)][:n_fat]
        fat = []
        for s in fat_sectors:
            if s >= self.n_sectors:
                raise CFBError("FAT sector out of range")
            fat.extend(struct.unpack_from("<%dI" % (self.sector_size // 4), data, self._offset(s)))
        self.fat = fat

        dir_bytes = self._read_chain(first_dir, None)
        self.entries = []
        for i in range(len(dir_bytes) // 128):
            raw = dir_bytes[i * 128:(i + 1) * 128]
            name_len = struct.unpack_from("<H", raw, 64)[0]
            name = raw[:max(0, name_len - 2)].decode("utf-16-le", "replace")
            etype = raw[66]
            left, right, child = struct.unpack_from("<III", raw, 68)
            start = struct.unpack_from("<I", raw, 116)[0]
            size = struct.unpack_from("<Q", raw, 120)[0]
            if major == 3:
                size &= 0xFFFFFFFF
            self.entries.append(dict(name=name, type=etype, left=left, right=right,
                                     child=child, start=start, size=size))
        if not self.entries or self.entries[0]["type"] != 5:
            raise CFBError("missing root entry")

        root = self.entries[0]
        self.ministream = self._read_chain(root["start"], root["size"]) if root["size"] else b""
        self.minifat = []
        if n_minifat and first_minifat not in (ENDOFCHAIN, FREESECT):
            mf = self._read_chain(first_minifat, None)
            self.minifat = list(struct.unpack_from("<%dI" % (len(mf) // 4), mf, 0))

        self.paths = {}
        self._walk(root["child"], "", set())

    def _offset(self, sect):
        return (sect + 1) * self.sector_size

    def _read_chain(self, start, size):
        out = []
        sect = start
        seen = set()
        total = 0
        while sect not in (ENDOFCHAIN, FREESECT):
            if sect in seen or sect >= len(self.fat) or sect >= self.n_sectors:
                raise CFBError("bad sector chain")
            seen.add(sect)
            off = self._offset(sect)
            out.append(self.data[off:off + self.sector_size])
            total += self.sector_size
            if total > MAX_STREAM:
                raise CFBError("stream too large")
            if size is not None and total >= size:
                break
            sect = self.fat[sect]
        buf = b"".join(out)
        return buf if size is None else buf[:size]

    def _read_mini(self, start, size):
        out = []
        sect = start
        seen = set()
        total = 0
        while sect not in (ENDOFCHAIN, FREESECT) and total < size:
            if sect in seen or sect >= len(self.minifat):
                raise CFBError("bad mini chain")
            seen.add(sect)
            off = sect * self.mini_size
            out.append(self.ministream[off:off + self.mini_size])
            total += self.mini_size
            sect = self.minifat[sect]
        return b"".join(out)[:size]

    def _walk(self, idx, prefix, seen):
        stack = [(idx, prefix)]
        while stack:
            i, pre = stack.pop()
            if i == NOSTREAM or i >= len(self.entries):
                continue
            if i in seen:
                raise CFBError("directory cycle")
            seen.add(i)
            e = self.entries[i]
            stack.append((e["left"], pre))
            stack.append((e["right"], pre))
            path = pre + e["name"]
            if e["type"] == 2:
                self.paths[path] = e
            elif e["type"] == 1:
                stack.append((e["child"], path + "/"))

    def streams(self):
        """Return {path: bytes} for every stream in the file."""
        return {p: self.read(p) for p in self.paths}

    def read(self, path):
        e = self.paths[path]
        size = e["size"]
        if size > MAX_STREAM:
            raise CFBError("stream too large")
        if size < self.mini_cutoff:
            return self._read_mini(e["start"], size)
        return self._read_chain(e["start"], size)
