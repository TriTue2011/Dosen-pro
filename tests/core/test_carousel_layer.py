"""Lớp luân phiên nằm DƯỚI mọi lớp sự kiện; show_clock hiện đồng hồ như một lớp."""

import time

import pytest
from conftest import _HAS_X265
from dosen_core.screen import CAROUSEL_OWNER, Screen

pytestmark = pytest.mark.skipif(not _HAS_X265, reason="cần ffmpeg có libx265")


def _wait(cond, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_carousel_layer_sits_under_events_and_returns_when_they_end(fake_cam_avtalk, cfg):
    scr = Screen(cfg)
    try:
        scr.show_text("Chuông cửa", ["Có khách"], owner="chuong", after="hold")           # sự kiện đang hiện
        scr.show_clock(owner=CAROUSEL_OWNER, ttl=60)                                      # luân phiên đẩy trang sau
        owners = [l["owner"] for l in scr.status()["layers"]]
        assert owners == [CAROUSEL_OWNER, "chuong"]                                       # luân phiên ở ĐÁY
        assert scr.status()["mode"] == "text"                                             # vẫn hiện sự kiện, không bị luân phiên đè
        scr.show_clock(owner=CAROUSEL_OWNER, ttl=60)                                      # trang kế: thay lớp cũ, vẫn ở đáy
        assert [l["owner"] for l in scr.status()["layers"]] == [CAROUSEL_OWNER, "chuong"]
        scr.release("chuong")
        assert _wait(lambda: scr.status()["mode"] == "clock")                             # hết sự kiện: trang luân phiên hiện lại
    finally:
        scr.off()
        scr.close()
