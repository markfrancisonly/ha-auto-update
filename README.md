# Auto Update

Installs Home Assistant Marketplace updates as soon as they appear, so none of
them waits in Settings. Requires Home Assistant 2026.11 or later, where the
Marketplace is built in.

Installs run one at a time through the normal `update.install` action, the
same path as the Install button. An update you skip stays skipped. Nothing
polls: an install starts when an update entity turns on.

Restarting stays with you. When an installed integration needs a restart, the
Marketplace raises a "restart required" repair. Auto Update ignores that repair
so Settings stays quiet, and turns on a binary sensor instead. Any restart
clears both.

## Installation

### Marketplace

1. Open **Settings → Marketplace**
2. Click the **⋮** menu (top right) → **Custom repositories**
3. Add this URL and set the category to **Integration**, then click **Add**:
   ```
   https://github.com/markfrancisonly/ha-auto-update
   ```
4. Find **Auto Update** in the Marketplace and click **Download**
5. Restart Home Assistant

### Manual

Copy `custom_components/auto_update/` into your Home Assistant
`config/custom_components/` and restart Home Assistant.

### Setup

**Settings → Devices & services → Add integration → Auto Update.** There is
nothing to configure.

Someone has to accept the Marketplace risk warning once (**Settings →
Marketplace**). Until then the Marketplace refuses installs that no user
started, and Auto Update gives up on each update after three tries, with a
warning in the log.

## Entities

Both sit on an **Auto update** service device.

- `switch.auto_update`: on (default) installs updates as they appear.
  Turning it on installs everything already waiting.
- `binary_sensor.auto_update_restart_required`: on while an installed update
  waits for a restart. Attribute `repositories` lists them.

Restart on your own schedule, for example:

```yaml
triggers:
  - trigger: time
    at: "04:00:00"
conditions:
  - condition: state
    entity_id: binary_sensor.auto_update_restart_required
    state: "on"
actions:
  - action: homeassistant.restart
```

## Notes

- A failed install is tried again after 15 seconds, up to three times for
  the same version. After that the update waits for a newer version, a
  restart, or the switch being turned off and on. A first try that the
  Marketplace refuses because it is still finishing another install succeeds
  on the retry.
- Cards and themes install too and never need a restart; browsers pick them up
  on their next reload.

## Development

```
pip install -r requirements_test.txt
pytest
```
