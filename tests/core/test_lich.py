"""Âm lịch, Can Chi, tuần và ngày lễ — đối chiếu với các ngày đã biết (không có mạng, không có tích hợp ngoài)."""

import io
from datetime import date, datetime

import pytest
from dosen_core import lich
from dosen_core.render import render_clock
from PIL import Image


def test_the_day_on_the_reference_clock_face():
    """Bảng mẫu của chủ máy: Thứ sáu 16-10-2026 = âm 07/09, năm Bính Ngọ, tuần 42, Phụ nữ Việt Nam còn 4 ngày."""
    info = lich.day_info(date(2026, 10, 16))
    assert (info["lunar_day"], info["lunar_month"], info["lunar_year"], info["leap"]) == (7, 9, 2026, False)
    assert info["can_chi"] == "Bính Ngọ" and info["week"] == 42
    assert (info["holiday"], info["holiday_in"]) == ("Phụ nữ Việt Nam", 4)


def test_lunar_dates_match_known_calendar_days():
    assert lich.lunar_to_solar(1, 1, 2024) == date(2024, 2, 10)         # Tết Giáp Thìn
    assert lich.lunar_to_solar(1, 1, 2025) == date(2025, 1, 29)         # Tết Ất Tỵ
    assert lich.lunar_to_solar(1, 1, 2026) == date(2026, 2, 17)         # Tết Bính Ngọ
    assert lich.lunar_to_solar(15, 8, 2026) == date(2026, 9, 25)        # Trung thu
    assert lich.lunar_to_solar(10, 3, 2026) == date(2026, 4, 26)        # Giỗ Tổ Hùng Vương
    assert lich.solar_to_lunar(date(2026, 11, 15)) == (7, 10, 2026, False)       # hôm nay theo lịch âm của HA


def test_leap_month_is_found_both_ways():
    assert lich.lunar_to_solar(1, 6, 2025, leap=True) == date(2025, 7, 25)      # nhuận tháng 6 năm Ất Tỵ
    assert lich.solar_to_lunar(date(2025, 7, 25)) == (1, 6, 2025, True)
    assert lich.lunar_to_solar(1, 7, 2025, leap=True) is None                   # năm đó không nhuận tháng 7


@pytest.mark.parametrize("d", [date(2026, 1, 1), date(2026, 2, 16), date(2026, 12, 31), date(2027, 2, 5), date(2030, 6, 1)])
def test_lunar_round_trip_for_any_day(d):
    ld, lm, ly, leap = lich.solar_to_lunar(d)
    assert lich.lunar_to_solar(ld, lm, ly, leap) == d


def test_next_holiday_counts_today_tomorrow_and_wraps_to_next_year():
    assert lich.next_holiday(date(2026, 2, 17)) == ("Tết Nguyên đán", 0)               # đúng hôm nay
    assert lich.next_holiday(date(2026, 2, 15)) == ("Giao thừa", 1)                     # ngày cuối tháng Chạp
    assert lich.next_holiday(date(2026, 12, 26)) == ("Tết Dương lịch", 6)               # qua năm sau
    name, days = lich.next_holiday(date(2026, 12, 31))
    assert (name, days) == ("Tết Dương lịch", 1)


def test_every_day_of_a_year_has_a_next_holiday_within_two_months():
    d = date(2026, 1, 1)
    while d.year == 2026:
        name, days = lich.next_holiday(d)
        assert name and 0 <= days <= 70, (d, name, days)
        d = date.fromordinal(d.toordinal() + 1)


def _img(png):
    return Image.open(io.BytesIO(png)).convert("RGB")


def test_clock_face_is_240x320_and_weather_changes_the_picture():
    now = datetime(2026, 10, 16, 13, 37)
    plain, sunny = _img(render_clock(now)), _img(render_clock(now, {"condition": "partlycloudy", "temp": 28.4,
                                                                  "humidity": 70, "high": 31, "low": 23}))
    assert plain.size == sunny.size == (240, 320)
    assert plain.tobytes() != sunny.tobytes()
    assert plain.crop((0, 0, 240, 236)).tobytes() == sunny.crop((0, 0, 240, 236)).tobytes()      # phần trên (giờ, lịch) giống nhau
    red = [(x, y) for x in range(100, 140) for y in range(40, 90) if sunny.getpixel((x, y))[0] > 150 and sunny.getpixel((x, y))[1] < 70]
    assert red, "dấu hai chấm đỏ giữa giờ phải có"


@pytest.mark.parametrize("cond", ["sunny", "clear-night", "partlycloudy", "cloudy", "rainy", "pouring", "lightning",
                                  "lightning-rainy", "fog", "snowy", "snowy-rainy", "hail", "windy", "windy-variant",
                                  "exceptional", "điều-chưa-biết"])
def test_every_weather_condition_draws_without_error(cond):
    for hour in (9, 22):
        assert _img(render_clock(datetime(2026, 7, 1, hour, 0), {"condition": cond, "temp": 30})).size == (240, 320)
