"""Âm lượng loa / mic của camera (fVideo.Volume, fVideo.VolumeIn)."""

import pytest
from dosen_core.camera import Camera


def test_get_and_set_speaker_mic_volume_and_anr(fake_cam_avtalk, cfg):
    cam = Camera(cfg)
    assert cam.get_audio() == {"speaker": 50, "mic": 50, "anr": True, "key_tip": True}
    out = cam.set_audio(speaker=100)
    assert out == {"speaker": 100, "mic": 50, "anr": True, "key_tip": True}                  # mic và chống ồn giữ nguyên
    out = cam.set_audio(mic=70, anr=False)
    assert out == {"speaker": 100, "mic": 70, "anr": False, "key_tip": True}
    for bad in (dict(speaker=101), dict(mic=-1), dict(speaker="5"), dict(anr="x"), dict(speaker=True), {}):
        with pytest.raises(ValueError):
            cam.set_audio(**bad)


def test_button_beep_can_be_turned_off_and_other_fields_are_kept(fake_cam_avtalk, cfg):
    """«Tắt tiếng báo khi nhấn nút»: Consumer.CommDevCfg.KeyAudioTip; trường khác của cấu hình (CheckX1Remote) giữ nguyên."""
    cam = Camera(cfg)
    out = cam.set_audio(key_tip=False)
    assert out["key_tip"] is False and out["speaker"] == 50 and out["mic"] == 50     # chỉ đổi tiếng bíp
    client = cam._control()                                          # đọc lại thẳng từ camera (giả chạy ở tiến trình khác)
    try:
        assert client.get_config("Consumer.CommDevCfg")["Consumer.CommDevCfg"] == {"CheckX1Remote": False, "KeyAudioTip": False}
    finally:
        client.close()
    assert cam.set_audio(key_tip=True)["key_tip"] is True
    with pytest.raises(ValueError):
        cam.set_audio(key_tip="off")
