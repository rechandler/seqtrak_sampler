import unittest

from seqtrak_sampler import sysex


def bulk_dump(address, data):
    count = 1 + 3 + len(data)
    bh, bl = count >> 7, count & 0x7F
    cc = (-(sysex.MODEL + sum(address) + sum(data))) & 0x7F  # as a real SEQTRAK does
    return [0xF0, 0x43, 0x00, 0x7F, 0x1C, bh, bl, 0x0C, *address, *data, cc, 0xF7]


def sample_general(name="Resampling-5-1", start=105388, end=511567, loop_on=0, loop_len=0):
    data = [0] * 112
    data[0:88] = sysex.encode_name(name)
    data[sysex.LOOP_ON] = loop_on
    data[0x60:0x64] = sysex.encode_point(loop_len)
    data[0x66:0x6A] = sysex.encode_point(start)
    data[0x6A:0x6E] = sysex.encode_point(end)
    return data


def _hex(text):
    return [int(x, 16) for x in text.split()]


class SysexTests(unittest.TestCase):
    def test_requests(self):
        self.assertEqual(sysex.identity_request(), [0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7])
        self.assertEqual(sysex.pad_request(3),
                         [0xF0, 0x43, 0x20, 0x7F, 0x1C, 0x0C, 0x11, 0x13, 0x03, 0xF7])
        self.assertEqual(sysex.enable_usb_sysex_transmit(),
                         [0xF0, 0x43, 0x10, 0x7F, 0x1C, 0x0C, 0x00, 0x00, 0x10, 0x01, 0xF7])

    def test_sampler_sound_request(self):
        self.assertEqual(sysex.sampler_sound_request(),
                         [0xF0, 0x43, 0x20, 0x7F, 0x1C, 0x0C, 0x11, 0x02, 0x0A, 0xF7])

    def test_bulk_dump_ignores_byte_count(self):
        msg = bulk_dump(sysex.sample_general_address(0), sample_general())
        msg[5], msg[6] = 0, 0x10   # wrong count; the checksum doesn't include it
        self.assertIsNotNone(sysex.parse_bulk_dump(msg))
        self.assertEqual(sysex.bulk_dump_problem([0xF0, 0x41, 0xF7]),
                         "not a complete SysEx message")

    def test_identity_reply(self):
        reply = [0xF0, 0x7E, 0x7F, 0x06, 0x02, 0x43, 0x00, 0x41, 0x64, 0x06, 0x0A,
                 0x00, 0x00, 0x7F, 0xF7]
        self.assertEqual(sysex.parse_identity_reply(reply), "2.0")
        reply[8] = 0x65
        self.assertIsNone(sysex.parse_identity_reply(reply))

    def test_points_round_trip(self):
        for v in (0, 1, 0x7F, 105388, 511567, 0xFFFFFF):
            self.assertEqual(sysex.decode_point(sysex.encode_point(v)), v)
        self.assertEqual(sysex.encode_point(0xFFFFFF), [0x07, 0x7F, 0x7F, 0x7F])

    def test_7bit_matches_y3s_layout(self):
        # element.bin in a real export: 00 'Resampl' 00 'ing-5-1'
        packed = [0x00, *b"Resampl", 0x00, *b"ing-5-1"]
        self.assertEqual(sysex.decode_name(packed), "Resampling-5-1")
        self.assertEqual(sysex.decode_7bit(sysex.encode_7bit(bytes(range(256)))),
                         bytes(range(256)))

    def test_encode_name_is_ascii_and_fixed_size(self):
        enc = sysex.encode_name("Lofi Rhodes é")
        self.assertEqual(len(enc), sysex.NAME_SIZE)
        self.assertEqual(sysex.decode_name(enc), "Lofi Rhodes _")

    def test_parse_pad_block(self):
        addr = sysex.sample_general_address(2)
        self.assertEqual(addr, (0x50, 0x2A, 0x00))
        pad = sysex.pad_from_block(sysex.parse_bulk_dump(bulk_dump(addr, sample_general())))
        self.assertEqual((pad.pad, pad.name, pad.start, pad.end),
                         (2, "Resampling-5-1", 105388, 511567))
        self.assertAlmostEqual(pad.seconds, (511567 - 105388 + 1) / 44100)

    def test_real_seqtrak_replies(self):
        """Messages captured from a SEQTRAK running OS 2.0."""
        header = _hex("F0 43 00 7F 1C 00 04 0C 11 13 00 50 F7")
        self.assertEqual(sysex.parse_bulk_dump(header).address, (0x11, 0x13, 0x00))
        pad1 = _hex(
            "F0 43 00 7F 1C 00 74 0C 50 0A 00 00 52 65 73 61 6D 70 6C 00 69 6E 67 2D 35 2D 31"
            + " 00" * 81 + " 1F 1C 4F 00 00 00 06 37 2C 00 0D 4A 3F 00 00 3F F7")
        pad = sysex.pad_from_block(sysex.parse_bulk_dump(pad1))
        self.assertEqual((pad.pad, pad.name, pad.start, pad.end),
                         (0, "Resampling-5-1", 105388, 222527))
        osc2 = _hex(
            "F0 43 00 7F 1C 00 71 0C 41 1A 00 01 01 00 03 16 00 00 00 01 00 00 40 00 40 40 00"
            " 3D 3D 01 7F 00 00 00 00 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 07"
            " 0B 00 00 64 54 00 03 00 00 00 7F 7F 32 00 7F 7F 7F 00 04 40 40 18 24 30 3C 48 01"
            " 00 01 00 01 00 01 00 40 40 40 40 40 00 00 40 3C 40 00 28 40 40 40 01 00 01 00 01"
            " 00 01 00 01 00 54 04 40 40 02 40 3C 11 F7")
        self.assertEqual(sysex.pad_notes_from_block(sysex.parse_bulk_dump(osc2)), (1, 61, 61))
        change = _hex("F0 43 10 7F 1C 0C 01 10 28 00 F7")
        self.assertEqual(sysex.bulk_dump_problem(change), "parameter change (not needed)")

    def test_rejects_bad_checksum_and_other_blocks(self):
        msg = bulk_dump(sysex.sample_general_address(0), sample_general())
        msg[-2] = (msg[-2] + 3) & 0x7F
        self.assertIsNone(sysex.parse_bulk_dump(msg))
        other = sysex.parse_bulk_dump(bulk_dump((0x41, 0x0A, 0x00), [0] * 109))
        self.assertIsNone(sysex.pad_from_block(other))

    def test_end_point_keeps_low_bits_set(self):
        self.assertEqual(sysex.end_point_for(439847, 511567), 439855)
        self.assertEqual(sysex.end_point_for(511568, 511567), 511567)
        self.assertEqual(sysex.end_point_for(511568, 511560), 511551)

    def test_trim_messages(self):
        pad = sysex.SamplerPad(1, "Old", 0, 511567, True, 400000)
        msgs = sysex.trim_messages(pad, 106607, 439847, 511567, name="New")
        self.assertEqual(msgs[0], [0xF0, 0x43, 0x10, 0x7F, 0x1C, 0x0C, 0x50, 0x1A, 0x66,
                                   *sysex.encode_point(106607), 0xF7])
        self.assertEqual(msgs[1][6:9], [0x50, 0x1A, 0x6A])
        self.assertEqual(sysex.decode_point(msgs[2][9:13]), 439855 - 106607 + 1)  # loop
        self.assertEqual(len(msgs), 3 + sysex.NAME_SIZE)
        no_loop = sysex.SamplerPad(1, "Old", 0, 511567, False, 400000)
        self.assertEqual(len(sysex.trim_messages(no_loop, 0, 1000, 511567, name="Old")), 2)


class RestoreTests(unittest.TestCase):
    def test_restore_points_loop_and_name(self):
        now = sysex.SamplerPad(1, "Trimmed", 5000, 9999, True, 4000)
        before = sysex.SamplerPad(1, "Original", 0, 88207, True, 88208)
        msgs = sysex.restore_messages(now, before)
        self.assertEqual(sysex.decode_point(msgs[0][9:13]), 0)
        self.assertEqual(sysex.decode_point(msgs[1][9:13]), 88207)
        self.assertEqual(msgs[2][6:9], [0x50, 0x1A, 0x60])
        self.assertEqual(len(msgs), 3 + sysex.NAME_SIZE)
        same = sysex.SamplerPad(1, "Original", 0, 88207, True, 88208)
        self.assertEqual(len(sysex.restore_messages(same, before)), 2)


class PadNoteTests(unittest.TestCase):
    def test_note_limits_from_oscillator_block(self):
        data = [0] * 109
        data[0x10], data[0x11] = 60, 60
        block = sysex.parse_bulk_dump(bulk_dump((0x41, 0x1A, 0x00), data))
        self.assertEqual(sysex.pad_notes_from_block(block), (1, 60, 60))
        synth = sysex.parse_bulk_dump(bulk_dump((0x41, 0x17, 0x00), data))
        self.assertIsNone(sysex.pad_notes_from_block(synth))

    def test_note_messages_use_channel_11(self):
        self.assertEqual(sysex.note_on(60), [0x9A, 60, 100])
        self.assertEqual(sysex.note_off(60), [0x8A, 60, 0])


if __name__ == "__main__":
    unittest.main()
