package io.github.rechandler.seqtrakprobe

import kotlin.math.abs
import kotlin.math.log10
import kotlin.math.max

/** A stereo recording: interleaved 16-bit L/R samples. */
class Capture(
    val samples: ShortArray,
    val rate: Int,
    /** Where the note-on landed in the recording, in frames. */
    val noteFrame: Int,
    /** How [noteFrame] was worked out: "timestamp" (AudioRecord's clock) or "estimate". */
    val noteTiming: String,
) {
    val frames get() = samples.size / 2
    val seconds get() = frames / rate.toDouble()
}

class CaptureReport(
    val peakDbfs: Double,
    /** First frame with sound at or after the note, or -1 if there's none. */
    val onsetFrame: Int,
    val latencyMs: Double?,
    val stereo: Boolean,
) {
    val heardSound get() = onsetFrame >= 0
}

object Analysis {
    /** -40 dBFS: quiet enough to catch a soft attack, loud enough to skip USB noise. */
    const val ONSET_LEVEL = 328

    fun analyze(c: Capture): CaptureReport {
        var peak = 0
        var stereo = false
        var onset = -1
        val from = max(0, c.noteFrame)
        for (f in 0 until c.frames) {
            val l = c.samples[2 * f].toInt()
            val r = c.samples[2 * f + 1].toInt()
            val level = max(abs(l), abs(r))
            if (level > peak) peak = level
            if (!stereo && abs(l - r) > ONSET_LEVEL) stereo = true
            if (onset < 0 && f >= from && level >= ONSET_LEVEL) onset = f
        }
        val peakDbfs = if (peak == 0) Double.NEGATIVE_INFINITY else 20 * log10(peak / 32768.0)
        val latency = if (onset >= 0) (onset - c.noteFrame) * 1000.0 / c.rate else null
        return CaptureReport(peakDbfs, onset, latency, stereo)
    }
}
