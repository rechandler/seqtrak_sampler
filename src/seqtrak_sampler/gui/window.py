"""Main window: load .Y3S files, trim them by eye and ear, export WAVs."""

from __future__ import annotations

import tempfile
import time
from array import array
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from .. import __version__, audio, sysex
from ..files import FolderScanner, safe_filename, unique_path
from ..y3s import read_y3s
from . import device as dev
from . import theme
from .device import Device
from .player import Player
from .recorder import Recorder
from .waveform import WaveformView
from .widgets import (
    ROLE_FITS, ROLE_PAD_SECONDS, ROLE_PAD_STATE, ROLE_SECONDS, DragButton, DragList,
    LengthMeter, LevelBar, PadDelegate, TakeDelegate, Toggle, play_icon, record_icon,
    stop_icon,
)

THRESHOLD = audio.dbfs_to_amplitude(-60.0)
PAD_MS = 5.0
NORMALIZE_DB = -1.0
DEFAULT_OUTPUT = Path.home() / "Music" / "SEQTRAK Samples"
WATCH_INTERVAL_MS = 1000
AUTO_SAVE_DELAY_MS = 700
DRAG_DIR = Path(tempfile.gettempdir()) / "seqtrak-sampler-drag"
CAPTURE_LEAD_MS = 150     # recording runs this long before the pad is played
CAPTURE_TAIL_MS = 400     # and this long after the note is released
ONSET = audio.dbfs_to_amplitude(-50.0)


@dataclass
class Take:
    path: Path | None  # the .Y3S it came from; None for recordings
    name: str
    left: array
    right: array
    rate: int
    start: int
    end: int
    saved_path: Path | None = None  # the WAV we last wrote for this take
    device_name: str = ""  # the sample's name on the SEQTRAK, if it came from a pad
    # Set when the take was captured by playing a pad: the pad it came from, the pad's
    # frame at index 0 of this audio, and the furthest end point it may be given.
    pad_index: int | None = None
    device_offset: int = 0
    device_max_end: int | None = None

    @property
    def label(self) -> str:
        return self.name or (self.path.stem if self.path else "Recording")

    @property
    def last_frame(self) -> int:
        return len(self.left) - 1

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
    take = Take(path, path.stem, left, right, sound.sample_rate, 0, len(left),
                device_name=sound.name)
    take.auto_trim()
    return take


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"SEQTRAK Sampler {__version__}")
        self.setAcceptDrops(True)
        self.resize(1140, 700)
        self.setMinimumSize(1000, 560)
        self.settings = QSettings("seqtrak_sampler", "SEQTRAK Sampler")
        self.takes: list[Take] = []
        self.player = Player(self)
        self._scanner: FolderScanner | None = None
        self._watch_timer = QTimer(self)
        self._watch_timer.setInterval(WATCH_INTERVAL_MS)
        self._watch_timer.timeout.connect(self._poll_watch_folder)
        self._dirty: list[Take] = []
        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.setSingleShot(True)
        self._auto_save_timer.timeout.connect(self._flush_auto_save)
        self.device = Device(self)
        self.recorder = Recorder(self)
        self._capture: sysex.SamplerPad | None = None  # pad being opened, if any

        # Header
        logo = QLabel()
        logo.setFixedSize(12, 12)
        logo.setStyleSheet(f"background: {theme.ACCENT}; border-radius: 6px;")
        title = QLabel("SEQTRAK Sampler")
        title.setObjectName("appTitle")
        version = QLabel(f"v{__version__}")
        version.setObjectName("versionChip")
        version.setToolTip(f"SEQTRAK Sampler version {__version__}")
        subtitle = QLabel("Turn SEQTRAK resamples into synth-ready WAVs")
        subtitle.setObjectName("appSubtitle")
        add_btn = QPushButton("+  Add files")
        add_btn.clicked.connect(self._add_dialog)
        self.watch_btn = QPushButton()
        self.watch_btn.clicked.connect(self._watch_clicked)
        header = QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(logo)
        header.addWidget(title)
        header.addWidget(version)
        header.addSpacing(6)
        header.addWidget(subtitle)
        header.addStretch()
        header.addWidget(self.watch_btn)
        header.addWidget(add_btn)

        # Sidebar: the list of loaded sounds
        samples_title = QLabel("SAMPLES")
        samples_title.setObjectName("sectionTitle")
        self.list = DragList(lambda row: self._drag_file(self.takes[row]))
        self.list.setToolTip("Drag a sample into the SEQTRAK app to import it")
        self.list.setItemDelegate(TakeDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.currentRowChanged.connect(self._show_take)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.setObjectName("ghost")
        self.remove_btn.clicked.connect(self._remove_current)
        # SEQTRAK panel: connection, recording, sampler pads
        device_title = QLabel("SEQTRAK")
        device_title.setObjectName("sectionTitle")
        self.device_dot = QLabel()
        self.device_dot.setFixedSize(8, 8)
        self.device_label = QLabel()
        self.device_label.setObjectName("muted")
        self.device_label.setWordWrap(True)
        self.enable_btn = QPushButton("Enable")
        self.enable_btn.setToolTip("Turn on the SEQTRAK's USB MIDI SysEx transmit setting "
                                   "so this app can read its sampler pads")
        self.enable_btn.clicked.connect(self.device.enable_sysex)
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_row.addWidget(self.device_dot)
        status_row.addWidget(self.device_label, 1)
        status_row.addWidget(self.enable_btn)

        self.rec_btn = QPushButton("  Record")
        self.rec_btn.setIcon(record_icon())
        self.rec_btn.setToolTip("Record the SEQTRAK's USB audio")
        self.rec_btn.clicked.connect(self._toggle_record)
        self.follow_box = Toggle("Rec on Play")
        self.follow_box.setToolTip("Start recording when you press Play on the SEQTRAK, "
                                   "and stop when you press Stop")
        self.follow_box.toggled.connect(self._follow_toggled)
        rec_row = QHBoxLayout()
        rec_row.addWidget(self.rec_btn)
        rec_row.addStretch()
        rec_row.addWidget(self.follow_box)
        self.rec_status = QLabel()
        self.rec_status.setObjectName("muted")
        self.level_bar = LevelBar()

        self.pads_list = QListWidget()
        self.pads_list.setItemDelegate(PadDelegate(self.pads_list))
        self.pads_list.setMouseTracking(True)
        self.pads_list.setFixedHeight(30 * sysex.SAMPLER_PADS + 4)
        self.pads_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.pads_list.itemClicked.connect(self._pad_clicked)
        for _ in range(sysex.SAMPLER_PADS):
            self.pads_list.addItem(QListWidgetItem())
        self._pads: dict[int, sysex.SamplerPad] = {}

        sidebar_col = QVBoxLayout()
        sidebar_col.setContentsMargins(0, 4, 0, 0)
        sidebar_col.setSpacing(8)
        sidebar_col.addWidget(device_title)
        sidebar_col.addLayout(status_row)
        sidebar_col.addLayout(rec_row)
        sidebar_col.addWidget(self.rec_status)
        sidebar_col.addWidget(self.level_bar)
        sidebar_col.addWidget(self.pads_list)
        self.log_btn = QPushButton("Copy MIDI log")
        self.log_btn.setObjectName("ghost")
        self.log_btn.setToolTip("Copy the recent MIDI messages to the clipboard, to report a "
                                "problem")
        self.log_btn.clicked.connect(self._copy_midi_log)
        sidebar_col.addWidget(self.log_btn, 0, Qt.AlignLeft)
        sidebar_col.addSpacing(10)
        sidebar_col.addWidget(samples_title)
        sidebar_col.addWidget(self.list, 1)
        sidebar_col.addWidget(self.remove_btn, 0, Qt.AlignLeft)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(250)
        sidebar.setLayout(sidebar_col)

        # Editor card
        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("nameEdit")
        self.name_edit.setPlaceholderText("Sample name")
        self.name_edit.textEdited.connect(self._rename)
        self.wave = WaveformView()
        self.wave.regionChanged.connect(self._region_changed)
        self.player.position.connect(self.wave.set_playhead)
        self.player.stopped.connect(lambda: self.play_btn.setIcon(play_icon()))
        self.play_btn = QPushButton()
        self.play_btn.setObjectName("play")
        self.play_btn.setFixedSize(40, 40)
        self.play_btn.setIcon(play_icon())
        self.play_btn.setToolTip("Play selection (Space)")
        self.play_btn.clicked.connect(self._toggle_play)
        QShortcut(QKeySequence(Qt.Key_Space), self, self._toggle_play)
        self.trim_btn = QPushButton("Auto-trim")
        self.trim_btn.setToolTip("Trim leading and trailing silence")
        self.trim_btn.clicked.connect(self._auto_trim)
        self.cut_btn = QPushButton(f"Cut to {audio.MAX_SECONDS:g} s")
        self.cut_btn.clicked.connect(self._cut_to_limit)
        self.normalize_box = Toggle(f"Normalize to {NORMALIZE_DB:g} dB")
        self.normalize_box.setChecked(self.settings.value("normalize", True, type=bool))
        self.normalize_box.toggled.connect(lambda on: self.settings.setValue("normalize", on))
        self.normalize_box.toggled.connect(lambda _: self._mark_dirty(*self.takes))
        self.meter = LengthMeter(audio.MAX_SECONDS)
        self.drag_btn = DragButton("⠿  Drag WAV",
                                   lambda: self._drag_file(self.current()))
        self.drag_btn.setToolTip("Drag this sample straight into the SEQTRAK app")
        self.send_btn = QPushButton("Apply to pad")
        self.send_btn.clicked.connect(self._send_trim)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(self.play_btn)
        controls.addSpacing(6)
        controls.addWidget(self.trim_btn)
        controls.addWidget(self.cut_btn)
        controls.addSpacing(14)
        controls.addWidget(self.normalize_box)
        controls.addStretch()

        controls.addWidget(self.meter)
        card_col = QVBoxLayout()
        card_col.setContentsMargins(20, 16, 20, 18)
        card_col.setSpacing(10)
        name_row = QHBoxLayout()
        name_row.addWidget(self.name_edit, 1)
        name_row.addWidget(self.send_btn)
        name_row.addWidget(self.drag_btn)
        card_col.addLayout(name_row)
        card_col.addWidget(self.wave, 1)
        card_col.addLayout(controls)
        card = QWidget()
        card.setObjectName("card")
        card.setAttribute(Qt.WA_StyledBackground)
        card.setLayout(card_col)

        body = QHBoxLayout()
        body.setSpacing(16)
        body.addWidget(sidebar)
        body.addWidget(card, 1)

        # Footer: output folder and export
        save_to = QLabel("Save to")
        save_to.setObjectName("muted")
        self.output_label = QLabel()
        self.output_label.setObjectName("chip")
        self.output_label.setMinimumWidth(120)
        change_btn = QPushButton("Change…")
        change_btn.setObjectName("ghost")
        change_btn.clicked.connect(self._choose_output)
        show_btn = QPushButton("Show")
        show_btn.setObjectName("ghost")
        show_btn.clicked.connect(self._show_output)
        self.toast = QLabel()
        self.toast.setObjectName("toast")
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast.clear)
        self.auto_save_box = Toggle("Auto-save")
        self.auto_save_box.setToolTip("Save every sample to the output folder as soon as it "
                                      "loads or changes")
        self.auto_save_box.setChecked(self.settings.value("auto_save", False, type=bool))
        self.auto_save_box.toggled.connect(self._auto_save_toggled)
        self.export_all_btn = QPushButton("Export all")
        self.export_all_btn.clicked.connect(self._export_all)
        self.export_btn = QPushButton("Export WAV")
        self.export_btn.setToolTip("Save as a 44.1 kHz, 16-bit stereo WAV")
        self.export_btn.setObjectName("primary")
        self.export_btn.setDefault(True)
        self.export_btn.clicked.connect(self._export_current)
        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addWidget(save_to)
        footer.addWidget(self.output_label)
        footer.addWidget(change_btn)
        footer.addWidget(show_btn)
        footer.addStretch()
        footer.addWidget(self.toast)
        footer.addSpacing(8)
        footer.addWidget(self.auto_save_box)
        footer.addSpacing(6)
        footer.addWidget(self.export_all_btn)
        footer.addWidget(self.export_btn)

        root = QVBoxLayout()
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(14)
        root.addLayout(header)
        root.addLayout(body, 1)
        root.addLayout(footer)
        central = QWidget()
        central.setLayout(root)
        self.setCentralWidget(central)
        self._update_output_label()
        self._show_take(-1)
        watch = self.settings.value("watch_dir", "")
        self._set_watch_folder(Path(watch) if watch and Path(watch).is_dir() else None)

        self.device.status.connect(self._device_status)
        self.device.padsChanged.connect(self._pads_changed)
        self.device.transportStarted.connect(self._transport_started)
        self.device.transportStopped.connect(self._transport_stopped)
        self.recorder.stateChanged.connect(self._recorder_state)
        self.recorder.elapsed.connect(
            lambda s: self.rec_status.setText(f"●  Recording  {s:.1f} s"))
        self.recorder.level.connect(self.level_bar.set_level)
        self.recorder.takeReady.connect(self._recorded)
        self.recorder.error.connect(self._recorder_error)
        self._device_status(self.device.state, "")
        self._recorder_state(self.recorder.state)
        self.device.start()
        if self.settings.value("rec_follow", False, type=bool):
            self.follow_box.setChecked(True)

    def closeEvent(self, event):
        self.device.stop()
        self.recorder.set_armed(False)
        super().closeEvent(event)

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
            self._append_take(take, select=False)
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

    def _append_take(self, take: Take, select: bool = True) -> None:
        self.takes.append(take)
        item = QListWidgetItem(take.label)
        self.list.addItem(item)
        self._update_item(item, take)
        if select:
            self.list.setCurrentRow(len(self.takes) - 1)
        self._mark_dirty(take)
        self._refresh_pads()

    # Watch folder

    def _set_watch_folder(self, folder: Path | None) -> None:
        self._scanner = FolderScanner(folder) if folder else None
        self.settings.setValue("watch_dir", str(folder) if folder else "")
        if folder:
            self._watch_timer.start()
            self.watch_btn.setText(f"●  Watching {folder.name or folder}")
            self.watch_btn.setToolTip(f"New .Y3S files saved to {folder} load automatically")
            self.watch_btn.setObjectName("watching")
        else:
            self._watch_timer.stop()
            self.watch_btn.setText("Watch folder…")
            self.watch_btn.setToolTip("Pick the folder you export .Y3S files to; new ones "
                                      "load automatically")
            self.watch_btn.setObjectName("")
        self.watch_btn.style().unpolish(self.watch_btn)
        self.watch_btn.style().polish(self.watch_btn)

    def _choose_watch_folder(self) -> None:
        start = self.settings.value("watch_dir", "") or str(Path.home() / "Downloads")
        folder = QFileDialog.getExistingDirectory(self, "Watch folder for SEQTRAK exports", start)
        if folder:
            self._set_watch_folder(Path(folder))
            self._show_toast(f"Watching {Path(folder).name} for new .Y3S files")

    def _watch_clicked(self) -> None:
        if self._scanner is None:
            self._choose_watch_folder()
            return
        menu = QMenu(self)
        menu.addAction("Open folder", lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(self._scanner.folder))))
        menu.addAction("Change folder…", self._choose_watch_folder)
        menu.addAction("Stop watching", lambda: self._set_watch_folder(None))
        menu.exec(self.watch_btn.mapToGlobal(self.watch_btn.rect().bottomLeft()))

    def _poll_watch_folder(self) -> None:
        if self._scanner is None:
            return
        for path in self._scanner.poll():
            existing = next((t for t in self.takes if t.path == path), None)
            try:
                fresh = load_take(path)
            except (OSError, ValueError) as e:
                self._show_toast(f"Couldn't open {path.name}: {e}", error=True)
                continue
            if existing is None:
                self._append_take(fresh)
                self._show_toast(f"Loaded {path.name}")
            else:
                # Re-exported under the same name: take the new audio, keep the name.
                existing.left, existing.right, existing.rate = fresh.left, fresh.right, fresh.rate
                existing.start, existing.end = fresh.start, fresh.end
                existing.device_name = fresh.device_name
                row = self.takes.index(existing)
                self._update_item(self.list.item(row), existing)
                if row == self.list.currentRow():
                    self._show_take(row)
                self._mark_dirty(existing)
                self._show_toast(f"Reloaded {path.name}")

    def _remove_current(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            self.player.stop()
            take = self.takes.pop(row)
            if take in self._dirty:
                self._dirty.remove(take)
            self._refresh_pads()
            self.list.takeItem(row)

    # Current take

    def current(self) -> Take | None:
        row = self.list.currentRow()
        return self.takes[row] if 0 <= row < len(self.takes) else None

    def _show_take(self, row: int) -> None:
        self.player.stop()
        take = self.current()
        for w in (self.name_edit, self.trim_btn, self.play_btn, self.remove_btn,
                  self.export_btn, self.drag_btn):
            w.setEnabled(take is not None)
        self.export_all_btn.setEnabled(bool(self.takes))
        if take is None:
            self.name_edit.clear()
            self.wave.clear()
            self.meter.set_seconds(None)
            self.cut_btn.setEnabled(False)
            self._update_send_button()
            return
        self.name_edit.setText(take.name)
        self.wave.set_sound(take.left, take.right, take.rate, take.start, take.end)
        self._update_length()

    def _update_length(self) -> None:
        take = self.current()
        if take is None:
            return
        self.meter.set_seconds(take.seconds)
        self._update_item(self.list.currentItem(), take)
        self.cut_btn.setEnabled(not take.fits)
        self.export_btn.setEnabled(take.fits)
        self.drag_btn.setEnabled(take.fits)
        self._update_send_button()

    def _rename(self, text: str) -> None:
        take = self.current()
        if take is not None:
            take.name = text
            self.list.currentItem().setText(take.label)
            self._refresh_pads()
            self._mark_dirty(take)

    def _region_changed(self, start: int, end: int) -> None:
        take = self.current()
        if take is not None:
            take.start, take.end = start, end
            self._update_length()
            self._mark_dirty(take)

    def _auto_trim(self) -> None:
        take = self.current()
        if take is not None:
            take.auto_trim()
            self.wave.set_region(take.start, take.end)
            self._update_length()
            self._mark_dirty(take)

    def _cut_to_limit(self) -> None:
        take = self.current()
        if take is not None:
            take.end = take.start + int(audio.MAX_SECONDS * take.rate)
            self.wave.set_region(take.start, take.end)
            self._update_length()
            self._mark_dirty(take)

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
        self.play_btn.setIcon(stop_icon())

    # Export

    def output_dir(self) -> Path:
        return Path(self.settings.value("output_dir", str(DEFAULT_OUTPUT)))

    def _update_item(self, item: QListWidgetItem | None, take: Take) -> None:
        if item is not None:
            item.setData(ROLE_SECONDS, take.seconds)
            item.setData(ROLE_FITS, take.fits)

    def _show_toast(self, text: str, error: bool = False) -> None:
        self.toast.setText(text)
        self.toast.setStyleSheet(f"color: {theme.DANGER};" if error else "")
        self._toast_timer.start(5000)

    def _update_output_label(self) -> None:
        folder = self.output_dir()
        try:
            shown = "~/" + folder.relative_to(Path.home()).as_posix()
        except ValueError:
            shown = str(folder)
        self.output_label.setText(shown)
        self.output_label.setToolTip(str(folder))

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
        dest = folder / (safe_filename(take.label) + ".wav")
        if dest.exists() and dest != take.saved_path and ask_overwrite:
            answer = QMessageBox.question(self, "Replace file?",
                                          f"{dest.name} already exists. Replace it?")
            if answer != QMessageBox.Yes:
                return None
        left, right = self._render(take)
        audio.write_wav(dest, left, right, take.rate)
        take.saved_path = dest
        return dest

    # Auto-save: write each take to the output folder shortly after it changes.

    def _auto_save_toggled(self, on: bool) -> None:
        self.settings.setValue("auto_save", on)
        if on:
            self._mark_dirty(*self.takes)

    def _mark_dirty(self, *takes: Take) -> None:
        if not self.auto_save_box.isChecked():
            return
        for take in takes:
            if take not in self._dirty:
                self._dirty.append(take)
        self._auto_save_timer.start(AUTO_SAVE_DELAY_MS)

    def auto_save_take(self, take: Take) -> Path:
        folder = self.output_dir()
        folder.mkdir(parents=True, exist_ok=True)
        stem = safe_filename(take.label)
        dest = unique_path(folder, stem, ".wav", keep=take.saved_path)
        if take.saved_path is not None and take.saved_path.parent == folder:
            # Renamed: reuse the old file name's slot rather than leaving a stale copy.
            if take.saved_path != dest and take.saved_path.exists():
                take.saved_path.unlink()
                dest = unique_path(folder, stem, ".wav")
        left, right = self._render(take)
        audio.write_wav(dest, left, right, take.rate)
        take.saved_path = dest
        return dest

    def _flush_auto_save(self) -> None:
        saved, failed = [], []
        while self._dirty:
            take = self._dirty.pop(0)
            if not take.fits:
                continue
            try:
                saved.append(self.auto_save_take(take))
            except (OSError, ValueError) as e:
                failed.append(f"{take.name}: {e}")
        if failed:
            self._show_toast("Auto-save failed: " + "; ".join(failed), error=True)
        elif len(saved) == 1:
            self._show_toast(f"✓  Auto-saved {saved[0].name}")
        elif saved:
            self._show_toast(f"✓  Auto-saved {len(saved)} samples")

    # Drag out

    def _drag_file(self, take: Take | None) -> Path | None:
        """Write the take somewhere it can be dragged from, and return that path."""
        if take is None:
            return None
        if not take.fits:
            self._show_toast(f"{take.name} is over the {audio.MAX_SECONDS:g} s limit",
                             error=True)
            return None
        try:
            if self.auto_save_box.isChecked():
                if take in self._dirty:
                    self._dirty.remove(take)
                return self.auto_save_take(take)
            DRAG_DIR.mkdir(parents=True, exist_ok=True)
            dest = DRAG_DIR / (safe_filename(take.label) + ".wav")
            left, right = self._render(take)
            audio.write_wav(dest, left, right, take.rate)
            return dest
        except (OSError, ValueError) as e:
            self._show_toast(f"Couldn't prepare {take.name}: {e}", error=True)
            return None

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
            self._show_toast(f"✓  Saved {dest.name}")

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
        self._show_toast(f"✓  Saved {done} of {len(self.takes)}")
        if skipped:
            QMessageBox.warning(self, "Some samples were not exported", "\n".join(skipped))

    # SEQTRAK connection and sampler pads

    def _device_status(self, state: str, detail: str) -> None:
        text, color = {
            dev.UNAVAILABLE: ("MIDI support isn't available in this build", theme.FAINT),
            dev.DISCONNECTED: ("Not connected. Plug in your SEQTRAK over USB.", theme.FAINT),
            dev.BUSY: ("In use by another app. Close the SEQTRAK app to connect.",
                       theme.ACCENT),
            dev.SILENT: ("Connected, but SysEx is off, so pads can't be read.", theme.ACCENT),
            dev.CONNECTED: (f"Connected{'  ·  ' + detail if detail else ''}", theme.OK),
        }[state]
        self.device_label.setText(text)
        self.device_dot.setStyleSheet(f"background: {color}; border-radius: 4px;")
        self.enable_btn.setVisible(state == dev.SILENT)
        self.log_btn.setVisible(state not in (dev.UNAVAILABLE, dev.DISCONNECTED))
        self.pads_list.setVisible(state == dev.CONNECTED)
        self._update_send_button()

    def _copy_midi_log(self) -> None:
        from PySide6.QtWidgets import QApplication

        pads = "; ".join(
            f"pad {p.pad + 1}: {p.name!r} start={p.start} end={p.end} notes={p.note_low}-"
            f"{p.note_high}" for p in self._pads.values()) or "none read"
        header = [f"SEQTRAK Sampler {__version__}", f"state: {self.device.state}",
                  f"pads: {pads}", ""]
        QApplication.clipboard().setText("\n".join(header + list(self.device.log)))
        self._show_toast(f"Copied {len(self.device.log)} MIDI log lines. Paste them in a "
                         f"message.")

    def _pad_state(self, pad: sysex.SamplerPad) -> str:
        if pad.empty:
            return "empty"
        return "in_app" if self._take_for_pad(pad) else "new"

    @staticmethod
    def _matches(take: Take, pad: sysex.SamplerPad) -> bool:
        if pad.empty:
            return False
        if take.pad_index is not None:
            return take.pad_index == pad.pad and take.device_name == pad.name
        return bool(take.device_name) and take.device_name == pad.name \
            and pad.end <= take.last_frame

    def _take_for_pad(self, pad: sysex.SamplerPad) -> Take | None:
        return next((t for t in self.takes if self._matches(t, pad)), None)

    def _pad_for_take(self, take: Take | None) -> sysex.SamplerPad | None:
        if take is None or self.device.state != dev.CONNECTED:
            return None
        return next((p for p in self._pads.values() if self._matches(take, p)), None)

    def _pads_changed(self, pads: list) -> None:
        previous = self._pads
        self._pads = {p.pad: p for p in pads}
        for pad in pads:
            old = previous.get(pad.pad)
            if old is not None and old != pad and not pad.empty \
                    and self._take_for_pad(pad) is None:
                self._show_toast(f"Pad {pad.pad + 1} has a new sample \u201c{pad.name}\u201d. "
                                 f"Click it in the pad list to open it.")
        self._refresh_pads()

    def _refresh_pads(self) -> None:
        for i in range(sysex.SAMPLER_PADS):
            item = self.pads_list.item(i)
            pad = self._pads.get(i)
            if pad is None:
                item.setText("")
                item.setData(ROLE_PAD_STATE, "empty")
                continue
            state = self._pad_state(pad)
            item.setText(pad.name if not pad.empty else "")
            item.setData(ROLE_PAD_STATE, state)
            item.setData(ROLE_PAD_SECONDS, pad.seconds)
            item.setToolTip({
                "empty": f"Pad {i + 1} is empty",
                "new": f"Pad {i + 1}: click to open it here. The SEQTRAK plays it once "
                       f"while the app records it.",
                "in_app": f"Pad {i + 1}: open. Click to edit it.",
            }[state])
        self._update_send_button()

    def _pad_clicked(self, item: QListWidgetItem) -> None:
        row = self.pads_list.row(item)
        pad = self._pads.get(row)
        if pad is None:
            self._show_toast(f"Pad {row + 1} hasn't been read from the SEQTRAK yet. If this "
                             f"keeps happening, click Copy MIDI log and send it.", error=True)
            return
        if pad.empty:
            self._show_toast(f"The SEQTRAK reports pad {row + 1} as empty.", error=True)
            return
        take = self._take_for_pad(pad)
        if take is not None:
            self.list.setCurrentRow(self.takes.index(take))
        else:
            self.open_pad(pad)

    # Opening a pad: play it once and record the SEQTRAK's USB audio.

    def open_pad(self, pad: sysex.SamplerPad) -> None:
        if self._capture is not None:
            return
        if pad.note is None:
            self._show_toast("Still reading the pad's settings. Try again in a moment.",
                             error=True)
            return
        if self.recorder.state == "recording":
            self._show_toast("Stop the current recording first.", error=True)
            return
        self._capture = pad
        self.recorder.start_take()
        if self.recorder.state != "recording":  # no audio input: error already shown
            self._capture = None
            return
        self.player.stop()
        self._show_toast(f"Opening pad {pad.pad + 1}: playing it once\u2026")
        hold = pad.seconds + 0.05
        QTimer.singleShot(CAPTURE_LEAD_MS, lambda: self.device.play_note(pad.note, hold))
        QTimer.singleShot(int(CAPTURE_LEAD_MS + hold * 1000 + CAPTURE_TAIL_MS),
                          self.recorder.stop_take)

    def _captured(self, pad: sysex.SamplerPad, left, right, rate: int) -> None:
        # The pad's start point is where its sound begins in the recording.
        onset = next((i for i in range(len(left))
                      if abs(left[i]) > ONSET or abs(right[i]) > ONSET), None)
        if onset is None:
            self._show_toast(f"Pad {pad.pad + 1} played silence. Is the track muted, or "
                             f"its volume down?", error=True)
            return
        left, right = left[onset:], right[onset:]
        length = min(len(left), pad.end - pad.start + 1)
        take = Take(None, pad.name, left, right, rate, 0, length, device_name=pad.name,
                    pad_index=pad.pad, device_offset=pad.start, device_max_end=pad.end)
        self._append_take(take)
        self._show_toast(f"\u2713  Opened pad {pad.pad + 1}. Trim it, then Apply.")

    def _update_send_button(self) -> None:
        connected = self.device.state == dev.CONNECTED
        self.send_btn.setVisible(connected)
        take = self.current()
        pad = self._pad_for_take(take)
        self.send_btn.setVisible(connected and pad is not None)
        self.send_btn.setEnabled(pad is not None and take is not None and take.fits)
        if pad is not None:
            self.send_btn.setText(f"Apply to pad {pad.pad + 1}")
            self.send_btn.setToolTip("Set the pad's start and end points (and name) on the "
                                     "SEQTRAK to match this selection")

    def _send_trim(self) -> None:
        take = self.current()
        pad = self._pad_for_take(take)
        if take is None or pad is None:
            return
        new_name = take.label if take.label != pad.name else None
        offset = take.device_offset
        last = take.last_frame + offset
        if take.device_max_end is not None:
            last = min(last, take.device_max_end)
        try:
            msgs = sysex.trim_messages(pad, take.start + offset, take.end + offset, last,
                                       new_name)
        except ValueError as e:
            QMessageBox.warning(self, "Can't apply", str(e))
            return
        rename = f" and rename it to \u201c{new_name}\u201d" if new_name else ""
        answer = QMessageBox.question(
            self, "Apply to SEQTRAK?",
            f"Trim pad {pad.pad + 1} (\u201c{pad.name}\u201d) to this "
            f"{take.seconds:.2f} s selection{rename}?\n\n"
            f"This edits the pad in the SEQTRAK's current project. Only its start and end "
            f"points change; the recorded audio stays as it is.")
        if answer != QMessageBox.Yes:
            return
        self.device.send_trim(msgs, pad.pad)
        if new_name:
            take.device_name = sysex.decode_name(sysex.encode_name(new_name))
        if take.pad_index is not None:
            # The selection is now the pad's playable region.
            take.device_offset += take.start
            take.device_max_end = min(last, sysex.end_point_for(take.end + offset, last))
            take.left, take.right = take.left[take.start:take.end], take.right[take.start:take.end]
            take.start, take.end = 0, len(take.left)
            self._show_take(self.list.currentRow())
        self._show_toast(f"\u2713  Applied to pad {pad.pad + 1}")

    # Recording from the SEQTRAK's USB audio

    def _toggle_record(self) -> None:
        if self.recorder.state == "recording":
            self.recorder.stop_take()
        else:
            self.recorder.start_take()

    def _follow_toggled(self, on: bool) -> None:
        self.settings.setValue("rec_follow", on)
        self.recorder.set_armed(on)
        self._recorder_state(self.recorder.state)

    def _transport_started(self) -> None:
        if self.follow_box.isChecked():
            self.recorder.start_take()

    def _transport_stopped(self) -> None:
        if self.follow_box.isChecked():
            self.recorder.stop_take()

    def _recorder_state(self, state: str) -> None:
        recording = state == "recording"
        self.rec_btn.setText("  Stop" if recording else "  Record")
        self.rec_btn.setIcon(stop_icon(theme.TEXT) if recording else record_icon())
        self.level_bar.setVisible(state != "idle")
        if recording:
            self.rec_status.setText("\u25cf  Recording")
            self.rec_status.setStyleSheet(f"color: {theme.DANGER};")
        elif state == "armed" and self.follow_box.isChecked():
            self.rec_status.setText("Armed. Press Play on the SEQTRAK to record.")
            self.rec_status.setStyleSheet("")
        else:
            self.rec_status.setText("Records the SEQTRAK's USB audio")
            self.rec_status.setStyleSheet("")

    def _recorder_error(self, message: str) -> None:
        self._capture = None
        self._show_toast(message, error=True)
        if self.follow_box.isChecked():
            self.follow_box.setChecked(False)

    def _recorded(self, left, right, rate: int) -> None:
        if self._capture is not None:
            pad, self._capture = self._capture, None
            self._captured(pad, left, right, rate)
            return
        take = Take(None, time.strftime("Take %H-%M-%S"), left, right, rate, 0, len(left))
        take.auto_trim()
        self._append_take(take)
        self._show_toast(f"\u2713  Recorded {take.seconds:.1f} s")
