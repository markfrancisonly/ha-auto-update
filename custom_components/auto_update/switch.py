"""Switch that turns automatic installing on and off."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import AutoUpdateConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AutoUpdateConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the auto update switch."""
    async_add_entities([AutoUpdateSwitch(entry)])


class AutoUpdateSwitch(SwitchEntity, RestoreEntity):
    """On: Marketplace updates install as soon as they appear."""

    _attr_name = "Auto update"
    _attr_icon = "mdi:package-up"
    _attr_should_poll = False

    def __init__(self, entry: AutoUpdateConfigEntry) -> None:
        self._updater = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_auto_update"

    async def async_added_to_hass(self) -> None:
        last = await self.async_get_last_state()
        self._attr_is_on = last is None or last.state != STATE_OFF
        self._updater.async_set_enabled(self._attr_is_on)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._set(False)

    def _set(self, on: bool) -> None:
        self._attr_is_on = on
        self._updater.async_set_enabled(on)
        self.async_write_ha_state()
