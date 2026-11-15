# Dịch vụ Dosen Pro (Docker)

HTTP API điều khiển màn hình, loa và mic của camera DOSEN RPO V1 (iCSee/XM) qua giao thức DVRIP
(cổng TCP 34567). Không dùng FunSDK, không qua cloud.

| Việc | API |
|---|---|
| Màn hình: hiện video, camera RTSP khác, ảnh | `POST /api/screen/show` `{"source": "...", "loop": false, "after": "off"\|"hold"}` |
| Màn hình: hiện chữ (hội thoại, thông báo) | `POST /api/screen/text` `{"title", "messages": [{"role": "user\|assistant\|caller\|info", "text"}], "font_size", "after"}` |
| Tắt màn hình, xem trạng thái | `POST /api/screen/off`, `GET /api/screen/status` |
| Loa: phát file/URL/TTS | `POST /api/speaker/play` `{"source": "...", "volume": 1.0}`, `POST /api/speaker/stop`, `GET /api/speaker/status` |
| Mic: nghe camera | `GET /api/audio/mic?format=pcm16\|wav\|g711a` (luồng), `POST /api/audio/mic/stop` |
| Loa (đẩy PCM thô) | `POST /api/audio/talk/start`, `/send` (`?format=pcm16\|g711a`), `/stop` |
| Event của camera (phát hiện người…) | `POST /api/events/start`, `GET /api/events`, `GET /api/events/stream` (SSE), `/stop` |
| Sức khoẻ, trạng thái | `GET /api/health`, `GET /api/status` |

`source` là bất cứ thứ gì ffmpeg đọc được: tệp, `http(s)://`, `rtsp://`, ảnh (`.png/.jpg`), hoặc `lavfi:...` để thử.
Màn hình camera là 240×320, H.265, 10 khung/giây; hình khác tỉ lệ được đặt vào khung có viền đen.
Chữ được vẽ thành ảnh rồi gửi như video (phông DejaVu, đủ tiếng Việt).

## Biến môi trường

Xem `.env.example`. Bắt buộc: `ICSEE_CAM_IP`, `ICSEE_CAM_USER`, `ICSEE_CAM_PASS`.
Nên đặt `API_KEY`: khi có, mọi lệnh `/api/*` (trừ `/api/health`) phải kèm `X-API-Key` hoặc `Authorization: Bearer`.
`LOG_LEVEL=DEBUG` để xem thêm; log không bao giờ chứa mật khẩu (URL RTSP được che `password=***`).

## Giao thức màn hình (AVTalk)

Giải mã từ tệp bắt gói của một cuộc gọi video thật từ app iCSee 7.9.6 tới camera và từ `libFunSDK.so`:
hai kết nối DVRIP; kết nối điều khiển đăng nhập, kết nối media chỉ mượn `SessionID`. Media: Claim `1417`
(kết nối media), Start `1415` (kết nối điều khiển), rồi gửi gói `1419`: khung key `00 00 01 fc` + [mã codec, fps, W/8, H/8,
giờ đóng gói XM, độ dài] + H.265 Annex-B; khung delta `00 00 01 fd` + độ dài; âm thanh `00 00 01 fa` + G.711A 320 byte/40 ms.
Chi tiết trong `app/avtalk.py`.

## Thử nhanh

```bash
curl -X POST localhost:8080/api/screen/text -H 'Content-Type: application/json' \
  -d '{"title":"Trợ lý","messages":[{"role":"user","text":"Bật đèn"},{"role":"assistant","text":"Đã bật."}]}'
curl -X POST localhost:8080/api/screen/show -H 'Content-Type: application/json' -d '{"source":"rtsp://camera-khac/stream"}'
curl -X POST localhost:8080/api/speaker/play -H 'Content-Type: application/json' -d '{"source":"http://.../tiengchuong.mp3"}'
```

## Kiểm thử

`python -m pytest tests -q` trong `server/` (cần ffmpeg có libx265). Bộ test dùng camera giả `tests/fake_cam.py`.
Lúc dựng ảnh Docker bộ test chạy luôn; ảnh hỏng test không dựng được.
