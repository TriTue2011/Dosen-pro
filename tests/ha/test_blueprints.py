"""Blueprint Dosen Pro: nạp được bằng bộ nạp thật của HA, thay đầu vào ra automation chạy đúng dịch vụ."""

import pathlib

import pytest
from homeassistant.components.blueprint import models
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.setup import async_setup_component
from homeassistant.util import yaml as yaml_util
from pytest_homeassistant_custom_component.common import async_mock_service

BP = pathlib.Path(__file__).parent.parent.parent / "blueprints" / "automation" / "dosen_pro"


def _bp(name):
    return models.Blueprint(yaml_util.load_yaml(str(BP / name)), expected_domain="automation",
                            schema=BLUEPRINT_SCHEMA)


async def _auto(hass, name, inputs, tmp_path):
    """Đặt blueprint vào thư mục cấu hình rồi tạo automation dùng nó."""
    dest = pathlib.Path(hass.config.path("blueprints/automation/dosen_pro"))
    dest.mkdir(parents=True, exist_ok=True)
    (dest / name).write_text((BP / name).read_text())
    assert await async_setup_component(hass, "automation", {"automation": [{
        "alias": name, "use_blueprint": {"path": f"dosen_pro/{name}", "input": inputs}}]})
    await hass.async_block_till_done()


@pytest.mark.parametrize("name", ["man_hinh_su_kien.yaml", "man_hinh_nhieu_su_kien.yaml", "nut_goi_camera_zalo.yaml",
                                  "man_hinh_the_thong_tin.yaml"])
def test_blueprints_load(name):
    bp = _bp(name)
    assert bp.metadata["domain"] == "automation" and bp.inputs


async def test_event_blueprint_shows_camera_with_ttl(hass, tmp_path):
    shows = async_mock_service(hass, "dosen_pro", "screen_show")
    texts = async_mock_service(hass, "dosen_pro", "screen_text")
    await _auto(hass, "man_hinh_su_kien.yaml", {
        "trigger": [{"platform": "event", "event_type": "chuong_bam"}],
        "kieu": "camera", "camera": "camera.cua", "thoi_gian": 20, "ten_lop": "chuong_cua"}, tmp_path)
    hass.bus.async_fire("chuong_bam")
    await hass.async_block_till_done()
    assert len(shows) == 1 and shows[0].data == {"camera_entity": "camera.cua",      # chọn camera theo danh sách của HA
                                                  "owner": "chuong_cua", "after": "hold", "ttl": 20.0,
                                                  "priority": 2, "split": True}
    assert not texts


async def test_event_blueprint_media_picker_and_manual_url(hass, tmp_path):
    shows = async_mock_service(hass, "dosen_pro", "screen_show")
    media = {"media_content_id": "media-source://media_source/local/chuong.mp4", "media_content_type": "video/mp4"}
    await _auto(hass, "man_hinh_su_kien.yaml", {
        "trigger": [{"platform": "event", "event_type": "bam"}], "kieu": "video", "media": media, "thoi_gian": 0}, tmp_path)
    hass.bus.async_fire("bam")
    await hass.async_block_till_done()
    assert shows[-1].data["media"] == media and "source" not in shows[-1].data and shows[-1].data["after"] == "off"


async def test_event_blueprint_text_without_ttl(hass, tmp_path):
    texts = async_mock_service(hass, "dosen_pro", "screen_text")
    await _auto(hass, "man_hinh_su_kien.yaml", {
        "trigger": [{"platform": "event", "event_type": "nguoi_la"}],
        "kieu": "chu", "tieu_de": "Cảnh báo", "noi_dung": "Có người lạ ở cổng", "thoi_gian": 0}, tmp_path)
    hass.bus.async_fire("nguoi_la")
    await hass.async_block_till_done()
    assert texts[-1].data["title"] == "Cảnh báo" and texts[-1].data["owner"] == "su_kien"
    assert "ttl" not in texts[-1].data and texts[-1].data["messages"][0]["text"] == "Có người lạ ở cổng"


async def test_call_button_blueprint_answers_or_dials(hass, tmp_path):
    answer = async_mock_service(hass, "zalo_call", "answer_voice_call")
    dial = async_mock_service(hass, "zalo_call", "make_call")
    async_mock_service(hass, "dosen_pro", "screen_text")
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {"co_goi": {}}})
    await _auto(hass, "nut_goi_camera_zalo.yaml", {
        "co_goi_den": "input_boolean.co_goi", "nguoi_nhan": "123456", "loai_goi": "video"}, tmp_path)
    press = {"event": "LocalAlarm", "status": "Start"}
    hass.bus.async_fire("dosen_pro_alarm", press)                       # chưa có cuộc gọi đến: gọi đi
    await hass.async_block_till_done()
    assert len(dial) == 1 and dial[0].data == {"thread_id": "123456", "type": "video"} and not answer
    hass.bus.async_fire("zalo_call_call_state", {"state": "incoming"})  # có cuộc gọi đến
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.co_goi").state == "on"
    hass.bus.async_fire("dosen_pro_alarm", press)                       # bấm nút: nghe máy
    await hass.async_block_till_done()
    assert len(answer) == 1 and len(dial) == 1
    hass.bus.async_fire("dosen_pro_alarm", {"event": "LocalAlarm", "status": "Stop"})   # nhả nút: không làm gì
    hass.bus.async_fire("dosen_pro_alarm", {"event": "appEventHumanDetectAlarm", "status": "Start"})   # sự kiện khác
    await hass.async_block_till_done()
    assert len(answer) == 1 and len(dial) == 1
    hass.bus.async_fire("zalo_call_call_state", {"state": "ended"})
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.co_goi").state == "off"


async def test_info_card_blueprint_sends_main_sensor_rows_and_ttl(hass, tmp_path):
    cards = async_mock_service(hass, "dosen_pro", "screen_card")
    await _auto(hass, "man_hinh_the_thong_tin.yaml", {
        "bat_dau": [{"platform": "event", "event_type": "xem_dien"}], "tieu_de": "Điện mặt trời", "cam_bien_chinh": "sensor.solar",
        "mau": "green", "cac_dong": ["sensor.nha", "sensor.pin"], "giu": 45, "ten_lop": "dien"}, tmp_path)
    hass.bus.async_fire("xem_dien")
    await hass.async_block_till_done()
    assert cards[-1].data == {"title": "Điện mặt trời", "color": "green", "owner": "dien", "after": "hold",
                              "main_entity": "sensor.solar", "entities": ["sensor.nha", "sensor.pin"], "ttl": 45.0}


async def test_info_card_blueprint_without_main_sensor_or_ttl_sends_only_what_is_chosen(hass, tmp_path):
    cards = async_mock_service(hass, "dosen_pro", "screen_card")
    await _auto(hass, "man_hinh_the_thong_tin.yaml", {
        "bat_dau": [{"platform": "event", "event_type": "xem_cb"}], "cac_dong": ["sensor.a"], "giu": 0}, tmp_path)
    hass.bus.async_fire("xem_cb")
    await hass.async_block_till_done()
    d = cards[-1].data
    assert "main_entity" not in d and "ttl" not in d and d["entities"] == ["sensor.a"] and d["title"] == "Thông tin"


async def _fire(hass, entity):
    hass.states.async_set(entity, "off")
    hass.states.async_set(entity, "on")
    await hass.async_block_till_done()


async def test_multi_event_blueprint_each_slot_shows_its_own_kind_with_priority(hass, tmp_path):
    shows = async_mock_service(hass, "dosen_pro", "screen_show")
    texts = async_mock_service(hass, "dosen_pro", "screen_text")
    await _auto(hass, "man_hinh_nhieu_su_kien.yaml", {
        "e1": ["binary_sensor.nguoi"], "k1": "camera", "c1": ["camera.cua", "camera.san", "camera.bep"], "u1": "3", "tg1": 10, "l1": "nguoi",
        "e2": ["binary_sensor.chuong"], "k2": "chu", "td2": "Chuông", "nd2": "Có khách", "u2": "2", "tg2": 0, "l2": "chuong",
        "e3": ["binary_sensor.khoi"], "k3": "anh", "m3": {"media_content_id": "media-source://media_source/local/khoi.png"},
        "u3": "4", "tg3": 0, "l3": "khoi",
        # ô 4 để trống: không kích hoạt
    }, tmp_path)
    await _fire(hass, "binary_sensor.nguoi")
    assert [c.data["camera_entity"] for c in shows] == ["camera.cua", "camera.san", "camera.bep"]
    assert all(c.data["split"] is True and c.data["priority"] == 3 and c.data["ttl"] == 10.0 for c in shows)
    assert [c.data["owner"] for c in shows] == ["nguoi_1", "nguoi_2", "nguoi_3"]          # mỗi camera một lớp riêng để ghép/xoay
    await _fire(hass, "binary_sensor.chuong")
    assert texts[-1].data["priority"] == 2 and texts[-1].data["owner"] == "chuong" and "ttl" not in texts[-1].data
    await _fire(hass, "binary_sensor.khoi")
    last = shows[-1].data
    assert last["priority"] == 4 and last["owner"] == "khoi" and last["after"] == "hold" and "split" not in last
    assert last["media"] == {"media_content_id": "media-source://media_source/local/khoi.png"}


async def test_old_event_blueprint_camera_now_carries_priority_and_split(hass, tmp_path):
    shows = async_mock_service(hass, "dosen_pro", "screen_show")
    await _auto(hass, "man_hinh_su_kien.yaml", {
        "trigger": [{"trigger": "state", "entity_id": "binary_sensor.nguoi", "to": "on"}],
        "kieu": "camera", "camera": "camera.cua", "thoi_gian": 10, "uu_tien": "3"}, tmp_path)
    await _fire(hass, "binary_sensor.nguoi")
    assert shows[-1].data == {"camera_entity": "camera.cua", "owner": "su_kien", "after": "hold", "priority": 3, "split": True, "ttl": 10.0}
