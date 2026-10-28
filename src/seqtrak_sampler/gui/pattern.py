"""Writing a slice pattern into the SEQTRAK's sampler track, and putting it back.

Everything goes through the selected pattern of the sampler track's piano roll.
Before anything is written, the pattern's events are read back in full, so Undo
can restore them exactly. Addresses and their hardware status are in sysex.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

from .. import sysex

MAX_EVENTS = 2048       # the piano roll's index is 11 bits


@dataclass
class PatternInfo:
    """The sampler track's selected pattern, as read before a write."""
    tempo: int
    pattern: int                  # 0-5
    steps: int                    # length, 1-128
    events: list[list[int]] = field(default_factory=list)   # 8 bytes each


class PatternWriter(QObject):
    progress = Signal(str)

    def __init__(self, device, part: int = sysex.SAMPLER_PART,
                 block: int = sysex.SAMPLER_ROLL, parent=None):
        super().__init__(parent)
        self.device = device
        self.part = part
        self.block = block

    # Reading

    def read(self, done) -> None:
        """done(info, error): the selected pattern, with every event in it."""
        def status(data):
            if data is None:
                return done(None, "The SEQTRAK didn't say whether it's playing.")
            if data[:1] != [0]:
                return done(None, "Stop the SEQTRAK first, then try again.")
            self.device.request(sysex.TEMPO, tempo)

        def tempo(data):
            if not data or len(data) < 2:
                return done(None, "Couldn't read the tempo.")
            value = (data[0] << 7) | data[1]
            self.device.request(sysex.pattern_select_address(self.part),
                                lambda d: pattern(value, d))

        def pattern(tempo_value, data):
            if not data or not 0 <= data[0] <= 5:
                return done(None, "Couldn't read which sampler pattern is selected.")
            self.device.request(sysex.pattern_step_address(self.part, data[0]),
                                lambda d: steps(tempo_value, data[0], d))

        def steps(tempo_value, number, data):
            if not data or len(data) < 2:
                return done(None, "Couldn't read the pattern's length.")
            info = PatternInfo(tempo_value, number, (data[0] << 7) | data[1])
            self._read_events(info, 0, done)

        self.progress.emit("Reading the sampler pattern…")
        self.device.request(sysex.SEQUENCER_STATUS, status)

    def _read_events(self, info: PatternInfo, index: int, done) -> None:
        def got(data):
            if data is None:
                return done(None, f"The SEQTRAK stopped answering while reading note "
                                  f"{index + 1} of the pattern.")
            if len(data) < 8 or index + 1 >= MAX_EVENTS:
                return done(info, None)
            info.events.append(list(data[:8]))
            if len(info.events) % 8 == 0:
                self.progress.emit(f"Reading the sampler pattern… {len(info.events)} notes")
            self._read_events(info, index + 1, done)

        self.device.request(sysex.roll_address(0x75, self.block, index), got)

    # Writing

    def write(self, info: PatternInfo, notes: list[tuple[int, int, int, int]],
              done) -> None:
        """Replace the selected pattern with `notes` (step, note, velocity, gate),
        lengthening the pattern if they need it. done(ok, message)."""
        needed = max((step for step, *_ in notes), default=0) + 1
        steps = info.steps
        if needed > steps:
            steps = min(sysex.MAX_PATTERN_STEPS, math.ceil(needed / 16) * 16)
        events = [sysex.roll_note(step * sysex.TICKS_PER_STEP + sysex.TICKS_PER_STEP // 2,
                                  note, velocity, gate)
                  for step, note, velocity, gate in notes if step < steps]
        self._replace(info.pattern, steps, events, done,
                      f"Pattern {info.pattern + 1} written: {len(events)} notes")

    def restore(self, info: PatternInfo, done) -> None:
        """Put the pattern back the way `read` found it."""
        self._replace(info.pattern, info.steps, info.events, done,
                      f"Pattern {info.pattern + 1} is back the way it was")

    def _replace(self, pattern: int, steps: int, events: list[list[int]], done,
                 success: str) -> None:
        def status(data):
            if data is None or data[:1] != [0]:
                return done(False, "Stop the SEQTRAK first, then try again.")
            self.device.request(sysex.pattern_select_address(self.part), selected)

        def selected(data):
            if not data or data[0] != pattern:
                return done(False, "The sampler track's selected pattern changed. Read it "
                                   "again before writing.")
            self.progress.emit(f"Writing {len(events)} notes…")
            self.device.send(sysex.delete_pattern(self.part, pattern))
            self.device.send(sysex.parameter_change(
                sysex.pattern_step_address(self.part, pattern), [steps >> 7, steps & 0x7F]))
            for event in events:
                self.device.send(sysex.roll_add(self.block, event))
            # Check the count: the last event is there and nothing follows it.
            if events:
                self.device.request(sysex.roll_address(0x75, self.block, len(events) - 1),
                                    last)
            else:
                last([0] * 8)

        def last(data):
            if data is None or len(data) < 8:
                return done(False, f"Wrote {len(events)} notes, but the SEQTRAK doesn't "
                                   f"report them all. Copy the MIDI log and send it.")
            self.device.request(sysex.roll_address(0x75, self.block, len(events)), after)

        def after(data):
            if data is None or len(data) >= 8:
                return done(False, f"Wrote {len(events)} notes, but the pattern holds "
                                   f"more than that. Copy the MIDI log and send it.")
            done(True, success)

        self.device.request(sysex.SEQUENCER_STATUS, status)
