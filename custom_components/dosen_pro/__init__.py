"""Dosen Pro: màn hình, loa và mic của camera DOSEN RPO V1 như thiết bị của HA."""

from __future__ import annotations

import logging
from datetime import timedelta

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DosenApi, DosenApiError
from .const import CONF_API_KEY, CONF_URL, DOMAIN, SCAN_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.MEDIA_PLAYER, Platform.SENSOR]

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
    }),
    "screen_text": vol.Schema({
        vol.Optional("entry_id"): cv.string,
        vol.Optional("title", default=""): cv.string,
        vol.Optional("messages", default=[]): vol.All(cv.ensure_list, [vol.Any(cv.string, _MESSAGE)]),
        vol.Optional("font_size", default=15): vol.All(vol.Coerce(int), vol.Range(min=10, max=30)),
        vol.Optional("after", default="hold"): vol.In(["off", "hold"]),
    }),
    "screen_off": vol.Schema({vol.Optional("entry_id"): cv.string}),
    "screen_power": vol.Schema({
        vol.Optional("entry_id"): cv.string,
        vol.Required("on"): cv.boolean,
    }),
    "screen_sleep": vol.Schema({
        vol.Optional("entry_id"): cv.string,
        vol.Required("seconds"): vol.All(vol.Coerce(int), vol.Range(min=0, max=3600)),
    }),
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
            await api.screen_show(d["source"], d["loop"], d["after"])
        elif call.service == "screen_text":
            await api.screen_text(d["title"], d["messages"], d["font_size"], d["after"])
        elif call.service == "screen_off":
            await api.screen_off()
        elif call.service == "screen_power":
            await api.screen_power(d["on"])
        elif call.service == "screen_sleep":
            await api.screen_sleep(d["seconds"])
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
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            for name in SERVICE_SCHEMAS:
                hass.services.async_remove(DOMAIN, name)
    return ok
