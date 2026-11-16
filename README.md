<p align="center">
  <img src="custom_components/dosen_pro/brand/icon@2x.png" alt="Dosen Pro" width="128">
</p>

# Dosen Pro

Biến **màn hình, loa, mic và camera** của camera gọi video **DOSEN RPO V1** (app iCSee / XM, màn hình 2,8") thành một thiết bị
đa dụng cho Home Assistant. Mic chỉ phục vụ **cuộc gọi Zalo** (không có Assist / từ khoá đánh thức):

- **Màn hình**: đồng hồ đầy đủ (giờ lớn, âm lịch, Can Chi, ngày lễ sắp tới, thời tiết), ảnh, video, camera trực tiếp; chiếu
  **cuộc gọi Zalo (tên người gọi + hình video) tự động**, chuông cửa… theo **lớp sự kiện**, xong thì tự về màn hình mặc định.
  Nguồn trực tiếp (camera cửa, hình cuộc gọi) đi **đường độ trễ thấp**: bỏ khung cũ thay vì xếp hàng, nên hình trên màn gần như thời gian thật.
- **Loa** (`media_player`): phát TTS, nhạc, URL ra loa camera.
- **Camera** (`camera`): hình và tiếng của chính camera qua RTSP, dùng cho Frigate, go2rtc, cuộc gọi Zalo…
- **Bộ đàm** cho cuộc gọi Zalo: tiếng người bên kia ra loa camera theo thời gian thật.

**Chạy thẳng trong Home Assistant, không cần Docker hay dịch vụ nào khác** (HAOS, Container, Core đều được). Tích hợp nói chuyện
trực tiếp với camera qua giao thức DVRIP (cổng 34567), dùng ffmpeg và Pillow có sẵn của HA; phông DejaVu kèm theo trong tích hợp.
Đã thử trên Home Assistant 2026.11.

| Phần | Thư mục | Là gì |
|---|---|---|
| Tích hợp HA | `custom_components/dosen_pro/` | Thực thể, dịch vụ, camera, bộ đàm, màn cuộc gọi Zalo |
| Lõi DVRIP | `custom_components/dosen_pro/dosen_core/` | Thuần Python, không phụ thuộc HA: màn hình (AVTalk), loa, báo động, lịch âm, vẽ màn hình |
| Blueprint | `blueprints/automation/dosen_pro/` | Chiếu video, camera, cuộc gọi Zalo lên màn; nút gọi |
| Biểu tượng | `custom_components/dosen_pro/brand/` | Icon sáng/tối cho HA |

## Cài đặt

1. **HACS** → ⋮ → Kho tuỳ chỉnh → `https://github.com/TriTue2011/Dosen-pro` (loại *Tích hợp*) → cài **Dosen Pro**.
   Không dùng HACS: chép thư mục `custom_components/dosen_pro` vào `/config/custom_components/`.
2. **Khởi động lại Home Assistant.** (Cập nhật bản mới cũng vậy: *Tải lại* tích hợp **không** nạp mã Python mới, phải khởi động lại.)
3. Cài đặt → Thiết bị và dịch vụ → Thêm tích hợp → **Dosen Pro** → điền **IP camera, cổng (34567), tài khoản và mật khẩu camera**.

> **Nên tạo một tài khoản riêng cho Dosen** trên camera (bằng phần mềm CMS của hãng). Camera **khoá tài khoản** nếu bị nhập sai mật khẩu
> nhiều lần (Ret 205); tích hợp tự chặn đăng nhập dồn dập khi camera từ chối để không kéo dài việc khoá.
> Đổi IP, tài khoản, mật khẩu về sau: ⋮ → **Cấu hình lại**. Hộp **Cấu hình** có hai tuỳ chọn: bật/tắt luồng sự kiện camera và địa chỉ HA
> dùng cho các đường dẫn ký sẵn.

## Thực thể

Cài đặt **hiển thị của màn hình** (màn mặc định, thời tiết, xoay, chất lượng, luân phiên, thẻ thông tin, độ sáng…) **không còn là thực thể**: tất cả nằm trong **một thẻ «Màn hình Dosen»** (xem mục dưới). Thiết bị chỉ còn các thực thể điều khiển và theo dõi:

| Thực thể | Làm gì |
|---|---|
| `media_player.dosen_pro_loa` | `tts.speak`, `media_player.play_media` (URL, tệp, kho media) ra loa camera. |
| `camera.dosen_pro_camera` | Hình và tiếng của camera qua RTSP (luồng phụ 800×448 HEVC + tiếng G.711). |
| `number.dosen_pro_am_luong_loa_camera`, `number.dosen_pro_am_luong_mic_camera` | Âm lượng loa và mic **của camera** (cài đặt thật, app iCSee cũng thấy). |
| `switch.dosen_pro_chong_on_mic_camera` | Chống ồn của mic camera. |
| `switch.dosen_pro_tieng_bip_khi_nhan_nut` | Tiếng bíp khi bấm phím trên thân camera (`KeyAudioTip`). |
| `button.dosen_pro_bat_man_hinh`, `button.dosen_pro_tat_man_hinh` | Bật / tắt nguồn màn hình camera. |
| `button.dosen_pro_ve_man_hinh_mac_dinh` | Gỡ mọi lớp sự kiện về màn mặc định. |
| `sensor.dosen_pro_noi_dung_man_hinh` | Màn đang chiếu gì: `off`, `clock`, `image`, `video`, `live`, `text`. |
| `event.dosen_pro_su_kien_camera` | Mọi báo động camera đẩy tới (xem *Nút gọi*). |

## Thẻ «Màn hình Dosen»: toàn bộ cài đặt hiển thị ở một chỗ

Cài tích hợp là **tự có thẻ**, không phải thêm tài nguyên hay chép tệp JS:

- **Thanh bên → «Màn hình Dosen»** (chỉ quản trị viên), hoặc
- trong bảng điều khiển: *Thêm thẻ* → **Màn hình Dosen** (`type: custom:dosen-pro-card`).

Thẻ có bốn phần, và **bản xem trước bên trái vẽ đúng hình sẽ lên màn camera bằng số liệu thật của HA**:

| Phần | Làm được gì |
|---|---|
| **Màn hình mặc định** | Loại (tắt, đồng hồ, ảnh, video, camera trực tiếp), nguồn (chọn camera / ảnh theo danh sách thực thể, hoặc gõ địa chỉ), thời tiết trên đồng hồ, xoay hình, kiểu khung, chất lượng hình. |
| **Luân phiên các màn** | Bật/tắt, **bao nhiêu giây mỗi màn**, **thẻ nào tham gia** (tích ô vuông), đổi thứ tự (↑↓), thêm / sửa / xoá thẻ. |
| **Trình soạn thẻ** | Tên thẻ; thẻ thông tin: tiêu đề, **cảm biến chính** (số lớn), **các dòng** (chọn thực thể, đặt tên hiển thị, màu từng dòng, đổi vị trí ↑↓), **màu** số lớn và tiêu đề (bảng màu hoặc màu tuỳ ý), giao diện **sáng/tối**, bố cục **danh sách** hoặc **lưới 2 cột**; thẻ camera / ảnh / đồng hồ. Mọi thay đổi hiện ngay ở bản xem trước. |
| **Màn hình camera** | Độ sáng, thời gian màn sáng, **tự hiện cuộc gọi Zalo / Telegram lên màn (lọc theo ID người gọi)**, bật / tắt màn. |

«**Gửi thử lên màn (30 giây)**» hiện thẻ đang xem lên màn camera thật rồi tự về như cũ. Cài đặt được lưu, khởi động lại HA vẫn giữ.

Mọi thứ thẻ làm đều là API WebSocket `dosen_pro/*` (chỉ quản trị viên): `state`, `set`, `card_save`, `card_delete`, `card_move`, `rotation`, `preview`, `show_now`, `screen_info`, `screen_set`.
Các dịch vụ `screen_card`, `screen_carousel`… vẫn dùng được cho automation.

## Màn hình

Màn hình hoạt động theo **lớp**:

- **Màn hình mặc định** (đặt ngay trên thiết bị, nhớ qua khởi động lại): đồng hồ, ảnh, video lặp hoặc camera trực tiếp. Khi khởi động HA,
  nếu nguồn chưa nạp kịp (ví dụ camera go2rtc) thì tự thử lại vài lần.
- **Sự kiện** (`dosen_pro.screen_show`, `screen_text` với `owner` và `ttl`): lớp **gần nhất đè lên**; hết video / hết `ttl` /
  `screen_release` thì về lớp bên dưới, hết lớp thì về màn hình mặc định. Cùng `owner` thì thay nhau, khác thì xếp chồng.
  Lớp sự kiện luôn hiện nguyên trạng (không xoay).
- **Cuộc gọi Zalo và Telegram** tự chiếu (lớp `zalo_call` / `telegram_call` chữ + `zalo_call_video` / `telegram_call_video` hình). Telegram lấy hình từ telegram-gateway (`/api/live/view/<id>`, MJPEG, token gửi bằng header), hiện khi nghe máy cuộc gọi video hoặc khi người gọi bật camera giữa cuộc thoại. **Chỉ chiếu cuộc gọi của các ID đã chọn:** ô «Chỉ các ID» trên thẻ Dosen Pro (threadID Zalo, ID người dùng Telegram, cách nhau dấu phẩy); để trống = mọi người gọi. Chiếu video, camera… làm bằng dịch vụ hoặc **blueprint**; nhiều sự kiện cùng lúc xếp theo mức ưu tiên (mục dưới).

### Nhiều sự kiện cùng lúc: ưu tiên, chia đôi, xoay camera

Mỗi lớp có **mức ưu tiên** `priority` 1 thấp · **2 thường** (mặc định; cuộc gọi Zalo cũng mức này) · 3 cao · 4 khẩn cấp:

- Mức cao hơn **luôn ở trên**. Sự kiện mức thấp tới sau thì chờ bên dưới (thời gian giữ `ttl` của nó vẫn chạy, hết giờ thì mất).
- **Cùng mức thì lớp tới sau đè lên**, hết thì trả lại lớp trước. Ví dụ đang xem hình cuộc gọi Zalo, camera cửa kích hoạt: camera hiện luôn,
  hết giờ thì khôi phục cuộc gọi.
- Lớp luân phiên (carousel) luôn ở đáy (mức 0).
- **Camera `split: true`** cùng mức trên cùng thì **chia đôi màn** (trên / dưới, mỗi ô 240×160, tối đa 2 camera). Có hơn 2 camera thì
  **cứ 5 giây đổi một lượt** (kể cả khi `ttl` mỗi camera là 10 giây); camera hết `ttl` thì rơi khỏi vòng. Camera đơn lẻ vẫn chiếm cả màn.
- Một camera trong màn chia đôi chết: màn hiện riêng lớp trên cùng (lớp hỏng tự gỡ) rồi ghép lại phần còn lại, không dựng lại liên tục.
- Phần chia đôi **chỉ gộp các camera `split`**; hình cuộc gọi Zalo không bị gộp.

Màn hình camera **chỉ hiện hình khi đang sáng**: mỗi lần mở phiên hiển thị, tích hợp tự bật nguồn màn (lệnh `OPScreenSwitchControl`).
Camera chỉ nhận **một phiên hiển thị** một lúc (phiên thứ hai bị từ chối, Ret 503).

### Đồng hồ

Bố cục dọc 240×320, vẽ lại mỗi phút và mỗi khi thời tiết đổi:

- thứ, ngày, **giờ lớn** (dấu hai chấm đỏ); chữ đen trên nền sáng, điểm nhấn đỏ, cho dễ đọc trên màn nhỏ;
- **ngày lễ sắp tới** và «Còn N ngày» (hoặc «Hôm nay», «Ngày mai»): ngày lễ dương lịch và âm lịch của Việt Nam, kể cả Giao thừa;
- **năm Can Chi**, ngày/tháng **âm lịch** cỡ lớn màu đỏ (có đánh dấu tháng nhuận; dương lịch đã ở dòng trên cùng), **số tuần**;
- **thời tiết**: biểu tượng (đổi theo ngày/đêm), nhiệt độ, tình trạng tiếng Việt, độ ẩm, thấp–cao trong ngày (lấy từ dự báo hằng ngày của
  thực thể, tự đổi °F sang °C). Chọn thực thể ở `select.dosen_pro_thoi_tiet_tren_dong_ho`; dự báo làm mới mỗi 30 phút.

Âm lịch tính ngay trong tích hợp (thuật toán thiên văn, múi giờ UTC+7), **không cần mạng hay tích hợp lịch khác**; đã đối chiếu với Tết 2024–2026,
Trung thu và Giỗ Tổ 2026, tháng nhuận 2025.

### Xoay hình và chất lượng

Đặt ở phần **Màn hình mặc định** của thẻ:

- **Xoay hình / kiểu khung** chỉ áp cho **màn hình mặc định** (ảnh, video, camera trực tiếp): xoay 90° để hình ngang vừa màn dọc,
  «lấp đầy» để cắt rìa thay vì chừa viền đen.
- **Chất lượng hình**: *Chuẩn* (mặc định, x265 ultrafast), *Cao* (cùng công cụ, chi tiết hơn khi chuyển động), *Cao nhất* (preset
  chậm hơn, đẹp nhất). **Mọi mức đều giới hạn kích thước từng khung hình ở khoảng 7,5 KB**: camera chỉ nhận gói media cỡ vài KB (app iCSee
  thật không gửi khung nào quá 5,8 KB; đo trên camera này cho thấy hiện được tới khoảng 8,4 KB, **đen từ khoảng 9,7 KB**). Vì vậy
  các mức chỉ khác nhau ở độ nén (crf) và preset, **không** khác ở tốc độ bit. Ảnh đứng yên như đồng hồ: khung đầu hơi mờ rồi vài khung
  sau tự làm nét. Nếu màn đen hoặc chập chờn thì đặt lại «Chuẩn».

## Cuộc gọi Zalo bằng camera Dosen

Dosen cung cấp đủ ba thứ cho tích hợp **Zalo Calling**:

- **Camera + mic**: chọn `camera.dosen_pro_camera` (RTSP có cả hình và tiếng);
- **Loa**: chọn `media_player.dosen_pro_loa`; tiếng người bên kia đi qua bộ đàm `POST /api/dosen_pro/intercom/<entry_id>?k=<khoá>`
  (G.711 A-law 8 kHz theo thời gian thật). Khoá riêng của mỗi thiết bị tự sinh và nằm trong dữ liệu mục cấu hình. Trong lúc có
  cuộc gọi.
- **Hình từ điện thoại lên màn**: **tự động**. Khi tích hợp Zalo Call báo cuộc gọi đến / đang gọi, màn hiện tên người gọi; nghe máy thì
  hiện hình video của cuộc gọi (luồng `/api/call/video/view` của app Zalo, giống cửa sổ popup) bằng đường độ trễ thấp; kết thúc thì gỡ.
  Tên người gọi **cỡ lớn**, lấy từ danh bạ Zalo (tra theo mã người gọi qua tích hợp Zalo Call, không khai thêm gì); chưa có trong danh bạ thì hiện «Có người gọi», không bao giờ hiện dãy số. Cuộc gọi thoại chỉ hiện chữ. Tắt ở thẻ «Màn hình Dosen» → *Màn hình camera* → «Hiện cuộc gọi Zalo». Địa chỉ app Zalo lấy từ mục
  cấu hình của tích hợp Zalo Call, không cần khai thêm.

> Phía Zalo Calling cần bản có hỗ trợ Dosen làm mic/loa; bản đó chưa có trong kho Zalo Calling công khai tại thời điểm phát hành này.

## Tự chế thông tin trên màn: thẻ thông tin

> Cách dễ nhất: thêm thẻ thông tin trong **thẻ «Màn hình Dosen»** (chọn thực thể, đặt tên, màu, xem trước ngay). Phần dưới là cách dùng bằng dịch vụ trong automation.

Muốn hiện **điện năng lượng mặt trời, nhiệt độ các phòng, cảm biến cửa, pin…** lên màn camera? Dịch vụ `dosen_pro.screen_card` vẽ một **thẻ**
cùng kiểu với đồng hồ (chữ đen, số màu): tiêu đề, **một số lớn** và tối đa 6 dòng *nhãn – giá trị* (8 dòng nếu không có số lớn).
Chỉ cần khai dữ liệu, không phải tự vẽ ảnh. Chọn trực tiếp **thực thể của Home Assistant**: tích hợp tự lấy tên, trạng thái, đơn vị
(số được làm tròn kiểu Việt Nam, `3,5 kW`; thực thể mất kết nối hiện «—»).

| Trường | Ý nghĩa |
|---|---|
| `title` | Tiêu đề (tự thu nhỏ nếu dài). |
| `main_entity` | Cảm biến chính: giá trị thành **số lớn**, tên thành nhãn dưới số. Hoặc tự điền `value` / `caption`. |
| `color` | Màu số lớn: `red` (mặc định), `green`, `blue`, `orange`, `black`. |
| `entities` | Danh sách thực thể, mỗi cái một dòng (tên + giá trị kèm đơn vị). Mỗi mục là `entity_id` hoặc `{entity: ..., name: "Phòng ngủ"}` để tự đặt tên hiển thị. Cảm biến bật/tắt được dịch ra chữ Việt và tô màu: hiện diện «Có người» (xanh lá) / «Trống», cửa «Mở» (cam) / «Đóng», khoá, khói, pin yếu… |
| `rows` | Dòng tự khai: `"Hôm nay: 18,6 kWh"` hoặc `{label, value, color}` (màu từng dòng). Đứng trước các dòng của `entities`. |
| `owner`, `ttl`, `after` | Như `screen_text`: lớp theo `owner` thay nhau; hết `ttl` về màn mặc định. |

```yaml
# Điện năng lượng mặt trời
- action: dosen_pro.screen_card
  data:
    title: "Điện năng lượng mặt trời"
    main_entity: sensor.cong_suat_mat_troi        # số lớn: 3,4 kW
    color: green
    entities:                                      # mỗi thực thể một dòng
      - sensor.san_luong_hom_nay
      - sensor.dien_tieu_thu_nha
      - sensor.pin_luu_tru
    ttl: 60

# Nhiệt độ các phòng, có dòng tự khai
- action: dosen_pro.screen_card
  data:
    title: "Nhiệt độ"
    color: orange
    main_entity: sensor.nhiet_do_phong_khach
    entities: [sensor.nhiet_do_phong_ngu, sensor.nhiet_do_bep, sensor.do_am_phong_khach]
    rows:
      - "Ngoài trời: {{ states('sensor.nhiet_do_ngoai_troi') }} °C"   # mẫu Jinja dùng bình thường
      - {label: "Cảnh báo", value: "Nóng", color: red}
```

Không muốn viết automation: dùng blueprint **`man_hinh_the_thong_tin.yaml`**, chọn cảm biến chính và các dòng theo danh sách, rồi thêm điều kiện
«Thời gian: mỗi phút» (để thẻ luôn mới) hoặc bấm nút / có người ở gần (để hiện). Mỗi thẻ một automation với một «Tên lớp» riêng.

Người khác muốn đóng góp thẻ mới (giá vàng, thời gian đi làm, lịch trực…) chỉ cần viết một blueprint hoặc automation gọi `screen_card` và gửi vào
thư mục `blueprints/automation/dosen_pro/`; không phải sửa mã Python. Muốn bố cục hoàn toàn riêng thì tự tạo ảnh 240×320 và dùng `screen_show`.

## Màn luân phiên: tự đổi giữa nhiều màn

> Cách dễ nhất: **thẻ «Màn hình Dosen» → Luân phiên các màn**: tích thẻ nào tham gia, đặt số giây, đổi thứ tự, bật. Phần dưới là cách dùng bằng dịch vụ.

Muốn màn camera **tự đổi lần lượt** giữa vài màn (ví dụ đồng hồ → điện mặt trời → camera cửa)? Dùng dịch vụ `dosen_pro.screen_carousel`:
khai danh sách **trang** và số giây mỗi trang, rồi bật. Bật/tắt về sau ở thẻ «Màn hình Dosen»; danh sách thẻ và
trạng thái bật được **lưu**, khởi động lại HA vẫn chạy tiếp.

```yaml
- action: dosen_pro.screen_carousel
  data:
    seconds: 20                       # thời lượng mặc định mỗi trang (3–3600 giây); đổi sau ở thẻ «Màn hình Dosen»
    pages:
      - {type: clock, seconds: 20}    # đồng hồ đầy đủ (tự cập nhật khi sang phút mới)
      - type: card                    # thẻ thông tin: cùng trường với screen_card
        title: "Điện năng lượng mặt trời"
        main_entity: sensor.cong_suat_mat_troi
        color: green
        entities: [sensor.san_luong_hom_nay, sensor.dien_tieu_thu_nha]
      - {type: camera, camera_entity: camera.cua, seconds: 8}     # camera trực tiếp
```

Tắt: `dosen_pro.screen_carousel` với `enabled: false` (hoặc tắt công tắc): lớp luân phiên được gỡ và màn về màn hình mặc định.

| Loại trang | Trường |
|---|---|
| `clock` | (không có) đồng hồ đầy đủ như màn mặc định |
| `card` | `title`, `main_entity`, `value`, `caption`, `color`, `entities`, `rows` (như `screen_card`) |
| `text` | `title`, `messages`, `font_size` (như `screen_text`) |
| `image` | `source`, `media` hoặc `image_entity` |
| `video` | `source` hoặc `media`, `loop` (mặc định lặp) |
| `camera` | `camera_entity` hoặc `source` (rtsp://…) |

Thời lượng mặc định mỗi màn là **20 giây**, chỉnh ở thẻ «Màn hình Dosen» (áp dụng từ màn kế tiếp, được nhớ qua khởi động lại). Trang nào có `seconds` riêng thì giữ riêng. Tối đa 12 trang. Cách hoạt động:

- Mỗi trang là một **lớp** của chủ `carousel`, nằm **dưới** mọi lớp sự kiện: cuộc gọi Zalo, chuông cửa, hội thoại Assist… vẫn đè lên;
  hết sự kiện thì trang đang tới lượt hiện lại.
- **Thẻ thông tin lấy số liệu mới mỗi lần hiện** và được vẽ lại ngay khi số liệu đổi trong lúc trang đang hiện; đồng hồ vẽ lại khi sang phút mới.
  Không vẽ lại khi nội dung giống hệt, để màn không chập chờn.
- Nếu HA chết giữa chừng, lớp luân phiên tự hết hạn (gấp 3 lần thời lượng trang) và màn về màn hình mặc định.

## Dịch vụ

`dosen_pro.screen_show` (nguồn: `camera_entity`, `media`, hoặc gõ `source`), `screen_text`, `screen_card`, `screen_carousel`, `screen_release`, `screen_default`,
`screen_off`, `screen_power`, `screen_sleep`. `screen_show`, `screen_text`, `screen_card` nhận thêm `priority` (1–4); `screen_show` nhận `split` (chia đôi màn cho camera) và `live`. Loa dùng `media_player.dosen_pro_loa` (`tts.speak`, `media_player.play_media`). Xem mô tả từng trường trong *Công cụ nhà phát triển → Dịch vụ*.

```yaml
# Chuông cửa: hình camera cửa lên màn gần thời gian thật (live: true), kèm tiếng chuông
- action: dosen_pro.screen_show
  data: {camera_entity: camera.cua, owner: chuong_cua, ttl: 30, live: true}
- action: media_player.play_media
  target: {entity_id: media_player.dosen_pro_loa}
  data: {media_content_id: "http://homeassistant.local:8123/local/chuong.mp3", media_content_type: music}

# Hiện hội thoại
- action: dosen_pro.screen_text
  data:
    title: "Trợ lý"
    messages:
      - {role: user, text: "Bật đèn phòng khách"}
      - {role: assistant, text: "Đã bật đèn."}

# TTS ra loa camera
- action: tts.speak
  target: {entity_id: tts.piper}
  data: {media_player_entity_id: media_player.dosen_pro_loa, message: "Có khách ở cửa"}
```

## Blueprint

Chép các tệp trong `blueprints/automation/dosen_pro/` vào `/config/blueprints/automation/dosen_pro/` (hoặc nhập bằng đường dẫn raw).

| Blueprint | Làm gì |
|---|---|
| `man_hinh_nhieu_viec.yaml` | **MỘT automation cho mọi sự kiện, thêm bao nhiêu việc cũng được** (không có danh sách làm sẵn). Trigger nào cũng được, đặt ID cho từng trigger; mỗi việc chọn trigger ID nào kích hoạt, tuỳ chọn «chỉ khi thực thể ở trạng thái», rồi chiếu camera (nhiều camera chia đôi màn) / hình / video / chữ với mức ưu tiên, thời gian giữ, tên lớp. Khi nhiều việc cùng khớp: chạy tất cả, việc đứng đầu, hoặc việc có cảm biến điều kiện đổi gần nhất. Thay cho hai blueprint cũ «chiếu khi có sự kiện» và «4 ô sự kiện» (đã xoá). |
| `nut_goi_camera_zalo.yaml` | Bấm nút gọi: có cuộc gọi Zalo đến thì **nghe máy**, không thì **gọi Zalo đi** cho người đã chọn. |
| `man_hinh_the_thong_tin.yaml` | Thẻ thông tin (số lớn + các dòng) từ thực thể HA: điện, nhiệt độ, cảm biến… |

## Nút gọi trên camera

Camera **tự xử lý** nút gọi (hiện màn gọi riêng của phần mềm camera) và, theo các lần đo, **không báo sự kiện nào qua mạng nội bộ**:
kênh báo động chỉ trả sự kiện phát hiện người. Để dò tiếp, mọi gói tin lạ camera đẩy ra (trên kênh báo động và hai kết nối của phiên màn hình)
đều hiện thành sự kiện `opcode_<số>` ở `event.dosen_pro_su_kien_camera` và trên bus HA dưới tên `dosen_pro_alarm`. Bấm nút rồi xem sự kiện nào
hiện ra; blueprint `nut_goi_camera_zalo` bắt sự kiện theo **tên bạn điền** (mặc định `LocalAlarm`, chưa được xác nhận trên camera này).
Các cấu hình tắt chuông/tiếng báo của firmware (`Consumer.DevRingControl`, `Alarm.LocalTipSwitch`…) camera này không hỗ trợ (Ret 607);
công tắc «Tiếng bíp khi nhấn nút» chỉ điều khiển `KeyAudioTip`.

## Gỡ lỗi

| Triệu chứng | Cách xử lý |
|---|---|
| Màn **đen / chập chờn** | Nguyên nhân thường gặp là khung hình quá lớn (≥ ~9,7 KB). Đặt «Chất lượng hình trên màn» về **Chuẩn**; kiểm `sensor.dosen_pro_noi_dung_man_hinh` có `last_error`; đừng tự đổi bộ mã hoá. |
| Không thấy thẻ **«Màn hình Dosen»** (thanh bên hoặc danh sách thẻ) | Cần **khởi động lại HA** sau khi cài/cập nhật, rồi làm mới cứng trình duyệt (Ctrl+F5). Thẻ chỉ dành cho quản trị viên. |
| Không thấy **icon** | Icon nằm ở `custom_components/dosen_pro/brand/` (cần HA 2026.3 trở lên): **khởi động lại HA** rồi làm mới cứng trình duyệt (Ctrl+F5) vì giao diện giữ biểu tượng mặc định trong bộ nhớ đệm. HACS lấy biểu tượng từ kho riêng của nó nên danh sách HACS có thể vẫn hiện biểu tượng mặc định. |
| Log «camera refused AVTalk claim: Ret=503» | Camera còn giữ phiên hiển thị cũ hoặc app khác đang mở màn; tự thử lại, thường hết sau vài giây. |
| Đăng nhập báo **Ret 205 / tài khoản bị khoá** | Đợi camera mở khoá (hoặc khởi động lại camera), kiểm đúng tên tài khoản; dùng tài khoản riêng. |
| Vừa cập nhật mà không thấy thực thể mới | **Khởi động lại HA** (không chỉ *Tải lại*). |
| Màn hiện camera mặc định, rút nguồn cắm lại thì log báo `401 Unauthorized` mãi | Đã sửa: luồng camera của HA có token đổi mỗi 5 phút, nên mỗi lần ffmpeg chạy lại tích hợp xin địa chỉ mới (trước đây dùng địa chỉ lưu từ lúc đặt, phải khởi động lại HA mới hết). |
| Rút nguồn / cắm lại camera mà màn không về | Tích hợp tự phát hiện kết nối chết trong ~10 giây và thử lại 3 → 30 giây một lần; nếu vẫn kẹt, log có «watchdog» kèm dấu vết luồng — gửi đoạn đó. |

## Giới hạn đã biết

- Loa và mic của camera chất lượng điện thoại (G.711, 8 kHz): nhạc nghe như qua điện thoại.
- Màn hình 240×320, một phiên hiển thị một lúc; hình video chưa kèm tiếng (tiếng ra loa qua `media_player` hoặc bộ đàm).
- Nút gọi trên camera chưa bắt được sự kiện qua mạng nội bộ (xem trên).
- Camera chỉ nhận một số phiên DVRIP đồng thời; tích hợp dùng vài kết nối (báo động, màn hình, loa).

## Phát triển và kiểm thử

```bash
pip install pytest Pillow
python -m pytest tests/core -q -p no:asyncio          # lõi DVRIP với camera giả (cần ffmpeg có libx265)

pip install pytest-homeassistant-custom-component
python -m pytest tests/ha -q                          # tích hợp HA
```

Lõi (`dosen_core`) không import Home Assistant nên chạy được độc lập. Phông DejaVu (giấy phép trong `dosen_core/fonts/LICENSE-DejaVu.txt`).
Tích hợp được làm theo khuôn của [ha-camera-talk](https://github.com/TriTue2011/ha-camera-talk).
