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


def format_state(hass: HomeAssistant, entity_id: str) -> tuple[str, str]:
    """(nhãn, giá trị) của một thực thể: tên thân thiện + trạng thái kèm đơn vị. Thiếu/không đọc được thì «—»."""
    state = hass.states.get(entity_id)
    if state is None:
        return entity_id, _NO_VALUE
    label = str(state.attributes.get("friendly_name") or entity_id)
    raw = str(state.state)
    if raw.lower() in _UNKNOWN:
        return label, _NO_VALUE
    unit = state.attributes.get("unit_of_measurement")
    try:
        text = format_number(float(raw))
    except ValueError:
        return label, raw                      # chữ (Bật, Đóng, Đang chạy…): giữ nguyên
    return label, f"{text} {unit}" if unit else text


def build_card(hass: HomeAssistant, data: dict) -> dict:
    """Từ dữ liệu dịch vụ ``screen_card`` ra thẻ để vẽ: ``title, value, caption, rows, color``.

    ``main_entity`` thành số lớn (nhãn là tên thực thể nếu không nêu ``caption``); ``entities`` và ``rows`` thành các dòng
    (``rows`` trước, rồi ``entities``). Dòng dạng chữ «nhãn: giá trị» được tách ở dấu hai chấm đầu tiên."""
    value, caption = data.get("value", ""), data.get("caption", "")
    if data.get("main_entity"):
        label, text = format_state(hass, data["main_entity"])
        value = value or text
        caption = caption or label
    rows: list[dict] = []
    for r in data.get("rows", []):
        if isinstance(r, str):
            label, _, val = r.partition(":")
            rows.append({"label": label.strip(), "value": val.strip()})
        else:
            rows.append(dict(r))
    for entity_id in data.get("entities", []):
        label, text = format_state(hass, entity_id)
        rows.append({"label": label, "value": text})
    return {"title": data.get("title", ""), "value": value, "caption": caption, "rows": rows,
            "color": data.get("color", "red")}
