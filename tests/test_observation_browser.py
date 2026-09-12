from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest
from playwright.async_api import async_playwright
from synthetic_chart import BEARISH, BULLISH, write_chart

from providency.catalog import PatternCatalog
from providency.config import Settings
from providency.observation import ObservationCoordinator
from providency.storage import Storage
from providency.vector import VectorAdapter, VectorAdapterError, discover_browser_charts


@pytest.mark.asyncio
async def test_borrowed_browser_capture_saved_analyzed_and_notified(tmp_path: Path) -> None:
    screenshot = write_chart(
        tmp_path / "fixture.png",
        [
            (40, 135, 140, 150, 156, BULLISH),
            (90, 120, 126, 136, 143, BULLISH),
            (140, 105, 111, 121, 128, BULLISH),
            (190, 85, 92, 103, 110, BULLISH),
            (240, 78, 84, 108, 115, BEARISH),
            (290, 95, 100, 120, 130, BEARISH),
        ],
        size=(640, 360),
    )
    uri = "data:image/png;base64," + base64.b64encode(screenshot.read_bytes()).decode()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content(f"""
        <div data-testid="asset-tab" str-asset-key-entity="btc"
             class="asset-tabs-component__tab--active" onclick="
          document.querySelector('.asset-tabs-component__tab--active').className='';
          this.className='asset-tabs-component__tab--active';
          document.querySelector('#symbol').textContent='BTC/BRL'">
          <span class="title-ticker">BTC/BRL</span><span class="title-period">15Min</span>
        </div>
        <div data-testid="asset-tab" str-asset-key-entity="eth" onclick="
          document.querySelector('.asset-tabs-component__tab--active').className='';
          this.className='asset-tabs-component__tab--active';
          document.querySelector('#symbol').textContent='ETH/BRL';
          const canvas=document.querySelector('.manager-content__canvas');
          canvas.remove();
          setTimeout(() => document.querySelector('#graphic-manager-content').append(canvas), 150)">
          <span class="title-ticker">ETH/BRL</span><span class="title-period">15Min</span>
        </div>
        <div role="dialog"><div id="graphic-manager-content">
          <div class="title-bar__indicators__item__text"><div>
            <div id="symbol" class="hover:underline">BTC/BRL</div>
            <div class="hover:underline">15Min</div>
          </div></div>
          <div class="manager-content__canvas" style="width:640px;height:360px">
            <img src="{uri}">
          </div>
        </div></div><div class="candle-clock">02:10</div>
        <button onclick="window.financial=true">Comprar</button>
        """)

        async def pages() -> list:
            return [page]

        adapter = VectorAdapter(Settings(data_dir=tmp_path), pages_provider=pages)
        storage = Storage(tmp_path / "test.db")
        storage.initialize()
        catalog = PatternCatalog(Path(__file__).parents[1] / "patterns")
        messages = []

        class Telegram:
            async def send_observation_summary(self, *, chat_id: int, text: str) -> dict:
                messages.append(text)
                return {"message_id": 42}

        coordinator = ObservationCoordinator(
            storage,
            adapter,
            catalog,
            Telegram(),
            telegram_chat_id=123,  # type: ignore[arg-type]
        )
        charts = await discover_browser_charts([page])
        coordinator.configure({"charts": charts, "enabled_pattern_ids": ["bearish_engulfing"]})
        result = await coordinator.run_cycle_once()
        assert result["status"] == "COMPLETE"
        assert len(messages) == 1 and "Engolfo" in messages[0]
        for item in result["results"]:
            assert item["pattern_results"][0]["status"] == "MATCH"
            evidence = item["evidence"]
            assert (
                hashlib.sha256(Path(evidence["path"]).read_bytes()).hexdigest()
                == evidence["sha256"]
            )
        assert storage.observation_pattern_references()
        with storage.connect() as connection:
            assert (
                connection.execute("SELECT status FROM observation_outbox").fetchone()[0] == "SENT"
            )
        assert await page.evaluate("Boolean(window.financial)") is False
        await page.locator(".candle-clock").evaluate("e => e.remove()")
        uncertain = await coordinator.run_cycle_once()
        assert all(
            item["pattern_results"][0]["status"] == "FORMING" for item in uncertain["results"]
        )
        assert "identificado(s)" not in uncertain["summary"]
        # A changed confirmed identity must not be relabeled as the old chart.
        await page.locator('[str-asset-key-entity="eth"] .title-period').evaluate(
            "e => e.textContent='60Min'"
        )
        changed = await coordinator.run_cycle_once()
        assert changed["results"][1]["analysis"] == "NO_DECISION"
        await adapter.stop()
        assert not page.is_closed()
        await page.locator('[str-asset-key-entity="eth"]').evaluate("e => e.remove()")
        with pytest.raises(VectorAdapterError, match="disponível"):
            await adapter.capture_chart(charts[1]["id"])
        await browser.close()


def test_catalog_produces_saved_reference_images(tmp_path: Path) -> None:
    catalog = PatternCatalog(Path(__file__).parents[1] / "patterns", illustration_dir=tmp_path)
    for item in catalog.to_list():
        assert Path(item["illustration"]["path"]).is_file()
        assert "didática" in item["illustration"]["label"]
