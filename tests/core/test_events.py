"""Unit + fake-camera tests for DVRIP alarm events (app/events.py).

No real camera is needed: socketpair stands in for the TCP connection
for framing tests, and tests/fake_cam.py (127.0.0.1:34567) plays the
camera for the session test -- it answers AlarmSet (1500) with Ret 100
and pushes a LocalAlarm + a VideoMotion AlarmInfo (1504).
"""

import json
import socket
import struct
import subprocess
import sys
import time

import pytest

from dosen_core import dvrip
from dosen_core.dvrip import DVRIPClient, parse_alarm_info
from dosen_core.events import AlarmSession


def _pair():
    a, b = socket.socketpair()
    client = DVRIPClient("127.0.0.1", 34567)
    client.sock = a
    return client, b


def _read_raw(sock: socket.socket, n: int) -> bytes:
    data = bytearray()
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        assert chunk, "peer closed the socket"
        data.extend(chunk)
    return bytes(data)


# -- parser ------------------------------------------------------------

def test_parse_alarm_info_nested():
    payload = (b'{"Name":"AlarmInfo","AlarmInfo":{"Event":"LocalAlarm",'
               b'"Channel":0,"StartTime":"2026-11-14 16:45:00"},'
               b'"SessionID":"0x00000001"}\n\x00')
    info = parse_alarm_info(payload)
    assert info == {"Event": "LocalAlarm", "Channel": 0,
                    "StartTime": "2026-11-14 16:45:00"}


def test_parse_alarm_info_flat_and_invalid():
    payload = b'{"Event":"VideoMotion","Channel":0}\n\x00'
    assert parse_alarm_info(payload) == {"Event": "VideoMotion",
                                         "Channel": 0}
    assert parse_alarm_info(b"not json\n\x00") is None
    assert parse_alarm_info(b"[1,2]\n\x00") is None


def test_alarm_subscribe_frame():
    client, peer = _pair()
    try:
        client.session = 0x12345678
        client.alarm_subscribe()
        raw = _read_raw(peer, 20)
        opcode = struct.unpack_from("<H", raw, 14)[0]
        length = struct.unpack_from("<I", raw, 16)[0]
        assert opcode == 1500
        body = json.loads(_read_raw(peer, length)[:-2])
        assert body["SessionID"] == "0x12345678"
        assert body["Name"] == ""  # python-dvr get_command("", AlarmSet)
    finally:
        peer.close()
        client.sock.close()


# -- session against the fake camera ------------------------------------



def test_alarm_session_receives_fake_events(fake_cam, cfg):
    session = AlarmSession(cfg)
    try:
        session.start()
        deadline = time.time() + 5
        while session.events_received < 2 and time.time() < deadline:
            time.sleep(0.05)
        assert session.events_received == 2
        events = session.recent(10)
        assert [e["event"] for e in events] == ["LocalAlarm",
                                                "VideoMotion"]
        # Classification: LocalAlarm is the inferred button press,
        # VideoMotion is not -- and the raw payload is always kept.
        assert events[0]["possible_button_press"] is True
        assert events[0]["button_press_note"] is not None
        assert events[0]["alarm"]["Channel"] == 0
        assert events[1]["possible_button_press"] is False
        assert events[1]["button_press_note"] is None
        status = session.status()
        assert status["running"] is True
        assert status["last_event"]["event"] == "VideoMotion"
    finally:
        session.stop()
    assert session.running is False
    assert session.last_error is None  # clean stop, not a camera error


def test_read_chunk_survives_timeout_in_the_middle_of_a_chunk():
    """Lỗi A: timeout 1 s rơi giữa một gói làm mất các byte đã đọc, gói
    kế tiếp bị đọc lệch và phiên chết với «bad chunk header»."""
    client, peer = _pair()
    try:
        client.sock.settimeout(0.2)
        payload = b'{"Event":"LocalAlarm"}\n\x00'
        raw = struct.pack("<BB2xII2xHI", 0xFF, 0x00, 1, 0, 1504,
                          len(payload)) + payload
        peer.sendall(raw[:10])           # nửa tiêu đề
        with pytest.raises(socket.timeout):
            client.read_chunk()
        peer.sendall(raw[10:25])         # hết tiêu đề + một phần dữ liệu
        with pytest.raises(socket.timeout):
            client.read_chunk()
        peer.sendall(raw[25:])           # phần còn lại
        opcode, got = client.read_chunk()
        assert opcode == 1504
        assert got == payload
    finally:
        peer.close()
        client.sock.close()


def test_concurrent_start_opens_one_connection(fake_cam, monkeypatch, cfg):
    """Lỗi B: hai POST /api/events/start cùng lúc cùng qua `if running`
    rồi mỗi bên mở một kết nối, một luồng đọc bị bỏ rơi."""
    import threading
    connects = []
    real_connect = DVRIPClient.connect

    def counting_connect(self):
        connects.append(1)
        time.sleep(0.2)                  # kéo dài cửa sổ tranh chấp
        return real_connect(self)
    monkeypatch.setattr(DVRIPClient, "connect", counting_connect)
    session = AlarmSession(cfg)
    try:
        threads = [threading.Thread(target=session.start) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert len(connects) == 1
    finally:
        session.stop()


def test_session_reconnects_after_camera_drops_connection(fake_cam,
                                                          monkeypatch, cfg):
    """Lỗi C: cam rớt kết nối thì bộ nghe chết im lặng, SSE kết thúc và
    không ai báo. Phải tự nối lại và đăng ký lại."""
    from dosen_core import events
    monkeypatch.setattr(events, "_RECONNECT_BASE_S", 0.05)
    monkeypatch.setattr(events, "_RECONNECT_MAX_S", 0.2)
    session = AlarmSession(cfg)
    try:
        session.start()
        deadline = time.time() + 5
        while session.events_received < 2 and time.time() < deadline:
            time.sleep(0.05)
        assert session.events_received == 2
        session.client.sock.close()      # giả lập rớt mạng
        deadline = time.time() + 8
        while session.events_received < 4 and time.time() < deadline:
            time.sleep(0.05)
        assert session.events_received == 4   # fake cam đẩy lại 2 event
        assert session.running is True
        assert session.reconnects >= 1
    finally:
        session.stop()


def test_stop_during_reconnect_backoff_is_prompt(fake_cam, monkeypatch, cfg):
    """Dừng khi đang chờ nối lại (cam không còn tới được) không được treo."""
    from dosen_core import events
    monkeypatch.setattr(events, "_RECONNECT_BASE_S", 30.0)
    monkeypatch.setattr(events, "_RECONNECT_MAX_S", 30.0)
    session = AlarmSession(cfg)
    session.start()
    session.client.sock.close()                  # rớt kết nối
    deadline = time.time() + 5
    while session.connected and time.time() < deadline:
        time.sleep(0.05)
    assert session.connected is False            # đang nằm trong backoff 30 s
    assert session.running is True
    t0 = time.time()
    session.stop()
    assert time.time() - t0 < 2
    assert session.running is False


def test_only_start_of_localalarm_counts_as_possible_press(cfg):
    """Lỗi D: Start và Stop của cùng một lần bấm đều bị gắn cờ, một lần
    bấm thành hai «lần bấm»."""
    session = AlarmSession(cfg)
    session._record({"Event": "LocalAlarm", "Status": "Start"})
    session._record({"Event": "LocalAlarm", "Status": "Stop"})
    session._record({"Event": "LocalAlarm"})        # cam không ghi Status
    flags = [e["possible_button_press"] for e in session.recent(10)]
    assert flags == [True, False, True]


def test_mic_stop_does_not_leave_a_traceback_in_the_reader_thread(fake_cam, cfg):
    """Dừng mic đóng socket dưới chân luồng đọc: OSError «Bad file
    descriptor» không được văng ra thành traceback (thấy trên cam thật)."""
    import threading
    from dosen_core.audio import MicSession
    errors = []
    old_hook = threading.excepthook
    threading.excepthook = lambda args: errors.append(args.exc_value)
    mic = MicSession(cfg)
    try:
        mic.start()
        time.sleep(0.5)                  # để luồng đọc đang chờ gói
        mic.stop()
        time.sleep(0.3)
    finally:
        threading.excepthook = old_hook
    assert errors == []
    assert mic.last_error is None        # dừng chủ động, không phải lỗi cam


