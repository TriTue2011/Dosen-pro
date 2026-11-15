"""Play audio (file, URL, TTS, stream) out of the camera speaker.

One ffmpeg process decodes any source to 8 kHz mono, paced in real time (``-re``).
Two routes to the camera: an OPTalk session when the screen is closed, or the audio
channel of the open AVTalk (screen) session when it is open, because the camera
refuses OPTalk (Ret 503) while AVTalk holds the speaker. The camera speaker is
telephone quality (G.711, 8 kHz).
"""

import logging
import os
import queue
import subprocess
import tempfile
import threading
import time

from .audio import TalkSession
from .dvrip import DVRIPError
from .video import redact_source

log = logging.getLogger("icsee.speaker")
_CHUNK = 640            # 40 ms of PCM16 at 8 kHz = one talk packet


class Speaker:
    def __init__(self, talk: TalkSession, screen=None):
        self._talk = talk
        self._screen = screen
        self.route = ""
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._token = 0
        self.source = ""
        self.playing = False
        self.bytes_sent = 0
        self.last_error: str | None = None
        self._we_started_talk = False

    def play(self, source: str, volume: float = 1.0) -> dict:
        if not 0.0 <= volume <= 4.0:
            raise ValueError("volume must be between 0 and 4 (1 = unchanged)")
        with self._lock:
            self._stop_proc()
            sink = self._screen.audio_sink() if self._screen is not None else None
            self.route = "avtalk" if sink is not None else "optalk"
            if sink is None and not self._talk.running:
                self._talk.start()
                self._we_started_talk = True
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-re"]
            if source.startswith("lavfi:"):
                cmd += ["-f", "lavfi"]
                src = source[len("lavfi:"):]
            else:
                src = source
            fmt = "alaw" if sink is not None else "s16le"
            cmd += ["-i", src, "-vn", "-ac", "1", "-ar", "8000",
                    "-filter:a", f"volume={volume}", "-f", fmt, "-"]
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE)
            except OSError as exc:
                raise RuntimeError(f"failed to start ffmpeg: {exc}") from exc
            self._proc = proc
            self._token += 1
            self.source, self.playing, self.last_error = source, True, None
            self.bytes_sent = 0
            threading.Thread(target=self._pump, args=(proc, self._token, sink),
                             daemon=True, name="speaker-pump").start()
            log.info("loa: phat %s volume=%s duong=%s", redact_source(source),
                     volume, self.route)
        return self.status()

    def play_bytes(self, data: bytes, volume: float = 1.0) -> dict:
        """Play audio sent as bytes (any container ffmpeg reads, e.g. a TTS WAV). Written to a temp
        file because ffmpeg reads it at real-time speed; the previous temp file is removed."""
        if not data:
            raise ValueError("no audio data")
        fd, path = tempfile.mkstemp(prefix="icsee-spk-", suffix=".audio")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        old, self._tmp_file = getattr(self, "_tmp_file", None), path
        try:
            return self.play(path, volume)
        finally:
            if old:
                try:
                    os.remove(old)
                except OSError:
                    pass

    def _pump(self, proc: subprocess.Popen, token: int, sink=None) -> None:
        try:
            while token == self._token:
                data = proc.stdout.read(320 if sink is not None else _CHUNK)
                if not data:
                    break
                if sink is not None:
                    sink.push_audio(data)             # A-law, paced by the screen's audio clock
                else:
                    self._talk.send_pcm16(data if len(data) % 2 == 0 else data[:-1])
                self.bytes_sent += len(data)
        except (OSError, DVRIPError, queue.Full) as exc:
            if token == self._token:
                self.last_error = f"send failed: {exc}"
                log.warning("loa: %s", self.last_error)
        finally:
            with self._lock:
                if token != self._token:
                    return
                try:
                    err = proc.stderr.read() or b""
                except (OSError, ValueError):
                    err = b""
                if err.strip() and not self.last_error:
                    self.last_error = redact_source(err.decode("utf-8", "replace").strip())
                    log.warning("loa: ffmpeg: %s", self.last_error)
                self.playing = False
                self._proc = None
                if self._we_started_talk:
                    time.sleep(0.3)             # let the camera drain its buffer
                    self._talk.stop()
                    self._we_started_talk = False
                log.info("loa: xong (%d byte)", self.bytes_sent)

    def _stop_proc(self) -> None:
        self._token += 1
        if self._proc is not None:
            try:
                self._proc.terminate()
            except OSError:
                pass
            self._proc = None
        self.playing = False

    def stop(self) -> dict:
        with self._lock:
            self._stop_proc()
            if self._we_started_talk:
                self._talk.stop()
                self._we_started_talk = False
            log.info("loa: dung")
        return self.status()

    def status(self) -> dict:
        return {"playing": self.playing, "route": self.route,
                "source": redact_source(self.source),
                "bytes_sent": self.bytes_sent, "last_error": self.last_error}
