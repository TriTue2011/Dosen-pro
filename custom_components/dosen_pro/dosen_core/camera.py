"""Một camera: gom màn hình, loa, mic và luồng báo động quanh MỘT cấu hình kết nối."""

from __future__ import annotations

from .audio import TalkSession
from .config import CamConfig
from .dvrip import DVRIPClient, DVRIPError
from .events import AlarmSession, other_record
from .screen import Screen
from .speaker import Speaker


class Camera:
    def __init__(self, cfg: CamConfig, now=None, on_alarm=None) -> None:
        self.cfg = cfg
        self.talk = TalkSession(cfg)
        self.screen = Screen(cfg, now, (lambda src, op, data: on_alarm(other_record(src, op, data))) if on_alarm else None)
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

    # -- âm lượng loa / mic của chính camera (cài đặt thật, app iCSee cũng thấy) ------------------
    _SPK, _MIC = "fVideo.Volume", "fVideo.VolumeIn"
    _KEYS = "Consumer.CommDevCfg"            # {"KeyAudioTip": bool, ...}: tiếng bíp khi bấm phím/nút trên thân camera

    def _control(self) -> DVRIPClient:
        client = DVRIPClient(self.cfg.cam_ip, self.cfg.cam_port, self.cfg.dvrip_connect_timeout,
                             self.cfg.dvrip_read_timeout)
        try:
            client.connect()
            client.login(self.cfg.cam_user, self.cfg.cam_pass)
        except Exception:
            client.close()
            raise
        return client

    @staticmethod
    def _first(reply: dict, name: str) -> dict:
        v = reply.get(name)
        return dict(v[0]) if isinstance(v, list) and v and isinstance(v[0], dict) else {}

    def _key_cfg(self, client: DVRIPClient) -> dict:
        """Cấu hình phím của camera; camera đời khác không có thì trả {}."""
        try:
            v = client.get_config(self._KEYS).get(self._KEYS)
        except DVRIPError:
            return {}
        return dict(v) if isinstance(v, dict) else {}

    def get_audio(self) -> dict:
        """{"speaker": 0..100, "mic": 0..100, "anr": bool|None, "key_tip": bool|None} đọc từ camera (một lần đăng nhập)."""
        client = self._control()
        try:
            spk = self._first(client.get_config(self._SPK), self._SPK)
            mic = self._first(client.get_config(self._MIC), self._MIC)
            key = self._key_cfg(client)
        finally:
            client.close()
        return {"speaker": spk.get("LeftVolume"), "mic": mic.get("LeftVolume"), "anr": mic.get("AnrOpen"),
                "key_tip": key.get("KeyAudioTip")}

    def set_audio(self, speaker: int | None = None, mic: int | None = None, anr: bool | None = None,
                  key_tip: bool | None = None) -> dict:
        """Đặt âm lượng loa / mic (0..100), chống ồn mic, tiếng bíp khi bấm nút. Chỉ đổi phần được truyền; các trường
        khác giữ nguyên."""
        for name, v in (("speaker", speaker), ("mic", mic)):
            if v is not None and (not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 100):
                raise ValueError(f"{name} must be an integer 0..100")
        if anr is not None and not isinstance(anr, bool):
            raise ValueError("anr must be true or false")
        if key_tip is not None and not isinstance(key_tip, bool):
            raise ValueError("key_tip must be true or false")
        if speaker is None and mic is None and anr is None and key_tip is None:
            raise ValueError("give speaker, mic, anr and/or key_tip")
        client = self._control()
        try:
            if speaker is not None:
                cur = self._first(client.get_config(self._SPK), self._SPK) or {"AudioMode": "Single"}
                cur["LeftVolume"] = cur["RightVolume"] = speaker
                client.set_config(self._SPK, [cur])
            if mic is not None or anr is not None:
                cur = self._first(client.get_config(self._MIC), self._MIC) or {"AudioMode": "Single"}
                if mic is not None:
                    cur["LeftVolume"] = cur["RightVolume"] = mic
                if anr is not None:
                    cur["AnrOpen"] = anr
                client.set_config(self._MIC, [cur])
            if key_tip is not None:
                cur = self._key_cfg(client)
                if not cur:
                    raise ValueError("this camera has no button-sound setting")
                cur["KeyAudioTip"] = key_tip
                client.set_config(self._KEYS, cur)
        finally:
            client.close()
        return self.get_audio()

    def close(self) -> None:
        for part in (self.events, self.speaker, self.talk):
            try:
                part.stop()
            except Exception:                    # noqa: BLE001 — đóng hết dù một phần lỗi
                pass
        self.screen.close()
