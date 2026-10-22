package io.github.rechandler.seqtrakprobe

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.view.View
import kotlin.math.abs
import kotlin.math.max

/** Left channel on top, right below, with the note (orange) and the sound's start (green). */
class WaveformView(context: Context) : View(context) {
    private var capture: Capture? = null
    private var onset = -1
    private val wave = Paint().apply { color = Color.rgb(236, 237, 240) }
    private val axis = Paint().apply { color = Color.rgb(46, 49, 57) }
    private val note = Paint().apply { color = Color.rgb(255, 122, 26); strokeWidth = 3f }
    private val sound = Paint().apply { color = Color.rgb(62, 207, 142); strokeWidth = 3f }

    fun show(c: Capture?, onsetFrame: Int) {
        capture = c
        onset = onsetFrame
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        canvas.drawColor(Color.rgb(25, 26, 31))
        val c = capture ?: return
        val w = width
        val h = height
        if (w == 0 || c.frames == 0) return
        val half = h / 2f
        for (ch in 0..1) {
            val mid = half * ch + half / 2
            canvas.drawLine(0f, mid, w.toFloat(), mid, axis)
            for (x in 0 until w) {
                val from = (x.toLong() * c.frames / w).toInt()
                val to = max(from + 1, ((x + 1).toLong() * c.frames / w).toInt())
                var peak = 0
                for (f in from until minOf(to, c.frames)) peak = max(peak, abs(c.samples[2 * f + ch].toInt()))
                val y = peak / 32768f * (half / 2 - 2)
                canvas.drawLine(x.toFloat(), mid - y, x.toFloat(), mid + y, wave)
            }
        }
        fun mark(frame: Int, paint: Paint) {
            if (frame < 0) return
            val x = frame.toFloat() / c.frames * w
            canvas.drawLine(x, 0f, x, h.toFloat(), paint)
        }
        mark(c.noteFrame, note)
        mark(onset, sound)
    }
}
