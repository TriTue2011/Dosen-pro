"""Cuộc gọi Zalo lên màn camera: tên người gọi, rồi HÌNH VIDEO của cuộc gọi — tự động, không cần blueprint.

Nghe sự kiện ``zalo_call_call_state`` của tích hợp Zalo Call. Đổ chuông / đang gọi: hiện tên người gọi. Nghe máy: hiện hình video lấy từ
app Zalo (``/api/call/video/view``, MJPEG, hiện giống cửa sổ popup). Kết thúc: gỡ, màn về như trước. Hai lớp riêng: chữ nằm dưới, hình
nằm trên; cuộc gọi thoại (không có hình) thì lớp hình tự rơi, còn lớp chữ."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import Event, HomeAssistant, callback
from yarl import URL

from .api import DosenApiError

_LOGGER = logging.getLogger(__name__)
ZALO_DOMAIN = "zalo_call"
EVENT_CALL_STATE = "zalo_call_call_state"
OWNER_TEXT, OWNER_VIDEO = "zalo_call", "zalo_call_video"
RING_STATES = ("incoming", "ringing")
TERMINAL_STATES = ("ended", "missed", "rejected", "declined", "no_answer", "error")
HOLD_SECONDS = 3600                                  # lớp tự hết hạn nếu sự kiện kết thúc không tới (HA/Zalo chết giữa chừng)
_SAY = {"incoming": ("đang gọi đến", "red"), "ringing": ("đang đổ chuông", "orange"), "answered": ("đang nói chuyện", "green")}
UNKNOWN_CALLER = "Có người gọi"
SESSION_COOKIE = "zalo_pc_app"                       # tên cookie phiên của app Zalo (app_auth.COOKIE_NAME)
LOGIN_TIMEOUT_S = 5.0
NAMES_TTL_S = 60.0                                   # danh bạ lấy từ app Zalo, nhớ chừng này giây rồi lấy lại khi gặp mã chưa biết
CONTACTS_TIMEOUT_S = 3.0


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


def _contact_names(contacts: Any) -> dict[str, str]:
    """{mã người/nhóm: tên đã lưu} từ dữ liệu danh bạ của app ({friends: {rows: [{id, name}]}, groups: {...}})."""
    out: dict[str, str] = {}
    for part in ("friends", "groups"):
        block = contacts.get(part) if isinstance(contacts, dict) else None
        for row in (block.get("rows") if isinstance(block, dict) else None) or []:
            if isinstance(row, dict) and str(row.get("id") or "") and str(row.get("name") or "").strip():
                out[str(row["id"])] = str(row["name"]).strip()
    return out


class ZaloCallScreen:
    def __init__(self, coordinator) -> None:
        self._c = coordinator
        self.hass: HomeAssistant = coordinator.hass
        self._names: dict[str, str] = {}
        self._names_at = -1e9
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
        data = dict(event.data)
        if self._c.zalo_call_screen and self._c.call_screen_allows(data.get("thread_id") or data.get("peer")):       # chỉ các threadID đã chọn (trống = mọi người)
            self.hass.async_create_task(self._handle(data))

    def _contacts_api(self):
        """API của app Zalo do tích hợp Zalo Call đang giữ (đã đăng nhập sẵn): dùng lại để tra tên, khỏi khai thêm tài khoản."""
        for rt in (self.hass.data.get(ZALO_DOMAIN) or {}).values():
            api = getattr(rt, "api", None)
            if callable(getattr(api, "contacts", None)):
                return api
        return None

    async def _video_headers(self, base: str) -> str | None:
        """Tiêu đề HTTP «Cookie: …» mang phiên đăng nhập app Zalo cho ffmpeg: ``/api/call/video/view`` cần đăng nhập, không có thì 401 và màn
        không hiện hình (nhật ký HA 15/11: «Server returned 401 Unauthorized»). Dùng lại phiên của tích hợp Zalo Call (đăng nhập lại để chắc còn
        hạn). Không lấy được thì thôi (trả None, ffmpeg thử không cookie)."""
        api = self._contacts_api()
        session = getattr(api, "_s", None)
        if api is None or session is None or not callable(getattr(api, "login", None)):
            return None
        try:
            await asyncio.wait_for(api.login(), LOGIN_TIMEOUT_S)
            morsel = session.cookie_jar.filter_cookies(URL(base)).get(SESSION_COOKIE)
        except Exception as exc:                                  # noqa: BLE001 — app chưa lên / sai mật khẩu: không có cookie
            _LOGGER.debug("Không lấy được phiên app Zalo cho luồng hình: %s", exc)
            return None
        return f"Cookie: {SESSION_COOKIE}={morsel.value}\r\n" if morsel is not None and morsel.value else None

    async def _caller_name(self, data: dict) -> str:
        """Tên để HIỆN: tên đã lưu trong danh bạ Zalo (tra theo mã) → tên có sẵn trong sự kiện → «Có người gọi». KHÔNG BAO GIỜ hiện dãy số:
        sự kiện cuộc gọi đến chỉ mang mã người (thread_id) trong ``peer``."""
        tid = str(data.get("thread_id") or data.get("peer") or "").strip()
        if tid and tid not in self._names and self.hass.loop.time() - self._names_at > NAMES_TTL_S:
            api = self._contacts_api()
            if api is not None:
                self._names_at = self.hass.loop.time()
                try:
                    self._names = _contact_names(await asyncio.wait_for(api.contacts(), CONTACTS_TIMEOUT_S))
                except Exception as exc:                          # noqa: BLE001 — app chưa lên / chậm: hiện tên có sẵn, không làm hỏng màn
                    _LOGGER.debug("Không lấy được danh bạ Zalo: %s", exc)
        for cand in (self._names.get(tid), data.get("peer")):
            name = str(cand or "").strip()
            if name and not name.isdigit():
                return name
        return UNKNOWN_CALLER

    async def _handle(self, data: dict) -> None:
        state, api = data.get("state"), self._c.api
        ctype = str(data.get("call_type") or "")
        kind = True if ctype == "video" else (False if ctype in ("voice", "group") else None)     # sự kiện cuộc gọi đến không luôn nói loại
        async with self._lock:
            try:
                if state in RING_STATES or state == "answered":
                    say, colour = _SAY[state]
                    await api.screen_call(await self._caller_name(data), say, kind, colour, OWNER_TEXT, HOLD_SECONDS)
                if state == "answered" and kind is not False:
                    url = video_url(self.hass, data.get("call_id"))
                    if url:
                        # after="off": hình hết/không có (cuộc gọi thoại: app trả 409) thì lớp tự gỡ, còn lại lớp chữ bên dưới.
                        await api.screen_show(url, False, "off", OWNER_VIDEO, HOLD_SECONDS, live=True,
                                              headers=await self._video_headers(app_base_url(self.hass)))
                elif state in TERMINAL_STATES:
                    await api.screen_release(OWNER_VIDEO)
                    await api.screen_release(OWNER_TEXT)
            except DosenApiError as exc:
                _LOGGER.debug("Không hiện được cuộc gọi Zalo lên màn: %s", exc)
