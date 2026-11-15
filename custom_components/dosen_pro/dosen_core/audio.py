"""Speaker (talk) session over DVRIP: a dedicated connection, login, OPTalk Claim+Start, then OPTalkData packets
(go2rtc backchannel.go). A drain thread reads and discards anything the camera sends back on the talk connection.
(The microphone is no longer read through DVRIP: Zalo Calling takes the camera's own sound from its RTSP stream.)
"""

import threading
import time

from . import g711
from .dvrip import DVRIPClient, DVRIPError


class TalkSession:
    """Speak out of the camera speaker via DVRIP talkback (OPTalkData)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.client: DVRIPClient | None = None
        self.running = False
        self.packets_sent = 0
        self.bytes_received = 0
        self.started_at: float | None = None
        self.last_error: str | None = None
        self._drain_stop = threading.Event()
        self._drain_thread: threading.Thread | None = None

    def start(self) -> None:
        if self.running:
            return
        if not self.cfg.cam_ip:
            raise DVRIPError("ICSEE_CAM_IP is not set")
        client = DVRIPClient(self.cfg.cam_ip, self.cfg.cam_port,
                             self.cfg.dvrip_connect_timeout,
                             self.cfg.dvrip_read_timeout)
        try:
            client.connect()
            client.login(self.cfg.cam_user, self.cfg.cam_pass)
            client.talk_claim_start()
            client.start_keepalive()
        except Exception:
            client.close()
            raise
        self.client = client
        self._drain_stop.clear()
        self._drain_thread = threading.Thread(
            target=client.drain_forever, args=(self._drain_stop,),
            daemon=True, name="talk-drain")
        self._drain_thread.start()
        self.running = True
        self.started_at = time.time()
        self.last_error = None

    def send_pcm16(self, pcm: bytes) -> int:
        """Send PCM16 8 kHz mono bytes to the speaker. Returns packets sent."""
        if not self.running or self.client is None:
            raise DVRIPError("talk session is not running")
        if len(pcm) % 2:
            raise ValueError("PCM16 input must have an even number of bytes")
        self.bytes_received += len(pcm)
        packets = self.client.talk_send(g711.alaw_encode(pcm))
        self.packets_sent += packets
        return packets

    def send_g711a(self, data: bytes) -> int:
        """Send raw G.711 A-law bytes to the speaker. Returns packets sent."""
        if not self.running or self.client is None:
            raise DVRIPError("talk session is not running")
        self.bytes_received += len(data)
        packets = self.client.talk_send(data)
        self.packets_sent += packets
        return packets

    def stop(self) -> None:
        self._drain_stop.set()
        if self.client is not None:
            self.client.close()
            self.client = None
        if self._drain_thread is not None:
            self._drain_thread.join(timeout=3)
        self.running = False

    def status(self) -> dict:
        return {
            "running": self.running,
            "packets_sent": self.packets_sent,
            "bytes_received": self.bytes_received,
            "started_at": self.started_at,
            "last_error": self.last_error,
        }
