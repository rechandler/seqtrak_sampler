"""SEQTRAK MIDI System Exclusive messages, from Yamaha's SEQTRAK Data List.

Only documented messages are used: Identity Request/Reply, Bulk Request,
Bulk Dump (received) and Parameter Change (sent). Page numbers refer to the
data list:
https://data.yamaha.com/files/download/other_assets/5/2226075/SEQTRAK_data_list_En_D0.pdf

Nothing here can move audio: sampler bulk dumps carry a pad's name, loop and
start/end points, but not the sample data itself (pp. 121, 152-154).
"""

from __future__ import annotations

from dataclasses import dataclass

YAMAHA = 0x43
GROUP_HI, GROUP_LO = 0x7F, 0x1C
MODEL = 0x0C
DEVICE = 0  # "n" in the data list

SAMPLER_PART = 10   # Part numbers: Kick=0 ... Perc2=6, Synth1/2=7/8, DX=9, Sampler=10
SAMPLER_PADS = 7    # Sampler elements 0-6
SAMPLE_RATE = 44100

# Bulk Control addresses (p. 122): 11 13 nn is the header of "Sampler Element
# Edit Buffer Element nn", i.e. pad nn of the current project.
BULK_HEADER = 0x11
BULK_FOOTER = 0x12
SAMPLER_ELEMENT_EDIT_BUFFER = 0x13
SOUND_EDIT_BUFFER = 0x02  # 11 02 pp: the sound of part pp, all its elements included

# SAMPLER Sample General block (pp. 152-154), at address 50 eA 00.
SAMPLE_GENERAL_HI = 0x50
NAME_OFFSET, NAME_SIZE = 0x00, 0x58
LOOP_ON = 0x5F
LOOP_LENGTH = 0x60
START_POINT = 0x66
END_POINT = 0x6A
MIN_LOOP_LENGTH = 0x50

# System General (p. 122): "USB MIDI Transmit System Exclusive Message". It
# defaults to Off, and while it is off the SEQTRAK answers no SysEx at all.
USB_SYSEX_TRANSMIT = (0x00, 0x00, 0x10)

REALTIME_START, REALTIME_CONTINUE, REALTIME_STOP = 0xFA, 0xFB, 0xFC

# AWM Sound Element "Oscillator / Amplitude / Pitch" block (p. 147), at 41 eA 00.
# Note Limit Low/High give the note range that plays each sampler pad.
OSCILLATOR_HI = 0x41
NOTE_LIMIT_LOW = 0x10
NOTE_LIMIT_HIGH = 0x11

SAMPLER_CHANNEL = 10  # MIDI channel 11 (p. 2)
MAX_FRAMES = int(16 * SAMPLE_RATE)


def identity_request() -> list[int]:
    return [0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7]


def parse_identity_reply(msg: list[int]) -> str | None:
    """Return the SEQTRAK OS version (e.g. "2.0") if msg is its Identity Reply."""
    if (len(msg) >= 15 and msg[:6] == [0xF0, 0x7E, msg[2], 0x06, 0x02, YAMAHA]
            and msg[8:10] == [0x64, 0x06]):
        return f"{1 + msg[10] / 10:.1f}"
    return None


def bulk_request(ah: int, am: int, al: int, device: int = DEVICE) -> list[int]:
    return [0xF0, YAMAHA, 0x20 | device, GROUP_HI, GROUP_LO, MODEL, ah, am, al, 0xF7]


def parameter_change(address: tuple[int, int, int], data: list[int],
                     device: int = DEVICE) -> list[int]:
    if any(not 0 <= b <= 0x7F for b in [*address, *data]):
        raise ValueError("SysEx data bytes must be 0-127")
    return [0xF0, YAMAHA, 0x10 | device, GROUP_HI, GROUP_LO, MODEL, *address, *data, 0xF7]


def enable_usb_sysex_transmit() -> list[int]:
    return parameter_change(USB_SYSEX_TRANSMIT, [0x01])


def pad_request(pad: int) -> list[int]:
    return bulk_request(BULK_HEADER, SAMPLER_ELEMENT_EDIT_BUFFER, pad)


def sampler_sound_request() -> list[int]:
    """Ask for the sampler track's whole sound: every pad's blocks in one dump."""
    return bulk_request(BULK_HEADER, SOUND_EDIT_BUFFER, SAMPLER_PART)


def sample_general_address(pad: int, offset: int = 0) -> tuple[int, int, int]:
    return (SAMPLE_GENERAL_HI, (pad << 4) | SAMPLER_PART, offset)


@dataclass
class BulkBlock:
    address: tuple[int, int, int]
    data: list[int]


def bulk_dump_problem(msg: list[int]) -> str | None:
    """Why msg isn't a SEQTRAK bulk dump this module can read, or None if it is."""
    if len(msg) < 4 or msg[0] != 0xF0 or msg[-1] != 0xF7:
        return "not a complete SysEx message"
    if msg[1] == YAMAHA and msg[2] & 0xF0 == 0x10:
        return "parameter change (not needed)"
    if len(msg) < 13 or msg[1] != YAMAHA or msg[2] & 0xF0 != 0x00:
        return "not a Yamaha bulk dump"
    if msg[3:5] != [GROUP_HI, GROUP_LO] or msg[7] != MODEL:
        return f"unexpected group/model {msg[3]:02X} {msg[4]:02X} / {msg[7]:02X}"
    # The data list (p. 115) says the checksum covers the byte count, address and
    # data, but a real SEQTRAK (OS 2.0) sums Model ID, address, data and checksum
    # to 0 in the low 7 bits, without the byte count.
    if sum(msg[7:-1]) & 0x7F:
        return "checksum mismatch"
    return None


def parse_bulk_dump(msg: list[int]) -> BulkBlock | None:
    """Parse F0 43 0n 7F 1C bh bl 0C ah am al data... cc F7 (p. 115).

    The byte count isn't enforced: the data runs from after the address to the
    checksum, whatever the count says.
    """
    if bulk_dump_problem(msg) is not None:
        return None
    return BulkBlock((msg[8], msg[9], msg[10]), msg[11:-2])


def decode_7bit(data: list[int]) -> bytes:
    """Yamaha 7-bit packing: each group is one byte of high bits, then up to 7 bytes."""
    out = bytearray()
    for i in range(0, len(data), 8):
        group = data[i:i + 8]
        msbs = group[0]
        for j, b in enumerate(group[1:]):
            out.append(b | (((msbs >> (6 - j)) & 1) << 7))
    return bytes(out)


def encode_7bit(raw: bytes) -> list[int]:
    out = []
    for i in range(0, len(raw), 7):
        chunk = raw[i:i + 7]
        msbs = 0
        for j, b in enumerate(chunk):
            msbs |= ((b >> 7) & 1) << (6 - j)
        out.append(msbs)
        out.extend(b & 0x7F for b in chunk)
    return out


def decode_name(data: list[int]) -> str:
    return decode_7bit(data).split(b"\0", 1)[0].decode("utf-8", errors="replace")


def encode_name(name: str) -> list[int]:
    """Encode a pad name for the 88-byte name field. Non-ASCII becomes "_"."""
    max_chars = NAME_SIZE // 8 * 7
    ascii_name = "".join(c if 32 <= ord(c) < 127 else "_" for c in name)[:max_chars]
    raw = ascii_name.encode("ascii").ljust(max_chars, b"\0")
    return encode_7bit(raw)


def decode_point(data: list[int]) -> int:
    """4-byte 24-bit value: 1st bit2-0 -> bit23-21, then 7 bits per byte."""
    b0, b1, b2, b3 = data
    return ((b0 & 0x07) << 21) | (b1 << 14) | (b2 << 7) | b3


def encode_point(value: int) -> list[int]:
    if not 0 <= value <= 0xFFFFFF:
        raise ValueError(f"point out of range: {value}")
    return [(value >> 21) & 0x07, (value >> 14) & 0x7F, (value >> 7) & 0x7F, value & 0x7F]


@dataclass
class SamplerPad:
    pad: int          # 0-6
    name: str
    start: int        # first frame
    end: int          # last frame (inclusive); 0 means the pad is empty
    loop_on: bool
    loop_length: int
    note_low: int | None = None   # from the oscillator block, once received
    note_high: int | None = None

    @property
    def note(self) -> int | None:
        """A note that plays this pad."""
        return self.note_low

    @property
    def empty(self) -> bool:
        return self.end == 0

    @property
    def seconds(self) -> float:
        return 0.0 if self.empty else (self.end - self.start + 1) / SAMPLE_RATE


def parse_sample_general(pad: int, data: list[int]) -> SamplerPad:
    if len(data) < END_POINT + 4:
        raise ValueError("Sample General block is too short")
    return SamplerPad(
        pad=pad,
        name=decode_name(data[NAME_OFFSET:NAME_OFFSET + NAME_SIZE]),
        start=decode_point(data[START_POINT:START_POINT + 4]),
        end=decode_point(data[END_POINT:END_POINT + 4]),
        loop_on=bool(data[LOOP_ON]),
        loop_length=decode_point(data[LOOP_LENGTH:LOOP_LENGTH + 4]),
    )


def pad_notes_from_block(block: BulkBlock) -> tuple[int, int, int] | None:
    """Return (pad, note_low, note_high) if this is a sampler pad's oscillator block."""
    hi, mid, lo = block.address
    if hi != OSCILLATOR_HI or lo != 0 or mid & 0x0F != SAMPLER_PART:
        return None
    pad = mid >> 4
    if pad >= SAMPLER_PADS or len(block.data) <= NOTE_LIMIT_HIGH:
        return None
    return pad, block.data[NOTE_LIMIT_LOW], block.data[NOTE_LIMIT_HIGH]


def note_on(note: int, velocity: int = 100) -> list[int]:
    return [0x90 | SAMPLER_CHANNEL, note & 0x7F, velocity & 0x7F]


def note_off(note: int) -> list[int]:
    return [0x80 | SAMPLER_CHANNEL, note & 0x7F, 0]


def points_messages(pad: int, start: int, end: int) -> list[list[int]]:
    """Set a pad's start and (inclusive) end point exactly as given."""
    return [
        parameter_change(sample_general_address(pad, START_POINT), encode_point(start)),
        parameter_change(sample_general_address(pad, END_POINT), encode_point(end)),
    ]


def pad_from_block(block: BulkBlock) -> SamplerPad | None:
    """Return the pad if this bulk block is a sampler pad's Sample General block."""
    hi, mid, lo = block.address
    if hi != SAMPLE_GENERAL_HI or lo != 0 or mid & 0x0F != SAMPLER_PART:
        return None
    pad = mid >> 4
    if pad >= SAMPLER_PADS:
        return None
    return parse_sample_general(pad, block.data)


def end_point_for(end_exclusive: int, last_frame: int) -> int:
    """The SEQTRAK's end point always has bits 3-0 set (p. 154)."""
    end = (end_exclusive - 1) | 0x0F
    while end > last_frame and end > 0x0F:
        end -= 0x10
    return end


def trim_messages(pad: SamplerPad, start: int, end_exclusive: int, last_frame: int,
                  name: str | None = None) -> list[list[int]]:
    """Parameter Change messages that set a pad's start/end points (and name)."""
    end = end_point_for(end_exclusive, last_frame)
    if not 0 <= start < end:
        raise ValueError("selection is too short for the SEQTRAK")
    msgs = points_messages(pad.pad, start, end)
    length = end - start + 1
    if pad.loop_on and pad.loop_length > length:
        msgs.append(parameter_change(sample_general_address(pad.pad, LOOP_LENGTH),
                                     encode_point(max(MIN_LOOP_LENGTH, length))))
    if name is not None and name != pad.name:
        for i, b in enumerate(encode_name(name)):
            msgs.append(parameter_change(sample_general_address(pad.pad, NAME_OFFSET + i), [b]))
    return msgs
