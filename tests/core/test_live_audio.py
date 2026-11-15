"""Tiếng cuộc gọi ra loa camera phải GIỮ độ trễ thấp: nguồn gửi dồn thì bỏ tiếng cũ, không để tồn thành hàng chục giây (chủ máy 15/11: trễ 30 s)."""

from dosen_core import avtalk
from dosen_core.speaker import Speaker, SpeakerStream


class _Sess:
    client = None

    def send(self, packet): pass


def _frame(n):
    return bytes([n % 256]) * 320


def test_live_audio_never_blocks_and_stays_within_half_a_second():
    s = avtalk.AVTalkSender(_Sess(), 240, 320, 10)
    for i in range(500):                                   # 500 khung (20 s) dồn vào cùng lúc, không có ai lấy ra
        s.push_audio_live(_frame(i))
    assert s._aq.qsize() <= avtalk.AVTalkSender.LIVE_AUDIO_MAX_FRAMES
    assert s.audio_dropped >= 480 and s.stats()["audio_dropped"] == s.audio_dropped
    last = []
    while not s._aq.empty():
        last.append(s._aq.get_nowait())
    assert last[-1] == _frame(499)                          # khung MỚI NHẤT còn lại (bỏ cái cũ, không bỏ cái mới)


def test_live_audio_in_steady_state_drops_nothing():
    s = avtalk.AVTalkSender(_Sess(), 240, 320, 10)
    for i in range(200):                                   # nguồn đúng thời gian thật: mỗi lần một khung, bên kia lấy ngay
        s.push_audio_live(_frame(i))
        s._aq.get_nowait()
    assert s.audio_dropped == 0


def test_speaker_stream_uses_the_live_path_for_the_avtalk_channel():
    class Sink:
        def __init__(self): self.live, self.blocking = [], []
        def push_audio_live(self, b): self.live.append(b)
        def push_audio(self, b, timeout=1.0): self.blocking.append(b)

    class Screen:
        def __init__(self): self.sink = Sink()
        def audio_sink(self): return self.sink

    scr = Screen()
    sp = Speaker(talk=None, screen=scr)
    SpeakerStream(sp).feed(b"\xd5" * 160)
    assert scr.sink.live == [b"\xd5" * 160] and not scr.sink.blocking
