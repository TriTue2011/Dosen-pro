"""Thẻ «Dosen Pro»: API WebSocket (cài đặt hiển thị, quản lý thẻ, xem trước), lưu qua khởi động lại, đăng ký giao diện."""

import asyncio
import base64
import pathlib
import sys
import types

import pytest
import test_dosen_pro as base
from custom_components.dosen_pro.const import DOMAIN

_setup, _cam, _calls = base._setup, base._cam, base._calls


class Ws:
    def __init__(self, client):
        self.client, self.n = client, 0

    async def call(self, type_, **kw):
        self.n += 1
        await self.client.send_json({"id": self.n, "type": f"dosen_pro/{type_}", **kw})
        return await self.client.receive_json()


@pytest.fixture
async def ws(hass, hass_ws_client):
    await _setup(hass)
    return Ws(await hass_ws_client(hass))


def _coord(hass):
    return hass.data[DOMAIN][next(iter(hass.data[DOMAIN]))]


async def test_state_has_settings_cards_and_the_choices_the_card_offers(hass, ws):
    r = await ws.call("state")
    assert r["success"]
    st = r["result"]
    assert st["settings"] == {"default_mode": None, "default_camera": "", "default_image": "", "default_text": "", "default_weather": "",
                              "default_rotate": 0, "default_fit": "contain", "default_quality": "standard", "zalo_call_screen": True}
    assert st["carousel"]["seconds"] == 20.0 and st["carousel"]["cards"] == [] and st["carousel"]["on"] is False
    assert {"red", "green"} <= set(st["options"]["colors"]) and st["options"]["themes"] == ["light", "dark"]
    assert st["entry_id"] == _coord(hass).entry.entry_id


async def test_only_admins_may_use_the_card_api(hass, hass_ws_client, hass_read_only_access_token):
    await _setup(hass)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json({"id": 1, "type": "dosen_pro/state"})
    r = await client.receive_json()
    assert r["success"] is False and r["error"]["code"] == "unauthorized"


async def test_display_settings_survive_a_restart(hass, hass_storage):
    entry = await _setup(hass, entry_id="eidset")
    await _coord(hass).display.async_set({"default_mode": "clock", "default_rotate": 90, "default_quality": "high", "zalo_call_screen": False})
    await hass.async_block_till_done()
    await _coord(hass).display._store._async_handle_write_data() if hasattr(_coord(hass).display._store, "_async_handle_write_data") else None
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    saved = hass_storage["dosen_pro.eidset.display"]["data"]
    assert saved["default_mode"] == "clock" and saved["default_rotate"] == 90 and saved["zalo_call_screen"] is False
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    c = _coord(hass)
    assert (c.default_mode, c.default_rotate, c.default_quality, c.zalo_call_screen) == ("clock", 90, "high", False)


async def test_a_setting_the_camera_refuses_is_rolled_back(hass, ws, monkeypatch):
    c = _coord(hass)

    async def boom(*a, **k):
        from custom_components.dosen_pro.api import DosenApiError
        raise DosenApiError("camera tu choi")
    monkeypatch.setattr(c.api, "screen_quality", boom)
    r = await ws.call("set", changes={"default_quality": "max"})
    assert r["success"] is False and "camera tu choi" in r["error"]["message"]
    assert c.default_quality == "standard"                                       # không áp được thì giữ như cũ


async def test_card_crud_move_rotation_and_validation(hass, ws):
    c = _coord(hass)
    r = await ws.call("card_save", card={"type": "card", "name": "Nhiệt độ", "title": "Nhiệt độ", "color": "orange", "theme": "dark",
                                         "layout": "tiles", "entities": [{"entity": "sensor.a", "name": "Phòng khách", "color": "#ff00aa"}]})
    assert r["success"], r
    a = r["result"]["id"]
    r = await ws.call("card_save", card={"type": "clock", "name": "Đồng hồ"})
    b = r["result"]["id"]
    cards = r["result"]["carousel"]["cards"]
    assert [x["name"] for x in cards] == ["Nhiệt độ", "Đồng hồ"] and r["result"]["carousel"]["selected"] == [a, b]
    assert cards[0]["entities"][0]["color"] == "#ff00aa" and cards[0]["theme"] == "dark" and cards[0]["layout"] == "tiles"
    # sửa tên + đổi vị trí
    r = await ws.call("card_save", card={"id": a, "type": "card", "name": "Nhiệt độ nhà", "title": "T", "entities": ["sensor.a"]})
    assert r["success"] and c.carousel.card_by_id(a)["name"] == "Nhiệt độ nhà"
    r = await ws.call("card_move", card_id=b, delta=-1)
    assert [x["id"] for x in r["result"]["carousel"]["cards"]] == [b, a]
    # luân phiên: chọn thẻ nào, bao nhiêu giây
    r = await ws.call("rotation", selected=[a], seconds=45, on=True)
    assert r["success"] and r["result"]["carousel"]["selected"] == [a] and r["result"]["carousel"]["seconds"] == 45.0
    assert c.carousel.on and c.carousel.running and [p["type"] for p in c.carousel.pages] == ["card"]
    # sai dữ liệu
    for bad in ({"type": "bay"}, {"type": "camera"}, {"type": "card", "title": "x", "color": "tim"}, {"type": "card", "name": "x"}):
        r = await ws.call("card_save", card=bad)
        assert r["success"] is False, bad
    assert (await ws.call("rotation", seconds=1))["success"] is False
    # bỏ hết thẻ tham gia thì luân phiên tự tắt
    r = await ws.call("rotation", selected=[])
    assert r["result"]["carousel"]["on"] is False and not c.carousel.running
    r = await ws.call("card_delete", ids=[a])
    assert [x["id"] for x in r["result"]["carousel"]["cards"]] == [b]
    await c.carousel.async_stop()


async def test_preview_renders_the_real_card_with_live_data_and_names_unrenderable_types(hass, ws):
    hass.states.async_set("sensor.nd", "27.7", {"unit_of_measurement": "°C", "friendly_name": "Nhiệt độ phòng khách"})
    hass.states.async_set("binary_sensor.ngu", "on", {"device_class": "occupancy", "friendly_name": "Hiện diện phòng ngủ Có người"})
    r = await ws.call("preview", card={"type": "card", "title": "Nhiệt độ", "main_entity": "sensor.nd", "color": "orange",
                                       "entities": [{"entity": "binary_sensor.ngu", "name": "Phòng ngủ"}]})
    assert r["success"], r
    png = base64.b64decode(r["result"]["png"])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    from io import BytesIO
    from PIL import Image
    assert Image.open(BytesIO(png)).size == (240, 320)
    clock = await ws.call("preview", card={"type": "clock"})
    assert clock["success"] and base64.b64decode(clock["result"]["png"])[:4] == b"\x89PNG"
    cam = await ws.call("preview", card={"type": "camera", "camera_entity": "camera.cua"})
    assert cam["success"] and cam["result"]["png"] is None and "trực tiếp" in cam["result"]["note"]
    assert (await ws.call("preview", card={"type": "bay"}))["success"] is False
    assert _coord(hass).carousel.memory == {}                                    # xem thử không ghi vào bộ nhớ giá trị


async def test_show_now_pushes_the_card_being_edited_to_the_screen_for_30_seconds(hass, ws, monkeypatch):
    async def fake_source(h, **kw):
        return "rtsp://x/" + (kw.get("camera_entity") or "")
    import custom_components.dosen_pro.websocket as wsmod
    monkeypatch.setattr(wsmod, "resolve_source", fake_source)
    hass.states.async_set("sensor.nd", "20", {"unit_of_measurement": "°C", "friendly_name": "Nhiệt độ"})
    await ws.call("show_now", card={"type": "card", "title": "T", "main_entity": "sensor.nd"})
    card = _calls("card")[-1]
    assert card["owner"] == "xem_thu" and card["ttl"] == 30 and card["value"] == "20 °C"
    await ws.call("show_now", card={"type": "clock"})
    assert _calls("clock")[-1]["owner"] == "xem_thu"
    await ws.call("show_now", card={"type": "camera", "camera_entity": "camera.cua"})
    assert _calls("show")[-1]["source"] == "rtsp://x/camera.cua" and _calls("show")[-1]["owner"] == "xem_thu"


async def test_screen_info_and_settings_use_the_cameras_own_values(hass, ws):
    r = await ws.call("screen_info")
    assert r["success"] and r["result"]["seconds"] == 0 and r["result"]["luminance"] == 100
    r = await ws.call("screen_set", luminance=40, seconds=30, power=True)
    assert r["success"]
    assert ("power", True) in _cam().screen.calls and _calls("sleep")[-1] == {"seconds": 30, "luminance": 40}
    assert (await ws.call("screen_set", luminance=500))["success"] is False


async def test_frontend_assets_are_registered_automatically_with_the_integration(hass, monkeypatch):
    """Cài tích hợp là tự có thẻ: tệp JS được phục vụ, nạp trên mọi bảng điều khiển và có mục ở thanh bên."""
    import custom_components.dosen_pro as pkg
    seen = {}
    frontend = types.ModuleType("homeassistant.components.frontend")
    frontend.add_extra_js_url = lambda h, url, es5=False: seen.setdefault("js", url)
    panel_custom = types.ModuleType("homeassistant.components.panel_custom")

    async def register_panel(h, **kw):
        seen["panel"] = kw
    panel_custom.async_register_panel = register_panel
    monkeypatch.setitem(sys.modules, "homeassistant.components.frontend", frontend)
    monkeypatch.setitem(sys.modules, "homeassistant.components.panel_custom", panel_custom)
    import homeassistant.components as comps
    monkeypatch.setattr(comps, "frontend", frontend, raising=False)
    monkeypatch.setattr(comps, "panel_custom", panel_custom, raising=False)

    async def static(paths):
        seen["static"] = paths
    hass.http = types.SimpleNamespace(async_register_static_paths=static)
    # gọi hàm THẬT (bản gốc, không phải bản bị fixture thay bằng hàm rỗng)
    real = pkg._async_register_frontend_assets.__wrapped__ if hasattr(pkg._async_register_frontend_assets, "__wrapped__") else None
    import importlib
    src = importlib.import_module("custom_components.dosen_pro")
    fn = getattr(src, "_REAL_ASSETS", None)
    assert fn is not None, "cần giữ hàm đăng ký thật ở _REAL_ASSETS"
    await fn(hass)
    assert seen["js"].startswith("/dosen_pro_static/dosen-pro-card.js?v=")
    assert seen["static"][0].url_path == "/dosen_pro_static" and (pathlib.Path(seen["static"][0].path) / "dosen-pro-card.js").is_file()
    assert seen["panel"]["webcomponent_name"] == "dosen-pro-panel" and seen["panel"]["require_admin"] is True
    assert seen["panel"]["module_url"] == seen["js"] and seen["panel"]["frontend_url_path"] == "dosen-pro"


def test_the_card_file_defines_both_elements_and_lists_itself_in_the_card_picker():
    js = (pathlib.Path(base.pkg.__file__).parent / "frontend" / "dosen-pro-card.js").read_text(encoding="utf-8")
    for needle in ('customElements.define("dosen-pro-card"', 'customElements.define("dosen-pro-panel"', 'type: "dosen-pro-card"',
                   "dosen_pro/state", "dosen_pro/preview", "dosen_pro/card_save", "dosen_pro/set"):
        assert needle in js, needle


async def test_old_display_setting_entities_are_removed_from_the_registry_on_setup(hass):
    from homeassistant.helpers import entity_registry as er
    from homeassistant.setup import async_setup_component
    assert await async_setup_component(hass, "homeassistant", {})
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    entry = MockConfigEntry(domain=DOMAIN, data=base.DATA, unique_id=base.DATA["host"], title="Dosen Pro", options={"alarm_events": False})
    entry.add_to_hass(hass)
    reg = er.async_get(hass)
    old = {"select": ["default_screen", "default_camera", "default_image", "default_weather", "default_rotate", "default_fit", "screen_quality"],
           "text": ["default_address"], "number": ["screen_time", "screen_brightness", "carousel-seconds", "mic_gain"],
           "switch": ["show_chat", "carousel", "mute"], "assist_satellite": ["satellite"], "button": ["btn-speaker_stop"]}
    for domain, suffixes in old.items():
        for suffix in suffixes:
            reg.async_get_or_create(domain, DOMAIN, f"{entry.entry_id}-{suffix}", config_entry=entry, suggested_object_id=f"dosen_pro_{suffix}")
    keep = reg.async_get_or_create("switch", DOMAIN, f"{entry.entry_id}-audio-anr", config_entry=entry, suggested_object_id="dosen_pro_chong_on")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    left = {e.unique_id for e in er.async_entries_for_config_entry(reg, entry.entry_id)}
    for suffixes in old.values():
        for suffix in suffixes:
            assert f"{entry.entry_id}-{suffix}" not in left, suffix
    assert keep.unique_id in left                  # thực thể còn dùng thì giữ
