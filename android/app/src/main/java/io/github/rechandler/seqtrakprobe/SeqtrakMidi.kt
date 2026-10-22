package io.github.rechandler.seqtrakprobe

import android.content.Context
import android.media.midi.MidiDevice
import android.media.midi.MidiDeviceInfo
import android.media.midi.MidiInputPort
import android.media.midi.MidiManager
import android.media.midi.MidiOutputPort
import android.media.midi.MidiReceiver
import android.os.Handler
import android.os.Looper

/** The SEQTRAK's USB MIDI connection, through Android's own MIDI service. */
class SeqtrakMidi(
    context: Context,
    private val onMessage: (IntArray) -> Unit,
    private val onLog: (String) -> Unit,
) {
    private val main = Handler(Looper.getMainLooper())
    val manager = context.getSystemService(Context.MIDI_SERVICE) as MidiManager?
    private var device: MidiDevice? = null
    private var input: MidiInputPort? = null     // we send to it
    private var output: MidiOutputPort? = null   // it sends to us
    var deviceName: String? = null
        private set

    val connected get() = input != null && output != null

    private val receiver = object : MidiReceiver() {
        private val assembler = SysexAssembler()
        override fun onSend(msg: ByteArray, offset: Int, count: Int, timestamp: Long) {
            for (m in assembler.feed(msg, offset, count)) main.post { onMessage(m) }
        }
    }

    @Suppress("DEPRECATION")  // getDevices(): its replacement needs API 33
    fun devices(): List<MidiDeviceInfo> = manager?.devices?.toList() ?: emptyList()

    fun describe(info: MidiDeviceInfo): String {
        val p = info.properties
        val name = p.getString(MidiDeviceInfo.PROPERTY_NAME)
            ?: p.getString(MidiDeviceInfo.PROPERTY_PRODUCT) ?: "unnamed"
        val type = when (info.type) {
            MidiDeviceInfo.TYPE_USB -> "USB"
            MidiDeviceInfo.TYPE_BLUETOOTH -> "Bluetooth"
            MidiDeviceInfo.TYPE_VIRTUAL -> "virtual"
            else -> "type ${info.type}"
        }
        return "$name ($type, ${info.inputPortCount} in / ${info.outputPortCount} out)"
    }

    /** The SEQTRAK if it's there; otherwise any USB MIDI device with ports both ways. */
    fun findSeqtrak(): MidiDeviceInfo? {
        val usable = devices().filter {
            it.type == MidiDeviceInfo.TYPE_USB && it.inputPortCount > 0 && it.outputPortCount > 0
        }
        return usable.firstOrNull { describe(it).uppercase().contains("SEQTRAK") } ?: usable.firstOrNull()
    }

    fun open(info: MidiDeviceInfo, done: (Boolean) -> Unit) {
        close()
        val mm = manager ?: return done(false)
        mm.openDevice(info, { dev ->
            if (dev == null) {
                onLog("Android couldn't open ${describe(info)}")
                done(false)
                return@openDevice
            }
            device = dev
            input = dev.openInputPort(0)
            output = dev.openOutputPort(0)
            output?.connect(receiver)
            deviceName = describe(info)
            onLog("Opened $deviceName")
            done(connected)
        }, main)
    }

    fun send(msg: IntArray) {
        val port = input ?: return
        val bytes = ByteArray(msg.size) { msg[it].toByte() }
        try {
            port.send(bytes, 0, bytes.size)
        } catch (e: Exception) {
            onLog("Send failed: ${e.message}")
        }
    }

    fun close() {
        try {
            output?.disconnect(receiver)
            output?.close()
            input?.close()
            device?.close()
        } catch (_: Exception) {
        }
        output = null
        input = null
        device = null
        deviceName = null
    }
}
