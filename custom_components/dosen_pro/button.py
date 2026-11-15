"""Các nút: bật/tắt nguồn màn hình, về màn hình mặc định, dừng loa."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .api import DosenApiError
from .const import DOMAIN
from .entity import DosenEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    c = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        DosenButton(c, "screen_on", "Bật màn hình", lambda api: api.screen_power(True)),
        DosenButton(c, "screen_off", "Tắt màn hình", lambda api: api.screen_power(False)),
        DosenButton(c, "screen_default", "Về màn hình mặc định", lambda api: api.screen_off()),
    ])


class DosenButton(DosenEntity, ButtonEntity):
    def __init__(self, coordinator, key: str, name: str, action) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}-btn-{key}"
        self._attr_name = name
        self._action = action

    async def async_press(self) -> None:
        try:
            await self._action(self.coordinator.api)
        except DosenApiError as exc:
            raise HomeAssistantError(f"Dosen Pro: {exc}") from exc
        await self.coordinator.async_request_refresh()
