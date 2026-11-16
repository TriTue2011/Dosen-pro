"""Cuộc gọi Telegram lên màn camera: tên người gọi, rồi HÌNH VIDEO của cuộc gọi — tự động, không cần blueprint (giống ``zalo.py`` cho Zalo).

Nghe sự kiện ``telegram_call_call_state`` của tích hợp Telegram Call. Đổ chuông / đang gọi: hiện tên người gọi (tên đã lưu trong danh bạ Telegram, có sẵn
trong sự kiện). Nghe máy cuộc gọi VIDEO (hoặc người gọi bật camera giữa cuộc thoại): hiện hình lấy từ telegram-gateway
(``GET /api/live/view/<id>``, MJPEG, cần token) — HA mở luồng bằng ffmpeg rồi đẩy lên màn. Kết thúc: gỡ, màn về như trước.
Chỉ hiện cuộc gọi của các ID trong «Chỉ các ID» (trống = mọi người) — lọc theo ``user_id`` Telegram."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import Event, HomeAssistant, callback

from .api import DosenApiError

_LOGGER = logging.getLogger(__name__)
TELEGRAM_DOMAIN = "telegram_call"
EVENT_CALL_STATE = "telegram_call_call_state"
OWNER_TEXT, OWNER_VIDEO = "telegram_call", "telegram_call_video"
RING_STATES = ("incoming", "ringing")
TERMINAL_STATES = ("ended", "missed", "not_answered", "error")
HOLD_SECONDS = 3600
_SAY = {"incoming": ("đang gọi đến", "red"), "ringing": ("đang đổ chuông", "orange"), "answered": ("đang nói chuyện", "green")}
UNKNOWN_CALLER = "Có người gọi"


def gateway_of(hass: HomeAssistant):
    """API của telegram-gateway do tích hợp Telegram Call đang giữ (địa chỉ + token), hoặc None khi chưa có tích hợp."""
    for entry in hass.config_entries.async_entries(TELEGRAM_DOMAIN):
        rt = getattr(entry, "runtime_data", None)
        api = getattr(rt, "api", None)
        if api is not None and callable(getattr(api, "view_url", None)):
            return api
    return None


class TelegramCallScreen:
    def __init__(self, coordinator) -> None:
        self._c = coordinator
        self.hass: HomeAssistant = coordinator.hass
        self._unsub = None
        self._shown: set[str] = set()                    # cuộc gọi đã hiện hình (một lần mỗi cuộc, kể cả khi video_request tới sau)
        self._lock = asyncio.Lock()

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
        data = dict(event.data)
        if self._c.zalo_call_screen and self._c.call_screen_allows(data.get("user_id")):
            self.hass.async_create_task(self._handle(data))

    @staticmethod
    def _name(data: dict) -> str:
        """Tên để HIỆN: tên đã lưu trong danh bạ Telegram (có sẵn trong sự kiện) → «Có người gọi». KHÔNG BAO GIỜ hiện dãy số (ID)."""
        name = str(data.get("name") or "").strip()
        return name if name and not name.isdigit() else UNKNOWN_CALLER

    async def _show_video(self, data: dict) -> None:
        cid = str(data.get("call_id"))
        gw = gateway_of(self.hass)
        if gw is None or cid in self._shown:
            return
        self._shown.add(cid)
        # after="off": hết hình / không có hình (cuộc thoại: gateway trả 409) thì lớp tự gỡ, còn lớp chữ bên dưới.
        await self._c.api.screen_show(gw.view_url(cid), False, "off", OWNER_VIDEO, HOLD_SECONDS, live=True, headers=gw.auth_header())

    async def _handle(self, data: dict) -> None:
        state, api = data.get("state"), self._c.api
        video = bool(data.get("video"))
        async with self._lock:
            try:
                if state in RING_STATES or state == "answered":
                    say, colour = _SAY[state]
                    await api.screen_call(self._name(data), say, video, colour, OWNER_TEXT, HOLD_SECONDS)
                if (state == "answered" and video) or state == "video_request":
                    await self._show_video(data)                # cuộc gọi video đã nghe máy, hoặc người gọi vừa bật camera giữa cuộc thoại
                elif state in TERMINAL_STATES:
                    self._shown.discard(str(data.get("call_id")))
                    await api.screen_release(OWNER_VIDEO)
                    await api.screen_release(OWNER_TEXT)
            except DosenApiError as exc:
                _LOGGER.debug("Không hiện được cuộc gọi Telegram lên màn: %s", exc)
