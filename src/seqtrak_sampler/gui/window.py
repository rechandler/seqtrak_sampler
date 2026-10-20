"""Main window: load .Y3S files, trim them by eye and ear, export WAVs."""

from __future__ import annotations

import re
from array import array
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSplitter,
    QVBoxLayout, QWidget,
)

from .. import audio
from ..y3s import read_y3s
from .player import Player
from .waveform import WaveformView

THRESHOLD = audio.dbfs_to_amplitude(-60.0)
PAD_MS = 5.0
NORMALIZE_DB = -1.0
DEFAULT_OUTPUT = Path.home() / "Music" / "SEQTRAK Samples"


@dataclass
class Take:
    path: Path
    name: str
    left: array
    right: array
    rate: int
    start: int
    end: int

    @property
    def seconds(self) -> float:
        return (self.end - self.start) / self.rate

    @property
    def fits(self) -> bool:
        return self.end - self.start <= int(audio.MAX_SECONDS * self.rate)

    def auto_trim(self) -> None:
        pad = int(self.rate * PAD_MS / 1000)
        start, end = audio.silence_bounds(self.left, self.right, THRESHOLD, pad)
        self.start, self.end = (start, end) if start < end else (0, len(self.left))


def load_take(path: Path) -> Take:
    sound = read_y3s(path)
    left, right = audio.to_stereo([kb.frames for kb in sound.keybanks])
    take = Take(path, path.stem, left, right, sound.sample_rate, 0, len(left))
    take.auto_trim()
    return take


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w\- .]", "_", name).strip(" .")
    return cleaned or "sample"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SEQTRAK Sampler")
        self.setAcceptDrops(True)
        self.resize(900, 460)
        self.settings = QSettings("seqtrak_sampler", "SEQTRAK Sampler")
        self.takes: list[Take] = []
        self.player = Player(self)

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show_take)
        add_btn = QPushButton("Add files…")
        add_btn.clicked.connect(self._add_dialog)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.clicked.connect(self._remove_current)
        left_col = QVBoxLayout()
        left_col.addWidget(self.list)
        row = QHBoxLayout()
        row.addWidget(add_btn)
        row.addWidget(self.remove_btn)
        left_col.addLayout(row)
        left_panel = QWidget()
        left_panel.setLayout(left_col)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Sample name")
        self.name_edit.textEdited.connect(self._rename)
        self.wave = WaveformView()
        self.wave.regionChanged.connect(self._region_changed)
        self.player.position.connect(self.wave.set_playhead)
        self.player.stopped.connect(lambda: self.play_btn.setText("▶ Play"))
        self.length_label = QLabel()
        self.trim_btn = QPushButton("Auto-trim")
        self.trim_btn.clicked.connect(self._auto_trim)
        self.cut_btn = QPushButton(f"Cut to {audio.MAX_SECONDS:g} s")
        self.cut_btn.clicked.connect(self._cut_to_limit)
        self.play_btn = QPushButton("▶ Play")
        self.play_btn.clicked.connect(self._toggle_play)
        QShortcut(QKeySequence(Qt.Key_Space), self, self._toggle_play)
        self.normalize_box = QCheckBox(f"Normalize ({NORMALIZE_DB:g} dB)")
        self.normalize_box.setChecked(self.settings.value("normalize", True, type=bool))
        self.normalize_box.toggled.connect(lambda on: self.settings.setValue("normalize", on))

        controls = QHBoxLayout()
        for w in (self.play_btn, self.trim_btn, self.cut_btn, self.normalize_box):
            controls.addWidget(w)
        controls.addStretch()
        controls.addWidget(self.length_label)
        right_col = QVBoxLayout()
        right_col.addWidget(self.name_edit)
        right_col.addWidget(self.wave, 1)
        right_col.addLayout(controls)
        right_panel = QWidget()
        right_panel.setLayout(right_col)

        splitter = QSplitter()
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([200, 700])

        self.output_label = QLabel()
        change_btn = QPushButton("Change…")
        change_btn.clicked.connect(self._choose_output)
        show_btn = QPushButton("Show")
        show_btn.clicked.connect(self._show_output)
        self.export_btn = QPushButton("Export")
        self.export_btn.setDefault(True)
        self.export_btn.clicked.connect(self._export_current)
        self.export_all_btn = QPushButton("Export all")
        self.export_all_btn.clicked.connect(self._export_all)
        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Output:"))
        bottom.addWidget(self.output_label, 1)
        for w in (change_btn, show_btn, self.export_btn, self.export_all_btn):
            bottom.addWidget(w)

        root = QVBoxLayout()
        root.addWidget(splitter, 1)
        root.addLayout(bottom)
        central = QWidget()
        central.setLayout(root)
        self.setCentralWidget(central)
        self._update_output_label()
        self._show_take(-1)

    # Loading

    def add_files(self, paths: list[Path]) -> None:
        errors = []
        first_new = len(self.takes)
        for path in paths:
            try:
                take = load_take(path)
            except (OSError, ValueError) as e:
                errors.append(f"{path.name}: {e}")
                continue
            self.takes.append(take)
            self.list.addItem(QListWidgetItem(take.name))
        if len(self.takes) > first_new:
            self.list.setCurrentRow(first_new)
        if errors:
            QMessageBox.warning(self, "Some files could not be opened", "\n".join(errors))

    def _add_dialog(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add SEQTRAK sounds", self.settings.value("last_dir", str(Path.home())),
            "SEQTRAK sounds (*.y3s *.Y3S);;All files (*)")
        if files:
            self.settings.setValue("last_dir", str(Path(files[0]).parent))
            self.add_files([Path(f) for f in files])

    def dragEnterEvent(self, event):
        if any(u.toLocalFile().lower().endswith(".y3s") for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls()
                 if u.toLocalFile().lower().endswith(".y3s")]
        self.add_files(paths)

    def _remove_current(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            self.player.stop()
            del self.takes[row]
            self.list.takeItem(row)

    # Current take

    def current(self) -> Take | None:
        row = self.list.currentRow()
        return self.takes[row] if 0 <= row < len(self.takes) else None

    def _show_take(self, row: int) -> None:
        self.player.stop()
        take = self.current()
        for w in (self.name_edit, self.trim_btn, self.play_btn, self.remove_btn,
                  self.export_btn):
            w.setEnabled(take is not None)
        self.export_all_btn.setEnabled(bool(self.takes))
        if take is None:
            self.name_edit.clear()
            self.wave.clear()
            self.length_label.clear()
            self.cut_btn.setEnabled(False)
            return
        self.name_edit.setText(take.name)
        self.wave.set_sound(take.left, take.right, take.rate, take.start, take.end)
        self._update_length()

    def _update_length(self) -> None:
        take = self.current()
        if take is None:
            return
        limit = f"{take.seconds:.2f} s / {audio.MAX_SECONDS:.2f} s"
        if take.fits:
            self.length_label.setText(f"{limit}  ✓ fits")
            self.length_label.setStyleSheet("color: #2a9d55;")
        else:
            self.length_label.setText(f"{limit}  ✗ too long")
            self.length_label.setStyleSheet("color: #d03b3b; font-weight: bold;")
        self.cut_btn.setEnabled(not take.fits)
        self.export_btn.setEnabled(take.fits)

    def _rename(self, text: str) -> None:
        take = self.current()
        if take is not None:
            take.name = text
            self.list.currentItem().setText(text or take.path.stem)

    def _region_changed(self, start: int, end: int) -> None:
        take = self.current()
        if take is not None:
            take.start, take.end = start, end
            self._update_length()

    def _auto_trim(self) -> None:
        take = self.current()
        if take is not None:
            take.auto_trim()
            self.wave.set_region(take.start, take.end)
            self._update_length()

    def _cut_to_limit(self) -> None:
        take = self.current()
        if take is not None:
            take.end = take.start + int(audio.MAX_SECONDS * take.rate)
            self.wave.set_region(take.start, take.end)
            self._update_length()

    def _render(self, take: Take, enforce_limit: bool = True) -> tuple[array, array]:
        start, end = take.start, take.end
        if not enforce_limit:
            end = min(end, start + int(audio.MAX_SECONDS * take.rate))
        norm = NORMALIZE_DB if self.normalize_box.isChecked() else None
        return audio.render_region(take.left, take.right, start, end, take.rate,
                                   normalize_db=norm, threshold=THRESHOLD)

    def _toggle_play(self) -> None:
        if self.player.playing:
            self.player.stop()
            return
        take = self.current()
        if take is None:
            return
        left, right = self._render(take, enforce_limit=False)
        try:
            self.player.play(left, right, take.rate, take.start)
        except RuntimeError as e:
            QMessageBox.warning(self, "Can't play", str(e))
            return
        self.play_btn.setText("■ Stop")

    # Export

    def output_dir(self) -> Path:
        return Path(self.settings.value("output_dir", str(DEFAULT_OUTPUT)))

    def _update_output_label(self) -> None:
        self.output_label.setText(str(self.output_dir()))

    def _choose_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Output folder", str(self.output_dir()))
        if folder:
            self.settings.setValue("output_dir", folder)
            self._update_output_label()

    def _show_output(self) -> None:
        self.output_dir().mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_dir())))

    def export_take(self, take: Take, ask_overwrite: bool = True) -> Path | None:
        """Write the take to the output folder. Returns the path, or None if skipped."""
        folder = self.output_dir()
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / (safe_filename(take.name or take.path.stem) + ".wav")
        if dest.exists() and ask_overwrite:
            answer = QMessageBox.question(self, "Replace file?",
                                          f"{dest.name} already exists. Replace it?")
            if answer != QMessageBox.Yes:
                return None
        left, right = self._render(take)
        audio.write_wav(dest, left, right, take.rate)
        return dest

    def _export_current(self) -> None:
        take = self.current()
        if take is None:
            return
        try:
            dest = self.export_take(take)
        except (OSError, ValueError) as e:
            QMessageBox.critical(self, "Export failed", str(e))
            return
        if dest is not None:
            self.statusBar().showMessage(f"Exported {dest}", 5000)

    def _export_all(self) -> None:
        done, skipped = 0, []
        for take in self.takes:
            if not take.fits:
                skipped.append(f"{take.name}: {take.seconds:.2f} s is over the "
                               f"{audio.MAX_SECONDS:g} s limit")
                continue
            try:
                if self.export_take(take):
                    done += 1
            except (OSError, ValueError) as e:
                skipped.append(f"{take.name}: {e}")
        self.statusBar().showMessage(f"Exported {done} of {len(self.takes)} to "
                                     f"{self.output_dir()}", 5000)
        if skipped:
            QMessageBox.warning(self, "Some samples were not exported", "\n".join(skipped))
