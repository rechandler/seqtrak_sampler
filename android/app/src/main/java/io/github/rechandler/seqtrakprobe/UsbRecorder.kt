package io.github.rechandler.seqtrakprobe

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTimestamp
import android.media.MediaRecorder

/** Records the SEQTRAK's USB audio while it plays a pad. */
class UsbRecorder(context: Context) {
    private val audio = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager

    class Result(val capture: Capture?, val routedTo: String?, val source: String, val error: String?)

    fun inputs(): List<AudioDeviceInfo> = audio.getDevices(AudioManager.GET_DEVICES_INPUTS).toList()

    fun findUsbInput(): AudioDeviceInfo? = inputs().firstOrNull { it.type in USB_TYPES }

    fun describe(d: AudioDeviceInfo): String {
        val type = when (d.type) {
            AudioDeviceInfo.TYPE_USB_DEVICE -> "USB device"
            AudioDeviceInfo.TYPE_USB_HEADSET -> "USB headset"
            AudioDeviceInfo.TYPE_USB_ACCESSORY -> "USB accessory"
            AudioDeviceInfo.TYPE_BUILTIN_MIC -> "built-in mic"
            AudioDeviceInfo.TYPE_TELEPHONY -> "telephony"
            AudioDeviceInfo.TYPE_BLUETOOTH_SCO -> "Bluetooth SCO"
            else -> "type ${d.type}"
        }
        val rates = d.sampleRates.joinToString("/").ifEmpty { "any" }
        val channels = d.channelCounts.joinToString("/").ifEmpty { "any" }
        return "${d.productName} ($type, rates $rates Hz, channels $channels)"
    }

    /**
     * Record [preroll] + [noteFrames] + [tail] frames from [device], playing the
     * note through [noteOn]/[noteOff] once [preroll] frames are in. Blocking: call
     * it off the main thread.
     */
    @SuppressLint("MissingPermission")  // MainActivity asks for RECORD_AUDIO first
    fun record(
        device: AudioDeviceInfo,
        noteFrames: Int,
        noteOn: () -> Unit,
        noteOff: () -> Unit,
        preroll: Int = RATE * 3 / 10,
        tail: Int = RATE * 7 / 10,
    ): Result {
        val unprocessed = audio.getProperty(AudioManager.PROPERTY_SUPPORT_AUDIO_SOURCE_UNPROCESSED) == "true"
        val source = if (unprocessed) MediaRecorder.AudioSource.UNPROCESSED
        else MediaRecorder.AudioSource.VOICE_RECOGNITION
        val sourceName = if (unprocessed) "UNPROCESSED" else "VOICE_RECOGNITION"
        val minBuf = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_STEREO, AudioFormat.ENCODING_PCM_16BIT)
        if (minBuf <= 0) return Result(null, null, sourceName, "44.1 kHz stereo 16-bit recording isn't supported (code $minBuf)")

        val rec = try {
            AudioRecord.Builder()
                .setAudioSource(source)
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(RATE)
                        .setChannelMask(AudioFormat.CHANNEL_IN_STEREO)
                        .build()
                )
                .setBufferSizeInBytes(maxOf(minBuf * 4, RATE * 4 / 2))
                .build()
        } catch (e: Exception) {
            return Result(null, null, sourceName, "Couldn't create the recorder: ${e.message}")
        }
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            rec.release()
            return Result(null, null, sourceName, "The recorder didn't initialize")
        }
        rec.setPreferredDevice(device)

        val total = preroll + noteFrames + tail
        val samples = ShortArray(total * 2)
        val chunk = ShortArray(minBuf)
        var frames = 0
        var noteFrame = -1
        var timing = "estimate"
        var noteSent = false
        var offSent = false
        var routed: String? = null
        val ts = AudioTimestamp()
        try {
            rec.startRecording()
            if (rec.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
                return Result(null, null, sourceName, "Recording didn't start (is another app using the input?)")
            }
            while (frames < total) {
                val n = rec.read(chunk, 0, minOf(chunk.size, (total - frames) * 2))
                if (n < 0) return Result(null, routed, sourceName, "Read failed (code $n)")
                System.arraycopy(chunk, 0, samples, frames * 2, n)
                frames += n / 2
                if (routed == null) rec.routedDevice?.let { routed = describe(it) }

                if (!noteSent && frames >= preroll) {
                    noteOn()
                    val sentAt = System.nanoTime()
                    noteSent = true
                    // AudioRecord's timestamp ties a frame to the same monotonic clock
                    // as System.nanoTime, so the note can be placed to the frame.
                    noteFrame = if (rec.getTimestamp(ts, AudioTimestamp.TIMEBASE_MONOTONIC) == AudioRecord.SUCCESS) {
                        timing = "timestamp"
                        (ts.framePosition + (sentAt - ts.nanoTime) * RATE / 1_000_000_000L).toInt()
                    } else {
                        frames  // what we've read so far; later than the truth by the buffered audio
                    }
                }
                if (noteSent && !offSent && frames >= noteFrame + noteFrames) {
                    noteOff()
                    offSent = true
                }
            }
        } catch (e: Exception) {
            return Result(null, routed, sourceName, "Recording failed: ${e.message}")
        } finally {
            if (noteSent && !offSent) noteOff()
            try {
                rec.stop()
            } catch (_: Exception) {
            }
            rec.release()
        }
        return Result(Capture(samples, RATE, noteFrame, timing), routed, sourceName, null)
    }

    companion object {
        const val RATE = Sysex.RATE
        val USB_TYPES = setOf(
            AudioDeviceInfo.TYPE_USB_DEVICE,
            AudioDeviceInfo.TYPE_USB_HEADSET,
            AudioDeviceInfo.TYPE_USB_ACCESSORY,
        )
    }
}
