"""Constants for Auto update."""

from homeassistant.const import Platform

DOMAIN = "auto_update"
MARKETPLACE = "marketplace"
RESTART_ISSUE_PREFIX = "restart_required_"

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SWITCH]
SIGNAL_RESTART_CHANGED = f"{DOMAIN}_restart_changed"

# The Marketplace clears in_progress a moment before it releases its install
# lock, so an install that lands in between is refused as busy.
RETRY_DELAY = 15
MAX_ATTEMPTS = 3
