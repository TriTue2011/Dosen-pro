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

    def release(self, owner):
        self.calls.append(("release", owner))
        return self._status

    def set_default(self, mode, source):
        self.calls.append(("default", {"mode": mode, "source": source}))
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


class FakeSpeaker:
    def __init__(self):
        self.calls = []

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
        FakeCamera.instances.append(self)

    def start(self):
        self.screen.load()

    def check_login(self):
        if FakeCamera.login_error is not None:
            raise FakeCamera.login_error
        return {"Ret": 100}

    def close(self):
        self.closed = True
