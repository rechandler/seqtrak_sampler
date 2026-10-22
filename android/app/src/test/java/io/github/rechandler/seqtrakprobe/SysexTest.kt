package io.github.rechandler.seqtrakprobe

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SysexTest {
    private fun hex(text: String) = text.trim().split(Regex("\\s+")).map { it.toInt(16) }.toIntArray()

    // Messages captured from a SEQTRAK running OS 2.0 (the same ones as tests/test_sysex.py).
    private val pad1 = hex(
        "F0 43 00 7F 1C 00 74 0C 50 0A 00 00 52 65 73 61 6D 70 6C 00 69 6E 67 2D 35 2D 31" +
            " 00".repeat(81) + " 1F 1C 4F 00 00 00 06 37 2C 00 0D 4A 3F 00 00 3F F7"
    )
    private val osc2 = hex(
        "F0 43 00 7F 1C 00 71 0C 41 1A 00 01 01 00 03 16 00 00 00 01 00 00 40 00 40 40 00" +
            " 3D 3D 01 7F 00 00 00 00 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 07" +
            " 0B 00 00 64 54 00 03 00 00 00 7F 7F 32 00 7F 7F 7F 00 04 40 40 18 24 30 3C 48 01" +
            " 00 01 00 01 00 01 00 40 40 40 40 40 00 00 40 3C 40 00 28 40 40 40 01 00 01 00 01" +
            " 00 01 00 01 00 54 04 40 40 02 40 3C 11 F7"
    )

    @Test
    fun readsARealPad() {
        val pad = padFromBlock(Sysex.parseBulkDump(pad1)!!)!!
        assertEquals(0, pad.index)
        assertEquals("Resampling-5-1", pad.name)
        assertEquals(105388, pad.start)
        assertEquals(222527, pad.end)
        assertEquals(222527 - 105388 + 1, pad.frames)
    }

    @Test
    fun readsARealOscillatorBlock() {
        val osc = oscillatorFromBlock(Sysex.parseBulkDump(osc2)!!)!!
        assertEquals(1, osc.index)
        assertEquals(61, osc.noteLow)
        assertEquals(61, osc.noteHigh)
        assertEquals((0x03 shl 7) or 0x16, osc.wave)
    }

    @Test
    fun rejectsBadChecksumAndParameterChanges() {
        val bad = pad1.copyOf().also { it[it.size - 2] = (it[it.size - 2] + 3) and 0x7F }
        assertNull(Sysex.parseBulkDump(bad))
        assertNull(Sysex.parseBulkDump(hex("F0 43 10 7F 1C 0C 01 10 28 00 F7")))
        val header = Sysex.parseBulkDump(hex("F0 43 00 7F 1C 00 04 0C 11 13 00 50 F7"))!!
        assertNull(padFromBlock(header))
    }

    @Test
    fun identityReply() {
        val reply = hex("F0 7E 7F 06 02 43 00 41 64 06 0A 00 00 7F F7")
        assertEquals("2.0", Sysex.parseIdentityReply(reply))
        assertNull(Sysex.parseIdentityReply(pad1))
    }

    @Test
    fun outgoingMessages() {
        assertArrayEquals(hex("F0 43 20 7F 1C 0C 11 13 03 F7"), Sysex.padRequest(3))
        assertArrayEquals(hex("F0 43 10 7F 1C 0C 00 00 10 01 F7"), Sysex.enableUsbSysex())
        assertArrayEquals(intArrayOf(0x9A, 61, 100), Sysex.noteOn(61))
        assertArrayEquals(intArrayOf(0x8A, 61, 0), Sysex.noteOff(61))
    }

    @Test
    fun assemblesSysexSplitAcrossPackets() {
        val a = SysexAssembler()
        val bytes = ByteArray(pad1.size) { pad1[it].toByte() }
        val withClock = bytes.copyOfRange(0, 50) + byteArrayOf(0xF8.toByte(), 0xFE.toByte()) +
            bytes.copyOfRange(50, bytes.size)
        assertTrue(a.feed(withClock, 0, 30).isEmpty())
        val out = a.feed(withClock, 30, withClock.size - 30)
        assertEquals(1, out.size)
        assertArrayEquals(pad1, out[0])
    }

    @Test
    fun dropsSysexCutShortAndNotes() {
        val a = SysexAssembler()
        val bytes = byteArrayOf(0xF0.toByte(), 0x43, 0x00, 0x9A.toByte(), 60, 100,
            0xF0.toByte(), 0x7E, 0xF7.toByte())
        val out = a.feed(bytes, 0, bytes.size)
        assertEquals(1, out.size)
        assertArrayEquals(intArrayOf(0xF0, 0x7E, 0xF7), out[0])
    }

    @Test
    fun findsWhereTheSoundStarts() {
        val rate = 44100
        val note = 13230
        val delay = 882                          // 20 ms
        val frames = note + delay + 44100
        val s = ShortArray(frames * 2)
        for (f in note + delay until frames) {
            s[2 * f] = 8000
            s[2 * f + 1] = (if (f % 2 == 0) 8000 else -8000).toShort()
        }
        s[100] = 2000                            // a click before the note isn't the pad
        val rep = Analysis.analyze(Capture(s, rate, note, "timestamp"))
        assertTrue(rep.heardSound)
        assertEquals(note + delay, rep.onsetFrame)
        assertEquals(20.0, rep.latencyMs!!, 0.01)
        assertTrue(rep.stereo)
        assertEquals(20 * kotlin.math.log10(8000 / 32768.0), rep.peakDbfs, 0.01)
    }

    @Test
    fun silenceHasNoOnset() {
        val rep = Analysis.analyze(Capture(ShortArray(2000), 44100, 100, "estimate"))
        assertFalse(rep.heardSound)
        assertNull(rep.latencyMs)
        assertFalse(rep.stereo)
        assertNotNull(rep.peakDbfs)
    }
}
