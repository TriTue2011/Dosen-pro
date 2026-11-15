"""Xoay hình và kiểu khung (vừa khung / lấp đầy) của màn hình camera: kiểm trên điểm ảnh thật sau khi mã hoá."""

import subprocess

import pytest

from conftest import _HAS_X265
from dosen_core.screen import Screen, build_hevc_cmd

W, H, FPS = 240, 320, 10
# Nửa trái đỏ, nửa phải xanh dương: nhìn màu ở vài điểm là biết hình bị xoay / cắt thế nào.
_RED_BLUE = ("lavfi:nullsrc=s=320x100:r=10:d=1,"
             "geq=r='if(lt(X,160),255,0)':g=0:b='if(lt(X,160),0,255)'")

needs_x265 = pytest.mark.skipif(not _HAS_X265, reason="cần ffmpeg có libx265")


def _first_frame_rgb(rotate, fit):
    """Mã hoá nguồn đỏ|xanh bằng đúng lệnh màn hình dùng, giải mã khung đầu về RGB: bytes W*H*3."""
    enc = subprocess.run(build_hevc_cmd(_RED_BLUE, W, H, FPS, rotate=rotate, fit=fit),
                         capture_output=True, timeout=60)
    assert enc.returncode == 0, enc.stderr.decode()
    dec = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "hevc", "-i", "pipe:0",
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         input=enc.stdout, capture_output=True, timeout=60)
    assert len(dec.stdout) == W * H * 3, dec.stderr.decode()
    return dec.stdout


def _px(frame, x, y):
    i = (y * W + x) * 3
    return frame[i], frame[i + 1], frame[i + 2]


def _is(px, colour):
    r, _, b = px
    return (r > 150 and b < 100) if colour == "red" else (b > 150 and r < 100) if colour == "blue" else (r + b < 60)


@needs_x265
def test_no_rotation_contain_keeps_left_red_right_blue_with_black_bars():
    f = _first_frame_rgb(0, "contain")
    assert _is(_px(f, 40, 160), "red") and _is(_px(f, 200, 160), "blue")
    assert _is(_px(f, 120, 8), "black") and _is(_px(f, 120, 312), "black")          # viền đen trên/dưới


@needs_x265
def test_rotate_90_turns_the_picture_clockwise_left_goes_to_top():
    f = _first_frame_rgb(90, "contain")
    assert _is(_px(f, 120, 60), "red") and _is(_px(f, 120, 260), "blue")             # trái → trên, phải → dưới
    assert _is(_px(f, 8, 160), "black") and _is(_px(f, 232, 160), "black")           # viền hai bên


@needs_x265
def test_rotate_270_is_the_opposite_of_90_and_180_swaps_left_right():
    f270 = _first_frame_rgb(270, "contain")
    assert _is(_px(f270, 120, 60), "blue") and _is(_px(f270, 120, 260), "red")
    f180 = _first_frame_rgb(180, "contain")
    assert _is(_px(f180, 40, 160), "blue") and _is(_px(f180, 200, 160), "red")


@needs_x265
def test_cover_fills_the_whole_screen_without_bars():
    f = _first_frame_rgb(0, "cover")
    assert not _is(_px(f, 120, 8), "black") and not _is(_px(f, 120, 312), "black")
    assert not _is(_px(f, 8, 160), "black") and not _is(_px(f, 232, 160), "black")
    f90 = _first_frame_rgb(90, "cover")                                              # hình ngang xoay dọc lấp đầy màn dọc
    assert _is(_px(f90, 120, 60), "red") and _is(_px(f90, 120, 260), "blue")
    assert not _is(_px(f90, 8, 160), "black")


def test_bad_rotate_or_fit_is_refused(cfg):
    for kw in ({"rotate": 45}, {"rotate": True}, {"rotate": "90"}, {"fit": "stretch"}):
        with pytest.raises(ValueError):
            build_hevc_cmd("x.mp4", W, H, FPS, **kw)
    scr = Screen(cfg)
    try:
        with pytest.raises(ValueError):
            scr.set_default("live", "rtsp://x", fit="zoom")
        with pytest.raises(ValueError):
            scr.set_view(rotate=91)
        assert scr.status()["view"] == {"rotate": 0, "fit": "contain"}
    finally:
        scr.close()


def test_rotation_applies_only_to_the_default_screen_not_to_event_layers(cfg):
    scr = Screen(cfg)
    try:
        scr.view.update(rotate=90, fit="cover")
        assert scr._view_of({"owner": "default", "mode": "live"}) == (90, "cover")
        assert scr._view_of({"owner": "default", "mode": "image"}) == (90, "cover")
        assert scr._view_of({"owner": "default", "mode": "clock"}) == (0, "contain")
        assert scr._view_of({"owner": "zalo_call", "mode": "video"}) == (0, "contain")       # sự kiện: nguyên trạng
        assert scr._view_of({"owner": "chuong", "mode": "live"}) == (0, "contain")
        assert scr._view_of({"owner": "assist", "mode": "text"}) == (0, "contain")
    finally:
        scr.close()


def _params(cmd):
    return cmd[cmd.index("-x265-params") + 1]


def test_standard_quality_uses_the_ultrafast_tools_and_a_small_frame_budget():
    """Mặc định: x265 ultrafast như bộ đã chạy tốt, GOP 4 s, bộ đệm VBV nhỏ để mỗi khung ≤ ~7,5 KB (camera đen với khung ≥ ~9,7 KB)."""
    cmd = build_hevc_cmd(_RED_BLUE, W, H, FPS)
    assert cmd[cmd.index("-preset") + 1] == "ultrafast"
    assert _params(cmd) == ("keyint=40:min-keyint=40:scenecut=0:bframes=0:slices=1:repeat-headers=1:aud=0:log-level=none:"
                            "vbv-maxrate=400:vbv-bufsize=48")
    assert "flags=lanczos" in cmd[cmd.index("-vf") + 1]


def test_higher_qualities_lower_crf_but_never_raise_the_bitrate_cap():
    """Tăng tốc độ bit tối đa làm khung phình to → camera đen. Các mức chỉ khác crf/preset; tốc độ bit tối đa luôn 400."""
    high, mx = build_hevc_cmd(_RED_BLUE, W, H, FPS, quality="high"), build_hevc_cmd(_RED_BLUE, W, H, FPS, quality="max")
    assert high[high.index("-preset") + 1] == "ultrafast"             # cùng công cụ mã hoá → chip giải mã camera đọc được
    assert "crf=20" in _params(high) and "vbv-maxrate=400" in _params(high) and "vbv-bufsize=48" in _params(high)
    assert mx[mx.index("-preset") + 1] == "slow" and "crf=18" in _params(mx) and "vbv-maxrate=400" in _params(mx)
    assert "vbv-bufsize=40" in _params(mx)
    with pytest.raises(ValueError):
        build_hevc_cmd(_RED_BLUE, W, H, FPS, quality="ultra")


def test_quality_setting_reencodes_and_is_validated(cfg):
    scr = Screen(cfg)
    try:
        assert scr.status()["quality"] == "standard"
        scr.set_quality("max")
        assert scr.status()["quality"] == "max"
        with pytest.raises(ValueError):
            scr.set_quality("hay-nhat")
        assert scr.status()["quality"] == "max"
    finally:
        scr.close()


@needs_x265
@pytest.mark.parametrize("quality", ["standard", "high", "max"])
@pytest.mark.parametrize("kind", ["clock", "motion"])
def test_no_frame_is_big_enough_to_blank_the_camera_screen(quality, kind, tmp_path):
    """Lỗi đo trên camera thật: khung ≥ ~9,7 KB (đồng hồ 20 KB, mức «Cao» 10–13 KB) làm màn đen. Đo kích thước TỪNG khung thật."""
    from datetime import datetime
    from dosen_core import avtalk
    from dosen_core.render import render_clock
    if kind == "clock":
        png = tmp_path / "clock.png"
        png.write_bytes(render_clock(datetime(2026, 11, 15, 13, 37), {"condition": "partlycloudy", "temp": 28, "humidity": 70,
                                                                      "high": 31, "low": 23}))
        src = str(png)
    else:
        src = "lavfi:testsrc2=size=800x448:rate=10"                           # nhiều chi tiết chuyển động
    out = subprocess.run(["timeout", "5"] + build_hevc_cmd(src, W, H, FPS, quality=quality), capture_output=True).stdout
    aus = list(avtalk.HevcSplitter().feed(out))
    assert len(aus) >= 20
    biggest = max(len(au) for _, au in aus)
    assert biggest <= 8000, f"khung lớn nhất {biggest} B (camera đen từ ~9,7 KB)"
