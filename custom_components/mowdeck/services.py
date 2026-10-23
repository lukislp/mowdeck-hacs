"""Entity services of the MowDeck integration (targets are MowDeck lawn mower entities)."""

from __future__ import annotations

import probatio as vol
from homeassistant.components.lawn_mower import DOMAIN as LAWN_MOWER_DOMAIN
from homeassistant.core import HomeAssistant, SupportsResponse, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service

from .const import (
    API_BLADE_HEIGHT_MAX_MM,
    API_BLADE_HEIGHT_MIN_MM,
    API_SPEED_MAX_MPS,
    DOMAIN,
    HEATMAP_CELLS,
    HEATMAP_LAYERS,
)
from .spot import PATTERNS as SPOT_PATTERNS
from .spot import (
    RADIUS_MAX_M,
    RADIUS_MIN_M,
    SPEED_DEFAULT_MPS,
    SPEED_MAX_MPS,
    SPEED_MIN_MPS,
    TURN_STYLES,
)

SERVICE_START_MOW = "start_mow"
SERVICE_EMERGENCY_STOP = "emergency_stop"
SERVICE_SET_BLADE_HEIGHT = "set_blade_height"
SERVICE_SET_SPEED = "set_speed"
SERVICE_GET_MAP = "get_map"
SERVICE_GET_HEATMAP = "get_heatmap"
SERVICE_SPOT_MOW = "spot_mow"


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the entity services on the lawn mower platform of this integration."""
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_START_MOW,
        entity_domain=LAWN_MOWER_DOMAIN,
        schema={
            vol.Optional("area_ids"): vol.All(cv.ensure_list, [cv.string]),
            vol.Optional("pattern"): dict,
        },
        func="async_start_mow",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_EMERGENCY_STOP,
        entity_domain=LAWN_MOWER_DOMAIN,
        schema=None,
        func="async_emergency_stop",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_SET_BLADE_HEIGHT,
        entity_domain=LAWN_MOWER_DOMAIN,
        schema={
            vol.Required("mm"): vol.All(
                vol.Coerce(int), vol.Range(API_BLADE_HEIGHT_MIN_MM, API_BLADE_HEIGHT_MAX_MM)
            )
        },
        func="async_set_blade_height",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_SET_SPEED,
        entity_domain=LAWN_MOWER_DOMAIN,
        schema={
            vol.Required("mps"): vol.All(
                vol.Coerce(float),
                vol.Range(min=0, max=API_SPEED_MAX_MPS, min_included=False),
            )
        },
        func="async_set_speed",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_GET_MAP,
        entity_domain=LAWN_MOWER_DOMAIN,
        schema=None,
        func="async_get_map",
        supports_response=SupportsResponse.ONLY,
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_GET_HEATMAP,
        entity_domain=LAWN_MOWER_DOMAIN,
        schema={
            vol.Required("layer"): vol.In(HEATMAP_LAYERS),
            vol.Optional("start"): cv.datetime,
            vol.Optional("end"): cv.datetime,
            vol.Optional("cell"): vol.All(vol.Coerce(float), vol.In(HEATMAP_CELLS)),
        },
        func="async_get_heatmap",
        supports_response=SupportsResponse.ONLY,
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_SPOT_MOW,
        entity_domain=LAWN_MOWER_DOMAIN,
        schema={
            vol.Optional("latitude"): cv.latitude,
            vol.Optional("longitude"): cv.longitude,
            vol.Optional("zone"): cv.entity_domain("zone"),
            vol.Optional("radius"): vol.All(
                vol.Coerce(float), vol.Range(min=RADIUS_MIN_M, max=RADIUS_MAX_M)
            ),
            vol.Optional("pattern", default="spiral"): vol.In(SPOT_PATTERNS),
            vol.Optional("angle", default=0): vol.All(vol.Coerce(float), vol.Range(0, 180)),
            vol.Optional("speed", default=SPEED_DEFAULT_MPS): vol.All(
                vol.Coerce(float), vol.Range(min=SPEED_MIN_MPS, max=SPEED_MAX_MPS)
            ),
            vol.Optional("blades", default=False): cv.boolean,
            vol.Optional("confirm_blades", default=False): cv.boolean,
            vol.Optional("return_to_dock", default=True): cv.boolean,
            vol.Optional("turn_style"): vol.In(TURN_STYLES),
        },
        func="async_spot_mow",
        supports_response=SupportsResponse.OPTIONAL,
    )
