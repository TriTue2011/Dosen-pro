"""Số: mức tăng mic (HA), thời gian sáng và độ sáng của màn hình camera."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.const import EntityCategory, UnitOfSoundPressure, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import DosenApiError
from .const import DOMAIN, MIC_GAIN_MAX, device_info
from .entity import DosenEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    c = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DosenMicGain(c), DosenScreenTime(c), DosenScreenBrightness(c), DosenSpeakerVolume(c),
                        DosenMicVolume(c)])


class DosenMicGain(DosenEntity, RestoreNumber):
    _attr_name = "Tăng mic"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = MIC_GAIN_MAX
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfSoundPressure.DECIBEL
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}-mic_gain"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        cu = await self.async_get_last_number_data()
        if cu is not None and cu.native_value is not None:
            self.coordinator.set_mic_gain(float(cu.native_value))

    @property
    def native_value(self) -> float:
        return self.coordinator.mic_gain_db

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.set_mic_gain(float(value))
        self.async_write_ha_state()


class _DosenScreenNumber(DosenEntity, RestoreNumber):
    """Số ghi xuống camera. Camera không báo ngược giá trị nên đây là giá trị đã đặt gần nhất."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _key = ""

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}-{self._key}"
        self._value: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        cu = await self.async_get_last_number_data()
        if cu is not None:
            self._value = cu.native_value

    @property
    def native_value(self) -> float | None:
        return self._value

    async def _gui(self, value: int) -> None:
        raise NotImplementedError

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self._gui(int(value))
        except DosenApiError as exc:
            raise HomeAssistantError(f"Dosen Pro: {exc}") from exc
        self._value = value
        self.async_write_ha_state()


class DosenScreenTime(_DosenScreenNumber):
    _attr_name = "Thời gian sáng màn hình"
    _attr_native_min_value = 0
    _attr_native_max_value = 3600
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _key = "screen_time"

    async def _gui(self, value: int) -> None:
        await self.coordinator.api.screen_sleep(seconds=value)      # 0 = không bao giờ tắt


class DosenScreenBrightness(_DosenScreenNumber):
    _attr_name = "Độ sáng màn hình"
    _attr_native_min_value = 1
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _key = "screen_brightness"

    async def _gui(self, value: int) -> None:
        await self.coordinator.api.screen_sleep(luminance=value)


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
