# ruff: noqa: E501
from pathlib import Path

import pytest

from providency.catalog import PatternCatalog
from providency.observation import ObservationCoordinator
from providency.storage import Storage
from providency.vector import CaptureDisposition, CaptureIssue, ChartCapture


class FakeAdapter:
    async def capture_chart(self, chart_id: str) -> ChartCapture:
        return ChartCapture(
            CaptureDisposition.NO_DECISION,
            "2026-09-10T12:00:00+00:00",
            CaptureIssue.LOADING,
            chart_id=chart_id,
        )

    async def capture_primary_chart(self) -> ChartCapture:
        return await self.capture_chart("chart-1")


@pytest.mark.asyncio
async def test_observation_cycle_distinguishes_unusable_capture_from_no_match(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    coordinator = ObservationCoordinator(
        storage,
        FakeAdapter(),  # type: ignore[arg-type]
        PatternCatalog(Path(__file__).parents[1] / "patterns"),
        telegram=None,
    )
    coordinator.configure(
        {
            "interval": {"value": 5, "unit": "minutes", "timezone": "UTC"},
            "charts": [{"id": "chart-1", "symbol": "BTCUSDT", "timeframe": "15m"}],
            "enabled_pattern_ids": ["bearish_engulfing"],
        }
    )
    result = await coordinator.run_cycle_once()
    assert result["status"] == "COMPLETE"
    assert result["results"][0]["capture"] == "NO_DECISION"
    assert result["results"][0]["analysis"] == "NO_DECISION"
    assert "nenhum padrão" not in result["summary"].lower()
