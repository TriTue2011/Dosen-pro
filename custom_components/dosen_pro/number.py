"""Số: âm lượng loa và mic của camera (cài đặt thật của camera, ảnh hưởng chất lượng cuộc gọi). Cài đặt màn hình nằm ở thẻ «Dosen Pro»."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import DosenApiError
from .const import DOMAIN, device_info


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    c = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DosenSpeakerVolume(c), DosenMicVolume(c)])




class _DosenAudioNumber(CoordinatorEntity, NumberEntity):
    """Âm lượng của CHÍNH camera (cài đặt thật, app iCSee cũng thấy). Đọc từ camera lúc khởi động và sau mỗi lần đặt."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER
    _key = ""

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}-audio-{self._key}"
        self._attr_device_info = device_info(coordinator.entry.entry_id)

    @property
    def native_value(self) -> float | None:
        return self.coordinator.audio.get(self._key)

    async def async_set_native_value(self, value: float) -> None:
        try:
            self.coordinator.audio = await self.coordinator.api.audio_set(**{self._key: int(value)})
        except DosenApiError as exc:
            raise HomeAssistantError(f"Dosen Pro: {exc}") from exc
        self.coordinator.async_update_listeners()


class DosenSpeakerVolume(_DosenAudioNumber):
    _attr_name = "Âm lượng loa camera"
    _key = "speaker"


class DosenMicVolume(_DosenAudioNumber):
    _attr_name = "Âm lượng mic camera"
    _key = "mic"
