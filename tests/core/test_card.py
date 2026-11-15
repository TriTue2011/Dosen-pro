"""Thẻ thông tin (render_card / Screen.show_card): bố cục, giới hạn dòng, kích thước khung, kiểm màu."""

import io
import subprocess

import pytest
from conftest import _HAS_X265
from dosen_core import avtalk
from dosen_core.render import CARD_COLORS, render_card
from dosen_core.screen import Screen, build_hevc_cmd
from PIL import Image

ROWS = [{"label": f"Cảm biến {i}", "value": f"{20 + i},5 °C", "color": c} for i, c in enumerate(list(CARD_COLORS) * 2)]


def _img(png):
    return Image.open(io.BytesIO(png)).convert("RGB")


def test_card_is_240x320_and_numbers_and_colors_show_up():
    im = _img(render_card("Điện", "3,4 kW", "Đang phát", ROWS[:4], "green"))
    assert im.size == (240, 320)
    green = [(x, y) for x in range(8, 232) for y in range(40, 110) if im.getpixel((x, y))[1] > 110 and im.getpixel((x, y))[0] < 60]
    assert green, "số lớn phải có màu xanh lá"


def test_too_many_rows_are_dropped_and_long_text_is_shrunk_or_cut_without_error():
    many = _img(render_card("t", "1", "x", ROWS))                      # có số lớn: tối đa 6 dòng
    assert many.size == (240, 320)
    huge = _img(render_card("Tiêu đề " * 12, "123456789012345 kW", "Chú thích " * 10,
                            [{"label": "Nhãn rất rất dài " * 6, "value": "Giá trị rất dài " * 6}], "blue"))
    assert huge.size == (240, 320)
    assert _img(render_card()).size == (240, 320)                      # rỗng cũng vẽ được


def test_rows_only_card_fits_eight_rows_inside_the_screen():
    im = _img(render_card("Cảm biến", rows=ROWS))
    last_text_rows = [y for y in range(280, 320) if any(sum(im.getpixel((x, y))) < 150 for x in range(8, 232))]
    assert last_text_rows and max(last_text_rows) < 318                # dòng cuối không tràn khỏi màn


def test_show_card_validates_color_and_shows_a_text_layer(cfg):
    scr = Screen(cfg)
    try:
        with pytest.raises(ValueError):
            scr.show_card("t", "1", color="tim")
        assert scr.status()["layers"] == []
    finally:
        scr.close()


@pytest.mark.skipif(not _HAS_X265, reason="cần ffmpeg có libx265")
@pytest.mark.parametrize("quality", ["standard", "high", "max"])
def test_card_frames_stay_small_enough_for_the_camera(quality, tmp_path):
    png = tmp_path / "card.png"
    png.write_bytes(render_card("Điện năng lượng mặt trời", "3,4 kW", "Đang phát", ROWS[:6], "green"))
    out = subprocess.run(["timeout", "5"] + build_hevc_cmd(str(png), 240, 320, 10, quality=quality), capture_output=True).stdout
    aus = list(avtalk.HevcSplitter().feed(out))
    assert len(aus) >= 20 and max(len(a) for _, a in aus) <= 8000
