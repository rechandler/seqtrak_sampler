"""The Pad sound strip in Edit mode: a pad's level, pan, pitch, envelope and filter,
and its loop, changed live on the SEQTRAK."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from .. import padsound, sysex
from .widgets import Toggle


class _ValueLabel(QLabel):
    reset = Signal()

    def mouseDoubleClickEvent(self, event):
        self.reset.emit()


class PadSoundPanel(QWidget):
    changed = Signal(str, int)        # setting key, new value (the user moved it)
    loopToggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sliders: dict[str, QSlider] = {}
        self._values: dict[str, QLabel] = {}
        self._setting = False

        title = QLabel("PAD SOUND")
        title.setObjectName("sectionTitle")
        note = QLabel("Changes play on the SEQTRAK as you move them. Undo puts them back.")
        note.setObjectName("muted")
        self.loop_box = Toggle("Loop")
        self.loop_box.setToolTip("Loop the end of the sample while the pad is held. Drag "
                                 "the green marker on the waveform to set where the loop "
                                 "starts.")
        self.loop_box.toggled.connect(self._loop_toggled)
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(title)
        top.addWidget(note, 1)
        top.addWidget(self.loop_box)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        per_row = 4
        for n, param in enumerate(padsound.PARAMS):
            row, col = divmod(n, per_row)
            label = QLabel(param.label)
            label.setObjectName("muted")
            label.setToolTip(param.tip)
            slider = QSlider(Qt.Horizontal)
            slider.setRange(param.low, param.high)
            slider.setToolTip(param.tip)
            slider.valueChanged.connect(lambda v, p=param: self._moved(p, v))
            value = _ValueLabel()
            value.setFixedWidth(52)
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if param.center is not None:
                value.setToolTip("Double-click to centre")
                value.reset.connect(lambda s=slider, c=param.center: s.setValue(c))
            grid.addWidget(label, row, col * 3)
            grid.addWidget(slider, row, col * 3 + 1)
            grid.addWidget(value, row, col * 3 + 2)
            grid.setColumnStretch(col * 3 + 1, 1)
            if col < per_row - 1:
                grid.setColumnMinimumWidth(col * 3 + 2, 60)
            self._sliders[param.key] = slider
            self._values[param.key] = value

        col_layout = QVBoxLayout(self)
        col_layout.setContentsMargins(0, 6, 0, 0)
        col_layout.setSpacing(8)
        col_layout.addLayout(top)
        col_layout.addLayout(grid)

    def set_pad(self, pad: sysex.SamplerPad) -> None:
        """Show the pad's settings without sending anything."""
        self._setting = True
        try:
            for param in padsound.PARAMS:
                value = padsound.value(pad, param)
                slider = self._sliders[param.key]
                slider.setEnabled(value is not None)
                if value is not None:
                    slider.setValue(value)
                self._values[param.key].setText("" if value is None else param.text(value))
            self.loop_box.setChecked(pad.loop_on)
        finally:
            self._setting = False

    def value(self, key: str) -> int:
        return self._sliders[key].value()

    def _moved(self, param: padsound.Param, value: int) -> None:
        self._values[param.key].setText(param.text(value))
        if not self._setting:
            self.changed.emit(param.key, value)

    def _loop_toggled(self, on: bool) -> None:
        if not self._setting:
            self.loopToggled.emit(on)
