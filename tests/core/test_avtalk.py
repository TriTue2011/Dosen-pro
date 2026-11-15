"""AVTalk wire format (app/avtalk.py), decoded from a real iCSee call."""

import struct

import pytest

from dosen_core import avtalk
from dosen_core.avtalk import HevcSplitter


def _nal(nal_type: int, payload: bytes = b"\x11\x22") -> bytes:
    return bytes([nal_type << 1, 1]) + payload


def test_pack_time_matches_the_capture():
    # 0x6ade8720 was the time field in the first key frame of the capture:
    # 2026-11-15 08:28:32 local.
    import time
    t = time.mktime((2026, 11, 15, 8, 28, 32, 0, 0, -1))
    assert avtalk.pack_time(t) == 0x6ADE8720


def test_packet_layouts():
    au = b"\x00\x00\x00\x01\x40\x01abc"
    key = avtalk.key_packet(240, 320, 10, 0x6ADE8720, au)
    assert key[:8] == bytes.fromhex("000001fc030a1e28")        # as captured
    assert struct.unpack_from("<II", key, 8) == (0x6ADE8720, len(au))
    assert key[16:] == au
    delta = avtalk.delta_packet(au)
    assert delta[:4] == b"\x00\x00\x01\xfd"
    assert struct.unpack_from("<I", delta, 4)[0] == len(au)
    audio = avtalk.audio_packet()
    assert audio[:8] == bytes.fromhex("000001fa0e024001")      # as captured
    assert len(audio) == 328
    with pytest.raises(ValueError):
        avtalk.key_packet(250, 320, 10, 0, au)                 # not a multiple of 8


def test_splitter_groups_parameter_sets_with_the_idr():
    stream = (b"\x00\x00\x00\x01" + _nal(32) + b"\x00\x00\x00\x01" + _nal(33)
              + b"\x00\x00\x00\x01" + _nal(34) + b"\x00\x00\x00\x01" + _nal(19)
              + b"\x00\x00\x01" + _nal(1) + b"\x00\x00\x00\x01" + _nal(1)
              + b"\x00\x00\x00\x01" + _nal(1))   # last NAL incomplete until next start code
    frames = HevcSplitter().feed(stream)
    assert [k for k, _ in frames] == [True, False, False]
    assert frames[0][1].count(b"\x00\x00\x00\x01") == 4          # VPS SPS PPS IDR
    assert frames[1][1] == b"\x00\x00\x00\x01" + _nal(1)


def test_splitter_is_chunking_independent():
    stream = (b"\x00\x00\x00\x01" + _nal(32) + b"\x00\x00\x00\x01" + _nal(33)
              + b"\x00\x00\x00\x01" + _nal(34) + b"\x00\x00\x00\x01" + _nal(20)
              + b"\x00\x00\x00\x01" + _nal(1, b"\x01\x02\x03")
              + b"\x00\x00\x00\x01" + _nal(1, b"\x04") + b"\x00\x00\x00\x01")
    whole = HevcSplitter().feed(stream)
    one_by_one = []
    sp = HevcSplitter()
    for i in range(len(stream)):
        one_by_one += sp.feed(stream[i:i + 1])
    assert whole == one_by_one
    assert len(whole) == 3
    assert [k for k, _ in whole] == [True, False, False]


