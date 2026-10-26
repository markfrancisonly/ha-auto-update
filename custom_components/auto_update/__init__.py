"""Auto update: install Marketplace updates as soon as they appear.

Restarts are left to the user; the restart required binary sensor says when
one is owed, and the matching repairs are kept out of Settings.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .manager import AutoUpdateConfigEntry, AutoUpdater


async def async_setup_entry(hass: HomeAssistant, entry: AutoUpdateConfigEntry) -> bool:
    """Set up Auto update."""
    entry.runtime_data = AutoUpdater(hass, entry)
    # The switch restores whether installing is on before anything is queued
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await entry.runtime_data.async_setup()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AutoUpdateConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
