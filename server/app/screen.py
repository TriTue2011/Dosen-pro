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
import subprocess
import tempfile
import threading
import time

from . import avtalk, render, video_sender
from .config import settings
from .dvrip import DVRIPClient, DVRIPError
from .video import redact_source

log = logging.getLogger("icsee.screen")

_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


def build_hevc_cmd(source: str, width: int, height: int, fps: int,
                   loop: bool = False) -> list[str]:
    """ffmpeg command: any source -> letterboxed H.265 Annex-B on stdout."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
    image = source.lower().split("?")[0].endswith(_IMAGE_EXT)
    if source.startswith("rtsp://"):
        cmd += ["-rtsp_transport", "tcp", "-timeout", "5000000"]
    else:
        cmd += ["-re"]
        if image:
            cmd += ["-loop", "1", "-framerate", str(fps)]
        elif loop:
            cmd += ["-stream_loop", "-1"]
    if source.startswith("lavfi:"):
        cmd += ["-f", "lavfi"]
        source = source[len("lavfi:"):]
    gop = fps * 4
    cmd += ["-i", source, "-an",
            "-vf", (f"scale={width}:{height}:force_original_aspect_ratio="
                    f"decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                    f"fps={fps}"),
            "-pix_fmt", "yuv420p", "-c:v", "libx265", "-preset", "ultrafast",
            "-tune", "zerolatency", "-x265-params",
            (f"keyint={gop}:min-keyint={gop}:scenecut=0:bframes=0:slices=1:"
             "repeat-headers=1:aud=0:log-level=none:"
             "vbv-maxrate=400:vbv-bufsize=400"),
            "-f", "hevc", "-"]
    return cmd


class Screen:
    def __init__(self):
        self._lock = threading.RLock()
        self._session: avtalk.AVTalkSession | None = None
        self._sender: avtalk.AVTalkSender | None = None
        self._proc: subprocess.Popen | None = None
        self._token = 0                       # bumps on every source switch
        self.width, self.height, self.fps = 240, 320, 10
        self.mode = "off"                     # off | video | image | text
        self.source = ""
        self.after = "off"
        self.started_at: float | None = None
        self.last_error: str | None = None
        self.switches = 0
        self._tmpdir = tempfile.mkdtemp(prefix="icsee-screen-")

    # -- session ---------------------------------------------------------
    def _negotiate(self) -> None:
        """Take the LCD size/fps from the camera's DecoderPram (best effort)."""
        client = DVRIPClient(settings.cam_ip, settings.cam_port,
                             settings.dvrip_connect_timeout,
                             settings.dvrip_read_timeout)
        try:
            client.connect()
            client.login(settings.cam_user, settings.cam_pass)
            got = video_sender.apply_decoder_pram(client.query_decoder_pram())
        except (OSError, DVRIPError):
            return
        finally:
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
        if not settings.cam_ip:
            raise DVRIPError("ICSEE_CAM_IP is not set")
        self._negotiate()
        session = avtalk.AVTalkSession(
            settings.cam_ip, settings.cam_port, settings.cam_user,
            settings.cam_pass, settings.dvrip_connect_timeout,
            settings.dvrip_read_timeout)
        session.open(self.width, self.height, self.fps)
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

    # -- sources ---------------------------------------------------------
    def _stop_encoder(self) -> None:
        self._token += 1
        if self._proc is not None:
            try:
                self._proc.terminate()
            except OSError:
                pass
            self._proc = None

    def show(self, source: str, loop: bool = False, after: str = "off",
             mode: str | None = None) -> dict:
        """Show ``source`` (file, URL, rtsp://, image, lavfi:...) on the LCD."""
        if after not in ("off", "hold"):
            raise ValueError("after must be 'off' or 'hold'")
        with self._lock:
            self._ensure_open()
            self._stop_encoder()
            assert self._sender is not None
            self._sender.reset()
            cmd = build_hevc_cmd(source, self.width, self.height, self.fps, loop)
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE)
            except OSError as exc:
                raise RuntimeError(f"failed to start ffmpeg: {exc}") from exc
            self._proc = proc
            self.mode = mode or ("image" if source.lower().split("?")[0]
                                 .endswith(_IMAGE_EXT) else "video")
            self.source, self.after, self.last_error = source, after, None
            self.switches += 1
            token = self._token
            threading.Thread(target=self._pump, args=(proc, token), daemon=True,
                             name="screen-pump").start()
            log.info("man hinh: hien %s (%s) after=%s", self.mode,
                     redact_source(source), after)
        return self.status()

    def show_text(self, title: str = "", messages: list | None = None,
                  font_size: int = 15, after: str = "hold") -> dict:
        png = render.render_screen(title, messages, font_size)
        path = os.path.join(self._tmpdir, "text-%d.png" % (self.switches + 1))
        with open(path, "wb") as f:
            f.write(png)
        status = self.show(path, after=after, mode="text")
        for old in os.listdir(self._tmpdir):      # keep only the newest image
            if old != os.path.basename(path):
                try:
                    os.remove(os.path.join(self._tmpdir, old))
                except OSError:
                    pass
        return status

    def _pump(self, proc: subprocess.Popen, token: int) -> None:
        splitter = avtalk.HevcSplitter()
        try:
            while True:
                chunk = proc.stdout.read1(65536)
                if not chunk:
                    break
                if token != self._token:
                    return                          # superseded by a newer source
                sender = self._sender
                if sender is None:
                    return
                for is_key, au in splitter.feed(chunk):
                    sender.push(is_key, au)
        finally:
            with self._lock:
                if token != self._token:
                    return
                err = b""
                try:
                    err = proc.stderr.read() or b""
                except (OSError, ValueError):
                    pass
                if err.strip():
                    self.last_error = redact_source(err.decode("utf-8", "replace").strip())
                    log.warning("man hinh: ffmpeg: %s", self.last_error)
                self._proc = None
                if self.after == "off" or self.last_error:
                    log.info("man hinh: nguon ket thuc, dong")
                    self._close_session()
                    self.mode = "off"
                else:
                    log.info("man hinh: nguon ket thuc, giu khung cuoi")

    def off(self) -> dict:
        with self._lock:
            self._stop_encoder()
            self._close_session()
            self.mode = "off"
            log.info("man hinh: tat")
        return self.status()

    def status(self) -> dict:
        s = self._sender
        return {"mode": self.mode,
                "open": bool(s and s.running),
                "source": redact_source(self.source) if self.mode != "text" else "text",
                "after": self.after, "size": [self.width, self.height],
                "fps": self.fps, "switches": self.switches,
                "started_at": self.started_at, "last_error": self.last_error,
                "sender": s.stats() if s else None}
