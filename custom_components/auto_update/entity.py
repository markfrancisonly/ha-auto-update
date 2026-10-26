"""Base entity for Auto update."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .manager import AutoUpdateConfigEntry


class AutoUpdateEntity(Entity):
    """An entity on the Auto update service device."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: AutoUpdateConfigEntry, key: str) -> None:
        self.updater = entry.runtime_data
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            entry_type=DeviceEntryType.SERVICE,
        )
