"""Load the component and its actions in a real Home Assistant 2026.10 instance."""

import asyncio
from pathlib import Path
from types import MappingProxyType
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant import bootstrap, config_entries, loader
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.samsung_pointer.client import PointerAuthError
from custom_components.samsung_pointer.const import DOMAIN
from custom_components.samsung_pointer.diagnostics import async_get_config_entry_diagnostics


@pytest.fixture
async def hass(tmp_path):
    (tmp_path / "custom_components").symlink_to(Path(__file__).parents[1] / "custom_components")
    hass = HomeAssistant(str(tmp_path))
    hass.config.skip_pip = True
    loader.async_setup(hass)
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    assert await bootstrap.async_load_base_functionality(hass)
    # Use a normal aiohttp session to avoid multicast discovery in unit tests.
    async with aiohttp.ClientSession() as session:
        with (
            patch(
                "custom_components.samsung_pointer.async_get_clientsession", return_value=session
            ),
            patch(
                "custom_components.samsung_pointer.config_flow.async_get_clientsession",
                return_value=session,
            ),
        ):
            yield hass
            await hass.async_stop(force=True)


def make_entry():
    return config_entries.ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Test TV",
        source="user",
        unique_id="test-tv",
        discovery_keys=MappingProxyType({}),
        options={},
        subentries_data=None,
        data={
            "host": "tv.example",
            "tv_id": "test-tv",
            "model": "Test Samsung",
            "client_name": "Test Pointer",
            "token": "test-only-token",
        },
    )


async def test_real_ha_setup_actions_and_unload(hass):
    entry = make_entry()
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()
    assert entry.state == config_entries.ConfigEntryState.LOADED
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert len(entities) == 1
    entity_id = entities[0].entity_id
    assert hass.states.get(entity_id).state == "off"
    assert hass.services.has_service(DOMAIN, "move")
    assert hass.services.has_service(DOMAIN, "click")
    with patch.object(entry.runtime_data, "move", new_callable=AsyncMock) as move:
        await hass.services.async_call(
            DOMAIN, "move", {"entity_id": entity_id, "x": "12.5", "y": "-6"}, blocking=True
        )
        move.assert_awaited_once_with(
            12.5,
            -6.0,
            speed_threshold=8.0,
            max_speed_gain=4.0,
        )
    with patch.object(entry.runtime_data, "click", new_callable=AsyncMock) as click:
        await hass.services.async_call(DOMAIN, "click", {"entity_id": entity_id}, blocking=True)
        click.assert_awaited_once_with("left")
    connection = entry.runtime_data
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert connection._closing


async def test_config_flow_full_pairing(hass):
    await async_setup_component(hass, "persistent_notification", {})
    with (
        patch(
            "custom_components.samsung_pointer.config_flow.get_device_info",
            new_callable=AsyncMock,
            return_value={"tv_id": "test-tv", "model": "Test Samsung"},
        ),
        patch("custom_components.samsung_pointer.config_flow.SamsungPointerClient") as client,
    ):
        paired = asyncio.Event()

        async def pair(**kwargs):
            await paired.wait()

        client.return_value.connect = AsyncMock(side_effect=pair)
        client.return_value.close = AsyncMock()
        client.return_value.token = "test-only-token"
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}, data={"host": "tv.example", "name": "Test TV"}
        )
        assert result["step_id"] == "pair"
        flow_id = result["flow_id"]
        result = await hass.config_entries.flow.async_configure(flow_id, {})
        assert result["type"] == "progress"
        paired.set()
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(flow_id)
        if result["type"] == "progress_done":
            result = await hass.config_entries.flow.async_configure(flow_id)
        assert result["type"] == "create_entry"
        assert result["data"]["token"] == "test-only-token"
        assert result["data"]["tv_id"] == "test-tv"
        client.return_value.close.assert_awaited_once()


async def test_config_flow_invalid_host(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data={"host": "http://tv.local", "name": "TV"}
    )
    assert result["type"] == "form"
    assert result["errors"] == {"host": "invalid_host"}


async def test_reconfigure_rejects_a_different_tv(hass):
    entry = make_entry()
    await hass.config_entries.async_add(entry)
    with patch(
        "custom_components.samsung_pointer.config_flow.get_device_info",
        new_callable=AsyncMock,
        return_value={"tv_id": "different-tv", "model": "Other TV"},
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reconfigure", "entry_id": entry.entry_id},
            data={"host": "other.example", "name": "Other TV"},
        )
    assert result["type"] == "abort"
    assert result["reason"] == "wrong_tv"
    assert entry.data["host"] == "tv.example"


async def test_pairing_rejected_creates_no_entry(hass):
    with (
        patch(
            "custom_components.samsung_pointer.config_flow.get_device_info",
            new_callable=AsyncMock,
            return_value={"tv_id": "test-tv", "model": "TV"},
        ),
        patch("custom_components.samsung_pointer.config_flow.SamsungPointerClient") as client,
    ):
        client.return_value.connect = AsyncMock(side_effect=PointerAuthError("Denied"))
        client.return_value.close = AsyncMock()
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}, data={"host": "tv.example", "name": "TV"}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["type"] == "abort"
        assert result["reason"] == "not_authorized"
        assert hass.config_entries.async_entries(DOMAIN) == []
        client.return_value.close.assert_awaited_once()


async def test_token_rotation_diagnostics_and_shutdown(hass):
    entry = make_entry()
    await hass.config_entries.async_add(entry)
    client = entry.runtime_data
    client.on_token("rotated-test-token")
    assert entry.data["token"] == "rotated-test-token"
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert set(diagnostics) == {"model", "connected", "version"}
    assert "token" not in repr(diagnostics)
    await hass.async_stop(force=True)
    assert client._closing
