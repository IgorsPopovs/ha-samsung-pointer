"""Allowlist diagnostics: never expose host, identity, name or pairing token."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_MODEL


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    return {
        "model": entry.data.get(CONF_MODEL),
        "connected": bool(entry.runtime_data.connected),
        "version": "0.1.0",
    }
