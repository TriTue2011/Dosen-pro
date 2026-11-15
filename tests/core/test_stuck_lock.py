"""Mất phiên tới camera khi ffmpeg đang chạy KHÔNG được làm màn kẹt vĩnh viễn (15/11: rút nguồn camera rồi automation không chiếu được).

Gốc: luồng đọc ffmpeg thoát sớm khi mất phiên (ffmpeg vẫn sống) rồi gọi dọn dẹp, dọn dẹp đọc ``stderr`` TRONG khoá → chặn mãi → ``show`` và vòng nền chờ khoá mãi."""

import threading
import time

from dosen_core.screen import Screen

CAM = "lavfi:testsrc=size=160x120:rate=10"


def _wait(cond, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_losing_the_session_while_ffmpeg_runs_never_holds_the_lock_forever(fake_cam_avtalk, cfg):
    scr = Screen(cfg)
    scr.load()
    try:
        scr.show(CAM, after="hold", owner="ev")
        assert _wait(lambda: scr.status()["sender"] and scr.status()["sender"]["video_packets"] > 3)
        with scr._lock:                                  # như một lần mở lại phiên thất bại: phiên đã đóng, ffmpeg cũ chưa bị dừng
            scr._close_session()
        time.sleep(1.5)                                  # luồng đọc gặp «chưa có phiên»
        assert scr._lock.acquire(timeout=3), "khoá bị giữ mãi (luồng đọc ffmpeg chặn khi dọn dẹp)"
        scr._lock.release()
        done = []
        t = threading.Thread(target=lambda: (scr.show("lavfi:testsrc2=size=160x120:rate=10", after="hold", owner="ev2"), done.append(1)), daemon=True)
        t.start()
        t.join(30)
        assert done, "show() không trở về: màn kẹt sau khi mất phiên"
        assert _wait(lambda: scr.status()["open"] and scr.status()["sender"]["video_packets"] > 3)   # phiên mở lại, hình ra tiếp
    finally:
        scr.off()
        scr.close()


def test_reshowing_the_same_layer_reopens_a_closed_session(fake_cam_avtalk, cfg):
    scr = Screen(cfg)
    try:
        scr.show(CAM, after="hold", owner="ev")
        assert _wait(lambda: scr.status()["open"])
        with scr._lock:
            scr._close_session()
        assert scr.status()["open"] is False
        scr.show(CAM, after="hold", owner="ev")          # cùng nguồn, cùng lớp: vẫn phải mở lại phiên (trước đây trả về sớm vì «đang hiện rồi»)
        assert _wait(lambda: scr.status()["open"])
    finally:
        scr.off()
        scr.close()
