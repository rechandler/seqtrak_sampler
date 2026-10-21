"""Slice mode: cut one sample into segments and load segment N onto pad N."""

from __future__ import annotations

from array import array
from dataclasses import dataclass

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSizePolicy, QVBoxLayout,
    QWidget,
)

from .. import audio, slicing, sysex
from . import device as dev
from . import theme
from .loader import Job, SliceLoader
from .waveform import WaveformView


@dataclass
class Source:
    name: str
    left: array
    right: array
    rate: int
    pad: int | None = None      # the pad that holds this audio, if any
    device_offset: int = 0      # that pad's frame for source frame 0


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
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
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
        for take in self.window.takes:
            self.source_box.addItem(f"Sample  ·  {take.label}", ("take", id(take)))
        if not self.source_box.count():
            self.source_box.addItem("Nothing to slice yet", None)
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
        kind, key = data
        if kind == "pad":
            pad = self.window._pads.get(key)
            if pad is None or pad.empty:
                return
            self.status.setText(f"Reading pad {key + 1}: the SEQTRAK plays it once…")
            if not self.window.open_pad(pad, on_done=self._pad_captured):
                self.status.setText("Couldn't play the pad. Is the SEQTRAK's USB audio "
                                    "connected?")
        else:
            take = next((t for t in self.window.takes if id(t) == key), None)
            if take is None:
                return
            left, right = self.window._render(take, enforce_limit=False)
            pad = take.pad_index
            self.set_source(Source(take.label, left, right, take.rate, pad,
                                   take.device_offset + take.start))

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
        return slicing.slices(start, end, self.wave.markers())

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
                status, kind = f"replaces “{pad.name}”", "replace"
            tiles.append((f"{seconds:.2f} s", status, kind))
        self.pad_map.set_tiles(tiles)

        connected = self.window.device.state == dev.CONNECTED
        too_long = has_source and \
            (self._region()[1] - self._region()[0]) > audio.MAX_SECONDS * self.source.rate
        self.load_btn.setText("Load to pad 1" if n <= 1 else f"Load to pads 1–{n}")
        self.load_btn.setEnabled(has_source and connected and n >= 1 and not too_long)
        if not connected:
            self.load_btn.setToolTip("Connect the SEQTRAK first")
        elif too_long:
            self.load_btn.setToolTip(f"The SEQTRAK samples up to {audio.MAX_SECONDS:g} s; "
                                     f"drag the handles in")
        else:
            self.load_btn.setToolTip("")

    def _load(self) -> None:
        segs = self.segments()
        if self.source is None or not segs:
            return
        replaced = []
        for i in range(len(segs)):
            pad = self.window._pads.get(i)
            if self.source.pad != i and pad is not None and not pad.empty:
                replaced.append(f"pad {i + 1} (“{pad.name}”)")
        if replaced:
            answer = QMessageBox.question(
                self, "Replace pads?",
                "This replaces the recordings on " + ", ".join(replaced) + ". They can't "
                "be brought back afterwards.\n\nContinue?")
            if answer != QMessageBox.Yes:
                return
        start, end = self._region()
        left, right = self.source.left[start:end], self.source.right[start:end]
        name = self.source.name[:70]
        jobs = []
        for i, (a, b) in enumerate(segs):
            trim_only = self.source.pad == i
            jobs.append(Job(i, a - start, b - start, f"{name} {i + 1}", trim_only=trim_only,
                            device_offset=self.source.device_offset + start))
        SliceLoader(self.window, left, right, self.source.rate, jobs).exec()
        self.window.device.request_pads()
        self.source = None
        self.wave.clear()
        self.status.setText("Done. Click a pad in the list on the left to fine-tune it in "
                            "Edit mode.")
        self._refresh()
