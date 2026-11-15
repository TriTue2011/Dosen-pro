"""Screen (đổi nguồn trên màn camera) và Speaker (phát ra loa), với camera giả."""

import os
import time

import pytest

from conftest import _HAS_X265
from dosen_core.audio import TalkSession
from dosen_core.screen import Screen
from dosen_core.speaker import Speaker

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


def test_text_screen_then_switch_sends_a_new_key_frame_then_off(fake_cam_avtalk, cfg):
    scr = Screen(cfg)
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


def test_video_that_ends_closes_the_screen_unless_hold(fake_cam_avtalk, cfg):
    scr = Screen(cfg)
    try:
        scr.show("lavfi:testsrc=size=160x120:rate=10:duration=1.5", after="off")
        assert _wait(lambda: scr.status()["mode"] == "off", 25)           # hết video thì đóng
        scr.show("lavfi:testsrc=size=160x120:rate=10:duration=1.5", after="hold")
        assert _wait(lambda: scr.status()["sender"] and scr.status()["sender"]["video_packets"] >= 10)
        time.sleep(3)
        assert scr.status()["open"] is True and scr.status()["mode"] == "video"   # giữ khung cuối
    finally:
        scr.off()


def test_bad_source_reports_error_and_closes(fake_cam_avtalk, cfg):
    scr = Screen(cfg)
    try:
        scr.show("/khong/co/tep.mp4")
        assert _wait(lambda: scr.status()["mode"] == "off")
        assert scr.status()["last_error"]
    finally:
        scr.off()


def test_speaker_plays_audio_to_the_talk_channel(fake_cam_avtalk, cfg):
    talk = TalkSession(cfg)
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


def test_speaker_uses_the_avtalk_audio_channel_while_the_screen_is_open(fake_cam_avtalk, cfg):
    """Màn đang mở thì camera giữ loa cho AVTalk (OPTalk bị Ret 503): tiếng phải đi theo AVTalk."""
    talk = TalkSession(cfg)
    scr = Screen(cfg)
    spk = Speaker(talk, scr)
    try:
        if os.path.exists("/tmp/fake-cam-talk.bin"):
            os.remove("/tmp/fake-cam-talk.bin")
        scr.show_text("t", ["đang hiện"])
        assert _wait(lambda: scr.status()["sender"]["key_packets"] >= 1)
        spk.play("lavfi:sine=frequency=440:duration=1.5")
        assert spk.status()["route"] == "avtalk"
        assert _wait(lambda: spk.status()["playing"] is False, 25)
        assert spk.status()["bytes_sent"] >= 8000 and spk.status()["last_error"] is None
        audio = [m for m in _media(fake_cam_avtalk) if m[1].startswith("000001fa")]
        loud = [m for m in audio if m[1][16:] != "d5" * 8]              # không phải âm im lặng
        assert len(loud) >= 20
        assert not os.path.exists("/tmp/fake-cam-talk.bin")             # không mở kênh OPTalk
    finally:
        spk.stop()
        scr.off()


def test_screen_sleep_get_and_set(fake_cam_avtalk, cfg):
    scr = Screen(cfg)
    first = scr.get_sleep()
    assert first == {"work_mode": "Timing", "work_time": 30, "luminance": 60}
    out = scr.set_sleep(0)                                   # 0 = không bao giờ tắt (như app: «Never»)
    assert out["work_time"] == 0 and out["work_mode"] == "Timing" and out["luminance"] == 60
    out = scr.set_sleep(luminance=80)                        # chỉ đổi độ sáng: thời gian giữ nguyên
    assert out == {"work_mode": "Timing", "work_time": 0, "luminance": 80}
    for bad in (-1, 3601, "5", True):
        with pytest.raises(ValueError):
            scr.set_sleep(bad)
    for bad in (0, 101, "9"):
        with pytest.raises(ValueError):
            scr.set_sleep(luminance=bad)
    with pytest.raises(ValueError):
        scr.set_sleep()


def test_screen_open_wakes_the_lcd_and_power_api_works(fake_cam_avtalk, cfg):
    scr = Screen(cfg)
    try:
        scr.show_text("t", ["x"])
        assert _wait(lambda: any(l.startswith("power ") for l in open(fake_cam_avtalk).read().splitlines()))
        lines = open(fake_cam_avtalk).read().splitlines()
        assert 'power {"ScreenStatus": true}' in lines          # tự bật màn khi mở phiên hiển thị
        i_power = next(i for i, l in enumerate(lines) if l.startswith("power "))
        i_media = next(i for i, l in enumerate(lines) if l.startswith("media "))
        assert i_power < i_media                                 # bật trước khi gửi hình
        assert scr.power(False) == {"screen_power": False, "ret": 100}
        assert scr.power(True)["screen_power"] is True
        with pytest.raises(ValueError):
            scr.power("on")
    finally:
        scr.off()


def test_speaker_plays_uploaded_wav_bytes(fake_cam_avtalk, cfg):
    import io
    import math
    import struct
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 6))) for i in range(8000)))   # 1 s
    talk = TalkSession(cfg)
    spk = Speaker(talk)
    try:
        spk.play_bytes(buf.getvalue())
        assert _wait(lambda: spk.status()["bytes_sent"] > 4000)
        assert _wait(lambda: spk.status()["playing"] is False, 25)
        assert spk.status()["last_error"] is None
        with pytest.raises(ValueError):
            spk.play_bytes(b"")
    finally:
        spk.stop()


# -- lớp sự kiện và màn hình mặc định ------------------------------------------------

def _screen(cfg):
    return Screen(cfg)


def test_newest_event_layer_wins_and_release_goes_back_down(fake_cam_avtalk, cfg):
    scr = _screen(cfg)
    try:
        scr.show("lavfi:testsrc=size=160x120:rate=10", after="hold", owner="goi_zalo")
        scr.show_text("Chuông cửa", ["có người bấm chuông"], owner="chuong")
        st = scr.status()
        assert st["mode"] == "text" and [l["owner"] for l in st["layers"]] == ["goi_zalo", "chuong"]
        scr.show_text("Chuông cửa", ["lần hai"], owner="chuong")            # cùng owner: thay chứ không chồng
        assert len(scr.status()["layers"]) == 2
        scr.release("chuong")
        st = scr.status()
        assert st["mode"] == "video" and [l["owner"] for l in st["layers"]] == ["goi_zalo"]   # về lớp bên dưới
        scr.release("khong_co")                                              # không lỗi khi không có
        scr.release("goi_zalo")
        assert scr.status()["mode"] == "off" and scr.status()["open"] is False   # hết lớp, mặc định tắt
    finally:
        scr.off()


def test_layer_ttl_returns_to_the_default_clock_and_default_is_saved(fake_cam_avtalk, cfg):
    scr = _screen(cfg)
    scr.load()
    try:
        assert scr.set_default("clock")["mode"] == "clock"
        assert scr.status()["open"] is True
        scr.show_text("Tin", ["ngắn thôi"], owner="tin", ttl=2)
        assert scr.status()["mode"] == "text" and scr.status()["layers"][0]["ttl_left"] is not None
        assert _wait(lambda: scr.status()["mode"] == "clock", 15)             # hết ttl: về đồng hồ
        assert scr.status()["layers"] == []
    finally:
        scr.set_default("off")
        scr.off()


def test_default_validation_and_live_needs_a_source(fake_cam_avtalk, cfg):
    scr = _screen(cfg)
    for bad in (("rác", ""), ("live", ""), ("image", ""), ("video", "")):
        with pytest.raises(ValueError):
            scr.set_default(*bad)
    with pytest.raises(ValueError):
        scr.show("x", owner="")
    with pytest.raises(ValueError):
        scr.show("x", ttl=0)
    assert scr.status()["layers"] == []          # lỗi tham số không để lại lớp nào


def test_clock_render():
    from datetime import datetime
    from dosen_core.render import render_clock
    png = render_clock(datetime(2026, 11, 15, 9, 5))
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 1000


def test_live_stream_goes_to_the_talk_channel_and_a_new_play_cuts_it_off(fake_cam_avtalk, cfg):
    """Tiếng người bên kia (A-law 8 kHz) đẩy vào loa theo thời gian thật; phát nguồn khác thì luồng cũ hết hiệu lực."""
    talk = TalkSession(cfg)
    spk = Speaker(talk)
    try:
        if os.path.exists("/tmp/fake-cam-talk.bin"):
            os.remove("/tmp/fake-cam-talk.bin")
        st = spk.open_stream()
        assert spk.status()["playing"] is True
        for _ in range(10):
            st.feed(b"\xd5" * 320)
        assert _wait(lambda: os.path.exists("/tmp/fake-cam-talk.bin") and os.path.getsize("/tmp/fake-cam-talk.bin") >= 3200)
        assert spk.status()["route"] == "optalk" and spk.status()["bytes_sent"] == 3200
        spk.play("lavfi:sine=frequency=440:duration=0.3")                # nguồn khác đè lên: luồng cũ bị cắt
        n = st.bytes_fed
        st.feed(b"\xd5" * 320)
        assert st.closed is True and st.bytes_fed == n
        _wait(lambda: spk.status()["playing"] is False)
    finally:
        spk.stop()
    assert talk.running is False


def test_live_stream_switches_to_avtalk_when_the_screen_opens_mid_call(fake_cam_avtalk, cfg):
    talk = TalkSession(cfg)
    scr = Screen(cfg)
    spk = Speaker(talk, scr)
    try:
        st = spk.open_stream()
        st.feed(b"\xd5" * 320)
        assert spk.status()["route"] == "optalk" and talk.running is True
        scr.show_text("Zalo", ["đang gọi"])                              # cuộc gọi bật màn hình giữa chừng
        assert _wait(lambda: scr.audio_sink() is not None)
        st.feed(b"\xd5" * 320)
        assert spk.status()["route"] == "avtalk" and talk.running is False   # OPTalk đóng, AVTalk giữ loa
        assert _wait(lambda: scr.status()["sender"]["audio_packets"] >= 1)
        st.close()
        assert spk.status()["playing"] is False
    finally:
        spk.stop()
        scr.off()


def test_weather_on_the_clock_redraws_it_and_bad_input_is_refused(fake_cam_avtalk, cfg):
    scr = Screen(cfg)
    try:
        assert scr.set_default("clock")["mode"] == "clock"
        assert _wait(lambda: scr.status()["sender"] and scr.status()["sender"]["key_packets"] >= 1)
        n = scr.status()["switches"]
        scr.set_weather({"condition": "rainy", "temp": 24.0, "humidity": 90})
        assert _wait(lambda: scr.status()["switches"] == n + 1)                 # đổi thời tiết → vẽ lại ngay
        assert scr.status()["weather"]["condition"] == "rainy"
        scr.set_weather({"condition": "rainy", "temp": 24.0, "humidity": 90})   # y hệt → không vẽ lại
        time.sleep(0.5)
        assert scr.status()["switches"] == n + 1
        scr.set_weather(None)
        assert _wait(lambda: scr.status()["switches"] == n + 2) and scr.status()["weather"] is None
        for bad in ({}, {"temp": 3}, "mưa"):
            with pytest.raises(ValueError):
                scr.set_weather(bad)
    finally:
        scr.off()
        scr.close()


def _stubborn_ffmpeg(tmp_path):
    """ffmpeg giả: lờ tín hiệu dừng và ghi dữ liệu liên tục tới khi ống đầy — đúng cảnh ffmpeg kẹt đo được trong HA."""
    p = tmp_path / "ffmpeg-cung-dau"
    p.write_text("#!/usr/bin/env python3\nimport signal, sys\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                 "while True:\n    sys.stdout.buffer.write(b'\\0' * 65536)\n    sys.stdout.buffer.flush()\n")
    p.chmod(0o755)
    return str(p)


def test_switching_source_really_stops_the_old_encoder_even_if_it_ignores_sigterm(fake_cam_avtalk, cfg, tmp_path):
    """Lỗi đo trên máy thật: đổi nguồn chỉ terminate() → ffmpeg đang kẹt không tắt → dồn sáu bộ mã hoá, màn live đen."""
    cfg.ffmpeg = _stubborn_ffmpeg(tmp_path)
    scr = Screen(cfg)
    olds = []
    try:
        for i in range(4):
            scr.show(f"http://x/{i}.mp4", after="hold", owner=f"o{i}")        # mỗi lần đổi là một bộ mã hoá mới
            assert _wait(lambda: scr._proc is not None)
            olds.append(scr._proc)
            time.sleep(0.3)
        for old in olds[:-1]:
            assert _wait(lambda old=old: old.poll() is not None, 10), "bộ mã hoá cũ vẫn còn chạy"
        assert olds[-1].poll() is None                                          # bộ mới nhất vẫn chạy
    finally:
        scr.off()
        scr.close()
    assert _wait(lambda: olds[-1].poll() is not None, 10)                       # tắt màn thì bộ cuối cũng dừng, không để ma


# -- mất nguồn camera: phát hiện chết âm thầm, bộ nền không chết, bộ canh giữ gỡ kẹt ----------------------------------

def test_every_dvrip_socket_is_tuned_to_detect_a_dead_peer_in_seconds(fake_cam_avtalk, cfg):
    """Rút nguồn camera không gửi gói đóng: phải có keepalive TCP ngắn + hạn chờ ACK, và NODELAY để gói nhỏ đi ngay."""
    import socket
    from dosen_core.dvrip import DVRIPClient, KEEPALIVE_IDLE_S, USER_TIMEOUT_MS
    c = DVRIPClient(cfg.cam_ip, cfg.cam_port, 3, 3)
    c.connect()
    try:
        s = c.sock
        assert s.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) == 1
        assert s.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) == 1
        assert s.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE) == KEEPALIVE_IDLE_S
        assert s.getsockopt(socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT) == USER_TIMEOUT_MS
    finally:
        c.close()


def test_the_background_loop_survives_unexpected_errors_and_retries_fast(fake_cam_avtalk, cfg, monkeypatch):
    """Lỗi loại lạ (không phải OSError/DVRIPError) từng làm vòng nền chết im lặng → màn không bao giờ tự về."""
    import dosen_core.screen as sm
    monkeypatch.setattr(sm, "RETRY_FIRST_S", 1.0)
    scr = Screen(cfg)
    real = scr._apply_locked
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise ValueError("lỗi lạ")
        return real()
    monkeypatch.setattr(scr, "_apply_locked", flaky)
    try:
        scr.set_default("clock")
    except ValueError:
        pass                                                  # lần gọi trực tiếp đầu tiên ném ra cho người gọi (đúng)
    scr.load()
    try:
        assert _wait(lambda: scr.status()["mode"] == "clock", 30)        # vòng nền tự thử lại và lên được
        assert scr._ticker.is_alive() and scr.status()["last_error"] is None
    finally:
        scr.off()
        scr.close()


def test_watchdog_aborts_the_network_when_an_apply_is_stuck(fake_cam_avtalk, cfg, monkeypatch, caplog):
    import threading
    import dosen_core.screen as sm
    monkeypatch.setattr(sm, "WATCHDOG_STUCK_S", 0.5)
    monkeypatch.setattr(sm, "WATCHDOG_EVERY_S", 0.2)
    scr = Screen(cfg)
    release, entered = threading.Event(), threading.Event()
    aborted = []
    monkeypatch.setattr(scr, "_abort_network", lambda: (aborted.append(1), release.set()))

    def stuck():
        entered.set()
        release.wait(20)                                       # giả lập chờ mạng không hạn
        raise OSError("connection aborted")
    monkeypatch.setattr(scr, "_apply_locked", stuck)
    scr.load()
    t = threading.Thread(target=lambda: scr.show_text("t", ["x"], owner="o"), daemon=True)
    try:
        t.start()
        assert entered.wait(5)
        assert _wait(lambda: aborted, 10)                      # canh giữ phát hiện kẹt và đóng thô kết nối
        assert _wait(lambda: not t.is_alive(), 10)             # luồng kẹt được gỡ ra
        assert scr.status()["aborts"] >= 1
        assert any("kẹt" in r.getMessage() for r in caplog.records)     # có ghi ngăn xếp để biết đúng chỗ
    finally:
        release.set()
        scr._stopping = True
