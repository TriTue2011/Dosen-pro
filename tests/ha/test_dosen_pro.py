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

DATA = {CONF_HOST: "192.168.1.50", CONF_PORT: 34567, CONF_USERNAME: "admin", CONF_PASSWORD: "mk"}


from conftest import _REAL  # noqa: E402  (fixture camera giả nằm ở conftest.py, dùng chung với test_panel.py)


async def _setup(hass, options=None, data=None, entry_id=None):
    assert await async_setup_component(hass, "homeassistant", {})       # Assist cần thành phần này
    kw = {"entry_id": entry_id} if entry_id else {}
    entry = MockConfigEntry(domain=DOMAIN, data=data or DATA, unique_id=DATA[CONF_HOST], title="Dosen Pro",
                            options={"alarm_events": False, **(options or {})}, **kw)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    try:                                       # đợi tác vụ nền khởi động (áp màn mặc định, thời tiết…) xong: các lệnh trong test không bị chen ngang
        await asyncio.wait_for(hass.data[DOMAIN][entry.entry_id].startup_done.wait(), 5)
    except (TimeoutError, KeyError):
        pass
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
    assert r["type"] == FlowResultType.CREATE_ENTRY and r["data"][CONF_HOST] == "192.168.1.50"
    assert r["data"][CONF_USERNAME] == "admin"


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
    new = {**DATA, CONF_HOST: "192.168.1.51", CONF_USERNAME: "dosen", CONF_PASSWORD: "moi"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], new)
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.data[CONF_HOST] == "192.168.1.51" and entry.data[CONF_USERNAME] == "dosen"
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
              "switch.dosen_pro_tat_mic", "sensor.dosen_pro_muc_mic", "sensor.dosen_pro_noi_dung_man_hinh",
              "button.dosen_pro_bat_man_hinh", "button.dosen_pro_tat_man_hinh",
              "button.dosen_pro_ve_man_hinh_mac_dinh", "button.dosen_pro_dung_loa",
              "event.dosen_pro_su_kien_camera", "camera.dosen_pro_camera"):
        assert e in ids, (e, sorted(ids))
    # Cài đặt hiển thị nay nằm ở thẻ «Dosen Pro» (WebSocket), KHÔNG còn là thực thể trên thiết bị.
    for gone in ("select.dosen_pro_man_hinh_mac_dinh", "select.dosen_pro_camera_mac_dinh", "select.dosen_pro_anh_mac_dinh",
                 "select.dosen_pro_thoi_tiet_tren_dong_ho", "select.dosen_pro_xoay_hinh_mac_dinh", "select.dosen_pro_kieu_khung_hinh_mac_dinh",
                 "select.dosen_pro_chat_luong_hinh_tren_man", "text.dosen_pro_dia_chi_mac_dinh", "number.dosen_pro_thoi_gian_sang_man_hinh",
                 "number.dosen_pro_do_sang_man_hinh", "number.dosen_pro_thoi_gian_moi_man_luan_phien",
                 "switch.dosen_pro_hien_hoi_thoai_tren_man_hinh", "switch.dosen_pro_luan_phien_man_hinh", "switch.dosen_pro_nguon_man_hinh"):
        assert gone not in ids, gone
    assert hass.states.get("sensor.dosen_pro_noi_dung_man_hinh").state == "off"


async def test_numbers_buttons_and_media_player(hass):
    await _setup(hass)
    cam = _cam()
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


async def _ws(hass, hass_ws_client, msg_type, **kw):
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": msg_type, **kw})
    return await client.receive_json()


async def test_default_screen_needs_a_source_and_uses_entities_chosen_in_the_card(hass, hass_ws_client, monkeypatch):
    async def fake_cam(h, entity_id, base=None):
        return "rtsp://resolved/" + entity_id

    async def fake_source(h, *, source=None, image_entity=None, base=None, **kw):
        return "http://signed/" + (image_entity or source)
    monkeypatch.setattr(pkg, "resolve_camera", fake_cam)
    monkeypatch.setattr(pkg, "resolve_source", fake_source)
    await _setup(hass)
    client = await hass_ws_client(hass)
    n = 0

    async def call(**changes):
        nonlocal n
        n += 1
        await client.send_json({"id": n, "type": "dosen_pro/set", "changes": changes})
        return await client.receive_json()
    for mode in ("live", "image", "video"):                                       # chưa chọn nguồn: báo lỗi, KHÔNG đổi loại
        r = await call(default_mode=mode)
        assert r["success"] is False and r["error"]["code"] == "dosen_pro_error", mode
    assert not _calls("default")
    st = (await call())["result"]["settings"]
    assert st["default_mode"] is None                                            # lỗi không để lại trạng thái dở
    r = await call(default_camera="camera.cua", default_mode="live")
    assert r["success"] and _calls("default")[-1] == {"mode": "live", "source": "rtsp://resolved/camera.cua"}
    assert r["result"]["settings"]["default_camera"] == "camera.cua"
    await call(default_image="image.thoi_tiet", default_mode="image")
    assert _calls("default")[-1] == {"mode": "image", "source": "http://signed/image.thoi_tiet"}
    await call(default_text="/local/clip.mp4", default_mode="video")
    assert _calls("default")[-1] == {"mode": "video", "source": "http://signed//local/clip.mp4"}
    await call(default_mode="off")
    assert _calls("default")[-1] == {"mode": "off", "source": ""}
    for bad in ({"default_mode": "bay"}, {"default_camera": "khong phai entity"}, {"default_rotate": 45}, {"khong_co": 1}, {"show_chat": "co"}):
        r = await call(**bad)
        assert r["success"] is False, bad


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
    await hass.data[DOMAIN][next(iter(hass.data[DOMAIN]))].display.async_set({"show_chat": False})
    n = len(_calls("text"))
    sat.on_pipeline_event(PipelineEvent(PipelineEventType.STT_END, {"stt_output": {"text": "xin chào"}}))
    await asyncio.sleep(0.4)
    assert len(_calls("text")) == n                                            # tắt «Hiện hội thoại» ở thẻ thì không vẽ gì nữa


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
    coord = hass.data[DOMAIN][next(iter(hass.data[DOMAIN]))]
    with pytest.raises(vol.Invalid):                                   # sự kiện không có tham số xoay
        await hass.services.async_call(DOMAIN, "screen_show", {"source": "x", "rotate": 90}, blocking=True)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, "screen_default", {"mode": "live", "source": "rtsp://c", "rotate": 45},
                                       blocking=True)
    await hass.services.async_call(DOMAIN, "screen_default", {"mode": "live", "source": "rtsp://c", "rotate": 270},
                                   blocking=True)
    assert _calls("default")[-1]["rotate"] == 270 and coord.default_rotate == 270          # dịch vụ cập nhật cả cài đặt của thẻ
    await coord.display.async_set({"default_rotate": 90})
    assert ("view", {"rotate": 90, "fit": "contain"}) in cam.screen.calls
    await coord.display.async_set({"default_fit": "cover"})
    assert cam.screen.calls[-1] == ("view", {"rotate": 90, "fit": "cover"})


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
    assert src == "rtsp://192.168.1.50:554/user=admin&password=" + DATA[CONF_PASSWORD] + "&channel=1&stream=1.sdp"
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


async def test_satellite_waits_quietly_while_the_vad_select_is_not_ready(hass, monkeypatch, caplog):
    """Mỗi lần nạp lại, ô «độ nhạy nói xong» còn «unavailable» vài giây: đợi rồi thử lại, không ghi ERROR."""
    from custom_components.dosen_pro.assist_satellite import DosenSatellite
    calls = []

    async def fake_accept(self, audio_stream=None, start_stage=None, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("'unavailable' is not a valid VadSensitivity")
        await _real_sleep(3600)

    async def idle_mic(self):
        await _real_sleep(3600)
    monkeypatch.setattr(DosenSatellite, "_chay", _REAL["chay"])                           # vòng nghe THẬT
    monkeypatch.setattr(DosenSatellite, "_doc_mic", idle_mic)
    monkeypatch.setattr(DosenSatellite, "async_accept_pipeline_from_satellite", fake_accept)
    monkeypatch.setattr("custom_components.dosen_pro.assist_satellite.asyncio.sleep", _fast_sleep, raising=False)
    await _setup(hass)
    for _ in range(100):
        if len(calls) >= 2:
            break
        await _real_sleep(0.05)
    assert len(calls) >= 2                                              # đã thử lại sau lỗi đầu
    assert "pipeline failed" not in caplog.text


_real_sleep = asyncio.sleep


async def _fast_sleep(delay, *a, **k):
    await _real_sleep(0 if delay == 2 else delay, *a, **k)


async def test_default_screen_is_retried_until_its_camera_exists(hass, monkeypatch, caplog):
    """HA mới khởi động: camera go2rtc chưa nạp → lần áp đầu hỏng. Phải thử lại (không để màn camera tắt mãi)."""
    from types import SimpleNamespace

    async def fast(delay, *a, **k):
        await _real_sleep(0)
    monkeypatch.setattr(pkg.asyncio, "sleep", fast)

    async def screen_view(rotate, fit):
        return {}

    async def screen_quality(quality):
        return {}

    class Coord:
        default_rotate, default_fit, default_quality = 0, "contain", "standard"
        startup_done = asyncio.Event()
        api = SimpleNamespace(screen_view=screen_view, screen_quality=screen_quality)

        def __init__(self, fail_times):
            self.n, self.fail = 0, fail_times

        def track_weather(self):
            pass

        async def push_weather(self):
            pass

        async def apply_default(self):
            self.n += 1
            if self.n <= self.fail:
                raise HomeAssistantError("Không tìm thấy camera camera.cua")
    c = Coord(3)
    await pkg._apply_default_safe(c)
    assert c.n == 4 and "Không áp dụng được" not in caplog.text                 # hỏng 3 lần rồi áp được
    c = Coord(99)
    await pkg._apply_default_safe(c)
    assert c.n == len(pkg.DEFAULT_RETRY_DELAYS)                                  # bỏ cuộc sau số lần đã định
    assert caplog.text.count("Không áp dụng được màn hình mặc định") == 1        # chỉ báo MỘT lần, không dội log


async def test_button_beep_switch_reads_and_writes_the_camera_setting(hass):
    await _setup(hass)
    cam = _cam()
    await hass.async_block_till_done()
    for _ in range(40):                                              # âm lượng/tiếng bíp đọc từ camera lúc khởi động
        if hass.states.get("switch.dosen_pro_tieng_bip_khi_nhan_nut").state != "unavailable":
            break
        await asyncio.sleep(0.05)
    assert hass.states.get("switch.dosen_pro_tieng_bip_khi_nhan_nut").state == "on"
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.dosen_pro_tieng_bip_khi_nhan_nut"}, blocking=True)
    assert cam.audio_calls[-1] == {"speaker": None, "mic": None, "anr": None, "key_tip": False}
    assert hass.states.get("switch.dosen_pro_tieng_bip_khi_nhan_nut").state == "off"
    assert hass.states.get("switch.dosen_pro_chong_on_mic_camera").state == "on"       # công tắc khác không bị đụng


async def test_weather_entity_feeds_the_clock_with_temperature_humidity_and_forecast_range(hass):
    from homeassistant.core import SupportsResponse

    async def forecasts(call):
        return {"weather.nha": {"forecast": [{"temperature": 91.4, "templow": 73.4}]}}           # °F
    hass.services.async_register("weather", "get_forecasts", forecasts, supports_response=SupportsResponse.ONLY)
    await _setup(hass)
    cam = _cam()
    hass.states.async_set("weather.nha", "partlycloudy", {"temperature": 82.4, "temperature_unit": "°F", "humidity": 70})
    await hass.async_block_till_done()
    coord = hass.data[DOMAIN][next(iter(hass.data[DOMAIN]))]
    await coord.display.async_set({"default_weather": "weather.nha"})
    w = [c[1] for c in cam.screen.calls if c[0] == "weather"][-1]
    assert w == {"condition": "partlycloudy", "temp": 28.0, "humidity": 70, "high": 33.0, "low": 23.0}   # °F → °C
    hass.states.async_set("weather.nha", "rainy", {"temperature": 75.2, "temperature_unit": "°F", "humidity": 88})
    await hass.async_block_till_done()
    for _ in range(40):                                                          # đổi trạng thái → tự đẩy lại
        if [c[1] for c in cam.screen.calls if c[0] == "weather"][-1]["condition"] == "rainy":
            break
        await asyncio.sleep(0.05)
    assert [c[1] for c in cam.screen.calls if c[0] == "weather"][-1]["condition"] == "rainy"
    await coord.display.async_set({"default_weather": ""})
    for _ in range(40):                                  # tác vụ nền lúc khởi động cũng đẩy thời tiết: chờ lần cuối là «không có»
        if [c[1] for c in cam.screen.calls if c[0] == "weather"][-1] is None:
            break
        await asyncio.sleep(0.05)
    assert [c[1] for c in cam.screen.calls if c[0] == "weather"][-1] is None              # bỏ chọn → đồng hồ không còn thời tiết


async def test_weather_without_forecast_service_still_shows_current_conditions(hass):
    await _setup(hass)                                                          # không đăng ký weather.get_forecasts
    hass.states.async_set("weather.nha", "sunny", {"temperature": 30, "temperature_unit": "°C"})
    await hass.data[DOMAIN][next(iter(hass.data[DOMAIN]))].display.async_set({"default_weather": "weather.nha"})
    w = [c[1] for c in _cam().screen.calls if c[0] == "weather"][-1]
    assert w["condition"] == "sunny" and w["temp"] == 30.0 and w["high"] is None and w["low"] is None


async def test_quality_defaults_to_standard_and_changes_are_pushed_and_saved(hass):
    await _setup(hass)
    coord = hass.data[DOMAIN][next(iter(hass.data[DOMAIN]))]
    assert coord.default_quality == "standard"
    await coord.display.async_set({"default_quality": "high"})
    assert ("quality", "high") in _cam().screen.calls
    await coord.display.async_set({"default_quality": "max"})
    assert [c for c in _cam().screen.calls if c[0] == "quality"][-1] == ("quality", "max")


def test_brand_icons_exist_with_the_sizes_home_assistant_expects():
    """HA (2026.3+) đọc icon của tích hợp tuỳ chỉnh từ custom_components/<domain>/brand/: bản sáng + tối, 256 px và @2x 512 px."""
    import pathlib
    from PIL import Image
    brand = pathlib.Path(pkg.__file__).parent / "brand"
    for name, size in (("icon.png", 256), ("icon@2x.png", 512), ("dark_icon.png", 256), ("dark_icon@2x.png", 512)):
        img = Image.open(brand / name)
        assert img.size == (size, size) and img.mode == "RGBA", name
        assert img.getchannel("A").getextrema()[0] == 0, f"{name}: góc phải trong suốt"


async def test_screen_card_builds_rows_from_entities_and_text(hass):
    await _setup(hass)
    hass.states.async_set("sensor.solar", "3.4567", {"unit_of_measurement": "kW", "friendly_name": "Công suất mặt trời"})
    hass.states.async_set("sensor.pin", "87", {"unit_of_measurement": "%", "friendly_name": "Pin"})
    hass.states.async_set("sensor.cua", "Đóng", {"friendly_name": "Cửa chính"})
    hass.states.async_set("sensor.hong", "unavailable", {"friendly_name": "Cảm biến hỏng"})
    hass.states.async_set("sensor.lon", "1234.56", {"unit_of_measurement": "W", "friendly_name": "Điện nhà"})
    await hass.services.async_call(DOMAIN, "screen_card", {
        "title": "Điện", "main_entity": "sensor.solar", "color": "green", "ttl": 60, "owner": "dien",
        "rows": ["Hôm nay: 18,6 kWh", {"label": "Bán lưới", "value": "2,2 kW", "color": "blue"}],
        "entities": ["sensor.pin", "sensor.cua", "sensor.hong", "sensor.lon", "sensor.khong_co"]}, blocking=True)
    card = _calls("card")[-1]
    assert card["title"] == "Điện" and card["value"] == "3,5 kW" and card["caption"] == "Công suất mặt trời"    # số lớn từ cảm biến chính
    assert card["color"] == "green" and card["owner"] == "dien" and card["ttl"] == 60 and card["after"] == "hold"
    assert card["rows"] == [
        {"label": "Hôm nay", "value": "18,6 kWh"},                                  # dòng chữ «nhãn: giá trị»
        {"label": "Bán lưới", "value": "2,2 kW", "color": "blue"},
        {"label": "Pin", "value": "87 %"}, {"label": "Cửa chính", "value": "Đóng"},
        {"label": "Cảm biến hỏng", "value": "—"},                                   # không đọc được → «—»
        {"label": "Điện nhà", "value": "1235 W"},                                   # số lớn làm tròn nguyên
        {"label": "sensor.khong_co", "value": "—"}]                                  # thực thể không tồn tại
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, "screen_card", {"color": "tim"}, blocking=True)
    await hass.services.async_call(DOMAIN, "screen_card", {"value": "5", "caption": "tự khai"}, blocking=True)    # không cần thực thể
    assert _calls("card")[-1]["value"] == "5" and _calls("card")[-1]["rows"] == []


# -- màn luân phiên ----------------------------------------------------------------------

async def test_carousel_cycles_pages_under_its_own_owner_and_stop_releases_it(hass, monkeypatch):
    import custom_components.dosen_pro.carousel as car_mod

    async def fake_source(h, **kw):
        return "rtsp://x/" + (kw.get("camera_entity") or "")
    monkeypatch.setattr(car_mod, "resolve_source", fake_source)
    monkeypatch.setattr(car_mod, "_CHECK_EVERY", 0.1)
    await _setup(hass)
    cam, c = _cam(), hass.data[DOMAIN][next(iter(hass.data[DOMAIN]))]
    hass.states.async_set("sensor.cs", "3.4", {"unit_of_measurement": "kW", "friendly_name": "Công suất"})
    c.carousel.pages = [{"type": "clock", "seconds": 0.3},
                        {"type": "card", "title": "Điện", "main_entity": "sensor.cs", "seconds": 0.3},
                        {"type": "camera", "camera_entity": "camera.cua", "seconds": 0.3}]     # bỏ qua schema để thử nhịp ngắn
    await c.carousel.async_start()
    for _ in range(80):
        kinds = [x[0] for x in cam.screen.calls if x[0] in ("clock", "card", "show")]
        if kinds.count("clock") >= 2:
            break
        await asyncio.sleep(0.05)
    seq = [(x[0], (x[1].get("owner") if x[0] != "show" else x[1]["owner"])) for x in cam.screen.calls if x[0] in ("clock", "card", "show")]
    assert [k for k, _ in seq][:4] == ["clock", "card", "show", "clock"]                     # lần lượt rồi quay vòng
    assert {o for _, o in seq} == {"carousel"}                                               # luôn dưới tên «carousel»
    card = _calls("card")[0]
    assert card["value"] == "3,4 kW" and card["ttl"] == pytest.approx(0.9)                  # lớp có hạn dài gấp 3 thời lượng trang
    assert c.carousel.on
    await hass.services.async_call(DOMAIN, "screen_carousel", {"enabled": False}, blocking=True)
    assert ("release", "carousel") in cam.screen.calls and not c.carousel.running
    n = len(cam.screen.calls)
    await asyncio.sleep(0.5)
    assert len(cam.screen.calls) == n                                                          # tắt rồi thì không đẩy gì nữa
    assert not c.carousel.on


async def test_carousel_card_is_redrawn_only_when_its_data_changes(hass, monkeypatch):
    import custom_components.dosen_pro.carousel as car_mod
    monkeypatch.setattr(car_mod, "_CHECK_EVERY", 0.1)
    await _setup(hass)
    cam, c = _cam(), hass.data[DOMAIN][next(iter(hass.data[DOMAIN]))]
    hass.states.async_set("sensor.nd", "25", {"unit_of_measurement": "°C", "friendly_name": "Nhiệt độ"})
    c.carousel.pages = [{"type": "card", "main_entity": "sensor.nd", "seconds": 1.2}]
    await c.carousel.async_start()
    await asyncio.sleep(0.6)
    assert len(_calls("card")) == 1                                                            # số liệu không đổi → không vẽ lại
    hass.states.async_set("sensor.nd", "26", {"unit_of_measurement": "°C", "friendly_name": "Nhiệt độ"})
    await asyncio.sleep(0.4)
    assert [x["value"] for x in _calls("card")] == ["25 °C", "26 °C"]                          # đổi thì vẽ lại ngay trong trang
    await c.carousel.async_stop()


async def test_carousel_service_validates_pages_and_needs_pages_to_start(hass):
    await _setup(hass)
    with pytest.raises(HomeAssistantError):                                                    # chưa có trang nào
        await hass.services.async_call(DOMAIN, "screen_carousel", {}, blocking=True)
    for bad in ([{"type": "bay"}], [{"type": "image"}], [{"type": "camera"}], [{"type": "card"}], [{"type": "clock", "seconds": 1}],
                [{"type": "clock"}] * 13, [{"type": "clock", "khong_co": 1}]):
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(DOMAIN, "screen_carousel", {"pages": bad}, blocking=True)
    await hass.services.async_call(DOMAIN, "screen_carousel", {"pages": [{"type": "clock"}, {"type": "text", "title": "Xin chào"}],
                                                              "seconds": 20}, blocking=True)
    car = hass.data[DOMAIN][next(iter(hass.data[DOMAIN]))].carousel
    assert car.on and [p["type"] for p in car.pages] == ["clock", "text"] and car.seconds == 20.0
    await hass.services.async_call(DOMAIN, "screen_carousel", {"enabled": False}, blocking=True)
    assert not car.on


async def test_carousel_pages_and_switch_survive_a_restart(hass, hass_storage):
    key = "dosen_pro.eid123.carousel"
    hass_storage[key] = {"version": 1, "minor_version": 1, "key": key, "data": {
        "pages": [{"type": "clock", "seconds": 30.0}, {"type": "text", "title": "Nhà"}], "seconds": 12.0, "on": True}}
    await _setup(hass, entry_id="eid123")
    c = hass.data[DOMAIN]["eid123"]
    assert [p["type"] for p in c.carousel.pages] == ["clock", "text"] and c.carousel.seconds == 12.0
    assert c.carousel.running and c.carousel.on
    await c.carousel.async_stop()


async def test_carousel_seconds_default_20_can_be_changed_and_is_validated(hass):
    await _setup(hass)
    car = hass.data[DOMAIN][next(iter(hass.data[DOMAIN]))].carousel
    assert car.seconds == 20.0                                                     # mặc định 20 giây
    car.set_seconds(45)
    assert car.seconds == 45.0
    for bad in (1, 4000):
        with pytest.raises(ValueError):                                            # ngoài 3–3600 bị từ chối
            car.set_seconds(bad)
    assert car.seconds == 45.0
    await hass.services.async_call(DOMAIN, "screen_carousel", {"pages": [{"type": "clock"}, {"type": "text", "title": "A"}],
                                                              "enabled": False}, blocking=True)
    assert [p.get("seconds") for p in car.pages] == [None, None]                    # trang không có seconds riêng → dùng thời lượng chung


async def test_card_translates_on_off_sensors_to_vietnamese_with_colour_and_custom_names(hass):
    await _setup(hass)
    s = hass.states.async_set
    s("binary_sensor.ngu", "on", {"device_class": "occupancy", "friendly_name": "Hiện diện phòng ngủ Có người"})
    s("binary_sensor.hoc", "off", {"device_class": "occupancy", "friendly_name": "Hiện diện phòng học"})
    s("binary_sensor.cua", "on", {"device_class": "door", "friendly_name": "Cửa chính"})
    s("binary_sensor.khoi", "on", {"device_class": "smoke", "friendly_name": "Báo khói"})
    s("binary_sensor.la", "off", {"friendly_name": "Cảm biến lạ"})
    s("switch.den", "on", {"friendly_name": "Đèn bếp"})
    s("person.tri", "home", {"friendly_name": "Trí"})
    s("sensor.chu", "Đang chạy", {"friendly_name": "Máy giặt"})
    await hass.services.async_call(DOMAIN, "screen_card", {"title": "Hiện diện", "entities": [
        {"entity": "binary_sensor.ngu", "name": "Phòng ngủ"}, {"entity": "binary_sensor.hoc", "name": "Phòng học"},
        "binary_sensor.cua", "binary_sensor.khoi", "binary_sensor.la", "switch.den", "person.tri", "sensor.chu"]}, blocking=True)
    rows = _calls("card")[-1]["rows"]
    assert rows == [
        {"label": "Phòng ngủ", "value": "Có người", "color": "green"},          # tên tự đặt thay cho «Hiện diện phòng ngủ Có người»
        {"label": "Phòng học", "value": "Trống"},
        {"label": "Cửa chính", "value": "Mở", "color": "orange"},
        {"label": "Báo khói", "value": "CÓ KHÓI", "color": "red"},
        {"label": "Cảm biến lạ", "value": "Tắt"},                                # không rõ loại: Bật/Tắt
        {"label": "Đèn bếp", "value": "Bật", "color": "green"},
        {"label": "Trí", "value": "Ở nhà", "color": "green"},
        {"label": "Máy giặt", "value": "Đang chạy"}]                              # chữ khác giữ nguyên
