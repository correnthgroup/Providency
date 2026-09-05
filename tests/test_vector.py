from __future__ import annotations

import io
from collections.abc import Mapping
from pathlib import Path

import pytest
from PIL import Image

from providency.config import Settings
from providency.context import PriceAnchor
from providency.vector import (
    AccountEnvironment,
    AppliedVectorState,
    CaptureDisposition,
    CaptureIssue,
    ChartCaptureService,
    DesiredVectorState,
    OrderState,
    PlaywrightControlProbe,
    PlaywrightPageProbe,
    PositionState,
    VectorAdapter,
    VectorAdapterError,
    VectorSelectors,
    VectorStateSynchronizer,
)


class DemoNode:
    def __init__(self, text: str | None) -> None:
        self.text = text

    async def is_visible(self) -> bool:
        return self.text is not None

    async def text_content(self) -> str | None:
        return self.text


class DemoLocator:
    def __init__(self, text: str | None) -> None:
        self.node = DemoNode(text)

    async def count(self) -> int:
        return 1 if self.node.text is not None else 0

    def nth(self, _index: int) -> DemoNode:
        return self.node

    @property
    def first(self) -> DemoNode:
        return self.node


class DemoPage:
    def __init__(self, values: Mapping[str, str]) -> None:
        self.values = values

    def locator(self, selector: str) -> DemoLocator:
        return DemoLocator(self.values.get(selector))


@pytest.mark.asyncio
async def test_demo_observation_uses_explicit_dom_states() -> None:
    configured = VectorSelectors(
        chart="#chart",
        modal="#modal",
        loading="#loading",
        login="#login",
        symbol="#symbol",
        timeframe="#timeframe",
        account_environment="#account",
        quantity_value="#quantity",
        order_state="#order",
        order_id="#order-id",
        order_requested_quantity="#requested",
        order_filled_quantity="#filled",
        position_state="#position",
        position_quantity="#position-quantity",
        position_average_price="#average",
    )
    probe = PlaywrightControlProbe(
        DemoPage(
            {
                "#account": "DEMO",
                "#symbol": "BTC/BRL",
                "#quantity": "2",
                "#order": "PARTIAL",
                "#order-id": "vector-1",
                "#requested": "2",
                "#filled": "1",
                "#position": "SHORT",
                "#position-quantity": "1",
                "#average": "100,50",
            }
        ),
        configured,
    )

    observed = await probe.observe_demo_state()

    assert observed.account is AccountEnvironment.DEMO
    assert observed.order.state is OrderState.PARTIAL
    assert observed.position.state is PositionState.SHORT
    assert observed.position.average_price == 100.5


@pytest.mark.asyncio
async def test_missing_demo_selectors_are_unknown() -> None:
    configured = VectorSelectors("#chart", "#modal", "#loading", "#login", "#symbol", "#tf")
    observed = await PlaywrightControlProbe(DemoPage({}), configured).observe_demo_state()
    assert observed.account is AccountEnvironment.UNKNOWN
    assert observed.order.state is OrderState.UNKNOWN
    assert observed.position.state is PositionState.UNKNOWN


class FakeProbe:
    def __init__(
        self,
        *,
        url: str = "https://vector.example/app",
        visible: set[str] | None = None,
        texts: Mapping[str, str] | None = None,
        boxes: Mapping[str, dict[str, float]] | None = None,
        counts: Mapping[str, int] | None = None,
    ) -> None:
        self.url = url
        self._visible = visible or set()
        self._texts = dict(texts or {})
        self._boxes = dict(boxes or {})
        self._counts = dict(counts or {})

    async def is_visible(self, selector: str) -> bool:
        return selector in self._visible

    async def visible_count(self, selector: str) -> int:
        return self._counts.get(selector, int(selector in self._visible))

    async def text_content(self, selector: str) -> str | None:
        return self._texts.get(selector)

    async def bounding_box(self, selector: str) -> dict[str, float] | None:
        return self._boxes.get(selector)

    async def screenshot(self, selector: str) -> bytes:
        assert selector == "chart"
        image = Image.new("RGB", (640, 360), "white")
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()


class FakeLocator:
    def __init__(self, visibility: list[bool]) -> None:
        self.visibility = visibility

    async def count(self) -> int:
        return len(self.visibility)

    def nth(self, index: int) -> FakeLocatorNode:
        return FakeLocatorNode(self.visibility[index])


class FakeLocatorNode:
    def __init__(self, visible: bool) -> None:
        self.visible = visible

    async def is_visible(self) -> bool:
        return self.visible


class FakePlaywrightPage:
    url = "https://vector.example/app"

    def locator(self, _selector: str) -> FakeLocator:
        return FakeLocator([False, True, True])


@pytest.fixture
def selectors() -> VectorSelectors:
    return VectorSelectors(
        chart="chart",
        modal="modal",
        loading="loading",
        login="login",
        symbol="symbol",
        timeframe="timeframe",
    )


@pytest.mark.asyncio
async def test_capture_writes_webp_and_complete_metadata(
    tmp_path: Path, selectors: VectorSelectors
) -> None:
    probe = FakeProbe(
        visible={"chart", "symbol", "timeframe"},
        texts={"symbol": "BTCUSD", "timeframe": "15m"},
        boxes={"chart": {"x": 12.0, "y": 34.0, "width": 640.0, "height": 360.0}},
    )
    service = ChartCaptureService(
        tmp_path,
        selectors=selectors,
        clock=lambda: "2026-09-05T12:30:00+00:00",
    )

    capture = await service.capture(probe)

    assert capture.disposition is CaptureDisposition.USABLE
    assert capture.issue is None
    assert capture.symbol == "BTCUSD"
    assert capture.timeframe == "15m"
    assert capture.region.width == 640
    assert capture.region.height == 360
    assert capture.path is not None
    assert capture.path.suffix == ".webp"
    assert capture.path.exists()
    assert len(capture.sha256 or "") == 64


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("probe", "issue"),
    [
        (FakeProbe(url="https://vector.example/login"), CaptureIssue.AUTHENTICATION_REQUIRED),
        (FakeProbe(visible={"login"}), CaptureIssue.AUTHENTICATION_REQUIRED),
        (FakeProbe(visible={"loading"}), CaptureIssue.LOADING),
        (FakeProbe(), CaptureIssue.CHART_NOT_FOUND),
        (
            FakeProbe(
                visible={"chart"},
                boxes={"chart": {"x": 0, "y": 0, "width": 100, "height": 80}},
            ),
            CaptureIssue.CHART_UNREADABLE,
        ),
    ],
)
async def test_capture_fails_closed_for_ambiguous_states(
    tmp_path: Path,
    selectors: VectorSelectors,
    probe: FakeProbe,
    issue: CaptureIssue,
) -> None:
    capture = await ChartCaptureService(tmp_path, selectors=selectors).capture(probe)

    assert capture.disposition is CaptureDisposition.NO_DECISION
    assert capture.issue is issue


@pytest.mark.asyncio
async def test_modal_intersecting_chart_blocks_capture(
    tmp_path: Path, selectors: VectorSelectors
) -> None:
    probe = FakeProbe(
        visible={"chart", "modal", "symbol", "timeframe"},
        texts={"symbol": "BTCUSD", "timeframe": "15m"},
        boxes={
            "chart": {"x": 0, "y": 0, "width": 640, "height": 360},
            "modal": {"x": 100, "y": 100, "width": 200, "height": 100},
        },
    )

    capture = await ChartCaptureService(tmp_path, selectors=selectors).capture(probe)

    assert capture.disposition is CaptureDisposition.NO_DECISION
    assert capture.issue is CaptureIssue.MODAL_OBSCURES_CHART


@pytest.mark.asyncio
async def test_multiple_visible_charts_are_ambiguous(
    tmp_path: Path, selectors: VectorSelectors
) -> None:
    probe = FakeProbe(visible={"chart"}, counts={"chart": 2})

    capture = await ChartCaptureService(tmp_path, selectors=selectors).capture(probe)

    assert capture.disposition is CaptureDisposition.NO_DECISION
    assert capture.issue is CaptureIssue.CHART_AMBIGUOUS


@pytest.mark.asyncio
async def test_playwright_probe_counts_only_visible_matches() -> None:
    probe = PlaywrightPageProbe(FakePlaywrightPage())

    assert await probe.visible_count("chart") == 2


@pytest.mark.asyncio
async def test_vector_url_must_use_https(tmp_path: Path) -> None:
    adapter = VectorAdapter(Settings(data_dir=tmp_path, vector_url="file:///sensitive"))

    with pytest.raises(VectorAdapterError) as raised:
        await adapter.start()

    assert raised.value.code == "PV-VECTOR-005"


def test_settings_keep_vector_profile_and_captures_under_data_dir(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)

    assert settings.vector_profile_dir == tmp_path / "vector-profile"
    assert settings.capture_dir == tmp_path / "captures"


class FakeControlProbe:
    def __init__(self, symbol: str, timeframe: str, moving_averages: dict[int, float]) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.moving_averages = moving_averages
        self.actions: list[tuple[str, str]] = []

    async def read_symbol(self) -> str | None:
        return self.symbol

    async def read_timeframe(self) -> str | None:
        return self.timeframe

    async def read_moving_averages(self) -> dict[int, float]:
        return dict(self.moving_averages)

    async def read_price_anchors(self) -> tuple[PriceAnchor, ...]:
        return ()

    async def set_symbol(self, value: str) -> None:
        self.actions.append(("symbol", value))
        self.symbol = value

    async def set_timeframe(self, value: str) -> None:
        self.actions.append(("timeframe", value))
        self.timeframe = value

    async def set_moving_average(self, period: int, enabled: bool) -> None:
        self.actions.append(("ma", f"{period}:{enabled}"))
        if enabled:
            self.moving_averages.setdefault(period, 1.0)
        else:
            self.moving_averages.pop(period, None)


@pytest.mark.asyncio
async def test_state_sync_uses_pre_action_post_and_reports_applied_values() -> None:
    probe = FakeControlProbe("ETH/BRL", "5min", {})
    desired = DesiredVectorState(
        symbol="BTC/BRL", timeframe="15min", moving_average_periods=(7, 70)
    )

    result = await VectorStateSynchronizer(probe).sync(desired)

    assert result.matches_desired
    assert result.pre.symbol == "ETH/BRL"
    assert result.post == AppliedVectorState(
        symbol="BTC/BRL",
        timeframe="15min",
        moving_averages={7: 1.0, 70: 1.0},
        missing_fields=(),
    )
    assert probe.actions == [
        ("symbol", "BTC/BRL"),
        ("timeframe", "15min"),
        ("ma", "7:True"),
        ("ma", "70:True"),
    ]


@pytest.mark.asyncio
async def test_context_capture_always_restores_primary_timeframe() -> None:
    probe = FakeControlProbe("BTC/BRL", "15min", {7: 1.0, 70: 2.0})
    synchronizer = VectorStateSynchronizer(probe)
    captured: list[str] = []

    async def capture() -> str:
        captured.append(probe.timeframe)
        return probe.timeframe

    primary, context, restored = await synchronizer.capture_primary_context(
        DesiredVectorState("BTC/BRL", "15min", (7, 70)),
        context_timeframe="1h",
        capture=capture,
    )

    assert (primary, context) == ("15min", "1h")
    assert restored.matches_desired
    assert probe.timeframe == "15min"


@pytest.mark.asyncio
async def test_missing_applied_state_never_matches_desired() -> None:
    probe = FakeControlProbe("", "", {})
    desired = DesiredVectorState("BTC/BRL", "15min", (7,))
    state = await VectorStateSynchronizer(probe).observe(desired)

    assert not state.matches(desired)
    assert set(state.missing_fields) == {"symbol", "timeframe", "moving_average:7"}
