"""Thực thể sự kiện: mọi báo động camera đẩy tới (phát hiện người, và nút gọi nếu camera báo)."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .entity import DosenEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    async_add_entities([DosenAlarmEvent(hass.data[DOMAIN][entry.entry_id])])


class DosenAlarmEvent(DosenEntity, EventEntity):
    _attr_name = "Sự kiện camera"
    _attr_event_types = ["alarm"]

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}-alarm"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.listen_alarm(self._khi_co_bao_dong))

    @callback
    def _khi_co_bao_dong(self, payload: dict) -> None:
        self._trigger_event("alarm", {"event": payload.get("event"), "status": payload.get("status"),
                                      "possible_button_press": payload.get("possible_button_press")})
        self.async_write_ha_state()
