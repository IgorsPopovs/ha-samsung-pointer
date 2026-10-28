"""UI setup, pairing, and reconfiguration for Samsung Pointer."""

import asyncio
import uuid
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_TOKEN
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import (
    PointerAuthError,
    PointerError,
    SamsungPointerClient,
    get_device_info,
    validate_host,
)
from .const import CLIENT_NAME, CONF_CLIENT_NAME, CONF_TV_ID, DOMAIN


class SamsungPointerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pair using a dedicated client identity, not another integration's token."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._task: asyncio.Task | None = None
        self._error: str | None = None

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            try:
                host = validate_host(user_input[CONF_HOST])
                info = await get_device_info(async_get_clientsession(self.hass), host)
            except ValueError:
                errors[CONF_HOST] = "invalid_host"
            except PointerError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(info[CONF_TV_ID])
                if self.source in ("reconfigure", "reauth"):
                    entry = (
                        self._get_reconfigure_entry()
                        if self.source == "reconfigure"
                        else self._get_reauth_entry()
                    )
                    if info[CONF_TV_ID] != entry.unique_id:
                        return self.async_abort(reason="wrong_tv")
                    self._data = dict(entry.data)
                else:
                    self._abort_if_unique_id_configured()
                    self._data[CONF_CLIENT_NAME] = f"{CLIENT_NAME} {uuid.uuid4().hex[:6]}"
                self._data.update(info)
                self._data.update(
                    {CONF_HOST: host, CONF_NAME: user_input[CONF_NAME].strip() or "Samsung TV"}
                )
                # Re-pairing is explicit; never borrow or edit SamsungTV Smart credentials.
                self._data.pop(CONF_TOKEN, None)
                return await self.async_step_pair()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=self._data.get(CONF_HOST, "")): str,
                    vol.Required(CONF_NAME, default=self._data.get(CONF_NAME, "Samsung TV")): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input=None) -> ConfigFlowResult:
        self._data = dict(self._get_reconfigure_entry().data)
        return await self.async_step_user(user_input)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self._data = dict(entry_data)
        return await self.async_step_user()

    async def async_step_pair(self, user_input=None) -> ConfigFlowResult:
        return (
            self.async_show_form(
                step_id="pair",
                description_placeholders={"client_name": self._data[CONF_CLIENT_NAME]},
            )
            if user_input is None
            else await self.async_step_pairing()
        )

    async def _pair(self) -> None:
        client = SamsungPointerClient(
            async_get_clientsession(self.hass),
            self._data[CONF_HOST],
            name=self._data[CONF_CLIENT_NAME],
        )
        try:
            await client.connect(timeout=60)
            if not client.token:
                self._error = "missing_token"
            else:
                self._data[CONF_TOKEN] = client.token
        except PointerAuthError:
            self._error = "not_authorized"
        except PointerError:
            self._error = "cannot_connect"
        finally:
            await client.close()

    async def async_step_pairing(self, user_input=None) -> ConfigFlowResult:
        if self._task is None:
            self._task = self.hass.async_create_task(self._pair())
        if not self._task.done():
            return self.async_show_progress(
                step_id="pairing",
                progress_action="pairing",
                progress_task=self._task,
                description_placeholders={"client_name": self._data[CONF_CLIENT_NAME]},
            )
        self._task.result()
        return self.async_show_progress_done(next_step_id="finish")

    async def async_step_finish(self, user_input=None) -> ConfigFlowResult:
        if self._error:
            return self.async_abort(reason=self._error)
        if self.source == "reconfigure":
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(), data_updates=self._data
            )
        if self.source == "reauth":
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data_updates=self._data
            )
        return self.async_create_entry(title=self._data[CONF_NAME], data=self._data)
