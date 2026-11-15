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


def _absolute(hass: HomeAssistant, url: str, expire: timedelta = LONG_LIVED, base: str | None = None) -> str:
    """Đường dẫn nội bộ của HA (/media/..., /api/..., /local/...) → địa chỉ tuyệt đối có chữ ký; địa chỉ ngoài giữ nguyên.
    Địa chỉ HA dùng: ``base`` (do người dùng đặt trong «Cấu hình»), không có thì địa chỉ nội bộ, rồi địa chỉ ngoài."""
    if not url.startswith("/"):
        return url
    if not base and hass.config.api is not None:
        base = f"http://127.0.0.1:{hass.config.api.port}"       # ffmpeg chạy ngay trong HA: gọi thẳng chính nó
    if not base:
        try:
            base = get_url(hass, prefer_external=False)          # nội bộ trước, không có thì địa chỉ ngoài
        except NoURLAvailableError as exc:
            raise HomeAssistantError(
                "HA chưa có địa chỉ để dịch vụ tải ảnh/video: đặt «Địa chỉ HA» trong Cấu hình của Dosen Pro "
                "hoặc địa chỉ nội bộ ở Cài đặt → Hệ thống → Mạng") from exc
    return base.rstrip("/") + async_sign_path(hass, url, expire)


async def resolve_media(hass: HomeAssistant, media: Any, base: str | None = None) -> str:
    """``media``: chuỗi (địa chỉ hay media-source://…) hoặc dict của bộ chọn media (có media_content_id)."""
    media_id = media.get("media_content_id") if isinstance(media, dict) else media
    if not media_id:
        raise HomeAssistantError("Chưa chọn media")
    if media_source.is_media_source_id(media_id):
        media_id = (await media_source.async_resolve_media(hass, media_id, None)).url
    return _absolute(hass, str(media_id), base=base)


async def resolve_camera(hass: HomeAssistant, entity_id: str, base: str | None = None) -> str:
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
    return _absolute(hass, f"/api/camera_proxy_stream/{entity_id}?token={token}", timedelta(days=1), base)


async def resolve_image(hass: HomeAssistant, entity_id: str, base: str | None = None) -> str:
    """Ảnh của một thực thể ``image`` trong HA (có chữ ký truy cập của chính thực thể)."""
    comp = hass.data.get("image")
    ent = comp.get_entity(entity_id) if comp is not None else None
    if ent is None:
        raise HomeAssistantError(f"Không tìm thấy thực thể ảnh {entity_id}")
    token = (ent.access_tokens or [None])[-1]
    if not token:
        raise HomeAssistantError(f"{entity_id} không có ảnh để chiếu")
    return _absolute(hass, f"/api/image_proxy/{entity_id}?token={token}", timedelta(days=1), base)


async def resolve_source(hass: HomeAssistant, *, source: str | None = None, camera_entity: str | None = None,
                         media: Any = None, image_entity: str | None = None, base: str | None = None) -> str:
    """Thứ tự ưu tiên: camera của HA, thực thể ảnh, media, rồi địa chỉ gõ tay."""
    if camera_entity:
        return await resolve_camera(hass, camera_entity, base)
    if image_entity:
        return await resolve_image(hass, image_entity, base)
    if media:
        return await resolve_media(hass, media, base)
    if source:
        return _absolute(hass, source, base=base)
    raise HomeAssistantError("Chưa chọn nguồn (camera, media hoặc địa chỉ)")
