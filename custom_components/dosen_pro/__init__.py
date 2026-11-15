"""Dosen Pro: màn hình, loa và mic của camera DOSEN RPO V1 như thiết bị của HA."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DosenApi, DosenApiError
from .const import (CONF_ALARM_EVENTS, DEFAULT_MODES, CONF_API_KEY, CONF_URL, DOMAIN, EVENT_ALARM,
                    SCAN_INTERVAL_SECONDS)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.ASSIST_SATELLITE, Platform.BUTTON, Platform.EVENT, Platform.MEDIA_PLAYER,
             Platform.NUMBER, Platform.SELECT, Platform.SENSOR, Platform.SWITCH, Platform.TEXT]

_MESSAGE = vol.Schema({
    vol.Optional("role", default="info"): vol.In(["user", "assistant", "caller", "info"]),
    vol.Required("text"): cv.string,
})
SERVICE_SCHEMAS = {
    "screen_show": vol.Schema({
        vol.Optional("entry_id"): cv.string,
        vol.Required("source"): cv.string,
        vol.Optional("loop", default=False): cv.boolean,
        vol.Optional("after", default="off"): vol.In(["off", "hold"]),
        vol.Optional("owner", default="automation"): cv.string,
        vol.Optional("ttl"): vol.All(vol.Coerce(float), vol.Range(min=1, max=86400)),
    }),
    "screen_release": vol.Schema({
        vol.Optional("entry_id"): cv.string,
        vol.Required("owner"): cv.string,
    }),
    "screen_default": vol.Schema({
        vol.Optional("entry_id"): cv.string,
        vol.Required("mode"): vol.In(DEFAULT_MODES),
        vol.Optional("source", default=""): cv.string,
    }),
    "screen_text": vol.Schema({
        vol.Optional("entry_id"): cv.string,
        vol.Optional("title", default=""): cv.string,
        vol.Optional("messages", default=[]): vol.All(cv.ensure_list, [vol.Any(cv.string, _MESSAGE)]),
        vol.Optional("font_size", default=15): vol.All(vol.Coerce(int), vol.Range(min=10, max=30)),
        vol.Optional("after", default="hold"): vol.In(["off", "hold"]),
        vol.Optional("owner", default="automation"): cv.string,
        vol.Optional("ttl"): vol.All(vol.Coerce(float), vol.Range(min=1, max=86400)),
    }),
    "screen_off": vol.Schema({vol.Optional("entry_id"): cv.string}),
    "screen_power": vol.Schema({
        vol.Optional("entry_id"): cv.string,
        vol.Required("on"): cv.boolean,
    }),
    "screen_sleep": vol.All(vol.Schema({
        vol.Optional("entry_id"): cv.string,
        vol.Optional("seconds"): vol.All(vol.Coerce(int), vol.Range(min=0, max=3600)),
        vol.Optional("luminance"): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
    }), cv.has_at_least_one_key("seconds", "luminance")),
    "speaker_play": vol.Schema({
        vol.Optional("entry_id"): cv.string,
        vol.Required("source"): cv.string,
        vol.Optional("volume", default=1.0): vol.All(vol.Coerce(float), vol.Range(min=0, max=4)),
    }),
    "speaker_stop": vol.Schema({vol.Optional("entry_id"): cv.string}),
}


class DosenCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: DosenApi) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN,
                         update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS))
        self.api = api
        self.entry = entry
        # Trạng thái chạy của vệ tinh Assist (đặt bởi các thực thể công tắc/số).
        self.mic_muted = False
        self.mic_gain_db = 0.0
        self.show_chat = True
        self.default_source = ""          # nguồn cho màn hình mặc định ảnh/video/live (đặt bởi thực thể text)
        self.mic_level_db: float | None = None   # mức tiếng mic gần nhất (dBFS), None = chưa có luồng
        self._on_level: list = []
        self._on_alarm: list = []
        self._on_mute: list = []
        self._on_gain: list = []

    @callback
    def set_mic_level(self, db: float | None) -> None:
        self.mic_level_db = db
        for fn in list(self._on_level):
            fn()

    @callback
    def listen_level(self, fn):
        self._on_level.append(fn)
        return lambda: self._on_level.remove(fn)

    @callback
    def listen_alarm(self, fn):
        self._on_alarm.append(fn)
        return lambda: self._on_alarm.remove(fn)

    async def _alarm_loop(self) -> None:
        """Mở phiên báo động trên dịch vụ rồi đọc luồng SSE; mỗi báo động thành một sự kiện bus
        ``dosen_pro_alarm`` (để blueprint/automation bắt, kể cả nút gọi của camera khi biết tên event).
        Đứt thì nghỉ 30 giây rồi nối lại, để không dồn dập đăng nhập camera."""
        while True:
            try:
                await self.api.events_start()
                async for ev in self.api.events_stream():
                    payload = {"entry_id": self.entry.entry_id, "event": ev.get("event"),
                               "status": (ev.get("alarm") or {}).get("Status"), "alarm": ev.get("alarm"),
                               "possible_button_press": ev.get("possible_button_press", False)}
                    self.hass.bus.async_fire(EVENT_ALARM, payload)
                    for fn in list(self._on_alarm):
                        fn(payload)
            except DosenApiError as exc:
                _LOGGER.debug("alarm stream: %s", exc)
            await asyncio.sleep(30)

    @callback
    def set_mic_muted(self, muted: bool) -> None:
        self.mic_muted = muted
        for fn in list(self._on_mute):
            fn(muted)

    @callback
    def listen_mute(self, fn):
        self._on_mute.append(fn)
        return lambda: self._on_mute.remove(fn)

    @callback
    def set_mic_gain(self, db: float) -> None:
        if db == self.mic_gain_db:
            return
        self.mic_gain_db = db
        for fn in list(self._on_gain):
            fn(db)

    @callback
    def listen_gain(self, fn):
        self._on_gain.append(fn)
        return lambda: self._on_gain.remove(fn)

    async def _async_update_data(self) -> dict:
        try:
            return {"screen": await self.api.screen_status(),
                    "speaker": await self.api.speaker_status()}
        except DosenApiError as exc:
            raise UpdateFailed(str(exc)) from exc


def _coordinator(hass: HomeAssistant, entry_id: str | None) -> DosenCoordinator:
    coords: dict = hass.data.get(DOMAIN, {})
    if entry_id:
        if entry_id not in coords:
            raise HomeAssistantError(f"không có thiết bị Dosen Pro với entry_id {entry_id}")
        return coords[entry_id]
    if len(coords) == 1:
        return next(iter(coords.values()))
    raise HomeAssistantError("có nhiều thiết bị Dosen Pro: điền entry_id")


async def _handle(hass: HomeAssistant, call: ServiceCall) -> None:
    coord = _coordinator(hass, call.data.get("entry_id"))
    api, d = coord.api, call.data
    try:
        if call.service == "screen_show":
            await api.screen_show(d["source"], d["loop"], d["after"], d["owner"], d.get("ttl"))
        elif call.service == "screen_text":
            await api.screen_text(d["title"], d["messages"], d["font_size"], d["after"], d["owner"],
                                  d.get("ttl"))
        elif call.service == "screen_release":
            await api.screen_release(d["owner"])
        elif call.service == "screen_default":
            await api.screen_default(d["mode"], d["source"])
            coord.default_source = d["source"] or coord.default_source
        elif call.service == "screen_off":
            await api.screen_off()
        elif call.service == "screen_power":
            await api.screen_power(d["on"])
        elif call.service == "screen_sleep":
            await api.screen_sleep(d.get("seconds"), d.get("luminance"))
        elif call.service == "speaker_play":
            await api.speaker_play(d["source"], d["volume"])
        elif call.service == "speaker_stop":
            await api.speaker_stop()
    except DosenApiError as exc:
        raise HomeAssistantError(f"Dosen Pro: {exc}") from exc
    await coord.async_request_refresh()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    api = DosenApi(async_get_clientsession(hass), entry.data[CONF_URL], entry.data.get(CONF_API_KEY, ""))
    coord = DosenCoordinator(hass, entry, api)
    await coord.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord
    if not hass.services.has_service(DOMAIN, "screen_show"):
        for name, schema in SERVICE_SCHEMAS.items():
            async def _svc(call: ServiceCall, _hass=hass) -> None:
                await _handle(_hass, call)
            hass.services.async_register(DOMAIN, name, _svc, schema=schema)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if entry.options.get(CONF_ALARM_EVENTS, True):
        entry.async_create_background_task(hass, coord._alarm_loop(), "dosen_pro alarm events")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            for name in SERVICE_SCHEMAS:
                hass.services.async_remove(DOMAIN, name)
    return ok
