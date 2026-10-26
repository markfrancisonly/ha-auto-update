"""Install Marketplace updates as they appear and hide the restart repairs."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.update import (
    ATTR_IN_PROGRESS,
    ATTR_LATEST_VERSION,
    SERVICE_INSTALL,
)
from homeassistant.components.update import DOMAIN as UPDATE_DOMAIN
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
        and state.attributes.get(ATTR_IN_PROGRESS) is not True
    )


def _latest(state: State | None) -> str | None:
    return state.attributes.get(ATTR_LATEST_VERSION) if state else None


@dataclass
class _Attempts:
    """Install attempts for one version of one update."""

    version: str | None
    count: int = 0


class AutoUpdater:
    """Install pending Marketplace updates one at a time."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        # The switch sets this when it restores its state
        self.enabled = False
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        # Queued, installing or waiting to retry: state changes leave these
        # alone, so a failed install can't requeue itself past the retry delay
        self._busy: set[str] = set()
        self._attempts: dict[str, _Attempts] = {}
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
            # Turning it on again also retries what was given up on
            self._attempts.clear()
            self.async_queue_pending()

    @callback
    def async_queue_pending(self) -> None:
        """Queue every update that is waiting now."""
        for entity_id in self._update_entity_ids():
            if _is_pending(self.hass.states.get(entity_id)):
                self._async_queue(entity_id)

    def restart_required(self) -> list[str]:
        """Return the names of the packages that wait for a restart."""
        # One package can leave a repair per installed version
        return list(
            dict.fromkeys(
                (issue.translation_placeholders or {}).get("name", issue.issue_id)
                for issue in self._restart_issues()
            )
        )

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
        if not self.enabled or entity_id in self._busy or self._gave_up(entity_id):
            return
        self._busy.add(entity_id)
        self._queue.put_nowait(entity_id)

    def _gave_up(self, entity_id: str) -> bool:
        attempts = self._attempts.get(entity_id)
        return (
            attempts is not None
            and attempts.count >= MAX_ATTEMPTS
            and attempts.version == _latest(self.hass.states.get(entity_id))
        )

    async def _async_run(self) -> None:
        """Install queued updates one at a time."""
        await self._started.wait()
        while True:
            entity_id = await self._queue.get()
            if await self._async_install(entity_id):
                self._async_retry_later(entity_id)
            else:
                self._busy.discard(entity_id)

    async def _async_install(self, entity_id: str) -> bool:
        """Install one update; return True to try it again later."""
        state = self.hass.states.get(entity_id)
        if not self.enabled or not _is_pending(state):
            return False
        version = _latest(state)
        attempts = self._attempts.get(entity_id)
        if attempts is None or attempts.version != version:
            attempts = self._attempts[entity_id] = _Attempts(version)
        if attempts.count >= MAX_ATTEMPTS:
            return False
        attempts.count += 1

        error: Exception | None = None
        try:
            await self.hass.services.async_call(
                UPDATE_DOMAIN,
                SERVICE_INSTALL,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
        except HomeAssistantError as err:
            error = err
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("Installing %s failed", entity_id)
            error = err

        state = self.hass.states.get(entity_id)
        if not _is_pending(state):
            self._attempts.pop(entity_id, None)
            return False
        if _latest(state) != version:
            return True
        if attempts.count >= MAX_ATTEMPTS:
            _LOGGER.warning(
                "Giving up on %s %s after %s attempts: %s",
                entity_id,
                version,
                attempts.count,
                error or "still not installed",
            )
            return False
        _LOGGER.debug("Retrying %s in %s s: %s", entity_id, RETRY_DELAY, error)
        return True

    @callback
    def _async_retry_later(self, entity_id: str) -> None:
        """Queue an update again after the delay; it stays busy until then."""

        @callback
        def _async_retry(_now) -> None:
            self._retries.pop(entity_id, None)
            self._queue.put_nowait(entity_id)

        self._retries[entity_id] = async_call_later(
            self.hass, RETRY_DELAY, _async_retry
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
