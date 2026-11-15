"""Công tắc: tắt mic, nguồn màn hình camera, hiện hội thoại trên màn hình."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.restore_state import RestoreEntity

from .api import DosenApiError
from .const import DOMAIN
from .entity import DosenEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    c = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DosenMuteSwitch(c), DosenScreenPower(c), DosenShowChat(c)])


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


class DosenScreenPower(DosenEntity, SwitchEntity, RestoreEntity):
    """Nguồn màn hình camera (nút «Nguồn điện màn hình: Bật / Tắt» của app iCSee).

    Camera KHÔNG báo ngược nguồn màn hình đang bật hay tắt (app cũng không biết), nên đây là trạng
    thái GIẢ ĐỊNH: nhớ lệnh gần nhất. Màn hình cũng tự sáng/tắt (chạm, hết thời gian) mà HA không thấy,
    vì vậy HA hiện cả hai nút bật và tắt, và mở màn hình hiển thị luôn tự bật nguồn."""

    _attr_name = "Nguồn màn hình"
    _attr_assumed_state = True

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}-screen_power"
        self._attr_is_on = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        cu = await self.async_get_last_state()
        if cu is not None and cu.state in (STATE_ON, STATE_OFF):
            self._attr_is_on = cu.state == STATE_ON

    async def _dat(self, on: bool) -> None:
        try:
            await self.coordinator.api.screen_power(on)
        except DosenApiError as exc:
            raise HomeAssistantError(f"Dosen Pro: {exc}") from exc
        self._attr_is_on = on
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._dat(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._dat(False)


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
