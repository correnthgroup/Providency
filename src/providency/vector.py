from __future__ import annotations

import asyncio
import hashlib
import io
import os
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from PIL import Image

from providency.config import Settings


class CaptureDisposition(StrEnum):
    USABLE = "USABLE"
    NO_DECISION = "NO_DECISION"


class CaptureIssue(StrEnum):
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    LOADING = "LOADING"
    MODAL_OBSCURES_CHART = "MODAL_OBSCURES_CHART"
    CHART_NOT_FOUND = "CHART_NOT_FOUND"
    CHART_AMBIGUOUS = "CHART_AMBIGUOUS"
    CHART_UNREADABLE = "CHART_UNREADABLE"
    SYMBOL_UNREADABLE = "SYMBOL_UNREADABLE"
    TIMEFRAME_UNREADABLE = "TIMEFRAME_UNREADABLE"


@dataclass(frozen=True, slots=True)
class VectorSelectors:
    chart: str
    modal: str
    loading: str
    login: str
    symbol: str
    timeframe: str

    @classmethod
    def from_env(cls) -> VectorSelectors:
        def configured(name: str, default: str) -> str:
            return os.getenv(name, "").strip() or default

        return cls(
            chart=configured(
                "PROVIDENCY_VECTOR_CHART_SELECTOR",
                '[data-testid*="chart" i], [class*="chart" i] canvas, canvas',
            ),
            modal=configured(
                "PROVIDENCY_VECTOR_MODAL_SELECTOR", '[role="dialog"], [aria-modal="true"]'
            ),
            loading=configured(
                "PROVIDENCY_VECTOR_LOADING_SELECTOR",
                '[aria-busy="true"], [class*="loading" i], [class*="spinner" i]',
            ),
            login=configured(
                "PROVIDENCY_VECTOR_LOGIN_SELECTOR",
                'input[type="password"], form[action*="login" i]',
            ),
            symbol=configured(
                "PROVIDENCY_VECTOR_SYMBOL_SELECTOR",
                '[data-testid*="symbol" i], [class*="symbol" i]',
            ),
            timeframe=configured(
                "PROVIDENCY_VECTOR_TIMEFRAME_SELECTOR",
                '[data-testid*="timeframe" i], [class*="timeframe" i]',
            ),
        )


@dataclass(frozen=True, slots=True)
class CaptureRegion:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class ChartCapture:
    disposition: CaptureDisposition
    captured_at: str
    issue: CaptureIssue | None = None
    symbol: str | None = None
    timeframe: str | None = None
    region: CaptureRegion | None = None
    path: Path | None = None
    sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["disposition"] = self.disposition.value
        payload["issue"] = self.issue.value if self.issue else None
        payload["path"] = str(self.path) if self.path else None
        return payload


class PageProbe(Protocol):
    @property
    def url(self) -> str: ...

    async def is_visible(self, selector: str) -> bool: ...

    async def visible_count(self, selector: str) -> int: ...

    async def text_content(self, selector: str) -> str | None: ...

    async def bounding_box(self, selector: str) -> Mapping[str, float] | None: ...

    async def screenshot(self, selector: str) -> bytes: ...


class VectorAdapterContract(Protocol):
    async def health_check(self) -> dict[str, str]: ...

    async def open_vector(self) -> dict[str, str]: ...

    async def capture_primary_chart(self) -> ChartCapture: ...

    async def stop(self) -> None: ...


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _region(box: Mapping[str, float]) -> CaptureRegion:
    return CaptureRegion(
        x=round(box["x"]),
        y=round(box["y"]),
        width=round(box["width"]),
        height=round(box["height"]),
    )


def _intersects(left: CaptureRegion, right: CaptureRegion) -> bool:
    return not (
        left.x + left.width <= right.x
        or right.x + right.width <= left.x
        or left.y + left.height <= right.y
        or right.y + right.height <= left.y
    )


class ChartCaptureService:
    def __init__(
        self,
        capture_dir: Path,
        *,
        selectors: VectorSelectors | None = None,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self.capture_dir = capture_dir
        self.selectors = selectors or VectorSelectors.from_env()
        self.clock = clock

    async def capture(self, page: PageProbe) -> ChartCapture:
        captured_at = self.clock()
        if "login" in page.url.casefold() or await page.is_visible(self.selectors.login):
            return self._blocked(captured_at, CaptureIssue.AUTHENTICATION_REQUIRED)
        if await page.is_visible(self.selectors.loading):
            return self._blocked(captured_at, CaptureIssue.LOADING)
        chart_count = await page.visible_count(self.selectors.chart)
        if chart_count == 0:
            return self._blocked(captured_at, CaptureIssue.CHART_NOT_FOUND)
        if chart_count > 1:
            return self._blocked(captured_at, CaptureIssue.CHART_AMBIGUOUS)

        chart_box = await page.bounding_box(self.selectors.chart)
        if chart_box is None:
            return self._blocked(captured_at, CaptureIssue.CHART_NOT_FOUND)
        chart_region = _region(chart_box)
        if chart_region.width < 320 or chart_region.height < 180:
            return self._blocked(captured_at, CaptureIssue.CHART_UNREADABLE, chart_region)

        if await page.is_visible(self.selectors.modal):
            modal_box = await page.bounding_box(self.selectors.modal)
            if modal_box is not None and _intersects(chart_region, _region(modal_box)):
                return self._blocked(
                    captured_at, CaptureIssue.MODAL_OBSCURES_CHART, chart_region
                )

        symbol = await self._visible_text(page, self.selectors.symbol)
        if symbol is None:
            return self._blocked(captured_at, CaptureIssue.SYMBOL_UNREADABLE, chart_region)
        timeframe = await self._visible_text(page, self.selectors.timeframe)
        if timeframe is None:
            return self._blocked(captured_at, CaptureIssue.TIMEFRAME_UNREADABLE, chart_region)

        png = await page.screenshot(self.selectors.chart)
        webp = self._to_webp(png)
        digest = hashlib.sha256(webp).hexdigest()
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        safe_timestamp = captured_at.replace(":", "-").replace("+", "_")
        path = self.capture_dir / f"primary-{safe_timestamp}-{digest[:12]}.webp"
        path.write_bytes(webp)
        return ChartCapture(
            disposition=CaptureDisposition.USABLE,
            captured_at=captured_at,
            symbol=symbol,
            timeframe=timeframe,
            region=chart_region,
            path=path,
            sha256=digest,
        )

    async def _visible_text(self, page: PageProbe, selector: str) -> str | None:
        if not await page.is_visible(selector):
            return None
        text = (await page.text_content(selector) or "").strip()
        return text or None

    def _blocked(
        self,
        captured_at: str,
        issue: CaptureIssue,
        region: CaptureRegion | None = None,
    ) -> ChartCapture:
        return ChartCapture(
            disposition=CaptureDisposition.NO_DECISION,
            captured_at=captured_at,
            issue=issue,
            region=region,
        )

    def _to_webp(self, image_bytes: bytes) -> bytes:
        with Image.open(io.BytesIO(image_bytes)) as image:
            output = io.BytesIO()
            image.convert("RGB").save(output, format="WEBP", quality=92, method=6)
            return output.getvalue()


class PlaywrightPageProbe:
    def __init__(self, page: Any) -> None:
        self.page = page

    @property
    def url(self) -> str:
        return str(self.page.url)

    async def is_visible(self, selector: str) -> bool:
        return await self.visible_count(selector) > 0

    async def visible_count(self, selector: str) -> int:
        locator = self.page.locator(selector)
        visible = 0
        for index in range(await locator.count()):
            if await locator.nth(index).is_visible():
                visible += 1
        return visible

    async def _first_visible(self, selector: str) -> Any:
        locator = self.page.locator(selector)
        for index in range(await locator.count()):
            candidate = locator.nth(index)
            if await candidate.is_visible():
                return candidate
        return locator.first

    async def text_content(self, selector: str) -> str | None:
        value = await (await self._first_visible(selector)).text_content()
        return str(value) if value is not None else None

    async def bounding_box(self, selector: str) -> Mapping[str, float] | None:
        box = await (await self._first_visible(selector)).bounding_box()
        return box if box is None else {key: float(value) for key, value in box.items()}

    async def screenshot(self, selector: str) -> bytes:
        value = await (await self._first_visible(selector)).screenshot(type="png")
        return bytes(value)


class VectorAdapterError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class VectorAdapter:
    def __init__(
        self, settings: Settings, *, selectors: VectorSelectors | None = None
    ) -> None:
        self.settings = settings
        self.selectors = selectors or VectorSelectors.from_env()
        self.capture_service = ChartCaptureService(
            settings.capture_dir, selectors=self.selectors
        )
        self._lock = asyncio.Lock()
        self._playwright: Any = None
        self._context: Any = None
        self._page: Any = None

    async def start(self) -> None:
        async with self._lock:
            await self._start_unlocked()

    async def _start_unlocked(self) -> None:
        if self._context is not None:
            return
        if not self.settings.vector_url:
            raise VectorAdapterError(
                "PV-VECTOR-001",
                "Configure PROVIDENCY_VECTOR_URL before opening Vector Web.",
            )
        parsed_url = urlparse(self.settings.vector_url)
        if parsed_url.scheme != "https" or not parsed_url.hostname:
            raise VectorAdapterError(
                "PV-VECTOR-005",
                "PROVIDENCY_VECTOR_URL must be a complete HTTPS URL.",
            )
        from playwright.async_api import async_playwright

        self.settings.vector_profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.settings.vector_profile_dir),
                headless=False,
            )
            self._page = (
                self._context.pages[0]
                if self._context.pages
                else await self._context.new_page()
            )
        except Exception:
            await self._playwright.stop()
            self._playwright = None
            raise VectorAdapterError(
                "PV-VECTOR-004",
                "Chromium could not start. Install the Playwright Chromium browser.",
            ) from None

    async def open_vector(self) -> dict[str, str]:
        async with self._lock:
            await self._start_unlocked()
            assert self._page is not None
            await self._page.goto(self.settings.vector_url, wait_until="domcontentloaded")
            return {"state": "WAITING_FOR_MANUAL_LOGIN", "url": str(self._page.url)}

    async def health_check(self) -> dict[str, str]:
        running = (
            self._context is not None
            and self._page is not None
            and not bool(self._page.is_closed())
        )
        return {"state": "OPEN" if running else "CLOSED"}

    async def capture_primary_chart(self) -> ChartCapture:
        async with self._lock:
            if self._page is None:
                raise VectorAdapterError("PV-VECTOR-002", "Open Vector Web before capturing.")
            try:
                return await self.capture_service.capture(PlaywrightPageProbe(self._page))
            except VectorAdapterError:
                raise
            except Exception as exc:
                raise VectorAdapterError(
                    "PV-VECTOR-003", "Vector chart capture failed."
                ) from exc

    async def stop(self) -> None:
        async with self._lock:
            if self._context is not None:
                await self._context.close()
            if self._playwright is not None:
                await self._playwright.stop()
            self._page = None
            self._context = None
            self._playwright = None
