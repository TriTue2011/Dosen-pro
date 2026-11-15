"""The camera screen as a display: one AVTalk session, swappable sources.

``Screen.show()`` keeps the AVTalk session open and swaps what feeds it: a
video file/URL, an RTSP camera, an image, or rendered text (app/render.py).
Each source is one ffmpeg process encoding H.265 for the LCD; switching
starts a new process and the sender waits for its first key frame. When a
source ends on its own the screen either closes (the camera returns to its
idle screen) or holds the last frame, per ``after``.
"""

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from datetime import datetime

from . import avtalk, render, video_sender
from .config import settings
from .dvrip import DVRIPClient, DVRIPError
from .video import redact_source

log = logging.getLogger("icsee.screen")

_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
_DEFAULT_MODES = ("off", "clock", "image", "video", "live")


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
        self.mode = "off"                     # what is on the LCD now: off | video | image | text | clock | live
        self.source = ""
        self.started_at: float | None = None
        self.last_error: str | None = None
        self.switches = 0
        self._tmpdir = tempfile.mkdtemp(prefix="icsee-screen-")
        # Layers = events, the newest on top wins; when a layer ends the one below (or the default) shows.
        self._layers: list[dict] = []
        self.default: dict = {"mode": "off", "source": ""}     # off | clock | image | video | live
        self._cur_key: tuple | None = None
        self._cur_layer: dict | None = None
        self._retry_at = 0.0
        self._clock_minute = ""
        self._state_file = os.path.join(os.environ.get("STATE_DIR", "/data"), "screen.json")
        self._ticker: threading.Thread | None = None

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
        if settings.screen_auto_wake and session.control is not None:
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
        client = DVRIPClient(settings.cam_ip, settings.cam_port,
                             settings.dvrip_connect_timeout, settings.dvrip_read_timeout)
        try:
            client.connect()
            client.login(settings.cam_user, settings.cam_pass)
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
        """Read the saved default screen and start the background ticker (call once at startup)."""
        try:
            with open(self._state_file) as f:
                d = json.load(f).get("default") or {}
            if d.get("mode") in _DEFAULT_MODES:
                self.default = {"mode": d["mode"], "source": str(d.get("source") or "")}
        except (OSError, ValueError):
            pass
        if self._ticker is None:
            self._ticker = threading.Thread(target=self._tick, daemon=True, name="screen-tick")
            self._ticker.start()

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
            with open(self._state_file, "w") as f:
                json.dump({"default": self.default}, f)
        except OSError as exc:
            log.warning("man hinh: khong luu duoc cai dat mac dinh: %s", exc)

    def _default_layer(self) -> dict | None:
        mode, src = self.default["mode"], self.default["source"]
        if mode == "off":
            return None
        if mode == "clock":
            png = os.path.join(self._tmpdir, "clock.png")
            now = datetime.now().astimezone()
            with open(png, "wb") as f:
                f.write(render.render_clock(now))
            self._clock_minute = now.strftime("%H%M")
            return {"owner": "default", "source": png, "mode": "clock", "loop": False, "after": "hold"}
        if not src:
            return None
        return {"owner": "default", "source": src, "mode": mode, "loop": mode == "video",
                "after": "off"}

    def _apply(self) -> None:
        """Make the encoder show the top layer (or the default); close the session if nothing is left."""
        with self._lock:
            layer = self._layers[-1] if self._layers else self._default_layer()
            if layer is None:
                self._stop_encoder()
                self._close_session()
                self._cur_key, self._cur_layer = None, None
                self.mode, self.source = "off", ""
                return
            key = (layer["owner"], layer["source"], layer["loop"], layer["mode"])
            if self._sender is not None and not self._sender.running:
                self._cur_key = None                  # the AVTalk session died: reopen below
            if key == self._cur_key and (self._proc is not None or layer.get("ended")):
                return
            self._ensure_open()
            self._stop_encoder()
            assert self._sender is not None
            self._sender.reset()
            cmd = build_hevc_cmd(layer["source"], self.width, self.height, self.fps, layer["loop"])
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
                     redact_source(layer["source"]), layer["owner"], len(self._layers))

    def show(self, source: str, loop: bool = False, after: str = "off", owner: str = "api",
             ttl: float | None = None, mode: str | None = None) -> dict:
        """Push an event layer showing ``source`` (file, URL, rtsp://, image, lavfi:...).

        The newest layer is displayed. A layer ends when its source ends (``after="off"``), when
        ``ttl`` seconds pass, or on ``release(owner)``; then the layer below, or the default screen,
        shows again. ``after="hold"`` keeps the last frame until ttl/release. Showing again with the
        same ``owner`` replaces that owner's layer."""
        if after not in ("off", "hold"):
            raise ValueError("after must be 'off' or 'hold'")
        if ttl is not None and not 0 < ttl <= 86400:
            raise ValueError("ttl must be 1..86400 seconds")
        if not isinstance(owner, str) or not owner or len(owner) > 64:
            raise ValueError("owner must be a short non-empty string")
        with self._lock:
            self._layers = [l for l in self._layers if l["owner"] != owner]
            layer = {"owner": owner, "source": source, "loop": bool(loop), "after": after,
                     "mode": mode or self._kind(source),
                     "deadline": time.monotonic() + ttl if ttl else None}
            self._layers.append(layer)
            try:
                self._apply()
            except Exception:
                self._layers = [l for l in self._layers if l is not layer]      # never leave a layer that is not shown
                raise
        return self.status()

    def show_text(self, title: str = "", messages: list | None = None, font_size: int = 15,
                  after: str = "hold", owner: str = "api", ttl: float | None = None) -> dict:
        png = render.render_screen(title, messages, font_size)
        path = os.path.join(self._tmpdir, "text-%s-%d.png" % (re.sub(r"\W", "_", owner), self.switches + 1))
        with open(path, "wb") as f:
            f.write(png)
        status = self.show(path, after=after, owner=owner, ttl=ttl, mode="text")
        for old in os.listdir(self._tmpdir):               # keep the newest text image and the clock only
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

    def set_default(self, mode: str, source: str = "") -> dict:
        if mode not in _DEFAULT_MODES:
            raise ValueError("mode must be one of " + ", ".join(_DEFAULT_MODES))
        if mode in ("image", "video", "live") and not source:
            raise ValueError("source is required for image, video and live")
        with self._lock:
            self.default = {"mode": mode, "source": source if mode in ("image", "video", "live") else ""}
            self._save()
            self._cur_key = None                      # force the encoder to restart on the new default
            self._apply()
        return self.status()

    def _stop_encoder(self) -> None:
        self._token += 1
        if self._proc is not None:
            try:
                self._proc.terminate()
            except OSError:
                pass
            self._proc = None

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
                layer = self._cur_layer
                if layer is None:
                    return
                is_default = layer["owner"] == "default"
                if is_default and layer["mode"] == "live":
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
        A failure backs off 30 s so a missing or locked camera is not hammered with logins."""
        backoff_until = 0.0
        while True:
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
                    want = bool(self._layers) or self.default["mode"] != "off"
                    alive = self._sender is not None and self._sender.running
                    if want and not alive:
                        self._cur_key = None
                        self._apply()
                    elif not self._layers and self.default["mode"] == "clock" \
                            and datetime.now().strftime("%H%M") != self._clock_minute:
                        self._cur_key = None
                        self._apply()
                    elif not self._layers and self.default["mode"] == "live" \
                            and self._proc is None and now >= self._retry_at:
                        self._cur_key = None
                        self._apply()
                except (OSError, DVRIPError, RuntimeError) as exc:
                    self.last_error = f"man hinh: {exc}"
                    log.warning("man hinh: %s; thu lai sau 30 giay", exc)
                    backoff_until = now + 30

    def status(self) -> dict:
        s = self._sender
        now = time.monotonic()
        return {"mode": self.mode,
                "open": bool(s and s.running),
                "source": redact_source(self.source) if self.mode not in ("text", "clock") else self.mode,
                "default": dict(self.default, source=redact_source(self.default["source"])),
                "layers": [{"owner": l["owner"], "mode": l["mode"],
                            "source": redact_source(l["source"]) if l["mode"] not in ("text", "clock") else l["mode"],
                            "after": l["after"],
                            "ttl_left": round(l["deadline"] - now, 1) if l.get("deadline") else None}
                           for l in self._layers],
                "size": [self.width, self.height],
                "fps": self.fps, "switches": self.switches,
                "started_at": self.started_at, "last_error": self.last_error,
                "sender": s.stats() if s else None}
