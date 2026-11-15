"""AVTalk wire format (app/avtalk.py), decoded from a real iCSee call."""

import struct

import pytest

from app import avtalk
from app.avtalk import HevcSplitter


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


# -- đầu-cuối với camera giả (cần ffmpeg + libx265: có trong ảnh Docker) ------

import json
import time

from conftest import _HAS_X265
from app.video import VideoPipeline


@pytest.mark.skipif(not _HAS_X265, reason="cần ffmpeg có libx265 (ảnh Docker)")
def test_pipeline_sends_h265_to_the_camera(fake_cam_avtalk):
    p = VideoPipeline()
    p.start("lavfi:testsrc=size=320x240:rate=10", width=240, height=320, fps=10,
            codec="H265", send=True)
    deadline = time.time() + 20
    while time.time() < deadline and p.status()["video_send"].get("video_packets", 0) < 25:
        time.sleep(0.2)
    status = p.status()
    p.stop()
    lines = open(fake_cam_avtalk).read().splitlines()
    claim = json.loads(next(l for l in lines if l.startswith("claim "))[6:])
    assert claim["AVTalk"]["Action"] == "Claim"
    assert claim["AVTalk"]["Video"] == {"Enc": "H265", "W": 240, "H": 320, "FPS": 10}
    start = json.loads(next(l for l in lines if l.startswith("start "))[6:])
    assert start["AVTalk"]["Action"] == "Start"               # lệnh Start trên kết nối điều khiển
    assert start["SessionID"] == claim["SessionID"]           # cùng một phiên
    media = [l.split() for l in lines if l.startswith("media ")]
    kinds = [m[1][:8] for m in media]
    assert kinds[0] == "000001fc" or "000001fa" == kinds[0]   # audio may lead
    video = [k for k in kinds if k in ("000001fc", "000001fd")]
    assert video and video[0] == "000001fc"                   # first video = key frame
    assert "000001fa" in kinds                                # silent audio between
    key_hdr = next(m[1] for m in media if m[1].startswith("000001fc"))
    assert key_hdr[8:16] == "030a1e28"                        # H265 10fps 240x320
    assert status["video_send"]["state"] == "sending"
    assert status["video_send"]["video_packets"] >= 25
