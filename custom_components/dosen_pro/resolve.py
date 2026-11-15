"""Đổi lựa chọn trong HA (camera, kho media, địa chỉ) thành địa chỉ mà dịch vụ Dosen đọc được."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components import media_source
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.network import NoURLAvailableError, get_url

#: Địa chỉ ký sẵn cho màn hình mặc định phải sống lâu (video lặp đọc lại tệp liên tục).
LONG_LIVED = timedelta(days=365)


def _absolute(hass: HomeAssistant, url: str, expire: timedelta = LONG_LIVED) -> str:
    """Đường dẫn nội bộ của HA (/media/..., /api/...) → địa chỉ tuyệt đối có chữ ký; địa chỉ ngoài giữ nguyên."""
    if not url.startswith("/"):
        return url
    try:
        base = get_url(hass, allow_external=False, prefer_external=False)
    except NoURLAvailableError as exc:
        raise HomeAssistantError("HA chưa có địa chỉ nội bộ (Cài đặt → Hệ thống → Mạng)") from exc
    return base + async_sign_path(hass, url, expire)


async def resolve_media(hass: HomeAssistant, media: Any) -> str:
    """``media``: chuỗi (địa chỉ hay media-source://…) hoặc dict của bộ chọn media (có media_content_id)."""
    media_id = media.get("media_content_id") if isinstance(media, dict) else media
    if not media_id:
        raise HomeAssistantError("Chưa chọn media")
    if media_source.is_media_source_id(media_id):
        media_id = (await media_source.async_resolve_media(hass, media_id, None)).url
    return _absolute(hass, str(media_id))


async def resolve_camera(hass: HomeAssistant, entity_id: str) -> str:
    """Luồng xem của một camera trong HA: địa chỉ nguồn (rtsp://…) nếu camera có, không thì luồng MJPEG có chữ ký."""
    comp = hass.data.get("camera")
    cam = comp.get_entity(entity_id) if comp is not None else None
    if cam is None:
        raise HomeAssistantError(f"Không tìm thấy camera {entity_id}")
    src = await cam.stream_source()
    if src:
        return src
    token = (cam.access_tokens or [None])[-1]
    if not token:
        raise HomeAssistantError(f"{entity_id} không có luồng xem")
    return _absolute(hass, f"/api/camera_proxy_stream/{entity_id}?token={token}", timedelta(days=1))


async def resolve_source(hass: HomeAssistant, *, source: str | None = None, camera_entity: str | None = None,
                         media: Any = None) -> str:
    """Thứ tự ưu tiên: camera của HA, rồi media, rồi địa chỉ gõ tay."""
    if camera_entity:
        return await resolve_camera(hass, camera_entity)
    if media:
        return await resolve_media(hass, media)
    if source:
        return _absolute(hass, source)
    raise HomeAssistantError("Chưa chọn nguồn (camera, media hoặc địa chỉ)")
