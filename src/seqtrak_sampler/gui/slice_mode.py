"""Slice mode: cut a pad's sample into segments and put segment N on pad N.

No audio is copied: pads 2..N get the source pad's sound settings, including its
recording (user wave number), level, envelope and filter, and each is given its own
start and end point. That's instant, exact to the frame, and undoable, since the pads'
previous recordings stay on the SEQTRAK.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QSizePolicy, QVBoxLayout,
)

from .. import slicing, sysex
from . import device as dev
from .pad_panel import PadMap, PadPanel, Source

__all__ = ["PadMap", "SlicePanel", "Source"]


class SlicePanel(PadPanel):
    """The Slice mode screen. `window` is the MainWindow (device, pads, player...)."""

    def __init__(self, window):
        super().__init__(window)
        title = QLabel("Slice to pads")
        title.setObjectName("panelTitle")
        source_label = QLabel("Source")
        source_label.setObjectName("muted")
        top = QHBoxLayout()
        top.addWidget(title)
        top.addStretch()
        top.addWidget(source_label)
        top.addWidget(self.source_box)

        self.wave.set_slicing(True)
        self.wave.regionChanged.connect(self._region_changed)
        self.wave.markersChanged.connect(lambda _m: self._refresh())
        self.wave.max_markers = slicing.MAX_SLICES - 1     # 7 segments, one per pad
        self.wave.markerRefused.connect(lambda: self.status.setText(
            f"{slicing.MAX_SLICES} segments is the most: one per pad. Move or remove a "
            f"cut (right-click) to make another."))

        seg_label = QLabel("Segments")
        seg_label.setObjectName("muted")
        self.minus_btn = QPushButton("−")
        self.plus_btn = QPushButton("+")
        for b in (self.minus_btn, self.plus_btn):
            b.setFixedWidth(36)
            b.setToolTip("Split into equal segments (replaces your cuts)")
        self.minus_btn.clicked.connect(lambda: self._split(self.count() - 1))
        self.plus_btn.clicked.connect(lambda: self._split(self.count() + 1))
        self.count_label = QLabel()
        self.count_label.setObjectName("segmentCount")
        self.count_label.setAlignment(Qt.AlignCenter)
        self.count_label.setFixedWidth(28)
        self.auto_btn = QPushButton("Auto-detect hits")
        self.auto_btn.clicked.connect(self._auto)
        hint = QLabel("Click to add a cut · drag to move · right-click to remove")
        hint.setObjectName("muted")
        hint.setMinimumWidth(0)
        hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        seg_row = QHBoxLayout()
        seg_row.setSpacing(8)
        seg_row.addWidget(seg_label)
        seg_row.addWidget(self.minus_btn)
        seg_row.addWidget(self.count_label)
        seg_row.addWidget(self.plus_btn)
        seg_row.addSpacing(8)
        seg_row.addWidget(self.auto_btn)
        seg_row.addSpacing(12)
        seg_row.addWidget(hint, 1)

        self.pad_map.clicked.connect(self._play_segment)

        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.undo_btn)
        bottom.addWidget(self.load_btn)

        col = QVBoxLayout(self)
        col.setContentsMargins(20, 16, 20, 18)
        col.setSpacing(12)
        col.addLayout(top)
        col.addWidget(self.wave, 1)
        col.addLayout(seg_row)
        col.addWidget(self.pad_map)
        col.addLayout(bottom)
        self._refresh()

    def on_source(self, source: Source) -> None:
        self.wave.set_markers(slicing.equal_markers(0, len(source.left), 2))

    # Segments

    def segments(self) -> list[tuple[int, int]]:
        if self.source is None:
            return []
        start, end = self._region()
        return slicing.slices(start, end, self.wave.markers())[:slicing.MAX_SLICES]

    def count(self) -> int:
        return len(self.segments())

    def _split(self, count: int) -> None:
        if self.source is None:
            return
        count = max(1, min(slicing.MAX_SLICES, count))
        start, end = self._region()
        self.wave.set_markers(slicing.equal_markers(start, end, count))
        self._refresh()

    def _auto(self) -> None:
        if self.source is None:
            return
        start, end = self._region()
        markers = slicing.hit_markers(self.source.left, self.source.right, self.source.rate,
                                      start, end)
        if not markers:
            self.status.setText("No clear hits found. Click the waveform to add cuts.")
        self.wave.set_markers(markers)
        self._refresh()

    def _region_changed(self, start: int, end: int) -> None:
        # Cuts outside the region don't make segments; dropping them keeps the
        # 7-segment cap true when the region grows again.
        markers = self.wave.markers()
        inside = [m for m in markers if start < m < end]
        if inside != markers:
            self.wave.set_markers(inside)
        self._refresh()

    def _play_segment(self, pad: int) -> None:
        segs = self.segments()
        if self.source is None or pad >= len(segs):
            return
        a, b = segs[pad]
        try:
            self.window.player.play(self.source.left[a:b], self.source.right[a:b],
                                    self.source.rate, a)
        except RuntimeError as e:
            self.status.setText(str(e))

    # Pad map and loading

    def _refresh(self) -> None:
        segs = self.segments()
        n = len(segs)
        self.count_label.setText(str(n) if self.source else "–")
        has_source = self.source is not None
        for w in (self.minus_btn, self.plus_btn, self.auto_btn):
            w.setEnabled(has_source)
        self.minus_btn.setEnabled(has_source and n > 1)
        self.plus_btn.setEnabled(has_source and n < slicing.MAX_SLICES)

        tiles = []
        for i in range(sysex.SAMPLER_PADS):
            if i >= n:
                tiles.append(("", "unused", "unused"))
                continue
            a, b = segs[i]
            seconds = (b - a) / self.source.rate
            status, kind = self.pad_status(i, "source · trimmed")
            tiles.append((f"{seconds:.2f} s", status, kind))
        self.pad_map.set_tiles(tiles)

        connected = self.window.device.state == dev.CONNECTED
        self.load_btn.setText("Apply to pad 1" if n <= 1 else f"Load to pads 1–{n}")
        self.load_btn.setEnabled(has_source and self.ready())
        self.load_btn.setToolTip("" if connected else "Connect the SEQTRAK first")
        self.undo_btn.setVisible(bool(self._last_load))

    def _load(self) -> None:
        segs = self.segments()
        source_pad = self.source_pad()
        if source_pad is None or not segs or not self.confirm_pads(list(range(len(segs)))):
            return
        name = self.source.name[:70]
        changes = []
        try:
            for i, (a, b) in enumerate(segs):
                start = self.source.device_offset + a
                end = self.source.device_offset + b
                label = f"{name} {i + 1}"
                current = self.current_pad(i)
                if i == self.source.pad:
                    msgs = sysex.trim_messages(current, start, end, source_pad.last_frame,
                                               label)
                else:
                    msgs = sysex.slice_messages(current, source_pad, start, end, label)
                changes.append((current, label, msgs))
        except ValueError as e:
            QMessageBox.warning(self, "Load to pads", str(e))
            return
        self.apply(changes)
        pads = "pad 1" if len(segs) == 1 else f"pads 1–{len(segs)}"
        self.status.setText(f"✓  Loaded onto {pads}. Play them on the SEQTRAK; Undo puts "
                            f"them back.")
        self._refresh()
