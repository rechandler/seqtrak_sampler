"""End-to-end tests of the SEQTRAK panel against a simulated device. Needs PySide6."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from array import array
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError:  # the CI test job runs without the GUI extras
    QApplication = None

from seqtrak_sampler import sysex

try:
    from PySide6.QtCore import QTimer
except ImportError:
    QTimer = None

sys.path.insert(0, str(Path(__file__).parent))
from test_sysex import bulk_dump, sample_general  # noqa: E402
from test_y3s import make_y3s  # noqa: E402


class FakeSeqtrak:
    """Answers the documented SysEx messages like a SEQTRAK would."""

    def __init__(self, sysex_on=True):
        self.sysex_on = sysex_on
        self.pads = {0: dict(name="Resampling-5-1", start=105388, end=511567)}
        self.callback = None
        self.sent: list[list[int]] = []

    def reply(self, msg):
        if self.callback and self.sysex_on:
            self.callback((msg, 0.0), None)

    def receive(self, msg):
        self.sent.append(msg)
        if msg[0] != 0xF0:
            return  # notes play sound on the real device; nothing to answer
        if msg == sysex.enable_usb_sysex_transmit():
            self.sysex_on = True
        elif msg == sysex.identity_request():
            self.reply([0xF0, 0x7E, 0x7F, 0x06, 0x02, 0x43, 0x00, 0x41, 0x64, 0x06, 0x0A,
                        0x00, 0x00, 0x7F, 0xF7])
        elif msg[:9] == sysex.pad_request(msg[8])[:9] and len(msg) == 10:
            pad = msg[8]
            p = self.pads.get(pad, dict(name="", start=0, end=0))
            osc = [0] * 109
            osc[sysex.NOTE_LIMIT_LOW] = osc[sysex.NOTE_LIMIT_HIGH] = 60 + pad
            self.reply(bulk_dump((sysex.OSCILLATOR_HI, (pad << 4) | 0x0A, 0x00), osc))
            self.reply(bulk_dump(sysex.sample_general_address(pad),
                                 sample_general(p["name"], p["start"], p["end"])))
        elif msg[2] == 0x10 and msg[6] == 0x50:
            pad, offset = msg[7] >> 4, msg[8]
            p = self.pads.setdefault(pad, dict(name="", start=0, end=0))
            if offset < sysex.NAME_SIZE:
                packed = p.setdefault("packed_name", sysex.encode_name(p["name"]))
                packed[offset] = msg[9]
                p["name"] = sysex.decode_name(packed)
            elif offset == sysex.START_POINT:
                p["start"] = sysex.decode_point(msg[9:13])
            elif offset == sysex.END_POINT:
                p["end"] = sysex.decode_point(msg[9:13])

    def module(self):
        device = self

        class MidiIn:
            def get_ports(self):
                return ["SEQTRAK"]

            def open_port(self, i):
                pass

            def close_port(self):
                pass

            def ignore_types(self, **kw):
                pass

            def set_callback(self, cb):
                device.callback = cb

        class MidiOut(MidiIn):
            def send_message(self, msg):
                device.receive(list(msg))

        return type("rtmidi", (), {"MidiIn": MidiIn, "MidiOut": MidiOut})


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class DevicePanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self, fake):
        from seqtrak_sampler.gui import device as dev
        from seqtrak_sampler.gui import window

        real = dev.Device.__init__

        def init(self_, parent=None, rtmidi=None):
            real(self_, parent, rtmidi=fake.module())

        dev.Device.__init__ = init
        self.addCleanup(setattr, dev.Device, "__init__", real)
        w = window.MainWindow()
        w.settings.clear()
        w._latencies = []
        w._history = {}
        w.settings.setValue("output_dir", tempfile.mkdtemp())
        self.addCleanup(w.close)
        return w

    def test_connects_reads_pads_and_sends_trim(self):
        from PySide6.QtWidgets import QMessageBox

        from seqtrak_sampler.gui.widgets import ROLE_PAD_STATE
        fake = FakeSeqtrak()
        w = self.make_window(fake)
        self.assertEqual(w.device.state, "connected")
        self.assertEqual(w._pads[0].name, "Resampling-5-1")
        self.assertEqual(w.pads_list.item(0).data(ROLE_PAD_STATE), "new")

        tmp = Path(tempfile.mkdtemp())
        y3s = tmp / "Resampling-5-1.Y3S"
        n = 511568
        make_y3s(y3s, [[0] * 106000 + [1000] * 300000 + [0] * (n - 406000)] * 2)
        with zipfile_name_patch(y3s, "Resampling-5-1"):
            w.add_files([y3s])
        take = w.current()
        self.assertEqual(take.device_name, "Resampling-5-1")
        self.assertEqual(w._pad_state(w._pads[0]), "in_app")
        self.assertTrue(w.send_btn.isEnabled())
        self.assertEqual(w.send_btn.text(), "Apply to pad 1")

        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        w._send_trim()
        self.assertEqual(fake.pads[0]["start"], take.start)
        self.assertEqual(fake.pads[0]["end"], sysex.end_point_for(take.end, take.last_frame))

    def test_open_pad_by_playing_it_then_apply_trim(self):
        from PySide6.QtWidgets import QMessageBox
        fake = FakeSeqtrak()
        fake.pads[1] = dict(name="Vox chop", start=1000, end=89199)   # 88200 frames
        w = self.make_window(fake)
        pad = w._pads[1]
        self.assertEqual(pad.note, 61)
        self.assertEqual(w._pad_state(pad), "new")

        # What the recorder would capture: a little latency, then the pad's sound.
        lead = 3000
        sound = array("h", [0] * lead + [4000] * 88200 + [0] * 8000)
        w._captured(pad, sound, array("h", sound), 44100)
        take = w.current()
        self.assertEqual((take.pad_index, take.device_offset, take.end), (1, 1000, 88200))
        self.assertEqual(w._pad_state(pad), "in_app")
        self.assertEqual(w.send_btn.text(), "Apply to pad 2")

        take.start, take.end = 44100, 66150      # keep 1.0 s - 1.5 s of the pad
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        w._send_trim()
        self.assertEqual(fake.pads[1]["start"], 1000 + 44100)
        self.assertEqual(fake.pads[1]["end"], sysex.end_point_for(1000 + 66150, 89199))
        self.assertEqual((take.start, take.device_offset), (0, 45100))

        # Applying again without changes keeps the same points.
        w._send_trim()
        self.assertEqual(fake.pads[1]["start"], 45100)

    def test_undo_restores_pad_and_reload_reopens_it(self):
        from PySide6.QtWidgets import QMessageBox
        fake = FakeSeqtrak()
        fake.pads[1] = dict(name="Vox chop", start=1000, end=89199)
        w = self.make_window(fake)
        sound = array("h", [0] * 500 + [4000] * 88200 + [0] * 4000)
        w._captured(w._pads[1], sound, array("h", sound), 44100)
        take = w.current()
        self.assertFalse(w.undo_btn.isEnabled())

        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        take.start, take.end = 44100, 66150
        w._send_trim()
        self.app.processEvents()
        w.device.request_pads()          # what send_trim schedules, done right away
        self.assertEqual(fake.pads[1]["start"], 45100)
        self.assertTrue(w.undo_btn.isEnabled())

        reopened = []
        w.open_pad = lambda pad: reopened.append((pad.pad, pad.start, pad.end))
        w._undo_trim()
        self.assertEqual((fake.pads[1]["start"], fake.pads[1]["end"]), (1000, 89199))
        self.assertFalse(w.undo_btn.isEnabled())
        w._reopen_pad(1)                 # what the reload timer does
        self.assertEqual(reopened, [(1, 1000, 89199)])
        self.assertNotIn(take, w.takes)

    def test_leading_silence_is_kept_and_aligned(self):
        from PySide6.QtWidgets import QMessageBox
        fake = FakeSeqtrak()
        fake.pads[1] = dict(name="Quiet start", start=0, end=132299)   # 3 s
        fake.pads[2] = dict(name="Hit", start=0, end=44099)
        w = self.make_window(fake)
        delay = 900

        # A pad whose sound starts right away teaches the app the delay.
        note = 2000
        hit = array("h", [0] * (note + delay) + [5000] * 44100 + [0] * 3000)
        w._captured(w._pads[2], hit, array("h", hit), 44100, note_frame=note)
        self.assertEqual(w._latencies, [delay])

        # Pad 2 starts with 1 s of silence: it stays in view, lined up with the pad.
        silence = 44100
        quiet = array("h", [0] * (note + delay + silence) + [5000] * 88200 + [0] * 3000)
        w._captured(w._pads[1], quiet, array("h", quiet), 44100, note_frame=note)
        take = w.current()
        self.assertEqual(take.end, 132300)
        self.assertEqual(max(take.left[:silence]), 0)
        self.assertEqual(take.left[silence], 5000)

        # Trimming the silence moves the pad's start to where the sound begins.
        take.start = silence
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        w._send_trim()
        self.assertEqual(fake.pads[1]["start"], silence)

    def test_load_slices_to_pads(self):
        from seqtrak_sampler.gui.loader import Job, SliceLoader
        fake = FakeSeqtrak()
        fake.pads = {}
        w = self.make_window(fake)
        rate = 44100
        source = array("h", [0] * 441 + [6000] * 88200)          # 2 s of sound
        jobs = [Job(2, 0, 44100, "Loop 1"), Job(4, 44100, len(source), "Loop 2")]
        played = []

        def fake_play(left, right, rate_, offset, to_seqtrak=False):
            played.append((len(left), to_seqtrak))
            QTimer.singleShot(0, w.player.stopped.emit)
        w.player.play = fake_play

        loader = SliceLoader(w, source, array("h", source), rate, jobs)
        self.assertEqual(fake.sent[-1][6:10], [0x00, 0x00, 0x19, 0x02])   # USB Audio In

        silence_before = 30000                      # what the SEQTRAK recorded first
        for job in jobs:
            loader._on_action()                     # Play into SEQTRAK
            self.app.processEvents()
            self.assertEqual(played[-1][1], True)
            # The SEQTRAK sampled: its pad now holds silence + the whole source.
            fake.pads[job.pad] = dict(name="Sample", start=0,  # fresh sample on the pad
                                      end=silence_before + len(source) + 9999 | 0xF)
            w.device.request_pads()
            pad = w._pads[job.pad]
            # Playing the pad back: its sound shows up after the recorded silence.
            capture = array("h", [0] * (silence_before + 441) + [6000] * 88200)
            loader._captured(pad, capture, array("h", capture), rate)
            w.device.request_pads()

        offset = silence_before + 441 - 441
        self.assertEqual(fake.pads[2]["start"], offset)
        self.assertEqual(fake.pads[4]["start"], offset + 44100)
        self.assertEqual(fake.pads[4]["end"],
                         sysex.end_point_for(offset + len(source), w._pads[4].last_frame))
        self.assertEqual([w._pads[2].name, w._pads[4].name], ["Loop 1", "Loop 2"])
        self.assertEqual(loader.progress.text(), "DONE")
        source_changes = [m for m in fake.sent if m[6:9] == [0x00, 0x00, 0x19]]
        self.assertEqual(source_changes[-1][9], 0x01)                    # source restored
        # Re-sampled pads hold new recordings: no stale undo steps for them.
        self.assertNotIn("Loop 1", w._history)

    def test_slice_mode_pad_map_and_jobs(self):
        from PySide6.QtWidgets import QMessageBox

        from seqtrak_sampler.gui import slice_mode
        fake = FakeSeqtrak()
        fake.pads = {0: dict(name="Break", start=1000, end=300015),
                     1: dict(name="Vox chop", start=0, end=88207)}
        w = self.make_window(fake)
        w.mode_group.button(1).click()
        panel = w.slice_panel
        self.assertEqual(w.stack.currentWidget(), panel)

        sound = array("h", [3000] * 132300)
        panel.set_source(slice_mode.Source("Break", sound, array("h", sound), 44100,
                                           pad=0, device_offset=1000))
        panel._split(3)
        self.assertEqual(panel.count(), 3)
        tiles = panel.pad_map._tiles
        self.assertEqual([t[2] for t in tiles],
                         ["source", "replace", "empty", "unused", "unused", "unused",
                          "unused"])
        self.assertEqual(panel.load_btn.text(), "Load to pads 1\u20133")
        self.assertTrue(panel.load_btn.isEnabled())

        captured = {}

        class FakeLoader:
            def __init__(self, window, left, right, rate, jobs):
                captured["jobs"] = jobs

            def exec(self):
                return 0
        original = slice_mode.SliceLoader
        slice_mode.SliceLoader = FakeLoader
        self.addCleanup(setattr, slice_mode, "SliceLoader", original)
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        panel._load()
        jobs = captured["jobs"]
        self.assertEqual([(j.pad, j.trim_only, j.name) for j in jobs],
                         [(0, True, "Break 1"), (1, False, "Break 2"),
                          (2, False, "Break 3")])
        self.assertEqual((jobs[1].start, jobs[1].end), (44100, 88200))
        self.assertEqual(jobs[0].device_offset, 1000)

    def test_loader_trims_source_pad_without_sampling(self):
        from seqtrak_sampler.gui.loader import Job, SliceLoader
        fake = FakeSeqtrak()
        fake.pads = {0: dict(name="Break", start=1000, end=300015)}
        w = self.make_window(fake)
        sound = array("h", [3000] * 132300)
        loader = SliceLoader(w, sound, array("h", sound), 44100,
                             [Job(0, 0, 44100, "Break 1", trim_only=True,
                                  device_offset=1000)])
        w.device.request_pads()
        self.assertEqual((fake.pads[0]["start"], w._pads[0].name), (1000, "Break 1"))
        self.assertEqual(fake.pads[0]["end"], sysex.end_point_for(45100, 300015))
        self.assertEqual(loader.progress.text(), "DONE")
        self.assertFalse(any(m[6:9] == [0x00, 0x00, 0x19] for m in fake.sent))  # no sampling
        self.assertIn("Break 1", w._history)       # trimming can be undone

    def test_note_messages_play_the_pad(self):
        fake = FakeSeqtrak()
        w = self.make_window(fake)
        w.device.play_note(60, 0.0)
        self.app.processEvents()
        self.assertIn([0x9A, 60, 100], fake.sent)

    def test_silent_device_can_be_enabled(self):
        fake = FakeSeqtrak(sysex_on=False)
        w = self.make_window(fake)
        w.device._identity_timer.timeout.emit()
        self.assertEqual(w.device.state, "silent")
        self.assertFalse(w.enable_btn.isHidden())
        w.device.enable_sysex()
        w.device.probe()
        self.assertEqual(w.device.state, "connected")

    def test_recorded_take_is_added(self):
        w = self.make_window(FakeSeqtrak())
        frames = array("h", [0] * 1000 + [5000] * 44100 + [0] * 1000)
        w._recorded(frames, array("h", frames), 44100)
        take = w.current()
        self.assertIsNone(take.path)
        self.assertTrue(take.label.startswith("Take "))
        self.assertAlmostEqual(take.seconds, 1.0, delta=0.02)
        self.assertFalse(w.send_btn.isEnabled())


class zipfile_name_patch:
    """Give a synthetic .Y3S a device sample name in element.bin."""

    def __init__(self, path, name):
        import zipfile
        with zipfile.ZipFile(path) as zf:
            members = {i.filename: zf.read(i) for i in zf.infolist()}
        key = next(k for k in members if k.endswith("element.bin"))
        data = bytearray(members[key])
        data[4:4 + sysex.NAME_SIZE] = bytes(sysex.encode_name(name))
        members[key] = bytes(data)
        with zipfile.ZipFile(path, "w") as zf:
            for k, v in members.items():
                zf.writestr(k, v)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


if __name__ == "__main__":
    unittest.main()
