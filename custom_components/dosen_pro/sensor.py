"""Cảm biến: chế độ màn hình và nguồn đang hiển thị."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, device_info


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    c = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DosenScreenMode(c), DosenMicLevel(c)])


class DosenScreenMode(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Nội dung màn hình"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["off", "video", "image", "text", "clock", "live"]

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
        return {"source": s.get("source"), "size": s.get("size"), "last_error": s.get("last_error"),
                "layers": s.get("layers"), "default": s.get("default")}


class DosenMicLevel(SensorEntity):
    """Mức tiếng mic camera đang đưa vào Assist (dBFS, cập nhật ~2 lần/giây). Không có số = chưa có luồng mic.
    Nói gần camera mà số vẫn quanh -60 trở xuống thì tăng «Tăng mic»; vệ tinh chỉ nghe được từ gọi khi tiếng đủ to."""

    _attr_has_entity_name = True
    _attr_name = "Mức mic"
    _attr_native_unit_of_measurement = "dB"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.entry.entry_id}-mic_level"
        self._attr_device_info = device_info(coordinator.entry.entry_id)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.coordinator.listen_level(self._khi_doi))

    @callback
    def _khi_doi(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        db = self.coordinator.mic_level_db
        return None if db is None else round(db, 1)
