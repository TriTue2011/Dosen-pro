"""Camera giả (đối tượng) cho test phía Home Assistant: ghi lại mọi lệnh tích hợp gửi xuống lõi."""

from __future__ import annotations


class FakeScreen:
    def __init__(self):
        self.calls = []
        self._status = {"mode": "off", "open": False, "source": "", "size": [240, 320], "last_error": None,
                        "layers": [], "default": {"mode": "clock", "source": ""}}

    def status(self):
        return self._status

    def load(self):
        self.calls.append(("load",))

    def close(self):
        self.calls.append(("close",))

    def show(self, source, loop, after, owner, ttl):
        self.calls.append(("show", {"source": source, "loop": loop, "after": after, "owner": owner, "ttl": ttl}))
        return self._status

    def show_text(self, title, messages, font_size, after, owner, ttl):
        self.calls.append(("text", {"title": title, "messages": messages, "font_size": font_size, "after": after,
                                    "owner": owner, "ttl": ttl}))
        return self._status

    def show_clock(self, after, owner, ttl):
        self.calls.append(("clock", {"after": after, "owner": owner, "ttl": ttl}))
        return self._status

    def show_card(self, title, value, caption, rows, color, after, owner, ttl, theme="light", layout="list", title_color=None):
        self.calls.append(("card", {"title": title, "value": value, "caption": caption, "rows": rows, "color": color,
                                    "after": after, "owner": owner, "ttl": ttl, "theme": theme, "layout": layout,
                                    "title_color": title_color}))
        return self._status

    def release(self, owner):
        self.calls.append(("release", owner))
        return self._status

    def set_default(self, mode, source, rotate=None, fit=None):
        self.calls.append(("default", {"mode": mode, "source": source,
                                       **({"rotate": rotate} if rotate is not None else {}),
                                       **({"fit": fit} if fit is not None else {})}))
        return self._status

    def set_quality(self, quality):
        self.calls.append(("quality", quality))
        return self._status

    def set_weather(self, weather):
        self.calls.append(("weather", weather))
        return self._status

    def set_view(self, rotate=None, fit=None):
        self.calls.append(("view", {"rotate": rotate, "fit": fit}))
        return self._status

    def off(self):
        self.calls.append(("off",))
        return self._status

    def power(self, on):
        self.calls.append(("power", on))
        return {"screen_power": on, "ret": 100}

    def set_sleep(self, seconds, luminance):
        self.calls.append(("sleep", {"seconds": seconds, "luminance": luminance}))
        return {}

    def get_sleep(self):
        return {"work_mode": "Timing", "work_time": 0, "luminance": 100}


class FakeStream:
    def __init__(self):
        self.fed, self.closed = bytearray(), False
        self.bytes_fed = 0

    def feed(self, data):
        self.fed += data
        self.bytes_fed += len(data)

    def close(self):
        self.closed = True


class FakeSpeaker:
    def __init__(self):
        self.calls = []
        self.streams = []

    def open_stream(self):
        self.calls.append(("stream",))
        self.streams.append(FakeStream())
        return self.streams[-1]

    def status(self):
        return {"playing": False, "route": "", "source": "", "bytes_sent": 0, "last_error": None}

    def play(self, source, volume):
        self.calls.append(("play", {"source": source, "volume": volume}))
        return self.status()

    def play_bytes(self, data, volume):
        self.calls.append(("bytes", len(data), volume))
        return self.status()

    def stop(self):
        self.calls.append(("stop",))
        return self.status()


class FakeMic:
    def __init__(self):
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def read_chunk(self, timeout=1.0):
        return None


class FakeEvents:
    def __init__(self):
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


class FakeCamera:
    """Thay cho ``dosen_core.camera.Camera``."""

    instances: list = []
    login_error: Exception | None = None            # test đặt để mô phỏng camera từ chối

    def __init__(self, cfg, now=None, on_alarm=None):
        self.cfg, self.now, self.on_alarm = cfg, now, on_alarm
        self.screen, self.speaker, self.mic, self.events = FakeScreen(), FakeSpeaker(), FakeMic(), FakeEvents()
        self.closed = False
        self.audio = {"speaker": 50, "mic": 50, "anr": True, "key_tip": True}
        self.audio_calls = []
        FakeCamera.instances.append(self)

    def start(self):
        self.screen.load()

    def check_login(self):
        if FakeCamera.login_error is not None:
            raise FakeCamera.login_error
        return {"Ret": 100}

    def get_audio(self):
        return dict(self.audio)

    def set_audio(self, speaker, mic, anr, key_tip=None):
        self.audio_calls.append({"speaker": speaker, "mic": mic, "anr": anr, **({"key_tip": key_tip} if key_tip is not None else {})})
        for k, v in (("speaker", speaker), ("mic", mic), ("anr", anr), ("key_tip", key_tip)):
            if v is not None:
                self.audio[k] = v
        return dict(self.audio)

    def close(self):
        self.closed = True
