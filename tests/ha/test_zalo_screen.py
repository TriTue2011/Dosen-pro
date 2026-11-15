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
    t = _calls("call")[-1]
    assert t == {"name": "Bà", "status": "đang gọi đến", "video": True, "color": "red", "owner": "zalo_call", "ttl": 3600} and not _calls("show")
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
    c = _calls("call")[-1]
    assert c["name"] == "Ông" and c["status"] == "đang nói chuyện" and c["video"] is False and c["color"] == "green" and not _calls("show")


async def test_no_zalo_integration_means_no_video_layer(hass):
    await _setup(hass)
    await _fire(hass, state="answered", peer="Bà", call_id="9", call_type="video")
    assert _calls("call") and not _calls("show")


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


async def test_screen_services_pass_priority_and_split_to_the_screen(hass):
    await _setup(hass)
    await hass.services.async_call(DOMAIN, "screen_show", {"source": "rtsp://cam/1", "priority": 3, "split": True}, blocking=True)
    s = _calls("show")[-1]
    assert s["priority"] == 3 and s["split"] is True
    await hass.services.async_call(DOMAIN, "screen_text", {"title": "x", "messages": ["y"], "priority": 4}, blocking=True)
    assert _calls("text")[-1]["priority"] == 4
    await hass.services.async_call(DOMAIN, "screen_show", {"source": "rtsp://cam/2"}, blocking=True)
    assert "priority" not in _calls("show")[-1] and "split" not in _calls("show")[-1]       # mặc định: mức 2, không chia đôi


async def test_camera_layers_get_a_refresher_that_asks_ha_for_a_fresh_stream(hass, monkeypatch):
    """Luồng camera của HA có token quay vòng: màn hình phải xin lại địa chỉ mỗi lần ffmpeg chạy lại (nếu không: 401 mãi sau khi rút nguồn)."""
    import asyncio

    import custom_components.dosen_pro as pkg
    urls = iter(["http://ha/stream?token=A", "http://ha/stream?token=B", "http://ha/stream?token=C"])

    async def fake_resolve(hass_, entity_id, base=None):
        return next(urls)
    monkeypatch.setattr(pkg, "resolve_camera", fake_resolve)

    async def fake_source(hass_, **kw):
        return "http://ha/stream?token=A" if kw.get("camera_entity") else "x"
    monkeypatch.setattr(pkg, "resolve_source", fake_source)
    await _setup(hass)
    await hass.services.async_call(DOMAIN, "screen_show", {"camera_entity": "camera.cua", "owner": "cua"}, blocking=True)
    owner, refresh = [r for r in _cam().screen.refreshers if r[0] == "cua"][-1]
    assert refresh is not None
    assert await asyncio.to_thread(refresh) == "http://ha/stream?token=A"              # từ luồng của màn hình: xin địa chỉ mới
    assert await asyncio.to_thread(refresh) == "http://ha/stream?token=B"
    assert refresh() is None                                                          # ở luồng sự kiện thì không chặn: dùng địa chỉ cũ
    await hass.services.async_call(DOMAIN, "screen_show", {"source": "http://x/clip.mp4", "owner": "tep"}, blocking=True)
    assert [r for r in _cam().screen.refreshers if r[0] == "tep"][-1][1] is None      # nguồn thường không cần làm mới


class _FakeZaloRuntime:
    """Giống đối tượng tích hợp Zalo Call giữ trong hass.data: có .api.contacts()."""

    def __init__(self, contacts=None, error=None):
        self.calls = 0
        outer = self

        class Api:
            async def contacts(self):
                outer.calls += 1
                if error:
                    raise error
                return contacts
        self.api = Api()


async def test_caller_shows_the_saved_contact_name_never_the_thread_id(hass):
    """Sự kiện cuộc gọi đến chỉ mang mã người (thread_id) trong «peer»: phải tra tên trong danh bạ Zalo."""
    await _zalo_entry(hass)
    hass.data["zalo_call"] = {"e1": _FakeZaloRuntime({"friends": {"rows": [{"id": "6598123", "name": "Nguyễn Thị Thu Hương"}]},
                                                      "groups": {"rows": [{"id": "77", "name": "Cả nhà"}]}})}
    await _setup(hass)
    await _fire(hass, state="incoming", peer="6598123", thread_id="6598123", call_id="1", call_type="user")
    c = _calls("call")[-1]
    assert c["name"] == "Nguyễn Thị Thu Hương" and c["video"] is None             # sự kiện không nói rõ loại: nhãn trung tính
    await _fire(hass, state="answered", peer="6598123", thread_id="6598123", call_id="1", call_type="user")
    assert _calls("call")[-1]["name"] == "Nguyễn Thị Thu Hương" and _calls("show")   # vẫn thử hình video (cuộc gọi thoại thì app trả 409, lớp tự rơi)
    assert hass.data["zalo_call"]["e1"].calls == 1                                 # danh bạ chỉ lấy một lần cho cả cuộc gọi


async def test_unknown_numeric_id_shows_a_neutral_label_not_the_number(hass):
    await _zalo_entry(hass)
    hass.data["zalo_call"] = {"e1": _FakeZaloRuntime({"friends": {"rows": []}})}
    await _setup(hass)
    await _fire(hass, state="incoming", peer="123456789", thread_id="123456789", call_id="2", call_type="user")
    assert _calls("call")[-1]["name"] == "Có người gọi"


async def test_contacts_failure_still_shows_the_call(hass):
    await _zalo_entry(hass)
    hass.data["zalo_call"] = {"e1": _FakeZaloRuntime(error=TimeoutError("app chậm"))}
    await _setup(hass)
    await _fire(hass, state="incoming", peer="Bà Năm", thread_id="55", call_id="3", call_type="user")
    assert _calls("call")[-1]["name"] == "Bà Năm"                                  # tên có sẵn trong sự kiện vẫn dùng được
