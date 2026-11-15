"""Hằng số của tích hợp Dosen Pro."""

DOMAIN = "dosen_pro"
CONF_URL = "url"                    # chỉ còn để nhận ra mục cấu hình cũ (kiểu dịch vụ Docker)
CONF_API_KEY = "api_key"
DEFAULT_PORT = 34567
SCAN_INTERVAL_SECONDS = 10

#: Tiếng mic đưa vào pipeline: PCM16 mono 16 kHz — định dạng Assist đòi (mic camera gốc 8 kHz, ffmpeg đổi).
MIC_RATE = 16000
MIC_CHUNK = 2048
#: Khuếch đại mic tối đa (dB). Cao hơn thì tiếng ồn nền cũng lớn theo.
MIC_GAIN_MAX = 30
#: Giữ chữ hội thoại trên màn camera thêm ngần này giây sau khi trả lời xong rồi tắt màn hiển thị.
CHAT_HOLD_SECONDS = 10
CHAT_MAX_MESSAGES = 6
CONF_ALARM_EVENTS = "alarm_events"       # tuỳ chọn: false = không mở luồng sự kiện camera (dùng trong test)
EVENT_ALARM = "dosen_pro_alarm"           # sự kiện bus HA cho MỌI báo động camera đẩy tới
DEFAULT_MODES = ["off", "clock", "image", "video", "live"]
ASSIST_OWNER = "assist"
# Tuỳ chọn của mục cấu hình (hộp «Cấu hình»): màn hình mặc định chọn theo danh sách của HA.
OPT_HA_URL = "ha_url"              # địa chỉ HA mà dịch vụ Docker dùng để tải ảnh/video (nếu HA chưa có địa chỉ nội bộ)
ROTATE_OPTIONS = ["0°", "90°", "180°", "270°"]          # xoay hình theo chiều kim đồng hồ
FIT_OPTIONS = {"Vừa khung (có viền)": "contain", "Lấp đầy (cắt rìa)": "cover"}
CONF_INTERCOM_KEY = "intercom_key"   # khoá trong địa chỉ bộ đàm (Zalo Call đọc từ dữ liệu mục cấu hình, như loa R1)
RTSP_PORT = 554
RTSP_STREAM = 1                      # 0 = luồng chính, 1 = luồng phụ (800x448 HEVC + tiếng A-law, đo trên camera thật)
NONE_OPTION = "—"                  # «chưa chọn» trong các ô chọn camera/ảnh


def device_info(entry_id: str) -> dict:
    """Một thiết bị cho cả loa và màn hình (tên thực thể luôn có tiền tố «Dosen Pro»)."""
    return {"identifiers": {(DOMAIN, entry_id)}, "name": "Dosen Pro", "manufacturer": "DOSEN"}
