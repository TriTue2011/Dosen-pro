"""Cuộc gọi Zalo tự lên màn camera: chữ tên người gọi, rồi hình video (nguồn trực tiếp, độ trễ thấp)."""

from test_dosen_pro import _calls, _cam, _setup
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dosen_pro.const import DOMAIN


async def _zalo_entry(hass, url="http://172.16.10.200:8090"):
    MockConfigEntry(domain="zalo_call", data={"url": url}, title="Zalo").add_to_hass(hass)


async def _fire(hass, **data):
    hass.bus.async_fire("zalo_call_call_state", data)
    await hass.async_block_till_done()


async def test_video_call_shows_caller_then_live_video_then_releases(hass):
    await _zalo_entry(hass)
    await _setup(hass)
    await _fire(hass, state="incoming", peer="Bà", call_id="7", call_type="video")
    t = _calls("text")[-1]
    assert t["owner"] == "zalo_call" and "Bà đang gọi đến" in t["messages"][0]["text"] and not _calls("show")
    await _fire(hass, state="answered", peer="Bà", call_id="7", call_type="video")
    s = _calls("show")[-1]
    assert s["source"] == "http://172.16.10.200:8090/api/call/video/view?call_id=7" and s["owner"] == "zalo_call_video"
    assert s["live"] is True                                     # hình cuộc gọi đi đường trực tiếp, bỏ hàng đợi cũ
    await _fire(hass, state="ended", peer="Bà", call_id="7")
    rel = [c for c in _cam().screen.calls if c[0] == "release"]
    assert ("release", "zalo_call_video") in rel and ("release", "zalo_call") in rel


async def test_voice_call_shows_text_only(hass):
    await _zalo_entry(hass)
    await _setup(hass)
    await _fire(hass, state="answered", peer="Ông", call_id="8", call_type="voice")
    assert "Ông đang nói chuyện" in _calls("text")[-1]["messages"][0]["text"] and not _calls("show")


async def test_no_zalo_integration_means_no_video_layer(hass):
    await _setup(hass)
    await _fire(hass, state="answered", peer="Bà", call_id="9", call_type="video")
    assert _calls("text") and not _calls("show")


async def test_switch_off_ignores_calls(hass):
    await _zalo_entry(hass)
    entry = await _setup(hass)
    await hass.data[DOMAIN][entry.entry_id].display.async_set({"zalo_call_screen": False})
    before = len(_cam().screen.calls)
    await _fire(hass, state="incoming", peer="Bà", call_id="1", call_type="video")
    assert len(_cam().screen.calls) == before


async def test_screen_show_service_passes_live_flag(hass):
    await _setup(hass)
    await hass.services.async_call(DOMAIN, "screen_show", {"source": "rtsp://cam/1", "live": True}, blocking=True)
    assert _calls("show")[-1]["live"] is True
