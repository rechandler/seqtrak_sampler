"""Small custom-painted widgets: toggle switch, length meter, list rows, icons."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap, QPolygonF
from PySide6.QtWidgets import QCheckBox, QStyle, QStyledItemDelegate, QWidget

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


def stop_icon() -> QIcon:
    return _icon(lambda p, s: p.drawRoundedRect(QRectF(s * 0.24, s * 0.24, s * 0.52, s * 0.52),
                                                2, 2))


class Toggle(QCheckBox):
    """A checkbox drawn as a pill-shaped switch."""

    TRACK_W, TRACK_H = 34, 20

    def sizeHint(self) -> QSize:
        text_w = self.fontMetrics().horizontalAdvance(self.text())
        return QSize(self.TRACK_W + 10 + text_w, 24)

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
