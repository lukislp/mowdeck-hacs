"""Time entities of a MowDeck mower: start and end of every daily time window the mower declares
as a setting (``settings.py``), for example its non-working hours.

The window is sent whole (``{"start": "HH:MM", "end": "HH:MM"}``, the mower's local time; an end
before the start crosses midnight). While the window is off (``null``), setting one bound
switches it on with the other bound from the last window seen, else one hour apart. The
setting's button switches the window off again.
"""

from __future__ import annotations

from datetime import time
from typing import TYPE_CHECKING, Any

from homeassistant.components.time import TimeEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError

from .const import DOMAIN
from .entity import async_add_mower_entities
from .settings import (
    DEFAULT_WINDOW_LENGTH,
    SettingEntity,
    format_clock,
    parse_clock,
    setting_entities,
    shift_clock,
)

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator

PARALLEL_UPDATES = 1

START = "start"
END = "end"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MowDeckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Start and end of every time window setting the mower declares."""
    async_add_mower_entities(
        entry,
        async_add_entities,
        lambda c, m: setting_entities(
            c,
            m,
            "time_window",
            lambda c, m, key: [SettingTime(c, m, key, START), SettingTime(c, m, key, END)],
        ),
        domain=Platform.TIME,
    )


def window_of(value: Any) -> tuple[time, time] | None:
    """A reported time window as (start, end); None while it is off or malformed."""
    if not isinstance(value, dict):
        return None
    start, end = parse_clock(value.get(START)), parse_clock(value.get(END))
    if start is None or end is None:
        return None
    return start, end


class SettingTime(SettingEntity, TimeEntity):
    """One bound (``start`` or ``end``) of a time window setting."""

    setting_type = "time_window"

    def __init__(
        self, coordinator: MowDeckCoordinator, mower_id: str, key: str, bound: str
    ) -> None:
        """Create the entity of one bound."""
        super().__init__(coordinator, mower_id, key, bound)
        self.bound = bound
        self._last_window = window_of(self.setting_value)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Remember the last window, for switching it on again."""
        self._last_window = window_of(self.setting_value) or self._last_window
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> time | None:
        """The bound of the reported window; unknown while the window is off."""
        window = window_of(self.setting_value)
        if window is None:
            return None
        return window[0] if self.bound == START else window[1]

    async def async_set_value(self, value: time) -> None:
        """Send the window with this bound changed."""
        window = window_of(self.setting_value) or self._last_window
        if window is not None:
            start, end = window
        elif self.bound == START:
            start, end = value, shift_clock(value, DEFAULT_WINDOW_LENGTH)
        else:
            start, end = shift_clock(value, -DEFAULT_WINDOW_LENGTH), value
        if self.bound == START:
            start = value
        else:
            end = value
        if format_clock(start) == format_clock(end):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="time_window_empty",
                translation_placeholders={"time": format_clock(start)},
            )
        await self.async_set_setting({START: format_clock(start), END: format_clock(end)})
