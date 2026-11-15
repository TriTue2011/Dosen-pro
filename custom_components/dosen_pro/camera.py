"""Hình và tiếng của camera Dosen như một camera của HA (RTSP của chính camera), để cuộc gọi Zalo, Frigate, go2rtc...
dùng làm camera và mic. Địa chỉ RTSP có mật khẩu nên chỉ trả qua ``stream_source`` (HA không đưa ra thuộc tính)."""

from __future__ import annotations

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.ffmpeg import async_get_image
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import DOMAIN, RTSP_PORT, RTSP_STREAM, device_info


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    async_add_entities([DosenCamera(hass.data[DOMAIN][entry.entry_id])])


def rtsp_url(data) -> str:
    """Kiểu địa chỉ của camera XM/iCSee: tài khoản và mật khẩu nằm ngay trong đường dẫn."""
    return (f"rtsp://{data[CONF_HOST]}:{RTSP_PORT}/user={data[CONF_USERNAME]}&password={data[CONF_PASSWORD]}"
            f"&channel=1&stream={RTSP_STREAM}.sdp")


class DosenCamera(Camera):
    _attr_has_entity_name = True
    _attr_name = "Camera"
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, coordinator) -> None:
        super().__init__()
        self._entry = coordinator.entry
        self._attr_unique_id = f"{coordinator.entry.entry_id}-camera"
        self._attr_device_info = device_info(coordinator.entry.entry_id)

    async def stream_source(self) -> str | None:
        return rtsp_url(self._entry.data)

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        return await async_get_image(self.hass, rtsp_url(self._entry.data), width=width, height=height)
