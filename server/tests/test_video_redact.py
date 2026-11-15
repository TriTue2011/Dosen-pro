"""Nguồn RTSP của camera mang mật khẩu trong URL: API không được trả lại."""

import shutil
import time

import pytest

from app.video import VideoPipeline, redact_source

SECRET = "fake-secret-pw"   # chuỗi giả, không phải mật khẩu thật


def test_redacts_query_password_and_userinfo():
    a = "rtsp://10.0.0.5:554/user=admin&password=%s&channel=1&stream=1.sdp?real_stream" % SECRET
    b = "rtsp://admin:%s@10.0.0.5:554/h264" % SECRET
    c = "rtsp://10.0.0.5/live?user=admin&pass=%s&x=1" % SECRET
    for url in (a, b, c):
        out = redact_source(url)
        assert SECRET not in out, out
    assert "channel=1" in redact_source(a)           # phần không bí mật giữ nguyên
    assert redact_source("/tmp/clip.mp4") == "/tmp/clip.mp4"


@pytest.mark.skipif(shutil.which("ffmpeg") is None,
                    reason="cần ffmpeg (có trong ảnh Docker)")
def test_status_and_last_error_do_not_leak_the_password():
    p = VideoPipeline()
    # nguồn hỏng: ffmpeg báo lỗi kèm đúng chuỗi nguồn
    p.start("rtsp://admin:%s@127.0.0.1:9/none" % SECRET,
            width=64, height=48, fps=5, codec="H264")
    deadline = time.time() + 15
    while p.running and time.time() < deadline:
        time.sleep(0.1)
    p.stop()
    assert SECRET not in repr(p.status()), p.status()
