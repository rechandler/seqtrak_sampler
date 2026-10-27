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


OSC2 = _hex(  # pad 2's oscillator block from a SEQTRAK running OS 2.0
    "F0 43 00 7F 1C 00 71 0C 41 1A 00 01 01 00 03 16 00 00 00 01 00 00 40 00 40 40 00"
    " 3D 3D 01 7F 00 00 00 00 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 07"
    " 0B 00 00 64 54 00 03 00 00 00 7F 7F 32 00 7F 7F 7F 00 04 40 40 18 24 30 3C 48 01"
    " 00 01 00 01 00 01 00 40 40 40 40 40 00 00 40 3C 40 00 28 40 40 40 01 00 01 00 01"
    " 00 01 00 01 00 54 04 40 40 02 40 3C 11 F7")


class SysexTests(unittest.TestCase):
    def test_requests(self):
        self.assertEqual(sysex.identity_request(), [0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7])
        self.assertEqual(sysex.pad_request(3),
                         [0xF0, 0x43, 0x20, 0x7F, 0x1C, 0x0C, 0x11, 0x13, 0x03, 0xF7])
        self.assertEqual(sysex.enable_usb_sysex_transmit(),
                         [0xF0, 0x43, 0x10, 0x7F, 0x1C, 0x0C, 0x00, 0x00, 0x10, 0x01, 0xF7])

    def test_sampling_source(self):
        self.assertEqual(sysex.sampling_source(sysex.SOURCE_USB_AUDIO),
                         [0xF0, 0x43, 0x10, 0x7F, 0x1C, 0x0C, 0x00, 0x00, 0x19, 0x02, 0xF7])

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
        osc2 = OSC2
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


class SliceMessageTests(unittest.TestCase):
    def test_wave_messages_point_pad_at_a_user_wave(self):
        msgs = sysex.wave_messages(3, 300)
        self.assertEqual(msgs[0], [0xF0, 0x43, 0x10, 0x7F, 0x1C, 0x0C, 0x41, 0x3A, 0x01,
                                   0x01, 0xF7])                       # user wave
        self.assertEqual(msgs[1][6:11], [0x41, 0x3A, 0x03, 300 >> 7, 300 & 0x7F])
        self.assertEqual([m[9] for m in msgs[2:4]], [63, 63])           # pad 4's note
        self.assertEqual(msgs[4][6:10], [0x41, 0x3A, 0x00, 0x01])       # assigned
        with self.assertRaises(ValueError):
            sysex.wave_messages(3, 0)

    def test_parameter_tables_cover_each_block(self):
        osc_bytes = sum(w for _, w in sysex.OSCILLATOR_PARAMS)
        self.assertEqual(osc_bytes + len(sysex._RESERVED_OSC), sysex.OSCILLATOR_SIZE)
        filt_bytes = sum(w for _, w in sysex.FILTER_PARAMS)
        self.assertEqual(filt_bytes + len(sysex._RESERVED_FILTER), sysex.FILTER_SIZE)
        self.assertIn((sysex.WAVE_NUMBER, 2), sysex.OSCILLATOR_PARAMS)
        self.assertIn((0x2E, 1), sysex.OSCILLATOR_PARAMS)          # Element Level
        self.assertIn((0x01, 2), sysex.FILTER_PARAMS)              # Cutoff
        self.assertNotIn(0x09, [o for o, _ in sysex.OSCILLATOR_PARAMS])  # reserved

    def test_reads_whole_blocks(self):
        osc = sysex.pad_oscillator_from_block(sysex.parse_bulk_dump(OSC2))
        self.assertEqual(len(osc.data), sysex.OSCILLATOR_SIZE)
        self.assertEqual(osc.data[0x2E], 100)                       # Element Level
        filt = sysex.parse_bulk_dump(bulk_dump((0x42, 0x3A, 0x00), list(range(71))))
        self.assertEqual(sysex.pad_filter_from_block(filt), (3, list(range(71))))
        synth = sysex.parse_bulk_dump(bulk_dump((0x42, 0x37, 0x00), list(range(71))))
        self.assertIsNone(sysex.pad_filter_from_block(synth))

    def source_pad(self):
        osc = [0x40] * sysex.OSCILLATOR_SIZE
        osc[0:5] = [1, 1, 0, 412 >> 7, 412 & 0x7F]
        osc[0x10] = osc[0x11] = 60
        osc[0x2E] = 110
        return sysex.SamplerPad(0, "Break", 1000, 300015, False, 300015, 60, 60, 412, True,
                                osc, [0x22] * sysex.FILTER_SIZE)

    def test_slice_messages_copy_the_source_pad(self):
        source = self.source_pad()
        empty = sysex.SamplerPad(2, "", 0, 0, False, 0, 0, 127, 0, False)  # blocks unknown
        msgs = sysex.slice_messages(empty, source, 45100, 89200, "Break 3")
        osc = {m[8]: m[9:-1] for m in msgs if m[6:8] == [0x41, 0x2A]}
        self.assertEqual(osc[sysex.WAVE_NUMBER], [412 >> 7, 412 & 0x7F])
        self.assertEqual((osc[0x10], osc[0x11]), ([62], [62]))     # its own note
        self.assertEqual(osc[0x2E], [110])                           # source's level
        self.assertEqual(len(osc), len(sysex.OSCILLATOR_PARAMS))     # unknown: send all
        filt = [m for m in msgs if m[6:8] == [0x42, 0x2A]]
        self.assertEqual(len(filt), len(sysex.FILTER_PARAMS))
        assign = [i for i, m in enumerate(msgs) if m[6:9] == [0x41, 0x2A, 0x00]]
        self.assertEqual(len(assign), 1)
        self.assertEqual(msgs[assign[0]][9], 1)
        self.assertTrue(all(m[6] == 0x50 for m in msgs[assign[0] + 1:]))  # then the sample
        by_offset = {m[8]: m[9:-1] for m in msgs if m[6] == 0x50}
        self.assertEqual(by_offset[sysex.LOOP_ON], [0])
        self.assertEqual(sysex.decode_point(by_offset[sysex.LOOP_LENGTH]), 300015)
        self.assertEqual(sysex.decode_point(by_offset[sysex.START_POINT]), 45100)
        self.assertEqual(sysex.decode_point(by_offset[sysex.END_POINT]),
                         sysex.end_point_for(89200, 300015))
        with self.assertRaises(ValueError):
            sysex.slice_messages(empty, sysex.SamplerPad(0, "x", 0, 99, False, 99), 0, 50, "x")

    def test_slice_messages_send_only_what_differs(self):
        source = self.source_pad()
        osc = list(source.osc)
        osc[0x10] = osc[0x11] = 61
        osc[0x2E] = 90
        target = sysex.SamplerPad(1, "Vox", 0, 88207, False, 88207, 61, 61, 412, True,
                                  osc, list(source.filt))
        msgs = sysex.copy_sound_messages(target, source)
        self.assertEqual([m[6:10] for m in msgs],
                         [[0x41, 0x1A, 0x2E, 110], [0x41, 0x1A, 0x00, 0x01]])

    def test_restore_puts_wave_back_or_switches_pad_off(self):
        now = sysex.SamplerPad(2, "Break 3", 45100, 89207, False, 300015, 62, 62, 412, True)
        before = sysex.SamplerPad(2, "Vox", 0, 88207, False, 88207, 62, 62, 401, True)
        msgs = sysex.restore_messages(now, before)
        self.assertIn(sysex.wave_messages(2, 401)[1], msgs)
        empty = sysex.SamplerPad(2, "", 0, 0, False, 0, 0, 127, 0, False)
        msgs = sysex.restore_messages(now, empty)
        self.assertIn(sysex.parameter_change(sysex.oscillator_address(2, 0), [0]), msgs)

    def test_restore_puts_whole_blocks_back(self):
        source = self.source_pad()
        now = sysex.SamplerPad(2, "Break 3", 45100, 89207, False, 300015, 62, 62, 412, True,
                               list(source.osc), list(source.filt))
        now.osc[0x10] = now.osc[0x11] = 62
        empty_osc = [0] * sysex.OSCILLATOR_SIZE
        empty_osc[0x11] = 127
        empty_osc[0x2E] = 0x40
        empty_filt = [0x15] + [0x40] * (sysex.FILTER_SIZE - 1)
        empty = sysex.SamplerPad(2, "", 0, 0, False, 0, 0, 127, 0, False,
                                 empty_osc, empty_filt)
        msgs = sysex.restore_messages(now, empty)
        changes = {(m[6], m[8]): m[9:-1] for m in msgs if m[6] in (0x41, 0x42)}
        self.assertEqual(changes[(0x41, 0x2E)], [0x40])
        self.assertEqual(changes[(0x41, 0x10)], [0])
        self.assertEqual(changes[(0x42, 0x00)], [0x15])
        self.assertEqual(changes[(0x41, 0x00)], [0])                 # switched off
        self.assertNotIn((0x41, sysex.WAVE_NUMBER), changes)        # wave 0 isn't valid


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
