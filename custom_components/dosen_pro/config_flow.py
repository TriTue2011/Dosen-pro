"""Luồng cấu hình: địa chỉ dịch vụ Docker Dosen Pro (+ khoá API nếu có)."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DosenApi, DosenApiError, DosenAuthError
from .const import CONF_API_KEY, CONF_URL, DOMAIN


class DosenConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            api = DosenApi(async_get_clientsession(self.hass), url, user_input.get(CONF_API_KEY, ""))
            try:
                await api.health()
                await api.status()           # /api/status cần khoá API: kiểm khoá luôn
            except DosenAuthError:
                errors["base"] = "invalid_auth"
            except DosenApiError:
                errors["base"] = "cannot_connect"
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
