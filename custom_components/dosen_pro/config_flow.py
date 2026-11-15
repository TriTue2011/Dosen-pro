"""Luồng cấu hình: địa chỉ dịch vụ Docker Dosen Pro (+ khoá API), cấu hình lại, và hộp «Cấu hình»
(màn hình mặc định chọn theo danh sách của HA)."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DosenApi, DosenApiError, DosenAuthError
from .const import CONF_ALARM_EVENTS, CONF_API_KEY, CONF_URL, DOMAIN, OPT_HA_URL


async def _kiem_tra(hass, url: str, api_key: str) -> str | None:
    """None nếu dịch vụ trả lời và khoá đúng, không thì mã lỗi."""
    api = DosenApi(async_get_clientsession(hass), url, api_key)
    try:
        await api.health()
        await api.status()           # /api/status cần khoá API: kiểm khoá luôn
    except DosenAuthError:
        return "invalid_auth"
    except DosenApiError:
        return "cannot_connect"
    return None


class DosenConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return DosenOptionsFlow()

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            err = await _kiem_tra(self.hass, url, user_input.get(CONF_API_KEY, ""))
            if err:
                errors["base"] = err
            else:
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Dosen Pro", data={
                    CONF_URL: url, CONF_API_KEY: user_input.get(CONF_API_KEY, "")})
        schema = vol.Schema({
            vol.Required(CONF_URL, default="http://127.0.0.1:8080"): str,
            vol.Optional(CONF_API_KEY, default=""): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(self, user_input=None):
        """Đổi địa chỉ dịch vụ / khoá API của mục đã có (không phải xoá rồi thêm lại)."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            err = await _kiem_tra(self.hass, url, user_input.get(CONF_API_KEY, ""))
            if err:
                errors["base"] = err
            else:
                await self.async_set_unique_id(url)
                return self.async_update_reload_and_abort(
                    entry, unique_id=url,
                    data_updates={CONF_URL: url, CONF_API_KEY: user_input.get(CONF_API_KEY, "")})
        cur = entry.data
        schema = vol.Schema({
            vol.Required(CONF_URL, default=cur.get(CONF_URL, "")): str,
            vol.Optional(CONF_API_KEY, default=cur.get(CONF_API_KEY, "")): str,
        })
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)


class DosenOptionsFlow(config_entries.OptionsFlow):
    """Hộp «Cấu hình»: chỉ các mục hạ tầng. Màn hình mặc định là THỰC THỂ trên thiết bị (ô chọn loại, camera, ảnh)."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(data={**self.config_entry.options, **user_input})
        opts = self.config_entry.options
        schema = vol.Schema({
            vol.Required(CONF_ALARM_EVENTS, default=opts.get(CONF_ALARM_EVENTS, True)): selector.BooleanSelector(),
            vol.Optional(OPT_HA_URL, description={"suggested_value": opts.get(OPT_HA_URL, "")}): selector.TextSelector(),
        })
        return self.async_show_form(step_id="init", data_schema=schema)
