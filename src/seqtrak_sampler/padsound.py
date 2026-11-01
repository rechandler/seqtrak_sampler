"""A sampler pad's sound settings and loop, as the Edit screen shows and changes them.

Every setting is in the pad's oscillator block (41 eA 00) or filter block (42 eA 00),
and the loop in its Sample General block (50 eA 00), all in the SEQTRAK Data List
(pp. 147-154). The knobs they match on the SEQTRAK itself: Volume, Pan, Pitch,
Attack, and Decay (a pad in Gate mode, as sampler pads are, decays on its release
time). The filter here is the pad's own; the SEQTRAK's Filter knob is track-wide.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass, replace

from . import sysex

MIN_LOOP = sysex.MIN_LOOP_LENGTH       # 80 frames: the shortest loop the SEQTRAK takes


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    block: int          # sysex.OSCILLATOR_HI or sysex.FILTER_HI
    offset: int
    low: int
    high: int
    width: int = 1      # data bytes (cutoff is 0-255 in two)
    center: int | None = None
    tip: str = ""

    def text(self, value: int) -> str:
        if self.key == "pan":
            return "C" if value == 64 else f"L{64 - value}" if value < 64 else f"R{value - 64}"
        if self.key == "coarse":
            return f"{value - 64:+d} st"
        if self.key == "fine":
            return f"{value - 64:+d}"
        if self.key in ("attack", "release"):
            return f"{envelope_seconds(value) * 1000:.0f} ms" if envelope_seconds(value) < 1 \
                else f"{envelope_seconds(value):.1f} s"
        return str(value)


PARAMS = [
    Param("level", "Volume", sysex.OSCILLATOR_HI, 0x2E, 0, 127,
          tip="The pad's level (the SEQTRAK's Volume knob)"),
    Param("pan", "Pan", sysex.OSCILLATOR_HI, 0x0B, 1, 127, center=64),
    Param("coarse", "Pitch", sysex.OSCILLATOR_HI, sysex.COARSE_TUNE, 40, 88, center=64,
          tip="Semitones, ±24 (the SEQTRAK's Pitch knob)"),
    Param("fine", "Fine", sysex.OSCILLATOR_HI, sysex.FINE_TUNE, 0, 127, center=64,
          tip="Fine tuning"),
    Param("attack", "Attack", sysex.OSCILLATOR_HI, 0x33, 0, 127,
          tip="How long the sound takes to fade in (approximate time)"),
    Param("release", "Release", sysex.OSCILLATOR_HI, 0x37, 0, 127,
          tip="How long it fades out after the note ends: the SEQTRAK's Decay knob "
              "(approximate time)"),
    Param("cutoff", "Cutoff", sysex.FILTER_HI, 0x01, 0, 255, width=2,
          tip="This pad's own filter cutoff"),
    Param("resonance", "Resonance", sysex.FILTER_HI, 0x05, 0, 127,
          tip="This pad's own filter resonance"),
]
BY_KEY = {p.key: p for p in PARAMS}


def _block(pad: sysex.SamplerPad, param: Param) -> list[int] | None:
    return pad.osc if param.block == sysex.OSCILLATOR_HI else pad.filt


def value(pad: sysex.SamplerPad, param: Param) -> int | None:
    """The setting as the pad holds it, or None if its block hasn't been read."""
    block = _block(pad, param)
    if block is None or len(block) < param.offset + param.width:
        return None
    if param.width == 2:
        return (block[param.offset] << 7) | block[param.offset + 1]
    return block[param.offset]


def _bytes(param: Param, value: int) -> list[int]:
    value = max(param.low, min(param.high, value))
    return [value >> 7, value & 0x7F] if param.width == 2 else [value]


def message(pad: int, param: Param, value: int) -> list[int]:
    return sysex.parameter_change((param.block, (pad << 4) | sysex.SAMPLER_PART,
                                   param.offset), _bytes(param, value))


def with_value(pad: sysex.SamplerPad, param: Param, value: int) -> sysex.SamplerPad:
    """The pad as it will be once the change is in (the blocks are copied)."""
    block = _block(pad, param)
    if block is None:
        return pad
    block = list(block)
    block[param.offset:param.offset + param.width] = _bytes(param, value)
    field = "osc" if param.block == sysex.OSCILLATOR_HI else "filt"
    return replace(pad, **{field: block})


def envelope_seconds(value: int) -> float:
    """Roughly how long an envelope time setting (0-127) lasts. The Data List gives no
    times, so this is a smooth guess from instant to about 10 s, for drawing only."""
    return 0.0 if value <= 0 else 0.002 * 5000 ** (value / 127)


# Loops. A loop plays from (end - loop length + 1) to the end point, over and over
# while the pad is held. One-shot pads report the recording's last frame as their
# loop length (see FORMAT.md), which is kept when the loop is switched off again.

def loop_start(pad: sysex.SamplerPad) -> int | None:
    """Where the pad's loop starts (a frame of its recording), or None if it's off."""
    if not pad.loop_on or pad.empty:
        return None
    return max(pad.start, pad.end - pad.loop_length + 1)


def loop_length_for(pad: sysex.SamplerPad, start: int) -> int:
    """The loop length that starts the loop at `start`, kept within the SEQTRAK's range."""
    return max(MIN_LOOP, pad.end - start + 1)


def loop_messages(pad: int, on: bool, length: int) -> list[list[int]]:
    return [
        sysex.parameter_change(sysex.sample_general_address(pad, sysex.LOOP_ON), [int(on)]),
        sysex.parameter_change(sysex.sample_general_address(pad, sysex.LOOP_LENGTH),
                               sysex.encode_point(length)),
    ]


def matched_start(left: array, right: array, wanted: int, end: int,
                  window: int) -> int:
    """The frame near `wanted` (within `window`) that best continues from the frame
    after `end`: same level and heading the same way, on both channels. Jumping there
    from the end of the loop is then smooth, so the loop doesn't click."""
    last = min(end, len(left) - 1)
    if last < 1:
        return wanted
    target = [(ch[last], ch[last] - ch[last - 1]) for ch in (left, right)]
    best, best_cost = wanted, None
    lo = max(1, wanted - window)
    hi = min(last - MIN_LOOP, wanted + window)
    for f in range(lo, hi + 1):
        cost = 0
        for (level, slope), ch in zip(target, (left, right)):
            # The frame after the end should look like the loop's first frame.
            cost += abs(ch[f] - (level + slope)) + 2 * abs((ch[f] - ch[f - 1]) - slope)
        cost += abs(f - wanted) // 4            # prefer staying close to where asked
        if best_cost is None or cost < best_cost:
            best, best_cost = f, cost
    return best
