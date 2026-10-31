"""Cutting a sound into slices: equal splits, hit detection, segment lists."""

from __future__ import annotations

from array import array

MAX_SLICES = 7          # the SEQTRAK sampler has 7 pads
WINDOW_MS = 10
MIN_GAP_MS = 80         # hits closer than this count as one
HIT_FLOOR = 0.02        # ignore "hits" quieter than this fraction of full scale
HIT_RISE = 2.5          # a hit is this much louder than the moment before it


def equal_markers(start: int, end: int, count: int) -> list[int]:
    """Markers that split [start, end) into `count` equal slices."""
    count = max(1, min(count, MAX_SLICES))
    return [start + (end - start) * i // count for i in range(1, count)]


def slices(start: int, end: int, markers: list[int]) -> list[tuple[int, int]]:
    """The [a, b) segments between start, the markers inside (start, end), and end."""
    cuts = sorted({m for m in markers if start < m < end})
    edges = [start, *cuts, end]
    return [(a, b) for a, b in zip(edges, edges[1:]) if b > a]


def _envelope(left: array, right: array, start: int, end: int, window: int) -> list[int]:
    env = []
    for a in range(start, end, window):
        b = min(end, a + window)
        env.append(max(max(map(abs, left[a:b])), max(map(abs, right[a:b]))))
    return env


def hit_markers(left: array, right: array, rate: int, start: int, end: int,
                max_slices: int = MAX_SLICES) -> list[int]:
    """Markers at the strongest hits (sudden rises in level) in [start, end).

    At most max_slices - 1 markers, so there are at most max_slices slices.
    """
    window = max(1, rate * WINDOW_MS // 1000)
    env = _envelope(left, right, start, end, window)
    floor = HIT_FLOOR * 32768
    # The first sound belongs to slice 1, whatever silence comes before it.
    first = next((i for i, level in enumerate(env) if level > floor), len(env))
    candidates = []
    for i in range(first + 1, len(env)):
        before = max(env[max(0, i - 3):i])
        if env[i] > floor and env[i] > HIT_RISE * max(before, 1):
            candidates.append((env[i] / max(before, 1), start + i * window))
    gap = rate * MIN_GAP_MS // 1000
    chosen: list[int] = []
    first_frame = start + first * window
    for _, frame in sorted(candidates, reverse=True):
        if frame - first_frame < gap or end - frame < gap:
            continue
        if all(abs(frame - c) >= gap for c in chosen):
            chosen.append(frame)
        if len(chosen) == max_slices - 1:
            break
    return sorted(chosen)


def onset(left: array, right: array, threshold: int, start: int = 0) -> int | None:
    """The first frame from `start` louder than threshold, or None."""
    for i in range(start, len(left)):
        if abs(left[i]) > threshold or abs(right[i]) > threshold:
            return i
    return None


def device_range(pad_start: int, captured_onset: int, source_onset: int,
                 a: int, b: int, last_frame: int) -> tuple[int, int]:
    """Where source frames [a, b) sit in a pad that sampled the whole source.

    captured_onset is where the source's first sound appears in a recording of the pad
    (index 0 = the pad's start point); source_onset is where it is in the source.
    Returns the pad's (start, end_exclusive) for the slice, clamped to the recording.
    """
    offset = pad_start + captured_onset - source_onset   # pad frame of source frame 0
    start = max(0, offset + a)
    end = min(last_frame + 1, offset + b)
    return start, end

