"""Cầu nối giữa tích hợp HA (async) và lõi ``dosen_core`` (luồng + socket chặn)."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from .dosen_core.dvrip import DVRIPError


class DosenApiError(Exception):
    """Camera từ chối, không tới được, hoặc tham số sai."""


class DosenAuthError(DosenApiError):
    """Tài khoản/mật khẩu sai hoặc tài khoản camera bị khoá."""


def phan_loai(exc: Exception) -> DosenApiError:
    """Đổi lỗi của lõi thành lỗi của tích hợp: sai mật khẩu / bị khoá → DosenAuthError."""
    text = str(exc)
    if isinstance(exc, DVRIPError) and (re.search(r"Ret=20[3-7]\b", text) or "login paused" in text):
        return DosenAuthError(text)
    return DosenApiError(text)


class LocalApi:
    """Cùng bộ hàm async mà tích hợp vẫn gọi, nhưng gọi thẳng camera (không qua dịch vụ nào)."""

    def __init__(self, camera) -> None:
        self.camera = camera

    async def _run(self, fn, *args: Any):
        try:
            return await asyncio.to_thread(fn, *args)
        except (OSError, DVRIPError, ValueError, RuntimeError) as exc:
            raise phan_loai(exc) from exc

    # -- trạng thái (không chạm camera) -----------------------------------
    async def screen_status(self) -> dict[str, Any]:
        return self.camera.screen.status()

    async def speaker_status(self) -> dict[str, Any]:
        return self.camera.speaker.status()

    # -- màn hình -----------------------------------------------------------
    async def screen_show(self, source: str, loop: bool = False, after: str = "off",
                          owner: str = "automation", ttl: float | None = None) -> dict[str, Any]:
        return await self._run(self.camera.screen.show, source, loop, after, owner, ttl)

    async def screen_text(self, title: str, messages: list, font_size: int = 15, after: str = "hold",
                          owner: str = "automation", ttl: float | None = None) -> dict[str, Any]:
        return await self._run(self.camera.screen.show_text, title, messages, font_size, after, owner, ttl)

    async def screen_release(self, owner: str) -> dict[str, Any]:
        return await self._run(self.camera.screen.release, owner)

    async def screen_default(self, mode: str, source: str = "") -> dict[str, Any]:
        return await self._run(self.camera.screen.set_default, mode, source)

    async def screen_off(self) -> dict[str, Any]:
        return await self._run(self.camera.screen.off)

    async def screen_power(self, on: bool) -> dict[str, Any]:
        return await self._run(self.camera.screen.power, on)

    async def screen_sleep(self, seconds: int | None = None, luminance: int | None = None) -> dict[str, Any]:
        return await self._run(self.camera.screen.set_sleep, seconds, luminance)

    # -- loa ----------------------------------------------------------------
    async def speaker_play(self, source: str, volume: float = 1.0) -> dict[str, Any]:
        return await self._run(self.camera.speaker.play, source, volume)

    async def speaker_play_bytes(self, audio: bytes, volume: float = 1.0) -> dict[str, Any]:
        return await self._run(self.camera.speaker.play_bytes, audio, volume)

    async def speaker_stop(self) -> dict[str, Any]:
        return await self._run(self.camera.speaker.stop)

    # -- mic ----------------------------------------------------------------
    async def mic_start(self) -> None:
        if not self.camera.mic.running:
            await self._run(self.camera.mic.start)

    async def mic_read(self, timeout: float = 1.0) -> bytes | None:
        """Khúc PCM16 8 kHz mono kế tiếp, hoặc None nếu chưa có / luồng đã đứt (xem ``mic_alive``)."""
        return await asyncio.to_thread(self.camera.mic.read_chunk, timeout)

    @property
    def mic_alive(self) -> bool:
        return self.camera.mic.running

    async def mic_stop(self) -> None:
        await asyncio.to_thread(self.camera.mic.stop)

    # -- báo động -----------------------------------------------------------
    async def events_start(self) -> None:
        if not self.camera.events.running:
            await self._run(self.camera.events.start)
