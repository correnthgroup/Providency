from __future__ import annotations

import asyncio
import hashlib
import io
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, TypeVar
from urllib.parse import urlparse

from PIL import Image

from providency.config import Settings
from providency.context import PriceAnchor


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
    PRICE_SCALE_UNREADABLE = "PRICE_SCALE_UNREADABLE"
    APPLIED_STATE_MISMATCH = "APPLIED_STATE_MISMATCH"


@dataclass(frozen=True, slots=True)
class DesiredVectorState:
    symbol: str
    timeframe: str
    moving_average_periods: tuple[int, ...] = ()
    require_price_scale: bool = False


@dataclass(frozen=True, slots=True)
class AppliedVectorState:
    symbol: str | None
    timeframe: str | None
    moving_averages: dict[int, float]
    missing_fields: tuple[str, ...]
    price_anchors: tuple[PriceAnchor, ...] = ()

    def matches(self, desired: DesiredVectorState) -> bool:
        return (
            not self.missing_fields
            and self.symbol == desired.symbol
            and self.timeframe == desired.timeframe
            and all(period in self.moving_averages for period in desired.moving_average_periods)
            and (not desired.require_price_scale or len(self.price_anchors) >= 2)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "moving_averages": {str(key): value for key, value in self.moving_averages.items()},
            "missing_fields": list(self.missing_fields),
            "price_anchors": [asdict(anchor) for anchor in self.price_anchors],
        }


@dataclass(frozen=True, slots=True)
class StateSyncResult:
    desired: DesiredVectorState
    pre: AppliedVectorState
    post: AppliedVectorState

    @property
    def matches_desired(self) -> bool:
        return self.post.matches(self.desired)

    def to_dict(self) -> dict[str, Any]:
        return {
            "desired": asdict(self.desired),
            "pre": self.pre.to_dict(),
            "post": self.post.to_dict(),
            "matches_desired": self.matches_desired,
        }


class VectorControlProbe(Protocol):
    async def read_symbol(self) -> str | None: ...

    async def read_timeframe(self) -> str | None: ...

    async def read_moving_averages(self) -> dict[int, float]: ...

    async def read_price_anchors(self) -> tuple[PriceAnchor, ...]: ...

    async def set_symbol(self, value: str) -> None: ...

    async def set_timeframe(self, value: str) -> None: ...

    async def set_moving_average(self, period: int, enabled: bool) -> None: ...


CaptureValue = TypeVar("CaptureValue")


class VectorStateSynchronizer:
    def __init__(self, probe: VectorControlProbe) -> None:
        self.probe = probe

    async def observe(self, desired: DesiredVectorState) -> AppliedVectorState:
        symbol = (await self.probe.read_symbol() or "").strip() or None
        timeframe = (await self.probe.read_timeframe() or "").strip() or None
        moving_averages = await self.probe.read_moving_averages()
        price_anchors = await self.probe.read_price_anchors()
        missing: list[str] = []
        if symbol is None:
            missing.append("symbol")
        if timeframe is None:
            missing.append("timeframe")
        for period in desired.moving_average_periods:
            if period not in moving_averages:
                missing.append(f"moving_average:{period}")
        if desired.require_price_scale and len(price_anchors) < 2:
            missing.append("price_scale")
        return AppliedVectorState(
            symbol, timeframe, moving_averages, tuple(missing), price_anchors
        )

    async def sync(self, desired: DesiredVectorState) -> StateSyncResult:
        pre = await self.observe(desired)
        if pre.symbol != desired.symbol:
            await self.probe.set_symbol(desired.symbol)
        if pre.timeframe != desired.timeframe:
            await self.probe.set_timeframe(desired.timeframe)
        for period in desired.moving_average_periods:
            if period not in pre.moving_averages:
                await self.probe.set_moving_average(period, True)
        post = await self.observe(desired)
        return StateSyncResult(desired, pre, post)

    async def capture_primary_context(
        self,
        desired: DesiredVectorState,
        *,
        context_timeframe: str,
        capture: Callable[[], Awaitable[CaptureValue]],
    ) -> tuple[CaptureValue, CaptureValue, StateSyncResult]:
        primary_sync = await self.sync(desired)
        if not primary_sync.matches_desired:
            raise VectorAdapterError(
                "PV-VECTOR-006", "Primary analysis state could not be verified."
            )
        primary = await capture()
        context_desired = DesiredVectorState(
            desired.symbol,
            context_timeframe,
            desired.moving_average_periods,
            desired.require_price_scale,
        )
        try:
            context_sync = await self.sync(context_desired)
            if not context_sync.matches_desired:
                raise VectorAdapterError(
                    "PV-VECTOR-007", "Context timeframe could not be verified."
                )
            context = await capture()
        finally:
            restored = await self.sync(desired)
        if not restored.matches_desired:
            raise VectorAdapterError(
                "PV-VECTOR-008", "Primary timeframe restoration could not be verified."
            )
        return primary, context, restored


@dataclass(frozen=True, slots=True)
class VectorSelectors:
    chart: str
    modal: str
    loading: str
    login: str
    symbol: str
    timeframe: str
    symbol_control: str = ""
    timeframe_control: str = ""
    moving_average_value: str = ""
    moving_average_toggle_template: str = ""
    price_anchor: str = ""

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
            symbol_control=os.getenv("PROVIDENCY_VECTOR_SYMBOL_CONTROL_SELECTOR", "").strip(),
            timeframe_control=os.getenv("PROVIDENCY_VECTOR_TIMEFRAME_CONTROL_SELECTOR", "").strip(),
            moving_average_value=os.getenv("PROVIDENCY_VECTOR_MA_VALUE_SELECTOR", "").strip(),
            moving_average_toggle_template=os.getenv(
                "PROVIDENCY_VECTOR_MA_TOGGLE_SELECTOR_TEMPLATE", ""
            ).strip(),
            price_anchor=os.getenv("PROVIDENCY_VECTOR_PRICE_ANCHOR_SELECTOR", "").strip(),
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

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ChartCapture:
        issue = payload.get("issue")
        region = payload.get("region")
        path = payload.get("path")
        return cls(
            disposition=CaptureDisposition(str(payload["disposition"])),
            captured_at=str(payload["captured_at"]),
            issue=CaptureIssue(str(issue)) if issue else None,
            symbol=str(payload["symbol"]) if payload.get("symbol") else None,
            timeframe=str(payload["timeframe"]) if payload.get("timeframe") else None,
            region=CaptureRegion(**region) if isinstance(region, Mapping) else None,
            path=Path(str(path)) if path else None,
            sha256=str(payload["sha256"]) if payload.get("sha256") else None,
        )


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

    async def sync_analysis_state(self, desired: DesiredVectorState) -> StateSyncResult: ...

    async def capture_primary_context(
        self, desired: DesiredVectorState, context_timeframe: str
    ) -> tuple[ChartCapture, ChartCapture, StateSyncResult]: ...

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
                return self._blocked(captured_at, CaptureIssue.MODAL_OBSCURES_CHART, chart_region)

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


class PlaywrightControlProbe:
    _number = re.compile(r"[-+]?\d[\d.,]*")

    def __init__(self, page: Any, selectors: VectorSelectors) -> None:
        self.page = page
        self.selectors = selectors
        self.reader = PlaywrightPageProbe(page)

    async def _text(self, selector: str) -> str | None:
        if not selector or not await self.reader.is_visible(selector):
            return None
        value = await self.reader.text_content(selector)
        return value.strip() if value and value.strip() else None

    async def read_symbol(self) -> str | None:
        return await self._text(self.selectors.symbol)

    async def read_timeframe(self) -> str | None:
        return await self._text(self.selectors.timeframe)

    async def read_moving_averages(self) -> dict[int, float]:
        selector = self.selectors.moving_average_value
        if not selector:
            return {}
        values: dict[int, float] = {}
        locator = self.page.locator(selector)
        for text in await locator.all_text_contents():
            numbers = self._number.findall(str(text))
            if len(numbers) < 2:
                continue
            period = int(numbers[0].replace(".", "").replace(",", ""))
            raw = numbers[-1]
            if "," in raw and "." in raw:
                raw = raw.replace(".", "").replace(",", ".")
            elif "," in raw:
                raw = raw.replace(",", ".")
            values[period] = float(raw)
        return values

    @staticmethod
    def _decimal(text: str) -> float:
        raw = text.strip().replace(" ", "")
        if "," in raw and "." in raw:
            raw = raw.replace(".", "").replace(",", ".")
        elif "," in raw:
            raw = raw.replace(",", ".")
        return float(raw)

    async def read_price_anchors(self) -> tuple[PriceAnchor, ...]:
        selector = self.selectors.price_anchor
        if not selector:
            return ()
        locator = self.page.locator(selector)
        chart_box = await self.reader.bounding_box(self.selectors.chart)
        chart_top = float(chart_box["y"]) if chart_box else 0.0
        anchors: list[PriceAnchor] = []
        for index in range(await locator.count()):
            item = locator.nth(index)
            if not await item.is_visible():
                continue
            text = str(await item.text_content() or "")
            matches = self._number.findall(text)
            box = await item.bounding_box()
            if not matches or box is None:
                continue
            anchors.append(
                PriceAnchor(
                    y=float(box["y"]) + float(box["height"]) / 2 - chart_top,
                    price=self._decimal(matches[-1]),
                    source="DOM_GEOMETRY",
                )
            )
        return tuple(anchors)

    async def _choose(self, selector: str, value: str) -> None:
        if not selector:
            raise VectorAdapterError(
                "PV-VECTOR-009", "An explicit non-financial control selector is required."
            )
        locator = self.page.locator(selector).first
        await locator.select_option(label=value)

    async def set_symbol(self, value: str) -> None:
        await self._choose(self.selectors.symbol_control, value)

    async def set_timeframe(self, value: str) -> None:
        await self._choose(self.selectors.timeframe_control, value)

    async def set_moving_average(self, period: int, enabled: bool) -> None:
        template = self.selectors.moving_average_toggle_template
        if not template or "{period}" not in template:
            raise VectorAdapterError(
                "PV-VECTOR-009", "An explicit moving-average selector template is required."
            )
        locator = self.page.locator(template.format(period=period)).first
        checked = bool(await locator.is_checked())
        if checked != enabled:
            await locator.click()


class VectorAdapterError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class VectorAdapter:
    def __init__(self, settings: Settings, *, selectors: VectorSelectors | None = None) -> None:
        self.settings = settings
        self.selectors = selectors or VectorSelectors.from_env()
        self.capture_service = ChartCaptureService(settings.capture_dir, selectors=self.selectors)
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
                self._context.pages[0] if self._context.pages else await self._context.new_page()
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
                raise VectorAdapterError("PV-VECTOR-003", "Vector chart capture failed.") from exc

    async def sync_analysis_state(self, desired: DesiredVectorState) -> StateSyncResult:
        async with self._lock:
            if self._page is None:
                raise VectorAdapterError("PV-VECTOR-002", "Open Vector Web before syncing.")
            synchronizer = VectorStateSynchronizer(
                PlaywrightControlProbe(self._page, self.selectors)
            )
            return await synchronizer.sync(desired)

    async def capture_primary_context(
        self, desired: DesiredVectorState, context_timeframe: str
    ) -> tuple[ChartCapture, ChartCapture, StateSyncResult]:
        async with self._lock:
            if self._page is None:
                raise VectorAdapterError("PV-VECTOR-002", "Open Vector Web before capturing.")
            page_probe = PlaywrightPageProbe(self._page)
            synchronizer = VectorStateSynchronizer(
                PlaywrightControlProbe(self._page, self.selectors)
            )

            async def capture() -> ChartCapture:
                return await self.capture_service.capture(page_probe)

            return await synchronizer.capture_primary_context(
                desired, context_timeframe=context_timeframe, capture=capture
            )

    async def stop(self) -> None:
        async with self._lock:
            if self._context is not None:
                await self._context.close()
            if self._playwright is not None:
                await self._playwright.stop()
            self._page = None
            self._context = None
            self._playwright = None
