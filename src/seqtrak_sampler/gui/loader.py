"""Loads slices of a sample onto SEQTRAK sampler pads, one pad at a time.

There is no MIDI command to start sampling, so for each pad the user presses the pad
and REC SAMPLE on the SEQTRAK. The app plays the whole sample into it over USB audio,
then plays the pad back once to find where the sample landed, and trims the pad to
its slice.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .. import slicing, sysex

LEAD_SECONDS = 0.3   # silence played before the sample, so its start isn't clipped
TAIL_SECONDS = 0.3
PAD_READ_MS = 800    # time for the SEQTRAK to report the pad after sampling


@dataclass
class Job:
    pad: int                 # 0-6
    start: int               # slice [start, end) in the rendered sample's frames
    end: int
    name: str


class SliceLoader(QDialog):
    def __init__(self, window, left: array, right: array, rate: int, jobs: list[Job]):
        super().__init__(window)
        self.window = window
        self.left, self.right, self.rate = left, right, rate
        self.jobs = jobs
        self.index = 0
        self.loaded: list[Job] = []
        self.source_onset = slicing.onset(left, right, window.ONSET_LEVEL) or 0

        self.setWindowTitle("Load slices to pads")
        self.setMinimumWidth(460)
        self.progress = QLabel()
        self.progress.setObjectName("sectionTitle")
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setTextFormat(Qt.RichText)
        self.action = QPushButton()
        self.action.setObjectName("primary")
        self.action.clicked.connect(self._on_action)
        self.cancel = QPushButton("Cancel")
        self.cancel.setObjectName("ghost")
        self.cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.cancel)
        buttons.addWidget(self.action)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        layout.addWidget(self.progress)
        layout.addWidget(self.text)
        layout.addLayout(buttons)
        self._next_step = None
        self._playing = False

        # REC SAMPLE should record what the app plays over USB.
        window.device.send(sysex.sampling_source(sysex.SOURCE_USB_AUDIO))
        self._arm()

    # Steps

    def _step(self, text: str, action: str | None, next_step) -> None:
        job = self.jobs[self.index] if self.index < len(self.jobs) else None
        if job is not None:
            self.progress.setText(f"SLICE {self.index + 1} OF {len(self.jobs)}  →  "
                                  f"PAD {job.pad + 1}")
        self.text.setText(text)
        self.action.setVisible(action is not None)
        self.action.setEnabled(action is not None)
        self.action.setText(action or "")
        self._next_step = next_step

    def _on_action(self) -> None:
        if self._next_step is not None:
            self._next_step()

    def _arm(self) -> None:
        pad = self.jobs[self.index].pad + 1
        self._step(f"On the SEQTRAK, select the <b>SAMPLER</b> track, press <b>pad {pad}</b>, "
                   f"then press <b>REC SAMPLE</b>.<br><br>Then click <b>Play into SEQTRAK</b>.",
                   "Play into SEQTRAK", self._play)

    def _play(self) -> None:
        lead = array("h", bytes(2 * int(LEAD_SECONDS * self.rate)))
        tail = array("h", bytes(2 * int(TAIL_SECONDS * self.rate)))
        player = self.window.player
        try:
            player.play(lead + self.left + tail, lead + self.right + tail, self.rate, 0,
                        to_seqtrak=True)
        except RuntimeError as e:
            self._step(f"Couldn't play into the SEQTRAK: {e}", "Try again", self._play)
            return
        player.stopped.connect(self._played)
        self._playing = True
        seconds = len(self.left) / self.rate
        self._step(f"Playing the sample into the SEQTRAK ({seconds:.1f} s)…", None, None)

    def _played(self) -> None:
        self.window.player.stopped.disconnect(self._played)
        self._playing = False
        self._step("Press <b>REC SAMPLE</b> on the SEQTRAK to stop sampling, then click "
                   "<b>Next</b>.", "Next", self._read_pad)

    def _read_pad(self) -> None:
        self._step("Reading the pad from the SEQTRAK…", None, None)
        self.window.device.request_pads()
        QTimer.singleShot(PAD_READ_MS, self._capture)

    def _capture(self) -> None:
        job = self.jobs[self.index]
        pad = self.window._pads.get(job.pad)
        if pad is None or pad.empty:
            self._step(f"Pad {job.pad + 1} looks empty, so the sampling may not have run. "
                       f"Check the SEQTRAK and try again.", "Try again", self._arm)
            return
        self._step(f"Finding where the sample landed on pad {job.pad + 1} (the SEQTRAK plays "
                   f"it once)…", None, None)
        if not self.window.open_pad(pad, on_done=self._captured):
            self._step("Couldn't play the pad back. Is the SEQTRAK's USB audio connected?",
                       "Try again", self._capture)

    def _captured(self, pad: sysex.SamplerPad, left, right, rate: int) -> None:
        job = self.jobs[self.index]
        onset = None if left is None else slicing.onset(left, right, self.window.ONSET_LEVEL)
        if onset is None:
            self._step(f"Pad {job.pad + 1} played silence. Sample it again?", "Try again",
                       self._arm)
            return
        start, end = slicing.device_range(pad.start, onset, self.source_onset,
                                          job.start, job.end, pad.last_frame)
        try:
            msgs = sysex.trim_messages(pad, start, end, pad.last_frame, job.name)
        except ValueError as e:
            self._step(f"Couldn't trim pad {job.pad + 1}: {e}", "Try again", self._arm)
            return
        self.window._push_history(pad, job.name)
        self.window.device.send_trim(msgs, pad.pad)
        self.loaded.append(job)
        self.index += 1
        if self.index < len(self.jobs):
            self._arm()
        else:
            self._finish()

    def _finish(self) -> None:
        self._restore_source()
        pads = ", ".join(str(j.pad + 1) for j in self.loaded)
        self.progress.setText("DONE")
        self._step(f"Loaded {len(self.loaded)} slice{'s' if len(self.loaded) != 1 else ''} "
                   f"onto pad{'s' if len(self.loaded) != 1 else ''} {pads}. Click a pad in "
                   f"the list to fine-tune it; Undo puts a pad back.", "Done", self.accept)
        self.cancel.hide()

    def _restore_source(self) -> None:
        # Back to the SEQTRAK's default sampling source (built-in mic / AUDIO IN).
        self.window.device.send(sysex.sampling_source(sysex.SOURCE_MIC_LINE))

    def reject(self) -> None:
        if self._playing:
            self.window.player.stopped.disconnect(self._played)
            self._playing = False
        self.window.player.stop()
        self._restore_source()
        super().reject()
