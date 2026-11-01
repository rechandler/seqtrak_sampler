import math
import unittest
from array import array

from seqtrak_sampler import padsound, sysex
from test_sysex import OSC2


def pad_with_blocks():
    osc = OSC2[11:-2]
    filt = [0x04, 0x01, 0x20] + [0x40] * 68
    return sysex.SamplerPad(1, "Vox", 1000, 89199, False, 89199, 61, 61, 406, True,
                            list(osc), filt)


class ParamTests(unittest.TestCase):
    def test_reads_a_real_pad(self):
        pad = pad_with_blocks()
        by = padsound.BY_KEY
        self.assertEqual(padsound.value(pad, by["level"]), 100)
        self.assertEqual(padsound.value(pad, by["pan"]), 64)
        self.assertEqual(padsound.value(pad, by["coarse"]), 64)
        self.assertEqual(padsound.value(pad, by["cutoff"]), (0x01 << 7) | 0x20)
        empty = sysex.SamplerPad(2, "", 0, 0, False, 0)
        self.assertIsNone(padsound.value(empty, by["level"]))

    def test_messages_and_local_copy(self):
        pad = pad_with_blocks()
        by = padsound.BY_KEY
        self.assertEqual(padsound.message(1, by["level"], 90),
                         sysex.parameter_change((0x41, 0x1A, 0x2E), [90]))
        self.assertEqual(padsound.message(1, by["cutoff"], 200),
                         sysex.parameter_change((0x42, 0x1A, 0x01), [1, 72]))
        self.assertEqual(padsound.message(1, by["coarse"], 99)[-2], 88)      # clamped
        changed = padsound.with_value(pad, by["cutoff"], 200)
        self.assertEqual(padsound.value(changed, by["cutoff"]), 200)
        self.assertEqual(padsound.value(pad, by["cutoff"]), 160)             # untouched

    def test_display(self):
        by = padsound.BY_KEY
        self.assertEqual([by["pan"].text(v) for v in (1, 64, 100)], ["L63", "C", "R36"])
        self.assertEqual(by["coarse"].text(52), "-12 st")
        self.assertEqual(by["attack"].text(0), "0 ms")
        self.assertTrue(by["release"].text(127).endswith(" s"))

    def test_envelope_times_grow(self):
        times = [padsound.envelope_seconds(v) for v in range(128)]
        self.assertEqual(times[0], 0)
        self.assertTrue(all(a < b for a, b in zip(times, times[1:])))
        self.assertAlmostEqual(times[127], 10.0, places=3)


class LoopTests(unittest.TestCase):
    def test_loop_points(self):
        pad = pad_with_blocks()
        self.assertIsNone(padsound.loop_start(pad))
        looped = sysex.SamplerPad(1, "Vox", 1000, 89199, True, 44100)
        self.assertEqual(padsound.loop_start(looped), 89199 - 44100 + 1)
        self.assertEqual(padsound.loop_length_for(looped, 45100), 44100)
        self.assertEqual(padsound.loop_length_for(looped, 89190), padsound.MIN_LOOP)
        msgs = padsound.loop_messages(1, True, 44100)
        self.assertEqual(msgs[0], sysex.parameter_change((0x50, 0x1A, 0x5F), [1]))
        self.assertEqual(sysex.decode_point(msgs[1][9:13]), 44100)

    def test_matched_start_continues_the_wave(self):
        # A 100 Hz sine: the loop should start a whole number of cycles from the end.
        rate, n = 44100, 44100
        sine = array("h", [int(10000 * math.sin(2 * math.pi * 100 * i / rate))
                           for i in range(n)])
        end = 30000
        start = padsound.matched_start(sine, sine, 10000, end, window=400)
        period = rate / 100
        cycles = (end + 1 - start) / period
        self.assertAlmostEqual(cycles, round(cycles), delta=0.01)
        self.assertLessEqual(abs(start - 10000), 400)
