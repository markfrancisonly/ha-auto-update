"""Switch that turns automatic installing on and off."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import AutoUpdateEntity
from .manager import AutoUpdateConfigEntry

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AutoUpdateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the auto update switch."""
    async_add_entities([AutoUpdateSwitch(entry)])


class AutoUpdateSwitch(AutoUpdateEntity, SwitchEntity, RestoreEntity):
    """On: updates install as soon as they appear."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = None

    def __init__(self, entry: AutoUpdateConfigEntry) -> None:
        super().__init__(entry, "auto_update")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        self._attr_is_on = last is None or last.state != STATE_OFF
        self.updater.async_set_enabled(self._attr_is_on)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._set(False)

    def _set(self, on: bool) -> None:
        self._attr_is_on = on
        self.updater.async_set_enabled(on)
        self.async_write_ha_state()
