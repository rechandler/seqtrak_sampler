"""Read-only parser for SEQTRAK sound files (.Y3S).

See FORMAT.md for what is known about the layout. This module never writes
.Y3S files; it only extracts the audio.
"""

from __future__ import annotations

import posixpath
import struct
import sys
import xml.etree.ElementTree as ET
import zipfile
from array import array
from dataclasses import dataclass

from .sysex import NAME_SIZE, decode_name

KEYBANK_HEADER_SIZE = 4
KEYBANK_RECORD_SIZE = 64
# Offsets inside one 64-byte keybank record in waveform0.bin.
KB_ROOT_NOTE = 8
KB_SAMPLE_RATE = 36
KB_FRAME_COUNT = 60

# Refuse absurd member sizes so a malformed or hostile zip can't exhaust memory.
MAX_MEMBER_SIZE = 64 * 1024 * 1024


class Y3SError(ValueError):
    pass


@dataclass
class Keybank:
    number: int
    root_note: int
    sample_rate: int
    frames: array  # signed 16-bit mono PCM, native byte order


# element.bin starts with 4 bytes, then the name in the same 7-bit packed form
# the SEQTRAK uses over MIDI (see sysex.decode_7bit).
ELEMENT_NAME_OFFSET = 4


@dataclass
class Y3SSound:
    uuid: str
    content_number: int | None
    keybanks: list[Keybank]
    name: str = ""  # the sample's name on the SEQTRAK

    @property
    def sample_rate(self) -> int:
        return self.keybanks[0].sample_rate

    @property
    def frame_count(self) -> int:
        return len(self.keybanks[0].frames)


def _read_member(zf: zipfile.ZipFile, name: str) -> bytes:
    try:
        info = zf.getinfo(name)
    except KeyError:
        raise Y3SError(f"missing {name}") from None
    if info.file_size > MAX_MEMBER_SIZE:
        raise Y3SError(f"{name} is too large ({info.file_size} bytes)")
    return zf.read(info)


def _xml_int(root: ET.Element, tag: str) -> int | None:
    el = root.find(tag)
    if el is None or el.text is None or not el.text.strip():
        return None
    return int(el.text)


def _pcm16le(data: bytes) -> array:
    frames = array("h")
    frames.frombytes(data[: len(data) - len(data) % 2])
    if sys.byteorder == "big":
        frames.byteswap()
    return frames


def read_y3s(path) -> Y3SSound:
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise Y3SError("not a .Y3S file (not a zip archive)") from None

    with zf:
        folders = {n.split("/", 1)[0] for n in zf.namelist() if "/" in n}
        if len(folders) != 1:
            raise Y3SError(f"expected one sound folder, found {len(folders)}")
        folder = folders.pop()

        def member(name: str) -> bytes:
            return _read_member(zf, posixpath.join(folder, name))

        element = ET.fromstring(member("element.xml"))
        try:
            element_bin = member("element.bin")
            name = decode_name(list(element_bin[ELEMENT_NAME_OFFSET:
                                                ELEMENT_NAME_OFFSET + NAME_SIZE]))
        except Y3SError:
            name = ""
        waveform = member("waveform0.bin")
        if len(waveform) < KEYBANK_HEADER_SIZE:
            raise Y3SError("waveform0.bin is truncated")
        (count,) = struct.unpack_from("<I", waveform, 0)
        if len(waveform) < KEYBANK_HEADER_SIZE + count * KEYBANK_RECORD_SIZE:
            raise Y3SError("waveform0.bin is shorter than its keybank count")

        records = {}
        for i in range(count):
            base = KEYBANK_HEADER_SIZE + i * KEYBANK_RECORD_SIZE
            records[i] = (
                waveform[base + KB_ROOT_NOTE],
                struct.unpack_from("<I", waveform, base + KB_SAMPLE_RATE)[0],
                struct.unpack_from("<I", waveform, base + KB_FRAME_COUNT)[0],
            )

        keybanks = []
        for i in range(count):
            meta = ET.fromstring(member(f"sample{i}.xml"))
            number = _xml_int(meta, "KeybankNumber")
            if number not in records:
                raise Y3SError(f"sample{i}.xml names unknown keybank {number}")
            root_note, rate, frame_count = records[number]
            data = member(f"sample{i}.bin")
            size = _xml_int(meta, "DataSize")
            if size is not None and size != len(data):
                raise Y3SError(f"sample{i}.bin is {len(data)} bytes, expected {size}")
            frames = _pcm16le(data)
            if len(frames) != frame_count:
                raise Y3SError(
                    f"sample{i}.bin has {len(frames)} frames, keybank says {frame_count}"
                )
            keybanks.append(Keybank(number, root_note, rate, frames))

    if not keybanks:
        raise Y3SError("sound contains no samples")
    keybanks.sort(key=lambda kb: kb.number)
    if len({kb.sample_rate for kb in keybanks}) != 1:
        raise Y3SError("keybanks have different sample rates")
    if len({len(kb.frames) for kb in keybanks}) != 1:
        raise Y3SError("keybanks have different lengths")

    uuid = element.findtext("UUID") or folder
    return Y3SSound(uuid, _xml_int(element, "ContentNumber"), keybanks, name)
