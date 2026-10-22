package io.github.rechandler.seqtrakprobe

import android.Manifest
import android.app.Activity
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.media.midi.MidiDeviceInfo
import android.media.midi.MidiManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * A one-screen test of what SEQTRAK Sampler needs from an Android phone over USB:
 * MIDI both ways (to read and play pads) and the SEQTRAK's USB audio (to record them).
 */
class MainActivity : Activity() {
    private val main = Handler(Looper.getMainLooper())
    private lateinit var midi: SeqtrakMidi
    private lateinit var recorder: UsbRecorder

    private enum class Check(val label: String) {
        MIDI("SEQTRAK found over USB MIDI"),
        SYSEX("SEQTRAK answers SysEx"),
        PADS("Pads read from the SEQTRAK"),
        AUDIO_IN("USB audio input found"),
        ROUTED("Recording comes from the USB input"),
        SOUND("Pad sound recorded"),
    }

    private val checks = mutableMapOf<Check, Boolean?>()
    private val checkViews = mutableMapOf<Check, TextView>()
    private val pads = sortedMapOf<Int, Pad>()
    private val notes = mutableMapOf<Int, Int>()
    private val log = ArrayList<String>()
    private var osVersion: String? = null
    private var busy = false

    private lateinit var status: TextView
    private lateinit var enableButton: Button
    private lateinit var padList: LinearLayout
    private lateinit var waveform: WaveformView
    private lateinit var result: TextView
    private lateinit var logView: TextView

    private val deviceCallback = object : MidiManager.DeviceCallback() {
        override fun onDeviceAdded(device: MidiDeviceInfo) {
            addLog("MIDI device added: ${midi.describe(device)}")
            if (!midi.connected) main.postDelayed({ connect() }, 500)
        }

        override fun onDeviceRemoved(device: MidiDeviceInfo) {
            addLog("MIDI device removed: ${midi.describe(device)}")
            if (midi.deviceName == midi.describe(device)) {
                midi.close()
                set(Check.MIDI, false)
                status.text = "SEQTRAK disconnected."
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        midi = SeqtrakMidi(this, ::onMessage, ::addLog)
        recorder = UsbRecorder(this)
        setContentView(buildUi())
        addLog("${Build.MANUFACTURER} ${Build.MODEL}, Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})")
        if (midi.manager == null) {
            status.text = "This phone has no MIDI support, so it can't talk to the SEQTRAK."
            set(Check.MIDI, false)
        } else {
            @Suppress("DEPRECATION")
            midi.manager?.registerDeviceCallback(deviceCallback, main)
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 1)
        }
        connect()
    }

    override fun onDestroy() {
        midi.manager?.unregisterDeviceCallback(deviceCallback)
        midi.close()
        super.onDestroy()
    }

    // Steps

    private fun connect() {
        val info = midi.findSeqtrak()
        val all = midi.devices()
        addLog("MIDI devices: " + if (all.isEmpty()) "none" else all.joinToString("; ") { midi.describe(it) })
        val usb = recorder.findUsbInput()
        addLog("Audio inputs: " + recorder.inputs().joinToString("; ") { recorder.describe(it) })
        set(Check.AUDIO_IN, usb != null)
        if (info == null) {
            set(Check.MIDI, false)
            status.text = "No SEQTRAK found. Connect it with a USB data cable (USB-C to USB-C, " +
                "or an OTG adapter) and switch it on."
            return
        }
        status.text = "Opening ${midi.describe(info)}…"
        midi.open(info) { ok ->
            set(Check.MIDI, ok)
            if (ok) identify() else status.text = "Android found the SEQTRAK but couldn't open it."
        }
    }

    private fun identify() {
        osVersion = null
        status.text = "Asking the SEQTRAK who it is…"
        send(Sysex.identityRequest())
        main.postDelayed({
            if (osVersion == null) {
                set(Check.SYSEX, false)
                enableButton.visibility = View.VISIBLE
                status.text = "The SEQTRAK didn't answer. Its USB SysEx setting is probably off " +
                    "(the default): tap Enable SysEx."
            }
        }, 1500)
    }

    private fun enableSysex() {
        addLog("Turning on USB MIDI Transmit System Exclusive")
        send(Sysex.enableUsbSysex())
        main.postDelayed({ identify() }, 400)
    }

    private fun readPads() {
        pads.clear()
        notes.clear()
        status.text = "Reading pads…"
        for (pad in 0 until Sysex.PADS) main.postDelayed({ send(Sysex.padRequest(pad)) }, pad * 80L)
        main.postDelayed({
            set(Check.PADS, pads.isNotEmpty())
            status.text = if (pads.isEmpty()) "No pad data came back." else
                "Tap a pad with a sample: the SEQTRAK plays it while the phone records its USB audio."
            renderPads()
        }, 2000)
    }

    private fun capture(pad: Pad) {
        if (busy) return
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 1)
            return
        }
        val usb = recorder.findUsbInput()
        set(Check.AUDIO_IN, usb != null)
        if (usb == null) {
            result.text = "This phone doesn't list a USB audio input, so it can't record the SEQTRAK."
            return
        }
        val note = notes[pad.index] ?: (Sysex.BASE_NOTE + pad.index)
        val noteFrames = minOf(pad.frames, 16 * Sysex.RATE)
        busy = true
        renderPads()
        result.text = "Recording pad ${pad.index + 1} through ${recorder.describe(usb)}…"
        addLog("Recording pad ${pad.index + 1} (note $note, ${"%.2f".format(Locale.US, pad.seconds)} s)")
        Thread {
            val r = recorder.record(usb, noteFrames, { send(Sysex.noteOn(note)) }, { send(Sysex.noteOff(note)) })
            main.post { showCapture(pad, r) }
        }.start()
    }

    private fun showCapture(pad: Pad, r: UsbRecorder.Result) {
        busy = false
        renderPads()
        addLog("Recorder source ${r.source}, routed to ${r.routedTo ?: "unknown"}")
        val c = r.capture
        if (c == null) {
            result.text = "Couldn't record: ${r.error}"
            addLog("Recording error: ${r.error}")
            set(Check.SOUND, false)
            return
        }
        val routedUsb = r.routedTo?.contains("(USB") == true
        set(Check.ROUTED, routedUsb)
        val rep = Analysis.analyze(c)
        set(Check.SOUND, rep.heardSound)
        waveform.show(c, rep.onsetFrame)
        val lines = mutableListOf(
            "Pad ${pad.index + 1}: recorded ${"%.2f".format(Locale.US, c.seconds)} s",
            "From: ${r.routedTo ?: "unknown input"}",
            if (rep.peakDbfs.isInfinite()) "Peak: silence" else "Peak: ${"%.1f".format(Locale.US, rep.peakDbfs)} dBFS",
            if (rep.stereo) "Stereo: left and right differ" else "Stereo: left and right are the same",
        )
        lines += if (rep.latencyMs != null) {
            "Sound starts ${"%.0f".format(Locale.US, rep.latencyMs)} ms after the note (${c.noteTiming})"
        } else {
            "No sound after the note. Check the SEQTRAK's volume and that the pad plays."
        }
        result.text = lines.joinToString("\n")
        lines.forEach(::addLog)
    }

    // MIDI

    private fun send(m: IntArray) {
        if (m[0] == 0xF0) addLog("→ ${Sysex.hex(m)}")
        midi.send(m)
    }

    private fun onMessage(m: IntArray) {
        addLog("← ${Sysex.hex(m)}")
        Sysex.parseIdentityReply(m)?.let {
            osVersion = it
            set(Check.SYSEX, true)
            enableButton.visibility = View.GONE
            status.text = "Connected: SEQTRAK OS $it"
            readPads()
            return
        }
        val block = Sysex.parseBulkDump(m) ?: return
        padFromBlock(block)?.let { pads[it.index] = it; renderPads() }
        oscillatorFromBlock(block)?.let { notes[it.index] = it.noteLow }
    }

    // UI

    private fun set(check: Check, ok: Boolean?) {
        checks[check] = ok
        val mark = when (ok) { true -> "✓"; false -> "✗"; null -> "·" }
        checkViews[check]?.apply {
            text = "$mark  ${check.label}"
            setTextColor(when (ok) { true -> GREEN; false -> RED; null -> MUTED })
        }
    }

    private fun renderPads() {
        padList.removeAllViews()
        for (i in 0 until Sysex.PADS) {
            val pad = pads[i]
            val b = Button(this)
            b.isAllCaps = false
            b.gravity = Gravity.START or Gravity.CENTER_VERTICAL
            b.text = when {
                pad == null -> "Pad ${i + 1}"
                pad.empty -> "Pad ${i + 1}  ·  empty"
                else -> "Pad ${i + 1}  ·  ${pad.name}  ·  ${"%.2f".format(Locale.US, pad.seconds)} s"
            }
            b.isEnabled = pad != null && !pad.empty && !busy
            b.setOnClickListener { pad?.let(::capture) }
            padList.addView(b)
        }
    }

    private fun addLog(line: String) {
        val stamp = SimpleDateFormat("HH:mm:ss.SSS", Locale.US).format(Date())
        log += "$stamp  $line"
        if (log.size > 400) log.removeAt(0)
        if (::logView.isInitialized) logView.text = log.takeLast(60).joinToString("\n")
    }

    private fun report(): String = buildString {
        appendLine("SEQTRAK USB test ${BuildConfig.VERSION_NAME}")
        appendLine("${Build.MANUFACTURER} ${Build.MODEL}, Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})")
        appendLine("SEQTRAK OS: ${osVersion ?: "unknown"}")
        appendLine()
        for (c in Check.entries) {
            val mark = when (checks[c]) { true -> "PASS"; false -> "FAIL"; null -> "not tried" }
            appendLine("$mark  ${c.label}")
        }
        appendLine()
        appendLine(result.text)
        appendLine()
        appendLine("Log:")
        log.forEach(::appendLine)
    }

    private fun shareReport() {
        val text = report()
        (getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager)
            .setPrimaryClip(ClipData.newPlainText("SEQTRAK USB test", text))
        Toast.makeText(this, "Report copied", Toast.LENGTH_SHORT).show()
        startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).apply {
            type = "text/plain"
            putExtra(Intent.EXTRA_SUBJECT, "SEQTRAK USB test report")
            putExtra(Intent.EXTRA_TEXT, text)
        }, "Share report"))
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun text(size: Float, color: Int = TEXT, bold: Boolean = false) = TextView(this).apply {
        textSize = size
        setTextColor(color)
        if (bold) typeface = Typeface.DEFAULT_BOLD
        setPadding(0, dp(4), 0, dp(4))
    }

    private fun button(label: String, onClick: () -> Unit) = Button(this).apply {
        text = label
        isAllCaps = false
        setOnClickListener { onClick() }
    }

    private fun buildUi(): View {
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(24), dp(16), dp(24))
        }
        col.addView(text(22f, bold = true).apply { text = "SEQTRAK USB test" })
        col.addView(text(14f, MUTED).apply {
            text = "Checks whether this phone can read, play and record the SEQTRAK's pads over USB. " +
                "Close the SEQTRAK app first. Version ${BuildConfig.VERSION_NAME}."
        })

        for (c in Check.entries) {
            val v = text(16f)
            checkViews[c] = v
            col.addView(v)
            set(c, null)
        }

        status = text(15f, ACCENT).apply { text = "Starting…" }
        col.addView(status)

        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        row.addView(button("Connect") { connect() })
        enableButton = button("Enable SysEx") { enableSysex() }.apply { visibility = View.GONE }
        row.addView(enableButton)
        row.addView(button("Read pads") { if (midi.connected) readPads() else connect() })
        col.addView(row)

        col.addView(text(13f, MUTED, bold = true).apply { text = "PADS" })
        padList = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        col.addView(padList)
        renderPads()

        waveform = WaveformView(this)
        col.addView(waveform, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(160)).apply {
            topMargin = dp(12)
        })
        result = text(14f).apply { text = "Orange line: the note. Green line: where the sound starts." }
        col.addView(result)

        col.addView(button("Share report") { shareReport() })

        col.addView(text(13f, MUTED, bold = true).apply { text = "LOG" })
        logView = text(11f, MUTED).apply { typeface = Typeface.MONOSPACE; setTextIsSelectable(true) }
        col.addView(logView)

        return ScrollView(this).apply {
            setBackgroundColor(BG)
            addView(col)
        }
    }

    companion object {
        val BG = Color.rgb(17, 18, 21)
        val TEXT = Color.rgb(236, 237, 240)
        val MUTED = Color.rgb(139, 144, 156)
        val ACCENT = Color.rgb(255, 122, 26)
        val GREEN = Color.rgb(62, 207, 142)
        val RED = Color.rgb(255, 92, 92)
    }
}
