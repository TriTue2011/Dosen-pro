"""API WebSocket cho thẻ «Dosen Pro» (giao diện): đọc/ghi toàn bộ cài đặt hiển thị, quản lý thẻ luân phiên, xem trước đúng hình sẽ lên màn.

Mọi lệnh chỉ dành cho quản trị viên. ``entry_id`` tuỳ chọn khi chỉ có một camera."""

from __future__ import annotations

import base64
import copy
import logging
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .api import DosenApiError
from .card import build_card
from .carousel import page_of, validate_pages
from .const import DEFAULT_MODES, DOMAIN
from .dosen_core.render import CARD_COLORS, CARD_LAYOUTS, CARD_THEMES, render_card, render_clock
from .dosen_core.screen import FITS, QUALITIES, ROTATIONS
from .resolve import resolve_source

_LOGGER = logging.getLogger(__name__)
PREVIEW_OWNER = "xem_thu"
PREVIEW_SECONDS = 30
_BASE = {vol.Optional("entry_id"): str}


def _coordinator(hass: HomeAssistant, entry_id: str | None):
    coords = hass.data.get(DOMAIN, {})
    if entry_id:
        if entry_id not in coords:
            raise HomeAssistantError(f"không có thiết bị Dosen Pro với entry_id {entry_id}")
        return coords[entry_id]
    if len(coords) == 1:
        return next(iter(coords.values()))
    raise HomeAssistantError("có nhiều thiết bị Dosen Pro: điền entry_id" if coords else "chưa có thiết bị Dosen Pro")


def panel_state(coord) -> dict[str, Any]:
    car = coord.carousel
    return {
        "entry_id": coord.entry.entry_id, "title": coord.entry.title,
        "settings": coord.display.as_dict(),
        "carousel": {"cards": car.cards, "selected": car.selected, "seconds": car.seconds, "on": car.on, "running": car.running},
        "options": {"modes": list(DEFAULT_MODES), "rotations": list(ROTATIONS), "fits": list(FITS), "qualities": list(QUALITIES),
                    "colors": list(CARD_COLORS), "themes": list(CARD_THEMES), "layouts": list(CARD_LAYOUTS)},
    }


def _handler(func):
    """Bọc lệnh: chỉ admin, chạy bất đồng bộ, lỗi HA/camera/kiểm dữ liệu thành lỗi WebSocket rõ ràng."""
    @websocket_api.require_admin
    @websocket_api.async_response
    async def wrapper(hass: HomeAssistant, connection, msg: dict) -> None:
        try:
            coord = _coordinator(hass, msg.get("entry_id"))
            connection.send_result(msg["id"], await func(hass, coord, msg))
        except vol.Invalid as exc:
            connection.send_error(msg["id"], "invalid_format", str(exc))
        except (HomeAssistantError, DosenApiError, KeyError, ValueError) as exc:
            connection.send_error(msg["id"], "dosen_pro_error", str(exc))
    return wrapper


def _page(card: dict) -> dict:
    return validate_pages([page_of(card)])[0]


async def _render(hass: HomeAssistant, coord, card: dict) -> bytes | None:
    """PNG đúng như sẽ lên màn (số liệu thật của HA lúc này); None với loại thẻ không vẽ được (camera, ảnh, video)."""
    page = _page(card)
    if page["type"] == "clock":
        weather = await coord.weather_info()
        return await hass.async_add_executor_job(render_clock, dt_util.now().replace(tzinfo=None), weather)
    if page["type"] == "card":
        data = build_card(hass, page, copy.deepcopy(coord.carousel.memory))          # xem thử không ghi bộ nhớ giá trị
        return await hass.async_add_executor_job(
            render_card, data["title"], data["value"], data["caption"], data["rows"], data["color"], data["theme"],
            data["layout"], data["title_color"])
    return None


@websocket_api.websocket_command({vol.Required("type"): "dosen_pro/state", **_BASE})
@_handler
async def ws_state(hass, coord, msg):
    return panel_state(coord)


@websocket_api.websocket_command({vol.Required("type"): "dosen_pro/set", vol.Required("changes"): dict, **_BASE})
@_handler
async def ws_set(hass, coord, msg):
    await coord.display.async_set(msg["changes"])
    return panel_state(coord)


@websocket_api.websocket_command({vol.Required("type"): "dosen_pro/card_save", vol.Required("card"): dict, **_BASE})
@_handler
async def ws_card_save(hass, coord, msg):
    car, card = coord.carousel, msg["card"]
    if card.get("id") and car.card_by_id(card["id"]):
        car.update_card(card["id"], card)
        card_id = card["id"]
    else:
        card_id = car.add_card(card, select=bool(card.get("join", True)))
    await car.async_apply()
    return {"id": card_id, **panel_state(coord)}


@websocket_api.websocket_command({vol.Required("type"): "dosen_pro/card_delete", vol.Required("ids"): [str], **_BASE})
@_handler
async def ws_card_delete(hass, coord, msg):
    coord.carousel.delete_cards(msg["ids"])
    await coord.carousel.async_apply()
    return panel_state(coord)


@websocket_api.websocket_command({vol.Required("type"): "dosen_pro/card_move", vol.Required("card_id"): str,
                                  vol.Required("delta"): vol.All(int, vol.In([-1, 1])), **_BASE})
@_handler
async def ws_card_move(hass, coord, msg):
    coord.carousel.move_card(msg["card_id"], msg["delta"])   # «id» đã là mã tin nhắn WebSocket nên đặt tên card_id
    return panel_state(coord)


@websocket_api.websocket_command({vol.Required("type"): "dosen_pro/rotation", vol.Optional("selected"): [str],
                                  vol.Optional("seconds"): vol.All(vol.Coerce(float), vol.Range(min=3, max=3600)),
                                  vol.Optional("on"): bool, **_BASE})
@_handler
async def ws_rotation(hass, coord, msg):
    car = coord.carousel
    if "selected" in msg:
        car.set_selected(msg["selected"])
    if "seconds" in msg:
        car.set_seconds(msg["seconds"])
    await car.async_apply(msg.get("on"))
    return panel_state(coord)


@websocket_api.websocket_command({vol.Required("type"): "dosen_pro/preview", vol.Required("card"): dict, **_BASE})
@_handler
async def ws_preview(hass, coord, msg):
    png = await _render(hass, coord, msg["card"])
    return {"png": base64.b64encode(png).decode() if png else None,
            "note": None if png else "Loại thẻ này (camera, ảnh, video) hiện trực tiếp trên màn, không có bản xem trước."}


@websocket_api.websocket_command({vol.Required("type"): "dosen_pro/show_now", vol.Required("card"): dict, **_BASE})
@_handler
async def ws_show_now(hass, coord, msg):
    """Hiện thẻ đang soạn lên màn camera ngay (30 giây rồi về màn trước đó)."""
    page, api = _page(msg["card"]), coord.api
    if page["type"] == "clock":
        await api.screen_clock("hold", PREVIEW_OWNER, PREVIEW_SECONDS)
    elif page["type"] == "card":
        await api.screen_card(build_card(hass, page, copy.deepcopy(coord.carousel.memory)), "hold", PREVIEW_OWNER, PREVIEW_SECONDS)
    elif page["type"] == "text":
        await api.screen_text(page.get("title", ""), [{"role": "info", "text": str(m)} for m in page.get("messages", [])],
                              page.get("font_size", 15), "hold", PREVIEW_OWNER, PREVIEW_SECONDS)
    else:
        url = await resolve_source(hass, source=page.get("source"), camera_entity=page.get("camera_entity"),
                                   media=page.get("media"), image_entity=page.get("image_entity"), base=coord.ha_base)
        await api.screen_show(url, bool(page.get("loop", page["type"] == "video")), "hold", PREVIEW_OWNER, PREVIEW_SECONDS)
    return {"ok": True, "seconds": PREVIEW_SECONDS}


async def _screen_info(coord) -> dict[str, Any]:
    """Thời gian sáng (giây, 0 = không tắt) và độ sáng của màn hình camera (đọc từ camera) cùng trạng thái màn đang chiếu."""
    info = await coord.api.screen_sleep_get()
    return {"seconds": info.get("work_time"), "luminance": info.get("luminance"), "status": await coord.api.screen_status()}


@websocket_api.websocket_command({vol.Required("type"): "dosen_pro/screen_info", **_BASE})
@_handler
async def ws_screen_info(hass, coord, msg):
    return await _screen_info(coord)


@websocket_api.websocket_command({vol.Required("type"): "dosen_pro/screen_set", vol.Optional("seconds"): vol.All(int, vol.Range(min=0, max=3600)),
                                  vol.Optional("luminance"): vol.All(int, vol.Range(min=1, max=100)), vol.Optional("power"): bool, **_BASE})
@_handler
async def ws_screen_set(hass, coord, msg):
    if "power" in msg:
        await coord.api.screen_power(msg["power"])
    if "seconds" in msg or "luminance" in msg:
        await coord.api.screen_sleep(msg.get("seconds"), msg.get("luminance"))
    return await _screen_info(coord)


COMMANDS = (ws_state, ws_set, ws_card_save, ws_card_delete, ws_card_move, ws_rotation, ws_preview, ws_show_now, ws_screen_info,
            ws_screen_set)


def async_register(hass: HomeAssistant) -> None:
    for cmd in COMMANDS:
        websocket_api.async_register_command(hass, cmd)
