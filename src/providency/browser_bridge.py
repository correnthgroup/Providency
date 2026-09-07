from __future__ import annotations

import asyncio
import re
import secrets
import time
from contextlib import suppress
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect


class BrowserBridge:
    """Session-scoped, authenticated transport to the operator's browser extension.

    No browsing history, cookies, credentials, or protocol messages are persisted.
    The extension exposes only the two allowed application origins.
    """

    def __init__(self) -> None:
        self._pair_code: str | None = None
        self._pair_deadline = 0.0
        self._client_token = secrets.token_urlsafe(32)
        self.extension: WebSocket | None = None
        self.client: WebSocket | None = None

    def begin_pairing(self) -> str:
        if self.extension is not None:
            raise ValueError("O navegador já está conectado.")
        self._pair_code = secrets.token_urlsafe(18)
        self._pair_deadline = time.monotonic() + 300
        return self._pair_code

    @property
    def connected(self) -> bool:
        return self.extension is not None

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._client_token}"}

    async def extension_socket(self, socket: WebSocket) -> None:
        origin = socket.headers.get("origin", "")
        if not re.fullmatch(r"chrome-extension://[a-p]{32}", origin) or self.extension is not None:
            await socket.close(code=1008)
            return
        await socket.accept()
        try:
            hello = await asyncio.wait_for(socket.receive_json(), timeout=10)
            supplied = hello.get("code", "") if isinstance(hello, dict) else ""
            if not (
                self._pair_code
                and time.monotonic() < self._pair_deadline
                and isinstance(supplied, str)
                and secrets.compare_digest(supplied, self._pair_code)
            ):
                await socket.close(code=1008)
                return
            self._pair_code = None
            self.extension = socket
            await socket.send_json({"type": "paired"})
            while True:
                message = await socket.receive_json()
                if not isinstance(message, dict):
                    await socket.close(code=1008)
                    return
                if message.get("type") == "ping":
                    await socket.send_json({"type": "pong"})
                elif self.client is not None and message.get("type") == "cdp":
                    await self.client.send_json(message["message"])
        except (WebSocketDisconnect, TimeoutError, ValueError, RuntimeError):
            pass
        finally:
            if self.extension is socket:
                self.extension = None
                if self.client is not None:
                    with suppress(RuntimeError):
                        await self.client.close(code=1011)

    async def cdp_socket(self, socket: WebSocket) -> None:
        if (
            not secrets.compare_digest(
                socket.headers.get("authorization", ""), self.headers["Authorization"]
            )
            or self.extension is None
            or self.client is not None
        ):
            await socket.close(code=1008)
            return
        await socket.accept()
        self.client = socket
        try:
            while self.extension is not None:
                message: dict[str, Any] = await socket.receive_json()
                await self.extension.send_json({"type": "cdp", "message": message})
        except (WebSocketDisconnect, ValueError, RuntimeError):
            pass
        finally:
            self.client = None
            if self.extension is not None:
                with suppress(RuntimeError):
                    await self.extension.send_json({"type": "detach"})

    async def close(self) -> None:
        for socket in (self.client, self.extension):
            if socket is not None:
                with suppress(RuntimeError):
                    await socket.close()
        self.client = None
        self.extension = None
        self._pair_code = None
