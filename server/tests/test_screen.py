"""Screen (đổi nguồn trên màn camera) và Speaker (phát ra loa), với camera giả."""

import os
import time

import pytest

from conftest import _HAS_X265
from app.audio import TalkSession
from app.screen import Screen
from app.speaker import Speaker

pytestmark = pytest.mark.skipif(not _HAS_X265, reason="cần ffmpeg có libx265 (ảnh Docker)")


def _media(log):
    return [l.split() for l in open(log).read().splitlines() if l.startswith("media ")]


def _wait(cond, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_text_screen_then_switch_sends_a_new_key_frame_then_off(fake_cam_avtalk):
    scr = Screen()
    try:
        scr.show_text("Trợ lý", [{"role": "user", "text": "Bật đèn"}])
        assert _wait(lambda: scr.status()["sender"]["key_packets"] >= 1)
        st = scr.status()
        assert st["mode"] == "text" and st["open"] is True and st["switches"] == 1
        scr.show_text("Trợ lý", [{"role": "user", "text": "Bật đèn"},
                                 {"role": "assistant", "text": "Đã bật."}])
        assert _wait(lambda: scr.status()["sender"]["key_packets"] >= 2)   # khung khoá mới khi đổi nguồn
        assert scr.status()["switches"] == 2
        kinds = [m[1][:8] for m in _media(fake_cam_avtalk)]
        assert kinds.count("000001fc") >= 2
    finally:
        scr.off()
    assert scr.status()["open"] is False and scr.status()["mode"] == "off"


def test_video_that_ends_closes_the_screen_unless_hold(fake_cam_avtalk):
    scr = Screen()
    try:
        scr.show("lavfi:testsrc=size=160x120:rate=10:duration=1.5", after="off")
        assert _wait(lambda: scr.status()["mode"] == "off", 25)           # hết video thì đóng
        scr.show("lavfi:testsrc=size=160x120:rate=10:duration=1.5", after="hold")
        assert _wait(lambda: scr.status()["sender"] and scr.status()["sender"]["video_packets"] >= 10)
        time.sleep(3)
        assert scr.status()["open"] is True and scr.status()["mode"] == "video"   # giữ khung cuối
    finally:
        scr.off()


def test_bad_source_reports_error_and_closes(fake_cam_avtalk):
    scr = Screen()
    try:
        scr.show("/khong/co/tep.mp4")
        assert _wait(lambda: scr.status()["mode"] == "off")
        assert scr.status()["last_error"]
    finally:
        scr.off()


def test_speaker_plays_audio_to_the_talk_channel(fake_cam_avtalk):
    talk = TalkSession()
    spk = Speaker(talk)
    try:
        spk.play("lavfi:sine=frequency=440:duration=1.2", volume=0.5)
        assert _wait(lambda: spk.status()["bytes_sent"] > 4000)
        assert _wait(lambda: spk.status()["playing"] is False)
        assert spk.status()["last_error"] is None
        assert os.path.getsize("/tmp/fake-cam-talk.bin") > 4000
    finally:
        spk.stop()
    assert talk.running is False
