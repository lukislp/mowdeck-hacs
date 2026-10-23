"""Lawn mower entity: activity, start, pause, dock and stop, plus the MowDeck entity services."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)
from homeassistant.core import HomeAssistant, ServiceResponse
from homeassistant.exceptions import ServiceValidationError

from . import spot
from .const import DOMAIN, SPEED_DECIMALS
from .entity import MowDeckEntity, async_add_mower_entities

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator

PARALLEL_UPDATES = 0

# MowerActivity (Telemetry) to Home Assistant; offline and unknown have no activity.
ACTIVITY_MAP: dict[str, LawnMowerActivity] = {
    "idle": LawnMowerActivity.IDLE,
    "mowing": LawnMowerActivity.MOWING,
    "paused": LawnMowerActivity.PAUSED,
    "returning": LawnMowerActivity.RETURNING,
    "docked": LawnMowerActivity.DOCKED,
    "charging": LawnMowerActivity.DOCKED,
    "error": LawnMowerActivity.ERROR,
    # A firmware update installs only on a docked mower.
    "updating": LawnMowerActivity.DOCKED,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MowDeckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One lawn mower entity per mower."""
    async_add_mower_entities(
        entry, async_add_entities, lambda coordinator, m: [MowDeckLawnMower(coordinator, m)]
    )


class MowDeckLawnMower(MowDeckEntity, LawnMowerEntity):
    """A MowDeck mower."""

    _attr_name = None
    _attr_supported_features = (
        LawnMowerEntityFeature.START_MOWING
        | LawnMowerEntityFeature.PAUSE
        | LawnMowerEntityFeature.DOCK
        | LawnMowerEntityFeature.STOP
    )

    def __init__(self, coordinator: MowDeckCoordinator, mower_id: str) -> None:
        """Create the entity; it is the device's main entity (no name of its own)."""
        super().__init__(coordinator, mower_id, "mower")
        self._attr_translation_key = None

    @property
    def activity(self) -> LawnMowerActivity | None:
        """The mapped activity of the newest state."""
        return ACTIVITY_MAP.get(str(self.mower_state.get("activity")))

    async def async_start_mowing(self) -> None:
        """Resume a paused job, otherwise start the selected area (or every area)."""
        if self.mower_state.get("activity") == "paused":
            await self.async_send_command({"kind": "resume_mow"})
            return
        await self.async_start_mow()

    async def async_pause(self) -> None:
        """Pause the running job."""
        await self.async_send_command({"kind": "pause_mow"})

    async def async_dock(self) -> None:
        """Return to the dock (a running job is paused and can be resumed)."""
        await self.async_send_command({"kind": "return_to_dock"})

    async def async_stop(self) -> None:
        """End the job and return to the dock (``return_to_dock`` with ``end_job``)."""
        await self.async_send_command({"kind": "return_to_dock", "end_job": True})

    # Entity services (registered in services.py)

    async def async_start_mow(
        self, area_ids: list[str] | None = None, pattern: dict[str, Any] | None = None
    ) -> None:
        """``mowdeck.start_mow``: start the given areas (default: the selected areas) with the
        given pattern (default: the mower's mowing defaults, when saved)."""
        areas = list(area_ids) if area_ids else self.coordinator.area_ids_to_mow(self.mower_id)
        if not areas:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_area_selected"
            )
        command: dict[str, Any] = {"kind": "start_mow", "area_ids": areas}
        if pattern:
            command["pattern"] = dict(pattern)
        elif self.coordinator.has_mow_defaults(self.mower_id):
            command["pattern"] = self.coordinator.mow_pattern(self.mower_id)
        await self.async_send_command(command)

    async def async_get_map(self) -> ServiceResponse:
        """``mowdeck.get_map``: the mower's current map with MowDeck's area names."""
        answer = await self.async_call_api(
            self.coordinator.client.get_map(self.mower_id), "get_map", "mower.view"
        )
        if answer is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_map")
        mower_map = dict(answer.get("map") or {})
        names = dict(self.coordinator.area_labels(self.mower_id))
        areas = [
            {**area, "display_name": names.get(str(area.get("id")))}
            for area in mower_map.get("areas") or []
            if isinstance(area, dict)
        ]
        return {
            "mower_id": self.mower_id,
            "revision": answer.get("revision"),
            "observed_at": answer.get("observed_at"),
            "map": {**mower_map, "areas": areas},
        }

    async def async_get_heatmap(
        self,
        layer: str,
        start: datetime | None = None,
        end: datetime | None = None,
        cell: float | None = None,
    ) -> ServiceResponse:
        """``mowdeck.get_heatmap``: a heatmap layer of the mower (default: the last 7 days)."""
        return await self.async_call_api(
            self.coordinator.client.get_heatmap(
                self.mower_id,
                layer,
                start=start.isoformat() if start else None,
                end=end.isoformat() if end else None,
                cell=cell,
            ),
            "get_heatmap",
            "mower.view",
        )

    async def async_emergency_stop(self) -> None:
        """``mowdeck.emergency_stop``: stop at once (allowed for viewers too)."""
        await self.async_send_command({"kind": "emergency_stop"})

    async def async_set_blade_height(self, mm: int) -> None:
        """``mowdeck.set_blade_height``."""
        await self.async_send_command({"kind": "set_blade_height", "mm": int(mm)})

    async def async_set_speed(self, mps: float) -> None:
        """``mowdeck.set_speed`` (two decimals, like the number entity)."""
        await self.async_send_command(
            {"kind": "set_speed", "mps": round(float(mps), SPEED_DECIMALS)}
        )

    async def async_spot_mow(
        self,
        latitude: float | None = None,
        longitude: float | None = None,
        zone: str | None = None,
        radius: float | None = None,
        pattern: str = "spiral",
        angle: float = 0.0,
        speed: float = spot.SPEED_DEFAULT_MPS,
        blades: bool = False,
        confirm_blades: bool = False,
        return_to_dock: bool = True,
        turn_style: str | None = None,
    ) -> ServiceResponse:
        """``mowdeck.spot_mow``: mow a circle around a point or a zone's centre, as the web
        app's spot sheet does: plan it with the Planner, then send ``spot_mow`` with the plan.

        MowDeck drives the run itself (no job of the mower); the blades run only when
        ``blades`` and ``confirm_blades`` are both set and the mower allows blades while
        driving, otherwise it is a dry run. Progress shows in the spot run sensor."""
        center, radius_m = self._spot_center(latitude, longitude, zone, radius)
        caps = self.capabilities
        if not caps.get("manual_drive"):
            raise _invalid("spot_unsupported")
        if blades and not confirm_blades:
            raise _invalid("spot_blades_unconfirmed")
        if blades and not caps.get("manual_drive_blades"):
            raise _invalid("spot_blades_unsupported")
        mower_map = self.coordinator.data.maps.get(self.mower_id) or {}
        reference = mower_map.get("reference")
        if not isinstance(reference, dict):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_map")
        polygon = spot.circle_polygon(spot.from_wgs84(reference, *center), radius_m)
        area_id = spot.containing_area(polygon, self.coordinator.areas(self.mower_id))
        if area_id is None:
            raise _invalid("spot_outside_areas")
        state = self.mower_state
        if (reason := spot.blocking_condition(state, caps)) is not None:
            raise _invalid(reason)
        style = spot.resolve_turn_style(
            turn_style, state.get("turn_style"), bool(caps.get("drive_reverse"))
        )
        request = spot.plan_request(
            mower_id=self.mower_id,
            area_id=area_id,
            polygon=polygon,
            pattern=pattern,
            angle_deg=float(angle),
            speed_mps=round(float(speed), 2),
            caps=caps,
            turn_style=style,
            transit=spot.transit_from(state, caps, reference),
        )
        plan = await self.async_call_api(
            self.coordinator.client.create_plan(request), "spot_mow", "mower.control"
        )
        transit = plan.get("transit")
        if isinstance(transit, dict) and not transit.get("found"):
            raise _invalid("spot_no_route", reason=str(transit.get("reason") or ""))
        run_blades = bool(blades and confirm_blades)
        accepted = await self.async_send_command(
            {
                "kind": "spot_mow",
                "plan": plan["spot_plan"],
                "blades": run_blades,
                "confirm_blades": run_blades,
                "speed_mps": round(float(speed), 2),
                "spot": {"center_wgs84": [center[0], center[1]], "radius_m": radius_m},
                "turn_style": turn_style,
                "return_to_dock": return_to_dock,
            }
        )
        return {
            "command_id": accepted.get("command_id"),
            "area_id": area_id,
            "blades": run_blades,
            "turn_style": style,
            "estimated_duration_s": plan.get("estimated_duration_s"),
            "covered_area_m2": plan.get("covered_area_m2"),
            "plan_hash": plan.get("plan_hash"),
        }

    def _spot_center(
        self,
        latitude: float | None,
        longitude: float | None,
        zone: str | None,
        radius: float | None,
    ) -> tuple[tuple[float, float], float]:
        """The spot's centre and radius: a point (radius default 1.5 m) or a zone (its centre,
        and its radius unless one is given; it must lie within 0.5 to 3 m)."""
        if zone is not None and (latitude is not None or longitude is not None):
            raise _invalid("spot_location")
        if zone is not None:
            found = self.hass.states.get(zone)
            if found is None or "latitude" not in found.attributes:
                raise _invalid("spot_zone_unknown", zone=zone)
            center = (float(found.attributes["latitude"]), float(found.attributes["longitude"]))
            if radius is None:
                radius = float(found.attributes.get("radius") or 0)
        elif latitude is not None and longitude is not None:
            center = (float(latitude), float(longitude))
        else:
            raise _invalid("spot_location")
        radius_m = spot.RADIUS_DEFAULT_M if radius is None else round(float(radius), 1)
        if not spot.RADIUS_MIN_M <= radius_m <= spot.RADIUS_MAX_M:
            raise _invalid("spot_radius", radius=f"{radius_m:g}")
        return center, radius_m


def _invalid(key: str, **placeholders: str) -> ServiceValidationError:
    """A translated validation error of ``mowdeck.spot_mow``."""
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )
