"""Samsung Pointer: local mouse control alongside existing TV integrations."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_TOKEN, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import SamsungPointerClient
from .const import CLIENT_NAME, CONF_CLIENT_NAME

PLATFORMS = [Platform.REMOTE]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Load without connecting, so a powered-off TV never blocks startup."""

    @callback
    def save_token(token: str) -> None:
        if token != entry.data.get(CONF_TOKEN):
            hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: token})

    entry.runtime_data = SamsungPointerClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        name=entry.data.get(CONF_CLIENT_NAME, CLIENT_NAME),
        token=entry.data.get(CONF_TOKEN),
        on_token=save_token,
    )

    async def stop_pointer(_event) -> None:
        await entry.runtime_data.close()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop_pointer))
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await entry.runtime_data.close()
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload only this integration's entities and connection."""
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.close()
        return True
    return False
