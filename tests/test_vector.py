from __future__ import annotations

import io
from collections.abc import Mapping
from pathlib import Path

import pytest
from PIL import Image

from providency.config import Settings
from providency.vector import (
    CaptureDisposition,
    CaptureIssue,
    ChartCaptureService,
    PlaywrightPageProbe,
    VectorAdapter,
    VectorAdapterError,
    VectorSelectors,
)


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
