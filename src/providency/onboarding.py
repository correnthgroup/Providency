from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, WebSocket
from playwright.async_api import Browser, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError

from providency.browser_bridge import BrowserBridge
from providency.config import Settings


class Onboarding:
    """Read-only preparation. It never starts the engine or sends a message."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bridge = BrowserBridge()
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.stage = "WELCOME"
        self.charts: list[dict[str, Any]] = []
        self.destinations: list[dict[str, str]] = []
        self.destination: dict[str, str] | None = None
        self.lock = asyncio.Lock()

    async def pages(self, host: str) -> list[Any]:
        if not self.bridge.connected:
            self.stage = "VECTOR_WAIT"
            raise ValueError("Conecte a extensão do Providency neste navegador.")
        if self.browser is None or not self.browser.is_connected():
            if self.playwright is None:
                self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.connect_over_cdp(
                f"ws://127.0.0.1:{self.settings.api_port}/browser-bridge/cdp",
                headers=self.bridge.headers,
                timeout=8000,
            )
        return [
            page
            for context in self.browser.contexts
            for page in context.pages
            if urlparse(page.url).hostname == host
        ]

    def state(self) -> dict[str, Any]:
        if not self.bridge.connected and self.stage not in {"WELCOME", "VECTOR_WAIT"}:
            self.stage = "VECTOR_WAIT"
            self.charts = []
            self.destination = None
        return {
            "stage": self.stage,
            "connected": self.bridge.connected,
            "charts": self.charts,
            "destinations": self.destinations,
            "destination": self.destination,
        }

    async def close(self) -> None:
        # CDP browser.close disconnects this transport; never close borrowed contexts.
        if self.browser is not None and self.browser.is_connected():
            await self.browser.close()
        if self.playwright is not None:
            await self.playwright.stop()
        await self.bridge.close()

    def install(self, app: FastAPI) -> None:
        @app.websocket("/browser-bridge/extension")
        async def extension(socket: WebSocket) -> None:
            await self.bridge.extension_socket(socket)

        @app.websocket("/browser-bridge/cdp")
        async def cdp(socket: WebSocket) -> None:
            await self.bridge.cdp_socket(socket)

        @app.get("/onboarding")
        async def status() -> dict[str, Any]:
            return self.state()

        @app.post("/onboarding/{command}")
        async def advance(command: str, request: Request) -> dict[str, Any]:
            origin = request.headers.get("origin")
            if origin and origin not in {
                f"http://127.0.0.1:{self.settings.ui_port}",
                f"http://localhost:{self.settings.ui_port}",
            }:
                raise HTTPException(403, "Origem não autorizada.")
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                raise HTTPException(415, "Envie JSON.")
            async with self.lock:
                self.state()
                try:
                    if command == "start":
                        self.stage = "VECTOR_WAIT"
                        self.charts = []
                        self.destination = None
                    elif command == "pair":
                        return {"code": self.bridge.begin_pairing()}
                    elif command == "vector-check" and self.stage == "VECTOR_WAIT":
                        from providency.vector import discover_browser_charts

                        self.charts = await discover_browser_charts(
                            await self.pages("web.vectorcrypto.com")
                        )
                        if not self.charts:
                            raise ValueError("Abra a Vector Web já conectada e seus gráficos.")
                        self.stage = "VECTOR_REVIEW"
                    elif command == "vector-confirm" and self.stage == "VECTOR_REVIEW":
                        self.stage = "TELEGRAM_WAIT"
                    elif command == "telegram-check" and self.stage == "TELEGRAM_WAIT":
                        self.destinations = []
                        for page in await self.pages("web.telegram.org"):
                            header = page.locator(
                                "#column-center .chat-info .peer-title, "
                                "#MiddleColumn .chat-info .title"
                            ).first
                            if await header.count():
                                title = (await header.inner_text(timeout=2000)).strip()
                                peer = urlparse(page.url).fragment
                                if title and peer:
                                    self.destinations.append({"name": title, "peer": peer})
                        if not self.destinations:
                            raise ValueError("Abra o Telegram Web e selecione o grupo de destino.")
                        self.stage = "TELEGRAM_REVIEW"
                    elif command == "telegram-confirm" and self.stage == "TELEGRAM_REVIEW":
                        payload = await request.json()
                        if not isinstance(payload, dict):
                            raise ValueError("Selecione um destino identificado.")
                        index = payload.get("index")
                        if type(index) is not int or not 0 <= index < len(self.destinations):
                            raise ValueError("Selecione um destino identificado.")
                        self.destination = self.destinations[index]
                        self.stage = "READY"
                    else:
                        raise ValueError("Esta etapa não está disponível. Atualize a tela.")
                except ValueError as exc:
                    raise HTTPException(409, str(exc)) from exc
                except PlaywrightError as exc:
                    raise HTTPException(
                        409,
                        "A conexão com a aba não respondeu. Confira a extensão e tente novamente.",
                    ) from exc
                return self.state()
