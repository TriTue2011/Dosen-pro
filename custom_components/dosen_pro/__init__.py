"""Dosen Pro: màn hình, loa và mic của camera DOSEN RPO V1 như thiết bị của HA."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryError, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import DosenApiError, LocalApi
from .const import (CONF_ALARM_EVENTS, CONF_URL, DEFAULT_MODES, DEFAULT_PORT, DOMAIN, EVENT_ALARM,
                    OPT_HA_URL, SCAN_INTERVAL_SECONDS)
from .dosen_core.camera import Camera
from .dosen_core.config import CamConfig
from .resolve import resolve_camera, resolve_image, resolve_media, resolve_source

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.ASSIST_SATELLITE, Platform.BUTTON, Platform.EVENT, Platform.MEDIA_PLAYER,
             Platform.NUMBER, Platform.SELECT, Platform.SENSOR, Platform.SWITCH, Platform.TEXT]

_MESSAGE = vol.Schema({
    vol.Optional("role", default="info"): vol.In(["user", "assistant", "caller", "info"]),
    vol.Required("text"): cv.string,
})
SERVICE_SCHEMAS = {
    "screen_show": vol.All(vol.Schema({
        vol.Optional("entry_id"): cv.string,
        vol.Optional("source"): cv.string,
        vol.Optional("camera_entity"): cv.entity_id,
        vol.Optional("media"): vol.Any(cv.string, dict),
        vol.Optional("loop", default=False): cv.boolean,
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
        vol.Optional("source", default=""): cv.string,
        vol.Optional("camera_entity"): cv.entity_id,
        vol.Optional("media"): vol.Any(cv.string, dict),
    }),
    "screen_text": vol.Schema({
        vol.Optional("entry_id"): cv.string,
        vol.Optional("title", default=""): cv.string,
        vol.Optional("messages", default=[]): vol.All(cv.ensure_list, [vol.Any(cv.string, _MESSAGE)]),
        vol.Optional("font_size", default=15): vol.All(vol.Coerce(int), vol.Range(min=10, max=30)),
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
    "speaker_play": vol.Schema({
        vol.Optional("entry_id"): cv.string,
        vol.Required("source"): cv.string,
        vol.Optional("volume", default=1.0): vol.All(vol.Coerce(float), vol.Range(min=0, max=4)),
    }),
    "speaker_stop": vol.Schema({vol.Optional("entry_id"): cv.string}),
}


class DosenCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: LocalApi) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN,
                         update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS))
        self.api = api
        self.entry = entry
        # Trạng thái chạy của vệ tinh Assist (đặt bởi các thực thể công tắc/số).
        self.mic_muted = False
        self.mic_gain_db = 0.0
        self.show_chat = True
        self.alarm_events_on = True
        # Màn hình mặc định (đặt bởi các thực thể select/text trên thiết bị, khôi phục sau khi khởi động lại).
        self.default_mode: str | None = None
        self.default_camera = ""
        self.default_image = ""
        self.default_text = ""
        self.mic_level_db: float | None = None   # mức tiếng mic gần nhất (dBFS), None = chưa có luồng
        self._on_level: list = []
        self._on_alarm: list = []
        self._on_mute: list = []
        self._on_gain: list = []

    @property
    def ha_base(self) -> str | None:
        return (self.entry.options.get(OPT_HA_URL) or "").strip() or None

    async def apply_default(self) -> None:
        """Gửi màn hình mặc định (loại + nguồn chọn trên các thực thể) xuống dịch vụ. Chưa từng chọn loại thì
        không đụng tới mặc định dịch vụ đang lưu. Lỗi được ném ra để người dùng thấy trên giao diện."""
        mode = self.default_mode
        if mode is None:
            return
        url = ""
        base = self.ha_base
        if mode == "live":
            if not (self.default_camera or self.default_text):
                raise HomeAssistantError("Chọn camera ở ô «Camera mặc định» (hoặc nhập địa chỉ rtsp:// ở ô «Địa chỉ mặc định»)")
            url = await (resolve_camera(self.hass, self.default_camera, base) if self.default_camera
                         else resolve_source(self.hass, source=self.default_text, base=base))
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
            await self.api.screen_default(mode, url)
        except DosenApiError as exc:
            raise HomeAssistantError(f"Dosen Pro: {exc}") from exc
        await self.async_request_refresh()

    @callback
    def set_mic_level(self, db: float | None) -> None:
        self.mic_level_db = db
        for fn in list(self._on_level):
            fn()

    @callback
    def listen_level(self, fn):
        self._on_level.append(fn)
        return lambda: self._on_level.remove(fn)

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

    @callback
    def set_mic_muted(self, muted: bool) -> None:
        self.mic_muted = muted
        for fn in list(self._on_mute):
            fn(muted)

    @callback
    def listen_mute(self, fn):
        self._on_mute.append(fn)
        return lambda: self._on_mute.remove(fn)

    @callback
    def set_mic_gain(self, db: float) -> None:
        if db == self.mic_gain_db:
            return
        self.mic_gain_db = db
        for fn in list(self._on_gain):
            fn(db)

    @callback
    def listen_gain(self, fn):
        self._on_gain.append(fn)
        return lambda: self._on_gain.remove(fn)

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
            await api.screen_show(url, d["loop"], d["after"], d["owner"], d.get("ttl"))
        elif call.service == "screen_text":
            await api.screen_text(d["title"], d["messages"], d["font_size"], d["after"], d["owner"],
                                  d.get("ttl"))
        elif call.service == "screen_release":
            await api.screen_release(d["owner"])
        elif call.service == "screen_default":
            url = ""
            if d["mode"] in ("image", "video", "live"):
                url = await resolve_source(coord.hass, source=d.get("source"), camera_entity=d.get("camera_entity"),
                                           media=d.get("media"), base=coord.ha_base)
            await api.screen_default(d["mode"], url)
        elif call.service == "screen_off":
            await api.screen_off()
        elif call.service == "screen_power":
            await api.screen_power(d["on"])
        elif call.service == "screen_sleep":
            await api.screen_sleep(d.get("seconds"), d.get("luminance"))
        elif call.service == "speaker_play":
            await api.speaker_play(d["source"], d["volume"])
        elif call.service == "speaker_stop":
            await api.speaker_stop()
    except DosenApiError as exc:
        raise HomeAssistantError(f"Dosen Pro: {exc}") from exc
    await coord.async_request_refresh()


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
            hass.loop.call_soon_threadsafe(coord_._on_alarm_event, record)
    camera = Camera(cfg, now=dt_util.now, on_alarm=_bao_dong)
    camera.start()
    coord = DosenCoordinator(hass, entry, LocalApi(camera))
    holder["coord"] = coord
    await coord.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord
    if not hass.services.has_service(DOMAIN, "screen_show"):
        for name, schema in SERVICE_SCHEMAS.items():
            async def _svc(call: ServiceCall, _hass=hass) -> None:
                await _handle(_hass, call)
            hass.services.async_register(DOMAIN, name, _svc, schema=schema)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coord.alarm_events_on = bool(entry.options.get(CONF_ALARM_EVENTS, True))
    if coord.alarm_events_on:
        entry.async_create_background_task(hass, coord._alarm_loop(), "dosen_pro alarm events")
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    entry.async_create_background_task(hass, _apply_default_safe(coord), "dosen_pro default screen")
    return True


async def _apply_default_safe(coord: DosenCoordinator) -> None:
    try:
        await coord.apply_default()
    except (DosenApiError, HomeAssistantError, KeyError) as exc:
        _LOGGER.warning("Không áp dụng được màn hình mặc định: %s", exc)


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
