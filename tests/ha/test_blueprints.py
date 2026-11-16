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


@pytest.mark.parametrize("name", ["man_hinh_nhieu_viec.yaml", "nut_goi_camera_zalo.yaml",
                                  "man_hinh_the_thong_tin.yaml"])
def test_blueprints_load(name):
    bp = _bp(name)
    assert bp.metadata["domain"] == "automation" and bp.inputs


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


# -- «Một automation, thêm bao nhiêu sự kiện cũng được» ------------------------------------------------------------

def _trig(event, tid):
    return {"trigger": "event", "event_type": event, "id": tid}


async def test_many_events_one_automation_each_trigger_id_runs_its_own_job(hass, tmp_path):
    shows = async_mock_service(hass, "dosen_pro", "screen_show")
    texts = async_mock_service(hass, "dosen_pro", "screen_text")
    await _auto(hass, "man_hinh_nhieu_viec.yaml", {
        "trigger": [_trig("nguoi", "nguoi_o_cua"), _trig("khoi", "khoi_bep")],
        "viec": [
            {"ten": "Người ở cửa", "khi": ["nguoi_o_cua"], "kieu": "camera", "camera": ["camera.cua"], "thoi_gian": 20, "uu_tien": 3, "ten_lop": "nguoi_o_cua"},
            {"ten": "Khói bếp", "khi": ["khoi_bep"], "kieu": "chu", "tieu_de": "Có khói", "noi_dung": "Khói trong bếp", "uu_tien": 4, "ten_lop": "khoi"}]}, tmp_path)
    hass.bus.async_fire("nguoi")
    await hass.async_block_till_done()
    assert [x.data for x in shows] == [{"camera_entity": "camera.cua", "owner": "nguoi_o_cua", "after": "hold", "priority": 3, "split": True, "ttl": 20.0}]
    assert not texts
    hass.bus.async_fire("khoi")
    await hass.async_block_till_done()
    assert len(shows) == 1 and texts[-1].data == {"title": "Có khói", "messages": [{"role": "info", "text": "Khói trong bếp"}], "owner": "khoi",
                                                  "after": "hold", "priority": 4}


async def test_many_events_several_cameras_split_and_condition_entity_gates_the_job(hass, tmp_path):
    shows = async_mock_service(hass, "dosen_pro", "screen_show")
    hass.states.async_set("input_boolean.co_nha", "on")
    await _auto(hass, "man_hinh_nhieu_viec.yaml", {
        "trigger": [_trig("bam", "chuong")],
        "viec": [
            {"ten": "Chuông - hai camera", "khi": ["chuong"], "kieu": "camera", "camera": ["camera.cua", "camera.san"], "ten_lop": "chuong"},
            {"ten": "Chỉ khi vắng nhà", "khi": ["chuong"], "chi_khi_thuc_the": "input_boolean.co_nha", "chi_khi_trang_thai": ["off"],
             "kieu": "anh", "nguon": "http://nas/anh.jpg", "ten_lop": "vang"}]}, tmp_path)
    hass.bus.async_fire("bam")
    await hass.async_block_till_done()
    assert [x.data["camera_entity"] for x in shows] == ["camera.cua", "camera.san"]
    assert [x.data["owner"] for x in shows] == ["chuong_1", "chuong_2"]               # nhiều camera: mỗi camera một lớp riêng để chia đôi màn
    shows.clear()
    hass.states.async_set("input_boolean.co_nha", "off")                               # vắng nhà: việc thứ hai khớp
    hass.bus.async_fire("bam")
    await hass.async_block_till_done()
    assert shows[-1].data == {"loop": False, "owner": "vang", "priority": 2, "after": "hold", "source": "http://nas/anh.jpg"}


async def test_many_events_modes_first_or_most_recent_condition_sensor(hass, tmp_path):
    import asyncio
    shows = async_mock_service(hass, "dosen_pro", "screen_show")
    hass.states.async_set("binary_sensor.a", "on")
    await asyncio.sleep(0.05)
    hass.states.async_set("binary_sensor.b", "on")
    jobs = [{"ten": "a", "chi_khi_thuc_the": "binary_sensor.a", "chi_khi_trang_thai": ["on"], "kieu": "camera", "camera": ["camera.a"], "ten_lop": "a"},
            {"ten": "b", "chi_khi_thuc_the": "binary_sensor.b", "chi_khi_trang_thai": ["on"], "kieu": "camera", "camera": ["camera.b"], "ten_lop": "b"}]
    await _auto(hass, "man_hinh_nhieu_viec.yaml", {"trigger": [_trig("go", "x")], "viec": jobs, "cach_chon": "moi_nhat"}, tmp_path)
    hass.bus.async_fire("go")
    await hass.async_block_till_done()
    assert [x.data["camera_entity"] for x in shows] == ["camera.b"]                    # b đổi trạng thái sau cùng


async def test_many_events_a_hundred_jobs_pick_by_trigger_id(hass, tmp_path):
    shows = async_mock_service(hass, "dosen_pro", "screen_show")
    jobs = [{"ten": "v%d" % i, "khi": ["t%d" % i], "kieu": "camera", "camera": ["camera.c%d" % i], "ten_lop": "l%d" % i} for i in range(100)]
    await _auto(hass, "man_hinh_nhieu_viec.yaml", {"trigger": [_trig("s%d" % i, "t%d" % i) for i in (3, 57, 99)], "viec": jobs}, tmp_path)
    hass.bus.async_fire("s57")
    await hass.async_block_till_done()
    assert [x.data["camera_entity"] for x in shows] == ["camera.c57"]


async def test_many_events_media_picker_manual_url_and_text_without_ttl(hass, tmp_path):
    shows = async_mock_service(hass, "dosen_pro", "screen_show")
    texts = async_mock_service(hass, "dosen_pro", "screen_text")
    media = {"media_content_id": "media-source://media_source/local/chuong.mp4", "media_content_type": "video/mp4"}
    await _auto(hass, "man_hinh_nhieu_viec.yaml", {
        "trigger": [_trig("bam", "video"), _trig("url", "url"), _trig("la", "chu")],
        "viec": [{"ten": "Video chuông", "khi": ["video"], "kieu": "video", "media": media, "thoi_gian": 0},
                 {"ten": "Địa chỉ gõ tay", "khi": ["url"], "kieu": "video", "nguon": "rtsp://cam/s", "media": media, "thoi_gian": 15, "lap": True},
                 {"ten": "Chữ", "khi": ["chu"], "kieu": "chu", "tieu_de": "Cảnh báo", "noi_dung": "Có người lạ ở cổng"}]}, tmp_path)
    hass.bus.async_fire("bam")
    await hass.async_block_till_done()
    assert shows[-1].data["media"] == media and "source" not in shows[-1].data and shows[-1].data["after"] == "off"      # video không lặp, không ttl
    hass.bus.async_fire("url")
    await hass.async_block_till_done()
    assert shows[-1].data["source"] == "rtsp://cam/s" and "media" not in shows[-1].data and shows[-1].data["ttl"] == 15.0 and shows[-1].data["loop"] is True
    hass.bus.async_fire("la")
    await hass.async_block_till_done()
    assert texts[-1].data["title"] == "Cảnh báo" and "ttl" not in texts[-1].data and texts[-1].data["messages"][0]["text"] == "Có người lạ ở cổng"
    assert texts[-1].data["owner"] == "viec_2"                                   # không đặt tên lớp: lấy theo số thứ tự của việc

