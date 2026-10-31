"""What Slice and Keys mode share: a source pad, a pad map, and loading onto pads
with Undo. Each mode decides what goes on which pad."""

from __future__ import annotations

from array import array
from dataclasses import dataclass

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QComboBox, QLabel, QMessageBox, QPushButton, QWidget

from .. import sysex
from . import device as dev
from . import theme
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


class PadPanel(QWidget):
    """A mode screen that loads onto pads from a source pad. `window` is the
    MainWindow (device, pads, player...). Subclasses lay the widgets out and fill
    in _refresh and on_source."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.source: Source | None = None
        self._last_load: list[int] = []   # pads changed by the last load, for Undo
        self.setObjectName("card")
        self.setAttribute(Qt.WA_StyledBackground)

        self.source_box = QComboBox()
        self.source_box.setMinimumWidth(240)
        self.source_box.activated.connect(self._source_chosen)
        self.wave = WaveformView()
        self.pad_map = PadMap()
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

    # Source

    def refresh_sources(self) -> None:
        """Rebuild the source list from the pads."""
        current = self.source_box.currentData()
        self.source_box.blockSignals(True)
        self.source_box.clear()
        for i in range(sysex.SAMPLER_PADS):
            pad = self.window._pads.get(i)
            if pad is not None and not pad.empty:
                self.source_box.addItem(f"Pad {i + 1}  ·  {pad.name}", ("pad", i))
        if not self.source_box.count():
            self.source_box.addItem("No pads with a sample", None)
        self.source_box.setCurrentIndex(max(0, self._index_of(current)))
        self.source_box.blockSignals(False)

    def _index_of(self, data) -> int:
        # QComboBox.findData can't match the ("pad", n) tuples, so look them up here.
        return next((i for i in range(self.source_box.count())
                     if self.source_box.itemData(i) == data), -1)

    def activate(self) -> None:
        """Called when the mode opens: start from pad 1 if there's nothing loaded."""
        self.refresh_sources()
        if self.source is None and self.source_box.currentData() is not None:
            self._source_chosen(self.source_box.currentIndex())
        self._refresh()

    def choose_pad(self, pad: int) -> None:
        """Make pad `pad` the source (a click in the sidebar's pad list)."""
        self.refresh_sources()
        index = self._index_of(("pad", pad))
        if index >= 0:
            self.source_box.setCurrentIndex(index)
            self._source_chosen(index)

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
        self.on_source(source)
        self.status.clear()
        self._refresh()

    def on_source(self, source: Source) -> None:
        """A new source was set; the mode can set up its own view of it."""

    def _region(self) -> tuple[int, int]:
        return self.wave._start, self.wave._end

    def source_pad(self) -> sysex.SamplerPad | None:
        return self.window._pads.get(self.source.pad) if self.source else None

    def ready(self) -> bool:
        """Connected, with the source pad's settings read: loading can go ahead."""
        pad = self.source_pad()
        return (self.window.device.state == dev.CONNECTED and pad is not None
                and pad.osc is not None)

    def _refresh(self) -> None:
        raise NotImplementedError

    def _load(self) -> None:
        raise NotImplementedError

    # Loading and Undo

    def confirm_pads(self, pads: list[int]) -> bool:
        """Ask before replacing pads (other than the source) that hold a sample."""
        taken = [f"pad {i + 1} (“{p.name}”)" for i in pads
                 if i != self.source.pad and (p := self.window._pads.get(i)) is not None
                 and not p.empty]
        if not taken:
            return True
        answer = QMessageBox.question(
            self, "Use these pads?",
            "This puts the new sounds on " + ", ".join(taken) + " instead of their "
            "current samples. Their samples stay on the SEQTRAK, so Undo brings them "
            "back.\n\nContinue?")
        return answer == QMessageBox.Yes

    def apply(self, changes: list[tuple[sysex.SamplerPad, str, list[list[int]]]]) -> None:
        """Send each (pad as it is now, its new name, messages), keeping Undo steps."""
        for current, label, msgs in changes:
            self.window._push_history(current, label)
            self.window.device.send_trim(msgs, current.pad)
        self._last_load = [current.pad for current, _, _ in changes]

    def pad_status(self, i: int, source_text: str) -> tuple[str, str]:
        """(status, kind) for pad i's tile: the source, empty, or what it replaces."""
        pad = self.window._pads.get(i)
        if self.source is not None and self.source.pad == i:
            return source_text, "source"
        if pad is None or pad.empty:
            return "empty", "empty"
        return f"was “{pad.name}”", "replace"

    def current_pad(self, i: int) -> sysex.SamplerPad:
        return self.window._pads.get(i) or sysex.SamplerPad(i, "", 0, 0, False, 0)

    def _undo_load(self) -> None:
        for pad in self._last_load:
            self.window.undo_pad(pad)
        self._last_load = []
        self.status.setText("Pads put back the way they were.")
        self._refresh()
