"""Talks to a connected SEQTRAK over USB MIDI: presence, sampler pads, transport."""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, replace

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication

from .. import sysex

PORT_SCAN_MS = 2000
PAD_POLL_MS = 3000
IDENTITY_TIMEOUT_MS = 2500
PORT_NAME = "SEQTRAK"
LOG_SIZE = 1500
# The official SEQTRAK app waits 2 ms after every message it sends (per the
# community reference github.com/jaeggerr/seqtrak-docs, docs/07-transports.md).
# Parameter changes aren't acknowledged, so a burst that's too fast can lose
# messages without anyone noticing.
SEND_PACE_S = 0.002
STORED_REPLY_MS = 1500   # how long to wait for one stored element's dump

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


@dataclass
class StoredElement:
    """A sampler element kept in the SEQTRAK's user memory, as read back."""
    bank: int                        # user bank 1-8
    number: int                      # 0-127
    replied: bool = False
    name: str = ""
    start: int = 0
    end: int = 0
    loop_on: bool = False
    loop_length: int = 0
    wave: int | None = None
    assigned: bool | None = None
    level: int | None = None

    @property
    def empty(self) -> bool:
        return self.end == 0

    @property
    def label(self) -> str:
        owner = sysex.temporary_slot_owner(self.number) if self.bank == 8 else None
        where = f"User {self.bank} #{self.number}"
        if owner is None:
            return where
        return f"Project {owner[0] + 1} pad {owner[1] + 1} ({where})"


class Device(QObject):
    status = Signal(str, str)        # state, detail
    padsChanged = Signal(object)     # list[SamplerPad]
    transportStarted = Signal()
    transportStopped = Signal()
    storedProgress = Signal(int, int)   # done, total
    storedRead = Signal(object)         # list[StoredElement], when a read finishes
    pace = SEND_PACE_S
    _incoming = Signal(object)       # raw MIDI from rtmidi's thread

    def __init__(self, parent=None, rtmidi=None):
        super().__init__(parent)
        self._rtmidi = rtmidi or _load_rtmidi()
        self._in = self._out = None
        self._port_name: str | None = None
        self.state = UNAVAILABLE if self._rtmidi is None else DISCONNECTED
        self.version = ""
        self.pads: dict[int, sysex.SamplerPad] = {}
        self._osc: dict[int, sysex.PadOscillator] = {}
        self._filt: dict[int, list[int]] = {}
        self.log: deque[str] = deque(maxlen=LOG_SIZE)
        self._incoming.connect(self._handle)
        self._sendq: queue.Queue = queue.Queue()
        self._sender: threading.Thread | None = None
        self._bulk: tuple[int, int] | None = None     # (mm, nn) of the open bulk header
        self._stored_todo: list[tuple[int, int]] = []
        self._stored: list[StoredElement] = []
        self._stored_total = 0
        self._stored_current: StoredElement | None = None
        self._stored_timer = QTimer(self)
        self._stored_timer.setSingleShot(True)
        self._stored_timer.setInterval(STORED_REPLY_MS)
        self._stored_timer.timeout.connect(self._next_stored)

        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(PORT_SCAN_MS)
        self._scan_timer.timeout.connect(self._scan)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(PAD_POLL_MS)
        self._poll_timer.timeout.connect(self._poll)
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
        self.log.append(f"{time.strftime('%H:%M:%S')} state {state} {detail}".rstrip())
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
        self._drain_sends()
        if self.reading_stored:
            self._finish_stored()
        if self.pads:
            self.pads = {}
            self._osc = {}
            self._filt = {}
            self.padsChanged.emit([])

    # Messages

    def _log(self, direction: str, msg: list[int], note: str = "") -> None:
        stamp = time.strftime("%H:%M:%S")
        text = " ".join(f"{b:02X}" for b in msg[:160])
        if len(msg) > 160:
            text += f" ... ({len(msg)} bytes)"
        self.log.append(f"{stamp} {direction} {text}{'  ' + note if note else ''}")

    def send(self, msg: list[int]) -> None:
        """Send a message. Messages go out in order, `pace` seconds apart, from a
        background thread, so a long burst doesn't freeze the window."""
        if self._out is None:
            return
        self._log("->", msg)
        if self.pace <= 0:
            self._out.send_message(msg)
            return
        if self._sender is None or not self._sender.is_alive():
            self._sender = threading.Thread(target=self._send_loop, name="seqtrak-send",
                                            daemon=True)
            self._sender.start()
        self._sendq.put((self._out, msg))

    def _send_loop(self) -> None:
        while True:
            out, msg = self._sendq.get()
            try:
                out.send_message(msg)
            except Exception:   # the port was closed under us; nothing to do
                pass
            finally:
                self._sendq.task_done()
            time.sleep(self.pace)

    @property
    def sending(self) -> int:
        """Messages still waiting to go out."""
        return self._sendq.unfinished_tasks

    def flush(self, timeout: float = 10.0) -> bool:
        """Wait until every queued message has been sent. True if it got there."""
        deadline = time.monotonic() + timeout
        while self._sendq.unfinished_tasks:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.001)
        return True

    def _drain_sends(self) -> None:
        while True:
            try:
                self._sendq.get_nowait()
            except queue.Empty:
                return
            self._sendq.task_done()

    def enable_sysex(self) -> None:
        """Turn on the SEQTRAK's "USB MIDI Transmit System Exclusive" setting."""
        self.send(sysex.enable_usb_sysex_transmit())
        QTimer.singleShot(300, self.probe)

    def request_pads(self) -> None:
        # Two documented ways to read the pads; whichever the SEQTRAK answers fills
        # them in (repeats are harmless).
        for pad in range(sysex.SAMPLER_PADS):
            self.send(sysex.pad_request(pad))
        self.send(sysex.sampler_sound_request())

    def _poll(self) -> None:
        # Only poll while this app is in front: in the background the user may be
        # importing in the SEQTRAK app, and our requests could get in its way.
        # Not while reading stored elements either: their dumps use pad 1's
        # addresses, so pad replies mixed in would be ambiguous.
        if self.reading_stored or self.sending:
            return
        app = QGuiApplication.instance()
        if app is None or app.applicationState() == Qt.ApplicationActive:
            self.request_pads()

    # Stored sampler elements (read-only)

    @property
    def reading_stored(self) -> bool:
        return self._stored_current is not None or bool(self._stored_todo)

    def read_stored(self, slots: list[tuple[int, int]] | None = None) -> None:
        """Read stored sampler elements one by one, (bank, number) each; by default
        every project's pad recordings (User 8, 72-127). Emits storedRead at the end."""
        if self.state != CONNECTED or self.reading_stored:
            return
        if slots is None:
            slots = [(sysex.TEMPORARY_BANK, sysex.temporary_slot(project, pad))
                     for project in range(sysex.PROJECTS) for pad in range(sysex.SAMPLER_PADS)]
        self._stored_todo = list(slots)
        self._stored = []
        self._stored_total = len(slots)
        self.log.append(f"{time.strftime('%H:%M:%S')} reading {len(slots)} stored elements")
        self._next_stored()

    def _next_stored(self) -> None:
        if self._stored_current is not None:
            self._stored.append(self._stored_current)
            self._stored_current = None
        self._bulk = None
        self.storedProgress.emit(len(self._stored), self._stored_total)
        if not self._stored_todo:
            self._finish_stored()
            return
        bank, number = self._stored_todo.pop(0)
        self._stored_current = StoredElement(bank, number)
        self.send(sysex.element_request(bank, number))
        self._stored_timer.start()

    def _finish_stored(self) -> None:
        self._stored_timer.stop()
        if self._stored_current is not None:
            self._stored.append(self._stored_current)
        self._stored_current = None
        self._stored_todo = []
        result, self._stored = self._stored, []
        self.storedRead.emit(result)

    def _stored_block(self, block: sysex.BulkBlock) -> bool:
        """Take a block that belongs to the stored element being read. True if taken."""
        element = self._stored_current
        if element is None or (self._bulk is not None and self._bulk[0] in (
                sysex.SAMPLER_ELEMENT_EDIT_BUFFER, sysex.SOUND_EDIT_BUFFER)):
            return False
        hi, mid, _lo = block.address
        if mid & 0x0F != sysex.SAMPLER_PART:
            return False
        if hi == sysex.OSCILLATOR_HI:
            osc = sysex.pad_oscillator_from_block(block)
            if osc is not None:
                element.wave, element.assigned = osc.wave, osc.assigned
                element.level = osc.data[0x2E] if len(osc.data) > 0x2E else None
        elif hi == sysex.SAMPLE_GENERAL_HI:
            try:
                general = sysex.parse_sample_general(0, block.data)
            except ValueError:
                return True
            element.name, element.start, element.end = general.name, general.start, general.end
            element.loop_on, element.loop_length = general.loop_on, general.loop_length
        elif hi != sysex.FILTER_HI:
            return False
        element.replied = True
        return True

    def play_note(self, note: int, seconds: float) -> None:
        """Hold a sampler note for `seconds` (sampler pads default to gate mode)."""
        self.send(sysex.note_on(note))
        QTimer.singleShot(int(seconds * 1000), lambda: self.send(sysex.note_off(note)))

    def set_points(self, pad: int, start: int, end: int) -> None:
        for msg in sysex.points_messages(pad, start, end):
            self.send(msg)

    def send_trim(self, msgs: list[list[int]], pad: int) -> None:
        for msg in msgs:
            self.send(msg)
        QTimer.singleShot(200, lambda: self.send(sysex.pad_request(pad)))

    def _with_osc(self, pad: sysex.SamplerPad) -> sysex.SamplerPad:
        osc = self._osc.get(pad.pad)
        if osc is None:
            return pad
        return replace(pad, note_low=osc.note_low, note_high=osc.note_high, wave=osc.wave,
                       assigned=osc.assigned, osc=osc.data or None,
                       filt=self._filt.get(pad.pad))

    def _update_known(self, index: int) -> None:
        known = self.pads.get(index)
        if known is not None:
            updated = self._with_osc(known)
            if updated != known:
                self.pads[index] = updated
                self.padsChanged.emit([self.pads[i] for i in sorted(self.pads)])

    def _handle(self, msg: list[int]) -> None:
        if not msg:
            return
        self._log("<-", msg)
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
        if block is None:
            self.log.append(f"   ignored: {sysex.bulk_dump_problem(msg)}")
            return
        hi, mid, lo = block.address
        if hi == sysex.BULK_HEADER:
            self._bulk = (mid, lo)
            return
        if hi == sysex.BULK_FOOTER:
            self._bulk = None
            element = self._stored_current
            if element is not None and mid == sysex.SAMPLER_ELEMENT_USER_1 + element.bank - 1 \
                    and lo == element.number:
                element.replied = True
                self._stored_timer.stop()
                QTimer.singleShot(0, self._next_stored)
            return
        if self._stored_block(block):
            return
        osc = sysex.pad_oscillator_from_block(block)
        if osc is not None:
            self._osc[osc.pad] = osc
            self._update_known(osc.pad)
            return
        filt = sysex.pad_filter_from_block(block)
        if filt is not None:
            self._filt[filt[0]] = filt[1]
            self._update_known(filt[0])
            return
        try:
            pad = sysex.pad_from_block(block)
        except ValueError as e:
            self.log.append(f"   ignored: {e} ({len(block.data)} data bytes)")
            return
        if pad is None:
            return
        pad = self._with_osc(pad)
        if self.state != CONNECTED:
            # A bulk reply proves SysEx works even if the identity reply was missed.
            self._identity_timer.stop()
            self._set_state(CONNECTED, f"OS {self.version}" if self.version else "")
        if self.pads.get(pad.pad) != pad:
            self.pads[pad.pad] = pad
            self.padsChanged.emit([self.pads[i] for i in sorted(self.pads)])
