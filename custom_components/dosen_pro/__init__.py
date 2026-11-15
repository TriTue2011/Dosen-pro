"""Dosen Pro: màn hình, loa và mic của camera DOSEN RPO V1 như thiết bị của HA."""

from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import timedelta
from pathlib import Path

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryError, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import DosenApiError, LocalApi
from .const import (CONF_INTERCOM_KEY, CONF_ALARM_EVENTS, CONF_URL, DEFAULT_MODES, DEFAULT_PORT, DOMAIN, EVENT_ALARM,
                    OPT_HA_URL, SCAN_INTERVAL_SECONDS)
from .dosen_core.camera import Camera
from .intercom import DosenIntercomView
from .dosen_core.config import CamConfig
from .card import build_card
from .carousel import _CARD_ENTITY, _CARD_ROW, COLOR, Carousel
from .display import DisplaySettings
from .zalo import ZaloCallScreen
from .websocket import async_register as ws_register
from .dosen_core.render import CARD_LAYOUTS, CARD_THEMES
from .dosen_core.screen import FITS, PRIORITIES, ROTATIONS
from .resolve import resolve_camera, resolve_image, resolve_media, resolve_source

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.BUTTON, Platform.CAMERA, Platform.EVENT, Platform.MEDIA_PLAYER, Platform.NUMBER, Platform.SENSOR,
             Platform.SWITCH]

_MESSAGE = vol.Schema({
    vol.Optional("role", default="info"): vol.In(["user", "assistant", "caller", "info"]),
    vol.Required("text"): cv.string,
})
SERVICE_SCHEMAS = {
    "screen_carousel": vol.Schema({
        vol.Optional("entry_id"): cv.string,
        vol.Optional("pages"): vol.All(cv.ensure_list, [dict]),
        vol.Optional("seconds"): vol.All(vol.Coerce(float), vol.Range(min=3, max=3600)),
        vol.Optional("enabled", default=True): cv.boolean,
    }),
    "screen_card": vol.Schema({
        vol.Optional("entry_id"): cv.string,
        vol.Optional("title", default=""): cv.string,
        vol.Optional("value", default=""): cv.string,
        vol.Optional("caption", default=""): cv.string,
        vol.Optional("color", default="red"): COLOR,
        vol.Optional("theme", default="light"): vol.In(list(CARD_THEMES)),
        vol.Optional("layout", default="list"): vol.In(list(CARD_LAYOUTS)),
        vol.Optional("title_color"): COLOR,
        vol.Optional("main_entity"): cv.entity_id,
        vol.Optional("entities", default=[]): vol.All(cv.ensure_list, [_CARD_ENTITY]),
        vol.Optional("rows", default=[]): vol.All(cv.ensure_list, [vol.Any(_CARD_ROW, cv.string)]),
        vol.Optional("priority", default=2): vol.All(vol.Coerce(int), vol.In(PRIORITIES)),
        vol.Optional("after", default="hold"): vol.In(["off", "hold"]),
        vol.Optional("owner", default="automation"): cv.string,
        vol.Optional("ttl"): vol.All(vol.Coerce(float), vol.Range(min=1, max=86400)),
    }),
    "screen_show": vol.All(vol.Schema({
        vol.Optional("entry_id"): cv.string,
        vol.Optional("source"): cv.string,
        vol.Optional("camera_entity"): cv.entity_id,
        vol.Optional("media"): vol.Any(cv.string, dict),
        vol.Optional("loop", default=False): cv.boolean,
        vol.Optional("live"): cv.boolean,
        vol.Optional("split", default=False): cv.boolean,
        vol.Optional("priority", default=2): vol.All(vol.Coerce(int), vol.In(PRIORITIES)),
        vol.Optional("after", default="off"): vol.In(["off", "hold"]),
        vol.Optional("owner", default="automation"): cv.string,
        vol.Optional("ttl"): vol.All(vol.Coerce(float), vol.Range(min=1, max=86400)),
    }), cv.has_at_least_one_key("source", "camera_entity", "media")),
    "screen_release": vol.Schema({
        vol.Optional("entry_id"): cv.string,
        vol.Required("owner"): cv.string,
    }),
    "screen_default": vol.Schema({
        vol.Optional("entry_id"): cv.string,
        vol.Required("mode"): vol.In(DEFAULT_MODES),
        vol.Optional("rotate"): vol.All(vol.Coerce(int), vol.In(ROTATIONS)),
        vol.Optional("fit"): vol.In(FITS),
        vol.Optional("source", default=""): cv.string,
        vol.Optional("camera_entity"): cv.entity_id,
        vol.Optional("media"): vol.Any(cv.string, dict),
    }),
    "screen_text": vol.Schema({
        vol.Optional("entry_id"): cv.string,
        vol.Optional("title", default=""): cv.string,
        vol.Optional("messages", default=[]): vol.All(cv.ensure_list, [vol.Any(cv.string, _MESSAGE)]),
        vol.Optional("font_size", default=15): vol.All(vol.Coerce(int), vol.Range(min=10, max=30)),
        vol.Optional("priority", default=2): vol.All(vol.Coerce(int), vol.In(PRIORITIES)),
        vol.Optional("after", default="hold"): vol.In(["off", "hold"]),
        vol.Optional("owner", default="automation"): cv.string,
        vol.Optional("ttl"): vol.All(vol.Coerce(float), vol.Range(min=1, max=86400)),
    }),
    "screen_off": vol.Schema({vol.Optional("entry_id"): cv.string}),
    "screen_power": vol.Schema({
        vol.Optional("entry_id"): cv.string,
        vol.Required("on"): cv.boolean,
    }),
    "screen_sleep": vol.All(vol.Schema({
        vol.Optional("entry_id"): cv.string,
        vol.Optional("seconds"): vol.All(vol.Coerce(int), vol.Range(min=0, max=3600)),
        vol.Optional("luminance"): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
    }), cv.has_at_least_one_key("seconds", "luminance")),
}


class DosenCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: LocalApi) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN,
                         update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS))
        self.api = api
        self.entry = entry
        self._default_lock = asyncio.Lock()
        self.startup_done = asyncio.Event()      # lần áp màn mặc định lúc khởi động đã xong (thành công hay bỏ cuộc)
        self.in_call = False              # bộ đàm đang đưa tiếng cuộc gọi ra loa (một cuộc một lúc)
        self.zalo_call_screen = True             # tự hiện cuộc gọi Zalo (tên người gọi, hình video) lên màn camera
        self.alarm_events_on = True
        self.audio: dict = {}             # âm lượng loa/mic + chống ồn đọc từ camera (rỗng = chưa đọc)
        # Cài đặt hiển thị (đặt bằng thẻ «Dosen Pro» qua WebSocket, lưu bằng Store — xem display.py).
        self.default_mode: str | None = None
        self.default_camera = ""
        self.default_image = ""
        self.default_text = ""
        self.default_quality = "standard"        # chất lượng mã hoá hình lên màn camera (standard | high | max)
        self.carousel: Carousel | None = None   # màn luân phiên (tạo khi nạp mục cấu hình)
        self.zalo: ZaloCallScreen | None = None
        self.display: DisplaySettings | None = None   # lưu/áp cài đặt hiển thị (thẻ «Dosen Pro» điều khiển)
        self.default_weather = ""                # thực thể weather.* hiện trên đồng hồ ("" = không hiện)
        self._unsub_weather = None
        self.default_rotate = 0                  # xoay hình (độ, theo chiều kim đồng hồ) cho mọi lớp không tự nêu
        self.default_fit = "contain"             # contain = vừa khung có viền; cover = lấp đầy màn, cắt rìa
        self._on_alarm: list = []

    @property
    def ha_base(self) -> str | None:
        return (self.entry.options.get(OPT_HA_URL) or "").strip() or None

    async def apply_default(self) -> None:
        """Gửi màn hình mặc định (loại + nguồn chọn trên các thực thể) xuống camera. Chưa từng chọn loại thì
        không đụng tới mặc định đang lưu. Lỗi được ném ra để người dùng thấy trên giao diện. Các lần gọi nối đuôi
        nhau (lần khởi động và lần người dùng chọn có thể chồng nhau): mỗi lần đọc lựa chọn MỚI NHẤT khi tới lượt."""
        async with self._default_lock:
            await self._apply_default_locked()

    def camera_refresher(self, entity_id: str, base: str | None):
        """Hàm đồng bộ (gọi từ luồng của màn hình) xin địa chỉ luồng MỚI của camera HA: token luồng quay vòng nên địa chỉ lưu sẵn sẽ hết hạn."""
        def refresh() -> str | None:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                return None                                   # đang ở luồng sự kiện: chờ kết quả sẽ tự khoá cứng, dùng địa chỉ cũ
            return asyncio.run_coroutine_threadsafe(resolve_camera(self.hass, entity_id, base), self.hass.loop).result(8)
        return refresh

    async def _apply_default_locked(self) -> None:
        mode = self.default_mode
        if mode is None:
            return
        url = ""
        base = self.ha_base
        refresh = None
        if mode == "live":
            if not (self.default_camera or self.default_text):
                raise HomeAssistantError("Chọn camera ở ô «Camera mặc định» (hoặc nhập địa chỉ rtsp:// ở ô «Địa chỉ mặc định»)")
            url = await (resolve_camera(self.hass, self.default_camera, base) if self.default_camera
                         else resolve_source(self.hass, source=self.default_text, base=base))
            if self.default_camera:
                refresh = self.camera_refresher(self.default_camera, base)
        elif mode == "image":
            if not (self.default_image or self.default_text):
                raise HomeAssistantError("Chọn ảnh ở ô «Ảnh mặc định» (hoặc nhập địa chỉ ở ô «Địa chỉ mặc định»)")
            url = await resolve_source(self.hass, image_entity=self.default_image or None,
                                       source=self.default_text, base=base)
        elif mode == "video":
            if not self.default_text:
                raise HomeAssistantError("Nhập địa chỉ video ở ô «Địa chỉ mặc định»")
            url = await resolve_source(self.hass, source=self.default_text, base=base)
        try:
            await self.api.screen_default(mode, url, refresh=refresh)
        except DosenApiError as exc:
            raise HomeAssistantError(f"Dosen Pro: {exc}") from exc
        await self.async_request_refresh()

    def hass_now_minute(self) -> str:
        """Phút hiện tại theo múi giờ của HA (đồng hồ trong màn luân phiên chỉ vẽ lại khi giá trị này đổi)."""
        return dt_util.now().strftime("%H%M")

    # ── Thời tiết trên đồng hồ ────────────────────────────────────────

    @callback
    def track_weather(self) -> None:
        """Theo dõi thực thể thời tiết đang chọn: đổi là đẩy xuống màn hình (đồng hồ vẽ lại)."""
        if self._unsub_weather is not None:
            self._unsub_weather()
            self._unsub_weather = None
        if self.default_weather:
            self._unsub_weather = async_track_state_change_event(self.hass, [self.default_weather], self._on_weather_state)

    @callback
    def _on_weather_state(self, _event) -> None:
        self.hass.async_create_task(self.push_weather_safe())

    async def _forecast_range(self, entity_id: str, unit: str | None) -> tuple[float | None, float | None]:
        """(cao nhất, thấp nhất) hôm nay theo dự báo hằng ngày của thực thể; không có dự báo thì (None, None)."""
        try:
            resp = await self.hass.services.async_call("weather", "get_forecasts", {"entity_id": entity_id, "type": "daily"},
                                                       blocking=True, return_response=True)
            today = ((resp or {}).get(entity_id) or {}).get("forecast", [])[0]
            hi, lo = today.get("temperature"), today.get("templow")
        except (HomeAssistantError, IndexError, KeyError, TypeError, AttributeError):
            return None, None
        return _celsius(hi, unit), _celsius(lo, unit)

    async def weather_info(self) -> dict | None:
        """Thời tiết hiện tại của thực thể đã chọn (None nếu chưa chọn / chưa có dữ liệu), dạng đồng hồ cần."""
        eid = self.default_weather
        state = self.hass.states.get(eid) if eid else None
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        a = state.attributes
        unit = a.get("temperature_unit")
        hi, lo = await self._forecast_range(eid, unit)
        return {"condition": state.state, "temp": _celsius(a.get("temperature"), unit),
                "humidity": a.get("humidity"), "high": hi, "low": lo}

    async def push_weather(self) -> None:
        """Đọc thực thể thời tiết đã chọn và đẩy xuống đồng hồ."""
        info = await self.weather_info()
        try:
            await self.api.screen_weather(info)
        except DosenApiError as exc:
            raise HomeAssistantError(f"Dosen Pro: {exc}") from exc

    async def push_weather_safe(self) -> None:
        try:
            await self.push_weather()
        except HomeAssistantError as exc:
            _LOGGER.debug("Chưa đẩy được thời tiết xuống đồng hồ: %s", exc)

    @callback
    def set_in_call(self, in_call: bool) -> None:
        self.in_call = in_call

    @callback
    def listen_alarm(self, fn):
        self._on_alarm.append(fn)
        return lambda: self._on_alarm.remove(fn)

    @callback
    def _on_alarm_event(self, record: dict) -> None:
        """Chạy trong vòng lặp HA: một báo động camera → sự kiện bus ``dosen_pro_alarm`` (để blueprint/automation bắt,
        kể cả nút gọi của camera khi biết tên event) và thực thể «Sự kiện camera»."""
        alarm = record.get("alarm") or {}
        payload = {"entry_id": self.entry.entry_id, "event": record.get("event"), "status": alarm.get("Status"),
                   "alarm": alarm, "possible_button_press": record.get("possible_button_press", False)}
        self.hass.bus.async_fire(EVENT_ALARM, payload)
        for fn in list(self._on_alarm):
            fn(payload)

    async def _alarm_loop(self) -> None:
        """Mở phiên báo động (một kết nối riêng tới camera). Sau khi mở được, lõi tự nối lại khi rớt; việc mở lần
        đầu thất bại (camera từ chối/tắt) thì thưa dần tối đa 10 phút, và LoginGuard còn chặn thêm khi tài khoản bị khoá."""
        wait = 30
        while True:
            try:
                await self.api.events_start()
                return
            except DosenApiError as exc:
                _LOGGER.debug("alarm session: %s", exc)
                wait = min(wait * 2, 600)
            await asyncio.sleep(wait)

    async def _async_update_data(self) -> dict:
        try:
            return {"screen": await self.api.screen_status(),
                    "speaker": await self.api.speaker_status()}
        except DosenApiError as exc:
            raise UpdateFailed(str(exc)) from exc


def _coordinator(hass: HomeAssistant, entry_id: str | None) -> DosenCoordinator:
    coords: dict = hass.data.get(DOMAIN, {})
    if entry_id:
        if entry_id not in coords:
            raise HomeAssistantError(f"không có thiết bị Dosen Pro với entry_id {entry_id}")
        return coords[entry_id]
    if len(coords) == 1:
        return next(iter(coords.values()))
    raise HomeAssistantError("có nhiều thiết bị Dosen Pro: điền entry_id")


async def _handle(hass: HomeAssistant, call: ServiceCall) -> None:
    coord = _coordinator(hass, call.data.get("entry_id"))
    api, d = coord.api, call.data
    try:
        if call.service == "screen_show":
            url = await resolve_source(coord.hass, source=d.get("source"), camera_entity=d.get("camera_entity"),
                                       media=d.get("media"), base=coord.ha_base)
            refresh = coord.camera_refresher(d["camera_entity"], coord.ha_base) if d.get("camera_entity") else None
            await api.screen_show(url, d["loop"], d["after"], d["owner"], d.get("ttl"), d.get("live"),
                                  d["priority"], d["split"], refresh)
        elif call.service == "screen_text":
            await api.screen_text(d["title"], d["messages"], d["font_size"], d["after"], d["owner"],
                                  d.get("ttl"), d["priority"])
        elif call.service == "screen_carousel":
            car = coord.carousel
            try:
                car.configure(d.get("pages"), d.get("seconds"))
            except vol.Invalid as exc:
                raise HomeAssistantError(f"Trang luân phiên không hợp lệ: {exc}") from exc
            if d["enabled"]:
                await car.async_start()
            else:
                await car.async_stop()
            coord.async_update_listeners()
        elif call.service == "screen_card":
            await api.screen_card(build_card(coord.hass, d), d["after"], d["owner"], d.get("ttl"), d["priority"])
        elif call.service == "screen_release":
            await api.screen_release(d["owner"])
        elif call.service == "screen_default":
            url = ""
            if d["mode"] in ("image", "video", "live"):
                url = await resolve_source(coord.hass, source=d.get("source"), camera_entity=d.get("camera_entity"),
                                           media=d.get("media"), base=coord.ha_base)
            refresh = (coord.camera_refresher(d["camera_entity"], coord.ha_base)
                       if d["mode"] == "live" and d.get("camera_entity") else None)
            await api.screen_default(d["mode"], url, d.get("rotate"), d.get("fit"), refresh)
            if d.get("rotate") is not None:            # giữ các ô «Xoay hình / Kiểu khung hình mặc định» khớp với màn thật
                coord.default_rotate = d["rotate"]
            if d.get("fit") is not None:
                coord.default_fit = d["fit"]
            coord.display._save()
            coord.async_update_listeners()
        elif call.service == "screen_off":
            await api.screen_off()
        elif call.service == "screen_power":
            await api.screen_power(d["on"])
        elif call.service == "screen_sleep":
            await api.screen_sleep(d.get("seconds"), d.get("luminance"))
    except DosenApiError as exc:
        raise HomeAssistantError(f"Dosen Pro: {exc}") from exc
    await coord.async_request_refresh()


#: Thực thể của các cài đặt hiển thị cũ (ô chọn, ô số, công tắc, văn bản): nay gom vào thẻ «Dosen Pro». Hậu tố mã nhận dạng để dọn khỏi sổ đăng ký.
REMOVED_UNIQUE_SUFFIXES = ("-screen_brightness", "-screen_time", "-carousel-seconds", "-default_image", "-default_camera",
                           "-screen_quality", "-default_fit", "-default_screen", "-default_weather", "-default_rotate",
                           "-show_chat", "-carousel", "-default_address",
                           # Assist đã bỏ (mic chỉ phục vụ cuộc gọi Zalo): vệ tinh, ô chọn pipeline/độ nhạy, mic, nút dừng loa
                           "-satellite", "-pipeline", "-vad_sensitivity", "-mic_gain", "-mic_level", "-mute", "-btn-speaker_stop")


@callback
def _remove_stale_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Xoá khỏi sổ đăng ký các thực thể cài đặt hiển thị cũ để không còn «không khả dụng» treo trong giao diện."""
    reg = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(reg, entry.entry_id):
        if ent.unique_id.endswith(REMOVED_UNIQUE_SUFFIXES):
            reg.async_remove(ent.entity_id)


FRONTEND_URL = "/dosen_pro_static"


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Một lần cho cả HA: API WebSocket, tệp JS của thẻ (tự nạp trên mọi bảng điều khiển) và mục «Màn hình Dosen» ở thanh bên."""
    if hass.data.get(f"{DOMAIN}_frontend"):
        return
    hass.data[f"{DOMAIN}_frontend"] = True
    ws_register(hass)
    await _async_register_frontend_assets(hass)


async def _async_register_frontend_assets(hass: HomeAssistant) -> None:
    from homeassistant.components import frontend, panel_custom
    from homeassistant.components.http import StaticPathConfig
    from homeassistant.loader import async_get_integration
    version = (await async_get_integration(hass, DOMAIN)).version
    await hass.http.async_register_static_paths([StaticPathConfig(FRONTEND_URL, str(Path(__file__).parent / "frontend"), False)])
    url = f"{FRONTEND_URL}/dosen-pro-card.js?v={version}"
    frontend.add_extra_js_url(hass, url)                  # thẻ có sẵn trong danh sách thẻ, không cần thêm tài nguyên
    await panel_custom.async_register_panel(
        hass, webcomponent_name="dosen-pro-panel", frontend_url_path="dosen-pro", module_url=url, sidebar_title="Màn hình Dosen",
        sidebar_icon="mdi:monitor-cellphone", require_admin=True, config={})


_REAL_ASSETS = _async_register_frontend_assets          # test gọi bản thật dù đã thay ``_async_register_frontend_assets``


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    d = entry.data
    if not d.get(CONF_HOST):
        raise ConfigEntryError("Cấu hình cũ (kiểu dịch vụ Docker): xoá mục này rồi thêm lại với IP và tài khoản camera")
    cfg = CamConfig(cam_ip=d[CONF_HOST], cam_port=int(d.get(CONF_PORT, DEFAULT_PORT)), cam_user=d[CONF_USERNAME],
                    cam_pass=d[CONF_PASSWORD], ffmpeg=get_ffmpeg_manager(hass).binary)
    holder: dict = {}

    def _bao_dong(record: dict) -> None:                       # chạy trong luồng của lõi → đưa về vòng lặp HA
        coord_ = holder.get("coord")
        if coord_ is not None:
            try:
                hass.loop.call_soon_threadsafe(coord_._on_alarm_event, record)
            except RuntimeError:                                # vòng lặp HA đã đóng (đang tắt/khởi động lại): bỏ qua, không ghi ERROR
                pass
    camera = Camera(cfg, now=dt_util.now, on_alarm=_bao_dong)
    camera.start()
    coord = DosenCoordinator(hass, entry, LocalApi(camera))
    holder["coord"] = coord
    await coord.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord
    coord.display = DisplaySettings(coord)
    await coord.display.async_load()
    coord.carousel = Carousel(coord)
    await coord.carousel.async_load()
    coord.zalo = ZaloCallScreen(coord)
    coord.zalo.start()
    entry.async_on_unload(coord.zalo.stop)
    if not entry.data.get(CONF_INTERCOM_KEY):          # chưa có khoá bộ đàm (mục mới / vừa cấu hình lại): sinh một khoá
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_INTERCOM_KEY: secrets.token_urlsafe(24)})
    await _async_register_frontend(hass)
    if not hass.data.get(f"{DOMAIN}_view"):
        hass.http.register_view(DosenIntercomView())
        hass.data[f"{DOMAIN}_view"] = True
    if not hass.services.has_service(DOMAIN, "screen_show"):
        for name, schema in SERVICE_SCHEMAS.items():
            async def _svc(call: ServiceCall, _hass=hass) -> None:
                await _handle(_hass, call)
            hass.services.async_register(DOMAIN, name, _svc, schema=schema)
    _remove_stale_entities(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coord.alarm_events_on = bool(entry.options.get(CONF_ALARM_EVENTS, True))
    if coord.alarm_events_on:
        entry.async_create_background_task(hass, coord._alarm_loop(), "dosen_pro alarm events")
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    entry.async_on_unload(lambda: coord._unsub_weather and coord._unsub_weather())
    @callback
    def _refresh_weather(_now) -> None:
        hass.async_create_task(coord.push_weather_safe())
    entry.async_on_unload(async_track_time_interval(          # dự báo cao/thấp đổi theo ngày: làm mới mỗi 30 phút
        hass, _refresh_weather, timedelta(minutes=30)))
    entry.async_create_background_task(hass, _apply_default_safe(coord), "dosen_pro default screen")
    coord.carousel.resume_if_on()
    entry.async_create_background_task(hass, _read_audio_safe(coord), "dosen_pro read audio")
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Phiên bản 1 (kiểu dịch vụ Docker: url + khoá API) lên 2 (kết nối thẳng camera). Không đổi được dữ liệu vì thiếu tài
    khoản camera, nên chỉ nâng số phiên bản; setup sẽ báo «xoá rồi thêm lại» cho tới khi người dùng cấu hình lại."""
    if entry.version < 2:
        hass.config_entries.async_update_entry(entry, version=2)
    return True


async def _read_audio_safe(coord: DosenCoordinator) -> None:
    """Đọc một lần âm lượng loa/mic của camera lúc khởi động (một lần đăng nhập). Lỗi thì để trống, không dồn dập thử lại."""
    try:
        coord.audio = await coord.api.audio_get()
        coord.async_update_listeners()
    except DosenApiError as exc:
        _LOGGER.debug("Chưa đọc được âm lượng camera: %s", exc)


def _celsius(value, unit: str | None) -> float | None:
    """Số đo nhiệt độ về °C, làm tròn 1 số lẻ (Fahrenheit thì đổi); không đọc được thì None."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if unit and "F" in str(unit):
        v = (v - 32) * 5 / 9
    return round(v, 1)


#: Lúc HA mới khởi động, camera/ảnh mà màn mặc định cần (vd. camera go2rtc) có thể chưa nạp kịp, hoặc camera còn giữ phiên
#: cũ (Ret=503): thử lại với khoảng nghỉ tăng dần thay vì bỏ cuộc ngay (đo 15/11: bỏ cuộc → màn camera tắt mãi).
DEFAULT_RETRY_DELAYS = (0, 5, 10, 20, 30, 60, 60)


async def _apply_default_safe(coord: DosenCoordinator) -> None:
    try:
        await _apply_default_with_retries(coord)
    finally:
        coord.startup_done.set()


async def _apply_default_with_retries(coord: DosenCoordinator) -> None:
    last: Exception | None = None
    for delay in DEFAULT_RETRY_DELAYS:
        if delay:
            await asyncio.sleep(delay)
        try:
            await coord.api.screen_view(coord.default_rotate, coord.default_fit)     # khôi phục xoay/khung đã chọn trước khi tắt HA
            await coord.api.screen_quality(coord.default_quality)
            coord.track_weather()
            await coord.push_weather()
            await coord.apply_default()
            return
        except (DosenApiError, HomeAssistantError, KeyError) as exc:
            last = exc
            _LOGGER.debug("Chưa áp dụng được màn hình mặc định (%s), thử lại", exc)
    _LOGGER.warning("Không áp dụng được màn hình mặc định: %s", last)


async def _options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Tuỳ chọn đổi (luồng sự kiện, địa chỉ HA): nạp lại mục để áp dụng."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        coord = hass.data[DOMAIN].pop(entry.entry_id, None)
        if coord is not None:                                   # đóng phiên màn hình, mic, loa, báo động
            await hass.async_add_executor_job(coord.api.camera.close)
        if not hass.data[DOMAIN]:
            for name in SERVICE_SCHEMAS:
                hass.services.async_remove(DOMAIN, name)
    return ok
