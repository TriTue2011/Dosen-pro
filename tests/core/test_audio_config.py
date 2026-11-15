"""Âm lượng loa / mic của camera (fVideo.Volume, fVideo.VolumeIn)."""

import pytest
from dosen_core.camera import Camera


def test_get_and_set_speaker_mic_volume_and_anr(fake_cam_avtalk, cfg):
    cam = Camera(cfg)
    assert cam.get_audio() == {"speaker": 50, "mic": 50, "anr": True}
    out = cam.set_audio(speaker=100)
    assert out == {"speaker": 100, "mic": 50, "anr": True}                  # mic và chống ồn giữ nguyên
    out = cam.set_audio(mic=70, anr=False)
    assert out == {"speaker": 100, "mic": 70, "anr": False}
    for bad in (dict(speaker=101), dict(mic=-1), dict(speaker="5"), dict(anr="x"), dict(speaker=True), {}):
        with pytest.raises(ValueError):
            cam.set_audio(**bad)
