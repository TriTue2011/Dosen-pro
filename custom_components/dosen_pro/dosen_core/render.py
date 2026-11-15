"""Render text screens (conversation, notices) for the camera's 240x320 LCD.

The LCD only shows video, so text is drawn into an image and sent as video
(app/screen.py). Layout: a title bar, then messages stacked from the bottom
so the newest is always visible; the oldest ones are dropped when they do
not fit. Vietnamese needs a font with the full Latin Extended range: DejaVu
Sans has it (the Docker image installs it).
"""

import io
import os

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 240, 320
_FONT_DIRS = (os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts") + os.sep,   # phông kèm theo (HA không có phông hệ thống)
              "/usr/share/fonts/truetype/dejavu/",
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


# ── Đồng hồ đầy đủ: giờ lớn, ngày lễ, âm lịch, Can Chi, tuần, thời tiết (kiểu bảng e-ink sáng) ──────────────────

# Màu cho màn LCD nhỏ: nền gần trắng, chữ ĐEN (không dùng chữ xám: khó đọc trên màn), đỏ tươi cho điểm nhấn.
CLOCK_BG, INK, RED, RULE = (250, 250, 246), (0, 0, 0), (214, 22, 22), (30, 30, 30)

WEATHER_VI = {
    "sunny": "Trời nắng", "clear-night": "Trời quang", "partlycloudy": "Ít mây", "cloudy": "Nhiều mây",
    "rainy": "Mưa", "pouring": "Mưa to", "lightning": "Dông", "lightning-rainy": "Mưa dông", "fog": "Sương mù",
    "snowy": "Tuyết", "snowy-rainy": "Mưa tuyết", "hail": "Mưa đá", "windy": "Có gió", "windy-variant": "Nhiều mây, gió",
    "exceptional": "Thời tiết xấu",
}


def _cloud(d, x, y, s, colour):
    """Đám mây trong ô s×s tại (x, y)."""
    d.ellipse([x + .10 * s, y + .38 * s, x + .42 * s, y + .70 * s], fill=colour)
    d.ellipse([x + .25 * s, y + .22 * s, x + .62 * s, y + .66 * s], fill=colour)
    d.ellipse([x + .50 * s, y + .34 * s, x + .90 * s, y + .70 * s], fill=colour)
    d.rectangle([x + .26 * s, y + .46 * s, x + .74 * s, y + .70 * s], fill=colour)


def _sun(d, cx, cy, r, colour):
    import math
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=colour)
    for k in range(8):
        a = k * math.pi / 4
        d.line([cx + math.cos(a) * r * 1.35, cy + math.sin(a) * r * 1.35,
                cx + math.cos(a) * r * 1.75, cy + math.sin(a) * r * 1.75], fill=colour, width=max(2, int(r * .22)))


def _moon(d, cx, cy, r, colour, bg):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=colour)
    d.ellipse([cx - r * .35, cy - r * 1.1, cx + r * 1.45, cy + r * .9], fill=bg)


def weather_icon(kind: str, night: bool, size: int = 64) -> Image.Image:
    """Biểu tượng thời tiết vẽ bằng hình đơn giản (nét to, đọc được từ xa); vẽ gấp 4 rồi thu nhỏ cho mịn."""
    k = 4
    s = size * k
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    sun_c, cloud_c, dark_c, rain_c, bolt_c, snow_c = (255, 140, 0), (96, 120, 156), (58, 74, 104), (0, 100, 224), (255, 190, 0), (40, 130, 230)
    if kind == "sunny":
        (_moon(d, s * .5, s * .5, s * .28, (90, 100, 160), (0, 0, 0, 0)) if night else _sun(d, s * .5, s * .5, s * .22, sun_c))
    elif kind == "clear-night":
        _moon(d, s * .5, s * .5, s * .28, (90, 100, 160), (0, 0, 0, 0))
    elif kind == "partlycloudy":
        (_moon(d, s * .36, s * .34, s * .2, (90, 100, 160), (0, 0, 0, 0)) if night else _sun(d, s * .36, s * .34, s * .16, sun_c))
        _cloud(d, s * .12, s * .26, s * .9, cloud_c)
    elif kind in ("cloudy", "windy-variant"):
        _cloud(d, s * .0, s * .30, s * .84, dark_c)
        _cloud(d, s * .16, s * .20, s * .84, cloud_c)
    elif kind == "fog":
        _cloud(d, s * .08, s * .02, s * .84, cloud_c)
        for i in range(3):
            d.line([s * .16, s * (.66 + i * .12), s * .84, s * (.66 + i * .12)], fill=dark_c, width=int(s * .05))
    elif kind == "windy" or kind == "exceptional":
        for i, (x1, x2) in enumerate(((.12, .8), (.2, .9), (.12, .66))):
            d.line([s * x1, s * (.32 + i * .17), s * x2, s * (.32 + i * .17)], fill=cloud_c, width=int(s * .06))
    else:                                                   # có mây dày + thứ rơi xuống
        _cloud(d, s * .06, s * .04, s * .88, dark_c if kind in ("pouring", "lightning", "lightning-rainy") else cloud_c)
        if kind in ("lightning", "lightning-rainy"):
            d.polygon([(s * .54, s * .56), (s * .38, s * .8), (s * .5, s * .78), (s * .44, s * .98), (s * .66, s * .7),
                       (s * .53, s * .72)], fill=bolt_c)
        if kind in ("rainy", "pouring", "lightning-rainy", "snowy-rainy", "hail"):
            n = 4 if kind == "pouring" else 3
            for i in range(n):
                x = s * (.26 + i * (.46 / max(n - 1, 1)))
                d.line([x, s * .66, x - s * .06, s * .86], fill=rain_c, width=int(s * .055))
        if kind in ("snowy", "snowy-rainy", "hail"):
            for i in range(3):
                x, y, r = s * (.3 + i * .2), s * (.72 + (i % 2) * .1), s * .035
                d.ellipse([x - r, y - r, x + r, y + r], fill=snow_c)
    return img.resize((size, size), Image.LANCZOS)


def _centered(d, text, font, cx, y, fill):
    d.text((cx - d.textlength(text, font=font) / 2, y), text, font=font, fill=fill)


def _plural_days(n: int) -> list[tuple[str, tuple]]:
    """Dòng đếm ngược tới ngày lễ: các đoạn (chữ, màu) để chữ số tô đỏ như bảng mẫu."""
    if n == 0:
        return [("Hôm nay", RED)]
    if n == 1:
        return [("Ngày mai", RED)]
    return [("Còn ", INK), (str(n), RED), (" ngày", INK)]


def render_clock(now, weather: dict | None = None) -> bytes:
    """PNG 240x320 của đồng hồ đầy đủ: thứ + ngày, giờ lớn, ngày lễ sắp tới + số ngày còn lại, năm Can Chi, tháng,
    âm lịch, số tuần, và khối thời tiết nếu có (``weather``: condition, temp, humidity, high, low)."""
    from . import lich
    info = lich.day_info(now.date())
    img = Image.new("RGB", (WIDTH, HEIGHT), CLOCK_BG)
    d = ImageDraw.Draw(img)
    # 1) thứ + ngày
    _centered(d, f"{_DAYS[now.weekday()]} {now.strftime('%d-%m-%Y')}", _font(18, True), WIDTH / 2, 6, INK)
    # 2) giờ lớn: từng chữ số một để dấu hai chấm là hai ô vuông đỏ như bảng mẫu
    big = _font(70, True)
    hh, mm = now.strftime("%H"), now.strftime("%M")
    dw = d.textlength("0", font=big)
    gap = 26
    x0 = (WIDTH - (4 * dw + gap)) / 2
    top = 36 - d.textbbox((0, 0), "0", font=big)[1]
    for i, ch in enumerate(hh + mm):
        x = x0 + (i % 2) * dw + (0 if i < 2 else 2 * dw + gap)
        d.text((x, top), ch, font=big, fill=INK)
    cx, cy = x0 + 2 * dw + gap / 2, 36 + 52 / 2
    for dy in (-12, 12):
        d.rectangle([cx - 5, cy + dy - 5, cx + 5, cy + dy + 5], fill=RED)
    # 3) ngày lễ sắp tới
    f17 = _font(17)
    _centered(d, info["holiday"], f17, WIDTH / 2, 112, INK)
    parts = _plural_days(info["holiday_in"])
    total = sum(d.textlength(t, font=f17) for t, _ in parts)
    x = (WIDTH - total) / 2
    for t, colour in parts:
        d.text((x, 136), t, font=f17, fill=colour)
        x += d.textlength(t, font=f17)
    d.line([8, 166, WIDTH - 8, 166], fill=RULE, width=1)
    # 4) năm Can Chi | tháng + âm lịch + tuần
    d.line([WIDTH / 2, 168, WIDTH / 2, 234], fill=RULE, width=1)
    _centered(d, str(now.year), _font(28, True), WIDTH / 4, 170, RED)
    _centered(d, f"Năm {info['can_chi']}", _font(14), WIDTH / 4, 206, INK)
    _centered(d, f"Tháng {now.month}", _font(20, True), WIDTH * 3 / 4, 172, RED)
    lunar = f"Âm lịch {info['lunar_day']:02d}/{info['lunar_month']:02d}" + (" nhuận" if info["leap"] else "")
    _centered(d, lunar, _font(13), WIDTH * 3 / 4, 198, INK)
    _centered(d, f"Tuần {info['week']}", _font(13, True), WIDTH * 3 / 4, 216, INK)
    d.line([8, 236, WIDTH - 8, 236], fill=RULE, width=1)
    # 5) thời tiết
    if weather and weather.get("condition") is not None:
        cond = str(weather["condition"])
        night = now.hour < 6 or now.hour >= 18
        icon = weather_icon(cond, night)
        img.paste(icon, (14, 246), icon)
        temp = weather.get("temp")
        if temp is not None:
            d.text((90, 242), f"{round(temp)}°", font=_font(38, True), fill=RED)
        text = WEATHER_VI.get("clear-night" if (night and cond == "sunny") else cond, cond)
        d.text((90, 284), text, font=_font(15), fill=INK)
        extra = []
        if weather.get("humidity") is not None:
            extra.append(f"Ẩm {round(weather['humidity'])}%")
        if weather.get("high") is not None and weather.get("low") is not None:
            extra.append(f"{round(weather['low'])}°–{round(weather['high'])}°")
        if extra:
            d.text((90, 302), "  ".join(extra), font=_font(13, True), fill=INK)
    else:
        _centered(d, "Chọn thời tiết ở ô", _font(14, True), WIDTH / 2, 262, INK)
        _centered(d, "«Thời tiết trên đồng hồ»", _font(14, True), WIDTH / 2, 282, INK)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
