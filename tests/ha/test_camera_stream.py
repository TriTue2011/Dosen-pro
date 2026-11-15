"""Camera Dosen: luồng MJPEG liên tục (một ffmpeg chạy suốt) thay vì chụp lại từng khung."""

import pytest
from test_dosen_pro import _setup

import custom_components.dosen_pro.camera as cam_mod


class _FakeStream:
    instances = []

    def __init__(self, binary):
        self.binary, self.opened, self.closed = binary, None, False
        _FakeStream.instances.append(self)

    async def open_camera(self, source, extra_cmd=None):
        self.opened = (source, extra_cmd)
        return True

    async def get_reader(self):
        return "READER"

    async def close(self):
        self.closed = True


async def _camera(hass):
    entry = await _setup(hass)
    return next(e for e in hass.data["camera"].entities if e.unique_id.endswith("-camera")), entry


async def test_mjpeg_stream_uses_one_continuous_ffmpeg_on_the_camera_rtsp(hass, monkeypatch):
    _FakeStream.instances.clear()
    calls = []

    async def fake_proxy(hass_, request, reader, content_type):
        calls.append((reader, content_type))
        return "RESPONSE"
    monkeypatch.setattr(cam_mod, "CameraMjpeg", _FakeStream)
    monkeypatch.setattr(cam_mod, "async_aiohttp_proxy_stream", fake_proxy)
    cam, entry = await _camera(hass)
    assert await cam.handle_async_mjpeg_stream(object()) == "RESPONSE"
    st = _FakeStream.instances[-1]
    assert st.opened[0] == cam_mod.rtsp_url(entry.data) and st.closed is True            # ffmpeg luôn được dọn
    assert calls and calls[0][0] == "READER"


async def test_mjpeg_stream_closes_ffmpeg_even_when_the_viewer_disconnects(hass, monkeypatch):
    _FakeStream.instances.clear()

    async def boom(*a, **k):
        raise ConnectionResetError("trình duyệt đóng")
    monkeypatch.setattr(cam_mod, "CameraMjpeg", _FakeStream)
    monkeypatch.setattr(cam_mod, "async_aiohttp_proxy_stream", boom)
    cam, _ = await _camera(hass)
    with pytest.raises(ConnectionResetError):
        await cam.handle_async_mjpeg_stream(object())
    assert _FakeStream.instances[-1].closed is True
