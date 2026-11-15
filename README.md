# Dosen Pro

Biến màn hình, loa và mic của camera **DOSEN RPO V1** (app iCSee/XM, màn hình 2,8" gọi video hai chiều) thành thiết bị đa dụng
cho Home Assistant: phát video từ HA, xem camera khác trực tiếp trên màn, hiện chữ hội thoại của Assist hay tên người gọi Zalo,
phát nhạc và TTS ra loa, nghe mic.

| Phần | Thư mục | Là gì |
|---|---|---|
| Dịch vụ Docker | `server/` | HTTP API nói DVRIP với camera (xem `server/README.md`) |
| Tích hợp HA (custom) | `custom_components/dosen_pro/` | Dịch vụ, thực thể loa (`media_player`) và cảm biến màn hình |
| Ảnh Docker | `ghcr.io/tritue2011/dosen-pro` | Dựng bằng `.github/workflows/image.yml` |

## Cài đặt

1. **Dịch vụ:** sao chép `server/.env.example` thành `.env`, điền IP, tài khoản, mật khẩu camera (và `API_KEY`), rồi
   `docker compose up -d` ở thư mục gốc (dùng ảnh GHCR), hoặc `docker compose up -d --build` trong `server/`.
2. **Tích hợp HA:** HACS → kho tuỳ chỉnh → `https://github.com/TriTue2011/Dosen-pro` → cài *Dosen Pro* → khởi động lại HA →
   Cài đặt → Thiết bị và dịch vụ → Thêm tích hợp → *Dosen Pro* → điền `http://<máy chạy Docker>:8080` và khoá API.

## Dùng trong Home Assistant

Thực thể: `media_player.dosen_pro_loa` (phát URL, TTS, kho media của HA), `sensor.dosen_pro_man_hinh` (off/video/image/text).
Dịch vụ `dosen_pro.screen_show`, `screen_text`, `screen_off`, `speaker_play`, `speaker_stop`.

```yaml
# Chuông cửa: hình camera cửa lên màn, kèm tiếng chuông
- action: dosen_pro.screen_show
  data: {source: "rtsp://camera-cua/stream", after: "off"}
- action: dosen_pro.speaker_play
  data: {source: "http://homeassistant.local:8123/local/chuong.mp3"}

# Hiện hội thoại Assist
- action: dosen_pro.screen_text
  data:
    title: "Trợ lý"
    messages:
      - {role: user, text: "{{ trigger.event.data.text }}"}
      - {role: assistant, text: "Đã bật đèn."}

# TTS ra loa camera
- action: tts.speak
  target: {entity_id: tts.piper}
  data: {media_player_entity_id: media_player.dosen_pro_loa, message: "Có khách ở cửa"}
```

## Giới hạn đã biết

- Loa và mic của camera là chất lượng điện thoại (G.711, 8 kHz): nhạc nghe như qua điện thoại.
- Màn hình 240×320, một phiên hình tại một thời điểm; hình có tiếng chưa hỗ trợ (hiện gửi âm thanh im lặng kèm hình; tiếng từ `speaker_play` đi qua kênh âm thanh của chính phiên màn hình khi màn đang mở).
- Nút gọi trên camera **không** sinh event qua đường LAN (đã đo): chưa bắt được sự kiện bấm nút.
- Chưa có tích hợp Assist (mic → STT) và cầu nối cuộc gọi Zalo: đã có đủ API (mic, loa, màn hình) để nối.
