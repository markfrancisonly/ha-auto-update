"""Binary sensor that is on while an installed update waits for a restart."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import AutoUpdateConfigEntry
from .const import SIGNAL_RESTART_CHANGED


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AutoUpdateConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the restart required sensor."""
    async_add_entities([RestartRequiredSensor(entry)])


class RestartRequiredSensor(BinarySensorEntity):
    """Mirrors the Marketplace's restart required repairs, which any restart clears."""

    _attr_name = "Restart required"
    _attr_icon = "mdi:restart-alert"
    _attr_should_poll = False

    def __init__(self, entry: AutoUpdateConfigEntry) -> None:
        self._updater = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_restart_required"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_RESTART_CHANGED, self._async_refresh
            )
        )
        self._async_refresh(write=False)

    @callback
    def _async_refresh(self, write: bool = True) -> None:
        waiting = self._updater.restart_required()
        self._attr_is_on = bool(waiting)
        self._attr_extra_state_attributes = {"repositories": waiting}
        if write:
            self.async_write_ha_state()
