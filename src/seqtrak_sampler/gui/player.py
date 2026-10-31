"""Plays a region of a stereo sound through the chosen (or default) output device."""

from __future__ import annotations

from array import array

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QTimer, Signal
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices

from .. import audio


class Player(QObject):
    position = Signal(object)  # current frame, or None when stopped
    stopped = Signal()
    failed = Signal(str)       # the output device reported an error

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sink: QAudioSink | None = None
        self._buffer: QBuffer | None = None
        self._offset = 0
        self._rate = 44100
        self.device_name = ""      # the output the last sound went to
        self.output_id: bytes | None = None   # chosen output; None = system default
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)

    @property
    def playing(self) -> bool:
        return self._sink is not None

    def play(self, left: array, right: array, rate: int, offset: int) -> None:
        """Play the given frames; `offset` is their position in the full sound."""
        self.stop()
        device = self.output()
        if device.isNull():
            raise RuntimeError("no audio output device found")
        self.device_name = device.description()
        fmt = QAudioFormat()
        fmt.setSampleRate(rate)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.Int16)
        self._buffer = QBuffer(self)
        self._buffer.setData(QByteArray(audio.to_pcm_bytes(left, right)))
        self._buffer.open(QIODevice.ReadOnly)
        self._sink = QAudioSink(device, fmt, self)
        self._sink.stateChanged.connect(self._on_state)
        self._offset, self._rate = offset, rate
        self._sink.start(self._buffer)
        self._timer.start()

    def output(self):
        """The chosen output if it's still there, else the system default."""
        if self.output_id is not None:
            for device in QMediaDevices.audioOutputs():
                if bytes(device.id()) == self.output_id:
                    return device
        return QMediaDevices.defaultAudioOutput()

    def stop(self) -> None:
        if self._sink is None:
            return
        sink, self._sink = self._sink, None
        sink.stateChanged.disconnect(self._on_state)
        sink.stop()
        sink.deleteLater()
        self._buffer.close()
        self._buffer.deleteLater()
        self._buffer = None
        self._timer.stop()
        self.position.emit(None)
        self.stopped.emit()

    def _tick(self) -> None:
        if self._sink is not None:
            played = self._sink.processedUSecs() * self._rate // 1_000_000
            self.position.emit(self._offset + played)

    def _on_state(self, state) -> None:
        if state == QAudio.IdleState:
            self.stop()
        elif state == QAudio.StoppedState and self._sink is not None:
            error = self._sink.error()
            if error not in (QAudio.NoError, QAudio.UnderrunError):
                name = getattr(error, "name", str(error))
                self.stop()
                self.failed.emit(f"Couldn't play through {self.device_name or 'the output'} "
                                 f"({name}).")

