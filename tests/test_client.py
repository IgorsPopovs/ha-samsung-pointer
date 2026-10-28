"""Exercise the real async client against a simulated TV, never a physical TV."""

import asyncio
import json
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import web

from custom_components.samsung_pointer.client import (
    PointerAuthError,
    PointerBusyError,
    PointerError,
    SamsungPointerClient,
    click,
    movement,
    validate_host,
)


@pytest.fixture
async def tv(aiohttp_server):
    state = {"commands": [], "connections": 0, "sockets": [], "event": "ms.channel.connect"}

    async def handler(request):
        state["connections"] += 1
        state["query"] = dict(request.query)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        state["sockets"].append(ws)
        await ws.send_json({"event": state["event"], "data": {"token": "test-only-token"}})
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                state["commands"].append(json.loads(msg.data))
        return ws

    app = web.Application()
    app.router.add_get("/api/v2/channels/samsung.remote.control", handler)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        original = session.ws_connect

        def local_connect(url, **kwargs):
            return original(
                url.with_scheme("ws").with_host("127.0.0.1").with_port(server.port), **kwargs
            )

        session.ws_connect = local_connect
        client = SamsungPointerClient(session, "tv.example", name="Test Pointer")
        yield client, state
        await client.close()


async def wait_for(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.01)


async def test_pair_move_click_and_reuse_connection(tv):
    client, state = tv
    saved = []
    client.on_token = saved.append
    await client.connect()
    assert saved == ["test-only-token"]
    assert client.connected
    await client.move(12, -4)
    await client.click()
    await wait_for(lambda: len(state["commands"]) == 2)
    assert state["commands"] == [movement(12, -4), click("left")]
    assert state["connections"] == 1


async def test_fractional_moves_accumulate_until_a_whole_pixel(tv):
    client, state = tv
    await client.connect()
    await client.move(0.4, -0.4)
    await client.move(0.4, -0.4)
    assert state["commands"] == []
    await client.move(0.4, -0.4)
    await wait_for(lambda: len(state["commands"]) == 1)
    assert state["commands"] == [movement(1, -1)]


async def test_disconnect_reconnect_drops_old_gesture(tv):
    client, state = tv
    await client.connect()
    await state["sockets"][0].close()
    await wait_for(lambda: not client.connected)
    await client.move(100, 100)
    assert client.connected
    assert state["commands"] == []
    assert state["query"]["token"] == "test-only-token"
    await client.move(3, 4)
    await wait_for(lambda: len(state["commands"]) == 1)
    assert state["commands"] == [movement(3, 4)]


async def test_click_not_replayed_after_reconnect(tv):
    client, state = tv
    with pytest.raises(PointerBusyError, match="click again"):
        await client.click()
    assert client.connected
    assert state["commands"] == []


async def test_busy_gestures_are_not_queued(tv):
    client, state = tv
    await client.connect()
    async with client._lock:
        await client.move(100, 100)
        with pytest.raises(PointerBusyError):
            await client.click()
    await client.move(1, 2)
    await wait_for(lambda: len(state["commands"]) == 1)
    assert state["commands"] == [movement(1, 2)]


async def test_authorization_rejection_does_not_prompt_repeatedly(tv):
    client, state = tv
    state["event"] = "ms.channel.unauthorized"
    with pytest.raises(PointerAuthError):
        await client.connect()
    with pytest.raises(PointerAuthError):
        await client.connect()
    assert state["connections"] == 1
    assert not client.connected
    assert state["commands"] == []


async def test_timeout_closes_connection(tv):
    client, state = tv
    state["event"] = "ms.channel.ready"
    with pytest.raises(PointerError, match="Cannot connect"):
        await client.connect(timeout=0.1)
    assert not client.connected
    assert client._ws is None
    with pytest.raises(PointerError, match="cooling down"):
        await client.connect()
    assert state["connections"] == 1


async def test_cancel_pairing_closes_connection(tv):
    client, state = tv
    state["event"] = "ms.channel.ready"
    task = asyncio.create_task(client.connect())
    await wait_for(lambda: state["connections"] == 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not client.connected
    assert client._ws is None


async def test_failed_send_not_retried(tv):
    client, state = tv
    await client.connect()
    client._ws.send_json = AsyncMock(side_effect=aiohttp.ClientConnectionError("secret-url"))
    with pytest.raises(PointerError, match="not retried") as error:
        await client.click()
    assert "secret-url" not in str(error.value)
    assert not client.connected
    assert state["commands"] == []


async def test_close_blocks_later_connections(tv):
    client, state = tv
    await client.close()
    with pytest.raises(PointerError, match="shutting down"):
        await client.connect()
    assert state["connections"] == 0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -2001, 2001, "bad"])
def test_invalid_deltas(value):
    with pytest.raises(ValueError):
        movement(value, 0)


def test_negative_and_fractional_deltas():
    assert movement("-12.7", "4.2")["params"]["Position"] == {"x": -13, "y": 4, "Time": "0"}


@pytest.mark.parametrize("host", ["http://tv.local", "tv.local/path", "user@tv.local", "a..b", ""])
def test_invalid_hosts(host):
    with pytest.raises(ValueError):
        validate_host(host)


@pytest.mark.parametrize("host", ["192.0.2.1", "tv.local", "::1"])
def test_valid_hosts(host):
    assert validate_host(host) == host
