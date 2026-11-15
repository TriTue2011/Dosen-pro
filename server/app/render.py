"""Render text screens (conversation, notices) for the camera's 240x320 LCD.

The LCD only shows video, so text is drawn into an image and sent as video
(app/screen.py). Layout: a title bar, then messages stacked from the bottom
so the newest is always visible; the oldest ones are dropped when they do
not fit. Vietnamese needs a font with the full Latin Extended range: DejaVu
Sans has it (the Docker image installs it).
"""

import io

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 240, 320
_FONT_DIRS = ("/usr/share/fonts/truetype/dejavu/",
              "/usr/share/fonts/truetype/liberation/",
              "/usr/share/fonts/dejavu/")
_REGULAR = ("DejaVuSans.ttf", "LiberationSans-Regular.ttf")
_BOLD = ("DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf")

BG = (12, 16, 28)
TITLE_BG = (30, 40, 70)
# role -> (label, colour of the label, colour of the text)
ROLES = {
    "user": ("Bạn", (110, 200, 255), (235, 240, 250)),
    "assistant": ("Trợ lý", (120, 230, 150), (235, 250, 240)),
    "caller": ("Cuộc gọi", (255, 200, 110), (250, 245, 235)),
    "info": ("", (160, 170, 190), (200, 206, 220)),
}


def _font(size: int, bold: bool = False):
    for d in _FONT_DIRS:
        for name in (_BOLD if bold else _REGULAR):
            try:
                return ImageFont.truetype(d + name, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    """Word-wrap by pixel width; a word wider than the line is broken."""
    lines: list[str] = []
    for para in str(text).split("\n"):
        cur = ""
        for word in para.split(" "):
            cand = word if not cur else cur + " " + word
            if draw.textlength(cand, font=font) <= max_w:
                cur = cand
                continue
            if cur:
                lines.append(cur)
            while draw.textlength(word, font=font) > max_w and len(word) > 1:
                k = len(word)
                while k > 1 and draw.textlength(word[:k], font=font) > max_w:
                    k -= 1
                lines.append(word[:k])
                word = word[k:]
            cur = word
        lines.append(cur)
    return lines


def render_screen(title: str = "", messages: list | None = None,
                  font_size: int = 15) -> bytes:
    """PNG (240x320) of a title bar plus the newest messages that fit.

    ``messages``: list of ``{"role": user|assistant|caller|info, "text": ...}``
    (a bare string counts as ``info``).
    """
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    d = ImageDraw.Draw(img)
    f = _font(font_size)
    fb = _font(font_size - 2, bold=True)
    top = 0
    if title:
        top = font_size + 12
        d.rectangle((0, 0, WIDTH, top), fill=TITLE_BG)
        d.text((8, 5), _wrap(d, title, fb, WIDTH - 16)[0], font=fb,
               fill=(255, 255, 255))
    pad = 8
    line_h = font_size + 4
    blocks = []                                   # newest last
    for m in messages or []:
        if isinstance(m, str):
            m = {"role": "info", "text": m}
        label, c_label, c_text = ROLES.get(m.get("role"), ROLES["info"])
        body = _wrap(d, m.get("text", ""), f, WIDTH - 2 * pad)
        blocks.append((label, c_label, c_text, body))
    # Stack from the bottom; drop the oldest messages that do not fit.
    avail = HEIGHT - top - pad
    chosen, used = [], 0
    for label, c_label, c_text, body in reversed(blocks):
        h = len(body) * line_h + (line_h - 2 if label else 0) + 6
        if used + h > avail:
            if not chosen:                         # one huge message: keep its tail
                keep = max(1, (avail - (line_h if label else 0) - 6) // line_h)
                body = ["…"] + body[-(keep - 1):] if keep > 1 else body[-1:]
                h = len(body) * line_h + (line_h - 2 if label else 0) + 6
                chosen.append((label, c_label, c_text, body, h))
            break
        chosen.append((label, c_label, c_text, body, h))
        used += h
    y = HEIGHT - pad - sum(c[4] for c in chosen)
    for label, c_label, c_text, body, h in reversed(chosen):
        if label:
            d.text((pad, y), label, font=fb, fill=c_label)
            y += line_h - 2
        for ln in body:
            d.text((pad, y), ln, font=f, fill=c_text)
            y += line_h
        y += 6
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


_DAYS = ("Thứ hai", "Thứ ba", "Thứ tư", "Thứ năm", "Thứ sáu", "Thứ bảy", "Chủ nhật")


def render_clock(now) -> bytes:
    """PNG (240x320) of a clock face: big HH:MM, weekday and date in Vietnamese."""
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    d = ImageDraw.Draw(img)
    big, mid = _font(64, bold=True), _font(20)
    t = now.strftime("%H:%M")
    d.text(((WIDTH - d.textlength(t, font=big)) / 2, 96), t, font=big, fill=(235, 240, 250))
    line = f"{_DAYS[now.weekday()]}, {now.strftime('%d/%m/%Y')}"
    d.text(((WIDTH - d.textlength(line, font=mid)) / 2, 190), line, font=mid, fill=(150, 170, 200))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
