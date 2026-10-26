"""Binary sensor that is on while an installed update waits for a restart."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import AutoUpdateEntity
from .manager import AutoUpdateConfigEntry

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AutoUpdateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the restart required sensor."""
    async_add_entities([RestartRequiredSensor(entry)])


class RestartRequiredSensor(AutoUpdateEntity, BinarySensorEntity):
    """Mirrors the Marketplace's restart required repairs, which any restart clears."""

    def __init__(self, entry: AutoUpdateConfigEntry) -> None:
        super().__init__(entry, "restart_required")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.updater.signal_restart, self._async_refresh
            )
        )
        self._update_attrs()

    @callback
    def _async_refresh(self) -> None:
        self._update_attrs()
        self.async_write_ha_state()

    def _update_attrs(self) -> None:
        waiting = self.updater.restart_required()
        self._attr_is_on = bool(waiting)
        self._attr_extra_state_attributes = {"repositories": waiting}
