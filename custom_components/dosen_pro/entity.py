"""Thực thể gốc: gắn mọi thực thể của camera vào cùng một thiết bị."""

from __future__ import annotations

from homeassistant.helpers.entity import Entity

from .const import device_info


class DosenEntity(Entity):
    _attr_has_entity_name = True

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self._entry = coordinator.entry
        self._attr_device_info = device_info(coordinator.entry.entry_id)
