"""Máy khách HTTP cho dịch vụ Docker Dosen Pro (thư mục server/)."""

from __future__ import annotations

from typing import Any

import aiohttp


class DosenApiError(Exception):
    """Dịch vụ trả lỗi hoặc không tới được."""


class DosenAuthError(DosenApiError):
    """Khoá API sai hoặc thiếu."""


class DosenApi:
    def __init__(self, session: aiohttp.ClientSession, url: str, api_key: str = "") -> None:
        self._session = session
        self.url = url.rstrip("/")
        self._headers = {"X-API-Key": api_key} if api_key else {}

    @property
    def mic_url(self) -> str:
        """Luồng mic của camera (WAV 8 kHz mono, không kết thúc) cho ffmpeg của HA."""
        return self.url + "/api/audio/mic?format=wav"

    @property
    def mic_headers(self) -> str | None:
        """Giá trị cho ``ffmpeg -headers`` (khoá API), hoặc None khi không có khoá."""
        return f"X-API-Key: {self._headers['X-API-Key']}\r\n" if self._headers else None

    async def _call(self, method: str, path: str, body: dict | None = None, *,
                    data: bytes | None = None, params: dict | None = None) -> dict[str, Any]:
        try:
            async with self._session.request(
                method, self.url + path, json=body, data=data, params=params,
                headers=({**self._headers, "Content-Type": "application/octet-stream"}
                         if data is not None else self._headers),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 401:
                    raise DosenAuthError("khoá API sai hoặc thiếu")
                data = await resp.json(content_type=None)
                if resp.status >= 400:
                    msg = data.get("message") if isinstance(data, dict) else None
                    raise DosenApiError(msg or f"HTTP {resp.status}")
                return data if isinstance(data, dict) else {}
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise DosenApiError(str(exc)) from exc

    async def health(self) -> dict[str, Any]:
        return await self._call("GET", "/api/health")

    async def status(self) -> dict[str, Any]:
        """Cần khoá API (khác health): dùng để kiểm khoá lúc cấu hình."""
        return await self._call("GET", "/api/status")

    async def screen_status(self) -> dict[str, Any]:
        return await self._call("GET", "/api/screen/status")

    async def speaker_status(self) -> dict[str, Any]:
        return await self._call("GET", "/api/speaker/status")

    async def screen_show(self, source: str, loop: bool = False, after: str = "off") -> dict[str, Any]:
        return await self._call("POST", "/api/screen/show", {"source": source, "loop": loop, "after": after})

    async def screen_text(self, title: str, messages: list, font_size: int = 15,
                          after: str = "hold") -> dict[str, Any]:
        return await self._call("POST", "/api/screen/text", {
            "title": title, "messages": messages, "font_size": font_size, "after": after})

    async def screen_off(self) -> dict[str, Any]:
        return await self._call("POST", "/api/screen/off")

    async def screen_power(self, on: bool) -> dict[str, Any]:
        return await self._call("POST", "/api/screen/power", {"on": on})

    async def screen_sleep(self, seconds: int | None = None,
                           luminance: int | None = None) -> dict[str, Any]:
        body = {k: v for k, v in (("seconds", seconds), ("luminance", luminance)) if v is not None}
        return await self._call("POST", "/api/screen/sleep", body)

    async def speaker_play(self, source: str, volume: float = 1.0) -> dict[str, Any]:
        return await self._call("POST", "/api/speaker/play", {"source": source, "volume": volume})

    async def speaker_play_bytes(self, audio: bytes, volume: float = 1.0) -> dict[str, Any]:
        """Phát âm thanh chỉ có trong HA (câu trả lời TTS của Assist) ra loa camera."""
        return await self._call("POST", "/api/speaker/play_bytes", data=audio, params={"volume": volume})

    async def speaker_stop(self) -> dict[str, Any]:
        return await self._call("POST", "/api/speaker/stop")
