"""Xếp lớp theo MỨC ƯU TIÊN, chia đôi màn cho camera, xoay camera khi có hơn hai, và không dựng lại mãi khi một camera chết."""

import subprocess
import time

import pytest
from conftest import _HAS_X265
from dosen_core import avtalk, screen as screen_mod
from dosen_core.screen import Screen, build_split_cmd

needs_x265 = pytest.mark.skipif(not _HAS_X265, reason="cần ffmpeg có libx265")
CAM = "lavfi:testsrc2=size=320x180:rate=10"


def _wait(cond, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def _shown(scr):
    layer = scr._cur_layer
    if layer is None:
        return None
    return tuple(m["owner"] for m in layer["members"]) if layer.get("members") else layer["owner"]


def test_higher_priority_stays_on_top_and_lower_waits_below(fake_cam_avtalk, cfg):
    scr = Screen(cfg)
    try:
        scr.show_text("Cháy", ["khói ở bếp"], owner="chay", priority=4)
        scr.show_text("Chuông", ["có khách"], owner="chuong", priority=2)            # tới sau nhưng thấp hơn: không đè
        assert _shown(scr) == "chay"
        scr.show_text("Người lạ", ["ngoài cổng"], owner="nguoila", priority=3)
        assert _shown(scr) == "chay"
        scr.release("chay")
        assert _shown(scr) == "nguoila"                                              # mức 3 trước mức 2
        scr.release("nguoila")
        assert _shown(scr) == "chuong"
    finally:
        scr.off()


def test_same_priority_newest_wins_then_the_earlier_layer_is_restored(fake_cam_avtalk, cfg):
    scr = Screen(cfg)
    try:
        scr.show("lavfi:testsrc=size=160x120:rate=10", after="hold", owner="zalo_call_video")      # như hình cuộc gọi Zalo
        scr.show(CAM, after="hold", owner="cam_cua", ttl=60)
        assert _shown(scr) == "cam_cua"                                              # sự kiện cùng mức: phát luôn
        scr.release("cam_cua")
        assert _shown(scr) == "zalo_call_video"                                      # rồi khôi phục cuộc gọi
    finally:
        scr.off()


def test_carousel_is_always_below_every_event(fake_cam_avtalk, cfg):
    scr = Screen(cfg)
    try:
        scr.show_text("Thẻ", ["một"], owner=screen_mod.CAROUSEL_OWNER, priority=4)      # dù xin mức cao: luân phiên luôn mức 0
        scr.show_text("Tin", ["nhỏ"], owner="tin", priority=1)
        assert _shown(scr) == "tin"
        scr.release("tin")
        assert _shown(scr) == screen_mod.CAROUSEL_OWNER
        assert [l["priority"] for l in scr.status()["layers"]] == [0]
    finally:
        scr.off()


def test_bad_priority_is_refused(fake_cam_avtalk, cfg):
    scr = Screen(cfg)
    try:
        for bad in (0, 5, "2"):
            with pytest.raises(ValueError):
                scr.show_text("x", ["y"], owner="o", priority=bad)
    finally:
        scr.off()


def test_two_split_cameras_share_the_screen_and_a_third_does_not_join_a_lower_priority_one(fake_cam_avtalk, cfg):
    scr = Screen(cfg)
    try:
        scr.show(CAM, after="hold", owner="cam_a", split=True)
        assert _shown(scr) == "cam_a"                                                # một camera: toàn màn, không ghép
        scr.show(CAM, after="hold", owner="cam_b", split=True)
        assert _shown(scr) == ("cam_a", "cam_b") and scr.status()["mode"] == "live"
        assert _wait(lambda: scr.status()["sender"]["video_packets"] >= 5)           # khung ghép thật sự ra tới camera
        scr.show(CAM, after="hold", owner="cam_thap", split=True, priority=1)         # mức thấp hơn: không chen vào
        assert _shown(scr) == ("cam_a", "cam_b")
        scr.release("cam_b")
        assert _shown(scr) == "cam_a"
    finally:
        scr.off()


def test_a_split_camera_does_not_merge_with_a_non_split_layer(fake_cam_avtalk, cfg):
    scr = Screen(cfg)
    try:
        scr.show("lavfi:testsrc=size=160x120:rate=10", after="hold", owner="zalo_call_video")
        scr.show(CAM, after="hold", owner="cam_a", split=True)
        assert _shown(scr) == "cam_a"
    finally:
        scr.off()


def test_more_than_two_cameras_take_turns_every_few_seconds(fake_cam_avtalk, cfg, monkeypatch):
    monkeypatch.setattr(screen_mod, "SPLIT_ROTATE_S", 1.0)
    scr = Screen(cfg)
    scr.load()
    try:
        for name in ("c1", "c2", "c3"):
            scr.show(CAM, after="hold", owner=name, split=True, ttl=60)
        seen = {_shown(scr)}
        end = time.time() + 7
        while time.time() < end:
            seen.add(_shown(scr))
            time.sleep(0.2)
        owners = {o for pair in seen if isinstance(pair, tuple) for o in pair}
        assert owners == {"c1", "c2", "c3"} and len(seen) >= 3                         # camera nào cũng được lên màn, không kẹt một cặp
    finally:
        scr.off()


def test_a_dead_camera_in_the_split_does_not_cause_an_endless_rebuild_loop(fake_cam_avtalk, cfg):
    scr = Screen(cfg)
    scr.load()
    try:
        scr.show(CAM, after="hold", owner="tot", split=True)
        scr.show("lavfi:khong_co_nguon_nay", after="hold", owner="hong", split=True)
        assert _wait(lambda: [l["owner"] for l in scr.status()["layers"]] == ["tot"], 20)    # camera hỏng bị gỡ, camera tốt còn lại
        assert _wait(lambda: _shown(scr) == "tot", 10)
        before = scr.status()["switches"]
        time.sleep(3)
        assert scr.status()["switches"] == before and before <= 6                    # đã yên: không dựng lại liên tục
    finally:
        scr.off()


def test_split_command_stacks_the_cells_and_ends_when_any_source_dies():
    cmd = build_split_cmd(["rtsp://a/x", "rtsp://b/y"], 240, 320, 10)
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "vstack=inputs=2:shortest=1" in fc and cmd.count("-i") == 2 and "pad=240:160" in fc
    assert "-re" not in cmd and "keyint=10:min-keyint=10" in cmd[cmd.index("-x265-params") + 1]
    with pytest.raises(ValueError):
        build_split_cmd(["rtsp://a/x"], 240, 320, 10)
    with pytest.raises(ValueError):
        build_split_cmd(["rtsp://a/x", "rtsp://b/x", "rtsp://c/x"], 240, 320, 10)


@needs_x265
@pytest.mark.parametrize("quality", ["standard", "high", "max"])
def test_split_frames_stay_under_the_size_that_blacks_out_the_lcd(quality):
    """Gốc lỗi đen màn trước đây là khung ≥ ~9,7 KB; cảnh ghép hai camera nhiều chi tiết hơn nên phải đo lại ở MỌI mức chất lượng."""
    detail = "lavfi:mandelbrot=size=640x360:rate=10"
    noisy = "lavfi:testsrc2=size=640x360:rate=10,noise=alls=30:allf=t"
    cmd = build_split_cmd([detail, noisy], 240, 320, 10, quality=quality)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    sizes, splitter, end = [], avtalk.HevcSplitter(), time.time() + 40
    try:
        while len(sizes) < 40 and time.time() < end:
            chunk = p.stdout.read1(65536)
            if not chunk:
                break
            sizes += [len(au) for _, au in splitter.feed(chunk)]
    finally:
        p.kill()
        p.wait()
    assert len(sizes) >= 20, "ffmpeg không ra đủ khung"
    print("KHUNG", quality, len(sizes), "khung, lớn nhất", max(sizes), "byte, TB", sum(sizes) // len(sizes))
    assert max(sizes) < 9000, f"{quality}: khung lớn nhất {max(sizes)} byte"
