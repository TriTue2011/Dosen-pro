"""Ô chọn pipeline Assist và độ nhạy "nói xong" cho vệ tinh camera."""

from __future__ import annotations

from homeassistant.components.assist_pipeline import AssistPipelineSelect, VadSensitivitySelect
from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import DosenApiError
from .const import DEFAULT_MODES, DOMAIN, device_info
from .entity import DosenEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    c = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DosenPipelineSelect(hass, c), DosenVadSelect(hass, c), DosenDefaultScreen(c)])


class DosenPipelineSelect(DosenEntity, AssistPipelineSelect):
    def __init__(self, hass: HomeAssistant, coordinator) -> None:
        DosenEntity.__init__(self, coordinator)
        AssistPipelineSelect.__init__(self, hass, DOMAIN, coordinator.entry.entry_id)


class DosenVadSelect(DosenEntity, VadSensitivitySelect):
    def __init__(self, hass: HomeAssistant, coordinator) -> None:
        DosenEntity.__init__(self, coordinator)
        VadSensitivitySelect.__init__(self, hass, coordinator.entry.entry_id)


class DosenDefaultScreen(CoordinatorEntity, SelectEntity):
    """Màn hình mặc định: hiện khi không có sự kiện nào đè lên (off = để màn hình về như bình thường)."""

    _attr_has_entity_name = True
    _attr_name = "Màn hình mặc định"
    _attr_options = DEFAULT_MODES
    _attr_translation_key = None

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}-default_screen"
        self._attr_device_info = device_info(coordinator.entry.entry_id)

    @property
    def current_option(self) -> str | None:
        return ((self.coordinator.data or {}).get("screen", {}).get("default") or {}).get("mode")

    async def async_select_option(self, option: str) -> None:
        src = self.coordinator.default_source
        if option in ("image", "video", "live") and not src:
            raise HomeAssistantError("Nhập «Nguồn màn hình mặc định» (ảnh, video hoặc rtsp://) trước")
        try:
            await self.coordinator.api.screen_default(option, src if option in ("image", "video", "live") else "")
        except DosenApiError as exc:
            raise HomeAssistantError(f"Dosen Pro: {exc}") from exc
        await self.coordinator.async_request_refresh()
