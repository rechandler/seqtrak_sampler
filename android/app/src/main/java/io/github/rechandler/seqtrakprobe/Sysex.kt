package io.github.rechandler.seqtrakprobe

import java.util.Locale

/**
 * The SEQTRAK SysEx messages the probe needs, from Yamaha's SEQTRAK Data List.
 * A Kotlin port of the matching parts of src/seqtrak_sampler/sysex.py.
 */
object Sysex {
    const val YAMAHA = 0x43
    const val MODEL = 0x0C
    const val SAMPLER_PART = 10
    const val PADS = 7
    const val RATE = 44100
    const val SAMPLER_CHANNEL = 10      // MIDI channel 11
    const val BASE_NOTE = 60            // pad n plays note 60 + n

    // SAMPLER Sample General block, 50 eA 00
    const val SAMPLE_GENERAL_HI = 0x50
    const val NAME_SIZE = 0x58
    const val LOOP_ON = 0x5F
    const val LOOP_LENGTH = 0x60
    const val START_POINT = 0x66
    const val END_POINT = 0x6A

    // Oscillator block, 41 eA 00
    const val OSCILLATOR_HI = 0x41
    const val WAVE_NUMBER = 0x03
    const val NOTE_LIMIT_LOW = 0x10
    const val NOTE_LIMIT_HIGH = 0x11

    fun identityRequest() = intArrayOf(0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7)

    /** The SEQTRAK OS version (e.g. "2.0") if [m] is its Identity Reply. */
    fun parseIdentityReply(m: IntArray): String? {
        if (m.size >= 15 && m[0] == 0xF0 && m[1] == 0x7E && m[3] == 0x06 && m[4] == 0x02 &&
            m[5] == YAMAHA && m[8] == 0x64 && m[9] == 0x06
        ) {
            return String.format(Locale.US, "%.1f", 1 + m[10] / 10.0)
        }
        return null
    }

    fun bulkRequest(ah: Int, am: Int, al: Int) =
        intArrayOf(0xF0, YAMAHA, 0x20, 0x7F, 0x1C, MODEL, ah, am, al, 0xF7)

    fun parameterChange(ah: Int, am: Int, al: Int, vararg data: Int): IntArray {
        require((intArrayOf(ah, am, al) + data).all { it in 0..0x7F }) { "SysEx data bytes must be 0-127" }
        return intArrayOf(0xF0, YAMAHA, 0x10, 0x7F, 0x1C, MODEL, ah, am, al, *data, 0xF7)
    }

    /** System "USB MIDI Transmit System Exclusive Message" = On (off by default). */
    fun enableUsbSysex() = parameterChange(0x00, 0x00, 0x10, 0x01)

    /** Bulk request for pad [pad] of the current project (11 13 nn). */
    fun padRequest(pad: Int) = bulkRequest(0x11, 0x13, pad)

    fun noteOn(note: Int) = intArrayOf(0x90 or SAMPLER_CHANNEL, note, 100)
    fun noteOff(note: Int) = intArrayOf(0x80 or SAMPLER_CHANNEL, note, 0)

    class Block(val ah: Int, val am: Int, val al: Int, val data: IntArray)

    /**
     * Parse F0 43 0n 7F 1C bh bl 0C ah am al data... cc F7. A real SEQTRAK sums
     * Model ID, address, data and checksum to 0 in the low 7 bits.
     */
    fun parseBulkDump(m: IntArray): Block? {
        if (m.size < 13 || m[0] != 0xF0 || m[m.size - 1] != 0xF7) return null
        if (m[1] != YAMAHA || (m[2] and 0xF0) != 0x00) return null
        if (m[3] != 0x7F || m[4] != 0x1C || m[7] != MODEL) return null
        var sum = 0
        for (i in 7 until m.size - 1) sum += m[i]
        if (sum and 0x7F != 0) return null
        return Block(m[8], m[9], m[10], m.copyOfRange(11, m.size - 2))
    }

    /** Yamaha 7-bit packing: each group is one byte of high bits, then up to 7 bytes. */
    fun decode7bit(data: IntArray): ByteArray {
        val out = ArrayList<Byte>()
        var i = 0
        while (i < data.size) {
            val msbs = data[i]
            for (j in 0 until 7) {
                if (i + 1 + j >= data.size) break
                out.add((data[i + 1 + j] or (((msbs shr (6 - j)) and 1) shl 7)).toByte())
            }
            i += 8
        }
        return out.toByteArray()
    }

    fun decodeName(data: IntArray): String {
        val raw = decode7bit(data)
        val end = raw.indexOf(0).let { if (it < 0) raw.size else it }
        return String(raw, 0, end, Charsets.UTF_8)
    }

    /** 4-byte 24-bit value: 1st bit2-0 -> bit23-21, then 7 bits per byte. */
    fun decodePoint(d: IntArray, at: Int): Int =
        ((d[at] and 0x07) shl 21) or (d[at + 1] shl 14) or (d[at + 2] shl 7) or d[at + 3]

    fun hex(m: IntArray, max: Int = 24): String {
        val shown = m.take(max).joinToString(" ") { String.format(Locale.US, "%02X", it) }
        return if (m.size > max) "$shown … (${m.size} bytes)" else shown
    }
}

data class Pad(
    val index: Int,
    val name: String,
    val start: Int,
    val end: Int,
    val loopOn: Boolean,
    val loopLength: Int,
) {
    val empty get() = end == 0
    val frames get() = if (empty) 0 else end - start + 1
    val seconds get() = frames / Sysex.RATE.toDouble()
}

data class PadOscillator(val index: Int, val noteLow: Int, val noteHigh: Int, val wave: Int)

fun padFromBlock(b: Sysex.Block): Pad? {
    if (b.ah != Sysex.SAMPLE_GENERAL_HI || b.al != 0 || (b.am and 0x0F) != Sysex.SAMPLER_PART) return null
    val pad = b.am shr 4
    val d = b.data
    if (pad >= Sysex.PADS || d.size < Sysex.END_POINT + 4) return null
    return Pad(
        index = pad,
        name = Sysex.decodeName(d.copyOfRange(0, Sysex.NAME_SIZE)),
        start = Sysex.decodePoint(d, Sysex.START_POINT),
        end = Sysex.decodePoint(d, Sysex.END_POINT),
        loopOn = d[Sysex.LOOP_ON] != 0,
        loopLength = Sysex.decodePoint(d, Sysex.LOOP_LENGTH),
    )
}

fun oscillatorFromBlock(b: Sysex.Block): PadOscillator? {
    if (b.ah != Sysex.OSCILLATOR_HI || b.al != 0 || (b.am and 0x0F) != Sysex.SAMPLER_PART) return null
    val pad = b.am shr 4
    val d = b.data
    if (pad >= Sysex.PADS || d.size <= Sysex.NOTE_LIMIT_HIGH) return null
    return PadOscillator(
        pad, d[Sysex.NOTE_LIMIT_LOW], d[Sysex.NOTE_LIMIT_HIGH],
        (d[Sysex.WAVE_NUMBER] shl 7) or d[Sysex.WAVE_NUMBER + 1],
    )
}

/**
 * Turns the MIDI byte stream into whole SysEx messages. Android can deliver a
 * long SysEx in several pieces, with real-time bytes (clock, active sensing)
 * mixed in; everything except SysEx is dropped.
 */
class SysexAssembler {
    private val buf = ArrayList<Int>()
    private var inSysex = false

    fun feed(bytes: ByteArray, offset: Int, count: Int): List<IntArray> {
        val out = ArrayList<IntArray>()
        for (i in offset until offset + count) {
            val b = bytes[i].toInt() and 0xFF
            when {
                b >= 0xF8 -> {}                          // real-time: may appear anywhere
                b == 0xF0 -> { buf.clear(); buf.add(b); inSysex = true }
                !inSysex -> {}
                b == 0xF7 -> { buf.add(b); out.add(buf.toIntArray()); buf.clear(); inSysex = false }
                b >= 0x80 -> { buf.clear(); inSysex = false }   // a status byte cuts it short
                else -> buf.add(b)
            }
        }
        return out
    }
}
