"""Dark theme: colors, Qt palette and stylesheet."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
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
    app.setStyleSheet(STYLESHEET)
