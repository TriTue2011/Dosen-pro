"""Một camera: gom màn hình, loa, mic và luồng báo động quanh MỘT cấu hình kết nối."""

from __future__ import annotations

from .audio import MicSession, TalkSession
from .config import CamConfig
from .dvrip import DVRIPClient
from .events import AlarmSession
from .screen import Screen
from .speaker import Speaker


class Camera:
    def __init__(self, cfg: CamConfig, now=None, on_alarm=None) -> None:
        self.cfg = cfg
        self.talk = TalkSession(cfg)
        self.mic = MicSession(cfg)
        self.screen = Screen(cfg, now)
        self.speaker = Speaker(self.talk, self.screen, cfg.ffmpeg)
        self.events = AlarmSession(cfg, on_alarm)

    def start(self) -> None:
        self.screen.load()

    def check_login(self) -> dict:
        """Đăng nhập thử MỘT lần (qua LoginGuard) rồi đóng. Ném DVRIPError / OSError nếu không được."""
        client = DVRIPClient(self.cfg.cam_ip, self.cfg.cam_port, self.cfg.dvrip_connect_timeout,
                             self.cfg.dvrip_read_timeout)
        try:
            client.connect()
            return client.login(self.cfg.cam_user, self.cfg.cam_pass)
        finally:
            client.close()

    def close(self) -> None:
        for part in (self.events, self.speaker, self.mic, self.talk):
            try:
                part.stop()
            except Exception:                    # noqa: BLE001 — đóng hết dù một phần lỗi
                pass
        self.screen.close()
