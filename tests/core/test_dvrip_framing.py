"""Unit tests for DVRIP framing (app/dvrip.py) and AV-talk builders
(app/video_sender.py). No camera is needed: a socketpair stands in for
the TCP connection, and the expected bytes come from the go2rtc
pkg/dvrip source (client.go / backchannel.go / producer.go).
"""

import json
import socket
import struct

import pytest

from dosen_core import dvrip
from dosen_core.dvrip import DVRIPClient, DVRIPError


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


# -- SofiaHash ----------------------------------------------------------

def test_sofia_hash_known_vector():
    # md5("admin") = 21232f297a57a5a743894a0e4a801fc3; pairs summed mod 62
    # into "0123456789ABC...xyz" -> "6QNMIQGe".
    assert dvrip.sofia_hash("admin") == "6QNMIQGe"
    h = dvrip.sofia_hash("")
    assert len(h) == 8
    assert all(c in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
               "abcdefghijklmnopqrstuvwxyz" for c in h)


# -- command chunk framing ------------------------------------------------

def test_write_cmd_header():
    client, peer = _pair()
    try:
        client.write_cmd(1000, b'{"a":1}')
        raw = _read_raw(peer, 20 + 7)
        assert raw[0] == 0xFF
        assert struct.unpack_from("<I", raw, 4)[0] == 0  # session
        assert struct.unpack_from("<I", raw, 8)[0] == 0  # seq
        assert struct.unpack_from("<H", raw, 14)[0] == 1000
        assert struct.unpack_from("<I", raw, 16)[0] == 7
        assert raw[20:] == b'{"a":1}'
        # Sequence number increments.
        client.write_cmd(1006, b"{}")
        raw2 = _read_raw(peer, 20 + 2)
        assert struct.unpack_from("<I", raw2, 8)[0] == 1
    finally:
        peer.close()
        client.sock.close()


def test_read_chunk_roundtrip():
    client, peer = _pair()
    try:
        payload = b'{"Ret":100}' + b"\n\x00"
        header = struct.pack("<BB2xII2xHI", 0xFF, 0x00, 0x1234, 7,
                             1000, len(payload))
        peer.sendall(header + payload)
        opcode, data = client.read_chunk()
        assert opcode == 1000
        assert data == payload
        assert client.session == 0x1234
    finally:
        peer.close()
        client.sock.close()


def test_read_chunk_bad_header():
    client, peer = _pair()
    try:
        peer.sendall(b"\x00" * 20)
        with pytest.raises(DVRIPError):
            client.read_chunk()
    finally:
        peer.close()
        client.sock.close()


# -- media packets ---------------------------------------------------------

def _chunk(opcode: int, payload: bytes) -> bytes:
    header = struct.pack("<BB2xII2xHI", 0xFF, 0x00, 1, 0, opcode,
                         len(payload))
    return header + payload


def test_read_media_packet_audio():
    client, peer = _pair()
    try:
        audio = bytes(range(256)) + bytes(range(64))  # 320 bytes G.711
        assert len(audio) == 320
        # 00 00 01 FA | codec 14 (PCMA) | rate 2 (8000) | LE len | payload
        packet = b"\x00\x00\x01\xfa" + bytes([14, 2]) \
            + struct.pack("<H", 320) + audio
        peer.sendall(_chunk(1410, packet))
        ptype, payload, meta = client.read_media_packet()
        assert ptype == 0xFA
        assert payload == audio
        assert meta["codec"] == "g711a"
        assert meta["sample_rate"] == 8000
    finally:
        peer.close()
        client.sock.close()


def test_read_media_packet_iframe_h265():
    client, peer = _pair()
    try:
        video = b"\x00\x00\x00\x01\x40\x01" * 10  # fake NAL bytes
        # 00 00 01 FC | codec 0x03 (H265) | fps 30 | w 240 (=1920/8) |
        # h 135 (=1080/8) | ts LE u32 | len LE u32 | payload
        header = b"\x00\x00\x01\xfc" + bytes([0x03, 30, 240, 135]) \
            + struct.pack("<II", 123456, len(video))
        packet = header + video
        assert len(packet) == 16 + len(video)
        peer.sendall(_chunk(1410, packet))
        ptype, payload, meta = client.read_media_packet()
        assert ptype == 0xFC
        assert payload == video
        assert meta["codec"] == "h265"
        assert meta["fps"] == 30
        assert meta["width"] == 1920
        assert meta["height"] == 1080
        assert meta["frame"] == "I"
    finally:
        peer.close()
        client.sock.close()


def test_read_media_packet_skips_json_replies():
    """A KeepAlive JSON reply interleaved in the monitor stream (same
    socket) must be skipped, not mistaken for a media packet."""
    client, peer = _pair()
    try:
        audio = b"\xab" * 320
        packet = b"\x00\x00\x01\xfa" + bytes([14, 2]) \
            + struct.pack("<H", 320) + audio
        # Audio packet, then a KeepAlive JSON reply, then another packet.
        peer.sendall(_chunk(1410, packet))
        peer.sendall(_chunk(1006, b'{"Ret":100}\n\x00'))
        peer.sendall(_chunk(1410, packet))
        ptype, payload, meta = client.read_media_packet()
        assert ptype == 0xFA and payload == audio
        ptype, payload, meta = client.read_media_packet()
        assert ptype == 0xFA and payload == audio
        assert meta["codec"] == "g711a"
    finally:
        peer.close()
        client.sock.close()


def test_read_media_packet_split_across_chunks():
    client, peer = _pair()
    try:
        audio = b"\xab" * 320
        packet = b"\x00\x00\x01\xfa" + bytes([14, 2]) \
            + struct.pack("<H", 320) + audio
        # Split the media packet across two DVRIP chunks.
        peer.sendall(_chunk(1410, packet[:100]))
        peer.sendall(_chunk(1410, packet[100:]))
        ptype, payload, meta = client.read_media_packet()
        assert ptype == 0xFA
        assert payload == audio
    finally:
        peer.close()
        client.sock.close()


# -- talkback framing -------------------------------------------------------

def test_talk_send_framing():
    client, peer = _pair()
    try:
        packets = client.talk_send(b"\x00" * 640)  # 2 x 320-byte packets
        assert packets == 2
        for _ in range(2):
            header = _read_raw(peer, 20)
            assert header[0] == 0xFF
            assert struct.unpack_from("<H", header, 14)[0] == 1432
            assert struct.unpack_from("<I", header, 16)[0] == 328
            body = _read_raw(peer, 328)
            assert struct.unpack_from(">I", body, 0)[0] == 0x1FA
            assert body[4] == 14  # PCMA
            assert body[5] == 2   # 8000 Hz
            assert struct.unpack_from("<H", body, 6)[0] == 320
    finally:
        peer.close()
        client.sock.close()


def test_talk_send_short_tail_padded():
    client, peer = _pair()
    try:
        assert client.talk_send(b"\x11" * 100) == 1
        _read_raw(peer, 20)
        body = _read_raw(peer, 328)
        assert body[8:108] == b"\x11" * 100
        assert body[108:] == b"\xd5" * 220  # A-law silence padding
    finally:
        peer.close()
        client.sock.close()


# -- AV-talk builders ---------------------------------------------------------

def test_login_guard_stops_a_login_storm_after_the_camera_refuses():
    """Camera trả Ret=205 (khoá): các lần đăng nhập kế tiếp bị chặn ngay tại dịch vụ, không chạm camera."""
    from dosen_core.dvrip import DVRIPClient, DVRIPError, login_guard

    class _Fake(DVRIPClient):
        calls = 0

        def _write_json_cmd(self, opcode, obj):
            _Fake.calls += 1

        def read_json(self):
            raise DVRIPError("camera returned Ret=205: {'Ret': 205}")

    login_guard.reset()
    try:
        with pytest.raises(DVRIPError, match="Ret=205"):
            _Fake("127.0.0.1").login("u", "p")                  # lần đầu: hỏi camera, bị từ chối
        assert _Fake.calls == 1
        for _ in range(5):
            with pytest.raises(DVRIPError, match="login paused"):
                _Fake("127.0.0.1").login("u", "p")              # các lần sau: chặn, KHÔNG gửi gì
        assert _Fake.calls == 1
        login_guard._until = 0.0                                # hết thời gian chờ: được thử lại một lần
        with pytest.raises(DVRIPError, match="Ret=205"):
            _Fake("127.0.0.1").login("u", "p")
        assert _Fake.calls == 2 and login_guard._fails == 2     # lần từ chối thứ hai: chờ lâu gấp đôi
    finally:
        login_guard.reset()
