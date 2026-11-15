"""Tích hợp Dosen Pro (chạy thẳng trong HA, không Docker): luồng cấu hình, dịch vụ, thực thể, Assist, sự kiện."""

import asyncio

import pytest
import voluptuous as vol
from fakes import FakeCamera
from homeassistant.components.media_player import DOMAIN as MP_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.dosen_pro as pkg
import custom_components.dosen_pro.config_flow as cflow
from custom_components.dosen_pro.const import DOMAIN
from custom_components.dosen_pro.dosen_core.dvrip import DVRIPError

DATA = {CONF_HOST: "172.16.10.19", CONF_PORT: 34567, CONF_USERNAME: "jdja", CONF_PASSWORD: "mk"}


@pytest.fixture(autouse=True)
def _fake_camera(monkeypatch):
    FakeCamera.instances.clear()
    FakeCamera.login_error = None
    monkeypatch.setattr(pkg, "Camera", FakeCamera)
    monkeypatch.setattr(cflow, "Camera", FakeCamera)

    from custom_components.dosen_pro import assist_satellite as sat_mod

    async def _no_loop(self):                  # không mở ffmpeg/pipeline thật trong test
        return
    monkeypatch.setattr(sat_mod.DosenSatellite, "_chay", _no_loop)


async def _setup(hass, options=None, data=None):
    assert await async_setup_component(hass, "homeassistant", {})       # Assist cần thành phần này
    entry = MockConfigEntry(domain=DOMAIN, data=data or DATA, unique_id=DATA[CONF_HOST], title="Dosen Pro",
                            options={"alarm_events": False, **(options or {})})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _cam():
    return FakeCamera.instances[-1]


def _calls(kind):
    return [c[1] if len(c) > 1 else None for c in _cam().screen.calls if c[0] == kind]


# -- luồng cấu hình -------------------------------------------------------------------

async def test_config_flow_ok_and_camera_config_is_stored(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert r["type"] == FlowResultType.FORM
    r = await hass.config_entries.flow.async_configure(r["flow_id"], DATA)
    assert r["type"] == FlowResultType.CREATE_ENTRY and r["data"][CONF_HOST] == "172.16.10.19"
    assert r["data"][CONF_USERNAME] == "jdja"


@pytest.mark.parametrize("exc,code", [
    (DVRIPError("camera returned Ret=205: {}"), "account_locked"),
    (DVRIPError("login paused for 30 s: the camera refused the last login (Ret=205)"), "account_locked"),
    (DVRIPError("camera returned Ret=203: {}"), "invalid_auth"),
    (OSError("no route"), "cannot_connect"),
])
async def test_config_flow_errors(hass, exc, code):
    assert await async_setup_component(hass, "homeassistant", {})
    FakeCamera.login_error = exc
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], DATA)
    assert r["type"] == FlowResultType.FORM and r["errors"] == {"base": code}


async def test_reconfigure_changes_ip_account_and_password(hass):
    entry = await _setup(hass)
    r = await entry.start_reconfigure_flow(hass)
    assert r["type"] == FlowResultType.FORM and r["step_id"] == "reconfigure"
    new = {**DATA, CONF_HOST: "172.16.10.20", CONF_USERNAME: "dosen", CONF_PASSWORD: "moi"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], new)
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.data[CONF_HOST] == "172.16.10.20" and entry.data[CONF_USERNAME] == "dosen"
    assert FakeCamera.instances[0].closed is True                         # mục nạp lại: camera cũ được đóng gọn


async def test_old_docker_style_entry_asks_to_be_recreated(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(domain=DOMAIN, data={"url": "http://x:8080", "api_key": ""}, unique_id="http://x:8080")
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state == ConfigEntryState.SETUP_ERROR


# -- dịch vụ ------------------------------------------------------------------------------

async def test_services_call_the_camera(hass):
    await _setup(hass)
    cam = _cam()
    assert ("load",) in cam.screen.calls                                                   # bộ đếm giây của lõi được bật
    await hass.services.async_call(DOMAIN, "screen_text", {
        "title": "Trợ lý", "messages": [{"role": "user", "text": "Bật đèn"}, "chuỗi thường"]}, blocking=True)
    t = _calls("text")[-1]
    assert t["title"] == "Trợ lý" and t["after"] == "hold" and t["owner"] == "automation" and t["ttl"] is None
    await hass.services.async_call(DOMAIN, "screen_show", {"source": "http://x/clip.mp4", "loop": True}, blocking=True)
    assert _calls("show")[-1] == {"source": "http://x/clip.mp4", "loop": True, "after": "off", "owner": "automation", "ttl": None}
    await hass.services.async_call(DOMAIN, "screen_show", {"source": "rtsp://cam/1", "owner": "chuong", "ttl": 30}, blocking=True)
    assert _calls("show")[-1]["owner"] == "chuong" and _calls("show")[-1]["ttl"] == 30
    await hass.services.async_call(DOMAIN, "screen_release", {"owner": "chuong"}, blocking=True)
    assert ("release", "chuong") in cam.screen.calls
    await hass.services.async_call(DOMAIN, "screen_default", {"mode": "live", "source": "rtsp://cam/2"}, blocking=True)
    assert _calls("default")[-1] == {"mode": "live", "source": "rtsp://cam/2"}
    await hass.services.async_call(DOMAIN, "screen_off", {}, blocking=True)
    await hass.services.async_call(DOMAIN, "screen_power", {"on": True}, blocking=True)
    await hass.services.async_call(DOMAIN, "screen_sleep", {"seconds": 0}, blocking=True)
    await hass.services.async_call(DOMAIN, "screen_sleep", {"luminance": 40}, blocking=True)
    assert ("off",) in cam.screen.calls and ("power", True) in cam.screen.calls
    assert _calls("sleep")[-2:] == [{"seconds": 0, "luminance": None}, {"seconds": None, "luminance": 40}]
    await hass.services.async_call(DOMAIN, "speaker_play", {"source": "http://x/a.mp3", "volume": 0.5}, blocking=True)
    await hass.services.async_call(DOMAIN, "speaker_stop", {}, blocking=True)
    assert cam.speaker.calls == [("play", {"source": "http://x/a.mp3", "volume": 0.5}), ("stop",)]
    with pytest.raises(Exception):                                  # screen_sleep cần ít nhất seconds hoặc luminance
        await hass.services.async_call(DOMAIN, "screen_sleep", {}, blocking=True)


async def test_services_take_camera_and_media_from_ha_lists(hass, monkeypatch):
    import custom_components.dosen_pro.resolve as res
    await _setup(hass)

    async def fake_cam(h, e, base=None):
        return "rtsp://r/" + e

    async def fake_media(h, m, base=None):
        return "http://signed/" + m["media_content_id"]
    monkeypatch.setattr(res, "resolve_camera", fake_cam)
    monkeypatch.setattr(res, "resolve_media", fake_media)
    await hass.services.async_call(DOMAIN, "screen_show", {"camera_entity": "camera.cua", "owner": "c"}, blocking=True)
    assert _calls("show")[-1]["source"] == "rtsp://r/camera.cua"
    await hass.services.async_call(DOMAIN, "screen_show", {"media": {"media_content_id": "m1"}}, blocking=True)
    assert _calls("show")[-1]["source"] == "http://signed/m1"
    with pytest.raises(Exception):                                  # phải có ít nhất một nguồn
        await hass.services.async_call(DOMAIN, "screen_show", {"owner": "x"}, blocking=True)
    await hass.services.async_call(DOMAIN, "screen_default", {"mode": "image", "media": {"media_content_id": "a"}}, blocking=True)
    assert _calls("default")[-1] == {"mode": "image", "source": "http://signed/a"}


async def test_camera_errors_become_ha_errors(hass):
    await _setup(hass)

    def boom(*a, **k):
        raise OSError("camera tắt")
    _cam().screen.show_text = boom
    with pytest.raises(HomeAssistantError, match="camera tắt"):
        await hass.services.async_call(DOMAIN, "screen_text", {"title": "x"}, blocking=True)


# -- thực thể -----------------------------------------------------------------------------

async def test_entities_like_the_assist_camera_plus_screen(hass):
    await _setup(hass)
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
    assert "switch.dosen_pro_nguon_man_hinh" not in ids                  # nguồn màn hình là NÚT, không phải công tắc
    assert hass.states.get("sensor.dosen_pro_noi_dung_man_hinh").state == "off"


async def test_numbers_buttons_and_media_player(hass):
    await _setup(hass)
    cam = _cam()
    await hass.services.async_call("number", "set_value", {"entity_id": "number.dosen_pro_thoi_gian_sang_man_hinh", "value": 0}, blocking=True)
    await hass.services.async_call("number", "set_value", {"entity_id": "number.dosen_pro_do_sang_man_hinh", "value": 40}, blocking=True)
    assert _calls("sleep")[-2:] == [{"seconds": 0, "luminance": None}, {"seconds": None, "luminance": 40}]
    for ent, expect in (("button.dosen_pro_bat_man_hinh", ("power", True)), ("button.dosen_pro_tat_man_hinh", ("power", False)),
                        ("button.dosen_pro_ve_man_hinh_mac_dinh", ("off",))):
        await hass.services.async_call("button", "press", {"entity_id": ent}, blocking=True)
        assert expect in cam.screen.calls
    await hass.services.async_call("button", "press", {"entity_id": "button.dosen_pro_dung_loa"}, blocking=True)
    assert ("stop",) in cam.speaker.calls
    ent = "media_player.dosen_pro_loa"
    assert hass.states.get(ent).state == "idle"
    await hass.services.async_call(MP_DOMAIN, "play_media", {"entity_id": ent, "media_content_id": "http://example.com/a.mp3", "media_content_type": "music"}, blocking=True)
    assert cam.speaker.calls[-1] == ("play", {"source": "http://example.com/a.mp3", "volume": 1.0})      # 0.5 × 2
    await hass.services.async_call(MP_DOMAIN, "volume_set", {"entity_id": ent, "volume_level": 1.0}, blocking=True)
    await hass.services.async_call(MP_DOMAIN, "play_media", {"entity_id": ent, "media_content_id": "http://example.com/b.mp3", "media_content_type": "music"}, blocking=True)
    assert cam.speaker.calls[-1][1]["volume"] == 2.0


async def test_default_screen_needs_a_source_and_uses_entity_lists(hass, monkeypatch):
    calls = []

    async def fake_cam(h, entity_id, base=None):
        calls.append(("cam", entity_id))
        return "rtsp://resolved/" + entity_id

    async def fake_source(h, *, source=None, image_entity=None, base=None, **kw):
        return "http://signed/" + (image_entity or source)
    monkeypatch.setattr(pkg, "resolve_camera", fake_cam)
    monkeypatch.setattr(pkg, "resolve_source", fake_source)
    await _setup(hass)
    sel = lambda ent, opt: hass.services.async_call("select", "select_option", {"entity_id": ent, "option": opt}, blocking=True)
    for mode in ("live", "image", "video"):                                       # chưa chọn nguồn: báo lỗi, KHÔNG đổi loại
        with pytest.raises(HomeAssistantError):
            await sel("select.dosen_pro_man_hinh_mac_dinh", mode)
    assert not _calls("default")
    hass.states.async_set("camera.cua", "idle")
    hass.states.async_set("image.thoi_tiet", "x")
    cam = hass.states.get("select.dosen_pro_camera_mac_dinh")
    assert "camera.cua" in cam.attributes["options"] and cam.state == "—"                  # chọn theo danh sách entity
    assert "image.thoi_tiet" in hass.states.get("select.dosen_pro_anh_mac_dinh").attributes["options"]
    await sel("select.dosen_pro_camera_mac_dinh", "camera.cua")
    await sel("select.dosen_pro_man_hinh_mac_dinh", "live")
    assert _calls("default")[-1] == {"mode": "live", "source": "rtsp://resolved/camera.cua"}
    await sel("select.dosen_pro_anh_mac_dinh", "image.thoi_tiet")
    await sel("select.dosen_pro_man_hinh_mac_dinh", "image")
    assert _calls("default")[-1] == {"mode": "image", "source": "http://signed/image.thoi_tiet"}
    await hass.services.async_call("text", "set_value", {"entity_id": "text.dosen_pro_dia_chi_mac_dinh", "value": "/local/clip.mp4"}, blocking=True)
    await sel("select.dosen_pro_man_hinh_mac_dinh", "video")
    assert _calls("default")[-1] == {"mode": "video", "source": "http://signed//local/clip.mp4"}
    await sel("select.dosen_pro_man_hinh_mac_dinh", "off")
    assert _calls("default")[-1] == {"mode": "off", "source": ""}


# -- báo động, Assist, mic ------------------------------------------------------------

async def test_camera_alarms_become_bus_events_and_an_event_entity(hass):
    from custom_components.dosen_pro.const import EVENT_ALARM
    got = []
    hass.bus.async_listen(EVENT_ALARM, lambda e: got.append(e.data))
    await _setup(hass, options={"alarm_events": True})
    cam = _cam()
    for _ in range(40):                                                    # tích hợp mở phiên báo động (tác vụ nền)
        if cam.events.running:
            break
        await asyncio.sleep(0.05)
    assert cam.events.running is True
    await asyncio.to_thread(cam.on_alarm, {"event": "LocalAlarm", "alarm": {"Event": "LocalAlarm", "Status": "Start"},
                                           "possible_button_press": True})  # gọi từ luồng của lõi
    for _ in range(40):
        await hass.async_block_till_done()
        if got:
            break
        await asyncio.sleep(0.05)
    assert got and got[0]["event"] == "LocalAlarm" and got[0]["status"] == "Start" and got[0]["possible_button_press"] is True
    assert hass.states.get("event.dosen_pro_su_kien_camera").attributes["event_type"] == "alarm"


async def test_assist_conversation_is_shown_on_the_camera_screen(hass):
    from homeassistant.components.assist_pipeline import PipelineEvent, PipelineEventType
    from homeassistant.components.assist_satellite.const import DOMAIN as AS_DOMAIN
    await _setup(hass)
    sat = hass.data[AS_DOMAIN].get_entity("assist_satellite.dosen_pro")
    sat.on_pipeline_event(PipelineEvent(PipelineEventType.WAKE_WORD_END, {"wake_word_output": {}}))
    sat.on_pipeline_event(PipelineEvent(PipelineEventType.STT_END, {"stt_output": {"text": "bật đèn phòng khách"}}))
    sat.on_pipeline_event(PipelineEvent(PipelineEventType.INTENT_END, {"intent_output": {
        "response": {"speech": {"plain": {"speech": "Đã bật đèn."}}}, "continue_conversation": False}}))
    await asyncio.sleep(0.4)
    await hass.async_block_till_done()
    t = _calls("text")[-1]
    assert t["owner"] == "assist" and t["ttl"] == 60 and t["title"] == "Trợ lý"                   # lớp riêng của Assist, có hạn
    assert [(m["role"], m["text"]) for m in t["messages"]] == [("user", "bật đèn phòng khách"), ("assistant", "Đã bật đèn.")]
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.dosen_pro_hien_hoi_thoai_tren_man_hinh"}, blocking=True)
    n = len(_calls("text"))
    sat.on_pipeline_event(PipelineEvent(PipelineEventType.STT_END, {"stt_output": {"text": "xin chào"}}))
    await asyncio.sleep(0.4)
    assert len(_calls("text")) == n                                            # tắt công tắc thì không vẽ gì nữa


async def test_listening_text_is_released_when_the_run_ends_without_a_reply(hass, monkeypatch):
    """Gọi «alexa» rồi không nói gì: lượt kết thúc không có TTS, «Đang nghe…» không được treo tới hết hạn 60 s."""
    from homeassistant.components.assist_pipeline import PipelineEvent, PipelineEventType
    from homeassistant.components.assist_satellite.const import DOMAIN as AS_DOMAIN
    from custom_components.dosen_pro import assist_satellite as sat_mod
    monkeypatch.setattr(sat_mod, "CHAT_HOLD_SECONDS", 0.1)
    await _setup(hass)
    sat = hass.data[AS_DOMAIN].get_entity("assist_satellite.dosen_pro")
    sat.on_pipeline_event(PipelineEvent(PipelineEventType.WAKE_WORD_END, {"wake_word_output": {}}))
    await asyncio.sleep(0.4)
    assert _calls("text")[-1]["messages"][-1]["text"] == "Đang nghe…"
    assert ("release", "assist") not in _cam().screen.calls
    sat.on_pipeline_event(PipelineEvent(PipelineEventType.RUN_END, {}))
    await asyncio.sleep(0.5)
    assert ("release", "assist") in _cam().screen.calls


async def test_mic_level_sensor_follows_the_satellite_stream(hass):
    import array
    import math

    from custom_components.dosen_pro.assist_satellite import muc_db
    quiet = array.array("h", [10] * 1024).tobytes()
    loud = array.array("h", [int(16000 * math.sin(i / 5)) for i in range(1024)]).tobytes()
    assert muc_db(quiet) < -60 < -10 < muc_db(loud) and muc_db(b"") == -90.0
    entry = await _setup(hass)
    coord = hass.data[DOMAIN][entry.entry_id]
    assert hass.states.get("sensor.dosen_pro_muc_mic").state == "unknown"
    coord.set_mic_level(muc_db(loud))
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.dosen_pro_muc_mic").state) > -20
    coord.set_mic_level(None)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.dosen_pro_muc_mic").state == "unknown"


# -- vòng đời -------------------------------------------------------------------------------

async def test_options_flow_and_unload_closes_the_camera(hass):
    entry = await _setup(hass)
    r = await hass.config_entries.options.async_init(entry.entry_id)
    assert r["type"] == FlowResultType.FORM
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"alarm_events": False, "ha_url": "http://192.168.1.5:8123"})
    assert r["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["ha_url"] == "http://192.168.1.5:8123"
    cam = _cam()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert cam.closed is True and not hass.services.has_service(DOMAIN, "screen_show")


async def test_resolve_uses_ha_url_option_and_local_default(hass, monkeypatch):
    from homeassistant.helpers.network import NoURLAvailableError

    from custom_components.dosen_pro import resolve
    assert await async_setup_component(hass, "http", {})
    url = resolve._absolute(hass, "/local/a.png", base="http://192.168.1.5:8123/")
    assert url.startswith("http://192.168.1.5:8123/local/a.png?authSig=")
    assert resolve._absolute(hass, "rtsp://cam/1") == "rtsp://cam/1"
    assert resolve._absolute(hass, "/local/a.png").startswith("http://127.0.0.1:")          # ffmpeg chạy ngay trong HA
    monkeypatch.setattr(hass.config, "api", None)
    monkeypatch.setattr(resolve, "get_url", lambda *a, **k: (_ for _ in ()).throw(NoURLAvailableError()))
    with pytest.raises(HomeAssistantError, match="Địa chỉ HA"):
        resolve._absolute(hass, "/local/a.png")


async def test_camera_volume_entities_read_and_write_the_camera(hass):
    await _setup(hass)
    cam = _cam()
    for _ in range(40):                                            # đọc âm lượng lúc khởi động (tác vụ nền)
        await hass.async_block_till_done()
        if hass.states.get("number.dosen_pro_am_luong_loa_camera").state == "50":
            break
        await asyncio.sleep(0.05)
    assert hass.states.get("number.dosen_pro_am_luong_loa_camera").state == "50"
    assert hass.states.get("number.dosen_pro_am_luong_mic_camera").state == "50"
    assert hass.states.get("switch.dosen_pro_chong_on_mic_camera").state == "on"
    await hass.services.async_call("number", "set_value", {"entity_id": "number.dosen_pro_am_luong_loa_camera", "value": 100}, blocking=True)
    assert cam.audio_calls[-1] == {"speaker": 100, "mic": None, "anr": None}
    assert hass.states.get("number.dosen_pro_am_luong_loa_camera").state == "100"
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.dosen_pro_chong_on_mic_camera"}, blocking=True)
    assert cam.audio_calls[-1] == {"speaker": None, "mic": None, "anr": False}
    assert hass.states.get("switch.dosen_pro_chong_on_mic_camera").state == "off"


async def test_rotate_and_fit_are_default_screen_settings_only(hass):
    await _setup(hass)
    cam = _cam()
    with pytest.raises(vol.Invalid):                                   # sự kiện không có tham số xoay
        await hass.services.async_call(DOMAIN, "screen_show", {"source": "x", "rotate": 90}, blocking=True)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, "screen_default", {"mode": "live", "source": "rtsp://c", "rotate": 45},
                                       blocking=True)
    await hass.services.async_call(DOMAIN, "screen_default", {"mode": "live", "source": "rtsp://c", "rotate": 270},
                                   blocking=True)
    assert _calls("default")[-1]["rotate"] == 270
    assert hass.states.get("select.dosen_pro_xoay_hinh_mac_dinh").state == "270°"        # ô chọn khớp màn thật
    await hass.services.async_call("select", "select_option", {"entity_id": "select.dosen_pro_xoay_hinh_mac_dinh",
                                                               "option": "90°"}, blocking=True)
    assert ("view", {"rotate": 90, "fit": "contain"}) in cam.screen.calls
    await hass.services.async_call("select", "select_option", {"entity_id": "select.dosen_pro_kieu_khung_hinh_mac_dinh",
                                                               "option": "Lấp đầy (cắt rìa)"}, blocking=True)
    assert cam.screen.calls[-1] == ("view", {"rotate": 90, "fit": "cover"})
    assert hass.states.get("select.dosen_pro_kieu_khung_hinh_mac_dinh").state == "Lấp đầy (cắt rìa)"


async def test_stt_audio_is_saved_with_the_recognised_text_for_diagnosis(hass):
    """«Hỏi một đằng nghe một nẻo»: giữ lại đúng đoạn tiếng gửi bộ nhận giọng + chữ nó nghe ra + số đo."""
    import os
    import wave
    from homeassistant.components.assist_pipeline import PipelineEvent, PipelineEventType
    from homeassistant.components.assist_satellite.const import DOMAIN as AS_DOMAIN
    await _setup(hass)
    sat = hass.data[AS_DOMAIN].get_entity("assist_satellite.dosen_pro")
    sat._ring.append((32767).to_bytes(2, "little", signed=True) * 1024)           # một khúc bị cắt đỉnh
    sat.on_pipeline_event(PipelineEvent(PipelineEventType.STT_END, {"stt_output": {"text": "bật đèn bếp"}}))
    await hass.async_block_till_done()
    d = hass.config.path("dosen_pro_stt")
    txt = ""
    for _ in range(60):                                     # thư mục cấu hình của test dùng chung: chờ đúng lượt của test này
        try:
            txt = open(os.path.join(d, "last.txt"), encoding="utf-8").read()
        except OSError:
            txt = ""
        if "bật đèn bếp" in txt:
            break
        await asyncio.sleep(0.05)
    assert "nghe ra: bật đèn bếp" in txt and "ti_le_cat_dinh: 1.0000" in txt
    with wave.open(os.path.join(d, "last.wav")) as w:
        assert w.getframerate() == 16000 and w.getnframes() == 1024


async def test_camera_entity_exposes_the_cameras_own_rtsp_stream_with_audio(hass):
    """Cuộc gọi Zalo chọn «Camera» và «Mic» đều là thực thể này: địa chỉ RTSP của chính camera (hình + tiếng)."""
    from homeassistant.components import camera as ha_camera
    await _setup(hass)
    assert hass.states.get("camera.dosen_pro_camera") is not None
    src = await ha_camera.async_get_stream_source(hass, "camera.dosen_pro_camera")
    assert src == "rtsp://172.16.10.19:554/user=jdja&password=" + DATA[CONF_PASSWORD] + "&channel=1&stream=1.sdp"
    assert "password" not in str(hass.states.get("camera.dosen_pro_camera").attributes)   # không lộ ra thuộc tính


async def test_intercom_key_is_generated_once_and_survives_a_reload(hass):
    entry = await _setup(hass)
    key = entry.data["intercom_key"]
    assert len(key) >= 24
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.data["intercom_key"] == key


async def test_intercom_posts_alaw_to_the_speaker_with_the_key_and_pauses_assist(hass, hass_client_no_auth):
    entry = await _setup(hass)
    client = await hass_client_no_auth()
    base = f"/api/dosen_pro/intercom/{entry.entry_id}"
    assert (await client.post(base, data=b"\xd5" * 320)).status == 401                    # không khoá
    assert (await client.post(base + "?k=sai", data=b"\xd5" * 320)).status == 401         # sai khoá
    assert (await client.post("/api/dosen_pro/intercom/khong-co?k=x", data=b"")).status == 401
    resp = await client.post(base + "?k=" + entry.data["intercom_key"], data=b"\xd5" * 960)
    assert resp.status == 200 and (await resp.json())["bytes"] == 960
    st = _cam().speaker.streams[-1]
    assert bytes(st.fed) == b"\xd5" * 960 and st.closed is True                           # đóng luồng khi cuộc gọi hết
    assert hass.data[DOMAIN][entry.entry_id].in_call is False                              # nhả cờ → Assist nghe lại


async def test_intercom_refuses_a_second_call_and_the_satellite_stops_listening_meanwhile(hass, hass_client_no_auth):
    entry = await _setup(hass)
    coord = hass.data[DOMAIN][entry.entry_id]
    client = await hass_client_no_auth()
    coord.set_in_call(True)                                                                # đang có một cuộc gọi
    resp = await client.post(f"/api/dosen_pro/intercom/{entry.entry_id}?k={entry.data['intercom_key']}", data=b"\xd5" * 320)
    assert resp.status == 409
    assert coord.in_call is True                                                           # cuộc gọi kia không bị phá
