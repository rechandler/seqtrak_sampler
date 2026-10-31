import unittest
from array import array

from seqtrak_sampler import slicing


def beats(rate, positions, length, level=12000, decay=2000):
    """A track of short decaying hits at the given frame positions."""
    out = array("h", bytes(2 * length))
    for p in positions:
        for i in range(decay):
            if p + i < length:
                out[p + i] = int(level * (1 - i / decay)) * (1 if i % 2 else -1)
    return out


class SlicingTests(unittest.TestCase):
    def test_equal_markers(self):
        self.assertEqual(slicing.equal_markers(0, 400, 4), [100, 200, 300])
        self.assertEqual(slicing.equal_markers(100, 200, 1), [])
        self.assertEqual(len(slicing.equal_markers(0, 1000, 20)), 6)  # capped at 7 slices

    def test_slices_ignore_markers_outside_and_duplicates(self):
        self.assertEqual(slicing.slices(10, 100, [50, 50, 5, 150, 30]),
                         [(10, 30), (30, 50), (50, 100)])
        self.assertEqual(slicing.slices(0, 10, []), [(0, 10)])

    def test_hit_markers_find_each_hit(self):
        rate = 44100
        hits = [4410, 26460, 48510, 70560]
        track = beats(rate, hits, 90000)
        markers = slicing.hit_markers(track, track, rate, 0, len(track))
        self.assertEqual(len(markers), 3)            # first hit is the start itself
        for found, expected in zip(markers, hits[1:]):
            self.assertLess(abs(found - expected), rate * 15 // 1000)

    def test_hit_markers_capped(self):
        rate = 44100
        track = beats(rate, list(range(2205, 2205 + 12 * 8820, 8820)), 120000)
        self.assertEqual(len(slicing.hit_markers(track, track, rate, 0, len(track))), 6)

    def test_onset(self):
        sig = array("h", [0, 3, 0, 900, 0])
        self.assertEqual(slicing.onset(sig, sig, 100), 3)
        self.assertIsNone(slicing.onset(sig, sig, 1000))


class DeviceRangeTests(unittest.TestCase):
    def test_maps_slice_through_sampling_offset(self):
        # The pad recorded 0.5 s of silence before the source; its start point is 1000.
        # In the playback recording the source's first sound arrives 21050 frames in,
        # and in the source itself 100 frames in.
        start, end = slicing.device_range(1000, 21050, 100, 44100, 88200, 400000)
        offset = 1000 + 21050 - 100
        self.assertEqual((start, end), (offset + 44100, offset + 88200))

    def test_clamps_to_recording(self):
        self.assertEqual(slicing.device_range(0, 0, 5000, 0, 10000, 3000), (0, 3001))


if __name__ == "__main__":
    unittest.main()


