"""The one blade change threshold (sensor attribute, number and the follow-the-mower button)
and the mower's lifetime totals next to MowDeck's own counts."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mowdeck.health import blade_threshold

from .fake_mowdeck import MOWER_ID, FakeMowDeck

BLADE = "sensor.luba_blade_hours_since_change"
THRESHOLD = "number.luba_blade_change_reminder"
FOLLOW = "button.luba_blade_change_reminder_follow_the_mower"
MOWING = "sensor.luba_mowing_hours"
DISTANCE = "sensor.luba_distance_driven"
MOWING_MOWDECK = "sensor.luba_mowing_hours_since_mowdeck"
DISTANCE_MOWDECK = "sensor.luba_distance_driven_since_mowdeck"

# The live LUBA 2 AWD X of 2026-10-24: its own counter at 82.59 h of its own 100 h warning,
# MowDeck's default 50 h, MowDeck's own counts only since 2026-10-16.
LIVE: dict[str, Any] = {
    "blade_hours": 0.1,
    "blade_hours_since_change": 0.1,
    "blade_hours_threshold": 50.0,
    "blade_hours_threshold_custom": False,
    "blade_hours_threshold_effective": 100.0,
    "blade_hours_threshold_source": "device",
    "device_blade_hours": 82.59,
    "device_blade_warn_hours": 100.0,
    "blade_source": "device",
    "mowing_hours": 0.14,
    "distance_m": 151.0,
    "device_mowing_hours": 1012.5,
    "device_distance_m": 432100.0,
}


def state_of(hass: HomeAssistant, entity_id: str) -> Any:
    """The state object of an entity (asserting it exists)."""
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


async def setup(
    hass: HomeAssistant, fake_api: FakeMowDeck, entry: MockConfigEntry, **health: Any
) -> None:
    """Load the entry with the live health totals (changed by ``health``)."""
    fake_api.blade_thresholds = []
    fake_api.health[MOWER_ID].update(LIVE | health)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def unload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Unload the entry again."""
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_one_threshold_on_sensor_and_number(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """The sensor's threshold and the number both show the mower's own 100 h, not 50 h."""
    await setup(hass, fake_api, config_entry)
    blade = state_of(hass, BLADE)
    assert blade.state == "82.59"
    assert blade.attributes["threshold"] == 100.0
    assert blade.attributes["threshold_source"] == "device"
    number = state_of(hass, THRESHOLD)
    assert number.state == "100.0"
    assert (number.attributes["custom"], number.attributes["source"]) == (False, "device")
    await unload(hass, config_entry)


async def test_setting_the_number_and_following_the_mower_again(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """Setting the number sets MowDeck's threshold; the button drops it again."""
    await setup(hass, fake_api, config_entry)
    await hass.services.async_call(
        "number", "set_value", {ATTR_ENTITY_ID: THRESHOLD, "value": 80}, blocking=True
    )
    assert fake_api.blade_thresholds == [{"hours": 80.0}]
    number = state_of(hass, THRESHOLD)
    assert (number.state, number.attributes["source"]) == ("80.0", "custom")
    blade = state_of(hass, BLADE)
    assert (blade.attributes["threshold"], blade.attributes["threshold_source"]) == (80.0, "custom")

    await hass.services.async_call("button", SERVICE_PRESS, {ATTR_ENTITY_ID: FOLLOW}, blocking=True)
    await hass.async_block_till_done()
    assert fake_api.blade_thresholds == [{"hours": 80.0}, {"hours": None}]
    number = state_of(hass, THRESHOLD)
    assert (number.state, number.attributes["custom"]) == ("100.0", False)
    assert state_of(hass, BLADE).attributes["threshold"] == 100.0
    await unload(hass, config_entry)


async def test_follow_the_mower_needs_fleet_manage(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """A 403 on the button names fleet.manage."""
    await setup(hass, fake_api, config_entry)
    fake_api.relations = {"can_view", "can_control"}
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "button", SERVICE_PRESS, {ATTR_ENTITY_ID: FOLLOW}, blocking=True
        )
    assert err.value.translation_placeholders == {
        "command": "blade_threshold",
        "permission": "fleet.manage",
    }
    await unload(hass, config_entry)


@pytest.mark.usefixtures("loaded_entry")
async def test_no_follow_button_before_mowdeck_0_8(hass: HomeAssistant) -> None:
    """Without per-mower thresholds there is nothing to drop: no button."""
    assert hass.states.get(FOLLOW) is None


@pytest.mark.parametrize(
    ("health", "expected"),
    [
        # MowDeck 0.18.1 names the effective threshold.
        (LIVE, (100.0, "device")),
        # Older MowDeck: set in MowDeck, else the mower's warning, else MowDeck's threshold.
        ({"blade_hours_threshold": 80.0, "blade_hours_threshold_custom": True}, (80.0, "custom")),
        (
            {
                "blade_hours_threshold": 50.0,
                "blade_source": "device",
                "device_blade_warn_hours": 100.0,
            },
            (100.0, "device"),
        ),
        ({"blade_hours_threshold": 50.0, "blade_source": "telemetry"}, (50.0, "default")),
        (
            {
                "blade_hours_threshold": 50.0,
                "blade_source": "device",
                "device_blade_warn_hours": None,
            },
            (50.0, "default"),
        ),
    ],
)
def test_blade_threshold(health: dict[str, Any], expected: tuple[Any, str]) -> None:
    """The one threshold: custom, else the mower's own warning, else the default."""
    assert blade_threshold(health) == expected


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_lifetime_totals_of_the_mower(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """Mowing hours and distance show the mower's lifetime values; MowDeck's own counts
    have their own sensors."""
    await setup(hass, fake_api, config_entry)
    mowing = state_of(hass, MOWING)
    assert (mowing.state, mowing.attributes["source"]) == ("1012.5", "device")
    distance = state_of(hass, DISTANCE)
    assert distance.attributes["unit_of_measurement"] == "km"
    assert float(distance.state) == pytest.approx(432.1)
    assert distance.attributes["source"] == "device"
    assert state_of(hass, MOWING_MOWDECK).state == "0.14"
    assert float(state_of(hass, DISTANCE_MOWDECK).state) == pytest.approx(0.151)
    await unload(hass, config_entry)


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_lifetime_totals_without_device_counters(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """A mower without lifetime counters (or an older MowDeck): MowDeck's own counts."""
    await setup(hass, fake_api, config_entry, device_mowing_hours=None, device_distance_m=None)
    mowing = state_of(hass, MOWING)
    assert (mowing.state, mowing.attributes["source"]) == ("0.14", "telemetry")
    assert state_of(hass, DISTANCE).attributes["source"] == "telemetry"
    await unload(hass, config_entry)


async def test_since_mowdeck_sensors_are_disabled_by_default(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """MowDeck's own counts are there for those who want them, disabled by default."""
    await setup(hass, fake_api, config_entry)
    for entity_id in (MOWING_MOWDECK, DISTANCE_MOWDECK):
        registered = entity_registry.async_get(entity_id)
        assert registered is not None, entity_id
        assert registered.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    await unload(hass, config_entry)
