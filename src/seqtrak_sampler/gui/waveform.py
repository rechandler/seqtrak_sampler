"""Stereo waveform view with draggable start/end handles."""

from __future__ import annotations

from array import array

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from .. import audio
from . import theme

PEAK_BUCKETS = 4096
HANDLE_GRAB_PX = 10
RULER_H = 22
PAD = 12


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
    markersChanged = Signal(object)  # list[int], in slice mode

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self._channels: list[tuple[list[int], list[int]]] = []
        self._frames = 0
        self._peak = 1
        self._rate = 44100
        self._start = 0
        self._end = 0
        self._playhead: int | None = None
        self._dragging: str | None = None   # "start", "end" or "marker"
        self._hover: str | None = None
        self._slicing = False
        self._markers: list[int] = []
        self._drag_marker: int | None = None  # index into _markers

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

    def set_slicing(self, on: bool) -> None:
        self._slicing = on
        self.update()

    def set_markers(self, markers: list[int]) -> None:
        self._markers = sorted(markers)
        self.update()

    def markers(self) -> list[int]:
        return list(self._markers)

    def set_playhead(self, frame: int | None) -> None:
        self._playhead = frame
        self.update()

    # Coordinate helpers: the waveform is drawn inside a padded area.

    def _area(self) -> QRectF:
        return QRectF(PAD, PAD, self.width() - 2 * PAD, self.height() - 2 * PAD - RULER_H)

    def _x(self, frame: int) -> float:
        a = self._area()
        return a.left() + frame / max(1, self._frames) * a.width()

    def _frame(self, x: float) -> int:
        a = self._area()
        frac = (x - a.left()) / max(1.0, a.width())
        return max(0, min(self._frames, round(frac * self._frames)))

    # Painting

    def _channel_path(self, mins: list[int], maxs: list[int], lane: QRectF) -> QPainterPath:
        mid = lane.center().y()
        scale = (lane.height() / 2 - 2) / self._peak
        cols = max(1, int(lane.width()))
        buckets = len(mins)
        top, bottom = [], []
        for c in range(cols):
            b0 = c * buckets // cols
            b1 = max(b0 + 1, (c + 1) * buckets // cols)
            hi = max(maxs[b0:b1])
            lo = min(mins[b0:b1])
            x = lane.left() + c + 0.5
            top.append(QPointF(x, mid - max(hi, 0) * scale - 0.5))
            bottom.append(QPointF(x, mid - min(lo, 0) * scale + 0.5))
        path = QPainterPath()
        path.addPolygon(QPolygonF(top + bottom[::-1]))
        path.closeSubpath()
        return path

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bg = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(QColor(theme.BG))
        p.drawRoundedRect(bg, 12, 12)

        if not self._channels:
            self._paint_empty(p, bg)
            return

        area = self._area()
        lane_h = area.height() / len(self._channels)
        lanes = [QRectF(area.left(), area.top() + i * lane_h, area.width(), lane_h)
                 for i in range(len(self._channels))]
        paths = [self._channel_path(mins, maxs, lane)
                 for (mins, maxs), lane in zip(self._channels, lanes)]

        p.setPen(QPen(QColor(theme.SURFACE_2), 1))
        for lane in lanes:
            p.drawLine(QPointF(lane.left(), lane.center().y()),
                       QPointF(lane.right(), lane.center().y()))
        if len(lanes) == 2:
            p.setPen(QPen(QColor(theme.BORDER), 1, Qt.DashLine))
            p.drawLine(QPointF(area.left(), lanes[1].top()), QPointF(area.right(), lanes[1].top()))

        sx, ex = self._x(self._start), self._x(self._end)
        selection = QRectF(sx, area.top() - PAD + 2, ex - sx, area.height() + PAD - 2)

        # Outside the selection: dim. Inside: bright.
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.SURFACE_3))
        for path in paths:
            p.drawPath(path)
        tint = QColor(theme.ACCENT)
        tint.setAlpha(10)
        p.fillRect(selection, tint)
        p.save()
        p.setClipRect(selection)
        p.setBrush(QColor(theme.TEXT))
        for path in paths:
            p.drawPath(path)
        p.restore()

        limit_end = self._start + int(audio.MAX_SECONDS * self._rate)
        if self._end > limit_end:
            lx = self._x(limit_end)
            over = QRectF(lx, selection.top(), ex - lx, selection.height())
            red = QColor(theme.DANGER)
            red.setAlpha(45)
            p.fillRect(over, red)
            p.save()
            p.setClipRect(over)
            p.setBrush(QColor(theme.DANGER))
            for path in paths:
                p.drawPath(path)
            p.restore()
            p.setPen(QPen(QColor(theme.DANGER), 1, Qt.DashLine))
            p.drawLine(QPointF(lx, selection.top()), QPointF(lx, selection.bottom()))

        small = QFont(self.font())
        small.setPixelSize(10)
        small.setWeight(QFont.DemiBold)
        p.setFont(small)
        p.setPen(QColor(theme.FAINT))
        for name, lane in zip("LR", lanes):
            p.drawText(QRectF(area.left() + 4, lane.top() + 2, 20, 14), Qt.AlignLeft, name)

        self._paint_ruler(p, area)
        if self._slicing:
            self._paint_slices(p, selection)
        for which, x in (("start", sx), ("end", ex)):
            self._paint_handle(p, x, selection, which)

        if self._playhead is not None:
            px = self._x(self._playhead)
            p.setPen(QPen(QColor(theme.ACCENT), 2))
            p.drawLine(QPointF(px, selection.top()), QPointF(px, selection.bottom()))

    def _paint_slices(self, p: QPainter, selection: QRectF) -> None:
        inside = [m for m in self._markers if self._start < m < self._end]
        edges = [self._start, *inside, self._end]
        font = QFont(self.font())
        font.setPixelSize(11)
        font.setWeight(QFont.DemiBold)
        p.setFont(font)
        colors = theme.SEGMENT_COLORS
        for n, (a, b) in enumerate(zip(edges, edges[1:]), start=1):
            xa, xb = self._x(a), self._x(b)
            color = QColor(colors[(n - 1) % len(colors)])
            band = QColor(color)
            band.setAlpha(26)
            p.fillRect(QRectF(xa, selection.top(), xb - xa, selection.height()), band)
            p.fillRect(QRectF(xa, selection.bottom() - 3, xb - xa, 3), color)
            label = QRectF(xa + 4, selection.top() + 4, 22, 16)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(label, 4, 4)
            p.setPen(QColor("#111215"))
            p.drawText(label, Qt.AlignCenter, str(n))
        for m in inside:
            x = self._x(m)
            p.setPen(QPen(QColor(theme.ACCENT_HOVER), 1.5, Qt.DashLine))
            p.drawLine(QPointF(x, selection.top()), QPointF(x, selection.bottom()))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.ACCENT_HOVER))
            p.drawPolygon(QPolygonF([QPointF(x - 5, selection.bottom()),
                                     QPointF(x + 5, selection.bottom()),
                                     QPointF(x, selection.bottom() - 7)]))

    def _paint_empty(self, p: QPainter, bg: QRectF) -> None:
        box = bg.adjusted(18, 18, -18, -18)
        p.setPen(QPen(QColor(theme.SURFACE_3), 1.5, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(box, 10, 10)
        title = QFont(self.font())
        title.setPixelSize(15)
        title.setWeight(QFont.DemiBold)
        p.setFont(title)
        p.setPen(QColor(theme.TEXT))
        p.drawText(box.adjusted(0, -12, 0, -12), Qt.AlignCenter, "Drop .Y3S files here")
        sub = QFont(self.font())
        sub.setPixelSize(12)
        p.setFont(sub)
        p.setPen(QColor(theme.MUTED))
        p.drawText(box.adjusted(0, 16, 0, 16), Qt.AlignCenter,
                   "Export a sound from the SEQTRAK app, then drag it in")

    def _paint_ruler(self, p: QPainter, area: QRectF) -> None:
        seconds = self._frames / self._rate
        step = next(s for s in (0.5, 1, 2, 5, 10, 30) if area.width() / max(seconds / s, 1) >= 60)
        font = QFont(self.font())
        font.setPixelSize(10)
        p.setFont(font)
        y = area.bottom() + 6
        t = 0.0
        while t <= seconds:
            x = self._x(int(t * self._rate))
            p.setPen(QPen(QColor(theme.BORDER), 1))
            p.drawLine(QPointF(x, area.bottom() + 2), QPointF(x, area.bottom() + 6))
            p.setPen(QColor(theme.FAINT))
            p.drawText(QRectF(x - 30, y, 60, 14), Qt.AlignCenter, f"{t:g}s")
            t += step

    def _paint_handle(self, p: QPainter, x: float, selection: QRectF, which: str) -> None:
        active = which in (self._hover, self._dragging)
        color = QColor(theme.ACCENT_HOVER if active else theme.ACCENT)
        p.setPen(QPen(color, 2))
        p.drawLine(QPointF(x, selection.top()), QPointF(x, selection.bottom()))
        tab_w, tab_h = 12, 22
        tab = QRectF(x - tab_w / 2, selection.center().y() - tab_h / 2, tab_w, tab_h)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(tab, 4, 4)
        p.setPen(QPen(QColor(0, 0, 0, 110), 1))
        for dy in (-4, 0, 4):
            cy = tab.center().y() + dy
            p.drawLine(QPointF(tab.left() + 3.5, cy), QPointF(tab.right() - 3.5, cy))

    # Handle dragging

    def _handle_at(self, x: float) -> str | None:
        ds, de = abs(x - self._x(self._start)), abs(x - self._x(self._end))
        if min(ds, de) > HANDLE_GRAB_PX:
            return None
        return "start" if ds <= de else "end"

    def _marker_at(self, x: float) -> int | None:
        best = None
        for i, m in enumerate(self._markers):
            d = abs(x - self._x(m))
            if d <= HANDLE_GRAB_PX and (best is None or d < best[0]):
                best = (d, i)
        return None if best is None else best[1]

    def mousePressEvent(self, event):
        if not self._channels:
            return
        x = event.position().x()
        if event.button() == Qt.RightButton and self._slicing:
            i = self._marker_at(x)
            if i is not None:
                del self._markers[i]
                self.update()
                self.markersChanged.emit(self.markers())
            return
        if event.button() != Qt.LeftButton:
            return
        self._dragging = self._handle_at(x)
        if self._dragging is None and self._slicing:
            i = self._marker_at(x)
            if i is None:
                frame = self._frame(x)
                if not self._start < frame < self._end:
                    return
                self._markers.append(frame)
                self._markers.sort()
                i = self._markers.index(frame)
                self.markersChanged.emit(self.markers())
            self._dragging, self._drag_marker = "marker", i
            self.update()

    def mouseMoveEvent(self, event):
        x = event.position().x()
        if self._dragging == "marker":
            frame = min(max(self._frame(x), self._start + 1), self._end - 1)
            self._markers[self._drag_marker] = frame
            self.update()
            return
        if self._dragging is None:
            hover = self._handle_at(x) if self._channels else None
            if hover is None and self._slicing and self._marker_at(x) is not None:
                hover = "marker"
            if hover != self._hover:
                self._hover = hover
                self.setCursor(Qt.SizeHorCursor if hover else Qt.ArrowCursor)
                self.update()
            return
        frame = self._frame(x)
        if self._dragging == "start":
            self._start = min(frame, self._end - 1)
        else:
            self._end = max(frame, self._start + 1)
        self.update()
        self.regionChanged.emit(self._start, self._end)

    def mouseReleaseEvent(self, event):
        if self._dragging == "marker":
            self._markers.sort()
            self.markersChanged.emit(self.markers())
        self._dragging = None
        self._drag_marker = None
        self.update()

    def leaveEvent(self, event):
        if self._hover and self._dragging is None:
            self._hover = None
            self.update()
