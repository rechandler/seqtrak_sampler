import unittest
from array import array

from seqtrak_sampler import keys

class KeysTests(unittest.TestCase):
    def test_note_names_follow_yamaha(self):
        self.assertEqual([keys.note_name(n) for n in (36, 60, 61, 96)],
                         ["C1", "C3", "C#3", "C6"])

    def test_scale_notes(self):
        self.assertEqual(keys.scale_notes(60, keys.SCALES["Major"]),
                         [60, 62, 64, 65, 67, 69, 71])
        self.assertEqual(keys.scale_notes(57, keys.SCALES["Minor pentatonic"]),
                         [57, 60, 62, 64, 67, 69, 72])          # carries on an octave up
        self.assertEqual(keys.shifts([62, 64], 60), [2, 4])

    def test_repitch_changes_length(self):
        sound = array("h", range(0, 4000, 4))                  # 1000 samples, a ramp
        up = keys.repitch(sound, 12)
        self.assertEqual(len(up), 500)
        self.assertEqual(up[10], sound[20])
        self.assertEqual(len(keys.repitch(sound, -12)), 2000)
        self.assertEqual(keys.repitch(sound, 0), sound)
