"""Fixtures dùng chung: camera giả có AVTalk/OPTalk."""

import shutil
import subprocess
import time

import pytest

from app.config import settings

_HAS_X265 = (shutil.which("ffmpeg") is not None and "libx265" in subprocess.run(
    ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True,
    text=True).stdout) if shutil.which("ffmpeg") else False


@pytest.fixture
def fake_cam_avtalk(monkeypatch):
    import os
    import socket
    log = "/tmp/fake-cam-avtalk.log"
    if os.path.exists(log):
        os.remove(log)
    proc = subprocess.Popen(["python", "tests/fake_cam.py"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", 34567), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        monkeypatch.setattr(settings, "cam_ip", "127.0.0.1")
        monkeypatch.setattr(settings, "cam_port", 34567)
        monkeypatch.setattr(settings, "cam_user", "admin")
        monkeypatch.setattr(settings, "cam_pass", "")
        yield log
    finally:
        proc.terminate()
        proc.wait(timeout=5)


