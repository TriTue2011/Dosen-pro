"""Play audio (file, URL, TTS, stream) out of the camera speaker.

One ffmpeg process decodes any source to 8 kHz mono PCM, paced in real time
(``-re``), and the chunks go to the camera over an OPTalk session. The camera
speaker is telephone quality (G.711, 8 kHz).
"""

import logging
import subprocess
import threading
import time

from .audio import TalkSession
from .dvrip import DVRIPError
from .video import redact_source

log = logging.getLogger("icsee.speaker")
_CHUNK = 640            # 40 ms of PCM16 at 8 kHz = one talk packet


class Speaker:
    def __init__(self, talk: TalkSession):
        self._talk = talk
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
            if not self._talk.running:
                self._talk.start()
                self._we_started_talk = True
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-re"]
            if source.startswith("lavfi:"):
                cmd += ["-f", "lavfi"]
                src = source[len("lavfi:"):]
            else:
                src = source
            cmd += ["-i", src, "-vn", "-ac", "1", "-ar", "8000",
                    "-filter:a", f"volume={volume}", "-f", "s16le", "-"]
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE)
            except OSError as exc:
                raise RuntimeError(f"failed to start ffmpeg: {exc}") from exc
            self._proc = proc
            self._token += 1
            self.source, self.playing, self.last_error = source, True, None
            self.bytes_sent = 0
            threading.Thread(target=self._pump, args=(proc, self._token),
                             daemon=True, name="speaker-pump").start()
            log.info("loa: phat %s volume=%s", redact_source(source), volume)
        return self.status()

    def _pump(self, proc: subprocess.Popen, token: int) -> None:
        try:
            while token == self._token:
                data = proc.stdout.read(_CHUNK)
                if not data:
                    break
                self._talk.send_pcm16(data if len(data) % 2 == 0 else data[:-1])
                self.bytes_sent += len(data)
        except (OSError, DVRIPError) as exc:
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
        return {"playing": self.playing,
                "source": redact_source(self.source),
                "bytes_sent": self.bytes_sent, "last_error": self.last_error}
