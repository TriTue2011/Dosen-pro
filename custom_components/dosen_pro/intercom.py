"""Bộ đàm: Zalo Call (hay bất cứ ai) POST tiếng A-law 8 kHz theo thời gian thật vào đây, tiếng ra loa camera.

Cùng khuôn với bộ đàm của loa R1 / dahua_talk để app Zalo dùng được: khoá riêng trong địa chỉ (``?k=``), một phiên
một lúc. Trong lúc có cuộc gọi, vệ tinh Assist ngừng nghe để tiếng cuộc gọi không kích hoạt từ khoá đánh thức."""

from __future__ import annotations

import asyncio
import hmac
import logging

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from .api import DosenApiError
from .const import CONF_INTERCOM_KEY, DOMAIN

_LOGGER = logging.getLogger(__name__)


class DosenIntercomView(HomeAssistantView):
    url = "/api/dosen_pro/intercom/{entry_id}"
    name = "api:dosen_pro:intercom"
    requires_auth = False

    async def post(self, request: web.Request, entry_id: str) -> web.Response:
        hass = request.app["hass"]
        coord = hass.data.get(DOMAIN, {}).get(entry_id)
        key = str(coord.entry.data.get(CONF_INTERCOM_KEY) or "") if coord is not None else ""
        if not key or not hmac.compare_digest(key.encode(), request.query.get("k", "").encode()):
            return web.Response(status=401, text="unauthorized")
        if coord.in_call:
            return web.Response(status=409, text="intercom already in use")
        try:
            stream = await coord.api.speaker_open_stream()
        except DosenApiError as exc:
            return web.Response(status=503, text=str(exc))
        coord.set_in_call(True)
        _LOGGER.debug("intercom %s: open", entry_id)
        try:
            while chunk := await request.content.readany():
                await asyncio.to_thread(stream.feed, chunk)
        except Exception as exc:                       # noqa: BLE001 — camera từ chối/đứt giữa chừng: báo, không treo bộ đàm
            _LOGGER.warning("intercom %s: %s", entry_id, exc)
            return web.Response(status=502, text=str(exc))
        finally:
            try:
                await asyncio.to_thread(stream.close)
            finally:
                coord.set_in_call(False)
        return web.json_response({"bytes": stream.bytes_fed})
