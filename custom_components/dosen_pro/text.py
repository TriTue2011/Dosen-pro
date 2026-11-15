"""Ô nhập nguồn cho màn hình mặc định (ảnh, video hoặc camera trực tiếp)."""

from __future__ import annotations

from homeassistant.components.text import RestoreText, TextMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .api import DosenApiError
from .const import DOMAIN
from .entity import DosenEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    async_add_entities([DosenDefaultSource(hass.data[DOMAIN][entry.entry_id])])


class DosenDefaultSource(DosenEntity, RestoreText):
    """Địa chỉ ảnh / video / camera (rtsp://…) cho màn hình mặc định. Ẩn nội dung vì URL camera có mật khẩu."""

    _attr_name = "Nguồn màn hình mặc định"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = TextMode.PASSWORD
    _attr_native_max = 500

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}-default_source"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        cu = await self.async_get_last_text_data()
        if cu is not None and cu.native_value:
            self.coordinator.default_source = cu.native_value

    @property
    def native_value(self) -> str:
        return self.coordinator.default_source

    async def async_set_value(self, value: str) -> None:
        self.coordinator.default_source = value
        mode = ((self.coordinator.data or {}).get("screen", {}).get("default") or {}).get("mode")
        if mode in ("image", "video", "live") and value:        # đang dùng mặc định cần nguồn: áp dụng luôn
            try:
                await self.coordinator.api.screen_default(mode, value)
            except DosenApiError as exc:
                raise HomeAssistantError(f"Dosen Pro: {exc}") from exc
        self.async_write_ha_state()
