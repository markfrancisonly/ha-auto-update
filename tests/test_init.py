"""Tests for installing updates and the restart required sensor."""

from __future__ import annotations

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.update import (
    ATTR_IN_PROGRESS,
    ATTR_INSTALLED_VERSION,
    ATTR_LATEST_VERSION,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.auto_update.const import DOMAIN, MAX_ATTEMPTS, RETRY_DELAY

UPDATE = "update.example_update"


class FakeMarketplace:
    """A Marketplace update entity and the install action behind it."""

    def __init__(self, hass: HomeAssistant, fail: bool = False) -> None:
        self.hass = hass
        self.fail = fail
        self.calls = 0
        self.installed = "1.0"
        self.latest = "2.0"
        er.async_get(hass).async_get_or_create(
            "update", "marketplace", "example", suggested_object_id="example_update"
        )
        hass.services.async_register("update", "install", self._install)
        self.write()

    def write(self, in_progress: bool = False) -> None:
        self.hass.states.async_set(
            UPDATE,
            "off" if self.installed == self.latest else "on",
            {
                ATTR_INSTALLED_VERSION: self.installed,
                ATTR_LATEST_VERSION: self.latest,
                ATTR_IN_PROGRESS: in_progress,
            },
        )

    async def _install(self, call: ServiceCall) -> None:
        self.calls += 1
        self.write(in_progress=True)
        if self.fail:
            # A failed install clears in_progress, as the update entity does
            self.write()
            raise HomeAssistantError("Download failed")
        self.installed = self.latest
        self.write()


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Auto update")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entity_id(
    hass: HomeAssistant, entry: MockConfigEntry, platform: str, key: str
) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert entity_id
    return entity_id


async def _wait_retry(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(timedelta(seconds=RETRY_DELAY + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_installs_waiting_update(hass: HomeAssistant) -> None:
    """An update that waits at setup is installed once."""
    fake = FakeMarketplace(hass)
    await _setup(hass)
    assert fake.calls == 1
    assert hass.states.get(UPDATE).state == "off"


async def test_installs_update_when_it_appears(hass: HomeAssistant) -> None:
    """A new release is installed as soon as its update entity turns on."""
    fake = FakeMarketplace(hass)
    await _setup(hass)
    fake.latest = "3.0"
    fake.write()
    await hass.async_block_till_done()
    assert fake.calls == 2
    assert fake.installed == "3.0"


async def test_failed_install_waits_and_gives_up(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A failing install is retried after the delay, then given up on."""
    fake = FakeMarketplace(hass, fail=True)
    await _setup(hass)
    # Clearing in_progress on failure must not queue it again at once
    assert fake.calls == 1

    for attempt in range(2, MAX_ATTEMPTS + 1):
        await _wait_retry(hass, freezer)
        assert fake.calls == attempt

    for _ in range(3):
        await _wait_retry(hass, freezer)
    fake.write()
    await hass.async_block_till_done()
    assert fake.calls == MAX_ATTEMPTS


async def test_new_version_starts_new_attempts(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Giving up holds for that version only."""
    fake = FakeMarketplace(hass, fail=True)
    await _setup(hass)
    for _ in range(MAX_ATTEMPTS):
        await _wait_retry(hass, freezer)
    assert fake.calls == MAX_ATTEMPTS

    fake.fail = False
    fake.latest = "3.0"
    fake.write()
    await hass.async_block_till_done()
    assert fake.calls == MAX_ATTEMPTS + 1
    assert fake.installed == "3.0"


async def test_busy_then_installed(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """An install refused once succeeds on the retry."""
    fake = FakeMarketplace(hass, fail=True)
    await _setup(hass)
    fake.fail = False
    await _wait_retry(hass, freezer)
    assert fake.calls == 2
    assert hass.states.get(UPDATE).state == "off"


async def test_switch(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Off holds installs; on installs what waits and retries what was given up."""
    fake = FakeMarketplace(hass, fail=True)
    entry = await _setup(hass)
    switch = _entity_id(hass, entry, "switch", "auto_update")
    assert hass.states.get(switch).state == "on"
    for _ in range(MAX_ATTEMPTS):
        await _wait_retry(hass, freezer)
    assert fake.calls == MAX_ATTEMPTS

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": switch}, blocking=True
    )
    fake.fail = False
    fake.write()
    await hass.async_block_till_done()
    assert fake.calls == MAX_ATTEMPTS

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": switch}, blocking=True
    )
    await hass.async_block_till_done()
    assert fake.calls == MAX_ATTEMPTS + 1
    assert hass.states.get(UPDATE).state == "off"


@pytest.mark.parametrize(
    "issue_ids", [["restart_required_1_tags/2.0", "restart_required_1_tags/3.0"]]
)
async def test_restart_required(hass: HomeAssistant, issue_ids: list[str]) -> None:
    """Restart repairs are hidden from Settings and mirrored by the sensor."""
    entry = await _setup(hass)
    sensor = _entity_id(hass, entry, "binary_sensor", "restart_required")
    assert hass.states.get(sensor).state == "off"

    for issue_id in issue_ids:
        ir.async_create_issue(
            hass,
            "marketplace",
            issue_id,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="restart_required",
            translation_placeholders={"name": "Example"},
        )
    await hass.async_block_till_done()

    registry = ir.async_get(hass)
    for issue_id in issue_ids:
        assert registry.async_get_issue("marketplace", issue_id).dismissed_version
    state = hass.states.get(sensor)
    assert state.state == "on"
    assert state.attributes["repositories"] == ["Example"]

    for issue_id in issue_ids:
        ir.async_delete_issue(hass, "marketplace", issue_id)
    await hass.async_block_till_done()
    assert hass.states.get(sensor).state == "off"


async def test_other_repairs_untouched(hass: HomeAssistant) -> None:
    """Only the Marketplace's restart repairs are hidden."""
    await _setup(hass)
    ir.async_create_issue(
        hass,
        "marketplace",
        "critical_1",
        is_fixable=False,
        severity=ir.IssueSeverity.CRITICAL,
        translation_key="critical",
    )
    await hass.async_block_till_done()
    assert (
        ir.async_get(hass)
        .async_get_issue("marketplace", "critical_1")
        .dismissed_version
        is None
    )


async def test_unload(hass: HomeAssistant) -> None:
    """The entry unloads cleanly."""
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
