"""Small custom-painted widgets: toggle switch, length meter, list rows, icons."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QMimeData, QPointF, QRectF, QSize, Qt, QUrl
from PySide6.QtGui import QColor, QDrag, QFont, QIcon, QPainter, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QListWidget, QPushButton, QStyle,
    QStyledItemDelegate, QWidget,
)

from . import theme

# Item data roles used by TakeDelegate.
ROLE_SECONDS = Qt.UserRole
ROLE_FITS = Qt.UserRole + 1


def _icon(draw, size: int = 16, color: str = "#1a0d03") -> QIcon:
    pm = QPixmap(size * 2, size * 2)  # 2x for high-DPI screens
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(2, 2)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    draw(p, size)
    p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


def play_icon() -> QIcon:
    return _icon(lambda p, s: p.drawPolygon(QPolygonF(
        [QPointF(s * 0.3, s * 0.18), QPointF(s * 0.84, s * 0.5), QPointF(s * 0.3, s * 0.82)])))


def stop_icon(color: str = "#1a0d03") -> QIcon:
    return _icon(lambda p, s: p.drawRoundedRect(QRectF(s * 0.24, s * 0.24, s * 0.52, s * 0.52),
                                                2, 2), color=color)


class Toggle(QCheckBox):
    """A checkbox drawn as a pill-shaped switch."""

    TRACK_W, TRACK_H = 34, 20

    def sizeHint(self) -> QSize:
        text_w = self.fontMetrics().horizontalAdvance(self.text())
        return QSize(self.TRACK_W + 10 + text_w + 2, 24)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        y = (self.height() - self.TRACK_H) / 2
        track = QRectF(0, y, self.TRACK_W, self.TRACK_H)
        on = self.isChecked()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.ACCENT if on else theme.SURFACE_3))
        p.drawRoundedRect(track, self.TRACK_H / 2, self.TRACK_H / 2)
        knob = self.TRACK_H - 6
        x = track.right() - knob - 3 if on else track.left() + 3
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(x, y + 3, knob, knob))
        p.setPen(QColor(theme.TEXT if self.isEnabled() else theme.FAINT))
        p.drawText(QRectF(self.TRACK_W + 10, 0, self.width(), self.height()),
                   Qt.AlignVCenter | Qt.AlignLeft, self.text())


class LengthMeter(QWidget):
    """Shows the selection length against the SEQTRAK sample limit."""

    def __init__(self, limit: float, parent=None):
        super().__init__(parent)
        self._limit = limit
        self._seconds: float | None = None
        self.setFixedSize(190, 40)

    def set_seconds(self, seconds: float | None) -> None:
        self._seconds = seconds
        self.update()

    def paintEvent(self, event):
        if self._seconds is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fits = self._seconds <= self._limit
        color = QColor(theme.OK if fits else theme.DANGER)
        w = self.width()

        big = QFont(self.font())
        big.setPixelSize(17)
        big.setWeight(QFont.DemiBold)
        p.setFont(big)
        p.setPen(QColor(theme.TEXT if fits else theme.DANGER))
        value = f"{self._seconds:.2f} s"
        p.drawText(QRectF(0, 0, w, 24), Qt.AlignLeft | Qt.AlignVCenter, value)
        offset = p.fontMetrics().horizontalAdvance(value)
        small = QFont(self.font())
        small.setPixelSize(12)
        p.setFont(small)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(offset + 6, 0, w, 24), Qt.AlignLeft | Qt.AlignVCenter,
                   f"of {self._limit:g} s")
        p.setPen(color)
        p.drawText(QRectF(0, 0, w, 24), Qt.AlignRight | Qt.AlignVCenter,
                   "fits" if fits else "too long")

        bar = QRectF(0, 30, w, 6)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.SURFACE_3))
        p.drawRoundedRect(bar, 3, 3)
        filled = QRectF(bar.left(), bar.top(), w * min(1.0, self._seconds / self._limit), 6)
        p.setBrush(color)
        p.drawRoundedRect(filled, 3, 3)


class TakeDelegate(QStyledItemDelegate):
    """List row with the sample name, its length and a status dot."""

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), 52)

    def paint(self, p: QPainter, option, index):
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect).adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        if selected or hovered:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.SURFACE_3 if selected else theme.SURFACE_2))
            p.drawRoundedRect(r, 10, 10)
        if selected:
            p.setBrush(QColor(theme.ACCENT))
            p.drawRoundedRect(QRectF(r.left() + 4, r.top() + 12, 3, r.height() - 24), 1.5, 1.5)

        fits = index.data(ROLE_FITS)
        seconds = index.data(ROLE_SECONDS)
        name_font = QFont(option.font)
        name_font.setPixelSize(13)
        name_font.setWeight(QFont.Medium)
        p.setFont(name_font)
        p.setPen(QColor(theme.TEXT))
        text_rect = QRectF(r.left() + 16, r.top() + 7, r.width() - 40, 20)
        name = p.fontMetrics().elidedText(index.data(Qt.DisplayRole), Qt.ElideRight,
                                          int(text_rect.width()))
        p.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, name)

        sub_font = QFont(option.font)
        sub_font.setPixelSize(11)
        p.setFont(sub_font)
        p.setPen(QColor(theme.MUTED if fits else theme.DANGER))
        sub = f"{seconds:.2f} s" + ("" if fits else "  ·  over 16 s")
        p.drawText(QRectF(r.left() + 16, r.top() + 26, r.width() - 40, 18),
                   Qt.AlignLeft | Qt.AlignVCenter, sub)

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.OK if fits else theme.DANGER))
        p.drawEllipse(QPointF(r.right() - 14, r.center().y()), 3.5, 3.5)
        p.restore()


def start_file_drag(source: QWidget, path: Path | None) -> None:
    """Drag a file out of the app, e.g. into the SEQTRAK app's import."""
    if path is None:
        return
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    drag = QDrag(source)
    drag.setMimeData(mime)
    drag.exec(Qt.CopyAction)


class DragList(QListWidget):
    """A list whose rows can be dragged out as files. `file_for_row` makes the file."""

    def __init__(self, file_for_row: Callable[[int], Path | None], parent=None):
        super().__init__(parent)
        self._file_for_row = file_for_row
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)

    def startDrag(self, supported_actions):
        row = self.currentRow()
        if row >= 0:
            start_file_drag(self, self._file_for_row(row))


class DragButton(QPushButton):
    """A button you drag rather than click; dragging produces `make_file()`."""

    def __init__(self, text: str, make_file: Callable[[], Path | None], parent=None):
        super().__init__(text, parent)
        self._make_file = make_file
        self._press = None
        self.setCursor(Qt.OpenHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press = event.position()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press is not None and (
            (event.position() - self._press).manhattanLength() >= QApplication.startDragDistance()
        ):
            self._press = None
            self.setDown(False)
            start_file_drag(self, self._make_file())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press = None
        super().mouseReleaseEvent(event)


def record_icon(color: str = theme.DANGER) -> QIcon:
    return _icon(lambda p, s: p.drawEllipse(QRectF(s * 0.22, s * 0.22, s * 0.56, s * 0.56)),
                 color=color)


# Item data roles used by PadDelegate.
ROLE_PAD_STATE = Qt.UserRole + 2   # "empty", "new", "in_app"
ROLE_PAD_SECONDS = Qt.UserRole + 3

PAD_COLORS = {"empty": theme.FAINT, "new": theme.ACCENT, "in_app": theme.OK}
PAD_LABELS = {"empty": "empty", "new": "not in app", "in_app": "in app"}


class PadDelegate(QStyledItemDelegate):
    """Compact row for one SEQTRAK sampler pad: number, name, length, status."""

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), 30)

    def paint(self, p: QPainter, option, index):
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect).adjusted(2, 1, -2, -1)
        state = index.data(ROLE_PAD_STATE) or "empty"
        if option.state & (QStyle.State_Selected | QStyle.State_MouseOver) and state != "empty":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.SURFACE_2))
            p.drawRoundedRect(r, 8, 8)

        small = QFont(option.font)
        small.setPixelSize(11)
        small.setWeight(QFont.DemiBold)
        p.setFont(small)
        p.setPen(QColor(theme.FAINT))
        p.drawText(QRectF(r.left() + 8, r.top(), 14, r.height()), Qt.AlignVCenter,
                   str(index.row() + 1))

        name_font = QFont(option.font)
        name_font.setPixelSize(12)
        p.setFont(name_font)
        p.setPen(QColor(theme.FAINT if state == "empty" else theme.TEXT))
        name_rect = QRectF(r.left() + 26, r.top(), r.width() - 100, r.height())
        name = index.data(Qt.DisplayRole) or "—"
        p.drawText(name_rect, Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(name, Qt.ElideRight, int(name_rect.width())))

        if state != "empty":
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(r.right() - 72, r.top(), 46, r.height()),
                       Qt.AlignVCenter | Qt.AlignRight, f"{index.data(ROLE_PAD_SECONDS):.1f} s")
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(PAD_COLORS[state]))
        p.drawEllipse(QPointF(r.right() - 12, r.center().y()), 3.5, 3.5)
        p.restore()


class LevelBar(QWidget):
    """A thin input level meter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._level = 0.0
        self.setFixedHeight(4)

    def set_level(self, level: float) -> None:
        self._level = level
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.SURFACE_3))
        p.drawRoundedRect(QRectF(self.rect()), 2, 2)
        if self._level > 0:
            color = theme.DANGER if self._level > 0.98 else theme.OK
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(0, 0, self.width() * self._level, self.height()), 2, 2)
