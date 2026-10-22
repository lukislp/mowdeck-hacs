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

SERVICE_START_MOW = "start_mow"
SERVICE_EMERGENCY_STOP = "emergency_stop"
SERVICE_SET_BLADE_HEIGHT = "set_blade_height"
SERVICE_SET_SPEED = "set_speed"
SERVICE_GET_MAP = "get_map"
SERVICE_GET_HEATMAP = "get_heatmap"


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
