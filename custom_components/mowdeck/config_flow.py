"""Config flow of the MowDeck integration: base URL and API token, reauth, reconfigure,
options."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import probatio as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    MowDeckAuthError,
    MowDeckClient,
    MowDeckConnectionError,
    MowDeckError,
    MowDeckForbiddenError,
    normalize_base_url,
)
from .const import (
    CONF_API_TOKEN,
    CONF_EXPOSE_EMERGENCY_STOP,
    CONF_PERSISTENT_SEVERITY,
    CONF_POLL_INTERVAL,
    DEFAULT_EXPOSE_EMERGENCY_STOP,
    DEFAULT_PERSISTENT_SEVERITY,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
    PERSISTENT_OFF,
    SEVERITIES,
)

_LOGGER = logging.getLogger(__name__)

TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
        vol.Required(CONF_API_TOKEN): TOKEN_SELECTOR,
    }
)
REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_API_TOKEN): TOKEN_SELECTOR})
RECONFIGURE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
        vol.Optional(CONF_API_TOKEN): TOKEN_SELECTOR,
    }
)


class InvalidUrlError(Exception):
    """The URL is not an http(s) URL."""


class NoAccountError(Exception):
    """The token belongs to no account (operators and services cannot use the integration)."""


async def validate_input(hass: HomeAssistant, url: str, token: str) -> dict[str, Any]:
    """Exchange the token, read ``/me`` and list the mowers; return what the entry needs.

    Raises InvalidUrlError, NoAccountError, MowDeckAuthError, MowDeckForbiddenError or MowDeckError.
    """
    try:
        base_url = normalize_base_url(url)
    except ValueError as err:
        raise InvalidUrlError from err
    client = MowDeckClient(async_get_clientsession(hass), base_url, token, retries=1)
    me = await client.get_me()
    mowers = await client.list_mowers()
    account_id = me.get("account_id")
    if not account_id:
        raise NoAccountError
    return {
        "url": base_url,
        "account_id": str(account_id),
        "title": str(me.get("display_name") or me.get("username") or "MowDeck"),
        "mowers": len(mowers),
    }


class MowDeckConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up MowDeck with a base URL and an API token."""

    VERSION = 1

    async def _async_validate(
        self, url: str, token: str, errors: dict[str, str]
    ) -> dict[str, Any] | None:
        try:
            return await validate_input(self.hass, url, token)
        except InvalidUrlError:
            errors[CONF_URL] = "invalid_url"
        except NoAccountError:
            errors["base"] = "no_account"
        except MowDeckAuthError:
            errors["base"] = "invalid_auth"
        except MowDeckForbiddenError:
            errors["base"] = "missing_permission"
        except MowDeckConnectionError:
            errors["base"] = "cannot_connect"
        except MowDeckError:
            _LOGGER.exception("Unexpected MowDeck API answer")
            errors["base"] = "unknown"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the URL of the MowDeck installation and an API token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            info = await self._async_validate(
                user_input[CONF_URL], user_input[CONF_API_TOKEN], errors
            )
            if info is not None:
                await self.async_set_unique_id(info["account_id"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=info["title"],
                    data={CONF_URL: info["url"], CONF_API_TOKEN: user_input[CONF_API_TOKEN]},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
            description_placeholders={"example_url": "https://mowdeck.example.ts.net"},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The token was revoked or expired: ask for a new one."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate the new token against the same account and reload the entry."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            info = await self._async_validate(
                entry.data[CONF_URL], user_input[CONF_API_TOKEN], errors
            )
            if info is not None:
                await self.async_set_unique_id(info["account_id"])
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_TOKEN: user_input[CONF_API_TOKEN]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"url": entry.data[CONF_URL]},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the URL (MowDeck moved) and optionally the token; same account only."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            token = user_input.get(CONF_API_TOKEN) or entry.data[CONF_API_TOKEN]
            info = await self._async_validate(user_input[CONF_URL], token, errors)
            if info is not None:
                await self.async_set_unique_id(info["account_id"])
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_URL: info["url"], CONF_API_TOKEN: token}
                )
        suggested = {CONF_URL: entry.data[CONF_URL]}
        if user_input is not None:
            suggested[CONF_URL] = user_input[CONF_URL]
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(RECONFIGURE_SCHEMA, suggested),
            errors=errors,
            description_placeholders={"example_url": "https://mowdeck.example.ts.net"},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MowDeckOptionsFlow:
        """The options flow."""
        return MowDeckOptionsFlow()


class MowDeckOptionsFlow(OptionsFlowWithReload):
    """Poll interval, the emergency stop button and persistent notifications."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show and store the options (the entry reloads afterwards)."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_POLL_INTERVAL: int(user_input[CONF_POLL_INTERVAL]),
                    CONF_EXPOSE_EMERGENCY_STOP: bool(user_input[CONF_EXPOSE_EMERGENCY_STOP]),
                    CONF_PERSISTENT_SEVERITY: str(
                        user_input.get(CONF_PERSISTENT_SEVERITY, DEFAULT_PERSISTENT_SEVERITY)
                    ),
                }
            )
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_POLL_INTERVAL,
                    default=options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_POLL_INTERVAL,
                        max=MAX_POLL_INTERVAL,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_EXPOSE_EMERGENCY_STOP,
                    default=options.get(CONF_EXPOSE_EMERGENCY_STOP, DEFAULT_EXPOSE_EMERGENCY_STOP),
                ): bool,
                vol.Required(
                    CONF_PERSISTENT_SEVERITY,
                    default=options.get(CONF_PERSISTENT_SEVERITY, DEFAULT_PERSISTENT_SEVERITY),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[PERSISTENT_OFF, *reversed(SEVERITIES)],
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key=CONF_PERSISTENT_SEVERITY,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
