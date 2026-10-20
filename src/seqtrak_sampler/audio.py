"""Stereo 16-bit audio helpers: trimming, length limits, normalizing, WAV output."""

from __future__ import annotations

import math
import sys
import wave
from array import array

# The SEQTRAK caps a sample at 16 seconds.
MAX_SECONDS = 16.0
FULL_SCALE = 32767


class TooLongError(ValueError):
    pass


def to_stereo(channels: list[array]) -> tuple[array, array]:
    """Return (left, right). A mono sound is duplicated to both sides."""
    if len(channels) == 1:
        return channels[0], array("h", channels[0])
    if len(channels) == 2:
        return channels[0], channels[1]
    raise ValueError(
        f"sound has {len(channels)} keybanks; only mono or stereo sounds are supported"
    )


def silence_bounds(left: array, right: array, threshold: int, pad: int) -> tuple[int, int]:
    """Return [start, end) covering everything louder than threshold, plus pad frames."""
    n = len(left)
    start = 0
    while start < n and abs(left[start]) <= threshold and abs(right[start]) <= threshold:
        start += 1
    if start == n:
        return 0, 0
    end = n
    while abs(left[end - 1]) <= threshold and abs(right[end - 1]) <= threshold:
        end -= 1
    return max(0, start - pad), min(n, end + pad)


def dbfs_to_amplitude(db: float) -> int:
    return int(round(FULL_SCALE * 10 ** (db / 20)))


def normalize(left: array, right: array, target_db: float) -> tuple[array, array]:
    peak = max(max(map(abs, left), default=0), max(map(abs, right), default=0))
    if peak == 0:
        return left, right
    gain = dbfs_to_amplitude(target_db) / peak

    def scale(ch: array) -> array:
        return array("h", (max(-32768, min(FULL_SCALE, round(s * gain))) for s in ch))

    return scale(left), scale(right)


def fade_out(left: array, right: array, frames: int) -> None:
    """Apply a linear fade over the last `frames` frames, in place."""
    n = len(left)
    frames = min(frames, n)
    for i in range(frames):
        g = (frames - 1 - i) / frames
        left[n - frames + i] = int(left[n - frames + i] * g)
        right[n - frames + i] = int(right[n - frames + i] * g)


def enforce_max_length(
    left: array, right: array, sample_rate: int, truncate: bool, fade_ms: float = 10.0
) -> tuple[array, array]:
    limit = int(MAX_SECONDS * sample_rate)
    if len(left) <= limit:
        return left, right
    if not truncate:
        raise TooLongError(
            f"sample is {len(left) / sample_rate:.2f} s; the SEQTRAK limit is "
            f"{MAX_SECONDS:g} s (use --truncate to cut it)"
        )
    left, right = left[:limit], right[:limit]
    fade_out(left, right, math.ceil(sample_rate * fade_ms / 1000))
    return left, right


def write_wav(path, left: array, right: array, sample_rate: int) -> None:
    interleaved = array("h", bytes(4 * len(left)))
    interleaved[0::2] = left
    interleaved[1::2] = right
    if sys.byteorder == "big":
        interleaved.byteswap()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(interleaved.tobytes())
