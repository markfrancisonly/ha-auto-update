"""Install Marketplace updates as they appear and hide the restart repairs."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from homeassistant.components.update import DOMAIN as UPDATE_DOMAIN
from homeassistant.components.update import SERVICE_INSTALL
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, STATE_ON
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.start import async_at_started

from .const import (
    DOMAIN,
    MARKETPLACE,
    MAX_ATTEMPTS,
    RESTART_ISSUE_PREFIX,
    RETRY_DELAY,
    SIGNAL_RESTART_CHANGED,
)

_LOGGER = logging.getLogger(__name__)


@callback
def _is_update_entity(data: er.EventEntityRegistryUpdatedData) -> bool:
    return data["entity_id"].startswith(f"{UPDATE_DOMAIN}.")


@callback
def _is_restart_issue(data: ir.EventIssueRegistryUpdatedData) -> bool:
    return data["domain"] == MARKETPLACE and data["issue_id"].startswith(
        RESTART_ISSUE_PREFIX
    )


def _is_pending(state: State | None) -> bool:
    """Return if an update entity offers an update that is not installing."""
    return (
        state is not None
        and state.state == STATE_ON
        and state.attributes.get("in_progress") is not True
    )


class AutoUpdater:
    """Install pending Marketplace updates one at a time."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        # The switch sets this when it restores its state
        self.enabled = False
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._queued: set[str] = set()
        self._attempts: dict[str, int] = {}
        self._retries: dict[str, Callable[[], None]] = {}
        self._unsub_state: Callable[[], None] | None = None
        self._started = asyncio.Event()

    async def async_setup(self) -> None:
        """Start listening and installing."""
        self.entry.async_on_unload(
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED,
                self._async_registry_updated,
                event_filter=_is_update_entity,
            )
        )
        self.entry.async_on_unload(
            self.hass.bus.async_listen(
                ir.EVENT_REPAIRS_ISSUE_REGISTRY_UPDATED,
                self._async_issue_updated,
                event_filter=_is_restart_issue,
            )
        )
        self.entry.async_on_unload(self._async_stop)
        self._async_track()
        for issue in self._restart_issues():
            self._async_hide_issue(issue.issue_id)

        self.entry.async_create_background_task(
            self.hass, self._async_run(), f"{DOMAIN} installer"
        )
        self.entry.async_on_unload(async_at_started(self.hass, self._async_started))

    @callback
    def _async_started(self, _hass: HomeAssistant) -> None:
        """Install nothing while Home Assistant is still starting."""
        self._started.set()
        self.async_queue_pending()

    @callback
    def async_set_enabled(self, enabled: bool) -> None:
        """Turn installing on or off."""
        self.enabled = enabled
        if enabled:
            self.async_queue_pending()

    @callback
    def async_queue_pending(self) -> None:
        """Queue every update that is waiting now."""
        for entity_id in self._update_entity_ids():
            if _is_pending(self.hass.states.get(entity_id)):
                self._async_queue(entity_id)

    def restart_required(self) -> list[str]:
        """Return the names of the packages that wait for a restart."""
        return [
            (issue.translation_placeholders or {}).get("name", issue.issue_id)
            for issue in self._restart_issues()
        ]

    def _restart_issues(self) -> list[ir.IssueEntry]:
        return [
            issue
            for (domain, issue_id), issue in ir.async_get(self.hass).issues.items()
            if domain == MARKETPLACE
            and issue_id.startswith(RESTART_ISSUE_PREFIX)
            and issue.active
        ]

    def _update_entity_ids(self) -> list[str]:
        return [
            entry.entity_id
            for entry in er.async_get(self.hass).entities.values()
            if entry.platform == MARKETPLACE and entry.domain == UPDATE_DOMAIN
        ]

    @callback
    def _async_track(self) -> None:
        """Follow the Marketplace update entities that exist now."""
        if self._unsub_state:
            self._unsub_state()
        self._unsub_state = async_track_state_change_event(
            self.hass, self._update_entity_ids(), self._async_state_changed
        )

    @callback
    def _async_registry_updated(self, _event: Event) -> None:
        self._async_track()

    @callback
    def _async_state_changed(self, event: Event[EventStateChangedData]) -> None:
        if _is_pending(event.data["new_state"]):
            self._async_queue(event.data["entity_id"])

    @callback
    def _async_queue(self, entity_id: str) -> None:
        if not self.enabled or entity_id in self._queued:
            return
        self._queued.add(entity_id)
        self._queue.put_nowait(entity_id)

    async def _async_run(self) -> None:
        """Install queued updates one at a time."""
        await self._started.wait()
        while True:
            entity_id = await self._queue.get()
            self._queued.discard(entity_id)
            if not self.enabled or not _is_pending(self.hass.states.get(entity_id)):
                continue
            try:
                await self.hass.services.async_call(
                    UPDATE_DOMAIN,
                    SERVICE_INSTALL,
                    {ATTR_ENTITY_ID: entity_id},
                    blocking=True,
                )
            except HomeAssistantError as err:
                self._async_retry(entity_id, err)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Installing %s failed", entity_id)
            else:
                self._attempts.pop(entity_id, None)

    @callback
    def _async_retry(self, entity_id: str, err: HomeAssistantError) -> None:
        attempt = self._attempts.get(entity_id, 0) + 1
        if attempt >= MAX_ATTEMPTS:
            self._attempts.pop(entity_id, None)
            _LOGGER.warning("Could not install %s: %s", entity_id, err)
            return
        self._attempts[entity_id] = attempt
        _LOGGER.debug("Retrying %s in %s s: %s", entity_id, RETRY_DELAY, err)

        @callback
        def _async_again(_now) -> None:
            self._retries.pop(entity_id, None)
            self._async_queue(entity_id)

        self._retries[entity_id] = async_call_later(
            self.hass, RETRY_DELAY, _async_again
        )

    @callback
    def _async_issue_updated(self, event: Event) -> None:
        if event.data["action"] != "remove":
            self._async_hide_issue(event.data["issue_id"])
        async_dispatcher_send(self.hass, SIGNAL_RESTART_CHANGED)

    @callback
    def _async_hide_issue(self, issue_id: str) -> None:
        """Ignore a restart repair; issues() and this integration still see it."""
        issue = ir.async_get(self.hass).async_get_issue(MARKETPLACE, issue_id)
        if issue is not None and issue.active and issue.dismissed_version is None:
            ir.async_ignore_issue(self.hass, MARKETPLACE, issue_id, True)

    @callback
    def _async_stop(self) -> None:
        if self._unsub_state:
            self._unsub_state()
        for unsub in self._retries.values():
            unsub()
        self._retries.clear()
