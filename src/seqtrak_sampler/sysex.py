"""SEQTRAK MIDI System Exclusive messages, from Yamaha's SEQTRAK Data List.

Only documented messages are used: Identity Request/Reply, Bulk Request,
Bulk Dump (received) and Parameter Change (sent). Page numbers refer to the
data list:
https://data.yamaha.com/files/download/other_assets/5/2226075/SEQTRAK_data_list_En_D0.pdf

Nothing here can move audio: sampler bulk dumps carry a pad's name, loop and
start/end points, but not the sample data itself (pp. 121, 152-154).
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
# 11 14 nn - 11 1B nn: Sampler Element User 1-8, element nn (p. 122). Under Bank
# Select MSB 62, programs 72-127 of User 8 are "temporary areas for the sampler
# function: Project 1 Elements 1..7, Project 2 Elements 1..7, ..." (pp. 116-117),
# i.e. where each project's pad recordings are kept.
SAMPLER_ELEMENT_USER_1 = 0x14
TEMPORARY_BANK = 8
TEMPORARY_FIRST = 72
PROJECTS = 8

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
# System General "Sampling Source" (p. 122): what REC SAMPLE records.
SAMPLING_SOURCE = (0x00, 0x00, 0x19)
SOURCE_RESAMPLING, SOURCE_MIC_LINE, SOURCE_USB_AUDIO = 0x00, 0x01, 0x02

REALTIME_START, REALTIME_CONTINUE, REALTIME_STOP = 0xFA, 0xFB, 0xFC

# AWM Sound Element "Oscillator / Amplitude / Pitch" block (p. 147), at 41 eA 00.
# Note Limit Low/High give the note range that plays each sampler pad.
OSCILLATOR_HI = 0x41
ELEMENT_ASSIGN = 0x00
WAVE_SELECT = 0x01      # 1 = user wave (fixed on the sampler)
WAVE_NUMBER = 0x03      # 2 bytes: user wave 1-2048, the recording the pad plays
NOTE_LIMIT_LOW = 0x10
NOTE_LIMIT_HIGH = 0x11
COARSE_TUNE = 0x4F      # 40-88 = -24 to +24 semitones; 64 is no change (p. 148)
COARSE_CENTER = 64
BASE_NOTE = 60          # pad n plays note 60 + n on a real SEQTRAK

# Every parameter of the Oscillator/Amplitude/Pitch block (p. 147-148) and of the
# Filter/EQ/LFO block at 42 eA 00 (p. 149-150), as (offset, size); reserved bytes
# are left out. Together they hold all of a pad's sound settings: level, envelope,
# pitch, filter, EQ and LFO.
FILTER_HI = 0x42
OSCILLATOR_SIZE = 0x6D
FILTER_SIZE = 0x47
_TWO_BYTE_OSC = {0x03, 0x27, 0x45, 0x47, 0x49, 0x4B, 0x5C, 0x5E, 0x60, 0x62, 0x64}
_RESERVED_OSC = {0x05, 0x06, 0x07, 0x09, 0x17, 0x1A, *range(0x1B, 0x26), 0x29, 0x2D, 0x32,
                 0x3C, 0x52}
_TWO_BYTE_FILTER = {0x01, 0x07, 0x0A, 0x0C, 0x13, 0x15, 0x17, 0x19, 0x1B, 0x28, 0x2A, 0x2C,
                    0x2E, 0x34, 0x37}
_RESERVED_FILTER = {0x04, 0x09, 0x42}


def _params(size: int, two_byte: set[int], reserved: set[int]) -> list[tuple[int, int]]:
    out, offset = [], 0
    while offset < size:
        width = 2 if offset in two_byte else 1
        if offset not in reserved:
            out.append((offset, width))
        offset += width
    return out


OSCILLATOR_PARAMS = _params(OSCILLATOR_SIZE, _TWO_BYTE_OSC, _RESERVED_OSC)
FILTER_PARAMS = _params(FILTER_SIZE, _TWO_BYTE_FILTER, _RESERVED_FILTER)

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


def parameter_request(address: tuple[int, int, int], device: int = DEVICE) -> list[int]:
    """Ask for one parameter; the SEQTRAK answers with a Parameter Change (p. 115)."""
    return [0xF0, YAMAHA, 0x30 | device, GROUP_HI, GROUP_LO, MODEL, *address, 0xF7]


def parse_parameter_change(msg: list[int]) -> tuple[tuple[int, int, int], list[int]] | None:
    """(address, data) if msg is a Parameter Change, e.g. the answer to a request."""
    if (len(msg) >= 10 and msg[0] == 0xF0 and msg[-1] == 0xF7 and msg[1] == YAMAHA
            and msg[2] & 0xF0 == 0x10 and msg[3:6] == [GROUP_HI, GROUP_LO, MODEL]):
        return (msg[6], msg[7], msg[8]), msg[9:-1]
    return None


def enable_usb_sysex_transmit() -> list[int]:
    return parameter_change(USB_SYSEX_TRANSMIT, [0x01])


def sampling_source(source: int) -> list[int]:
    return parameter_change(SAMPLING_SOURCE, [source])


def pad_request(pad: int) -> list[int]:
    return bulk_request(BULK_HEADER, SAMPLER_ELEMENT_EDIT_BUFFER, pad)


def sampler_sound_request() -> list[int]:
    """Ask for the sampler track's whole sound: every pad's blocks in one dump."""
    return bulk_request(BULK_HEADER, SOUND_EDIT_BUFFER, SAMPLER_PART)


def element_request(bank: int, number: int) -> list[int]:
    """Bulk request for stored sampler element `number` (0-127) of user bank 1-8."""
    if not 1 <= bank <= 8 or not 0 <= number <= 127:
        raise ValueError(f"no sampler element user {bank} #{number}")
    return bulk_request(BULK_HEADER, SAMPLER_ELEMENT_USER_1 + bank - 1, number)


def temporary_slot(project: int, pad: int) -> int:
    """User 8 program that keeps a project's (0-7) pad (0-6) recording."""
    return TEMPORARY_FIRST + project * SAMPLER_PADS + pad


def temporary_slot_owner(number: int) -> tuple[int, int] | None:
    """(project, pad), both 0-based, for a User 8 temporary-area program number."""
    if not TEMPORARY_FIRST <= number < TEMPORARY_FIRST + PROJECTS * SAMPLER_PADS:
        return None
    return divmod(number - TEMPORARY_FIRST, SAMPLER_PADS)


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
    wave: int | None = None       # user wave number (the recording the pad plays)
    assigned: bool | None = None  # Element Assign: is the pad switched on
    osc: list[int] | None = None  # the whole oscillator block (41 eA 00)
    filt: list[int] | None = None  # the whole filter block (42 eA 00)

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

    @property
    def last_frame(self) -> int:
        """The recording's last frame. A real SEQTRAK reports it in Loop Length for a
        one-shot pad (see FORMAT.md); otherwise the end point is the best we know."""
        if not self.loop_on and self.loop_length >= self.end:
            return self.loop_length
        return self.end


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


@dataclass
class PadOscillator:
    pad: int
    note_low: int
    note_high: int
    wave: int
    assigned: bool
    data: list[int] = field(default_factory=list)


def pad_oscillator_from_block(block: BulkBlock) -> PadOscillator | None:
    """The settings we use from a sampler pad's oscillator block (41 eA 00, p. 147)."""
    hi, mid, lo = block.address
    if hi != OSCILLATOR_HI or lo != 0 or mid & 0x0F != SAMPLER_PART:
        return None
    pad = mid >> 4
    d = block.data
    if pad >= SAMPLER_PADS or len(d) <= NOTE_LIMIT_HIGH:
        return None
    return PadOscillator(pad, d[NOTE_LIMIT_LOW], d[NOTE_LIMIT_HIGH],
                         (d[WAVE_NUMBER] << 7) | d[WAVE_NUMBER + 1], bool(d[ELEMENT_ASSIGN]),
                         list(d[:OSCILLATOR_SIZE]))


def pad_filter_from_block(block: BulkBlock) -> tuple[int, list[int]] | None:
    """(pad, data) if this is a sampler pad's Filter/EQ/LFO block (42 eA 00)."""
    hi, mid, lo = block.address
    pad = mid >> 4
    if (hi != FILTER_HI or lo != 0 or mid & 0x0F != SAMPLER_PART or pad >= SAMPLER_PADS
            or len(block.data) < FILTER_SIZE):
        return None
    return pad, list(block.data[:FILTER_SIZE])


def pad_notes_from_block(block: BulkBlock) -> tuple[int, int, int] | None:
    """Return (pad, note_low, note_high) if this is a sampler pad's oscillator block."""
    osc = pad_oscillator_from_block(block)
    return None if osc is None else (osc.pad, osc.note_low, osc.note_high)


def oscillator_address(pad: int, offset: int) -> tuple[int, int, int]:
    return (OSCILLATOR_HI, (pad << 4) | SAMPLER_PART, offset)


def block_messages(hi: int, pad: int, params: list[tuple[int, int]], new: list[int],
                   old: list[int] | None, skip: tuple[int, ...] = ()) -> list[list[int]]:
    """Parameter changes that turn a pad's block `old` into `new`, one per parameter
    that differs (every parameter when `old` isn't known)."""
    msgs = []
    for offset, width in params:
        if offset in skip or offset + width > len(new):
            continue
        value = new[offset:offset + width]
        if old is None or old[offset:offset + width] != value:
            msgs.append(parameter_change((hi, (pad << 4) | SAMPLER_PART, offset), value))
    return msgs


def copy_sound_messages(target: SamplerPad, source: SamplerPad,
                        coarse: int | None = None) -> list[list[int]]:
    """Give pad `target` all of source's sound settings (recording, level, envelope,
    filter, EQ, LFO), keeping its own note, and Coarse Tune `coarse` if given.
    Switches the pad on last."""
    if source.osc is None:
        raise ValueError("the source pad's settings haven't been read yet")
    osc = list(source.osc)
    osc[NOTE_LIMIT_LOW] = osc[NOTE_LIMIT_HIGH] = BASE_NOTE + target.pad
    if coarse is not None:
        coarse_message(target.pad, coarse)          # range check
        osc[COARSE_TUNE] = coarse
    msgs = block_messages(OSCILLATOR_HI, target.pad, OSCILLATOR_PARAMS, osc, target.osc,
                          skip=(ELEMENT_ASSIGN,))
    if source.filt is not None:
        msgs += block_messages(FILTER_HI, target.pad, FILTER_PARAMS, source.filt, target.filt)
    msgs.append(parameter_change(oscillator_address(target.pad, ELEMENT_ASSIGN), [0x01]))
    return msgs


def wave_messages(pad: int, wave: int, note_low: int | None = None,
                  note_high: int | None = None, assigned: bool = True) -> list[list[int]]:
    """Point a pad at user wave `wave` (a recording already on the SEQTRAK) and
    switch it on, playing on its own note."""
    if not 1 <= wave <= 2048:
        raise ValueError(f"user wave number out of range: {wave}")
    note_low = BASE_NOTE + pad if note_low is None else note_low
    note_high = note_low if note_high is None else note_high
    return [
        parameter_change(oscillator_address(pad, WAVE_SELECT), [0x01]),
        parameter_change(oscillator_address(pad, WAVE_NUMBER), [wave >> 7, wave & 0x7F]),
        parameter_change(oscillator_address(pad, NOTE_LIMIT_LOW), [note_low]),
        parameter_change(oscillator_address(pad, NOTE_LIMIT_HIGH), [note_high]),
        parameter_change(oscillator_address(pad, ELEMENT_ASSIGN), [int(assigned)]),
    ]


def slice_messages(target: SamplerPad, source: SamplerPad, start: int, end_exclusive: int,
                   name: str, coarse: int | None = None) -> list[list[int]]:
    """Make pad `target` sound like the source pad, playing source's recording from
    start to end (frames of that recording), named `name`."""
    if source.wave is None or source.osc is None:
        raise ValueError("the source pad's recording isn't known yet")
    last = source.last_frame
    end = end_point_for(end_exclusive, last)
    if not 0 <= start < end:
        raise ValueError("segment is too short for the SEQTRAK")
    msgs = copy_sound_messages(target, source, coarse)
    msgs.append(parameter_change(sample_general_address(target.pad, LOOP_ON), [0x00]))
    msgs.append(parameter_change(sample_general_address(target.pad, LOOP_LENGTH),
                                 encode_point(last)))
    msgs += points_messages(target.pad, start, end)
    msgs += name_messages(target.pad, name)
    return msgs


# Project Common / General (p. 124) and Scale (p. 125).
TEMPO = (0x30, 0x40, 0x76)                 # 2 bytes, 5-300 BPM
SCALE_SELECT = (0x30, 0x40, 0x7E)          # 0-7: Scale 1-8
KEY = (0x30, 0x40, 0x7F)                   # 0x40-0x4B: transpose 0 to +11


def scale_note_address(scale: int, pad: int) -> tuple[int, int, int]:
    """Scale (0-7) note of synth key (pad) 0-6: what that key plays, 36-96."""
    return (0x30, 0x4D, 16 * scale + pad)


def coarse_message(pad: int, value: int) -> list[int]:
    """Set a pad's Coarse Tune (40-88; 64 = as recorded)."""
    if not COARSE_CENTER - 24 <= value <= COARSE_CENTER + 24:
        raise ValueError("a pad can be tuned at most 24 semitones up or down")
    return parameter_change(oscillator_address(pad, COARSE_TUNE), [value])


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
        msgs += name_messages(pad.pad, name)
    return msgs


def name_messages(pad: int, name: str) -> list[list[int]]:
    return [parameter_change(sample_general_address(pad, NAME_OFFSET + i), [b])
            for i, b in enumerate(encode_name(name))]


def restore_messages(current: SamplerPad, saved: SamplerPad) -> list[list[int]]:
    """Put a pad's sound settings, start, end, loop length and name back to a saved
    state."""
    msgs = []
    if saved.osc is not None and current.osc is not None:
        # An empty pad's wave number (0) isn't a valid setting: leave it.
        skip = (ELEMENT_ASSIGN,) if saved.wave else (ELEMENT_ASSIGN, WAVE_NUMBER)
        msgs += block_messages(OSCILLATOR_HI, current.pad, OSCILLATOR_PARAMS, saved.osc,
                               current.osc, skip=skip)
        if saved.filt is not None and current.filt is not None:
            msgs += block_messages(FILTER_HI, current.pad, FILTER_PARAMS, saved.filt,
                                   current.filt)
        if saved.assigned != current.assigned:
            msgs.append(parameter_change(oscillator_address(current.pad, ELEMENT_ASSIGN),
                                         [int(bool(saved.assigned))]))
    elif saved.wave is not None and (saved.wave, saved.assigned, saved.note_low) != \
            (current.wave, current.assigned, current.note_low):
        if saved.wave >= 1:
            msgs += wave_messages(current.pad, saved.wave, saved.note_low, saved.note_high,
                                  bool(saved.assigned))
        else:
            msgs.append(parameter_change(oscillator_address(current.pad, ELEMENT_ASSIGN),
                                         [int(bool(saved.assigned))]))
    if saved.empty:
        msgs += points_messages(current.pad, 0, 0)
    else:
        msgs += points_messages(current.pad, saved.start, saved.end)
    if saved.loop_on != current.loop_on:
        msgs.append(parameter_change(sample_general_address(current.pad, LOOP_ON),
                                     [int(saved.loop_on)]))
    if saved.loop_length != current.loop_length:
        msgs.append(parameter_change(sample_general_address(current.pad, LOOP_LENGTH),
                                     encode_point(saved.loop_length)))
    if saved.name != current.name:
        msgs += name_messages(current.pad, saved.name)
    return msgs
