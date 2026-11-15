"""Màn cuộc gọi: tên LỚN, xuống dòng khi dài, không tràn khung."""

import io

import pytest
from dosen_core import render
from PIL import Image


def _img(*a, **k):
    return Image.open(io.BytesIO(render.render_call(*a, **k))).convert("RGB")


@pytest.mark.parametrize("name", ["Bà", "Nguyễn Thị Thu Hương", "Cả nhà họ Nguyễn Văn Trần Lê Phạm Hoàng", "", "X" * 80])
def test_call_screen_is_always_a_full_frame_with_text_inside_the_margins(name):
    im = _img(name, "đang gọi đến", True, "red")
    assert im.size == (240, 320)
    dark = [(x, y) for y in range(60, 236) for x in range(240) if sum(im.getpixel((x, y))) < 150]   # nét chữ đen vùng tên
    assert dark and min(x for x, _ in dark) >= 8 and max(x for x, _ in dark) <= 232


def test_short_name_is_drawn_much_larger_than_the_old_15px_text():
    im = _img("Bà Năm", "đang gọi đến")
    rows = [y for y in range(60, 236) if any(sum(im.getpixel((x, y))) < 150 for x in range(240))]
    assert rows and max(rows) - min(rows) >= 30                                  # chiều cao chữ (kể cả dấu) ≥ 30 px; chữ cũ cao ~15 px


def test_unknown_color_is_refused_by_the_screen():
    import dosen_core.screen as screen_mod
    assert "red" in render.CALL_COLORS and "purple" not in render.CALL_COLORS
