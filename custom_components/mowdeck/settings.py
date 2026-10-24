"""The mower's own settings, generated from what its connection declares (ADR 0046 in
lukislp/mowdeck).

``MowerCapabilities.settings`` lists each setting with its type (``bool``, ``int``, ``enum``,
``time_window``) and range or options; ``MowerState.settings`` carries the current values (only
the keys the mower has reported); ``set_setting`` changes one (``can_manage_mowers``, the
token permission ``fleet.manage``). The platforms build one entity per declared setting:

* ``bool``: a switch;
* ``int``: a number with the declared range, step and unit;
* ``enum``: a select with the declared options;
* ``time_window``: two time entities (start and end) and a button that switches the window
  off (``null``).

They are configuration entities, only created for tokens that may manage the mower, and they
follow the declaration: a setting that disappears (other firmware, other connection) takes its
entities with it. Keys MowDeck knows have translated names (``KNOWN_SETTINGS``); any other key
is shown as "Setting <key>".
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.const import EntityCategory

from .commands import async_send_confirmed
from .const import CAN_MANAGE_MOWERS
from .entity import MowDeckEntity

if TYPE_CHECKING:
    from homeassistant.helpers.entity import Entity

    from .coordinator import MowDeckCoordinator

# The setting keys MowDeck knows (SETTING_KEYS of mowdeck_contracts.v1.settings) with the type
# they have translated names for; a key declared with another type is shown like an unknown one.
KNOWN_SETTINGS: dict[str, str] = {
    "rain_detection": "bool",
    "turn_style": "enum",
    "traversal_mode": "enum",
    "side_led": "bool",
    "night_light": "bool",
    "wildlife_safety": "enum",
    "voice_enabled": "bool",
    "voice_volume": "int",
    "voice_gender": "enum",
    "non_work_hours": "time_window",
    "recharge_level": "int",
    "smart_recharge_level": "bool",
    "resume_level": "int",
    "smart_resume_level": "bool",
}
# A new time window when only one bound is set while the window is off.
DEFAULT_WINDOW_LENGTH = timedelta(hours=1)
# set_setting needs can_manage_mowers on the mower; a 403 names this token permission.
SETTING_PERMISSION = "fleet.manage"


def declared_settings(
    coordinator: MowDeckCoordinator, mower_id: str, setting_type: str
) -> list[dict[str, Any]]:
    """The mower's declared settings of one type, in declaration order; none for tokens that
    may not manage the mower."""
    if not coordinator.permitted(mower_id, CAN_MANAGE_MOWERS):
        return []
    caps = coordinator.data.capabilities.get(mower_id) or {}
    specs = caps.get("settings")
    if not isinstance(specs, list):
        return []
    return [
        spec
        for spec in specs
        if isinstance(spec, dict)
        and isinstance(spec.get("key"), str)
        and spec.get("type") == setting_type
    ]


def setting_entities(
    coordinator: MowDeckCoordinator,
    mower_id: str,
    setting_type: str,
    factory: Callable[[MowDeckCoordinator, str, str], Sequence[Entity]],
) -> list[Entity]:
    """The entities ``factory(coordinator, mower_id, key)`` builds for every declared setting
    of ``setting_type``."""
    return [
        entity
        for spec in declared_settings(coordinator, mower_id, setting_type)
        for entity in factory(coordinator, mower_id, str(spec["key"]))
    ]


class SettingEntity(MowDeckEntity):
    """An entity of one declared setting; ``part`` tells the entities of one setting apart
    (``start``, ``end`` and ``clear`` of a time window)."""

    _attr_entity_category = EntityCategory.CONFIG
    # The SettingType the platform's entities stand for.
    setting_type: str

    def __init__(
        self, coordinator: MowDeckCoordinator, mower_id: str, key: str, part: str = ""
    ) -> None:
        """Bind the entity to the setting ``key``."""
        suffix = f"_{part}" if part else ""
        known = KNOWN_SETTINGS.get(key) == self.setting_type
        super().__init__(coordinator, mower_id, f"setting_{key if known else 'other'}{suffix}")
        self.key = key
        self._attr_unique_id = f"{mower_id}_setting_{key}{suffix}"
        if not known:
            self._attr_translation_placeholders = {"setting": key}

    @property
    def spec(self) -> dict[str, Any]:
        """The setting's current declaration (empty once the mower no longer declares it)."""
        for spec in self.capabilities.get("settings") or []:
            if isinstance(spec, dict) and spec.get("key") == self.key:
                return spec
        return {}

    @property
    def setting_value(self) -> Any:
        """The value the mower last reported (None while it has not)."""
        values = self.mower_state.get("settings")
        return values.get(self.key) if isinstance(values, dict) else None

    @property
    def available(self) -> bool:
        """Available while the mower declares the setting."""
        return super().available and bool(self.spec)

    async def async_set_setting(self, value: Any) -> None:
        """Send ``set_setting``; the new value shows once the mower reports it."""
        await async_send_confirmed(
            self,
            {"kind": "set_setting", "key": self.key, "value": value},
            SETTING_PERMISSION,
        )


def parse_clock(value: Any) -> time | None:
    """An ``HH:MM`` value as a time of day (None for anything else)."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        return None


def format_clock(value: time) -> str:
    """A time of day as ``HH:MM`` (seconds dropped)."""
    return f"{value.hour:02d}:{value.minute:02d}"


def shift_clock(value: time, delta: timedelta) -> time:
    """A time of day moved by ``delta``, wrapping around midnight."""
    return (datetime.combine(datetime(2000, 1, 1), value) + delta).time()
