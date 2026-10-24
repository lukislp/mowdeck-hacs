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
from homeassistant.helpers import entity_registry as er

from . import spot
from .const import DOMAIN, SPEED_DECIMALS
from .entity import MowDeckEntity, async_add_mower_entities
from .geo import from_wgs84
from .start_options import (
    CM_PER_M,
    DEFAULT_JOB_OPTIONS,
    PARAMETERS,
    describe,
    problem,
    start_parameters,
)

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
        self,
        area_ids: list[str] | None = None,
        areas: list[str] | None = None,
        pattern: str | dict[str, Any] | None = None,
        angle_deg: float | None = None,
        border_laps: int | None = None,
        spacing_cm: float | None = None,
        blade_height_mm: int | None = None,
        speed_mps: float | None = None,
        obstacle_detection: str | None = None,
        obstacle_laps: int | None = None,
        order: str | None = None,
        start_progress_pct: int | None = None,
        ignore_unsupported: bool = False,
    ) -> ServiceResponse:
        """``mowdeck.start_mow``: start the given areas (default: the selected areas).

        Every parameter is checked against this mower's capabilities first; one that does not
        fit fails the action for this mower before anything is sent, or is left out and
        reported under ``ignored`` with ``ignore_unsupported``. Then ``set_blade_height`` and
        ``set_speed`` are sent (when given), then ``start_mow``. Pattern parameters left out
        come from the mower's saved mowing defaults (MowDeck's Fleet start does not fill them
        in); an option the defaults hold that the mower no longer declares is left out."""
        specs = start_parameters(self.capabilities)
        ignored: list[dict[str, Any]] = []
        given = {
            "pattern": pattern if isinstance(pattern, str) else None,
            "angle_deg": angle_deg,
            "border_laps": border_laps,
            "spacing_cm": spacing_cm,
            "blade_height_mm": blade_height_mm,
            "speed_mps": None if speed_mps is None else round(float(speed_mps), SPEED_DECIMALS),
            "obstacle_detection": obstacle_detection,
            "obstacle_laps": obstacle_laps,
            "order": order,
            # A start progress of 0 is the beginning: it always fits and is not sent.
            "start_progress_pct": start_progress_pct or None,
        }
        accepted: dict[str, Any] = {}
        for name, value in given.items():
            if value is None:
                continue
            spec = specs.get(name)
            reason = problem(spec, value)
            if reason is None:
                accepted[name] = value
            elif ignore_unsupported:
                ignored.append(_ignored(name, value, reason, spec, "action"))
            else:
                raise self._start_error(reason, name, value, spec)
        target = self._start_areas(
            list(area_ids or []), list(areas or []), ignore_unsupported, ignored
        )
        legacy = dict(pattern) if isinstance(pattern, dict) else None
        sent_pattern = self._start_pattern(legacy, accepted, specs, ignored)

        commands: list[dict[str, Any]] = []
        if "blade_height_mm" in accepted:
            commands.append({"kind": "set_blade_height", "mm": int(accepted["blade_height_mm"])})
        if "speed_mps" in accepted:
            commands.append({"kind": "set_speed", "mps": accepted["speed_mps"]})
        start: dict[str, Any] = {"kind": "start_mow", "area_ids": target}
        if sent_pattern is not None:
            start["pattern"] = sent_pattern
        commands.append(start)
        issued = [
            {
                "kind": command["kind"],
                "command_id": (await self.async_send_command(command)).get("command_id"),
            }
            for command in commands
        ]
        return {
            "mower_id": self.mower_id,
            "command_id": issued[-1]["command_id"],
            "area_ids": target,
            "pattern": sent_pattern,
            "commands": issued,
            "ignored": ignored,
        }

    def _mower_name(self) -> str:
        """The mower's name for messages."""
        return str(
            (self.coordinator.data.mowers.get(self.mower_id) or {}).get("name") or self.mower_id
        )

    def _start_error(
        self, reason: str, name: str, value: Any, spec: dict[str, Any] | None
    ) -> ServiceValidationError:
        """A translated error naming the mower, the parameter and what the mower allows."""
        if reason == "unsupported":
            return _invalid("start_parameter_unsupported", mower=self._mower_name(), parameter=name)
        return _invalid(
            "start_parameter_invalid",
            mower=self._mower_name(),
            parameter=name,
            value=str(value),
            allowed=describe(spec),
        )

    def _start_areas(
        self,
        area_ids: list[str],
        areas: list[str],
        ignore_unsupported: bool,
        ignored: list[dict[str, Any]],
    ) -> list[str]:
        """The area ids to mow: ``area_ids`` as given, then ``areas`` (ids, names or area
        switches) resolved on this mower's map; area switches of other mowers are skipped.
        Neither given: the selected areas."""
        if not area_ids and not areas:
            selected = self.coordinator.area_ids_to_mow(self.mower_id)
            if not selected:
                raise _invalid("no_area_selected")
            return selected
        labels = self.coordinator.area_labels(self.mower_id)
        chosen = list(area_ids)
        for item in areas:
            found = self._resolve_area(str(item), labels)
            if found is None:
                if not ignore_unsupported:
                    raise _invalid(
                        "start_area_unknown",
                        mower=self._mower_name(),
                        area=str(item),
                        allowed=", ".join(label for _, label in labels) or "-",
                    )
                ignored.append(
                    {"parameter": "areas", "value": item, "reason": "invalid", "source": "action"}
                )
            elif found:
                chosen.append(found)
        target = list(dict.fromkeys(chosen))
        if not target:
            raise _invalid("start_no_area", mower=self._mower_name())
        return target

    def _resolve_area(self, item: str, labels: list[tuple[str, str]]) -> str | None:
        """The area id of an area id, area name or area switch on this mower; ``""`` for an
        area switch of another MowDeck mower, None when nothing matches."""
        if item.startswith("switch."):
            entry = er.async_get(self.hass).async_get(item)
            if entry is None or entry.platform != DOMAIN or "_area_" not in entry.unique_id:
                return None
            mower_id, _, area_id = entry.unique_id.partition("_area_")
            if mower_id != self.mower_id:
                return ""
            return area_id if any(area_id == known for known, _ in labels) else None
        for area_id, _ in labels:
            if item == area_id:
                return area_id
        wanted = item.casefold()
        for area_id, label in labels:
            if label.casefold() == wanted:
                return area_id
        return None

    def _start_pattern(
        self,
        legacy: dict[str, Any] | None,
        accepted: dict[str, Any],
        specs: dict[str, dict[str, Any]],
        ignored: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """``StartMow.pattern``: the given pattern object (the older form of the action) or the
        saved mowing defaults (MowDeck's when none are saved), with the given parameters on
        top. None (no pattern: the mower's own) when neither defaults nor parameters exist."""
        fields: dict[str, Any] = {}
        for name in PATTERN_PARAMETERS:
            if name in accepted:
                key = PATTERN_KEYS.get(name, name)
                value = accepted[name]
                fields[key] = round(value / CM_PER_M, 3) if name == "spacing_cm" else value
        if legacy is not None:
            return {**legacy, **fields}
        if not fields and not self.coordinator.has_mow_defaults(self.mower_id):
            return None
        base = {
            key: value
            for key, value in self.coordinator.mow_pattern(self.mower_id).items()
            if value is not None and key != "start_progress_pct"
        }
        for key in DEFAULT_JOB_OPTIONS:
            if key in base and key not in fields and (reason := problem(specs.get(key), base[key])):
                ignored.append(_ignored(key, base.pop(key), reason, specs.get(key), "defaults"))
        return {**base, **fields}

    async def async_get_start_options(self) -> ServiceResponse:
        """``mowdeck.get_start_options``: what ``mowdeck.start_mow`` accepts on this mower."""
        params: dict[str, Any] = start_parameters(self.capabilities)
        state = self.mower_state
        answer: dict[str, Any] = {
            "mower_id": self.mower_id,
            "name": self._mower_name(),
            "model": self.coordinator.model(self.mower_id),
            "parameters": params,
            "unsupported": [name for name in PARAMETERS if name not in params],
            "areas": [
                {"id": area_id, "name": label}
                for area_id, label in self.coordinator.area_labels(self.mower_id)
            ],
            "defaults": {
                "saved": self.coordinator.has_mow_defaults(self.mower_id),
                "pattern": self.coordinator.mow_pattern(self.mower_id),
            },
            "current": {
                "blade_height_mm": state.get("blade_height_mm"),
                "speed_mps": state.get("speed_mps"),
            },
        }
        return answer

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
        polygon = spot.circle_polygon(from_wgs84(reference, *center), radius_m)
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


# The start parameters that go into StartMow.pattern, and their MowPattern key where it differs.
PATTERN_PARAMETERS = (
    "pattern",
    "angle_deg",
    "border_laps",
    "spacing_cm",
    "obstacle_detection",
    "obstacle_laps",
    "order",
    "start_progress_pct",
)
PATTERN_KEYS = {"pattern": "name", "spacing_cm": "spacing_m"}


def _ignored(
    name: str, value: Any, reason: str, spec: dict[str, Any] | None, source: str
) -> dict[str, Any]:
    """An entry of ``ignored`` in the answer of ``mowdeck.start_mow``."""
    return {"parameter": name, "value": value, "reason": reason, "allowed": spec, "source": source}


def _invalid(key: str, **placeholders: str) -> ServiceValidationError:
    """A translated validation error of an action."""
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )
