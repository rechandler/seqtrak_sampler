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


ELEMENT_LEVEL = 0x2E
# A sampler pad's oscillator block from a SEQTRAK running OS 2.0 (pad 2, wave 406).
USED_OSC = [int(x, 16) for x in (
    "01 01 00 03 16 00 00 00 01 00 00 40 00 40 40 00 3D 3D 01 7F 00 00 00 00 01 00 00 00 00"
    " 00 00 00 00 00 00 00 00 00 00 00 00 00 07 0B 00 00 64 54 00 03 00 00 00 7F 7F 32 00 7F"
    " 7F 7F 00 04 40 40 18 24 30 3C 48 01 00 01 00 01 00 01 00 40 40 40 40 40 00 00 40 3C 40"
    " 00 28 40 40 40 01 00 01 00 01 00 01 00 01 00 54 04 40 40 02 40 3C").split()]
USED_FILTER = [0x04, 0x01, 0x20] + [0x40] * 68


class FakeSeqtrak:
    """Answers the documented SysEx messages like a SEQTRAK would."""

    def __init__(self, sysex_on=True):
        self.sysex_on = sysex_on
        self.pads = {0: dict(name="Resampling-5-1", start=105388, end=511567)}
        self.callback = None
        self.sent: list[list[int]] = []
        self.changes: list[tuple[int, int, int]] = []   # (pad, block, offset) changed

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
            p = self.pad(pad)
            self.reply(bulk_dump(sysex.oscillator_address(pad, 0), p["osc"]))
            self.reply(bulk_dump((sysex.FILTER_HI, (pad << 4) | 0x0A, 0x00), p["filt"]))
            self.reply(bulk_dump(sysex.sample_general_address(pad),
                                 sample_general(p["name"], p["start"], p["end"],
                                                int(p["loop_on"]), p["loop_length"])))
        elif msg[2] == 0x10 and msg[6] in (sysex.OSCILLATOR_HI, sysex.FILTER_HI):
            p, offset, data = self.pad(msg[7] >> 4), msg[8], msg[9:-1]
            block = p["osc"] if msg[6] == sysex.OSCILLATOR_HI else p["filt"]
            block[offset:offset + len(data)] = data
            self.changes.append((msg[7] >> 4, msg[6], offset))
            self._sync(p)
        elif msg[2] == 0x10 and msg[6] == 0x50:
            p, offset = self.pad(msg[7] >> 4), msg[8]
            if offset < sysex.NAME_SIZE:
                packed = p.setdefault("packed_name", sysex.encode_name(p["name"]))
                packed[offset] = msg[9]
                p["name"] = sysex.decode_name(packed)
            elif offset == sysex.LOOP_ON:
                p["loop_on"] = bool(msg[9])
            elif offset == sysex.LOOP_LENGTH:
                p["loop_length"] = sysex.decode_point(msg[9:13])
            elif offset == sysex.START_POINT:
                p["start"] = sysex.decode_point(msg[9:13])
            elif offset == sysex.END_POINT:
                p["end"] = sysex.decode_point(msg[9:13])

    def pad(self, pad):
        """The pad's state, with what a real SEQTRAK reports for anything not set."""
        p = self.pads.setdefault(pad, dict(name="", start=0, end=0))
        has_sample = p["end"] > 0
        if "osc" not in p:
            osc = list(USED_OSC)
            filt = list(USED_FILTER)
            wave = p.get("wave", 400 + pad if has_sample else 0)
            osc[sysex.WAVE_NUMBER:sysex.WAVE_NUMBER + 2] = [wave >> 7, wave & 0x7F]
            if has_sample:
                osc[sysex.NOTE_LIMIT_LOW] = osc[sysex.NOTE_LIMIT_HIGH] = 60 + pad
            else:
                # An empty pad: switched off, all notes, and not set up for a sample.
                osc[sysex.ELEMENT_ASSIGN] = 0
                osc[sysex.NOTE_LIMIT_LOW], osc[sysex.NOTE_LIMIT_HIGH] = 0, 127
                osc[ELEMENT_LEVEL] = 0x40
                filt[0] = 0x15
            p["osc"], p["filt"] = osc, filt
            self._sync(p)
        p.setdefault("loop_on", False)
        p.setdefault("loop_length", p["end"])     # one-shot pads report their last frame
        return p

    @staticmethod
    def _sync(p):
        osc = p["osc"]
        p["assigned"] = bool(osc[sysex.ELEMENT_ASSIGN])
        p["wave"] = (osc[sysex.WAVE_NUMBER] << 7) | osc[sysex.WAVE_NUMBER + 1]
        p["note_low"], p["note_high"] = osc[sysex.NOTE_LIMIT_LOW], osc[sysex.NOTE_LIMIT_HIGH]
        p["level"] = osc[ELEMENT_LEVEL]

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

    def test_slice_mode_loads_segments_onto_pads_and_undoes(self):
        from PySide6.QtWidgets import QMessageBox

        from seqtrak_sampler.gui import slice_mode
        fake = FakeSeqtrak()
        fake.pads = {0: dict(name="Break", start=1000, end=300015),
                     1: dict(name="Vox chop", start=0, end=88207)}
        w = self.make_window(fake)
        fake.pads[0]["osc"][ELEMENT_LEVEL] = 0x70       # the source pad's own settings
        fake.pads[0]["filt"][0x05] = 0x22
        empty_osc, empty_filt = list(fake.pads[2]["osc"]), list(fake.pads[2]["filt"])
        w.device.request_pads()
        w.mode_group.button(1).click()
        panel = w.slice_panel
        self.assertEqual(w.stack.currentWidget(), panel)
        self.assertEqual(w._pads[0].wave, 400)

        sound = array("h", [3000] * 132300)
        panel.set_source(slice_mode.Source("Break", sound, array("h", sound), 44100,
                                           pad=0, device_offset=1000))
        panel._split(3)
        self.assertEqual(panel.count(), 3)
        self.assertEqual([t[2] for t in panel.pad_map._tiles],
                         ["source", "replace", "empty", "unused", "unused", "unused",
                          "unused"])
        self.assertEqual(panel.load_btn.text(), "Load to pads 1\u20133")
        self.assertTrue(panel.load_btn.isEnabled())

        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        fake.changes.clear()
        panel._load()
        w.device.request_pads()          # what send_trim schedules, done right away
        # Nothing is played into the SEQTRAK: no notes, no sampling source change.
        self.assertFalse(any(m[0] != 0xF0 for m in fake.sent))
        self.assertFalse(any(m[6:9] == [0x00, 0x00, 0x19] for m in fake.sent))
        # Every pad plays the source pad's recording, trimmed to its segment.
        for i, (start, end) in enumerate([(1000, 45100), (45100, 89200), (89200, 133300)]):
            p = fake.pads[i]
            self.assertEqual((p["wave"], p["assigned"]), (400, True), i)
            self.assertEqual((p["note_low"], p["note_high"]), (60 + i, 60 + i), i)
            self.assertEqual(p["start"], start, i)
            self.assertEqual(p["end"], sysex.end_point_for(end, 300015), i)
            self.assertEqual(p["name"], f"Break {i + 1}")
        self.assertEqual(fake.pads[2]["loop_length"], 300015)
        self.assertFalse(fake.pads[1]["loop_on"])
        # ...and sounds like it: level, envelope, filter and EQ come from the source pad,
        # so a pad that was empty isn't left with an empty pad's settings.
        for i in (1, 2):
            expected = list(fake.pads[0]["osc"])
            expected[0x10] = expected[0x11] = 60 + i
            self.assertEqual(fake.pads[i]["osc"], expected, i)
            self.assertEqual(fake.pads[i]["filt"], fake.pads[0]["filt"], i)
        self.assertEqual(fake.pads[2]["level"], 0x70)
        # Pad 2 already held a sample, so only what differed was sent.
        self.assertEqual(sorted({(b, o) for p, b, o in fake.changes if p == 1}),
                         [(0x41, 0x00), (0x41, 0x03), (0x41, ELEMENT_LEVEL), (0x42, 0x05)])

        self.assertTrue(panel.undo_btn.isVisible() or not panel.isVisible())
        panel._undo_load()
        w.device.request_pads()
        self.assertEqual((fake.pads[0]["name"], fake.pads[0]["start"],
                          fake.pads[0]["end"]), ("Break", 1000, 300015))
        self.assertEqual((fake.pads[1]["name"], fake.pads[1]["wave"], fake.pads[1]["end"]),
                         ("Vox chop", 401, 88207))
        self.assertFalse(fake.pads[2]["assigned"])
        self.assertTrue(w._pads[2].empty)
        # Undo puts the empty pad's own settings back too (its wave number stays, unused).
        restored = list(empty_osc)
        restored[3:5] = fake.pads[2]["osc"][3:5]
        self.assertEqual(fake.pads[2]["osc"], restored)
        self.assertEqual(fake.pads[2]["filt"], empty_filt)
        self.assertEqual(fake.pads[1]["level"], 100)

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
