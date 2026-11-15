"""Hằng số của tích hợp Dosen Pro."""

DOMAIN = "dosen_pro"
CONF_URL = "url"
CONF_API_KEY = "api_key"
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
OPT_MODE = "default_mode"          # off | clock | image | video | live
OPT_CAMERA = "default_camera"      # entity camera trong HA (chế độ live)
OPT_IMAGE = "default_image"        # bộ chọn media (chế độ image)
OPT_VIDEO = "default_video"        # bộ chọn media (chế độ video)


def device_info(entry_id: str) -> dict:
    """Một thiết bị cho cả loa và màn hình (tên thực thể luôn có tiền tố «Dosen Pro»)."""
    return {"identifiers": {(DOMAIN, entry_id)}, "name": "Dosen Pro", "manufacturer": "DOSEN"}
