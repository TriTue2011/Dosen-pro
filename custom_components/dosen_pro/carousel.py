"""Màn luân phiên: các «trang» (đồng hồ, thẻ thông tin, ảnh, video, camera trực tiếp, chữ) lần lượt hiện lên màn camera.

Chạy ở phía Home Assistant (không phải lõi) vì thẻ thông tin cần dữ liệu MỚI của thực thể mỗi lần hiện. Mỗi trang là một lớp
của chủ ``carousel`` nằm DƯỚI mọi lớp sự kiện (cuộc gọi, chuông cửa, Assist…): có sự kiện thì nó đè lên, hết thì trang đang tới lượt hiện
lại. Danh sách trang và công tắc bật/tắt được lưu, khởi động lại HA vẫn chạy tiếp."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store

from .api import DosenApiError
from .card import build_card
from .dosen_core.render import CARD_COLORS, CARD_LAYOUTS, CARD_THEMES, HEX_COLOR
from .dosen_core.screen import CAROUSEL_OWNER
from .resolve import resolve_source

_LOGGER = logging.getLogger(__name__)
PAGE_TYPES = ("clock", "card", "image", "video", "camera", "text")
DEFAULT_SECONDS = 20.0
MIN_SECONDS, MAX_SECONDS = 3.0, 3600.0
_CHECK_EVERY = 5.0          # đồng hồ sang phút mới / số liệu thẻ đổi thì vẽ lại, kiểm mỗi ngần này giây (không vẽ lại khi giống hệt)
_MAX_PAGES = 12

COLOR = vol.Any(vol.In(list(CARD_COLORS)), vol.Match(HEX_COLOR))      # tên trong bảng màu hoặc #rrggbb
_CARD_ROW = vol.Schema({vol.Required("label"): cv.string, vol.Required("value"): cv.string, vol.Optional("color"): COLOR})
_CARD_ENTITY = vol.Any(cv.entity_id, vol.Schema({vol.Required("entity"): cv.entity_id, vol.Optional("name"): cv.string,
                                                 vol.Optional("color"): vol.Any(COLOR, None)}))
_MESSAGE = vol.Schema({vol.Optional("role", default="info"): vol.In(["user", "assistant", "caller", "info"]),
                       vol.Required("text"): cv.string})
PAGE_SCHEMA = vol.Schema({
    vol.Required("type"): vol.In(PAGE_TYPES),
    vol.Optional("seconds"): vol.All(vol.Coerce(float), vol.Range(min=MIN_SECONDS, max=MAX_SECONDS)),
    # thẻ thông tin
    vol.Optional("title"): cv.string, vol.Optional("value"): cv.string, vol.Optional("caption"): cv.string,
    vol.Optional("color"): COLOR, vol.Optional("main_entity"): cv.entity_id,
    vol.Optional("theme"): vol.In(list(CARD_THEMES)), vol.Optional("layout"): vol.In(list(CARD_LAYOUTS)),
    vol.Optional("title_color"): vol.Any(COLOR, None),
    vol.Optional("entities"): vol.All(cv.ensure_list, [_CARD_ENTITY]),
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


TYPE_LABELS = {"clock": "Đồng hồ", "card": "Thẻ thông tin", "camera": "Camera", "image": "Ảnh", "text": "Chữ", "video": "Video"}


def page_of(card: dict) -> dict:
    """Thẻ (có id, tên) → trang để hiện: bỏ ``id`` và ``name``."""
    return {k: v for k, v in card.items() if k not in ("id", "name")}


def auto_name(page: dict) -> str:
    return str(page.get("title") or page.get("caption") or TYPE_LABELS.get(page["type"], page["type"]))


class Carousel:
    """Bộ điều khiển luân phiên của một camera.

    Có một **thư viện thẻ** (mỗi thẻ: ``id``, ``name`` và các trường của một trang) và danh sách **thẻ tham gia luân phiên**.
    Thứ tự luân phiên = thứ tự trong thư viện. Thêm/sửa/xoá thẻ làm ở hộp «Cấu hình» (xem ``config_flow``); dịch vụ
    ``screen_carousel`` với ``pages`` thay cả thư viện (tên tự đặt)."""

    def __init__(self, coordinator) -> None:
        self._c = coordinator
        self.hass: HomeAssistant = coordinator.hass
        self.cards: list[dict] = []
        self.selected: list[str] = []         # id các thẻ tham gia luân phiên
        self.seconds: float = DEFAULT_SECONDS
        self.on = False
        self.memory: dict[str, list] = {}     # giá trị gần nhất đã biết của từng thực thể trong thẻ (xem card.MEMORY_MAX_AGE)
        self.index = -1                       # trang đang hiện (để cảm biến/chẩn đoán)
        self._task: asyncio.Task | None = None
        self._store = Store(self.hass, 1, f"dosen_pro.{coordinator.entry.entry_id}.carousel")

    # ── các trang đang tham gia ──────────────────────────────────────
    @property
    def pages(self) -> list[dict]:
        return [page_of(c) for c in self.cards if c["id"] in self.selected]

    @pages.setter
    def pages(self, pages: list[dict]) -> None:
        """Đặt thẳng danh sách trang (không kiểm: dành cho thử nghiệm); mỗi trang thành một thẻ tên tự đặt, tất cả tham gia."""
        self._set_cards_from_pages(list(pages))

    def _set_cards_from_pages(self, pages: list[dict]) -> None:
        """Thư viện mới từ danh sách trang ĐÃ kiểm (hoặc thẳng cho thử nghiệm): tên tự đặt, tất cả tham gia, không kiểm lại."""
        self.cards, self.selected = [], []
        for page in pages:
            card = {"id": uuid.uuid4().hex[:8], "name": self._unique_name(auto_name(page)), **page}
            self.cards.append(card)
            self.selected.append(card["id"])

    # ── thư viện thẻ ─────────────────────────────────────────────────
    def _unique_name(self, name: str, ignore_id: str | None = None) -> str:
        taken = {c["name"] for c in self.cards if c["id"] != ignore_id}
        base, n = name, 2
        while name in taken:
            name, n = f"{base} ({n})", n + 1
        return name

    def card_by_id(self, card_id: str) -> dict | None:
        return next((c for c in self.cards if c["id"] == card_id), None)

    def add_card(self, data: dict, select: bool = True, save: bool = True) -> str:
        """Thêm thẻ vào cuối thư viện; ``data`` gồm ``name`` và các trường của một trang. Trả về id. Ném ``vol.Invalid`` nếu sai."""
        page = validate_pages([page_of(data)])[0]
        card = {"id": uuid.uuid4().hex[:8], "name": self._unique_name(str(data.get("name") or auto_name(page))), **page}
        self.cards.append(card)
        if select:
            self.selected.append(card["id"])
        if save:
            self._save()
        return card["id"]

    def update_card(self, card_id: str, data: dict) -> None:
        old = self.card_by_id(card_id)
        if old is None:
            raise KeyError(card_id)
        page = validate_pages([page_of(data)])[0]
        new = {"id": card_id, "name": self._unique_name(str(data.get("name") or old["name"]), card_id), **page}
        self.cards[self.cards.index(old)] = new
        self._save()

    def delete_cards(self, ids: list[str]) -> None:
        self.cards = [c for c in self.cards if c["id"] not in ids]
        self.selected = [i for i in self.selected if i not in ids]
        self._save()

    def move_card(self, card_id: str, delta: int) -> None:
        """Dời thẻ lên (-1) hoặc xuống (+1) trong thư viện = đổi thứ tự luân phiên."""
        i = next((n for n, c in enumerate(self.cards) if c["id"] == card_id), None)
        if i is None:
            raise KeyError(card_id)
        j = max(0, min(len(self.cards) - 1, i + delta))
        self.cards.insert(j, self.cards.pop(i))
        self._save()

    # ── lưu / nạp ────────────────────────────────────────────────────
    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.seconds = float(data.get("seconds") or DEFAULT_SECONDS)
        self.memory = {k: v for k, v in (data.get("memory") or {}).items() if isinstance(v, list) and len(v) == 3}
        self.cards, self.selected = [], []
        try:
            if data.get("cards"):                                       # định dạng thư viện thẻ
                for c in data["cards"]:
                    try:
                        page = validate_pages([page_of(c)])[0]
                    except vol.Invalid as exc:
                        _LOGGER.warning("Bỏ qua thẻ luân phiên không hợp lệ (%s): %s", c.get("name"), exc)
                        continue
                    self.cards.append({"id": str(c.get("id") or uuid.uuid4().hex[:8]), "name": str(c.get("name") or auto_name(page)), **page})
                ids = {c["id"] for c in self.cards}
                self.selected = [i for i in data.get("selected", []) if i in ids]
            elif data.get("pages"):                                     # định dạng cũ (danh sách trang): đổi thành thư viện
                for page in validate_pages(data["pages"]):
                    self.add_card({**page, "name": auto_name(page)}, select=True, save=False)
        except vol.Invalid as exc:
            _LOGGER.warning("Danh sách trang luân phiên đã lưu không hợp lệ, bỏ qua: %s", exc)
            self.cards, self.selected = [], []
        self.on = bool(data.get("on")) and bool(self.pages)

    def _save(self) -> None:
        self._store.async_delay_save(lambda: json.loads(json.dumps(
            {"cards": self.cards, "selected": self.selected, "seconds": self.seconds, "on": self.on,
             "memory": self.memory})), 0.5)

    def _save_memory_soon(self) -> None:
        """Ghi giá trị đã nhớ (hoãn 30 giây để gộp nhiều lần đổi; không ghi đĩa mỗi lần cảm biến nhích)."""
        self._store.async_delay_save(lambda: json.loads(json.dumps(
            {"cards": self.cards, "selected": self.selected, "seconds": self.seconds, "on": self.on,
             "memory": self.memory})), 30)

    # ── điều khiển ───────────────────────────────────────────────────
    def configure(self, pages: list[dict] | None, seconds: float | None) -> None:
        """Dịch vụ ``screen_carousel``: ``pages`` thay cả thư viện (mọi trang tham gia)."""
        if pages is not None:
            checked = validate_pages(pages)
            self._set_cards_from_pages(checked)
            self._save()
        if seconds is not None:
            self.set_seconds(seconds)
        self._save()

    def set_seconds(self, seconds: float) -> None:
        """Thời lượng mặc định mỗi trang (trang có ``seconds`` riêng thì giữ riêng). Áp dụng từ trang kế tiếp, không cần khởi động lại vòng."""
        if not MIN_SECONDS <= float(seconds) <= MAX_SECONDS:
            raise ValueError(f"seconds must be {MIN_SECONDS:g}..{MAX_SECONDS:g}")
        self.seconds = float(seconds)
        self._save()
        self._c.async_update_listeners()

    def set_selected(self, ids: list[str]) -> None:
        keep = {i for i in ids}
        self.selected = [c["id"] for c in self.cards if c["id"] in keep]     # thứ tự = thứ tự thư viện
        self._save()

    async def async_apply(self, on: bool | None = None) -> None:
        """Áp dụng thay đổi thư viện/lựa chọn: đang chạy thì chạy lại vòng với trang mới; hết trang thì tắt."""
        if on is not None:
            self.on = on
        if self.on and not self.pages:
            self.on = False
        if self.on:
            self._restart()
            self._save()
            self._c.async_update_listeners()
        else:
            await self.async_stop()

    async def async_start(self) -> None:
        if not self.pages:
            raise HomeAssistantError("Chưa có thẻ nào tham gia luân phiên: thêm thẻ ở Cấu hình → Dosen Pro, hoặc gọi dosen_pro.screen_carousel với pages")
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
            before = json.dumps(self.memory, sort_keys=True)
            card = build_card(self.hass, page, self.memory)
            if json.dumps(self.memory, sort_keys=True) != before:
                self._save_memory_soon()
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
