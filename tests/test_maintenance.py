"""Maintenance buttons (restart, map sync, dock relocation, leave dock, RTK resync) and the
emergency nudges."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mowdeck import commands

from .fake_mowdeck import MOWER_ID, FakeMowDeck

LEAVE_DOCK = "button.luba_leave_the_dock"
RESTART = "button.luba_restart_mower"
SYNC_MAP = "button.luba_read_map_again"
RELOCATE_DOCK = "button.luba_relocate_charging_station"
RESYNC_RTK = "button.luba_read_rtk_and_dock_position_again"
FORWARD = "button.luba_emergency_nudge_forward"
BACK = "button.luba_emergency_nudge_back"
LEFT = "button.luba_emergency_nudge_left"
RIGHT = "button.luba_emergency_nudge_right"
MAINTENANCE = [LEAVE_DOCK, RESTART, SYNC_MAP, RELOCATE_DOCK, RESYNC_RTK]
NUDGES = [FORWARD, BACK, LEFT, RIGHT]


@pytest.fixture
def capable_mower(fake_api: FakeMowDeck) -> FakeMowDeck:
    """A mower with every maintenance command that MowDeck can drive, reverse included."""
    fake_api.capabilities[MOWER_ID].update(
        {
            "maintenance": [
                "restart_mower",
                "sync_map",
                "relocate_dock",
                "leave_dock",
                "resync_rtk",
            ],
            "undock": True,
            "manual_drive": True,
            "drive_reverse": True,
        }
    )
    return fake_api


@pytest.fixture
def fast_results() -> Iterator[None]:
    """Wait only briefly for command results nobody sends."""
    with patch.object(commands, "COMMAND_RESULT_TIMEOUT", 0.2):
        yield


async def press(hass: HomeAssistant, entity_id: str) -> None:
    """Press a button and wait."""
    await hass.services.async_call(
        "button", SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


def sent(fake_api: FakeMowDeck) -> list[dict[str, Any]]:
    """The command bodies the fake Fleet received."""
    return [c["command"] for c in fake_api.commands]


async def refresh_static(entry: MockConfigEntry) -> None:
    """Refetch capabilities, maps and permissions now."""
    coordinator = entry.runtime_data.coordinator
    coordinator._static_fetched_at.clear()
    await coordinator.async_refresh()


@pytest.mark.usefixtures("capable_mower", "loaded_entry")
async def test_registration(hass: HomeAssistant, entity_registry: er.EntityRegistry) -> None:
    """Leave the dock is an ordinary control, enabled; the device actions and the nudges are
    configuration and disabled by default."""
    leave = entity_registry.async_get(LEAVE_DOCK)
    assert leave is not None
    assert leave.disabled_by is None
    assert leave.entity_category is None
    assert hass.states.get(LEAVE_DOCK) is not None
    for entity_id in [*MAINTENANCE[1:], *NUDGES]:
        entry = entity_registry.async_get(entity_id)
        assert entry is not None, entity_id
        assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION, entity_id
        assert entry.entity_category is EntityCategory.CONFIG, entity_id
    restart = entity_registry.async_get(RESTART)
    assert restart is not None
    assert restart.original_device_class == "restart"


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "capable_mower", "loaded_entry")
async def test_commands(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """Each button sends its command; a nudge is one manual_drive frame of 500 ms at 0.4."""
    for entity_id in [*MAINTENANCE, *NUDGES]:
        await press(hass, entity_id)
    assert sent(fake_api) == [
        {"kind": "leave_dock"},
        {"kind": "restart_mower"},
        {"kind": "sync_map"},
        {"kind": "relocate_dock"},
        {"kind": "resync_rtk"},
        {"kind": "manual_drive", "linear": 0.4, "angular": 0.0, "ttl_ms": 500},
        {"kind": "manual_drive", "linear": -0.4, "angular": 0.0, "ttl_ms": 500},
        {"kind": "manual_drive", "linear": 0.0, "angular": 0.4, "ttl_ms": 500},
        {"kind": "manual_drive", "linear": 0.0, "angular": -0.4, "ttl_ms": 500},
    ]


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "capable_mower", "loaded_entry")
async def test_refusals(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """The controller's state rules come back as translated errors with MowDeck's reason."""
    fake_api.command_results["restart_mower"] = (
        "rejected",
        "the mower is mowing; a restart needs it docked, charging or idle (end the job first)",
    )
    with pytest.raises(HomeAssistantError) as err:
        await press(hass, RESTART)
    assert err.value.translation_key == "command_refused"
    assert err.value.translation_placeholders == {
        "command": "restart_mower",
        "status": "rejected",
        "reason": "the mower is mowing; a restart needs it docked, charging or idle "
        "(end the job first)",
    }

    fake_api.command_results["manual_drive"] = (
        "rejected",
        "pause or end the job before driving manually",
    )
    with pytest.raises(HomeAssistantError) as err:
        await press(hass, FORWARD)
    assert err.value.translation_placeholders is not None
    assert err.value.translation_placeholders["command"] == "manual_drive"

    fake_api.command_results["leave_dock"] = ("failed", "mower is not connected")
    with pytest.raises(HomeAssistantError):
        await press(hass, LEAVE_DOCK)

    fake_api.command_results["sync_map"] = ("accepted", None)
    await press(hass, SYNC_MAP)


@pytest.mark.usefixtures("capable_mower", "loaded_entry", "fast_results")
async def test_no_verdict_counts_as_sent(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """Without a result in time the press counts as sent."""
    await press(hass, LEAVE_DOCK)
    assert sent(fake_api) == [{"kind": "leave_dock"}]


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "capable_mower")
async def test_permissions(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    loaded_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A 403 names the permission of the kind; the buttons follow the token's relations."""
    fake_api.forbid_commands = True
    for entity_id, permission in (
        (RESTART, "fleet.manage"),
        (LEAVE_DOCK, "mower.control"),
        (FORWARD, "mower.drive"),
    ):
        with pytest.raises(HomeAssistantError) as err:
            await press(hass, entity_id)
        assert err.value.translation_key == "command_forbidden"
        assert err.value.translation_placeholders is not None
        assert err.value.translation_placeholders["permission"] == permission
    fake_api.forbid_commands = False

    fake_api.relations = {"can_view", "can_control"}
    await refresh_static(loaded_entry)
    await hass.async_block_till_done()
    assert entity_registry.async_get(LEAVE_DOCK) is not None
    for entity_id in [*MAINTENANCE[1:], *NUDGES]:
        assert entity_registry.async_get(entity_id) is None, entity_id


@pytest.mark.usefixtures("capable_mower")
async def test_capabilities(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    loaded_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Only the listed maintenance commands; no reverse nudge without drive_reverse, no nudge
    without manual_drive."""
    fake_api.capabilities[MOWER_ID].update({"maintenance": ["sync_map"], "drive_reverse": False})
    await refresh_static(loaded_entry)
    await hass.async_block_till_done()
    assert entity_registry.async_get(SYNC_MAP) is not None
    for entity_id in (LEAVE_DOCK, RESTART, RELOCATE_DOCK, RESYNC_RTK, BACK):
        assert entity_registry.async_get(entity_id) is None, entity_id
    assert entity_registry.async_get(FORWARD) is not None

    fake_api.capabilities[MOWER_ID].update({"maintenance": [], "manual_drive": False})
    await refresh_static(loaded_entry)
    await hass.async_block_till_done()
    for entity_id in [*MAINTENANCE, *NUDGES]:
        assert entity_registry.async_get(entity_id) is None, entity_id
