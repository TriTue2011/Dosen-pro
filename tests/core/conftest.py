"""Fixture chung cho test lõi: camera giả (tests/core/fake_cam.py) và cấu hình trỏ vào nó."""

import os
import shutil
import socket
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import load_core  # noqa: E402

load_core.load()                                  # `import dosen_core` không kéo theo HA, không đụng sys.path
from dosen_core.config import CamConfig  # noqa: E402

_HAS_X265 = (shutil.which("ffmpeg") is not None and "libx265" in subprocess.run(
    ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout) if shutil.which("ffmpeg") else False


@pytest.fixture
def cfg():
    return CamConfig(cam_ip="127.0.0.1", cam_port=34567, cam_user="admin", cam_pass="")


def _chay_camera_gia():
    for f in ("/tmp/fake-cam-avtalk.log", "/tmp/fake-cam-talk.bin"):
        if os.path.exists(f):
            os.remove(f)
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "fake_cam.py")],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", 34567), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    return proc


@pytest.fixture
def fake_cam_avtalk():
    proc = _chay_camera_gia()
    try:
        yield "/tmp/fake-cam-avtalk.log"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.fixture(scope="module")
def fake_cam():
    proc = _chay_camera_gia()
    try:
        yield proc
    finally:
        proc.terminate()
        proc.wait(timeout=5)
