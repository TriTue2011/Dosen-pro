"""Hằng số của tích hợp Dosen Pro."""

DOMAIN = "dosen_pro"
CONF_URL = "url"                    # chỉ còn để nhận ra mục cấu hình cũ (kiểu dịch vụ Docker)
CONF_API_KEY = "api_key"
DEFAULT_PORT = 34567
SCAN_INTERVAL_SECONDS = 10

CONF_ALARM_EVENTS = "alarm_events"       # tuỳ chọn: false = không mở luồng sự kiện camera (dùng trong test)
EVENT_ALARM = "dosen_pro_alarm"           # sự kiện bus HA cho MỌI báo động camera đẩy tới
DEFAULT_MODES = ["off", "clock", "image", "video", "live"]
# Tuỳ chọn của mục cấu hình (hộp «Cấu hình»): màn hình mặc định chọn theo danh sách của HA.
OPT_HA_URL = "ha_url"              # địa chỉ HA mà dịch vụ Docker dùng để tải ảnh/video (nếu HA chưa có địa chỉ nội bộ)
CONF_INTERCOM_KEY = "intercom_key"   # khoá trong địa chỉ bộ đàm (Zalo Call đọc từ dữ liệu mục cấu hình, như loa R1)
RTSP_PORT = 554
RTSP_STREAM = 1                      # 0 = luồng chính, 1 = luồng phụ (800x448 HEVC + tiếng A-law, đo trên camera thật)


def device_info(entry_id: str) -> dict:
    """Một thiết bị cho cả loa và màn hình (tên thực thể luôn có tiền tố «Dosen Pro»)."""
    return {"identifiers": {(DOMAIN, entry_id)}, "name": "Dosen Pro", "manufacturer": "DOSEN"}
