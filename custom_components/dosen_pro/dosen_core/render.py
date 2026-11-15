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
    # 4) năm Can Chi | âm lịch + tuần
    d.line([WIDTH / 2, 168, WIDTH / 2, 234], fill=RULE, width=1)
    _centered(d, str(now.year), _font(28, True), WIDTH / 4, 170, RED)
    _centered(d, f"Năm {info['can_chi']}", _font(14), WIDTH / 4, 206, INK)
    # Ô bên phải: ÂM LỊCH (dương lịch đã có ở dòng trên cùng): ngày/tháng âm to màu đỏ, nhãn, số tuần.
    _centered(d, f"{info['lunar_day']:02d}/{info['lunar_month']:02d}", _font(28, True), WIDTH * 3 / 4, 167, RED)
    _centered(d, "Âm lịch" + (" nhuận" if info["leap"] else ""), _font(14, True), WIDTH * 3 / 4, 200, INK)
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


# ── Thẻ thông tin tự chế: số lớn + các dòng nhãn–giá trị (cùng kiểu với đồng hồ) ─────────────────────────────

CARD_COLORS = {"black": INK, "red": RED, "blue": (0, 90, 200), "green": (0, 130, 50), "orange": (225, 110, 0)}
CARD_THEMES = ("light", "dark")
CARD_LAYOUTS = ("list", "tiles")
HEX_COLOR = r"^#[0-9a-fA-F]{6}$"
CARD_MAX_ROWS_WITH_VALUE, CARD_MAX_ROWS, CARD_MAX_TILES = 6, 8, 6

# Bảng màu theo giao diện: nền, chữ, đường kẻ, đường kẻ mờ, và màu tên (đỏ/xanh…) — giao diện tối dùng màu sáng hơn để nổi trên nền đen.
_THEME = {
    "light": {"bg": CLOCK_BG, "ink": INK, "rule": RULE, "soft": (200, 200, 194),
              "named": CARD_COLORS},
    "dark": {"bg": (14, 18, 26), "ink": (242, 246, 252), "rule": (150, 160, 176), "soft": (58, 64, 78),
             "named": {"black": (242, 246, 252), "red": (255, 92, 92), "blue": (110, 172, 255), "green": (84, 222, 124),
                       "orange": (255, 170, 64)}},
}


def valid_color(value) -> bool:
    """Tên trong bảng màu hoặc ``#rrggbb``."""
    import re
    return isinstance(value, str) and (value in CARD_COLORS or re.match(HEX_COLOR, value) is not None)


def resolve_color(value, theme: str = "light", default: str = "black") -> tuple:
    """Tên trong bảng màu hoặc ``#rrggbb`` → RGB; không hiểu thì màu ``default``. Giao diện tối dùng bản sáng của các màu tên."""
    named = _THEME.get(theme, _THEME["light"])["named"]
    if isinstance(value, str) and len(value) == 7 and value.startswith("#"):
        try:
            return tuple(int(value[k:k + 2], 16) for k in (1, 3, 5))
        except ValueError:
            pass
    return named.get(value, named[default])


def _fit_font(d, text: str, max_w: int, size: int, min_size: int = 11, bold: bool = False):
    """Font lớn nhất (từ ``size`` xuống ``min_size``) mà ``text`` còn vừa ``max_w``; vẫn không vừa thì cắt chữ thêm «…»."""
    f = _font(size, bold)
    while d.textlength(text, font=f) > max_w and size > min_size:
        size -= 1
        f = _font(size, bold)
    if d.textlength(text, font=f) > max_w:
        while len(text) > 1 and d.textlength(text + "…", font=f) > max_w:
            text = text[:-1]
        text += "…"
    return text, f


def render_card(title: str = "", value: str = "", caption: str = "", rows: list | None = None, color: str = "red",
                theme: str = "light", layout: str = "list", title_color: str | None = None) -> bytes:
    """PNG 240x320: tiêu đề, MỘT SỐ LỚN (``value``) kèm nhãn (``caption``), rồi các dòng ``{label, value, color?}``.

    ``theme`` sáng/tối; ``layout`` ``list`` (số lớn + danh sách) hoặc ``tiles`` (lưới 2 cột, mỗi ô nhãn nhỏ + số to). Màu là tên trong
    ``CARD_COLORS`` hoặc ``#rrggbb``. Ai cũng dùng được cho điện năng, nhiệt độ, cảm biến…: chỉ khai dữ liệu, bố cục tự lo. Nhiều dòng quá
    thì bỏ bớt cuối; chữ dài tự thu nhỏ rồi cắt."""
    th = _THEME.get(theme, _THEME["light"])
    ink, rule, soft = th["ink"], th["rule"], th["soft"]
    accent = resolve_color(color, theme, "red")
    img = Image.new("RGB", (WIDTH, HEIGHT), th["bg"])
    d = ImageDraw.Draw(img)
    y = 8
    if title:
        text, f = _fit_font(d, str(title), WIDTH - 16, 19, 12, True)
        _centered(d, text, f, WIDTH / 2, y, resolve_color(title_color, theme, "black") if title_color else ink)
        y += 30
        d.line([8, y, WIDTH - 8, y], fill=rule, width=1)
        y += 8
    rows = list(rows or [])
    if layout == "tiles":
        _draw_tiles(d, rows, value, caption, accent, theme, y)
    else:
        _draw_list(d, rows, value, caption, accent, theme, y, bool(title))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


ZALO_BLUE = (0, 104, 255)
CALL_COLORS = {"red": (214, 22, 22), "orange": (230, 120, 0), "green": (0, 140, 60)}


def render_call(name: str, status: str, video: bool | None = None, color: str = "red") -> bytes:
    """PNG 240x320 cho cuộc gọi Zalo: thanh «Zalo», TÊN người gọi rất lớn (xuống dòng tới 3 dòng, vừa khung), trạng thái bên dưới.
    ``video``: True «gọi video», False «gọi thoại», None «cuộc gọi» (sự kiện cuộc gọi đến không luôn nói rõ loại). Nền sáng, chữ đen, như đồng hồ."""
    img = Image.new("RGB", (WIDTH, HEIGHT), CLOCK_BG)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, WIDTH, 46), fill=ZALO_BLUE)
    label, f = _fit_font(d, "Zalo · cuộc gọi" if video is None else ("Zalo · gọi video" if video else "Zalo · gọi thoại"), WIDTH - 16, 21, 12, True)
    _centered(d, label, f, WIDTH / 2, 11, (255, 255, 255))
    name = (name or "").strip() or "Có người gọi"
    max_w, top, bottom = WIDTH - 20, 62, 232
    for size in range(46, 19, -2):                       # cỡ lớn nhất mà tên vừa tối đa 3 dòng trong vùng giữa
        fn = _font(size, True)
        lines = _wrap(d, name, fn, max_w)
        if len(lines) <= 3 and len(lines) * int(size * 1.25) <= bottom - top:
            break
    lines = lines[:3]
    step = int(size * 1.25)
    y = top + (bottom - top - step * len(lines)) // 2
    for ln in lines:
        text, fl = _fit_font(d, ln, max_w, size, 14, True)
        _centered(d, text, fl, WIDTH / 2, y, INK)
        y += step
    d.line([20, 244, WIDTH - 20, 244], fill=RULE, width=1)
    text, fs = _fit_font(d, status, WIDTH - 20, 26, 14, True)
    _centered(d, text, fs, WIDTH / 2, 262, CALL_COLORS.get(color, CALL_COLORS["red"]))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _draw_list(d, rows, value, caption, accent, theme, y, has_title):
    th = _THEME[theme]
    ink, rule, soft = th["ink"], th["rule"], th["soft"]
    if value:
        text, f = _fit_font(d, str(value), WIDTH - 16, 58, 24, True)
        _centered(d, text, f, WIDTH / 2, y, accent)
        y += int(f.size * 1.2) + 2
        if caption:
            text, f = _fit_font(d, str(caption), WIDTH - 16, 16, 11, True)
            _centered(d, text, f, WIDTH / 2, y, ink)
            y += 24
        y += 6
    rows = rows[:CARD_MAX_ROWS_WITH_VALUE if value else CARD_MAX_ROWS]
    if not rows:
        return
    if value or not has_title:                                # đã có đường kẻ dưới tiêu đề thì không kẻ đôi
        d.line([8, y, WIDTH - 8, y], fill=rule, width=1)
    y += 4
    roomy = not value and len(rows) <= 5                      # ít dòng, không có số lớn: chữ to, dòng cao cho đỡ trống
    vsize, lsize, step_max = (22, 20, 56) if roomy else (17, 15, 34)
    step = min(step_max, (HEIGHT - 8 - y) // len(rows))
    for r in rows:
        vtext, vfont = _fit_font(d, str(r.get("value", "")), 116 if roomy else 108, vsize, 11, True)
        vw = d.textlength(vtext, font=vfont)
        ltext, lfont = _fit_font(d, str(r.get("label", "")), WIDTH - 24 - int(vw) - 8, lsize, 11)
        ty = y + (step - int(vsize * 1.2)) // 2
        d.text((10, ty), ltext, font=lfont, fill=ink)
        d.text((WIDTH - 10 - vw, ty), vtext, font=vfont, fill=resolve_color(r.get("color"), theme, "black"))
        y += step
        d.line([8, y, WIDTH - 8, y], fill=soft, width=1)


def _draw_tiles(d, rows, value, caption, accent, theme, y):
    """Lưới 2 cột: mỗi ô nhãn nhỏ phía trên, số to có màu phía dưới. ``value`` (nếu có) là ô đầu tiên."""
    th = _THEME[theme]
    ink, soft = th["ink"], th["soft"]
    tiles = ([{"label": caption, "value": value, "color": None, "main": True}] if value else []) + rows
    tiles = tiles[:CARD_MAX_TILES]
    if not tiles:
        return
    n_rows = (len(tiles) + 1) // 2
    cell_h = min(110, (HEIGHT - 8 - y) // n_rows)
    cell_w = (WIDTH - 16) // 2
    for k, t in enumerate(tiles):
        cx, cy = 8 + (k % 2) * cell_w, y + (k // 2) * cell_h
        d.rectangle([cx + 3, cy + 3, cx + cell_w - 3, cy + cell_h - 3], outline=soft, width=1)
        label, f = _fit_font(d, str(t.get("label", "")), cell_w - 14, 14, 10)
        _centered(d, label, f, cx + cell_w / 2, cy + 8, ink)
        colour = accent if t.get("main") and not t.get("color") else resolve_color(t.get("color"), theme, "black")
        vtext, vf = _fit_font(d, str(t.get("value", "")), cell_w - 14, 32, 12, True)
        _centered(d, vtext, vf, cx + cell_w / 2, cy + 8 + 22 + max(0, (cell_h - 8 - 22 - int(vf.size * 1.2) - 8) // 2), colour)
