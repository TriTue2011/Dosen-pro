"""Cài đặt hiển thị của màn hình camera: MỘT chỗ lưu và áp dụng, do thẻ «Dosen Pro» (giao diện) điều khiển qua WebSocket.

Trước đây mỗi cài đặt là một thực thể trên thiết bị (ô chọn, ô số, công tắc…). Nay gom vào đây: màn hình mặc định và nguồn của nó,
thời tiết, xoay hình, kiểu khung, chất lượng, hiện cuộc gọi Zalo. Lưu bằng ``Store`` nên khởi động lại HA vẫn giữ."""

from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .api import DosenApiError
from .const import DEFAULT_MODES
from .dosen_core.screen import FITS, QUALITIES, ROTATIONS

_LOGGER = logging.getLogger(__name__)
_ENTITY = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")

#: khoá → hàm kiểm (trả giá trị đã chuẩn hoá, hoặc ném ValueError)
def _enum(allowed):
    def check(v):
        if v not in allowed:
            raise ValueError(f"phải là một trong {list(allowed)}")
        return v
    return check


def _entity_or_empty(v):
    if v in ("", None):
        return ""
    if not isinstance(v, str) or not _ENTITY.match(v):
        raise ValueError("phải là entity_id (vd. camera.cua) hoặc để trống")
    return v


def _text(v):
    if v is None:
        return ""
    if not isinstance(v, str) or len(v) > 500:
        raise ValueError("phải là chuỗi ngắn")
    return v.strip()


def _ids(v):
    """Danh sách ID người gọi (Zalo threadID / Telegram user_id) cách nhau dấu phẩy hoặc khoảng trắng → chuỗi chuẩn «id1, id2»; trống = mọi người."""
    if v is None:
        return ""
    items = [str(x).strip() for x in (v if isinstance(v, (list, tuple)) else re.split(r"[,\s;]+", str(v))) if str(x).strip()]
    if len(items) > 100 or any(not x.isdigit() or len(x) > 24 for x in items):
        raise ValueError("phải là các ID số (threadID Zalo, ID người dùng Telegram) cách nhau dấu phẩy, tối đa 100")
    return ", ".join(dict.fromkeys(items))


def _bool(v):
    if not isinstance(v, bool):
        raise ValueError("phải là true/false")
    return v


def _rotate(v):
    if isinstance(v, bool) or v not in ROTATIONS:
        raise ValueError(f"phải là một trong {list(ROTATIONS)}")
    return v


VALIDATORS = {
    "default_mode": _enum(DEFAULT_MODES), "default_camera": _entity_or_empty, "default_image": _entity_or_empty,
    "default_text": _text, "default_weather": _entity_or_empty, "default_rotate": _rotate, "default_fit": _enum(FITS),
    "default_quality": _enum(QUALITIES), "zalo_call_screen": _bool, "call_screen_ids": _ids,
}
_SOURCE_KEYS = {"default_mode", "default_camera", "default_image", "default_text"}


class DisplaySettings:
    """Lưu / nạp / áp dụng cài đặt hiển thị của một camera; thuộc tính thật nằm trên coordinator (giữ nguyên tên cũ)."""

    def __init__(self, coordinator) -> None:
        self._c = coordinator
        self._store = Store(coordinator.hass, 1, f"dosen_pro.{coordinator.entry.entry_id}.display")

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self._c, k) for k in VALIDATORS}

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        for key, check in VALIDATORS.items():
            if key in data:
                try:
                    setattr(self._c, key, check(data[key]))
                except ValueError as exc:
                    _LOGGER.warning("Bỏ qua cài đặt hiển thị không hợp lệ %s=%r: %s", key, data[key], exc)

    def _save(self) -> None:
        self._store.async_delay_save(self.as_dict, 0.5)

    async def async_set(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Đặt một hay nhiều cài đặt rồi áp xuống camera. Sai/không áp được thì trả về như cũ và ném ``HomeAssistantError``."""
        unknown = set(changes) - set(VALIDATORS)
        if unknown:
            raise HomeAssistantError(f"Cài đặt không có: {sorted(unknown)}")
        clean = {}
        for key, value in changes.items():
            try:
                clean[key] = VALIDATORS[key](value)
            except ValueError as exc:
                raise HomeAssistantError(f"{key}: {exc}") from exc
        before = self.as_dict()
        c = self._c
        for key, value in clean.items():
            setattr(c, key, value)
        changed = {k for k, v in clean.items() if before[k] != v}
        try:
            if changed & _SOURCE_KEYS:
                await c.apply_default()
            if changed & {"default_rotate", "default_fit"}:
                await c.api.screen_view(c.default_rotate, c.default_fit)
            if "default_quality" in changed:
                await c.api.screen_quality(c.default_quality)
            if "default_weather" in changed:
                c.track_weather()
                await c.push_weather()
        except (HomeAssistantError, DosenApiError) as exc:
            for key in clean:
                setattr(c, key, before[key])                              # không áp dụng được thì giữ như cũ
            c.track_weather()
            if isinstance(exc, HomeAssistantError):
                raise
            raise HomeAssistantError(f"Dosen Pro: {exc}") from exc
        self._save()
        c.async_update_listeners()
        return self.as_dict()
