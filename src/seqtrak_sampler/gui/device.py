"""Talks to a connected SEQTRAK over USB MIDI: presence, sampler pads, transport."""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from .. import sysex

PORT_SCAN_MS = 2000
PAD_POLL_MS = 3000
IDENTITY_TIMEOUT_MS = 2500
PORT_NAME = "SEQTRAK"

# Connection states reported by Device.status
UNAVAILABLE = "unavailable"    # python-rtmidi isn't installed
DISCONNECTED = "disconnected"
BUSY = "busy"                  # port exists but another app holds it (Windows)
SILENT = "silent"              # connected, but the SEQTRAK doesn't answer SysEx
CONNECTED = "connected"


def _load_rtmidi():
    try:
        import rtmidi
    except ImportError:
        return None
    return rtmidi


class Device(QObject):
    status = Signal(str, str)        # state, detail
    padsChanged = Signal(object)     # list[SamplerPad]
    transportStarted = Signal()
    transportStopped = Signal()
    _incoming = Signal(object)       # raw MIDI from rtmidi's thread

    def __init__(self, parent=None, rtmidi=None):
        super().__init__(parent)
        self._rtmidi = rtmidi or _load_rtmidi()
        self._in = self._out = None
        self._port_name: str | None = None
        self.state = UNAVAILABLE if self._rtmidi is None else DISCONNECTED
        self.version = ""
        self.pads: dict[int, sysex.SamplerPad] = {}
        self._incoming.connect(self._handle)

        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(PORT_SCAN_MS)
        self._scan_timer.timeout.connect(self._scan)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(PAD_POLL_MS)
        self._poll_timer.timeout.connect(self.request_pads)
        self._identity_timer = QTimer(self)
        self._identity_timer.setSingleShot(True)
        self._identity_timer.setInterval(IDENTITY_TIMEOUT_MS)
        self._identity_timer.timeout.connect(lambda: self._set_state(SILENT))

    def start(self) -> None:
        if self._rtmidi is None:
            self.status.emit(UNAVAILABLE, "MIDI support isn't installed")
            return
        self._scan()
        self._scan_timer.start()

    def stop(self) -> None:
        self._scan_timer.stop()
        self._close()

    # Connection

    def _set_state(self, state: str, detail: str = "") -> None:
        self.state = state
        if state == CONNECTED:
            self._poll_timer.start()
            self.request_pads()
        else:
            self._poll_timer.stop()
        self.status.emit(state, detail)

    def _find(self, midi) -> int | None:
        try:
            ports = midi.get_ports()
        except Exception:
            return None
        return next((i for i, name in enumerate(ports) if PORT_NAME in name.upper()), None)

    def _scan(self) -> None:
        probe_in = self._in or self._rtmidi.MidiIn()
        index = self._find(probe_in)
        if self._in is not None:
            if index is None:
                self._close()
                self._set_state(DISCONNECTED)
            return
        if index is None:
            if self.state != DISCONNECTED:
                self._set_state(DISCONNECTED)
            return
        try:
            midi_in, midi_out = self._rtmidi.MidiIn(), self._rtmidi.MidiOut()
            out_index = self._find(midi_out)
            if out_index is None:
                raise OSError("no SEQTRAK output port")
            midi_in.open_port(index)
            midi_out.open_port(out_index)
        except Exception as e:  # rtmidi raises its own error types
            if self.state != BUSY:
                self._set_state(BUSY, str(e))
            return
        midi_in.ignore_types(sysex=False, timing=True, active_sense=True)
        midi_in.set_callback(lambda event, _data: self._incoming.emit(event[0]))
        self._in, self._out = midi_in, midi_out
        self._port_name = probe_in.get_ports()[index]
        self.probe()

    def probe(self) -> None:
        """Ask the SEQTRAK to identify itself; no answer means SysEx is off."""
        self.send(sysex.identity_request())
        self._identity_timer.start()

    def _close(self) -> None:
        for port in (self._in, self._out):
            if port is not None:
                try:
                    port.close_port()
                except Exception:
                    pass
        self._in = self._out = None
        self._port_name = None
        self._identity_timer.stop()
        if self.pads:
            self.pads = {}
            self.padsChanged.emit([])

    # Messages

    def send(self, msg: list[int]) -> None:
        if self._out is not None:
            self._out.send_message(msg)

    def enable_sysex(self) -> None:
        """Turn on the SEQTRAK's "USB MIDI Transmit System Exclusive" setting."""
        self.send(sysex.enable_usb_sysex_transmit())
        QTimer.singleShot(300, self.probe)

    def request_pads(self) -> None:
        for pad in range(sysex.SAMPLER_PADS):
            self.send(sysex.pad_request(pad))

    def send_trim(self, msgs: list[list[int]], pad: int) -> None:
        for msg in msgs:
            self.send(msg)
        QTimer.singleShot(200, lambda: self.send(sysex.pad_request(pad)))

    def _handle(self, msg: list[int]) -> None:
        if not msg:
            return
        if msg[0] in (sysex.REALTIME_START, sysex.REALTIME_CONTINUE):
            self.transportStarted.emit()
            return
        if msg[0] == sysex.REALTIME_STOP:
            self.transportStopped.emit()
            return
        if msg[0] != 0xF0:
            return
        version = sysex.parse_identity_reply(msg)
        if version is not None:
            self._identity_timer.stop()
            self.version = version
            if self.state != CONNECTED:
                self._set_state(CONNECTED, f"OS {version}")
            return
        block = sysex.parse_bulk_dump(msg)
        pad = sysex.pad_from_block(block) if block else None
        if pad is None:
            return
        if self.state != CONNECTED:
            # A bulk reply proves SysEx works even if the identity reply was missed.
            self._identity_timer.stop()
            self._set_state(CONNECTED, f"OS {self.version}" if self.version else "")
        if self.pads.get(pad.pad) != pad:
            self.pads[pad.pad] = pad
            self.padsChanged.emit([self.pads[i] for i in sorted(self.pads)])
