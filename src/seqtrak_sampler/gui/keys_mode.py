"""Keys mode: one pad's sample on all 7 pads, each tuned to a note of a scale, so
the sampler pads play like keys.

Every pad gets the source pad's sound settings and trim, and its own Coarse Tune.
"Project scale" uses the notes the SEQTRAK's own synth keys play: the project's
selected scale and key, read from the device (Data List pp. 124-125).
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QMessageBox, QVBoxLayout

from .. import keys, sysex
from . import device as dev
from .pad_panel import PadPanel

PROJECT_SCALE = "Project scale"
NOTE_RANGE = range(36, 97)          # C1-C6, the range of the SEQTRAK's scale notes
DEFAULT_NOTE = 60                   # C3


def _note_box(default: int) -> QComboBox:
    box = QComboBox()
    for note in NOTE_RANGE:
        box.addItem(keys.note_name(note), note)
    box.setCurrentIndex(box.findData(default))
    return box


class KeysPanel(PadPanel):
    def __init__(self, window):
        super().__init__(window)
        self._project_notes: list[int] | None = None
        self._reading_scale = False

        title = QLabel("Pads as keys")
        title.setObjectName("panelTitle")
        source_label = QLabel("Source")
        source_label.setObjectName("muted")
        top = QHBoxLayout()
        top.addWidget(title)
        top.addStretch()
        top.addWidget(source_label)
        top.addWidget(self.source_box)

        self.wave.regionChanged.connect(lambda _a, _b: self._refresh())

        sample_label = QLabel("Sample plays")
        sample_label.setObjectName("muted")
        self.sample_box = _note_box(DEFAULT_NOTE)
        self.sample_box.setToolTip("The note the recording sounds as. The pads are tuned "
                                   "relative to it.")
        notes_label = QLabel("Notes")
        notes_label.setObjectName("muted")
        self.scale_box = QComboBox()
        self.scale_box.addItem(PROJECT_SCALE)
        for name in keys.SCALES:
            self.scale_box.addItem(name)
        self.scale_box.setToolTip("Project scale: the notes the SEQTRAK's synth keys play "
                                  "in this project")
        self.root_label = QLabel("Root")
        self.root_label.setObjectName("muted")
        self.root_box = _note_box(DEFAULT_NOTE)
        self.scale_info = QLabel()
        self.scale_info.setObjectName("muted")
        for box in (self.sample_box, self.root_box):
            box.activated.connect(lambda _i: self._refresh())
        self.scale_box.activated.connect(self._scale_chosen)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(sample_label)
        row.addWidget(self.sample_box)
        row.addSpacing(16)
        row.addWidget(notes_label)
        row.addWidget(self.scale_box)
        row.addSpacing(8)
        row.addWidget(self.root_label)
        row.addWidget(self.root_box)
        row.addWidget(self.scale_info)
        row.addStretch(1)
        hint = QLabel("Handles set what every pad plays · click a pad to hear it")
        hint.setObjectName("muted")
        row.addWidget(hint)

        self.pad_map.clicked.connect(self._play_pad)

        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.undo_btn)
        bottom.addWidget(self.load_btn)

        col = QVBoxLayout(self)
        col.setContentsMargins(20, 16, 20, 18)
        col.setSpacing(12)
        col.addLayout(top)
        col.addWidget(self.wave, 1)
        col.addLayout(row)
        col.addWidget(self.pad_map)
        col.addLayout(bottom)
        self._scale_chosen()

    # Notes

    def using_project_scale(self) -> bool:
        return self.scale_box.currentText() == PROJECT_SCALE

    def notes(self) -> list[int] | None:
        """The 7 notes the pads will play, or None while the project scale is unknown."""
        if self.using_project_scale():
            return self._project_notes
        return keys.scale_notes(self.root_box.currentData(),
                                keys.SCALES[self.scale_box.currentText()])

    def shifts(self) -> list[int] | None:
        notes = self.notes()
        return None if notes is None else keys.shifts(notes, self.sample_box.currentData())

    def _scale_chosen(self, _index: int = 0) -> None:
        project = self.using_project_scale()
        self.root_label.setVisible(not project)
        self.root_box.setVisible(not project)
        self.scale_info.setVisible(project)
        if project and self._project_notes is None:
            self.read_project_scale()
        self._refresh()

    def read_project_scale(self) -> None:
        """Read the project's selected scale and key: the notes of synth keys 1-7."""
        device = self.window.device
        if device.state != dev.CONNECTED or self._reading_scale:
            return
        self._reading_scale = True
        self.scale_info.setText("reading…")

        def failed():
            self._reading_scale = False
            self.scale_info.setText("couldn't read it")
            self.status.setText("Couldn't read the project's scale. Pick a scale from "
                                "Notes instead.")
            self._refresh()

        def scale(data):
            if not data or not 0 <= data[0] <= 7:
                return failed()
            device.request(sysex.KEY, lambda d: key(data[0], d))

        def key(number, data):
            if not data or not 0x40 <= data[0] <= 0x4B:
                return failed()
            read_note(number, data[0] - 0x40, [])

        def read_note(number, transpose, notes):
            if len(notes) == keys.PADS:
                self._reading_scale = False
                self._project_notes = [n + transpose for n in notes]
                self.scale_info.setText(f"scale {number + 1}"
                                        + (f", key +{transpose}" if transpose else ""))
                self._refresh()
                return

            def got(data):
                if not data:
                    return failed()
                read_note(number, transpose, notes + [data[0]])
            device.request(sysex.scale_note_address(number, len(notes)), got)

        device.request(sysex.SCALE_SELECT, scale)

    def activate(self) -> None:
        # Read it each time: the scale or key may have changed on the SEQTRAK.
        if self.using_project_scale():
            self.read_project_scale()
        super().activate()

    # Pad map and loading

    def _refresh(self) -> None:
        notes = self.notes()
        shifts = self.shifts()
        has_source = self.source is not None
        tiles = []
        for i in range(keys.PADS):
            if notes is None or not has_source:
                name = keys.note_name(notes[i]) if notes else ""
                tiles.append((name, "", "unused"))
                continue
            shift = shifts[i]
            status, kind = self.pad_status(i, "source")
            if abs(shift) > keys.MAX_SHIFT:
                status, kind = f"{shift:+d} st: more than 24", "replace"
            else:
                status = f"{shift:+d} st · {status}"
            tiles.append((keys.note_name(notes[i]), status, kind))
        self.pad_map.set_tiles(tiles)

        in_range = shifts is not None and all(abs(s) <= keys.MAX_SHIFT for s in shifts)
        connected = self.window.device.state == dev.CONNECTED
        self.load_btn.setText(f"Load to pads 1–{keys.PADS}")
        self.load_btn.setEnabled(has_source and in_range and self.ready())
        self.load_btn.setToolTip("" if connected else "Connect the SEQTRAK first")
        self.undo_btn.setVisible(bool(self._last_load))

    def _play_pad(self, pad: int) -> None:
        shifts = self.shifts()
        if self.source is None or shifts is None:
            return
        a, b = self._region()
        shift = shifts[pad]
        try:
            self.window.player.play(keys.repitch(self.source.left[a:b], shift),
                                    keys.repitch(self.source.right[a:b], shift),
                                    self.source.rate, a)
        except RuntimeError as e:
            self.status.setText(str(e))

    def _load(self) -> None:
        source_pad = self.source_pad()
        notes, shifts = self.notes(), self.shifts()
        if source_pad is None or notes is None or not self.confirm_pads(list(range(keys.PADS))):
            return
        a, b = self._region()
        start = self.source.device_offset + a
        end = self.source.device_offset + b
        base = source_pad.osc[sysex.COARSE_TUNE]
        name = self.source.name[:66]
        changes = []
        try:
            for i in range(keys.PADS):
                coarse = base + shifts[i]
                label = f"{name} {keys.note_name(notes[i])}"
                current = self.current_pad(i)
                if i == self.source.pad:
                    msgs = sysex.trim_messages(current, start, end, source_pad.last_frame,
                                               label)
                    msgs.append(sysex.coarse_message(i, coarse))
                else:
                    msgs = sysex.slice_messages(current, source_pad, start, end, label,
                                                coarse=coarse)
                changes.append((current, label, msgs))
        except ValueError as e:
            QMessageBox.warning(self, "Load to pads", str(e))
            return
        self.apply(changes)
        self.status.setText(f"✓  Pads 1–{keys.PADS} play "
                            + " ".join(keys.note_name(n) for n in notes)
                            + ". Play them on the SEQTRAK; Undo puts them back.")
        self._refresh()
