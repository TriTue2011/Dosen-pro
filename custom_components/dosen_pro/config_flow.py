"""Luồng cấu hình: IP, tài khoản và mật khẩu camera (cấu hình lại được), và hộp «Cấu hình» (hạ tầng)."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .api import DosenApiError, DosenAuthError, phan_loai
from .const import CONF_ALARM_EVENTS, DEFAULT_PORT, DOMAIN, OPT_HA_URL
from .dosen_core.camera import Camera
from .dosen_core.config import CamConfig


async def _kiem_tra(hass, host: str, port: int, user: str, password: str) -> str | None:
    """Đăng nhập thử MỘT lần. None nếu được, không thì mã lỗi: cannot_connect / invalid_auth / account_locked."""
    cam = Camera(CamConfig(cam_ip=host, cam_port=port, cam_user=user, cam_pass=password,
                           ffmpeg=get_ffmpeg_manager(hass).binary))
    try:
        await hass.async_add_executor_job(cam.check_login)
    except (OSError, Exception) as exc:                     # noqa: BLE001 — mọi lỗi đều quy về một mã cho người dùng
        err = phan_loai(exc)
        if isinstance(err, DosenAuthError):
            text = str(err)
            return "account_locked" if ("Ret=205" in text or "login paused" in text or "Ret=206" in text) else "invalid_auth"
        return "cannot_connect"
    return None


def _schema(cur: dict) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_HOST, default=cur.get(CONF_HOST, "")): str,
        vol.Required(CONF_PORT, default=cur.get(CONF_PORT, DEFAULT_PORT)): int,
        vol.Required(CONF_USERNAME, default=cur.get(CONF_USERNAME, "")): str,
        vol.Required(CONF_PASSWORD, default=cur.get(CONF_PASSWORD, "")): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
    })


class DosenConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return DosenOptionsFlow()

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            err = await _kiem_tra(self.hass, host, user_input[CONF_PORT], user_input[CONF_USERNAME],
                                  user_input[CONF_PASSWORD])
            if err:
                errors["base"] = err
            else:
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=f"Dosen Pro ({host})", data={**user_input, CONF_HOST: host})
        return self.async_show_form(step_id="user", data_schema=_schema(user_input or {}), errors=errors)

    async def async_step_reconfigure(self, user_input=None):
        """Đổi IP / tài khoản / mật khẩu của mục đã có (không phải xoá rồi thêm lại)."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            err = await _kiem_tra(self.hass, host, user_input[CONF_PORT], user_input[CONF_USERNAME],
                                  user_input[CONF_PASSWORD])
            if err:
                errors["base"] = err
            else:
                await self.async_set_unique_id(host)
                return self.async_update_reload_and_abort(
                    entry, unique_id=host, title=f"Dosen Pro ({host})", data={**user_input, CONF_HOST: host})
        return self.async_show_form(step_id="reconfigure", data_schema=_schema(user_input or dict(entry.data)),
                                    errors=errors)


class DosenOptionsFlow(config_entries.OptionsFlow):
    """Hộp «Cấu hình»: chỉ các mục hạ tầng. Màn hình mặc định là THỰC THỂ trên thiết bị."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(data={**self.config_entry.options, **user_input})
        opts = self.config_entry.options
        schema = vol.Schema({
            vol.Required(CONF_ALARM_EVENTS, default=opts.get(CONF_ALARM_EVENTS, True)): selector.BooleanSelector(),
            vol.Optional(OPT_HA_URL, description={"suggested_value": opts.get(OPT_HA_URL, "")}): selector.TextSelector(),
        })
        return self.async_show_form(step_id="init", data_schema=schema)
