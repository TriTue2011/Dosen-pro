"""Ô nhập địa chỉ cho màn hình mặc định (video, ảnh, rtsp://… khi không chọn trong danh sách)."""

from __future__ import annotations

from homeassistant.components.text import RestoreText, TextMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .entity import DosenEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    async_add_entities([DosenDefaultAddress(hass.data[DOMAIN][entry.entry_id])])


class DosenDefaultAddress(DosenEntity, RestoreText):
    """Địa chỉ gõ tay: video (loại «video»), ảnh (nếu không chọn thực thể ảnh), camera rtsp:// (nếu không chọn camera).
    Dạng http(s)://…, rtsp://… hoặc đường dẫn của HA như /local/anh.png. Ẩn nội dung vì URL camera có mật khẩu."""

    _attr_name = "Địa chỉ mặc định"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = TextMode.PASSWORD
    _attr_native_max = 500

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}-default_address"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        cu = await self.async_get_last_text_data()
        if cu is not None and cu.native_value:
            self.coordinator.default_text = cu.native_value

    @property
    def native_value(self) -> str:
        return self.coordinator.default_text

    async def async_set_value(self, value: str) -> None:
        before = self.coordinator.default_text
        self.coordinator.default_text = value
        if self.coordinator.default_mode in ("image", "video", "live"):
            try:
                await self.coordinator.apply_default()
            except Exception:
                self.coordinator.default_text = before
                raise
        self.async_write_ha_state()
