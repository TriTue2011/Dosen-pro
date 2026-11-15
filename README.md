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

Làm theo khuôn của [ha-camera-talk](https://github.com/TriTue2011/ha-camera-talk) (camera Dahua/Imou làm vệ tinh Assist), thêm phần màn hình.

| Thực thể | Làm gì |
|---|---|
| `assist_satellite.dosen_pro` | Nghe mic camera, chạy pipeline Assist (từ gọi → nhận giọng → hiểu lệnh → trả lời), đọc câu trả lời ra loa camera, hội thoại nối tiếp. **Câu hỏi và câu trả lời hiện lên màn hình camera**, màn tự tắt sau 10 giây. |
| `media_player.dosen_pro_loa` | `tts.speak`, `media_player.play_media` (URL, tệp, kho media) ra loa camera. |
| `number.dosen_pro_tang_mic` | Khuếch đại mic 0–30 dB trước khi vào Assist. |
| `switch.dosen_pro_tat_mic` | Tắt nghe (loa và màn hình vẫn chạy). |
| `select.dosen_pro_…` | Chọn pipeline Assist; độ nhạy nhận biết «nói xong». |
| `switch.dosen_pro_hien_hoi_thoai_tren_man_hinh` | Bật/tắt việc hiện hội thoại Assist trên màn hình. |
| `button.dosen_pro_bat_man_hinh`, `button.dosen_pro_tat_man_hinh` | Bật / tắt nguồn màn hình camera (nút «Nguồn điện màn hình» của app). Là **nút**, không phải công tắc: camera không báo ngược nguồn màn hình đang bật hay tắt. |
| `sensor.dosen_pro_muc_mic` | Mức tiếng mic (dBFS) đang đưa vào Assist. Nói gần camera mà số vẫn từ -60 trở xuống thì tăng «Tăng mic»; không có số = chưa có luồng mic. |
| `number.dosen_pro_thoi_gian_sang_man_hinh`, `number.dosen_pro_do_sang_man_hinh` | Thời gian màn hình sáng (0 = không bao giờ tắt) và độ sáng 1–100%. |
| `sensor.dosen_pro_noi_dung_man_hinh` | Màn hình đang chiếu gì: `off` (không chiếu gì từ HA), `video`, `image`, `text`. **Không phải nguồn màn hình**: màn camera có thể đang sáng dù cảm biến này là `off`. |

Dịch vụ `dosen_pro.screen_show` (nguồn chọn theo danh sách: `camera_entity`, `media`, hoặc gõ `source`), `screen_text`, `screen_release`, `screen_default`, `screen_off`, `screen_power`, `screen_sleep`, `speaker_play`, `speaker_stop`.

**Màn hình camera chỉ hiện hình khi đang sáng.** Mỗi lần mở phiên hiển thị, dịch vụ tự bật nguồn màn hình (lệnh `OPScreenSwitchControl`, như nút «Nguồn điện màn hình: Bật» của app; tắt tự động bằng `SCREEN_AUTO_WAKE=0`). `screen_sleep` đặt thời gian sáng (`seconds`, 0 = không bao giờ tắt) và độ sáng (`luminance`) của chính camera (cấu hình `Dev.ScreenWorkMode`).

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

## Màn hình: lớp sự kiện và màn hình mặc định

Màn hình camera hoạt động theo **lớp**:

- **Màn hình mặc định**: đặt ngay trên thiết bị bằng các thực thể (không cần vào hộp Cấu hình):
  `select.dosen_pro_man_hinh_mac_dinh` (loại: tắt, đồng hồ, ảnh, video lặp, camera trực tiếp), rồi nguồn:
  `select.dosen_pro_camera_mac_dinh` (chọn **theo danh sách camera trong HA**), `select.dosen_pro_anh_mac_dinh` (chọn **theo danh sách thực thể ảnh**),
  `text.dosen_pro_dia_chi_mac_dinh` (gõ địa chỉ video/ảnh/rtsp:// hoặc đường dẫn HA như `/local/anh.png`). Chọn loại chưa có nguồn thì báo lỗi rõ ràng
  và không đổi. Lưu lại, khởi động lại vẫn áp dụng. Đổi địa chỉ dịch vụ hoặc khoá API: ⋮ → **Cấu hình lại**.
- **Sự kiện** (dịch vụ `screen_show`, `screen_text` với `owner` và `ttl`): lớp **gần nhất đè lên**; hết video / hết `ttl` / `screen_release`
  thì màn hình **về lớp bên dưới**, hết lớp thì về màn hình mặc định. Cùng `owner` thì thay nhau, khác thì xếp chồng.
- **Assist** tự chiếu hội thoại lên màn (lớp `assist`, mặc định bật, tắt ở `switch.dosen_pro_hien_hoi_thoai_tren_man_hinh`).
  Việc chiếu video, camera, cuộc gọi Zalo… làm bằng **blueprint** (bên dưới), không cài sẵn trong tích hợp.

Mặc định `off` thì màn hình không bị giữ: dịch vụ chỉ mở phiên hiển thị khi có sự kiện, xong thì đóng. Mặc định khác `off` thì giữ một phiên mở liên tục.

## Blueprint (thư mục `blueprints/automation/dosen_pro/`)

Chép ba tệp vào `/config/blueprints/automation/dosen_pro/` (hoặc nhập qua đường dẫn raw của GitHub).

| Blueprint | Làm gì |
|---|---|
| `man_hinh_su_kien.yaml` | Có sự kiện (chuông cửa, phát hiện người…) thì chiếu video / camera trực tiếp / ảnh / chữ lên màn, giữ N giây rồi về mặc định. Camera chọn trong danh sách camera của HA, video/ảnh chọn trong kho media. |
| `man_hinh_cuoc_goi_zalo.yaml` | Cuộc gọi Zalo đến / đang gọi / đang nói chuyện hiện lên màn (tên người gọi, và hình video nếu điền địa chỉ hình); kết thúc thì gỡ. |
| `nut_goi_camera_zalo.yaml` | Bấm nút gọi trên camera: có cuộc gọi Zalo đến thì **nghe máy**, không thì **gọi Zalo đi** cho người đã chọn. |

**Nút gọi của camera:** camera chưa báo sự kiện nào qua mạng nội bộ khi bấm nút (đã đo, chỉ có báo phát hiện người), nên blueprint thứ ba
bắt sự kiện `dosen_pro_alarm` mang tên bạn điền (mặc định `LocalAlarm`, **chưa được xác nhận**). Mọi báo động camera đẩy tới đều thành sự kiện
`dosen_pro_alarm` và thực thể `event.dosen_pro_su_kien_camera`: bấm nút rồi xem sự kiện nào hiện ra để điền đúng tên.

## Giới hạn đã biết

- Loa và mic của camera là chất lượng điện thoại (G.711, 8 kHz): nhạc nghe như qua điện thoại.
- Màn hình 240×320, một phiên hình tại một thời điểm; hình có tiếng chưa hỗ trợ (hiện gửi âm thanh im lặng kèm hình; tiếng từ `speaker_play` đi qua kênh âm thanh của chính phiên màn hình khi màn đang mở).
- Nút gọi trên camera **không** sinh event qua đường LAN (đã đo): chưa bắt được sự kiện bấm nút.
- Chưa có tích hợp Assist (mic → STT) và cầu nối cuộc gọi Zalo: đã có đủ API (mic, loa, màn hình) để nối.
