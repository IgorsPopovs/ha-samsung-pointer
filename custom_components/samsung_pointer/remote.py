"""Pointer remote and targeted movement/click actions."""

from collections.abc import Iterable
from typing import Any

import voluptuous as vol
from homeassistant.components.remote import RemoteEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import PointerAuthError, PointerError, SamsungPointerClient
from .const import CONF_MODEL, CONF_TV_ID, DOMAIN

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([SamsungPointerRemote(entry)])
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "move",
        {
            vol.Required("x"): vol.All(vol.Coerce(float), vol.Range(min=-2000, max=2000)),
            vol.Required("y"): vol.All(vol.Coerce(float), vol.Range(min=-2000, max=2000)),
        },
        "async_move",
    )
    platform.async_register_entity_service(
        "click",
        {
            vol.Optional("button", default="left"): vol.In(["left", "right"]),
        },
        "async_click",
    )


class SamsungPointerRemote(RemoteEntity):
    """State means pointer socket connected, not TV power."""

    _attr_has_entity_name = True
    _attr_name = "Pointer"
    _attr_icon = "mdi:cursor-default"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry
        self.client: SamsungPointerClient = entry.runtime_data
        self._attr_unique_id = f"{entry.data[CONF_TV_ID]}_pointer"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_TV_ID])},
            name=entry.title,
            manufacturer="Samsung Electronics",
            model=entry.data.get(CONF_MODEL, "Samsung TV"),
        )

    @property
    def is_on(self) -> bool:
        return self.client.connected

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.client.on_state = self._connection_changed

    async def async_will_remove_from_hass(self) -> None:
        self.client.on_state = None
        await super().async_will_remove_from_hass()

    @callback
    def _connection_changed(self) -> None:
        self.async_write_ha_state()

    async def _run(self, action, *args) -> None:
        try:
            await action(*args)
        except PointerAuthError as error:
            self.entry.async_start_reauth(self.hass)
            raise HomeAssistantError(str(error)) from None
        except PointerError as error:
            raise HomeAssistantError(str(error)) from None
        except ValueError as error:
            raise ServiceValidationError(str(error)) from None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._run(self.client.connect)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._run(self.client.disconnect)

    async def async_move(self, x: float, y: float) -> None:
        await self._run(self.client.move, x, y)

    async def async_click(self, button: str = "left") -> None:
        await self._run(self.client.click, button)

    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        commands = list(command)
        if len(commands) != 1 or commands[0] not in ("left_click", "right_click"):
            raise ServiceValidationError("Send exactly one left_click or right_click command")
        if kwargs.get("num_repeats", 1) != 1 or kwargs.get("hold_secs", 0):
            raise ServiceValidationError("Repeated clicks and held buttons are not supported")
        await self.async_click(commands[0].removesuffix("_click"))
