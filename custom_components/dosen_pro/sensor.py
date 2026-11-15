"""Cảm biến: chế độ màn hình và nguồn đang hiển thị."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, device_info


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    async_add_entities([DosenScreenMode(hass.data[DOMAIN][entry.entry_id])])


class DosenScreenMode(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Nội dung màn hình"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["off", "video", "image", "text"]

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_screen"
        self._attr_device_info = device_info(coordinator.entry.entry_id)

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("screen", {}).get("mode")

    @property
    def extra_state_attributes(self):
        s = (self.coordinator.data or {}).get("screen", {})
        return {"source": s.get("source"), "after": s.get("after"), "size": s.get("size"),
                "last_error": s.get("last_error")}
