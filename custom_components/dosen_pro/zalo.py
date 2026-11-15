"""Cuộc gọi Zalo lên màn camera: tên người gọi, rồi HÌNH VIDEO của cuộc gọi — tự động, không cần blueprint.

Nghe sự kiện ``zalo_call_call_state`` của tích hợp Zalo Call. Đổ chuông / đang gọi: hiện tên người gọi. Nghe máy: hiện hình video lấy từ
app Zalo (``/api/call/video/view``, MJPEG, hiện giống cửa sổ popup). Kết thúc: gỡ, màn về như trước. Hai lớp riêng: chữ nằm dưới, hình
nằm trên; cuộc gọi thoại (không có hình) thì lớp hình tự rơi, còn lớp chữ."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import Event, HomeAssistant, callback

from .api import DosenApiError

_LOGGER = logging.getLogger(__name__)
ZALO_DOMAIN = "zalo_call"
EVENT_CALL_STATE = "zalo_call_call_state"
OWNER_TEXT, OWNER_VIDEO = "zalo_call", "zalo_call_video"
RING_STATES = ("incoming", "ringing")
TERMINAL_STATES = ("ended", "missed", "rejected", "declined", "no_answer", "error")
HOLD_SECONDS = 3600                                  # lớp tự hết hạn nếu sự kiện kết thúc không tới (HA/Zalo chết giữa chừng)
_SAY = {"incoming": "đang gọi đến", "ringing": "đang đổ chuông", "answered": "đang nói chuyện"}


def app_base_url(hass: HomeAssistant) -> str | None:
    """Địa chỉ app Zalo (từ mục cấu hình của tích hợp Zalo Call), vd. http://192.168.1.10:8090."""
    for entry in hass.config_entries.async_entries(ZALO_DOMAIN):
        url = str(entry.options.get("url") or entry.data.get("url") or "").strip()
        if url:
            return url.rstrip("/")
    return None


def video_url(hass: HomeAssistant, call_id: Any) -> str | None:
    base = app_base_url(hass)
    if not base:
        return None
    suffix = f"?call_id={call_id}" if call_id not in (None, "") else ""
    return f"{base}/api/call/video/view{suffix}"


class ZaloCallScreen:
    def __init__(self, coordinator) -> None:
        self._c = coordinator
        self.hass: HomeAssistant = coordinator.hass
        self._unsub = None
        self._lock = asyncio.Lock()                  # sự kiện tới dồn dập: xử lý lần lượt, đúng thứ tự

    @callback
    def start(self) -> None:
        self._unsub = self.hass.bus.async_listen(EVENT_CALL_STATE, self._on_event)

    @callback
    def stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    @callback
    def _on_event(self, event: Event) -> None:
        if self._c.zalo_call_screen:
            self.hass.async_create_task(self._handle(dict(event.data)))

    async def _handle(self, data: dict) -> None:
        state, api = data.get("state"), self._c.api
        peer = str(data.get("peer") or "?")
        async with self._lock:
            try:
                if state in RING_STATES or state == "answered":
                    await api.screen_text("Zalo", [{"role": "caller", "text": f"{peer} {_SAY[state]}"}], 15, "hold", OWNER_TEXT,
                                          HOLD_SECONDS)
                if state == "answered" and str(data.get("call_type") or "") not in ("voice", "group"):
                    url = video_url(self.hass, data.get("call_id"))
                    if url:
                        # after="off": hình hết/không có (cuộc gọi thoại: app trả 409) thì lớp tự gỡ, còn lại lớp chữ bên dưới.
                        await api.screen_show(url, False, "off", OWNER_VIDEO, HOLD_SECONDS, live=True)
                elif state in TERMINAL_STATES:
                    await api.screen_release(OWNER_VIDEO)
                    await api.screen_release(OWNER_TEXT)
            except DosenApiError as exc:
                _LOGGER.debug("Không hiện được cuộc gọi Zalo lên màn: %s", exc)
