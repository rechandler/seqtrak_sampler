"""Dark theme: colors, Qt palette and stylesheet."""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication

BG = "#111215"
SURFACE = "#191a1f"
SURFACE_2 = "#22242b"
SURFACE_3 = "#2b2e36"
BORDER = "#2e3139"
TEXT = "#ecedf0"
MUTED = "#8b909c"
FAINT = "#555a66"
ACCENT = "#ff7a1a"
ACCENT_HOVER = "#ff923f"
ACCENT_PRESSED = "#e66a0f"
OK = "#3ecf8e"
DANGER = "#ff5c5c"
# One colour per slice / pad in Slice mode.
SEGMENT_COLORS = ["#ff7a1a", "#3ecf8e", "#5aa9ff", "#c77dff", "#ff5fa2", "#f4d03f",
                  "#2ed3d3"]

STYLESHEET = f"""
* {{
    font-size: 13px;
    color: {TEXT};
}}
QMainWindow, QDialog, QMessageBox {{
    background: {BG};
}}
QWidget#card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 14px;
}}
QWidget#sidebar {{
    background: transparent;
}}
QLabel#appTitle {{
    font-size: 15px;
    font-weight: 600;
}}
QLabel#appSubtitle, QLabel#muted, QLabel#sectionTitle {{
    color: {MUTED};
}}
QLabel#sectionTitle {{
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1px;
}}
QLabel#versionChip {{
    color: {MUTED};
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 1px 6px;
    font-size: 11px;
}}
QLabel#chip {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 10px;
    color: {MUTED};
}}
QLabel#toast {{
    color: {OK};
}}
QLineEdit {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 10px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus {{
    border-color: {ACCENT};
}}
QLineEdit#nameEdit {{
    background: transparent;
    border: 1px solid transparent;
    font-size: 20px;
    font-weight: 600;
    padding: 4px 6px;
}}
QLineEdit#nameEdit:hover {{
    border-color: {BORDER};
}}
QLineEdit#nameEdit:focus {{
    border-color: {ACCENT};
    background: {SURFACE_2};
}}
QPushButton {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 14px;
    font-weight: 500;
}}
QPushButton:hover {{
    background: {SURFACE_3};
}}
QPushButton:pressed {{
    background: {BORDER};
}}
QPushButton:disabled {{
    color: {FAINT};
    background: {SURFACE};
    border-color: {SURFACE_2};
}}
QPushButton:checked {{
    background: rgba(255, 122, 26, 0.16);
    border-color: {ACCENT};
    color: {ACCENT_HOVER};
}}
QComboBox {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 30px 6px 10px;
    combobox-popup: 0;  /* a scrolling list capped at maxVisibleItems, not full height */
}}
QComboBox:hover {{
    background: {SURFACE_3};
}}
QComboBox:on, QComboBox:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 26px;
    border: none;
    background: transparent;
}}
QComboBox::down-arrow {{
    image: url("{{chevron}}");
    width: 10px;
    height: 10px;
}}
QComboBox QAbstractItemView QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 2px;
}}
QComboBox QAbstractItemView QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 2px;
    min-height: 24px;
}}
QComboBox QAbstractItemView QScrollBar::handle:vertical:hover {{
    background: {FAINT};
}}
QComboBox QAbstractItemView QScrollBar::add-line, QComboBox QAbstractItemView QScrollBar::sub-line,
QComboBox QAbstractItemView QScrollBar::add-page, QComboBox QAbstractItemView QScrollBar::sub-page {{
    height: 0;
    background: none;
}}
QComboBox QAbstractItemView::item {{
    min-height: 24px;
    padding: 0 6px;
}}
QComboBox QAbstractItemView {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px;
    outline: 0;
    selection-background-color: {SURFACE_3};
    selection-color: {TEXT};
}}
QFrame#segmented {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QPushButton#segment {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    color: {MUTED};
    padding: 5px 14px;
}}
QPushButton#segment:hover {{
    color: {TEXT};
}}
QPushButton#segment:checked {{
    background: rgba(255, 122, 26, 0.14);
    border-color: rgba(255, 122, 26, 0.6);
    color: {TEXT};
}}
QLabel#panelTitle {{
    font-size: 20px;
    font-weight: 600;
}}
QLabel#segmentCount {{
    font-size: 16px;
    font-weight: 600;
}}
QPushButton#primary {{
    background: {ACCENT};
    border: none;
    color: #1a0d03;
    font-weight: 600;
    padding: 8px 20px;
}}
QPushButton#primary:hover {{
    background: {ACCENT_HOVER};
}}
QPushButton#primary:pressed {{
    background: {ACCENT_PRESSED};
}}
QPushButton#primary:disabled {{
    background: {SURFACE_2};
    color: {FAINT};
}}
QPushButton#watching {{
    background: rgba(62, 207, 142, 0.10);
    border: 1px solid rgba(62, 207, 142, 0.45);
    color: {OK};
}}
QPushButton#watching:hover {{
    background: rgba(62, 207, 142, 0.18);
}}
QMenu {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 18px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background: {SURFACE_3};
}}
QPushButton#ghost {{
    background: transparent;
    border: 1px solid transparent;
    color: {MUTED};
}}
QPushButton#ghost:hover {{
    background: {SURFACE_2};
    color: {TEXT};
}}
QPushButton#ghost:disabled {{
    color: {FAINT};
    background: transparent;
}}
QPushButton#play {{
    background: {ACCENT};
    border: none;
    border-radius: 20px;
    padding: 0;
}}
QPushButton#play:hover {{
    background: {ACCENT_HOVER};
}}
QPushButton#play:disabled {{
    background: {SURFACE_2};
}}
QListWidget {{
    background: transparent;
    border: none;
    outline: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {SURFACE_3};
    border-radius: 3px;
    min-height: 24px;
}}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
    height: 0;
}}
QToolTip {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    color: {TEXT};
    padding: 4px 8px;
}}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    for role, color in (
        (QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, SURFACE),
        (QPalette.AlternateBase, SURFACE_2), (QPalette.Text, TEXT),
        (QPalette.Button, SURFACE_2), (QPalette.ButtonText, TEXT),
        (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, "#1a0d03"),
        (QPalette.PlaceholderText, FAINT), (QPalette.ToolTipBase, SURFACE_2),
        (QPalette.ToolTipText, TEXT), (QPalette.Mid, BORDER),
    ):
        pal.setColor(role, QColor(color))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(FAINT))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(FAINT))
    app.setPalette(pal)
    app.setStyleSheet(STYLESHEET.replace("{chevron}", _chevron()))


def _chevron() -> str:
    """Draw the combo boxes' down chevron to a PNG (and an @2x one for sharp
    screens) and return its path for the stylesheet. Drawn rather than shipped, so
    it needs no image plugin and no data file in the packaged app."""
    folder = Path(tempfile.gettempdir()) / "seqtrak-sampler"
    folder.mkdir(exist_ok=True)
    for scale, name in ((1, "chevron.png"), (2, "chevron@2x.png")):
        size = 10 * scale
        image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        p = QPainter(image)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(MUTED), 1.6 * scale, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPolyline([QPointF(1.5 * scale, 3.5 * scale), QPointF(5 * scale, 7 * scale),
                        QPointF(8.5 * scale, 3.5 * scale)])
        p.end()
        image.save(str(folder / name))
    return (folder / "chevron.png").as_posix()
