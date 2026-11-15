"""Ô chọn pipeline Assist và độ nhạy "nói xong" cho vệ tinh camera."""

from __future__ import annotations

from homeassistant.components.assist_pipeline import AssistPipelineSelect, VadSensitivitySelect
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .entity import DosenEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    c = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DosenPipelineSelect(hass, c), DosenVadSelect(hass, c)])


class DosenPipelineSelect(DosenEntity, AssistPipelineSelect):
    def __init__(self, hass: HomeAssistant, coordinator) -> None:
        DosenEntity.__init__(self, coordinator)
        AssistPipelineSelect.__init__(self, hass, DOMAIN, coordinator.entry.entry_id)


class DosenVadSelect(DosenEntity, VadSensitivitySelect):
    def __init__(self, hass: HomeAssistant, coordinator) -> None:
        DosenEntity.__init__(self, coordinator)
        VadSensitivitySelect.__init__(self, hass, coordinator.entry.entry_id)
