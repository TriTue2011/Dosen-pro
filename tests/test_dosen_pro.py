"""Tích hợp Dosen Pro: luồng cấu hình, dịch vụ, media_player, cảm biến."""

import aiohttp
import pytest
from homeassistant.components.media_player import DOMAIN as MP_DOMAIN
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dosen_pro.const import CONF_API_KEY, CONF_URL, DOMAIN

URL = "http://dosen.test:8080"
SCREEN = {"mode": "off", "open": False, "source": "", "size": [240, 320], "last_error": None, "layers": [],
          "default": {"mode": "clock", "source": ""}}
SPEAKER = {"playing": False, "source": "", "bytes_sent": 0, "last_error": None}


def _mock_api(aioclient_mock, status=200):
    aioclient_mock.get(URL + "/api/health", json={"status": "ok"})
    aioclient_mock.get(URL + "/api/status", status=status, json={"service": "x"})
    aioclient_mock.get(URL + "/api/screen/status", json=SCREEN)
    aioclient_mock.get(URL + "/api/speaker/status", json=SPEAKER)
    aioclient_mock.get(URL + "/api/events/stream", text="")          # luồng báo động rỗng (config flow tạo mục thật)
    for p in ("screen/show", "screen/text", "screen/off", "screen/sleep", "screen/power", "screen/release",
              "screen/default", "events/start", "speaker/play", "speaker/play_bytes", "speaker/stop"):
        aioclient_mock.post(URL + "/api/" + p, json={})


async def _setup(hass, aioclient_mock, monkeypatch=None):
    _mock_api(aioclient_mock)
    if monkeypatch is not None:                 # không mở ffmpeg/pipeline thật trong test
        from custom_components.dosen_pro import assist_satellite as sat_mod

        async def _no_loop(self):
            return
        monkeypatch.setattr(sat_mod.DosenSatellite, "_chay", _no_loop)
    assert await async_setup_component(hass, "homeassistant", {})       # Assist cần thành phần này
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_URL: URL, CONF_API_KEY: "k1"}, unique_id=URL,
                            options={"alarm_events": False})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _posts(aioclient_mock, path):
    return [(m, u, b) for m, u, b, _ in aioclient_mock.mock_calls if m == "POST" and str(u).endswith(path)]


async def test_config_flow_ok_and_errors(hass, aioclient_mock):
    _mock_api(aioclient_mock)
    assert await async_setup_component(hass, "homeassistant", {})
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert r["type"] == FlowResultType.FORM
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_URL: URL + "/", CONF_API_KEY: "k1"})
    assert r["type"] == FlowResultType.CREATE_ENTRY
    assert r["data"] == {CONF_URL: URL, CONF_API_KEY: "k1"}          # bỏ dấu / cuối


async def test_config_flow_wrong_key(hass, aioclient_mock):
    _mock_api(aioclient_mock, status=401)
    assert await async_setup_component(hass, "homeassistant", {})
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_URL: URL, CONF_API_KEY: "sai"})
    assert r["type"] == FlowResultType.FORM and r["errors"] == {"base": "invalid_auth"}


async def test_config_flow_cannot_connect(hass, aioclient_mock):
    assert await async_setup_component(hass, "homeassistant", {})
    aioclient_mock.get("http://khong.co:8080/api/health", exc=aiohttp.ClientConnectionError("down"))
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_URL: "http://khong.co:8080"})
    assert r["errors"] == {"base": "cannot_connect"}


async def test_services_call_the_api(hass, aioclient_mock):
    await _setup(hass, aioclient_mock)
    await hass.services.async_call(DOMAIN, "screen_text", {
        "title": "Trợ lý", "messages": [{"role": "user", "text": "Bật đèn"}, "chuỗi thường"]}, blocking=True)
    body = _posts(aioclient_mock, "/api/screen/text")[-1][2]
    assert body["title"] == "Trợ lý" and body["after"] == "hold" and len(body["messages"]) == 2
    assert body["owner"] == "automation" and "ttl" not in body
    await hass.services.async_call(DOMAIN, "screen_show", {"source": "http://x/clip.mp4", "loop": True}, blocking=True)
    assert _posts(aioclient_mock, "/api/screen/show")[-1][2] == {
        "source": "http://x/clip.mp4", "loop": True, "after": "off", "owner": "automation"}
    await hass.services.async_call(DOMAIN, "screen_show", {"source": "rtsp://cam/1", "owner": "chuong", "ttl": 30}, blocking=True)
    assert _posts(aioclient_mock, "/api/screen/show")[-1][2]["owner"] == "chuong"
    assert _posts(aioclient_mock, "/api/screen/show")[-1][2]["ttl"] == 30
    await hass.services.async_call(DOMAIN, "screen_release", {"owner": "chuong"}, blocking=True)
    assert _posts(aioclient_mock, "/api/screen/release")[-1][2] == {"owner": "chuong"}
    await hass.services.async_call(DOMAIN, "screen_default", {"mode": "live", "source": "rtsp://cam/2"}, blocking=True)
    assert _posts(aioclient_mock, "/api/screen/default")[-1][2] == {"mode": "live", "source": "rtsp://cam/2"}
    await hass.services.async_call(DOMAIN, "screen_off", {}, blocking=True)
    assert _posts(aioclient_mock, "/api/screen/off")
    await hass.services.async_call(DOMAIN, "screen_power", {"on": True}, blocking=True)
    assert _posts(aioclient_mock, "/api/screen/power")[-1][2] == {"on": True}
    await hass.services.async_call(DOMAIN, "screen_sleep", {"seconds": 0}, blocking=True)
    assert _posts(aioclient_mock, "/api/screen/sleep")[-1][2] == {"seconds": 0}
    await hass.services.async_call(DOMAIN, "speaker_play", {"source": "http://x/a.mp3", "volume": 0.5}, blocking=True)
    assert _posts(aioclient_mock, "/api/speaker/play")[-1][2] == {"source": "http://x/a.mp3", "volume": 0.5}
    await hass.services.async_call(DOMAIN, "speaker_stop", {}, blocking=True)
    assert _posts(aioclient_mock, "/api/speaker/stop")


async def test_api_key_is_sent(hass, aioclient_mock):
    await _setup(hass, aioclient_mock)
    headers = [h for m, u, b, h in aioclient_mock.mock_calls if str(u).endswith("/api/screen/status")]
    assert headers and headers[0].get("X-API-Key") == "k1"


async def test_media_player_plays_a_url_and_reports_state(hass, aioclient_mock):
    await _setup(hass, aioclient_mock)
    ent = "media_player.dosen_pro_loa"
    assert hass.states.get(ent).state == "idle"
    await hass.services.async_call(MP_DOMAIN, "play_media", {
        "entity_id": ent, "media_content_id": "http://example.com/a.mp3", "media_content_type": "music"}, blocking=True)
    body = _posts(aioclient_mock, "/api/speaker/play")[-1][2]
    assert body["source"] == "http://example.com/a.mp3" and body["volume"] == 1.0   # 0.5 * 2
    await hass.services.async_call(MP_DOMAIN, "volume_set", {"entity_id": ent, "volume_level": 1.0}, blocking=True)
    await hass.services.async_call(MP_DOMAIN, "play_media", {
        "entity_id": ent, "media_content_id": "http://example.com/b.mp3", "media_content_type": "music"}, blocking=True)
    assert _posts(aioclient_mock, "/api/speaker/play")[-1][2]["volume"] == 2.0
    await hass.services.async_call(MP_DOMAIN, "media_stop", {"entity_id": ent}, blocking=True)
    assert _posts(aioclient_mock, "/api/speaker/stop")


async def test_screen_sensor(hass, aioclient_mock):
    await _setup(hass, aioclient_mock)
    st = hass.states.get("sensor.dosen_pro_noi_dung_man_hinh")
    assert st is not None and st.state == "off" and st.attributes["size"] == [240, 320]


async def test_unload_removes_services(hass, aioclient_mock):
    entry = await _setup(hass, aioclient_mock)
    assert hass.services.has_service(DOMAIN, "screen_show")
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not hass.services.has_service(DOMAIN, "screen_show")


async def test_entities_like_the_assist_camera_plus_screen(hass, aioclient_mock, monkeypatch):
    await _setup(hass, aioclient_mock, monkeypatch)
    ids = set(hass.states.async_entity_ids())
    for e in ("media_player.dosen_pro_loa", "assist_satellite.dosen_pro", "number.dosen_pro_tang_mic",
              "number.dosen_pro_thoi_gian_sang_man_hinh", "number.dosen_pro_do_sang_man_hinh",
              "switch.dosen_pro_tat_mic", "sensor.dosen_pro_muc_mic",
              "switch.dosen_pro_hien_hoi_thoai_tren_man_hinh", "sensor.dosen_pro_noi_dung_man_hinh",
              "button.dosen_pro_bat_man_hinh", "button.dosen_pro_tat_man_hinh",
              "button.dosen_pro_ve_man_hinh_mac_dinh", "button.dosen_pro_dung_loa",
              "event.dosen_pro_su_kien_camera", "select.dosen_pro_man_hinh_mac_dinh",
              "select.dosen_pro_camera_mac_dinh", "select.dosen_pro_anh_mac_dinh",
              "text.dosen_pro_dia_chi_mac_dinh"):
        assert e in ids, (e, sorted(ids))
    assert any(i.startswith("select.dosen_pro") for i in ids)       # pipeline + độ nhạy nói xong
    # Bật/tắt nguồn màn hình là NÚT (camera không báo ngược trạng thái), không có công tắc.
    assert "switch.dosen_pro_nguon_man_hinh" not in ids


async def test_numbers_write_to_the_camera(hass, aioclient_mock, monkeypatch):
    await _setup(hass, aioclient_mock, monkeypatch)
    await hass.services.async_call("number", "set_value", {"entity_id": "number.dosen_pro_thoi_gian_sang_man_hinh", "value": 0}, blocking=True)
    assert _posts(aioclient_mock, "/api/screen/sleep")[-1][2] == {"seconds": 0}
    await hass.services.async_call("number", "set_value", {"entity_id": "number.dosen_pro_do_sang_man_hinh", "value": 40}, blocking=True)
    assert _posts(aioclient_mock, "/api/screen/sleep")[-1][2] == {"luminance": 40}


async def test_assist_conversation_is_shown_on_the_camera_screen(hass, aioclient_mock, monkeypatch):
    import asyncio

    from homeassistant.components.assist_pipeline import PipelineEvent, PipelineEventType
    from homeassistant.components.assist_satellite.const import DOMAIN as AS_DOMAIN
    await _setup(hass, aioclient_mock, monkeypatch)
    sat = hass.data[AS_DOMAIN].get_entity("assist_satellite.dosen_pro")
    sat.on_pipeline_event(PipelineEvent(PipelineEventType.WAKE_WORD_END, {"wake_word_output": {}}))
    sat.on_pipeline_event(PipelineEvent(PipelineEventType.STT_END, {"stt_output": {"text": "bật đèn phòng khách"}}))
    sat.on_pipeline_event(PipelineEvent(PipelineEventType.INTENT_END, {"intent_output": {
        "response": {"speech": {"plain": {"speech": "Đã bật đèn."}}}, "continue_conversation": False}}))
    await asyncio.sleep(0.4)
    await hass.async_block_till_done()
    body = _posts(aioclient_mock, "/api/screen/text")[-1][2]
    assert body["title"] == "Trợ lý" and body["after"] == "hold"
    assert body["owner"] == "assist" and body["ttl"] == 60                 # lớp riêng của Assist, có hạn
    assert [(m["role"], m["text"]) for m in body["messages"]] == [
        ("user", "bật đèn phòng khách"), ("assistant", "Đã bật đèn.")]       # «Đang nghe…» đã bị thay
    # Tắt công tắc «Hiện hội thoại» thì không vẽ gì nữa.
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.dosen_pro_hien_hoi_thoai_tren_man_hinh"}, blocking=True)
    n = len(_posts(aioclient_mock, "/api/screen/text"))
    sat.on_pipeline_event(PipelineEvent(PipelineEventType.STT_END, {"stt_output": {"text": "xin chào"}}))
    await asyncio.sleep(0.4)
    assert len(_posts(aioclient_mock, "/api/screen/text")) == n


async def test_buttons_and_default_screen_select(hass, aioclient_mock, monkeypatch):
    await _setup(hass, aioclient_mock, monkeypatch)
    for ent, path, expect in (("button.dosen_pro_bat_man_hinh", "/api/screen/power", {"on": True}),
                              ("button.dosen_pro_tat_man_hinh", "/api/screen/power", {"on": False})):
        await hass.services.async_call("button", "press", {"entity_id": ent}, blocking=True)
        assert _posts(aioclient_mock, path)[-1][2] == expect
    await hass.services.async_call("button", "press", {"entity_id": "button.dosen_pro_ve_man_hinh_mac_dinh"}, blocking=True)
    assert _posts(aioclient_mock, "/api/screen/off")
    await hass.services.async_call("button", "press", {"entity_id": "button.dosen_pro_dung_loa"}, blocking=True)
    assert _posts(aioclient_mock, "/api/speaker/stop")
    st = hass.states.get("select.dosen_pro_man_hinh_mac_dinh")
    assert st.state == "clock" and set(st.attributes["options"]) == {"off", "clock", "image", "video", "live"}
    # live/image/video cần nguồn: chưa chọn thì báo lỗi rõ ràng và KHÔNG đổi loại.
    from homeassistant.exceptions import HomeAssistantError
    for mode in ("live", "image", "video"):
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call("select", "select_option", {"entity_id": "select.dosen_pro_man_hinh_mac_dinh", "option": mode}, blocking=True)
    assert not _posts(aioclient_mock, "/api/screen/default")
    assert hass.states.get("select.dosen_pro_man_hinh_mac_dinh").state == "clock"        # vẫn như cũ


async def test_default_screen_is_set_with_entities_from_ha_lists(hass, aioclient_mock, monkeypatch):
    import custom_components.dosen_pro as pkg
    calls = []

    async def fake_cam(h, entity_id, base=None):
        calls.append(("cam", entity_id))
        return "rtsp://resolved/" + entity_id

    async def fake_source(h, *, source=None, image_entity=None, base=None, **kw):
        calls.append(("src", image_entity or source))
        return "http://signed/" + (image_entity or source)
    monkeypatch.setattr(pkg, "resolve_camera", fake_cam)
    monkeypatch.setattr(pkg, "resolve_source", fake_source)
    await _setup(hass, aioclient_mock, monkeypatch)
    hass.states.async_set("camera.cua", "idle")                      # camera trong danh sách của HA
    hass.states.async_set("image.thoi_tiet", "2026-11-15")           # thực thể ảnh trong danh sách của HA
    cam = hass.states.get("select.dosen_pro_camera_mac_dinh")
    assert "camera.cua" in cam.attributes["options"] and cam.state == "—"                  # chọn theo entity
    assert "image.thoi_tiet" in hass.states.get("select.dosen_pro_anh_mac_dinh").attributes["options"]
    sel = lambda ent, opt: hass.services.async_call("select", "select_option", {"entity_id": ent, "option": opt}, blocking=True)
    await sel("select.dosen_pro_camera_mac_dinh", "camera.cua")                           # chọn camera: chưa áp dụng (loại đang là clock)
    assert not _posts(aioclient_mock, "/api/screen/default")
    await sel("select.dosen_pro_man_hinh_mac_dinh", "live")                               # chọn loại: áp dụng với camera đã chọn
    assert _posts(aioclient_mock, "/api/screen/default")[-1][2] == {"mode": "live", "source": "rtsp://resolved/camera.cua"}
    await sel("select.dosen_pro_anh_mac_dinh", "image.thoi_tiet")
    await sel("select.dosen_pro_man_hinh_mac_dinh", "image")
    assert _posts(aioclient_mock, "/api/screen/default")[-1][2] == {"mode": "image", "source": "http://signed/image.thoi_tiet"}
    await hass.services.async_call("text", "set_value", {"entity_id": "text.dosen_pro_dia_chi_mac_dinh", "value": "/local/clip.mp4"}, blocking=True)
    await sel("select.dosen_pro_man_hinh_mac_dinh", "video")                              # video: dùng địa chỉ gõ tay
    assert _posts(aioclient_mock, "/api/screen/default")[-1][2] == {"mode": "video", "source": "http://signed//local/clip.mp4"}
    await sel("select.dosen_pro_man_hinh_mac_dinh", "off")
    assert _posts(aioclient_mock, "/api/screen/default")[-1][2] == {"mode": "off", "source": ""}


async def test_services_take_camera_and_media_from_ha_lists(hass, aioclient_mock, monkeypatch):
    import custom_components.dosen_pro.resolve as res
    await _setup(hass, aioclient_mock, monkeypatch)

    async def fake_cam(h, e, base=None):
        return "rtsp://r/" + e

    async def fake_media(h, m, base=None):
        return "http://signed/" + m["media_content_id"]
    monkeypatch.setattr(res, "resolve_camera", fake_cam)
    monkeypatch.setattr(res, "resolve_media", fake_media)
    await hass.services.async_call(DOMAIN, "screen_show", {"camera_entity": "camera.cua", "owner": "c"}, blocking=True)
    assert _posts(aioclient_mock, "/api/screen/show")[-1][2]["source"] == "rtsp://r/camera.cua"
    await hass.services.async_call(DOMAIN, "screen_show", {"media": {"media_content_id": "m1"}}, blocking=True)
    assert _posts(aioclient_mock, "/api/screen/show")[-1][2]["source"] == "http://signed/m1"
    with pytest.raises(Exception):                       # phải có ít nhất một nguồn
        await hass.services.async_call(DOMAIN, "screen_show", {"owner": "x"}, blocking=True)
    await hass.services.async_call(DOMAIN, "screen_default", {"mode": "image", "media": {"media_content_id": "a"}}, blocking=True)
    assert _posts(aioclient_mock, "/api/screen/default")[-1][2] == {"mode": "image", "source": "http://signed/a"}


async def test_reconfigure_changes_url_and_key(hass, aioclient_mock, monkeypatch):
    entry = await _setup(hass, aioclient_mock, monkeypatch)
    new = "http://dosen.test:9090"
    aioclient_mock.get(new + "/api/health", json={"status": "ok"})
    aioclient_mock.get(new + "/api/status", json={"service": "x"})
    aioclient_mock.get(new + "/api/screen/status", json=SCREEN)
    aioclient_mock.get(new + "/api/speaker/status", json=SPEAKER)
    aioclient_mock.get(new + "/api/events/stream", text="")
    aioclient_mock.post(new + "/api/events/start", json={})
    r = await entry.start_reconfigure_flow(hass)
    assert r["type"] == FlowResultType.FORM and r["step_id"] == "reconfigure"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_URL: new + "/", CONF_API_KEY: "k2"})
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.data[CONF_URL] == new and entry.data[CONF_API_KEY] == "k2"


async def test_camera_alarms_become_bus_events_and_an_event_entity(hass, aioclient_mock, monkeypatch):
    from custom_components.dosen_pro.api import DosenApi, DosenApiError
    from custom_components.dosen_pro.const import EVENT_ALARM

    async def fake_stream(self):
        yield {"event": "LocalAlarm", "alarm": {"Event": "LocalAlarm", "Status": "Start"}, "possible_button_press": True}
        raise DosenApiError("hết")
    monkeypatch.setattr(DosenApi, "events_stream", fake_stream)
    got = []
    hass.bus.async_listen(EVENT_ALARM, lambda e: got.append(e.data))
    entry = await _setup(hass, aioclient_mock, monkeypatch)
    entry.async_create_background_task(hass, hass.data[DOMAIN][entry.entry_id]._alarm_loop(), "test alarm")
    for _ in range(40):
        await hass.async_block_till_done()
        if got:
            break
        import asyncio
        await asyncio.sleep(0.05)
    assert got and got[0]["event"] == "LocalAlarm" and got[0]["status"] == "Start" and got[0]["possible_button_press"] is True
    assert hass.states.get("event.dosen_pro_su_kien_camera").attributes["event_type"] == "alarm"
    assert _posts(aioclient_mock, "/api/events/start")


async def test_mic_level_sensor_follows_the_satellite_stream(hass, aioclient_mock, monkeypatch):
    from custom_components.dosen_pro.assist_satellite import muc_db
    import array, math
    quiet = array.array("h", [10] * 1024).tobytes()
    loud = array.array("h", [int(16000 * math.sin(i / 5)) for i in range(1024)]).tobytes()
    assert muc_db(quiet) < -60 < -10 < muc_db(loud) and muc_db(b"") == -90.0
    entry = await _setup(hass, aioclient_mock, monkeypatch)
    coord = hass.data[DOMAIN][entry.entry_id]
    assert hass.states.get("sensor.dosen_pro_muc_mic").state == "unknown"       # chưa có luồng
    coord.set_mic_level(muc_db(loud))
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.dosen_pro_muc_mic").state) > -20
    coord.set_mic_level(None)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.dosen_pro_muc_mic").state == "unknown"


async def test_options_flow_has_only_infrastructure_choices(hass, aioclient_mock, monkeypatch):
    entry = await _setup(hass, aioclient_mock, monkeypatch)
    r = await hass.config_entries.options.async_init(entry.entry_id)
    assert r["type"] == FlowResultType.FORM and set(r["data_schema"].schema) == {
        vol_key for vol_key in r["data_schema"].schema if str(vol_key) in ("alarm_events", "ha_url")}
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"alarm_events": False, "ha_url": "http://192.168.1.5:8123"})
    assert r["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["ha_url"] == "http://192.168.1.5:8123"


async def test_resolve_uses_ha_url_option_and_explains_when_no_address(hass, monkeypatch):
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers.network import NoURLAvailableError

    from custom_components.dosen_pro import resolve
    assert await async_setup_component(hass, "http", {})
    url = resolve._absolute(hass, "/local/a.png", base="http://192.168.1.5:8123/")       # «Địa chỉ HA» do người dùng đặt
    assert url.startswith("http://192.168.1.5:8123/local/a.png?authSig=")
    assert resolve._absolute(hass, "rtsp://cam/1") == "rtsp://cam/1"                      # địa chỉ ngoài giữ nguyên
    assert resolve._absolute(hass, "/local/a.png").startswith("http")                    # HA tự chọn nội bộ rồi địa chỉ ngoài
    monkeypatch.setattr(resolve, "get_url", lambda *a, **k: (_ for _ in ()).throw(NoURLAvailableError()))
    with pytest.raises(HomeAssistantError, match="Địa chỉ HA"):                           # không có địa chỉ nào: nói rõ phải làm gì
        resolve._absolute(hass, "/local/a.png")
