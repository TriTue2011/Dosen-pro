"""Màn luân phiên: các «trang» (đồng hồ, thẻ thông tin, ảnh, video, camera trực tiếp, chữ) lần lượt hiện lên màn camera.

Chạy ở phía Home Assistant (không phải lõi) vì thẻ thông tin cần dữ liệu MỚI của thực thể mỗi lần hiện. Mỗi trang là một lớp
của chủ ``carousel`` nằm DƯỚI mọi lớp sự kiện (cuộc gọi, chuông cửa, Assist…): có sự kiện thì nó đè lên, hết thì trang đang tới lượt hiện
lại. Danh sách trang và công tắc bật/tắt được lưu, khởi động lại HA vẫn chạy tiếp."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store

from .api import DosenApiError
from .card import build_card
from .dosen_core.render import CARD_COLORS
from .dosen_core.screen import CAROUSEL_OWNER
from .resolve import resolve_source

_LOGGER = logging.getLogger(__name__)
PAGE_TYPES = ("clock", "card", "image", "video", "camera", "text")
DEFAULT_SECONDS = 10.0
MIN_SECONDS, MAX_SECONDS = 3.0, 3600.0
_CHECK_EVERY = 5.0          # đồng hồ sang phút mới / số liệu thẻ đổi thì vẽ lại, kiểm mỗi ngần này giây (không vẽ lại khi giống hệt)
_MAX_PAGES = 12

_CARD_ROW = vol.Schema({vol.Required("label"): cv.string, vol.Required("value"): cv.string,
                        vol.Optional("color"): vol.In(list(CARD_COLORS))})
_MESSAGE = vol.Schema({vol.Optional("role", default="info"): vol.In(["user", "assistant", "caller", "info"]),
                       vol.Required("text"): cv.string})
PAGE_SCHEMA = vol.Schema({
    vol.Required("type"): vol.In(PAGE_TYPES),
    vol.Optional("seconds"): vol.All(vol.Coerce(float), vol.Range(min=MIN_SECONDS, max=MAX_SECONDS)),
    # thẻ thông tin
    vol.Optional("title"): cv.string, vol.Optional("value"): cv.string, vol.Optional("caption"): cv.string,
    vol.Optional("color"): vol.In(list(CARD_COLORS)), vol.Optional("main_entity"): cv.entity_id,
    vol.Optional("entities"): vol.All(cv.ensure_list, [cv.entity_id]),
    vol.Optional("rows"): vol.All(cv.ensure_list, [vol.Any(_CARD_ROW, cv.string)]),
    # chữ
    vol.Optional("messages"): vol.All(cv.ensure_list, [vol.Any(_MESSAGE, cv.string)]),
    vol.Optional("font_size"): vol.All(vol.Coerce(int), vol.Range(min=10, max=30)),
    # ảnh / video / camera
    vol.Optional("source"): cv.string, vol.Optional("media"): vol.Any(cv.string, dict),
    vol.Optional("image_entity"): cv.entity_id, vol.Optional("camera_entity"): cv.entity_id,
    vol.Optional("loop"): cv.boolean,
}, extra=vol.PREVENT_EXTRA)


def validate_pages(pages: Any) -> list[dict]:
    """Kiểm danh sách trang; ném ``vol.Invalid`` nếu sai. Mỗi loại trang phải có đủ nguồn của nó."""
    if not isinstance(pages, list) or not pages:
        raise vol.Invalid("cần ít nhất một trang")
    if len(pages) > _MAX_PAGES:
        raise vol.Invalid(f"tối đa {_MAX_PAGES} trang")
    out = []
    for i, raw in enumerate(pages, 1):
        page = PAGE_SCHEMA(raw)
        t = page["type"]
        has = lambda *keys: any(page.get(k) for k in keys)           # noqa: E731
        if t == "image" and not has("source", "media", "image_entity"):
            raise vol.Invalid(f"trang {i} (ảnh) cần source, media hoặc image_entity")
        if t == "video" and not has("source", "media"):
            raise vol.Invalid(f"trang {i} (video) cần source hoặc media")
        if t == "camera" and not has("camera_entity", "source"):
            raise vol.Invalid(f"trang {i} (camera) cần camera_entity hoặc source")
        if t == "card" and not has("title", "value", "main_entity", "entities", "rows"):
            raise vol.Invalid(f"trang {i} (thẻ) cần ít nhất một trong title, value, main_entity, entities, rows")
        if t == "text" and not has("title", "messages"):
            raise vol.Invalid(f"trang {i} (chữ) cần title hoặc messages")
        out.append(page)
    return out


class Carousel:
    """Bộ điều khiển luân phiên của một camera: giữ danh sách trang, bật/tắt, chạy vòng hiển thị."""

    def __init__(self, coordinator) -> None:
        self._c = coordinator
        self.hass: HomeAssistant = coordinator.hass
        self.pages: list[dict] = []
        self.seconds: float = DEFAULT_SECONDS
        self.on = False
        self.index = -1                       # trang đang hiện (để cảm biến/chẩn đoán)
        self._task: asyncio.Task | None = None
        self._store = Store(self.hass, 1, f"dosen_pro.{coordinator.entry.entry_id}.carousel")

    # ── lưu / nạp ────────────────────────────────────────────────────
    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        try:
            self.pages = validate_pages(data["pages"]) if data.get("pages") else []
        except vol.Invalid as exc:
            _LOGGER.warning("Danh sách trang luân phiên đã lưu không hợp lệ, bỏ qua: %s", exc)
            self.pages = []
        self.seconds = float(data.get("seconds") or DEFAULT_SECONDS)
        self.on = bool(data.get("on")) and bool(self.pages)

    def _save(self) -> None:
        self._store.async_delay_save(lambda: json.loads(json.dumps(
            {"pages": self.pages, "seconds": self.seconds, "on": self.on})), 0.5)

    # ── điều khiển ───────────────────────────────────────────────────
    def configure(self, pages: list[dict] | None, seconds: float | None) -> None:
        if pages is not None:
            self.pages = validate_pages(pages)
        if seconds is not None:
            self.seconds = float(seconds)
        self._save()

    async def async_start(self) -> None:
        if not self.pages:
            raise HomeAssistantError("Chưa có trang nào cho màn luân phiên: gọi dosen_pro.screen_carousel với pages")
        self.on = True
        self._save()
        self._restart()
        self._c.async_update_listeners()                                  # công tắc và thuộc tính cập nhật ngay

    async def async_stop(self) -> None:
        self.on = False
        self._save()
        await self._cancel()
        self._c.async_update_listeners()
        try:
            await self._c.api.screen_release(CAROUSEL_OWNER)             # gỡ lớp luân phiên: về màn mặc định / lớp bên dưới
        except DosenApiError as exc:
            _LOGGER.debug("Không gỡ được lớp luân phiên: %s", exc)

    def _restart(self) -> None:
        if self._task is not None:
            self._task.cancel()
        self._task = self._c.entry.async_create_background_task(self.hass, self._run(), "dosen_pro carousel")

    async def _cancel(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):                  # noqa: BLE001 — đang tắt, không còn gì để báo
                pass

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def resume_if_on(self) -> None:
        """Gọi sau khi nạp: bật lại nếu lần trước đang chạy."""
        if self.on and self.pages:
            self._restart()

    # ── vòng hiển thị ────────────────────────────────────────────────
    async def _run(self) -> None:
        last_sig: str | None = None
        try:
            while True:
                for self.index, page in enumerate(list(self.pages)):
                    seconds = float(page.get("seconds") or self.seconds)
                    end = asyncio.get_running_loop().time() + seconds
                    last_sig = None                                      # sang trang mới: luôn vẽ
                    while True:
                        try:
                            last_sig = await self._show(page, seconds, last_sig)
                        except (DosenApiError, HomeAssistantError) as exc:
                            _LOGGER.debug("Trang luân phiên %s chưa hiện được: %s", page["type"], exc)
                        left = end - asyncio.get_running_loop().time()
                        if left <= 0:
                            break
                        await asyncio.sleep(min(_CHECK_EVERY, left))
        except asyncio.CancelledError:
            raise

    async def _show(self, page: dict, seconds: float, last_sig: str | None) -> str | None:
        """Đẩy trang xuống màn nếu nội dung khác lần trước (``last_sig``); trả chữ ký mới. Lớp có hạn dài gấp 3 để không treo mãi nếu vòng chết."""
        api, ttl, t = self._c.api, seconds * 3, page["type"]
        if t == "clock":
            sig = self._c.hass_now_minute()
            if sig != last_sig:
                await api.screen_clock("hold", CAROUSEL_OWNER, ttl)
            return sig
        if t == "card":
            card = build_card(self.hass, page)
            sig = json.dumps(card, sort_keys=True, default=str)
            if sig != last_sig:
                await api.screen_card(card, "hold", CAROUSEL_OWNER, ttl)
            return sig
        if t == "text":
            messages = [{"role": "info", "text": m} if isinstance(m, str) else m for m in page.get("messages", [])]
            sig = json.dumps([page.get("title"), messages], sort_keys=True, default=str)
            if sig != last_sig:
                await api.screen_text(page.get("title", ""), messages, page.get("font_size", 15), "hold", CAROUSEL_OWNER, ttl)
            return sig
        if last_sig is None:                                           # ảnh / video / camera: một lần đầu trang
            url = await resolve_source(self.hass, source=page.get("source"), camera_entity=page.get("camera_entity"),
                                       media=page.get("media"), image_entity=page.get("image_entity"), base=self._c.ha_base)
            await api.screen_show(url, bool(page.get("loop", t == "video")), "hold", CAROUSEL_OWNER, ttl)
        return t
