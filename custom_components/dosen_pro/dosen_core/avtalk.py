"""AVTalk: send video + audio to the camera screen over DVRIP.

The wire format below was decoded from a PCAPdroid capture of a real iCSee
app video call to the DOSEN RPO V1 (15/11/2026). Through the cloud relay
the app speaks plain DVRIP, so the same frames work directly over LAN:

* two connections, like the app: a CONTROL one that logs in, and a MEDIA one
  that only connects and quotes the control connection's SessionID;
* claim   (media connection) opcode 1417, JSON
          {"Name":"AVTalk","AVTalk":{"Action":"Claim","Channel":0,
           "ProType":0,"Video":{"Enc":"H265","W":240,"H":320,"FPS":10},
           "Audio":{"Enc":"g711a","SB":16,"SR":8000}},"SessionID":"0x.."}
* reply   opcode 1418, {"Ret":100}
* start   (control connection) opcode 1415, the same JSON with
          "Action":"Start"; reply 1416 {"AVTalk":{"Channel":0},"Ret":100}.
          Without this the camera takes the media but shows nothing
          (Monitor and Talk have the same Claim + Start pairing).
* media   opcode 1419 (seq 0), one packet per chunk, no reply, no flow
          control, no close message (the app just drops the TCP session):
    key frame   00 00 01 fc | enc(3=H265) fps W/8 H/8 | time u32 LE |
                length u32 LE | Annex-B access unit (VPS SPS PPS IDR)
    delta frame 00 00 01 fd | length u32 LE | Annex-B access unit
    audio       00 00 01 fa | 0e (G.711A) 02 (8 kHz) | length u16 LE |
                320 bytes (40 ms); the app sends silence (0xD5) nonstop
  ``time`` is the sender's local time packed XM-style:
  (year-2000)<<26 | month<<22 | day<<17 | hour<<12 | minute<<6 | second.

Measured on the capture: 240x320 H.265, 10 fps, a key frame about every
37 frames, ~130 kbit/s, 25 audio packets per second.
"""

import queue
import struct
import threading
import time

import json

from .dvrip import (OP_AVTALK_CLAIM, OP_AVTALK_DATA, OP_AVTALK_REPLY,
                    OP_AVTALK_START, OP_AVTALK_START_REPLY, DVRIPClient,
                    DVRIPError)

SILENCE_ALAW = b"\xd5" * 320       # 40 ms of G.711 A-law silence
AUDIO_PERIOD_S = 0.04
_ENC_H265 = 3
_START = b"\x00\x00\x00\x01"


def pack_time(t: float | None = None) -> int:
    """Local time packed the way the camera's media headers carry it."""
    lt = time.localtime(time.time() if t is None else t)
    return (((lt.tm_year - 2000) & 0x3F) << 26 | lt.tm_mon << 22
            | lt.tm_mday << 17 | lt.tm_hour << 12 | lt.tm_min << 6
            | lt.tm_sec)


def key_packet(width: int, height: int, fps: int, ts: int,
               au: bytes) -> bytes:
    if width % 8 or height % 8 or not (8 <= width <= 2040
                                       and 8 <= height <= 2040):
        raise ValueError("width/height must be multiples of 8, <= 2040")
    if not 1 <= fps <= 255:
        raise ValueError("fps must be 1..255")
    return (b"\x00\x00\x01\xfc"
            + bytes([_ENC_H265, fps, width // 8, height // 8])
            + struct.pack("<II", ts, len(au)) + au)


def delta_packet(au: bytes) -> bytes:
    return b"\x00\x00\x01\xfd" + struct.pack("<I", len(au)) + au


def audio_packet(g711: bytes = SILENCE_ALAW) -> bytes:
    return b"\x00\x00\x01\xfa\x0e\x02" + struct.pack("<H", len(g711)) + g711


class HevcSplitter:
    """Annex-B H.265 byte stream -> (is_key, access_unit) per frame.

    Assumes one slice per picture (x265 ``slices=1``, which the pipeline
    sets): a frame ends at its VCL NAL; parameter sets / SEI before it
    travel with it. Key = IRAP picture (NAL types 16..23). Each NAL is
    re-emitted behind a 4-byte start code, like the app does.
    """

    def __init__(self):
        self._buf = bytearray()
        self._pending: list[bytes] = []

    def feed(self, data: bytes) -> list[tuple[bool, bytes]]:
        self._buf += data
        out: list[tuple[bool, bytes]] = []
        while True:
            a = self._buf.find(b"\x00\x00\x01")
            if a < 0:
                # keep a possible partial start code at the tail
                del self._buf[:max(0, len(self._buf) - 2)]
                break
            b = self._buf.find(b"\x00\x00\x01", a + 3)
            if b < 0:
                del self._buf[:a]          # drop leading junk, wait for more
                break
            nal = bytes(self._buf[a + 3:b]).rstrip(b"\x00")
            del self._buf[:b]
            if not nal:
                continue
            self._pending.append(nal)
            nal_type = (nal[0] >> 1) & 0x3F
            if nal_type < 32:                       # VCL: frame complete
                au = b"".join(_START + n for n in self._pending)
                self._pending = []
                out.append((16 <= nal_type <= 23, au))
        return out


def _ret_of(payload: bytes):
    body = payload[:-2] if payload[-2:] == b"\n\x00" else payload
    return json.loads(body.decode("utf-8")).get("Ret")


class AVTalkSession:
    """AVTalk toward the camera: a control connection plus a media link."""

    def __init__(self, host: str, port: int, user: str, password: str,
                 connect_timeout: float = 5.0, read_timeout: float = 10.0):
        self._args = (host, port, connect_timeout, read_timeout)
        self._user, self._password = user, password
        self.control: DVRIPClient | None = None
        self.client: DVRIPClient | None = None        # the media link
        self._drain_stop = threading.Event()

    def _expect(self, client: DVRIPClient, reply_op: int, what: str) -> None:
        for _ in range(8):                 # skip unrelated chunks
            opcode, payload = client.read_chunk()
            if opcode == reply_op:
                ret = _ret_of(payload)
                if ret not in (100, 515):
                    raise DVRIPError(f"camera refused AVTalk {what}: Ret={ret}")
                return
        raise DVRIPError(f"no AVTalk {what} reply (opcode {reply_op})")

    def open(self, width: int, height: int, fps: int) -> None:
        control = DVRIPClient(*self._args)
        media = DVRIPClient(*self._args)
        try:
            control.connect()
            control.login(self._user, self._password)
            control.start_keepalive()
            media.connect()                # no login: the session is quoted
            media.session = control.session
            body = {"Name": "AVTalk",
                    "AVTalk": {"Action": "Claim", "Channel": 0, "ProType": 0,
                               "Video": {"Enc": "H265", "W": width,
                                         "H": height, "FPS": fps},
                               "Audio": {"Enc": "g711a", "SB": 16,
                                         "SR": 8000}},
                    "SessionID": "0x%08X" % control.session}
            media._write_json_cmd(OP_AVTALK_CLAIM, body)
            self._expect(media, OP_AVTALK_REPLY, "claim")
            body["AVTalk"]["Action"] = "Start"
            control._write_json_cmd(OP_AVTALK_START, body)
            self._expect(control, OP_AVTALK_START_REPLY, "start")
            media.start_keepalive()
            self._drain_stop.clear()
            for c, n in ((media, "avtalk-media-drain"),
                         (control, "avtalk-control-drain")):
                threading.Thread(target=c.drain_forever,
                                 args=(self._drain_stop,), daemon=True,
                                 name=n).start()
        except Exception:
            control.close()
            media.close()
            raise
        self.control, self.client = control, media

    def send(self, packet: bytes) -> None:
        assert self.client is not None
        self.client.write_cmd(OP_AVTALK_DATA, packet, seq=0)

    def close(self) -> None:
        self._drain_stop.set()
        for c in (self.client, self.control):
            if c is not None:
                c.close()
        self.client = self.control = None


class AVTalkSender:
    """Paces queued access units to the camera, with silent audio between.

    Video goes out at ``fps`` (the camera screen is a 10 fps display), audio
    at 25 packets/s. If the producer outruns the camera the queue is cleared
    and delta frames are skipped until the next key frame, so the decoder is
    never fed frames whose reference is missing.
    """

    def __init__(self, session: AVTalkSession, width: int, height: int,
                 fps: int, max_queue: int = 60):
        self.session = session
        self.width, self.height, self.fps = width, height, fps
        self._q: queue.Queue = queue.Queue(maxsize=max_queue)
        # Audio to the camera speaker while the screen is open: the camera gives the
        # speaker to AVTalk (a separate OPTalk claim gets Ret 503), so sound travels
        # in AVTalk's own audio channel, in place of the silence sent by default.
        self._aq: queue.Queue = queue.Queue(maxsize=50)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._need_key = True
        self.running = False
        self.video_sent = 0
        self.key_sent = 0
        self.audio_sent = 0
        self.bytes_sent = 0
        self.queue_overflows = 0
        self.last_error: str | None = None

    def start(self) -> None:
        self._stop.clear()
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="avtalk-sender")
        self._thread.start()

    def push(self, is_key: bool, au: bytes) -> None:
        try:
            self._q.put_nowait((is_key, au))
        except queue.Full:
            self.queue_overflows += 1
            while True:                      # drop everything queued
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    break
            self._need_key = True
            if is_key:
                self._q.put_nowait((is_key, au))

    def push_audio(self, g711: bytes, timeout: float = 1.0) -> None:
        """Queue G.711 A-law audio (any length) for the camera speaker; blocks while
        the queue is full, so a real-time producer is paced by the 40 ms packet clock."""
        for i in range(0, len(g711), 320):
            self._aq.put(g711[i:i + 320].ljust(320, b"\xd5"), timeout=timeout)

    def reset(self) -> None:
        """Drop queued frames and wait for the next key frame (source switch)."""
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self._need_key = True

    def _send(self, packet: bytes) -> None:
        self.session.send(packet)
        self.bytes_sent += len(packet)

    def _loop(self) -> None:
        period = 1.0 / self.fps
        now = time.monotonic()
        next_video = next_audio = now
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                budget = 5
                while next_audio <= now and budget:
                    try:
                        frame = self._aq.get_nowait()
                    except queue.Empty:
                        frame = SILENCE_ALAW
                    self._send(audio_packet(frame))
                    self.audio_sent += 1
                    next_audio += AUDIO_PERIOD_S
                    budget -= 1
                if next_audio < now - 0.2:
                    next_audio = now
                if now >= next_video:
                    try:
                        is_key, au = self._q.get_nowait()
                    except queue.Empty:
                        is_key = au = None
                    if au is not None:
                        if self._need_key and not is_key:
                            pass             # wait for a key frame
                        else:
                            self._need_key = False
                            if is_key:
                                self._send(key_packet(
                                    self.width, self.height, self.fps,
                                    pack_time(), au))
                                self.key_sent += 1
                            else:
                                self._send(delta_packet(au))
                            self.video_sent += 1
                        next_video = max(next_video + period, now - period)
                time.sleep(0.004)
        except (OSError, DVRIPError) as exc:
            if not self._stop.is_set():
                self.last_error = f"send failed: {exc}"
        finally:
            self.running = False

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self.running = False

    def stats(self) -> dict:
        return {"video_packets": self.video_sent, "key_packets": self.key_sent,
                "audio_packets": self.audio_sent, "bytes": self.bytes_sent,
                "queue_overflows": self.queue_overflows,
                "queued": self._q.qsize(), "last_error": self.last_error}
