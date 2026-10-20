"""Desktop app for converting and trimming SEQTRAK sounds. Requires PySide6."""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtWidgets import QApplication

    from .window import MainWindow

    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setApplicationName("SEQTRAK Sampler")
    window = MainWindow()
    window.add_files([Path(a) for a in argv[1:] if a.lower().endswith(".y3s")])
    window.show()
    return app.exec()
