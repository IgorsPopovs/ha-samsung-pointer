"""Small asynchronous Samsung mouse client, independent of SamsungTV Smart.

The TV does not acknowledge mouse commands. A successful send proves only that
the message reached the socket, not that the current app displayed a pointer.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import math
import re
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any

import aiohttp
from yarl import URL


class PointerError(Exception):
    """A safe-to-display error, never containing TV responses or token URLs."""


class PointerAuthError(PointerError):
    """The TV rejected authorization."""


class PointerBusyError(PointerError):
    """A previous operation is still in progress."""


def validate_host(value: str) -> str:
    """Accept an IP or hostname, never a URL with paths or credentials."""
    host = value.strip()
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        if (
            len(host) <= 253
            and re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", host)
            and all(0 < len(label) <= 63 for label in host.split("."))
        ):
            return host.lower()
        raise ValueError("Enter a TV IP address or hostname, without a URL") from None


def movement(x: float, y: float) -> dict[str, Any]:
    """Build bounded mouse deltas; accept templated numeric strings."""
    values = [round(value) for value in _movement_delta(x, y)]
    return {
        "method": "ms.remote.control",
        "params": {
            "Cmd": "Move",
            "Position": {"x": values[0], "y": values[1], "Time": "0"},
            "TypeOfRemote": "ProcessMouseDevice",
        },
    }


def _movement_delta(x: float, y: float) -> tuple[float, float]:
    values = (float(x), float(y))
    if any(not math.isfinite(value) or abs(value) > 2000 for value in values):
        raise ValueError("Mouse delta must be finite and between -2000 and 2000")
    return values


def click(button: str) -> dict[str, Any]:
    """Only complete clicks are exposed, so no button can remain held down."""
    if button not in ("left", "right"):
        raise ValueError("Button must be left or right")
    return {
        "method": "ms.remote.control",
        "params": {
            "Cmd": "LeftClick" if button == "left" else "RightClick",
            "TypeOfRemote": "ProcessMouseDevice",
        },
    }


async def get_device_info(session: aiohttp.ClientSession, host: str) -> dict[str, str]:
    """Read only the identity fields needed for configuration."""
    url = URL.build(scheme="http", host=validate_host(host), port=8001, path="/api/v2/")
    try:
        async with asyncio.timeout(5):
            async with session.get(url, allow_redirects=False) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)
        device = data["device"]
        identifier = device.get("id") or device.get("duid")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("Missing identity")
        return {
            "tv_id": identifier.removeprefix("uuid:"),
            "model": str(device.get("modelName", "Samsung TV")),
        }
    except aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError:
        raise PointerError(
            "Cannot read TV identity; turn on the TV and check its address"
        ) from None


class SamsungPointerClient:
    """Keep one socket open and coalesce movement while a send is in progress."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        *,
        name: str,
        token: str | None = None,
        port: int = 8002,
        on_token: Callable[[str], None] | None = None,
        on_state: Callable[[], None] | None = None,
    ) -> None:
        self.session = session
        self.host = validate_host(host)
        self.name = name
        self.token = token
        self.port = port
        self.on_token = on_token
        self.on_state = on_state
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._closing = False
        self._authorized = False
        self._retry_at = 0.0
        self._auth_failed = False
        self._move_remainder_x = 0.0
        self._move_remainder_y = 0.0
        self._queued_move_x = 0.0
        self._queued_move_y = 0.0

    @property
    def connected(self) -> bool:
        return bool(self._authorized and self._ws and not self._ws.closed)

    def _notify(self) -> None:
        if self.on_state:
            self.on_state()

    async def connect(self, *, timeout: float = 12) -> None:
        async with self._lock:
            await self._connect(timeout)
            self._clear_queued_moves()

    async def _connect(self, timeout: float) -> None:
        if self._closing:
            raise PointerError("Pointer is shutting down")
        if self.connected:
            return
        if self._auth_failed:
            raise PointerAuthError("Reconfigure Samsung Pointer to authorize the TV again")
        if time.monotonic() < self._retry_at:
            raise PointerError("TV connection is cooling down; try again in a few seconds")
        await self._disconnect()
        query = {"name": base64.b64encode(self.name.encode()).decode()}
        if self.token:
            query["token"] = self.token
        url = URL.build(
            scheme="wss",
            host=self.host,
            port=self.port,
            path="/api/v2/channels/samsung.remote.control",
        ).with_query(query)
        ws = None
        try:
            async with asyncio.timeout(timeout):
                # Samsung's local service has a self-signed TLS certificate.
                # This exception is scoped to this TV socket only.
                ws = await self.session.ws_connect(
                    url, ssl=False, autoping=True, heartbeat=None, compress=0, max_msg_size=262144
                )
                if self._closing:
                    raise PointerError("Pointer is shutting down")
                self._ws = ws
                async for message in ws:
                    if message.type != aiohttp.WSMsgType.TEXT:
                        continue
                    try:
                        data = json.loads(message.data)
                    except ValueError:
                        continue
                    if not isinstance(data, dict):
                        continue
                    event = data.get("event")
                    if event in ("ms.channel.unauthorized", "ms.channel.timeOut"):
                        raise PointerAuthError("TV did not authorize Samsung Pointer")
                    if event == "ms.channel.connect":
                        payload = data.get("data")
                        new_token = payload.get("token") if isinstance(payload, dict) else None
                        if new_token is not None and str(new_token) != self.token:
                            self.token = str(new_token)
                            if self.on_token:
                                self.on_token(self.token)
                        self._authorized = True
                        self._reader = asyncio.create_task(self._read(ws))
                        self._notify()
                        return
                raise PointerError("TV closed the connection before authorization")
        except PointerAuthError:
            self._auth_failed = True
            raise
        except aiohttp.ClientError, TimeoutError, OSError:
            raise PointerError(
                "Cannot connect to TV; check power, network and pairing permission"
            ) from None
        finally:
            if not self._authorized:
                if ws:
                    await ws.close()
                self._ws = None
                self._retry_at = time.monotonic() + 10
                self._notify()

    async def _read(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Drain events so aiohttp can answer TV ping frames."""
        try:
            async for message in ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    with suppress(ValueError, TypeError, AttributeError):
                        data = json.loads(message.data)
                        if data.get("event") == "ms.channel.unauthorized":
                            self._auth_failed = True
                            break
        except aiohttp.ClientError, OSError:
            pass
        finally:
            if self._ws is ws:
                self._authorized = False
                self._notify()
            await ws.close()

    async def move(
        self,
        x: float,
        y: float,
        *,
        speed_threshold: float = 8,
        max_speed_gain: float = 4,
    ) -> None:
        delta_x, delta_y = _movement_delta(x, y)
        speed_threshold = float(speed_threshold)
        max_speed_gain = float(max_speed_gain)
        if not math.isfinite(speed_threshold) or speed_threshold <= 0:
            raise ValueError("Speed threshold must be a positive finite number")
        if not math.isfinite(max_speed_gain) or not 1 <= max_speed_gain <= 8:
            raise ValueError("Maximum speed gain must be between 1 and 8")
        if delta_x == 0 and delta_y == 0:
            return
        speed = math.hypot(delta_x, delta_y)
        gain = min(max_speed_gain, max(1.0, speed / speed_threshold))
        delta_x = max(-2000.0, min(2000.0, delta_x * gain))
        delta_y = max(-2000.0, min(2000.0, delta_y * gain))
        if self._lock.locked():
            self._queue_move(delta_x, delta_y)
            return
        async with self._lock:
            if not self.connected:
                await self._connect(12)
                self._clear_queued_moves()
                return  # The old gesture is stale after reconnect. The next one moves.
            await self._send_move(delta_x, delta_y)
            await self._flush_queued_moves()

    def _queue_move(self, x: float, y: float) -> None:
        self._queued_move_x = max(-2000.0, min(2000.0, self._queued_move_x + x))
        self._queued_move_y = max(-2000.0, min(2000.0, self._queued_move_y + y))

    def _clear_queued_moves(self) -> None:
        self._queued_move_x = 0.0
        self._queued_move_y = 0.0

    async def _send_move(self, x: float, y: float) -> None:
        total_x = self._move_remainder_x + x
        total_y = self._move_remainder_y + y
        move_x, move_y = math.trunc(total_x), math.trunc(total_y)
        self._move_remainder_x = total_x - move_x
        self._move_remainder_y = total_y - move_y
        if move_x or move_y:
            await self._send(movement(move_x, move_y))

    async def _flush_queued_moves(self) -> None:
        while self.connected and (self._queued_move_x or self._queued_move_y):
            delta_x, delta_y = self._queued_move_x, self._queued_move_y
            self._clear_queued_moves()
            await self._send_move(delta_x, delta_y)

    async def click(self, button: str = "left") -> None:
        payload = click(button)
        if self._lock.locked():
            raise PointerBusyError("Pointer is busy; try the click again")
        async with self._lock:
            if not self.connected:
                await self._connect(12)
                raise PointerBusyError("Pointer reconnected; click again")
            await self._send(payload)
            await self._flush_queued_moves()

    async def _send(self, payload: dict[str, Any]) -> None:
        try:
            async with asyncio.timeout(1):
                if not self._ws or self._ws.closed:
                    raise PointerError("TV connection was lost")
                await self._ws.send_json(payload)
        except aiohttp.ClientError, OSError, TimeoutError:
            await self._disconnect()
            raise PointerError("TV connection was lost; command was not retried") from None

    async def _disconnect(self) -> None:
        self._authorized = False
        self._move_remainder_x = 0.0
        self._move_remainder_y = 0.0
        self._clear_queued_moves()
        reader, self._reader = self._reader, None
        ws, self._ws = self._ws, None
        if reader:
            reader.cancel()
            with suppress(asyncio.CancelledError):
                await reader
        if ws:
            await ws.close()
        self._notify()

    async def disconnect(self) -> None:
        """Disconnect only the pointer; never power off the TV."""
        async with self._lock:
            await self._disconnect()

    async def close(self) -> None:
        self._closing = True
        await self._disconnect()
