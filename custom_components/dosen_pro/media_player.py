"""Loa của camera như một media_player: phát nhạc, TTS (Assist), âm thanh bất kỳ."""

from __future__ import annotations

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseMedia, MediaPlayerEntity, MediaPlayerEntityFeature, MediaPlayerState, MediaType,
    async_process_play_media_url)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import DosenApiError
from .const import DOMAIN, device_info


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    async_add_entities([DosenSpeaker(hass.data[DOMAIN][entry.entry_id])])


class DosenSpeaker(CoordinatorEntity, MediaPlayerEntity):
    _attr_has_entity_name = True
    _attr_name = "Loa"
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.VOLUME_SET | MediaPlayerEntityFeature.BROWSE_MEDIA)

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_speaker"
        self._attr_volume_level = 0.5          # 0.5 = âm lượng gốc của nguồn; 1.0 = gấp đôi
        self._attr_device_info = device_info(coordinator.entry.entry_id)

    @property
    def state(self):
        return (MediaPlayerState.PLAYING
                if (self.coordinator.data or {}).get("speaker", {}).get("playing")
                else MediaPlayerState.IDLE)

    async def async_play_media(self, media_type, media_id, **kwargs) -> None:
        if media_source.is_media_source_id(media_id):
            media_id = (await media_source.async_resolve_media(self.hass, media_id, self.entity_id)).url
        media_id = async_process_play_media_url(self.hass, media_id)
        try:
            await self.coordinator.api.speaker_play(media_id, self._attr_volume_level * 2)
        except DosenApiError as exc:
            raise RuntimeError(f"Dosen Pro: {exc}") from exc
        await self.coordinator.async_request_refresh()

    async def async_media_stop(self) -> None:
        await self.coordinator.api.speaker_stop()
        await self.coordinator.async_request_refresh()

    async def async_set_volume_level(self, volume: float) -> None:
        self._attr_volume_level = volume        # áp dụng cho lần phát kế tiếp
        self.async_write_ha_state()

    async def async_browse_media(self, media_content_type=None, media_content_id=None) -> BrowseMedia:
        return await media_source.async_browse_media(
            self.hass, media_content_id,
            content_filter=lambda item: item.media_content_type.startswith("audio/"))
