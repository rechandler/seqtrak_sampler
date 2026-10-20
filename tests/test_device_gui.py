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
        if msg == sysex.enable_usb_sysex_transmit():
            self.sysex_on = True
        elif msg == sysex.identity_request():
            self.reply([0xF0, 0x7E, 0x7F, 0x06, 0x02, 0x43, 0x00, 0x41, 0x64, 0x06, 0x0A,
                        0x00, 0x00, 0x7F, 0xF7])
        elif msg[:9] == sysex.pad_request(msg[8])[:9] and len(msg) == 10:
            pad = msg[8]
            p = self.pads.get(pad, dict(name="", start=0, end=0))
            self.reply(bulk_dump(sysex.sample_general_address(pad),
                                 sample_general(p["name"], p["start"], p["end"])))
        elif msg[2] == 0x10 and msg[6] == 0x50:
            pad, offset = msg[7] >> 4, msg[8]
            p = self.pads.setdefault(pad, dict(name="", start=0, end=0))
            if offset == sysex.START_POINT:
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
        self.assertEqual(w.send_btn.text(), "Send trim to pad 1")

        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        w._send_trim()
        self.assertEqual(fake.pads[0]["start"], take.start)
        self.assertEqual(fake.pads[0]["end"], sysex.end_point_for(take.end, take.last_frame))

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
