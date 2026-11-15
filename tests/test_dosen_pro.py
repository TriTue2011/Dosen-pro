"""Tích hợp Dosen Pro: luồng cấu hình, dịch vụ, media_player, cảm biến."""

import aiohttp
import pytest
from homeassistant.components.media_player import DOMAIN as MP_DOMAIN
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dosen_pro.const import CONF_API_KEY, CONF_URL, DOMAIN

URL = "http://dosen.test:8080"
SCREEN = {"mode": "off", "open": False, "source": "", "after": "off", "size": [240, 320], "last_error": None}
SPEAKER = {"playing": False, "source": "", "bytes_sent": 0, "last_error": None}


def _mock_api(aioclient_mock, status=200):
    aioclient_mock.get(URL + "/api/health", json={"status": "ok"})
    aioclient_mock.get(URL + "/api/status", status=status, json={"service": "x"})
    aioclient_mock.get(URL + "/api/screen/status", json=SCREEN)
    aioclient_mock.get(URL + "/api/speaker/status", json=SPEAKER)
    for p in ("screen/show", "screen/text", "screen/off", "screen/sleep", "speaker/play", "speaker/stop"):
        aioclient_mock.post(URL + "/api/" + p, json={})


async def _setup(hass, aioclient_mock):
    _mock_api(aioclient_mock)
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_URL: URL, CONF_API_KEY: "k1"}, unique_id=URL)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _posts(aioclient_mock, path):
    return [(m, u, b) for m, u, b, _ in aioclient_mock.mock_calls if m == "POST" and str(u).endswith(path)]


async def test_config_flow_ok_and_errors(hass, aioclient_mock):
    _mock_api(aioclient_mock)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert r["type"] == FlowResultType.FORM
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_URL: URL + "/", CONF_API_KEY: "k1"})
    assert r["type"] == FlowResultType.CREATE_ENTRY
    assert r["data"] == {CONF_URL: URL, CONF_API_KEY: "k1"}          # bỏ dấu / cuối


async def test_config_flow_wrong_key(hass, aioclient_mock):
    _mock_api(aioclient_mock, status=401)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_URL: URL, CONF_API_KEY: "sai"})
    assert r["type"] == FlowResultType.FORM and r["errors"] == {"base": "invalid_auth"}


async def test_config_flow_cannot_connect(hass, aioclient_mock):
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
    await hass.services.async_call(DOMAIN, "screen_show", {"source": "http://x/clip.mp4", "loop": True}, blocking=True)
    assert _posts(aioclient_mock, "/api/screen/show")[-1][2] == {"source": "http://x/clip.mp4", "loop": True, "after": "off"}
    await hass.services.async_call(DOMAIN, "screen_off", {}, blocking=True)
    assert _posts(aioclient_mock, "/api/screen/off")
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
    st = hass.states.get("sensor.dosen_pro_man_hinh")
    assert st is not None and st.state == "off" and st.attributes["size"] == [240, 320]


async def test_unload_removes_services(hass, aioclient_mock):
    entry = await _setup(hass, aioclient_mock)
    assert hass.services.has_service(DOMAIN, "screen_show")
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not hass.services.has_service(DOMAIN, "screen_show")
