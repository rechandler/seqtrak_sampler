"""Slice mode: cut a pad's sample into segments and put segment N on pad N.

No audio is copied: pads 2..N get the source pad's sound settings, including its
recording (user wave number), level, envelope and filter, and each is given its own
start and end point. That's instant, exact to the frame, and undoable, since the pads'
previous recordings stay on the SEQTRAK.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSizePolicy, QVBoxLayout,
    QWidget,
)

from .. import slicing, sysex
from . import device as dev
from . import theme
from .pattern import PatternInfo, PatternWriter
from .waveform import WaveformView


@dataclass
class Source:
    name: str
    left: array
    right: array
    rate: int
    pad: int                    # the pad whose recording this is
    device_offset: int = 0      # the recording's frame for source frame 0


class PadMap(QWidget):
    """Seven pad tiles showing what loading will do to each pad. Click to hear a slice."""

    clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(64)
        self.setMouseTracking(True)
        self._tiles: list[tuple[str, str, str]] = []  # (title, status, kind) per pad
        self._hover = -1

    def set_tiles(self, tiles: list[tuple[str, str, str]]) -> None:
        self._tiles = tiles
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(600, 64)

    def _rect(self, i: int) -> QRectF:
        gap = 8
        w = (self.width() - gap * (sysex.SAMPLER_PADS - 1)) / sysex.SAMPLER_PADS
        return QRectF(i * (w + gap), 0, w, self.height() - 1)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        title_font = QFont(self.font())
        title_font.setPixelSize(12)
        title_font.setWeight(QFont.DemiBold)
        small = QFont(self.font())
        small.setPixelSize(11)
        for i, (title, status, kind) in enumerate(self._tiles):
            r = self._rect(i)
            used = kind != "unused"
            color = QColor(theme.SEGMENT_COLORS[i]) if used else QColor(theme.FAINT)
            fill = QColor(color)
            fill.setAlpha(34 if used else 0)
            if used and i == self._hover:
                fill.setAlpha(60)
            p.setPen(color if used else QColor(theme.BORDER))
            p.setBrush(fill if used else QColor(theme.SURFACE))
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 9, 9)
            p.setFont(title_font)
            p.setPen(QColor(theme.TEXT if used else theme.FAINT))
            p.drawText(r.adjusted(10, 8, -8, -30), Qt.AlignLeft | Qt.AlignVCenter,
                       f"Pad {i + 1}")
            p.setPen(color if used else QColor(theme.FAINT))
            p.drawText(r.adjusted(10, 8, -8, -30), Qt.AlignRight | Qt.AlignVCenter, title)
            p.setFont(small)
            p.setPen(QColor(theme.DANGER if kind == "replace" else
                            theme.MUTED if used else theme.FAINT))
            text = p.fontMetrics().elidedText(status, Qt.ElideRight, int(r.width() - 18))
            p.drawText(r.adjusted(10, 32, -8, -8), Qt.AlignLeft | Qt.AlignVCenter, text)

    def mouseMoveEvent(self, event):
        hover = next((i for i in range(len(self._tiles))
                      if self._rect(i).contains(event.position())
                      and self._tiles[i][2] != "unused"), -1)
        if hover != self._hover:
            self._hover = hover
            self.setCursor(Qt.PointingHandCursor if hover >= 0 else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, event):
        self._hover = -1
        self.update()

    def mousePressEvent(self, event):
        if self._hover >= 0:
            self.clicked.emit(self._hover)


class SlicePanel(QWidget):
    """The Slice mode screen. `window` is the MainWindow (device, pads, player...)."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.source: Source | None = None
        self._last_load: list[int] = []   # pads changed by the last load, for Undo
        self._pattern_backup: PatternInfo | None = None   # for Undo pattern
        self._pattern_busy = False
        self.writer = PatternWriter(window.device, parent=self)
        self.setObjectName("card")
        self.setAttribute(Qt.WA_StyledBackground)

        title = QLabel("Slice to pads")
        title.setObjectName("panelTitle")
        source_label = QLabel("Source")
        source_label.setObjectName("muted")
        self.source_box = QComboBox()
        self.source_box.setMinimumWidth(240)
        self.source_box.activated.connect(self._source_chosen)
        top = QHBoxLayout()
        top.addWidget(title)
        top.addStretch()
        top.addWidget(source_label)
        top.addWidget(self.source_box)

        self.wave = WaveformView()
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

        self.pad_map = PadMap()
        self.pad_map.clicked.connect(self._play_segment)

        self.status = QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.load_btn = QPushButton()
        self.load_btn.setObjectName("primary")
        self.load_btn.clicked.connect(self._load)
        self.undo_btn = QPushButton("↶  Undo")
        self.undo_btn.setToolTip("Put the pads back the way they were before this load")
        self.undo_btn.clicked.connect(self._undo_load)
        self.undo_btn.hide()
        self.writer.progress.connect(self.status.setText)
        self.pattern_btn = QPushButton("Write pattern")
        self.pattern_btn.setToolTip("Write a sampler-track pattern that plays the slices "
                                    "in order, at the SEQTRAK's tempo")
        self.pattern_btn.clicked.connect(self._write_pattern)
        self.pattern_undo_btn = QPushButton("↶  Undo pattern")
        self.pattern_undo_btn.setToolTip("Put the sampler pattern back the way it was")
        self.pattern_undo_btn.clicked.connect(self._undo_pattern)
        self.pattern_undo_btn.hide()
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.pattern_undo_btn)
        bottom.addWidget(self.pattern_btn)
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

    # Source

    def refresh_sources(self) -> None:
        """Rebuild the source list from the pads and loaded samples."""
        current = self.source_box.currentData()
        self.source_box.blockSignals(True)
        self.source_box.clear()
        for i in range(sysex.SAMPLER_PADS):
            pad = self.window._pads.get(i)
            if pad is not None and not pad.empty:
                self.source_box.addItem(f"Pad {i + 1}  ·  {pad.name}", ("pad", i))
        if not self.source_box.count():
            self.source_box.addItem("No pads with a sample", None)
        index = self.source_box.findData(current)
        self.source_box.setCurrentIndex(max(0, index))
        self.source_box.blockSignals(False)

    def activate(self) -> None:
        """Called when Slice mode opens: start from pad 1 if there's nothing loaded."""
        self.refresh_sources()
        if self.source is None and self.source_box.currentData() is not None:
            self._source_chosen(self.source_box.currentIndex())
        self._refresh()

    def _source_chosen(self, index: int) -> None:
        data = self.source_box.itemData(index)
        if data is None:
            return
        _kind, key = data
        pad = self.window._pads.get(key)
        if pad is None or pad.empty:
            return
        self.status.setText(f"Reading pad {key + 1}: the SEQTRAK plays it once…")
        if not self.window.open_pad(pad, on_done=self._pad_captured):
            self.status.setText("Couldn't play the pad. Is the SEQTRAK's USB audio "
                                "connected?")

    def _pad_captured(self, pad: sysex.SamplerPad, left, right, rate: int) -> None:
        if left is None:
            self.status.setText(f"Pad {pad.pad + 1} played silence. Is the sampler track "
                                f"muted?")
            return
        length = min(len(left), pad.end - pad.start + 1)
        self.set_source(Source(pad.name, left[:length], right[:length], rate, pad.pad,
                               pad.start))

    def set_source(self, source: Source) -> None:
        self.source = source
        n = len(source.left)
        self.wave.set_sound(source.left, source.right, source.rate, 0, n)
        self.wave.set_markers(slicing.equal_markers(0, n, 2))
        self.status.clear()
        self._refresh()

    # Segments

    def _region(self) -> tuple[int, int]:
        return self.wave._start, self.wave._end

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
            pad = self.window._pads.get(i)
            if self.source.pad == i:
                status, kind = "source · trimmed", "source"
            elif pad is None or pad.empty:
                status, kind = "empty", "empty"
            else:
                status, kind = f"was “{pad.name}”", "replace"
            tiles.append((f"{seconds:.2f} s", status, kind))
        self.pad_map.set_tiles(tiles)

        connected = self.window.device.state == dev.CONNECTED
        source_pad = self.window._pads.get(self.source.pad) if has_source else None
        ready = source_pad is not None and source_pad.osc is not None
        self.load_btn.setText("Apply to pad 1" if n <= 1 else f"Load to pads 1–{n}")
        self.load_btn.setEnabled(has_source and connected and ready)
        self.load_btn.setToolTip("" if connected else "Connect the SEQTRAK first")
        self.undo_btn.setVisible(bool(self._last_load))
        self.pattern_btn.setEnabled(has_source and connected and n > 0
                                    and not self._pattern_busy)
        self.pattern_undo_btn.setVisible(self._pattern_backup is not None)
        self.pattern_undo_btn.setEnabled(connected and not self._pattern_busy)

    def _load(self) -> None:
        segs = self.segments()
        source_pad = self.window._pads.get(self.source.pad) if self.source else None
        if source_pad is None or not segs:
            return
        taken = [f"pad {i + 1} (“{p.name}”)" for i in range(len(segs))
                 if i != self.source.pad and (p := self.window._pads.get(i)) is not None
                 and not p.empty]
        if taken:
            answer = QMessageBox.question(
                self, "Use these pads?",
                "This puts segments on " + ", ".join(taken) + " instead of their current "
                "samples. Their samples stay on the SEQTRAK, so Undo brings them back.\n\n"
                "Continue?")
            if answer != QMessageBox.Yes:
                return
        name = self.source.name[:70]
        changes = []
        try:
            for i, (a, b) in enumerate(segs):
                start = self.source.device_offset + a
                end = self.source.device_offset + b
                label = f"{name} {i + 1}"
                current = self.window._pads.get(i) or sysex.SamplerPad(i, "", 0, 0, False, 0)
                if i == self.source.pad:
                    msgs = sysex.trim_messages(current, start, end, source_pad.last_frame,
                                               label)
                else:
                    msgs = sysex.slice_messages(current, source_pad, start, end, label)
                changes.append((current, label, msgs))
        except ValueError as e:
            QMessageBox.warning(self, "Load to pads", str(e))
            return
        for current, label, msgs in changes:
            self.window._push_history(current, label)
            self.window.device.send_trim(msgs, current.pad)
        self._last_load = [current.pad for current, _, _ in changes]
        pads = "pad 1" if len(segs) == 1 else f"pads 1–{len(segs)}"
        self.status.setText(f"✓  Loaded onto {pads}. Play them on the SEQTRAK; Undo puts "
                            f"them back.")
        self._refresh()

    # Writing a pattern that plays the slices

    def _pattern_running(self, busy: bool) -> None:
        self._pattern_busy = busy
        self._refresh()

    def _write_pattern(self) -> None:
        if self.source is None or not self.segments():
            return
        self._pattern_running(True)
        self.writer.read(self._pattern_read)

    def _pattern_read(self, info: PatternInfo | None, error: str | None) -> None:
        if info is None:
            self._pattern_running(False)
            self.status.setText(error)
            return
        segs = self.segments()
        notes = [(p.note_low if (p := self.window._pads.get(i)) is not None
                  and p.note_low else sysex.BASE_NOTE + i) for i in range(len(segs))]
        planned = slicing.pattern_notes(segs, self.source.rate, info.tempo, notes)
        fits = [n for n in planned if n[0] < sysex.MAX_PATTERN_STEPS]
        last = max(step for step, *_ in fits) + 1 if fits else 1
        length = info.steps if last <= info.steps else min(
            sysex.MAX_PATTERN_STEPS, -(-last // 16) * 16)
        lines = [f"Write pattern {info.pattern + 1} of the sampler track: "
                 f"{len(fits)} notes, one per slice, at {info.tempo} BPM?"]
        if info.events:
            lines.append(f"It replaces the {len(info.events)} notes in it now. "
                         f"Undo pattern puts them back.")
        if length != info.steps:
            lines.append(f"The pattern grows from {info.steps} to {length} steps to fit.")
        if len(fits) < len(planned):
            lines.append(f"{len(planned) - len(fits)} slices start after 128 steps "
                         f"(8 bars) and are left out.")
        lines.append("Slice N plays pad N, so load the slices onto the pads too.")
        if QMessageBox.question(self, "Write pattern", "\n\n".join(lines)) != \
                QMessageBox.Yes:
            self._pattern_running(False)
            self.status.setText("")
            return
        self.writer.write(info, fits, lambda ok, msg: self._pattern_done(info, ok, msg))

    def _pattern_done(self, info: PatternInfo, ok: bool, message: str) -> None:
        # Keep the backup even when the check failed: notes may have been written.
        self._pattern_backup = info
        if ok:
            message = f"✓  {message}. Press Play on the SEQTRAK."
        self.status.setText(message)
        self._pattern_running(False)

    def _undo_pattern(self) -> None:
        info = self._pattern_backup
        if info is None:
            return
        self._pattern_running(True)

        def done(ok: bool, message: str) -> None:
            if ok:
                self._pattern_backup = None
                message = f"✓  {message}."
            self.status.setText(message)
            self._pattern_running(False)
        self.writer.restore(info, done)

    def _undo_load(self) -> None:
        for pad in self._last_load:
            self.window.undo_pad(pad)
        self._last_load = []
        self.status.setText("Pads put back the way they were.")
        self._refresh()
