"""Stereo waveform view with draggable start/end handles."""

from __future__ import annotations

from array import array

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from .. import audio

PEAK_BUCKETS = 4096
HANDLE_GRAB_PX = 8


def _peaks(frames: array, buckets: int) -> tuple[list[int], list[int]]:
    n = len(frames)
    buckets = max(1, min(buckets, n))
    mins, maxs = [], []
    for b in range(buckets):
        chunk = frames[b * n // buckets:(b + 1) * n // buckets]
        mins.append(min(chunk))
        maxs.append(max(chunk))
    return mins, maxs


class WaveformView(QWidget):
    regionChanged = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self._channels: list[tuple[list[int], list[int]]] = []
        self._frames = 0
        self._peak = 1
        self._rate = 44100
        self._start = 0
        self._end = 0
        self._playhead: int | None = None
        self._dragging: str | None = None

    def set_sound(self, left: array, right: array, rate: int, start: int, end: int) -> None:
        self._channels = [_peaks(left, PEAK_BUCKETS), _peaks(right, PEAK_BUCKETS)]
        self._frames = len(left)
        # Scale the view to the loudest point so quiet resamples are still visible.
        self._peak = max(256, *(max(max(maxs), -min(mins)) for mins, maxs in self._channels))
        self._rate = rate
        self._playhead = None
        self.set_region(start, end)

    def clear(self) -> None:
        self._channels = []
        self._frames = 0
        self._playhead = None
        self.update()

    def set_region(self, start: int, end: int) -> None:
        self._start, self._end = start, end
        self.update()

    def set_playhead(self, frame: int | None) -> None:
        self._playhead = frame
        self.update()

    # Coordinate helpers

    def _x(self, frame: int) -> float:
        return frame / max(1, self._frames) * self.width()

    def _frame(self, x: float) -> int:
        return max(0, min(self._frames, round(x / max(1, self.width()) * self._frames)))

    # Painting

    def paintEvent(self, event):
        p = QPainter(self)
        pal = self.palette()
        p.fillRect(self.rect(), pal.base())
        if not self._channels:
            p.setPen(pal.placeholderText().color())
            p.drawText(self.rect(), Qt.AlignCenter, "Drop .Y3S files here")
            return

        w, h = self.width(), self.height()
        wave_color = pal.highlight().color()
        lane_h = h / len(self._channels)
        for lane, (mins, maxs) in enumerate(self._channels):
            top = lane * lane_h
            mid = top + lane_h / 2
            scale = (lane_h / 2 - 2) / self._peak
            p.setPen(QPen(pal.mid().color(), 1))
            p.drawLine(QPointF(0, mid), QPointF(w, mid))
            p.setPen(QPen(wave_color, 1))
            buckets = len(mins)
            for x in range(w):
                b0 = x * buckets // w
                b1 = max(b0 + 1, (x + 1) * buckets // w)
                lo, hi = min(mins[b0:b1]), max(maxs[b0:b1])
                p.drawLine(QPointF(x + 0.5, mid - hi * scale), QPointF(x + 0.5, mid - lo * scale))
            p.setPen(pal.placeholderText().color())
            p.drawText(QRectF(4, top + 2, 20, 16), Qt.AlignLeft, "LR"[lane])

        sx, ex = self._x(self._start), self._x(self._end)
        shade = QColor(pal.window().color())
        shade.setAlpha(170)
        p.fillRect(QRectF(0, 0, sx, h), shade)
        p.fillRect(QRectF(ex, 0, w - ex, h), shade)

        limit_end = self._start + int(audio.MAX_SECONDS * self._rate)
        if self._end > limit_end:
            over = QColor(220, 50, 50, 70)
            p.fillRect(QRectF(self._x(limit_end), 0, ex - self._x(limit_end), h), over)

        handle = QColor(255, 170, 0)
        for x, direction in ((sx, 1), (ex, -1)):
            p.setPen(QPen(handle, 2))
            p.drawLine(QPointF(x, 0), QPointF(x, h))
            p.setBrush(handle)
            p.drawPolygon(QPolygonF([QPointF(x, 0), QPointF(x + 9 * direction, 0),
                                     QPointF(x, 12)]))

        if self._playhead is not None:
            p.setPen(QPen(pal.text().color(), 1))
            px = self._x(self._playhead)
            p.drawLine(QPointF(px, 0), QPointF(px, h))

    # Handle dragging

    def _handle_at(self, x: float) -> str | None:
        ds, de = abs(x - self._x(self._start)), abs(x - self._x(self._end))
        if min(ds, de) > HANDLE_GRAB_PX:
            return None
        return "start" if ds <= de else "end"

    def mousePressEvent(self, event):
        if self._channels and event.button() == Qt.LeftButton:
            self._dragging = self._handle_at(event.position().x())

    def mouseMoveEvent(self, event):
        x = event.position().x()
        if self._dragging is None:
            grab = self._channels and self._handle_at(x)
            self.setCursor(Qt.SizeHorCursor if grab else Qt.ArrowCursor)
            return
        frame = self._frame(x)
        if self._dragging == "start":
            self._start = min(frame, self._end - 1)
        else:
            self._end = max(frame, self._start + 1)
        self.update()
        self.regionChanged.emit(self._start, self._end)

    def mouseReleaseEvent(self, event):
        self._dragging = None
