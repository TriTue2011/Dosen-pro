"""The camera screen as a display: one AVTalk session, swappable sources.

``Screen.show()`` keeps the AVTalk session open and swaps what feeds it: a
video file/URL, an RTSP camera, an image, or rendered text (app/render.py).
Each source is one ffmpeg process encoding H.265 for the LCD; switching
starts a new process and the sender waits for its first key frame. When a
source ends on its own the screen either closes (the camera returns to its
idle screen) or holds the last frame, per ``after``.
"""

import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from datetime import datetime

from . import avtalk, render
from .dvrip import DVRIPClient, DVRIPError
from .util import apply_decoder_pram, reap_async, redact_source

log = logging.getLogger("icsee.screen")

_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
_DEFAULT_MODES = ("off", "clock", "image", "video", "live")


#: Bộ canh giữ: một lần ``_apply`` (mở phiên, khởi động ffmpeg…) kéo dài quá ngần này giây coi như KẸT → ghi ngăn xếp luồng và đóng thô kết nối.
WATCHDOG_STUCK_S = 25.0
WATCHDOG_EVERY_S = 3.0
RETRY_FIRST_S, RETRY_MAX_S = 3.0, 30.0                 # lần thử lại sau lỗi: 3 s rồi gấp đôi tới 30 s (cắm lại nguồn là lên nhanh)
CAROUSEL_OWNER = "carousel"              # lớp của màn luân phiên: luôn nằm dưới các lớp sự kiện khác
PRIORITY_DEFAULT = 2                     # 1 thấp · 2 thường (cuộc gọi Zalo, sự kiện mặc định) · 3 cao · 4 khẩn. Lớp ưu tiên cao hơn LUÔN ở trên; cùng mức thì lớp tới sau đè lên.
PRIORITIES = (1, 2, 3, 4)
SPLIT_SLOTS = 2                          # màn chia đôi trên/dưới: tối đa hai camera cùng lúc
SPLIT_ROTATE_S = 5.0                     # có hơn hai camera chờ: cứ 5 s đổi một lượt (kể cả khi thời gian giữ của mỗi camera dài hơn)
ROTATIONS = (0, 90, 180, 270)            # độ xoay hình theo chiều kim đồng hồ
FITS = ("contain", "cover")              # contain = vừa khung (có viền đen); cover = lấp đầy màn (cắt rìa)
_ROTATE_FILTER = {0: "", 90: "transpose=1,", 180: "hflip,vflip,", 270: "transpose=2,"}


def check_view(rotate, fit) -> None:
    """None = dùng mặc định của màn hình; còn lại phải là giá trị hợp lệ."""
    if rotate is not None and (isinstance(rotate, bool) or rotate not in ROTATIONS):
        raise ValueError("rotate must be one of 0, 90, 180, 270")
    if fit is not None and fit not in FITS:
        raise ValueError("fit must be 'contain' or 'cover'")


#: Mỗi KHUNG HÌNH phải nhỏ: camera chỉ nhận gói media tới cỡ vài KB. App iCSee thật không bao giờ gửi khung nào lớn hơn 5,8 KB và
#: không chia nhỏ khung (đo trên bản chụp mạng); camera này vẫn hiện tới khoảng 8,4 KB nhưng ĐEN từ khoảng 9,7 KB trở lên (đo 15/11:
#: đồng hồ khung khoá 19,8 KB và mức «Cao» cũ 9,6–13 KB đều đen; chữ 4–5 KB và live 6–8 KB hiện tốt). Muốn khung nhỏ phải giữ CẢ tốc độ bit
#: tối đa thấp (400 kbps) lẫn bộ đệm VBV nhỏ (~40–48 kbit): tăng tốc độ bit lên 1500 làm khung phình tới 13–28 KB (đo). Nên các mức chất
#: lượng chỉ khác nhau ở crf/preset, không khác ở tốc độ bit. Ảnh đứng yên thì khung khoá đầu hơi mờ rồi các khung sau tự làm nét.
#:
#: «standard»: x265 ultrafast (khối tối đa 32, không SAO) như bộ đã chạy tốt trên camera. «high»: cùng công cụ, crf 20 (chi tiết hơn khi
#: có chuyển động). «max»: preset slow (khối 64, SAO, rdoq…) crf 18 — đẹp nhất nhưng chip giải mã của camera có thể không đọc được
#: (chưa kiểm trên camera thật) nên chỉ để thử. Test đo kích thước từng khung cho cả ba mức (≤ 8000 B).
QUALITIES = ("standard", "high", "max")
_QUALITY = {
    "standard": {"preset": "ultrafast", "crf": None, "vbv": 400, "buf": 48, "gop_seconds": 4},
    "high": {"preset": "ultrafast", "crf": 20, "vbv": 400, "buf": 48, "gop_seconds": 4},
    "max": {"preset": "slow", "crf": 18, "vbv": 400, "buf": 40, "gop_seconds": 4},
}


def check_quality(quality) -> None:
    if quality not in QUALITIES:
        raise ValueError("quality must be one of " + ", ".join(QUALITIES))


LIVE_GOP_SECONDS = 1                      # nguồn trực tiếp: khung khoá mỗi 1 s để bỏ khung cũ rồi nhảy tới hình mới nhanh (≤ 1 s)


def is_live_source(source: str) -> bool:
    """Nguồn chạy theo thời gian thật (camera RTSP, luồng MJPEG/HTTP trực tiếp): hiện càng gần «bây giờ» càng tốt, không giả lập tốc độ."""
    low = source.lower().split("?")[0]
    return low.startswith("rtsp://") or low.startswith("rtsps://") or low.endswith((".mjpg", ".mjpeg", "/video/view", "/mjpeg", "/stream.mjpg"))


def _input_args(source: str, fps: int, live: bool, loop: bool) -> list[str]:
    """Tuỳ chọn đầu vào ffmpeg cho một nguồn, kết thúc bằng ``-i <nguồn>``."""
    cmd: list[str] = []
    image = source.lower().split("?")[0].endswith(_IMAGE_EXT)
    if source.startswith("rtsp://"):
        cmd += ["-rtsp_transport", "tcp", "-timeout", "5000000"]
    if live:
        # Nguồn trực tiếp: không «-re» (không tự làm chậm), không đệm đầu vào, dò luồng tối thiểu → hình lên sớm và không tụt lại sau thời gian thật.
        cmd += ["-fflags", "nobuffer+discardcorrupt", "-flags", "low_delay", "-probesize", "32768", "-analyzeduration", "0"]
        if not source.startswith("rtsp://"):
            cmd += ["-use_wallclock_as_timestamps", "1"]     # MJPEG qua HTTP không có dấu thời gian: dùng giờ nhận
    elif not source.startswith("rtsp://"):
        cmd += ["-re"]
        if image:
            cmd += ["-loop", "1", "-framerate", str(fps)]
        elif loop:
            cmd += ["-stream_loop", "-1"]
    if source.startswith("lavfi:"):
        cmd += ["-f", "lavfi"]
        source = source[len("lavfi:"):]
    return cmd + ["-i", source]


def _encode_args(quality: str, fps: int, live: bool) -> list[str]:
    q = _QUALITY[quality]
    gop = fps * (LIVE_GOP_SECONDS if live else q["gop_seconds"])
    params = (("crf=%d:" % q["crf"]) if q["crf"] else "") + (
        f"keyint={gop}:min-keyint={gop}:scenecut=0:bframes=0:slices=1:repeat-headers=1:aud=0:log-level=none:"
        f"vbv-maxrate={q['vbv']}:vbv-bufsize={q['buf']}")
    return ["-pix_fmt", "yuv420p", "-c:v", "libx265", "-preset", q["preset"],
            "-tune", "zerolatency", "-x265-params", params, "-f", "hevc", "-"]


def _fit_filter(width: int, height: int, fit: str) -> str:
    if fit == "cover":
        return f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,crop={width}:{height}"
    return (f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2")


def build_hevc_cmd(source: str, width: int, height: int, fps: int,
                   loop: bool = False, ffmpeg: str = "ffmpeg", rotate: int = 0, fit: str = "contain",
                   quality: str = "standard", live: bool | None = None) -> list[str]:
    """ffmpeg command: any source -> rotated, fitted H.265 Annex-B on stdout (xem ``_QUALITY`` về các mức chất lượng)."""
    check_view(rotate, fit)
    check_quality(quality)
    live = is_live_source(source) if live is None else live
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    cmd += _input_args(source, fps, live, loop)
    cmd += ["-an", "-vf", f"{_ROTATE_FILTER[rotate]}{_fit_filter(width, height, fit)},fps={fps}"]
    return cmd + _encode_args(quality, fps, live)


def build_split_cmd(sources: list[str], width: int, height: int, fps: int, ffmpeg: str = "ffmpeg",
                    quality: str = "standard") -> list[str]:
    """Chia màn theo chiều dọc: mỗi nguồn trực tiếp một ô cao ``height // n``, xếp trên → dưới, MỘT bộ mã hoá cho cả màn.
    ``shortest=1``: một nguồn chết thì ffmpeg thoát (màn không đứng hình một nửa)."""
    check_quality(quality)
    n = len(sources)
    if not 2 <= n <= SPLIT_SLOTS:
        raise ValueError(f"chia màn cần 2..{SPLIT_SLOTS} nguồn")
    cell = height // n
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    for src in sources:
        cmd += _input_args(src, fps, True, False)
    cells = ";".join(f"[{k}:v]{_fit_filter(width, cell, 'contain')},setsar=1,fps={fps}[c{k}]" for k in range(n))
    stack = "".join(f"[c{k}]" for k in range(n)) + f"vstack=inputs={n}:shortest=1[v]"
    cmd += ["-an", "-filter_complex", f"{cells};{stack}", "-map", "[v]"]
    return cmd + _encode_args(quality, fps, True)


class Screen:
    def __init__(self, cfg, now=None, on_other=None):
        self.cfg = cfg
        self._on_other = on_other
        self._now = now or (lambda: datetime.now().astimezone())   # giờ địa phương (trong HA: múi giờ của HA)
        self._lock = threading.RLock()
        self._session: avtalk.AVTalkSession | None = None
        self._sender: avtalk.AVTalkSender | None = None
        self._proc: subprocess.Popen | None = None
        self._token = 0                       # bumps on every source switch
        self.width, self.height, self.fps = 240, 320, 10
        self.mode = "off"                     # what is on the LCD now: off | video | image | text | clock | live
        self.source = ""
        self.started_at: float | None = None
        self.last_error: str | None = None
        self.switches = 0
        self._tmpdir = tempfile.mkdtemp(prefix="icsee-screen-")
        # Layers = events, the newest on top wins; when a layer ends the one below (or the default) shows.
        self._layers: list[dict] = []
        self._seq = 0                                  # thứ tự tới của lớp: cùng mức ưu tiên thì lớp tới sau ở trên
        self._group_ids: tuple = ()                    # nhóm camera đang chia màn (seq từng lớp)
        self._group_size = 0
        self._rot, self._rot_at = 0, 0.0               # vòng đổi camera khi có hơn SPLIT_SLOTS camera chờ
        self._split_block: frozenset | None = None     # nhóm đã làm ffmpeg thoát: hiện riêng lớp trên cùng, không ghép lại nhóm đó
        self.default: dict = {"mode": "off", "source": ""}     # off | clock | image | video | live
        self.view: dict = {"rotate": 0, "fit": "contain"}      # xoay + kiểu khung của màn hình mặc định
        self.quality = "standard"                              # mức chất lượng mã hoá (áp cho mọi lớp)
        self._cur_key: tuple | None = None
        self._cur_layer: dict | None = None
        self._retry_at = 0.0
        self._clock_sig: tuple | None = None          # (phút, thời tiết) của lần vẽ đồng hồ gần nhất
        self.weather: dict | None = None              # thời tiết hiện trên đồng hồ (HA đẩy xuống), None = không có
        self._ticker: threading.Thread | None = None
        self._watchdog: threading.Thread | None = None
        self._busy: tuple[int, float] | None = None     # (luồng, lúc bắt đầu) của lần _apply đang chạy
        self._opening_session: avtalk.AVTalkSession | None = None
        self._negotiating: DVRIPClient | None = None
        self.aborts = 0
        self._stopping = False

    # -- session ---------------------------------------------------------
    def _negotiate(self) -> None:
        """Take the LCD size/fps from the camera's DecoderPram (best effort)."""
        client = DVRIPClient(self.cfg.cam_ip, self.cfg.cam_port,
                             self.cfg.dvrip_connect_timeout,
                             self.cfg.dvrip_read_timeout)
        self._negotiating = client
        try:
            client.connect()
            client.login(self.cfg.cam_user, self.cfg.cam_pass)
            got = apply_decoder_pram(client.query_decoder_pram())
        except (OSError, DVRIPError):
            return
        finally:
            self._negotiating = None
            client.close()
        if got["negotiated"] and got["codec"] == "H265":
            self.width, self.height, self.fps = (
                got["width"] - got["width"] % 8, got["height"] - got["height"] % 8,
                got["fps"])

    def _ensure_open(self) -> None:
        if self._session is not None and self._sender is not None \
                and self._sender.running:
            return
        self._close_session()
        if not self.cfg.cam_ip:
            raise DVRIPError("ICSEE_CAM_IP is not set")
        self._negotiate()
        session = avtalk.AVTalkSession(
            self.cfg.cam_ip, self.cfg.cam_port, self.cfg.cam_user,
            self.cfg.cam_pass, self.cfg.dvrip_connect_timeout,
            self.cfg.dvrip_read_timeout, self._on_other)
        self._opening_session = session
        try:
            session.open(self.width, self.height, self.fps)
        finally:
            self._opening_session = None
        if self.cfg.screen_auto_wake and session.control is not None:
            # The LCD ignores video while it sleeps: switch it on, like the app's "Bật" button.
            try:
                session.control.screen_power_cmd(True)
            except OSError as exc:
                log.warning("man hinh: khong gui duoc lenh bat man hinh: %s", exc)
        sender = avtalk.AVTalkSender(session, self.width, self.height, self.fps)
        sender.start()
        self._session, self._sender = session, sender
        self.started_at = time.time()
        log.info("man hinh: mo phien AVTalk %dx%d@%d", self.width,
                 self.height, self.fps)

    def _close_session(self) -> None:
        if self._sender is not None:
            self._sender.stop()
        if self._session is not None:
            self._session.close()
        self._sender = self._session = None

    # -- how long the LCD stays lit (the app's "breath screen" setting) ---------
    _SLEEP_KEY = "Dev.ScreenWorkMode"

    def _control(self) -> DVRIPClient:
        client = DVRIPClient(self.cfg.cam_ip, self.cfg.cam_port,
                             self.cfg.dvrip_connect_timeout, self.cfg.dvrip_read_timeout)
        try:
            client.connect()
            client.login(self.cfg.cam_user, self.cfg.cam_pass)
        except Exception:
            client.close()
            raise
        return client

    def get_sleep(self) -> dict:
        """The camera's own LCD timeout: work_time seconds the screen stays lit, 0 = never sleeps."""
        client = self._control()
        try:
            cfg = client.get_config(self._SLEEP_KEY).get(self._SLEEP_KEY) or {}
        finally:
            client.close()
        return {"work_mode": cfg.get("WorkMode"), "work_time": cfg.get("WorkTime"),
                "luminance": cfg.get("WorkLuminance")}

    def set_sleep(self, seconds: int | None = None, luminance: int | None = None) -> dict:
        """Set the LCD timeout (``seconds``, 0 = never sleeps) and/or brightness (``luminance``
        1..100). Like the app, send all three fields of Dev.ScreenWorkMode back together: the
        current values are read first so the field not being changed stays as it is."""
        if seconds is None and luminance is None:
            raise ValueError("give seconds and/or luminance")
        if seconds is not None and (not isinstance(seconds, int) or isinstance(seconds, bool)
                                    or not 0 <= seconds <= 3600):
            raise ValueError("seconds must be an integer 0..3600 (0 = never sleeps)")
        if luminance is not None and (not isinstance(luminance, int) or isinstance(luminance, bool)
                                      or not 1 <= luminance <= 100):
            raise ValueError("luminance must be an integer 1..100")
        client = self._control()
        try:
            cur = client.get_config(self._SLEEP_KEY).get(self._SLEEP_KEY) or {}
            new = {"WorkMode": cur.get("WorkMode") or "Timing",
                   "WorkTime": cur.get("WorkTime", 0) if seconds is None else seconds,
                   "WorkLuminance": cur.get("WorkLuminance", 100) if luminance is None else luminance}
            client.set_config(self._SLEEP_KEY, new)
        finally:
            client.close()
        log.info("man hinh: dat thoi gian sang=%s do sang=%s", seconds, luminance)
        return self.get_sleep()

    def power(self, on: bool) -> dict:
        """Switch the camera LCD on or off (the app's "Nguồn điện màn hình" Bật / Tắt)."""
        if not isinstance(on, bool):
            raise ValueError("on must be true or false")
        client = self._control()
        try:
            client._write_json_cmd(1450, {
                "Name": "OPScreenSwitchControl", "SessionID": "0x%08X" % client.session,
                "OPScreenSwitchControl": {"ScreenStatus": on}})
            reply = client.read_json()
        finally:
            client.close()
        log.info("man hinh: nguon man hinh %s", "bat" if on else "tat")
        return {"screen_power": on, "ret": reply.get("Ret")}

    def audio_sink(self):
        """The running AVTalk sender (it has ``push_audio``), or None when the screen is off."""
        sender = self._sender
        return sender if sender is not None and sender.running else None

    # -- layers: events on top, the default below --------------------------------
    @staticmethod
    def _kind(source: str) -> str:
        return "image" if source.lower().split("?")[0].endswith(_IMAGE_EXT) else "video"

    def load(self) -> None:
        """Start the once-a-second background ticker (call once). The default screen itself is kept by the
        caller (Home Assistant entities restore it) and applied with ``set_default``."""
        if self._ticker is None:
            self._ticker = threading.Thread(target=self._tick, daemon=True, name="screen-tick")
            self._ticker.start()
            self._watchdog = threading.Thread(target=self._watch, daemon=True, name="screen-watch")
            self._watchdog.start()

    def close(self) -> None:
        """Stop everything: end every layer, drop the default and close the AVTalk session."""
        with self._lock:
            self._stopping = True
            self._layers = []
            self.default = {"mode": "off", "source": ""}
            self._stop_encoder()
            self._close_session()
            self.mode, self.source = "off", ""

    def _clock_signature(self, now) -> tuple:
        """Đồng hồ cần vẽ lại khi sang phút mới hoặc khi thời tiết đổi."""
        w = tuple(sorted(self.weather.items())) if self.weather else None
        return now.strftime("%H%M"), w

    def set_weather(self, weather: dict | None) -> dict:
        """Thời tiết cho đồng hồ: ``{"condition", "temp", "humidity", "high", "low"}`` hoặc None để bỏ."""
        if weather is not None and (not isinstance(weather, dict) or "condition" not in weather):
            raise ValueError("weather must be a dict with 'condition' (or None)")
        with self._lock:
            if weather == self.weather:
                return self.status()
            self.weather = dict(weather) if weather else None
            if self.default["mode"] == "clock" and not self._layers and self._sender is not None:
                self._cur_key = None                  # đang hiện đồng hồ: vẽ lại ngay
                self._apply()
        return self.status()

    def _default_layer(self) -> dict | None:
        mode, src = self.default["mode"], self.default["source"]
        if mode == "off":
            return None
        if mode == "clock":
            png = os.path.join(self._tmpdir, "clock.png")
            now = self._now()
            with open(png, "wb") as f:
                f.write(render.render_clock(now, self.weather))
            self._clock_sig = self._clock_signature(now)
            return {"owner": "default", "source": png, "mode": "clock", "loop": False, "after": "hold"}
        if not src:
            return None
        return {"owner": "default", "source": src, "mode": mode, "loop": mode == "video",
                "after": "off", "refresh": self.default.get("refresh")}

    def _view_of(self, layer: dict) -> tuple[int, str]:
        """Xoay + kiểu khung: CHỈ áp cho màn hình mặc định (ảnh, video, live). Lớp sự kiện (cuộc gọi, chuông cửa,
        Assist…) luôn hiện nguyên trạng; chữ và đồng hồ đã vẽ sẵn đúng chiều màn."""
        if layer["owner"] != "default" or layer["mode"] in ("text", "clock"):
            return 0, "contain"
        return self.view["rotate"], self.view["fit"]

    def _target(self) -> dict | None:
        """Cái đang phải hiện: lớp (ưu tiên, thứ tự tới) cao nhất; mặc định khi hết lớp. Các camera ``split`` liền nhau ở cùng mức trên cùng
        được ghép chia đôi (tối đa SPLIT_SLOTS ô, hơn thế thì xoay vòng mỗi SPLIT_ROTATE_S giây)."""
        stack = sorted(self._layers, key=lambda l: (l["priority"], l["seq"]))
        if not stack:
            self._group_ids, self._group_size = (), 0
            return self._default_layer()
        top = stack[-1]
        group: list[dict] = []
        if top.get("split"):
            for l in reversed(stack):
                if not (l.get("split") and l["priority"] == top["priority"]):
                    break
                group.append(l)
            group.reverse()                                    # cũ nhất trước
        ids = tuple(l["seq"] for l in group)
        if ids != self._group_ids:
            self._group_ids, self._rot, self._rot_at = ids, 0, time.monotonic() + SPLIT_ROTATE_S
        if len(group) < 2 or self._split_block == frozenset(ids):
            self._group_size = 0                                 # không ghép: không có gì để xoay
            return top
        self._group_size = len(group)
        n = len(group)
        members = [group[(self._rot + k) % n] for k in range(min(SPLIT_SLOTS, n))]
        return {"owner": "split", "mode": "live", "source": " + ".join(m["source"] for m in members),
                "members": members, "loop": False, "after": "hold", "live": True, "priority": top["priority"]}

    @staticmethod
    def _refresh_source(layer: dict) -> None:
        """Nguồn có mã truy cập QUAY VÒNG (luồng camera của HA: token đổi mỗi 5 phút, hết hạn sau ~10 phút) thì xin địa chỉ mới MỖI LẦN
        khởi động lại ffmpeg. Dùng địa chỉ cũ lưu từ lúc đặt thì sau lần mất kết nối đầu tiên là 401 mãi tới khi khởi động lại HA."""
        refresh = layer.get("refresh")
        if refresh is None:
            return
        try:
            fresh = refresh()
        except Exception as exc:                                       # noqa: BLE001 — không làm hỏng việc hiện hình: dùng địa chỉ cũ
            log.warning("man hinh: khong lam moi duoc nguon (%s); dung dia chi cu", exc)
            return
        if fresh:
            layer["source"] = fresh

    def _apply(self) -> None:
        """Make the encoder show the top layer (or the default); close the session if nothing is left."""
        with self._lock:
            self._busy = (threading.get_ident(), time.monotonic())
            try:
                self._apply_locked()
            finally:
                self._busy = None

    def _apply_locked(self) -> None:
        layer = self._target()
        if layer is None:
            self._stop_encoder()
            self._close_session()
            self._cur_key, self._cur_layer = None, None
            self.mode, self.source = "off", ""
            return
        rotate, fit = self._view_of(layer)
        key = (layer["owner"], layer["source"], layer["loop"], layer["mode"], rotate, fit, self.quality, layer.get("live"))
        if self._sender is None or not self._sender.running:
            self._cur_key = None                  # the AVTalk session died or was closed by a failed reopen: open it again below
        if key == self._cur_key and (self._proc is not None or layer.get("ended")):
            return
        self._ensure_open()
        self._stop_encoder()
        assert self._sender is not None
        self._sender.reset()
        for part in layer.get("members") or [layer]:
            self._refresh_source(part)
        if layer.get("members"):
            layer["source"] = " + ".join(m["source"] for m in layer["members"])
        live = is_live_source(layer["source"]) if layer.get("live") is None else bool(layer["live"])
        self._sender.set_live(live)                               # nguồn trực tiếp: bộ gửi bỏ khung cũ khi chậm
        if layer.get("members"):
            cmd = build_split_cmd([m["source"] for m in layer["members"]], self.width, self.height, self.fps, self.cfg.ffmpeg,
                                  self.quality)
        else:
            cmd = build_hevc_cmd(layer["source"], self.width, self.height, self.fps, layer["loop"], self.cfg.ffmpeg,
                                 rotate, fit, self.quality, live)
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as exc:
            raise RuntimeError(f"failed to start ffmpeg: {exc}") from exc
        layer["ended"] = False
        self._proc, self._cur_key, self._cur_layer = proc, key, layer
        self.mode, self.source = layer["mode"], layer["source"]
        self.last_error = None
        self.switches += 1
        threading.Thread(target=self._pump, args=(proc, self._token), daemon=True,
                         name="screen-pump").start()
        log.info("man hinh: hien %s (%s) lop=%s con %d lop", layer["mode"],
                 redact_source(layer["source"]), layer["owner"] if not layer.get("members") else
                 "chia doi " + "+".join(m["owner"] for m in layer["members"]), len(self._layers))

    def show(self, source: str, loop: bool = False, after: str = "off", owner: str = "api",
             ttl: float | None = None, mode: str | None = None, live: bool | None = None,
             priority: int = PRIORITY_DEFAULT, split: bool = False, refresh=None) -> dict:
        """Push an event layer showing ``source`` (file, URL, rtsp://, image, lavfi:...).

        The newest layer is displayed. A layer ends when its source ends (``after="off"``), when
        ``ttl`` seconds pass, or on ``release(owner)``; then the layer below, or the default screen,
        shows again. ``after="hold"`` keeps the last frame until ttl/release. Showing again with the
        same ``owner`` replaces that owner's layer.

        ``priority`` 1..4: lớp mức cao hơn luôn ở trên (mức thấp hơn chờ bên dưới, hết giờ ``ttl`` vẫn tính); cùng mức thì lớp tới sau đè
        lên rồi trả lại lớp dưới khi hết. ``split``: camera trực tiếp được chia màn với các camera ``split`` khác cùng mức trên cùng."""
        if priority not in PRIORITIES:
            raise ValueError("priority must be 1..4")
        if after not in ("off", "hold"):
            raise ValueError("after must be 'off' or 'hold'")
        if ttl is not None and not 0 < ttl <= 86400:
            raise ValueError("ttl must be 1..86400 seconds")
        if not isinstance(owner, str) or not owner or len(owner) > 64:
            raise ValueError("owner must be a short non-empty string")
        with self._lock:
            self._layers = [l for l in self._layers if l["owner"] != owner]
            self._seq += 1
            layer = {"owner": owner, "source": source, "loop": bool(loop), "after": after, "live": live,
                     "mode": mode or self._kind(source), "seq": self._seq, "split": bool(split), "refresh": refresh,
                     "priority": 0 if owner == CAROUSEL_OWNER else priority,     # luân phiên nằm DƯỚI mọi sự kiện (mức 0)
                     "deadline": time.monotonic() + ttl if ttl else None}
            self._layers.append(layer)
            try:
                self._apply()
            except Exception:
                self._layers = [l for l in self._layers if l is not layer]      # never leave a layer that is not shown
                raise
        return self.status()

    def show_text(self, title: str = "", messages: list | None = None, font_size: int = 15,
                  after: str = "hold", owner: str = "api", ttl: float | None = None, priority: int = PRIORITY_DEFAULT) -> dict:
        png = render.render_screen(title, messages, font_size)
        path = os.path.join(self._tmpdir, "text-%s-%d.png" % (re.sub(r"\W", "_", owner), self.switches + 1))
        with open(path, "wb") as f:
            f.write(png)
        status = self.show(path, after=after, owner=owner, ttl=ttl, mode="text", priority=priority)
        for old in os.listdir(self._tmpdir):               # keep the newest text image and the clock only
            if old.startswith("text-") and old != os.path.basename(path) \
                    and not any(l["source"].endswith(old) for l in self._layers):
                try:
                    os.remove(os.path.join(self._tmpdir, old))
                except OSError:
                    pass
        return status

    def show_clock(self, after: str = "hold", owner: str = "api", ttl: float | None = None) -> dict:
        """Đồng hồ đầy đủ như MỘT LỚP (dùng cho màn luân phiên). Không tự vẽ lại mỗi phút: người gọi đẩy lại khi cần."""
        png = render.render_clock(self._now(), self.weather)
        path = os.path.join(self._tmpdir, "text-%s-%d.png" % (re.sub(r"\W", "_", owner), self.switches + 1))
        with open(path, "wb") as f:
            f.write(png)
        status = self.show(path, after=after, owner=owner, ttl=ttl, mode="clock")
        for old in os.listdir(self._tmpdir):
            if old.startswith("text-") and old != os.path.basename(path) \
                    and not any(l["source"].endswith(old) for l in self._layers):
                try:
                    os.remove(os.path.join(self._tmpdir, old))
                except OSError:
                    pass
        return status

    def show_call(self, name: str, status: str, video: bool | None = None, color: str = "red", after: str = "hold",
                  owner: str = "zalo_call", ttl: float | None = None, priority: int = PRIORITY_DEFAULT) -> dict:
        """Màn cuộc gọi: tên người gọi cỡ lớn + trạng thái (xem ``render.render_call``), hiện như một lớp sự kiện."""
        if color not in render.CALL_COLORS:
            raise ValueError("color must be one of " + ", ".join(render.CALL_COLORS))
        path = os.path.join(self._tmpdir, "text-%s-%d.png" % (re.sub(r"\W", "_", owner), self.switches + 1))
        with open(path, "wb") as f:
            f.write(render.render_call(name, status, video, color))
        status_ = self.show(path, after=after, owner=owner, ttl=ttl, mode="text", priority=priority)
        for old in os.listdir(self._tmpdir):               # giữ ảnh chữ mới nhất và đồng hồ, như show_text
            if old.startswith("text-") and old != os.path.basename(path) \
                    and not any(l["source"].endswith(old) for l in self._layers):
                try:
                    os.remove(os.path.join(self._tmpdir, old))
                except OSError:
                    pass
        return status_

    def show_card(self, title: str = "", value: str = "", caption: str = "", rows: list | None = None,
                  color: str = "red", after: str = "hold", owner: str = "api", ttl: float | None = None,
                  theme: str = "light", layout: str = "list", title_color: str | None = None,
                  priority: int = PRIORITY_DEFAULT) -> dict:
        """Thẻ thông tin (số lớn + các dòng nhãn–giá trị): vẽ rồi hiện như một lớp sự kiện (xem ``render.render_card``)."""
        if theme not in render.CARD_THEMES or layout not in render.CARD_LAYOUTS:
            raise ValueError("theme must be light|dark and layout list|tiles")
        colours = [color, title_color, *[r.get("color") for r in (rows or [])]]
        if not all(c is None or render.valid_color(c) for c in colours):
            raise ValueError("color must be one of " + ", ".join(render.CARD_COLORS) + " or #rrggbb")
        png = render.render_card(title, value, caption, rows, color, theme, layout, title_color)
        path = os.path.join(self._tmpdir, "text-%s-%d.png" % (re.sub(r"\W", "_", owner), self.switches + 1))
        with open(path, "wb") as f:
            f.write(png)
        status = self.show(path, after=after, owner=owner, ttl=ttl, mode="text", priority=priority)
        for old in os.listdir(self._tmpdir):               # giữ ảnh chữ/thẻ mới nhất và đồng hồ, như show_text
            if old.startswith("text-") and old != os.path.basename(path) \
                    and not any(l["source"].endswith(old) for l in self._layers):
                try:
                    os.remove(os.path.join(self._tmpdir, old))
                except OSError:
                    pass
        return status

    def release(self, owner: str) -> dict:
        """End the layer of ``owner`` (no error if it is already gone)."""
        with self._lock:
            before = len(self._layers)
            self._layers = [l for l in self._layers if l["owner"] != owner]
            if len(self._layers) != before:
                self._apply()
        return self.status()

    def off(self) -> dict:
        """End every layer: the default screen shows (or the screen closes when the default is off)."""
        with self._lock:
            self._layers = []
            self._apply()
        return self.status()

    def set_default(self, mode: str, source: str = "", rotate: int | None = None, fit: str | None = None, refresh=None) -> dict:
        """Default screen. ``rotate``/``fit`` set the screen-wide view (None = keep the current one)."""
        if mode not in _DEFAULT_MODES:
            raise ValueError("mode must be one of " + ", ".join(_DEFAULT_MODES))
        if mode in ("image", "video", "live") and not source:
            raise ValueError("source is required for image, video and live")
        check_view(rotate, fit)
        with self._lock:
            if rotate is not None:
                self.view["rotate"] = rotate
            if fit is not None:
                self.view["fit"] = fit
            self.default = {"mode": mode, "source": source if mode in ("image", "video", "live") else "", "refresh": refresh}
            self._cur_key = None                      # force the encoder to restart on the new default
            self._apply()
        return self.status()

    def set_quality(self, quality: str) -> dict:
        """Mức chất lượng mã hoá: standard | high | max; mã hoá lại thứ đang hiện."""
        check_quality(quality)
        with self._lock:
            if quality != self.quality:
                self.quality = quality
                self._cur_key = None
                self._apply()
        return self.status()

    def set_view(self, rotate: int | None = None, fit: str | None = None) -> dict:
        """Set the screen-wide rotation / fit (None = keep) and re-encode what is showing now."""
        check_view(rotate, fit)
        with self._lock:
            if rotate is not None:
                self.view["rotate"] = rotate
            if fit is not None:
                self.view["fit"] = fit
            self._cur_key = None
            self._apply()
        return self.status()

    def _stop_encoder(self) -> None:
        self._token += 1
        proc, self._proc = self._proc, None
        if proc is not None:
            reap_async(proc)

    def _pump(self, proc: subprocess.Popen, token: int) -> None:
        splitter = avtalk.HevcSplitter()
        ended = True
        try:
            while True:
                chunk = proc.stdout.read1(65536)
                if not chunk:
                    break
                if token != self._token:
                    ended = False                   # superseded by a newer source: ``_stop_encoder`` is already reaping this ffmpeg
                    return
                sender = self._sender
                if sender is None:
                    continue                        # chưa/không còn phiên tới camera (vừa mất điện): bỏ khung nhưng VẪN đọc, ffmpeg không bị nghẽn ống
                for is_key, au in splitter.feed(chunk):
                    sender.push(is_key, au)
        finally:
            if ended:
                self._pump_finished(proc, token)


    def _pump_finished(self, proc, token: int) -> None:
        """Dọn sau khi ffmpeg KẾT THÚC. Tách riêng (không để ``return`` trong ``finally``: Python 3.14 cảnh báo).

        Chờ tiến trình thoát và đọc stderr NGOÀI khoá: trước đây ``proc.stderr.read()`` nằm trong khoá, mà luồng đọc thoát sớm khi mất phiên
        trong lúc ffmpeg vẫn sống → đọc chặn mãi, mọi ``show``/``_tick`` chờ khoá mãi (rút nguồn camera rồi automation không chiếu được, 15/11)."""
        err = b""
        try:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            err = proc.stderr.read() or b""
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        with self._lock:
            if token != self._token:
                return
            if err.strip():
                self.last_error = redact_source(err.decode("utf-8", "replace").strip())
                log.warning("man hinh: ffmpeg: %s", self.last_error)
            self._proc = None
            layer = self._cur_layer
            if layer is None:
                return
            is_default = layer["owner"] == "default"
            if layer.get("members"):
                # Màn chia đôi: một nguồn chết thì ffmpeg thoát, không biết nguồn nào. Đánh dấu nhóm này «không ghép» → hiện riêng lớp trên cùng
                # (nếu chính nó hỏng thì đường lớp đơn thường gỡ nó), tránh vòng dựng lại mãi cùng một tổ hợp.
                self._split_block = frozenset(self._group_ids)
                log.warning("man hinh: man chia doi dung (%s); hien rieng lop tren cung", self.last_error or "nguon ket thuc")
                self._cur_key = None
                self._apply_quiet()
            elif is_default and layer["mode"] == "live":
                self._retry_at = time.monotonic() + 5       # camera stream dropped: try again soon
                self._cur_key = None
            elif layer["after"] == "off" or (self.last_error and not is_default):
                log.info("man hinh: nguon ket thuc (%s), xuong lop duoi", layer["owner"])
                self._layers = [l for l in self._layers if l is not layer]
                self._cur_key = None
                self._apply_quiet()
            else:
                layer["ended"] = True              # hold: keep the last frame

    def _apply_quiet(self) -> None:
        try:
            self._apply()
        except (OSError, DVRIPError, RuntimeError) as exc:
            self.last_error = f"man hinh: {exc}"
            log.warning("man hinh: khong ap dung duoc lop ke tiep: %s", exc)

    def _tick(self) -> None:
        """Once a second: expire layers by ttl; keep the screen alive (apply the default at startup,
        reopen a dropped session, refresh the clock each minute, retry a dropped live camera).
        Sau lỗi thử lại sau 3 s rồi gấp đôi tới 30 s (camera vừa cắm lại nguồn lên nhanh, camera thiếu/khoá không bị dồn đăng nhập).
        BẮT MỌI LỖI: bộ nền này mà chết im lặng thì màn hình không bao giờ tự về (đã gặp 15/11)."""
        backoff_until, backoff = 0.0, RETRY_FIRST_S
        while not self._stopping:
            time.sleep(1.0)
            now = time.monotonic()
            with self._lock:
                try:
                    expired = [l for l in self._layers if l.get("deadline") and now >= l["deadline"]]
                    if expired:
                        self._layers = [l for l in self._layers if l not in expired]
                        self._cur_key = None
                        self._apply()
                        continue
                    if now < backoff_until:
                        continue
                    if self._group_size > SPLIT_SLOTS and now >= self._rot_at:
                        self._rot, self._rot_at, self._cur_key = self._rot + 1, now + SPLIT_ROTATE_S, None
                        self._apply()
                        continue
                    want = bool(self._layers) or self.default["mode"] != "off"
                    alive = self._sender is not None and self._sender.running
                    if want and not alive:
                        self._cur_key = None
                        self._apply()
                    elif not self._layers and self.default["mode"] == "clock" \
                            and self._clock_signature(self._now()) != self._clock_sig:
                        self._cur_key = None
                        self._apply()
                    elif not self._layers and self.default["mode"] == "live" \
                            and self._proc is None and now >= self._retry_at:
                        self._cur_key = None
                        self._apply()
                    backoff = RETRY_FIRST_S                      # chạy được: lần lỗi sau lại bắt đầu từ 3 s
                except (OSError, DVRIPError, RuntimeError) as exc:
                    self.last_error = f"man hinh: {exc}"
                    log.warning("man hinh: %s; thu lai sau %.0f giay", exc, backoff)
                    backoff_until, backoff = now + backoff, min(backoff * 2, RETRY_MAX_S)
                except Exception:                                # noqa: BLE001 — không bao giờ để bộ nền chết
                    self.last_error = "man hinh: loi bat ngo (xem nhat ky)"
                    log.exception("man hinh: loi bat ngo trong vong nen; thu lai sau %.0f giay", backoff)
                    backoff_until, backoff = now + backoff, min(backoff * 2, RETRY_MAX_S)

    def _watch(self) -> None:
        """Bộ canh giữ: một lần ``_apply`` quá ``WATCHDOG_STUCK_S`` giây là KẸT (kết nối nửa chết, chờ mạng không hạn…). Ghi ngăn xếp luồng
        kẹt vào nhật ký (để biết đúng chỗ) và đóng thô mọi kết nối: luồng đang chờ nhận lỗi, bỏ ``_apply`` và vòng nền thử lại."""
        while not self._stopping:
            time.sleep(WATCHDOG_EVERY_S)
            busy = self._busy
            if busy is None or time.monotonic() - busy[1] < WATCHDOG_STUCK_S:
                continue
            frame = sys._current_frames().get(busy[0])
            stack = "".join(traceback.format_stack(frame)) if frame is not None else "(không lấy được ngăn xếp)"
            log.warning("man hinh: _apply kẹt %.0f giây; ngăn xếp luồng:\n%s; đóng thô kết nối", time.monotonic() - busy[1], stack)
            self.aborts += 1
            self._abort_network()
            self._busy = (busy[0], time.monotonic())             # cho luồng thời gian thoát ra trước khi canh tiếp

    def _abort_network(self) -> None:
        """Đóng thô các kết nối (đang mở dở, đang chạy) để mọi chỗ chờ mạng nhận lỗi ngay."""
        for target in (self._opening_session, self._session):
            if target is not None:
                target.abort()
        if self._negotiating is not None:
            self._negotiating.close()

    def status(self) -> dict:
        s = self._sender
        now = time.monotonic()
        return {"mode": self.mode,
                "open": bool(s and s.running),
                "source": redact_source(self.source) if self.mode not in ("text", "clock") else self.mode,
                "default": {"mode": self.default["mode"], "source": redact_source(self.default["source"])},
                "view": dict(self.view), "quality": self.quality, "aborts": self.aborts, "weather": dict(self.weather) if self.weather else None,
                "layers": [{"owner": l["owner"], "mode": l["mode"],
                            "source": redact_source(l["source"]) if l["mode"] not in ("text", "clock") else l["mode"],
                            "after": l["after"], "priority": l["priority"], "split": l.get("split", False),
                            "ttl_left": round(l["deadline"] - now, 1) if l.get("deadline") else None}
                           for l in sorted(self._layers, key=lambda l: (l["priority"], l["seq"]))],     # đáy trước, lớp đang hiện cuối
                "size": [self.width, self.height],
                "fps": self.fps, "switches": self.switches,
                "started_at": self.started_at, "last_error": self.last_error,
                "sender": s.stats() if s else None}
