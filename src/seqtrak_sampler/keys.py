"""Keys mode: one sample across the 7 pads, each tuned to a note of a scale."""

from __future__ import annotations

from array import array

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
PADS = 7
MAX_SHIFT = 24          # Coarse Tune reaches -24 to +24 semitones (Data List p. 148)

# Intervals from the root, in semitones. Scales with fewer than 7 notes carry on
# into the next octave.
SCALES = {
    "Major": [0, 2, 4, 5, 7, 9, 11],
    "Natural minor": [0, 2, 3, 5, 7, 8, 10],
    "Harmonic minor": [0, 2, 3, 5, 7, 8, 11],
    "Dorian": [0, 2, 3, 5, 7, 9, 10],
    "Mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "Major pentatonic": [0, 2, 4, 7, 9],
    "Minor pentatonic": [0, 3, 5, 7, 10],
    "Chromatic": list(range(12)),
}


def note_name(note: int) -> str:
    """Yamaha's naming, as in the SEQTRAK Data List: 36 is C1, so 60 is C3."""
    return f"{NOTE_NAMES[note % 12]}{note // 12 - 2}"


def scale_notes(root: int, intervals: list[int], count: int = PADS) -> list[int]:
    """`count` notes of the scale going up from `root`."""
    return [root + 12 * (i // len(intervals)) + intervals[i % len(intervals)]
            for i in range(count)]


def shifts(notes: list[int], sample_note: int) -> list[int]:
    """Semitones to tune each pad by, for a sample that sounds `sample_note`."""
    return [note - sample_note for note in notes]


def repitch(samples: array, semitones: int) -> array:
    """The sound played `semitones` higher (or lower), the way a sampler does it:
    faster and shorter (or slower and longer). Linear interpolation; for previews."""
    ratio = 2 ** (semitones / 12)
    n = int(len(samples) / ratio)
    out = array("h", bytes(2 * n))
    last = len(samples) - 1
    for i in range(n):
        pos = i * ratio
        j = int(pos)
        if j >= last:
            out[i] = samples[last]
            continue
        frac = pos - j
        out[i] = int(samples[j] + (samples[j + 1] - samples[j]) * frac)
    return out
