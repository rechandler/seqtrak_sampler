"""Records the SEQTRAK's USB audio output."""

from __future__ import annotations

from array import array
from collections import deque

from PySide6.QtCore import QObject, Signal
from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices

DEVICE_NAME = "SEQTRAK"
RATE = 44100
PREROLL_SECONDS = 0.3   # kept while armed, so a take doesn't miss its first hit
MAX_SECONDS = 600


def find_seqtrak_input():
    for dev in QMediaDevices.audioInputs():
        if DEVICE_NAME in dev.description().upper():
            return dev
    return None


def _deinterleave(data: bytes, fmt: QAudioFormat) -> tuple[array, array]:
    channels = fmt.channelCount()
    if fmt.sampleFormat() == QAudioFormat.Float:
        floats = array("f")
        floats.frombytes(data[: len(data) - len(data) % 4])
        frames = array("h", (max(-32768, min(32767, int(v * 32767))) for v in floats))
    else:
        frames = array("h")
        frames.frombytes(data[: len(data) - len(data) % 2])
    if channels == 1:
        return frames, array("h", frames)
    usable = len(frames) - len(frames) % channels
    return frames[0:usable:channels], frames[1:usable:channels]


class Recorder(QObject):
    stateChanged = Signal(str)        # "idle", "armed", "recording"
    level = Signal(float)             # 0-1 peak of the latest audio
    elapsed = Signal(float)           # seconds recorded in the current take
    takeReady = Signal(object, object, int)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source: QAudioSource | None = None
        self._io = None
        self._fmt: QAudioFormat | None = None
        self._armed = False
        self._recording = False
        self._preroll: deque[tuple[array, array]] = deque()
        self._preroll_frames = 0
        self._left = array("h")
        self._right = array("h")

    @property
    def state(self) -> str:
        if self._recording:
            return "recording"
        return "armed" if self._source is not None else "idle"

    def _open(self) -> bool:
        if self._source is not None:
            return True
        device = find_seqtrak_input()
        if device is None:
            self.error.emit("SEQTRAK audio input not found. Is it connected over USB?")
            return False
        fmt = QAudioFormat()
        fmt.setSampleRate(RATE)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.Int16)
        if not device.isFormatSupported(fmt):
            fmt = device.preferredFormat()
            if fmt.sampleRate() != RATE or fmt.sampleFormat() not in (
                    QAudioFormat.Int16, QAudioFormat.Float):
                self.error.emit(f"The SEQTRAK audio input runs at {fmt.sampleRate()} Hz; "
                                f"set it to 44.1 kHz in your sound settings.")
                return False
        self._fmt = fmt
        self._source = QAudioSource(device, fmt, self)
        self._io = self._source.start()
        if self._io is None:
            self.error.emit("Couldn't open the SEQTRAK audio input.")
            self._source = None
            return False
        self._io.readyRead.connect(self._read)
        self.stateChanged.emit(self.state)
        return True

    def _close(self) -> None:
        if self._source is None:
            return
        self._source.stop()
        self._source.deleteLater()
        self._source = self._io = None
        self._preroll.clear()
        self._preroll_frames = 0
        self.level.emit(0.0)
        self.stateChanged.emit(self.state)

    def set_armed(self, armed: bool) -> None:
        """Keep the input open (with pre-roll) waiting for start_take()."""
        self._armed = armed
        if armed:
            self._open()
        elif not self._recording:
            self._close()

    def frames_recorded(self) -> int:
        """Frames in the current take so far, after reading any audio waiting."""
        self._read()
        return len(self._left)

    def start_take(self) -> None:
        if self._recording or not self._open():
            return
        self._left, self._right = array("h"), array("h")
        for left, right in self._preroll:
            self._left.extend(left)
            self._right.extend(right)
        self._preroll.clear()
        self._preroll_frames = 0
        self._recording = True
        self.stateChanged.emit(self.state)

    def stop_take(self) -> None:
        if not self._recording:
            return
        self._recording = False
        left, right = self._left, self._right
        self._left, self._right = array("h"), array("h")
        if not self._armed:
            self._close()
        self.stateChanged.emit(self.state)
        if len(left):
            self.takeReady.emit(left, right, RATE)

    def _read(self) -> None:
        if self._io is None:
            return
        data = bytes(self._io.readAll())
        if not data:
            return
        left, right = _deinterleave(data, self._fmt)
        if len(left):
            peak = max(max(left), -min(left), max(right), -min(right))
            self.level.emit(min(1.0, peak / 32768))
        if self._recording:
            self._left.extend(left)
            self._right.extend(right)
            self.elapsed.emit(len(self._left) / RATE)
            if len(self._left) >= MAX_SECONDS * RATE:
                self.stop_take()
        else:
            self._preroll.append((left, right))
            self._preroll_frames += len(left)
            while self._preroll and self._preroll_frames - len(self._preroll[0][0]) >= \
                    PREROLL_SECONDS * RATE:
                self._preroll_frames -= len(self._preroll.popleft()[0])
