"""Nguồn có mã truy cập quay vòng (luồng camera của HA) phải được xin lại mỗi lần ffmpeg khởi động lại — nếu không, sau lần mất kết nối đầu
tiên (rút nguồn camera màn hình) hình cứ 401 mãi tới khi khởi động lại HA (đã gặp 15/11)."""

import json
import time

from dosen_core import screen as screen_mod
from dosen_core.screen import Screen


def _record_sources(monkeypatch):
    seen = []
    real = screen_mod.build_hevc_cmd

    def spy(source, *a, **k):
        seen.append(source)
        return real(source, *a, **k)
    monkeypatch.setattr(screen_mod, "build_hevc_cmd", spy)
    return seen


def test_every_encoder_restart_asks_for_a_fresh_source(fake_cam_avtalk, cfg, monkeypatch):
    seen = _record_sources(monkeypatch)
    tokens = iter(range(100))
    scr = Screen(cfg)
    try:
        scr.show("http://ha/api/camera_proxy_stream/camera.cua?token=STALE", after="hold", owner="cam", live=True,
                 refresh=lambda: "lavfi:testsrc=size=160x%d:rate=10" % (120 + next(tokens)))
        for _ in range(3):                                        # camera màn hình rút nguồn / phiên dựng lại: ffmpeg chạy lại
            scr._cur_key = None
            scr._apply()
        assert len(seen) == 4 and len(set(seen)) == 4              # mỗi lần một địa chỉ mới
        assert not any("STALE" in s for s in seen)                 # địa chỉ cũ không bao giờ được dùng lại
    finally:
        scr.off()


def test_default_screen_refreshes_too_and_status_stays_json(fake_cam_avtalk, cfg, monkeypatch):
    seen = _record_sources(monkeypatch)
    scr = Screen(cfg)
    try:
        scr.set_default("live", "http://ha/api/camera_proxy_stream/camera.cua?token=STALE",
                        refresh=lambda: "lavfi:testsrc=size=160x120:rate=10")
        scr._cur_key = None
        scr._apply()
        assert seen == ["lavfi:testsrc=size=160x120:rate=10"] * 2
        json.dumps(scr.status())                                   # hàm làm mới không lọt ra trạng thái
    finally:
        scr.set_default("off")
        scr.off()


def test_a_failing_refresh_falls_back_to_the_old_source_instead_of_breaking(fake_cam_avtalk, cfg, monkeypatch):
    seen = _record_sources(monkeypatch)

    def boom():
        raise TimeoutError("HA chưa trả lời")
    scr = Screen(cfg)
    try:
        scr.show("lavfi:testsrc=size=160x120:rate=10", after="hold", owner="cam", refresh=boom)
        assert seen == ["lavfi:testsrc=size=160x120:rate=10"] and scr.status()["mode"] == "video"
    finally:
        scr.off()
