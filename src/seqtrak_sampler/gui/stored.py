"""The "Read stored samples" report: what the SEQTRAK keeps in its sampler element
memory, next to what the pads play now. Read-only; it's there to find out how the
SEQTRAK app decides what a pad's "Recorded sound" is."""

from __future__ import annotations

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from .. import sysex
from .device import StoredElement


def _seconds(start: int, end: int) -> str:
    return f"{start / sysex.SAMPLE_RATE:.2f}-{(end + 1) / sysex.SAMPLE_RATE:.2f} s"


def report(elements: list[StoredElement], pads: dict[int, sysex.SamplerPad]) -> str:
    stored = [e for e in elements if e.replied and not e.empty]
    empty = [e for e in elements if e.replied and e.empty]
    silent = [e for e in elements if not e.replied]
    lines = [
        "Stored sampler elements: User 8, programs 72-127, where the SEQTRAK keeps each",
        "project's pad recordings (SEQTRAK Data List, pp. 116-117).",
        "",
    ]
    for e in stored:
        wave = "?" if e.wave is None else e.wave
        level = "?" if e.level is None else e.level
        lines.append(f"{e.label}: {e.name!r}, wave {wave}, {_seconds(e.start, e.end)}, "
                     f"loop {'on' if e.loop_on else 'off'} {e.loop_length}, level {level}, "
                     f"{'on' if e.assigned else 'off'}")
    if not stored:
        lines.append("No stored element with a sample answered.")
    lines.append("")
    lines.append(f"Empty: {len(empty)}. No reply: {len(silent)}"
                 + (f" ({', '.join(f'#{e.number}' for e in silent[:12])}"
                    f"{', ...' if len(silent) > 12 else ''})" if silent else "") + ".")
    lines += ["", "Pads now:"]
    by_wave: dict[int, list[StoredElement]] = {}
    for e in stored:
        if e.wave:
            by_wave.setdefault(e.wave, []).append(e)
    for i in range(sysex.SAMPLER_PADS):
        pad = pads.get(i)
        if pad is None:
            lines.append(f"Pad {i + 1}: not read")
            continue
        if pad.empty:
            lines.append(f"Pad {i + 1}: empty")
            continue
        level = pad.osc[0x2E] if pad.osc and len(pad.osc) > 0x2E else "?"
        same = by_wave.get(pad.wave or -1, [])
        where = ("; same recording as " + ", ".join(f"{e.label} {e.name!r}" for e in same)
                 if same else "; recording not found among stored elements")
        lines.append(f"Pad {i + 1}: {pad.name!r}, wave {pad.wave}, "
                     f"{_seconds(pad.start, pad.end)}, level {level}{where}")
    return "\n".join(lines)


class StoredDialog(QDialog):
    def __init__(self, parent, text: str):
        super().__init__(parent)
        self.setWindowTitle("Stored samples on the SEQTRAK")
        self.resize(760, 520)
        intro = QLabel("This only reads from the SEQTRAK. Click Copy and paste it in a "
                       "message; the full replies are in Copy MIDI log too.")
        intro.setWordWrap(True)
        self.text = QPlainTextEdit(text)
        self.text.setReadOnly(True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        copy = QPushButton("Copy")
        copy.setObjectName("primary")
        copy.clicked.connect(self._copy)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(close)
        buttons.addWidget(copy)
        col = QVBoxLayout(self)
        col.addWidget(intro)
        col.addWidget(self.text, 1)
        col.addLayout(buttons)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.text.toPlainText())
        self.setWindowTitle("Copied")
