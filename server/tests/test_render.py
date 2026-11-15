"""Text rendering for the camera screen (app/render.py)."""

import io

from PIL import Image

from app.render import HEIGHT, WIDTH, render_screen


def _img(png):
    return Image.open(io.BytesIO(png)).convert("RGB")


def _lit(img, box):
    """Số điểm ảnh khác nền trong vùng box (có chữ hay không)."""
    bg = img.getpixel((WIDTH - 1, HEIGHT - 1))
    px = img.crop(box)
    return sum(1 for p in px.getdata() if p != bg)


def test_size_and_title_and_messages_are_drawn():
    png = render_screen("Trợ lý", [{"role": "user", "text": "Bật đèn phòng khách"},
                                   {"role": "assistant", "text": "Đã bật đèn phòng khách."}])
    img = _img(png)
    assert img.size == (WIDTH, HEIGHT)
    assert _lit(img, (0, 0, WIDTH, 28)) > 200          # thanh tiêu đề
    assert _lit(img, (0, HEIGHT - 120, WIDTH, HEIGHT)) > 200   # tin nhắn ở đáy


def test_newest_message_is_kept_and_oldest_dropped():
    many = [{"role": "info", "text": "dòng %d " % i + "x" * 30} for i in range(40)]
    img = _img(render_screen("", many))
    assert _lit(img, (0, HEIGHT - 40, WIDTH, HEIGHT)) > 100    # tin mới nhất ở đáy
    assert _lit(img, (0, 0, WIDTH, HEIGHT // 2)) > 200         # màn vẫn đầy chữ (chỉ bỏ nguyên tin, không cắt dở)


def test_one_huge_message_and_long_word_do_not_crash():
    png = render_screen("t", [{"role": "assistant", "text": "a" * 500 + " " + "từ " * 400}])
    assert _img(png).size == (WIDTH, HEIGHT)
    assert _img(render_screen("", ["chuỗi thường", {"role": "lạ", "text": "vai trò lạ"}])).size == (WIDTH, HEIGHT)
