"""Thẻ thông tin: biến thực thể của HA thành dòng nhãn–giá trị để vẽ lên màn camera (điện năng, nhiệt độ, cảm biến…)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

_NO_VALUE = "—"
_UNKNOWN = ("unknown", "unavailable", "none", "")


def format_number(value: float) -> str:
    """Số kiểu Việt Nam: dấu phẩy thập phân, tối đa 1 số lẻ (số lớn ≥ 100 thì làm tròn nguyên)."""
    text = f"{value:.0f}" if abs(value) >= 100 else f"{value:.1f}"
    if text.endswith(".0"):
        text = text[:-2]
    return text.replace(".", ",")


# Nhãn tiếng Việt cho trạng thái bật/tắt: (device_class của cảm biến nhị phân) → (chữ khi «on», màu khi «on», chữ khi «off»).
_BINARY = {
    "occupancy": ("Có người", "green", "Trống"), "presence": ("Có người", "green", "Trống"),
    "motion": ("Có chuyển động", "green", "Yên"), "moving": ("Đang động", "green", "Yên"),
    "door": ("Mở", "orange", "Đóng"), "window": ("Mở", "orange", "Đóng"), "opening": ("Mở", "orange", "Đóng"),
    "garage_door": ("Mở", "orange", "Đóng"), "lock": ("Mở khoá", "red", "Đã khoá"),
    "smoke": ("CÓ KHÓI", "red", "Bình thường"), "gas": ("CÓ GAS", "red", "Bình thường"), "moisture": ("Ẩm ướt", "blue", "Khô"),
    "problem": ("Có sự cố", "red", "Bình thường"), "safety": ("Nguy hiểm", "red", "An toàn"), "tamper": ("Bị phá", "red", "Bình thường"),
    "battery": ("Pin yếu", "red", "Pin tốt"), "connectivity": ("Đã kết nối", "green", "Mất kết nối"), "plug": ("Đang cắm", "green", "Rút"),
    "power": ("Có điện", "green", "Mất điện"), "light": ("Có sáng", "orange", "Tối"), "sound": ("Có tiếng", "orange", "Yên"),
    "vibration": ("Rung", "orange", "Yên"), "presence_home": ("Ở nhà", "green", "Vắng"),
}
_ON_OFF = ("Bật", "green", "Tắt")                       # công tắc, đèn, quạt… và cảm biến nhị phân không rõ loại
_TEXT = {"home": ("Ở nhà", "green"), "not_home": ("Vắng", None), "open": ("Mở", "orange"), "closed": ("Đóng", None),
         "locked": ("Đã khoá", None), "unlocked": ("Mở khoá", "red")}


def _entity_text(domain: str, device_class: str | None, raw: str) -> tuple[str, str | None] | None:
    """Chữ + màu tiếng Việt cho trạng thái dạng chữ quen thuộc; None nếu không có cách dịch (giữ nguyên trạng thái)."""
    if domain == "binary_sensor" and raw in ("on", "off"):
        on, colour, off = _BINARY.get(device_class or "", _ON_OFF)
        return (on, colour) if raw == "on" else (off, None)
    if domain in ("switch", "light", "fan", "input_boolean", "siren", "humidifier") and raw in ("on", "off"):
        return (_ON_OFF[0], _ON_OFF[1]) if raw == "on" else (_ON_OFF[2], None)
    return _TEXT.get(raw)


def format_state(hass: HomeAssistant, entity_id: str) -> tuple[str, str]:
    """(nhãn, giá trị) của một thực thể: tên thân thiện + trạng thái kèm đơn vị. Thiếu/không đọc được thì «—»."""
    label, text, _ = format_state_ex(hass, entity_id)
    return label, text


#: Cảm biến dùng pin (Zigbee…) chỉ báo thưa: sau khi HA khởi động lại chúng «unknown» cho tới lần báo kế (đo 15/11: nhiệt độ phòng khách,
#: ban công, độ ẩm phòng khách). Có bộ nhớ thì thẻ giữ giá trị gần nhất đã biết thay vì «—», nhưng không quá ngần này giây.
MEMORY_MAX_AGE = 12 * 3600


def format_state_ex(hass: HomeAssistant, entity_id: str, memory: dict | None = None,
                    now: float | None = None) -> tuple[str, str, str | None]:
    """Như ``format_state`` nhưng kèm màu gợi ý (hiện diện «Có người» xanh lá, cửa mở cam, cảnh báo đỏ…) hoặc None.

    ``memory`` (tuỳ chọn): ``{entity_id: [chữ, màu, thời điểm]}``; giá trị tốt được ghi vào, và khi cảm biến chưa có số thì dùng lại giá trị
    đã nhớ nếu chưa quá ``MEMORY_MAX_AGE``."""
    import time
    now = time.time() if now is None else now
    state = hass.states.get(entity_id)
    if state is None:
        return entity_id, _NO_VALUE, None
    label = str(state.attributes.get("friendly_name") or entity_id)
    raw = str(state.state)
    if raw.lower() in _UNKNOWN:
        old = (memory or {}).get(entity_id)
        if old and now - float(old[2]) <= MEMORY_MAX_AGE:
            return label, old[0], old[1]
        return label, _NO_VALUE, None
    result = _format_known(state, entity_id, label, raw)
    if memory is not None:
        memory[entity_id] = [result[1], result[2], now]
    return result


def _format_known(state, entity_id: str, label: str, raw: str) -> tuple[str, str, str | None]:
    translated = _entity_text(entity_id.split(".", 1)[0], state.attributes.get("device_class"), raw)
    if translated is not None:
        return label, translated[0], translated[1]
    unit = state.attributes.get("unit_of_measurement")
    try:
        text = format_number(float(raw))
    except ValueError:
        return label, raw, None                # chữ khác: giữ nguyên
    return label, f"{text} {unit}" if unit else text, None


def build_card(hass: HomeAssistant, data: dict, memory: dict | None = None) -> dict:
    """Từ dữ liệu dịch vụ ``screen_card`` ra thẻ để vẽ: ``title, value, caption, rows, color``.

    ``main_entity`` thành số lớn (nhãn là tên thực thể nếu không nêu ``caption``); ``entities`` và ``rows`` thành các dòng
    (``rows`` trước, rồi ``entities``). Mỗi mục của ``entities`` là ``entity_id`` hoặc ``{entity, name}`` để tự đặt tên hiển thị. Dòng dạng chữ «nhãn: giá trị» được tách ở dấu hai chấm đầu tiên."""
    value, caption = data.get("value", ""), data.get("caption", "")
    if data.get("main_entity"):
        label, text, _ = format_state_ex(hass, data["main_entity"], memory)
        value = value or text
        caption = caption or label
    rows: list[dict] = []
    for r in data.get("rows", []):
        if isinstance(r, str):
            label, _, val = r.partition(":")
            rows.append({"label": label.strip(), "value": val.strip()})
        else:
            rows.append(dict(r))
    for item in data.get("entities", []):
        entity_id, name = (item["entity"], item.get("name")) if isinstance(item, dict) else (item, None)
        label, text, colour = format_state_ex(hass, entity_id, memory)
        row = {"label": name or label, "value": text}
        colour = (item.get("color") if isinstance(item, dict) and item.get("color") else None) or colour     # màu tự chọn thắng màu theo trạng thái
        if colour:
            row["color"] = colour
        rows.append(row)
    return {"title": data.get("title", ""), "value": value, "caption": caption, "rows": rows,
            "color": data.get("color", "red"), "theme": data.get("theme", "light"), "layout": data.get("layout", "list"),
            "title_color": data.get("title_color")}
