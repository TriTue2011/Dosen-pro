"""Cuộc gọi Telegram tự lên màn camera (tên người gọi, rồi hình video từ gateway) và lọc theo ID người gọi cho cả Zalo lẫn Telegram."""

from types import SimpleNamespace

from test_dosen_pro import _calls, _cam, _setup
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dosen_pro.const import DOMAIN


class FakeGatewayApi:
    def view_url(self, call_id):
        return "http://172.16.10.200:3333/api/live/view/%s?account=%%2B84900000000" % call_id

    def auth_header(self):
        return "Authorization: Bearer TOKEN-GIA\r\n"


async def _tele_entry(hass):
    e = MockConfigEntry(domain="telegram_call", data={}, title="Telegram")
    e.add_to_hass(hass)
    e.runtime_data = SimpleNamespace(api=FakeGatewayApi())


async def _zalo_entry(hass):
    MockConfigEntry(domain="zalo_call", data={"url": "http://172.16.10.200:8090"}, title="Zalo").add_to_hass(hass)


async def _fire(hass, event, **data):
    hass.bus.async_fire(event, data)
    await hass.async_block_till_done()


async def test_telegram_video_call_shows_name_then_gateway_stream_with_token_then_releases(hass):
    await _tele_entry(hass)
    await _setup(hass)
    await _fire(hass, "telegram_call_call_state", state="incoming", call_id="b1", user_id=5, name="Đại ca", video=True)
    t = _calls("call")[-1]
    assert t == {"name": "Đại ca", "status": "đang gọi đến", "video": True, "color": "red", "owner": "telegram_call", "ttl": 3600} and not _calls("show")
    await _fire(hass, "telegram_call_call_state", state="answered", call_id="b1", user_id=5, name="Đại ca", video=True)
    s = _calls("show")[-1]
    assert s["source"].startswith("http://172.16.10.200:3333/api/live/view/b1") and s["owner"] == "telegram_call_video" and s["live"] is True
    assert ("telegram_call_video", "Authorization: Bearer TOKEN-GIA\r\n") in _cam().screen.headers          # token cho ffmpeg đi bằng header, không nằm trong địa chỉ
    await _fire(hass, "telegram_call_call_state", state="ended", call_id="b1", user_id=5, video=True)
    rel = [c for c in _cam().screen.calls if c[0] == "release"]
    assert ("release", "telegram_call_video") in rel and ("release", "telegram_call") in rel


async def test_telegram_voice_call_shows_text_only_until_the_caller_turns_the_camera_on(hass):
    await _tele_entry(hass)
    await _setup(hass)
    await _fire(hass, "telegram_call_call_state", state="answered", call_id="v1", user_id=5, name="", video=False)
    c = _calls("call")[-1]
    assert c["name"] == "Có người gọi" and c["status"] == "đang nói chuyện" and c["video"] is False and not _calls("show")      # không bao giờ hiện dãy số
    await _fire(hass, "telegram_call_call_state", state="video_request", call_id="v1", user_id=5, video=False)
    assert _calls("show")[-1]["owner"] == "telegram_call_video"
    n = len(_calls("show"))
    await _fire(hass, "telegram_call_call_state", state="video_request", call_id="v1", user_id=5, video=False)
    assert len(_calls("show")) == n                                                  # một lần mỗi cuộc


async def test_only_listed_ids_reach_the_screen_for_telegram_and_zalo(hass):
    await _tele_entry(hass)
    await _zalo_entry(hass)
    entry = await _setup(hass)
    await hass.data[DOMAIN][entry.entry_id].display.async_set({"call_screen_ids": "6518712943, 6643404425553198601"})
    before = len(_cam().screen.calls)
    await _fire(hass, "telegram_call_call_state", state="incoming", call_id="x", user_id=999, name="Lạ", video=True)
    await _fire(hass, "zalo_call_call_state", state="incoming", call_id="y", thread_id="111", peer="Lạ", call_type="video")
    assert len(_cam().screen.calls) == before                                        # người ngoài danh sách: không lên màn
    await _fire(hass, "telegram_call_call_state", state="incoming", call_id="x2", user_id=6518712943, name="Đại ca", video=True)
    assert _calls("call")[-1]["name"] == "Đại ca"
    await _fire(hass, "zalo_call_call_state", state="incoming", call_id="y2", thread_id="6643404425553198601", peer="Đại ca", call_type="video")
    assert _calls("call")[-1]["owner"] == "zalo_call"
    await hass.data[DOMAIN][entry.entry_id].display.async_set({"call_screen_ids": ""})      # trống: mọi người
    n = len(_calls("call"))
    await _fire(hass, "telegram_call_call_state", state="incoming", call_id="x3", user_id=999, name="Lạ", video=True)
    assert len(_calls("call")) == n + 1


async def test_call_screen_ids_setting_is_validated_and_normalised(hass):
    entry = await _setup(hass)
    disp = hass.data[DOMAIN][entry.entry_id].display
    await disp.async_set({"call_screen_ids": "6518712943;  6643404425553198601 6518712943"})
    assert hass.data[DOMAIN][entry.entry_id].call_screen_ids == "6518712943, 6643404425553198601"          # chuẩn hoá, bỏ trùng
    for bad in ("abc", "12, x9"):
        try:
            await disp.async_set({"call_screen_ids": bad})
        except Exception:
            continue
        raise AssertionError("phải từ chối " + bad)
