"""Blueprint Dosen Pro: nạp được bằng bộ nạp thật của HA, thay đầu vào ra automation chạy đúng dịch vụ."""

import pathlib

import pytest
from homeassistant.components.blueprint import models
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.setup import async_setup_component
from homeassistant.util import yaml as yaml_util
from pytest_homeassistant_custom_component.common import async_mock_service

BP = pathlib.Path(__file__).parent.parent / "blueprints" / "automation" / "dosen_pro"


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


@pytest.mark.parametrize("name", ["man_hinh_su_kien.yaml", "man_hinh_cuoc_goi_zalo.yaml", "nut_goi_camera_zalo.yaml"])
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
                                                  "owner": "chuong_cua", "after": "hold", "ttl": 20.0}
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


async def test_zalo_blueprint_shows_call_then_releases(hass, tmp_path):
    shows = async_mock_service(hass, "dosen_pro", "screen_show")
    texts = async_mock_service(hass, "dosen_pro", "screen_text")
    rels = async_mock_service(hass, "dosen_pro", "screen_release")
    await _auto(hass, "man_hinh_cuoc_goi_zalo.yaml", {"video_url": "http://zalo:8090/api/call/video/view"}, tmp_path)
    hass.bus.async_fire("zalo_call_call_state", {"state": "incoming", "peer": "Bà"})
    await hass.async_block_till_done()
    assert texts and texts[-1].data["owner"] == "zalo_call" and "Bà đang gọi đến" in texts[-1].data["messages"][0]["text"]
    assert not shows                                            # chưa nghe máy: chỉ hiện chữ
    hass.bus.async_fire("zalo_call_call_state", {"state": "answered", "peer": "Bà"})
    await hass.async_block_till_done()
    assert shows[-1].data == {"source": "http://zalo:8090/api/call/video/view", "owner": "zalo_call", "after": "hold"}
    hass.bus.async_fire("zalo_call_call_state", {"state": "ended", "peer": "Bà"})
    await hass.async_block_till_done()
    assert rels[-1].data == {"owner": "zalo_call"}


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
