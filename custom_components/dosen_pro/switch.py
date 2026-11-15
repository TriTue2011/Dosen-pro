"""Công tắc: tắt mic, hiện hội thoại trên màn hình. (Bật/tắt nguồn màn hình là NÚT, xem button.py.)"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import DosenApiError
from .const import DOMAIN, device_info
from .entity import DosenEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    c = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DosenMuteSwitch(c), DosenShowChat(c), DosenMicAnr(c), DosenKeyTip(c), DosenCarousel(c)])


class DosenMuteSwitch(DosenEntity, SwitchEntity, RestoreEntity):
    _attr_name = "Tắt mic"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}-mute"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        cu = await self.async_get_last_state()
        self.coordinator.set_mic_muted(cu is not None and cu.state == STATE_ON)

    @property
    def is_on(self) -> bool:
        return self.coordinator.mic_muted

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.set_mic_muted(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.set_mic_muted(False)
        self.async_write_ha_state()


class DosenShowChat(DosenEntity, SwitchEntity, RestoreEntity):
    _attr_name = "Hiện hội thoại trên màn hình"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}-show_chat"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        cu = await self.async_get_last_state()
        self.coordinator.show_chat = cu is None or cu.state != STATE_OFF

    @property
    def is_on(self) -> bool:
        return self.coordinator.show_chat

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.show_chat = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.show_chat = False
        self.async_write_ha_state()


class DosenMicAnr(CoordinatorEntity, SwitchEntity):
    """Chống ồn của mic camera (AnrOpen). Cài đặt thật của camera, đọc lúc khởi động và sau mỗi lần đặt."""

    _attr_has_entity_name = True
    _attr_name = "Chống ồn mic camera"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}-audio-anr"
        self._attr_device_info = device_info(coordinator.entry.entry_id)

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.audio.get("anr")

    async def _dat(self, on: bool) -> None:
        try:
            self.coordinator.audio = await self.coordinator.api.audio_set(anr=on)
        except DosenApiError as exc:
            raise HomeAssistantError(f"Dosen Pro: {exc}") from exc
        self.coordinator.async_update_listeners()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._dat(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._dat(False)


class DosenKeyTip(CoordinatorEntity, SwitchEntity):
    """Tiếng bíp khi bấm nút gọi / phím trên thân camera (``Consumer.CommDevCfg.KeyAudioTip``). Cài đặt thật của camera."""

    _attr_has_entity_name = True
    _attr_name = "Tiếng bíp khi nhấn nút"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}-key-tip"
        self._attr_device_info = device_info(coordinator.entry.entry_id)

    @property
    def available(self) -> bool:
        return self.coordinator.audio.get("key_tip") is not None          # camera không có cài đặt này thì ẩn đi

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.audio.get("key_tip")

    async def _dat(self, on: bool) -> None:
        try:
            self.coordinator.audio = await self.coordinator.api.audio_set(key_tip=on)
        except DosenApiError as exc:
            raise HomeAssistantError(f"Dosen Pro: {exc}") from exc
        self.coordinator.async_update_listeners()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._dat(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._dat(False)


class DosenCarousel(CoordinatorEntity, SwitchEntity):
    """Màn luân phiên: bật thì các «trang» (đồng hồ, thẻ thông tin, ảnh, camera…) lần lượt hiện lên màn. Trang đặt bằng
    dịch vụ ``dosen_pro.screen_carousel``; công tắc này chỉ bật/tắt và nhớ trạng thái."""

    _attr_has_entity_name = True
    _attr_name = "Luân phiên màn hình"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}-carousel"
        self._attr_device_info = device_info(coordinator.entry.entry_id)

    @property
    def is_on(self) -> bool:
        return self.coordinator.carousel.on

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        car = self.coordinator.carousel
        return {"pages": [p["type"] for p in car.pages], "seconds": car.seconds,
                "current": car.index if car.running and car.index >= 0 else None}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.carousel.async_start()          # chưa có trang thì ném lỗi rõ ràng cho người dùng
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.carousel.async_stop()
        self.async_write_ha_state()
