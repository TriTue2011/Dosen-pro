"""Chiếu hình TRỰC TIẾP ra màn càng nhanh càng tốt: không «-re», cờ ffmpeg giảm trễ, khung khoá mỗi 1 s, bộ gửi bỏ khung cũ khi chậm."""

import http.server
import subprocess
import threading
import time

import pytest
from conftest import _HAS_X265
from dosen_core import avtalk
from dosen_core.screen import build_hevc_cmd, is_live_source

needs_x265 = pytest.mark.skipif(not _HAS_X265, reason="cần ffmpeg có libx265")


@pytest.mark.parametrize("src,live", [("rtsp://cam/x", True), ("rtsps://cam/x", True), ("http://app:8090/api/call/video/view?call_id=7", True),
                                      ("http://cam/stream.mjpg", True), ("http://x/clip.mp4", False), ("/tmp/a.png", False),
                                      ("lavfi:testsrc", False), ("http://x/api/camera_proxy_stream/camera.cua?token=a", False)])
def test_which_sources_count_as_live(src, live):
    assert is_live_source(src) is live


def test_live_command_has_no_pacing_and_minimal_buffering_and_short_gop():
    live = build_hevc_cmd("http://app:8090/api/call/video/view?call_id=7", 240, 320, 10)
    assert "-re" not in live and "nobuffer+discardcorrupt" in live and "low_delay" in live
    assert live[live.index("-analyzeduration") + 1] == "0" and "-use_wallclock_as_timestamps" in live
    assert "keyint=10:min-keyint=10" in live[live.index("-x265-params") + 1]            # khung khoá mỗi 1 giây
    rtsp = build_hevc_cmd("rtsp://cam/x", 240, 320, 10)
    assert "-re" not in rtsp and "-rtsp_transport" in rtsp and "-use_wallclock_as_timestamps" not in rtsp
    file_ = build_hevc_cmd("http://x/clip.mp4", 240, 320, 10)
    assert "-re" in file_ and "nobuffer+discardcorrupt" not in file_                    # tệp vẫn tự giả lập tốc độ
    forced = build_hevc_cmd("http://x/other", 240, 320, 10, live=True)
    assert "-re" not in forced and "keyint=10:min-keyint=10" in forced[forced.index("-x265-params") + 1]


def test_live_sender_drops_stale_frames_instead_of_queueing_seconds_of_delay():
    class Sess:
        client = None

        def send(self, packet): pass
    s = avtalk.AVTalkSender(Sess(), 240, 320, 10)
    for i in range(50):                                   # nguồn thường: dồn tới giới hạn (≈ 5 s ở 10 fps)
        s.push(False, b"x")
    assert s._q.qsize() == 50
    s2 = avtalk.AVTalkSender(Sess(), 240, 320, 10)
    s2.set_live(True)
    for i in range(50):
        s2.push(i % 10 == 0, b"x")
    assert s2._q.qsize() <= avtalk.AVTalkSender.LIVE_MAX_QUEUE and s2.live_dropped >= 1   # trực tiếp: không bao giờ tụt lại quá ~0,3 s


class _Mjpeg(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace;boundary=frame")
        self.end_headers()
        p = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=15",
                              "-f", "mpjpeg", "-boundary_tag", "frame", "-q:v", "6", "-"], stdout=subprocess.PIPE)
        try:
            while True:
                d = p.stdout.read(4096)
                if not d:
                    break
                self.wfile.write(d)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            p.kill()
            p.wait()


@needs_x265
def test_live_mjpeg_over_http_reaches_the_screen_encoder_quickly_and_keeps_up(tmp_path):
    """Hình cuộc gọi Zalo (MJPEG qua HTTP): khung khoá đầu tiên tới nhanh (<3 s) và sau đó không tụt lại (đủ khung theo thời gian thật)."""
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Mjpeg)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/api/call/video/view?call_id=1"
    t0 = time.monotonic()
    p = subprocess.Popen(build_hevc_cmd(url, 240, 320, 10), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    splitter, first_key, aus = avtalk.HevcSplitter(), None, []
    try:
        while time.monotonic() - t0 < 7:
            chunk = p.stdout.read1(65536)
            if not chunk:
                break
            for is_key, au in splitter.feed(chunk):
                aus.append((time.monotonic() - t0, is_key, len(au)))
                if is_key and first_key is None:
                    first_key = time.monotonic() - t0
    finally:
        p.kill()
        p.wait()
        srv.shutdown()
    assert first_key is not None and first_key < 3.0, f"khung khoá đầu sau {first_key}"
    assert len([a for a in aus if a[0] > 1.0]) >= 40, "ffmpeg tụt lại: ít khung hơn thời gian thật (10 fps)"
    assert max(n for _, _, n in aus) <= 8000                                         # vẫn dưới ngưỡng khung làm camera đen
    keys = [t for t, k, _ in aus if k]
    assert len(keys) >= 4 and max(b - a for a, b in zip(keys, keys[1:])) < 1.6        # khung khoá đều ~1 s
