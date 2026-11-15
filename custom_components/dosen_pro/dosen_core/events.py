"""Button-press / alarm events from the camera over DVRIP (LAN, no cloud).

Receives the camera's push events on a dedicated DVRIP connection:
login, subscribe with opcode 1500 (AlarmSet), then the camera pushes
JSON AlarmInfo chunks with opcode 1504 on that same connection
(python-dvr ``alarmStart`` / ``alarm_thread``; hass-xmeye local_push).
No polling, no FunSDK, no cloud account -- only the camera IP + user /
password already in ``.env``.

What is proven and what is not (BUTTON-PRESS-EVENT.md):
* PROVEN (mechanism): subscribe 1500 + push 1504, read live on an
  NBD8008R-U by hass-xmeye, and in python-dvr source.
* NOT PROVEN (this camera): whether the DOSEN RPO V1 button press
  produces a 1504 at all, and with which ``Event`` name. The strongest
  inference is ``LocalAlarm`` -- the app maps that event to ``Caller``
  and opens the incoming-call screen -- but it is an inference, so
  events are always reported with their raw ``Event`` string and a
  ``possible_button_press`` flag that is labelled as inferred, never
  as a proven button press. A live test (subscribe, press the button,
  record the 1504 payloads) is what settles it; this session is the
  tool for that test.
* There is NO way to poll the button state -- events are push-only.
"""

import collections
import json
import logging
import socket
import threading
import time

from .dvrip import (DVRIPClient, DVRIPError, OK_RET, OP_ALARM_INFO,
                    parse_alarm_info)

log = logging.getLogger("icsee.events")

# Reconnect backoff when the camera drops the connection (seconds).
_RECONNECT_BASE_S = 2.0
_RECONNECT_MAX_S = 30.0
# Keepalive replies arrive every ~30 s as opcode 1007 on the real camera
# (1006 is the request opcode); not worth INFO lines.
_OP_KEEPALIVE = (1006, 1007)

# Events the iCSee app treats as a call/doorbell press. INFERRED ONLY --
# see module docstring; a firmware could use another name, in which case
# the raw Event string in the recorded event is what matters.
_BUTTON_LIKE_EVENTS = ("LocalAlarm",)

_MAX_EVENTS = 200


def describe_chunk(payload: bytes) -> str:
    """Nội dung một gói camera tự đẩy tới, để người đọc thấy được: chữ nếu là JSON/text, không thì hex."""
    body = payload[:-2] if payload[-2:] == b"\n\x00" else payload
    try:
        text = body[:400].decode("utf-8")
        if text.isprintable():
            return text
    except UnicodeDecodeError:
        pass
    return "hex:" + body[:200].hex()


def other_record(source: str, opcode: int, payload: bytes) -> dict:
    """Một gói KHÔNG phải báo động 1504 → bản ghi cùng dạng với báo động, tên sự kiện ``opcode_<số>``, để nút gọi
    (hay bất cứ thứ gì camera đẩy tới bằng đường khác) vẫn hiện ở thực thể «Sự kiện camera» và trên bus HA."""
    return {"event": f"opcode_{opcode}", "possible_button_press": False,
            "alarm": {"Source": source, "Opcode": opcode, "Raw": describe_chunk(payload)}}


class AlarmSession:
    """Listen for DVRIP alarm push events (opcode 1504) on one connection.

    The connection is re-opened (with backoff) when the camera drops it, so a
    long-running listener does not die silently. ``running`` stays True while
    reconnecting; ``connected`` tells whether a live connection exists now.
    """

    def __init__(self, cfg, on_event=None):
        self.cfg = cfg
        self.on_event = on_event
        self.client: DVRIPClient | None = None
        self.running = False
        self.connected = False
        self.events_received = 0
        self.reconnects = 0
        self.started_at: float | None = None
        self.last_error: str | None = None
        self.last_event: dict | None = None
        self._events: collections.deque = collections.deque(maxlen=_MAX_EVENTS)
        self._seq = 0
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def _open(self) -> DVRIPClient:
        """Connect, log in and subscribe on a fresh dedicated connection."""
        client = DVRIPClient(self.cfg.cam_ip, self.cfg.cam_port,
                             self.cfg.dvrip_connect_timeout,
                             self.cfg.dvrip_read_timeout)
        try:
            log.info("alarm: noi toi %s:%s", self.cfg.cam_ip, self.cfg.cam_port)
            client.connect()
            client.login(self.cfg.cam_user, self.cfg.cam_pass)
            log.info("alarm: dang nhap xong, session=0x%08X", client.session)
            client.alarm_subscribe()
            log.info("alarm: da gui dang ky 1500 (AlarmSet)")
            client.start_keepalive()
            # Short read timeout so stop() is responsive (same trick as
            # TalkSession.drain_forever); read_chunk() keeps partial chunks
            # across a timeout, and keepalive replies are ignored in the loop.
            assert client.sock is not None
            client.sock.settimeout(1.0)
        except Exception:
            client.close()
            raise
        return client

    def start(self) -> None:
        # Two concurrent POST /api/events/start run in worker threads: the
        # lock makes the check-then-open atomic (one connection, one reader).
        with self._start_lock:
            if self.running:
                return
            if not self.cfg.cam_ip:
                raise DVRIPError("ICSEE_CAM_IP is not set")
            self.client = self._open()
            self._stop.clear()
            self.running = True
            self.connected = True
            self.reconnects = 0
            self.started_at = time.time()
            self.last_error = None
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="alarm-session")
            self._thread.start()
            log.info("alarm: phien bat dau")

    def _record(self, info: dict) -> None:
        event_name = info.get("Event")
        # Only the START of a LocalAlarm is a possible press: the camera also
        # reports Status "Stop" for the same press, which must not count as a
        # second one. A payload without Status is kept as possible.
        is_start = info.get("Status") != "Stop"
        possible = event_name in _BUTTON_LIKE_EVENTS and is_start
        with self._lock:
            self._seq += 1
            record = {
                "seq": self._seq,
                "received_at": time.time(),
                "opcode": OP_ALARM_INFO,
                "event": event_name,
                "alarm": info,
                # INFERRED, not proven on the DOSEN RPO V1 -- see module
                # docstring. Only LocalAlarm is treated as a possible
                # button press; anything else is reported as-is.
                "possible_button_press": possible,
                "button_press_note": (
                    "suy doan: LocalAlarm duoc app dich thanh Caller va "
                    "mo man hinh goi den; chua chung minh tren RPO V1"
                    if possible else None),
            }
            self._events.append(record)
            self.events_received += 1
            self.last_event = record
        if self.on_event is not None:
            try:
                self.on_event(record)
            except Exception:                              # noqa: BLE001 — người nghe hỏng không được giết luồng đọc
                log.exception("alarm: on_event failed")
        log.info("alarm: event=%s status=%s possible_button_press=%s raw=%s",
                 event_name, info.get("Status"), possible,
                 json.dumps(info, ensure_ascii=False)[:500])

    def _handle_other(self, opcode: int, payload: bytes) -> None:
        """A chunk that is not an AlarmInfo: usually the Ret reply to the 1500
        subscribe. Log it, and surface a rejected subscribe as last_error
        (otherwise the session would look healthy while receiving nothing)."""
        if opcode in _OP_KEEPALIVE:
            log.debug("alarm: keepalive reply")
            return
        text = payload[:200]
        log.info("alarm: goi khac opcode=%s payload=%r", opcode, text)
        if self.on_event is not None:
            try:
                self.on_event(other_record("alarm", opcode, payload))
            except Exception:                              # noqa: BLE001
                log.exception("alarm: on_event failed")
        try:
            body = payload[:-2] if payload[-2:] == b"\n\x00" else payload
            ret = json.loads(body.decode("utf-8")).get("Ret")
        except (ValueError, UnicodeDecodeError, AttributeError):
            return
        if ret is not None and ret not in OK_RET:
            self.last_error = f"camera tra Ret={ret} cho opcode {opcode}"
            log.warning("alarm: %s", self.last_error)

    def _listen(self, client: DVRIPClient) -> str:
        """Read pushes until the connection is lost or stop() is called.
        Returns a short reason for the log."""
        while not self._stop.is_set():
            try:
                opcode, payload = client.read_chunk()
            except socket.timeout:
                continue
            except (OSError, DVRIPError) as exc:
                # A clean stop() closes the socket under the reader,
                # which surfaces as an error -- not a real failure.
                if self._stop.is_set():
                    return "stop"
                self.last_error = str(exc)
                return str(exc)
            if opcode != OP_ALARM_INFO:
                self._handle_other(opcode, payload)
                continue
            info = parse_alarm_info(payload)
            if info is None:
                log.warning("alarm: 1504 khong doc duoc JSON: %r", payload[:200])
                continue
            self._record(info)
        return "stop"

    def _loop(self) -> None:
        backoff = _RECONNECT_BASE_S
        try:
            while not self._stop.is_set():
                client = self.client
                assert client is not None
                reason = self._listen(client)
                self.connected = False
                client.close()
                self.client = None
                if self._stop.is_set():
                    break
                # Connection lost: reopen with exponential backoff.
                while not self._stop.is_set():
                    log.warning("alarm: mat ket noi (%s); noi lai sau %.1fs",
                                reason, backoff)
                    if self._stop.wait(backoff):
                        break
                    try:
                        client = self._open()
                    except (OSError, DVRIPError) as exc:
                        reason = str(exc)
                        self.last_error = reason
                        backoff = min(backoff * 2, _RECONNECT_MAX_S)
                        continue
                    if self._stop.is_set():
                        client.close()
                        break
                    self.client = client
                    self.connected = True
                    self.reconnects += 1
                    backoff = _RECONNECT_BASE_S
                    log.info("alarm: da noi lai (lan %d)", self.reconnects)
                    break
        finally:
            self.running = False
            self.connected = False
            if self.client is not None:
                self.client.close()
                self.client = None
            log.info("alarm: phien ket thuc")

    def recent(self, limit: int = 50) -> list[dict]:
        """Chronological order, oldest first; empty for limit <= 0."""
        if limit <= 0:
            return []
        with self._lock:
            return list(self._events)[-limit:]

    def events_since(self, last_seq: int) -> tuple[list[dict], int]:
        """Events with seq > last_seq, plus the newest seq seen."""
        with self._lock:
            new = [e for e in self._events if e["seq"] > last_seq]
            return new, self._seq

    def stop(self) -> None:
        log.info("alarm: yeu cau dung")
        self._stop.set()
        client = self.client
        if client is not None:
            client.close()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self.running = False
        self.connected = False

    def status(self) -> dict:
        return {
            "running": self.running,
            "connected": self.connected,
            "events_received": self.events_received,
            "reconnects": self.reconnects,
            "started_at": self.started_at,
            "last_event": self.last_event,
            "last_error": self.last_error,
            "note": ("DVRIP alarm push 1500/1504; LocalAlarm Start = possible "
                     "button press (inferred, not proven on RPO V1)"),
        }
