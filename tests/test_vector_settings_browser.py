from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from providency.vector import VectorAdapter

FIXTURE = """
<div data-testid="asset-tab" str-asset-key-entity="btc15"
 class="asset-tabs-component__tab--active">
 <span class="title-ticker">BTC/BRL</span><span class="title-period">15Min</span></div>
<div data-testid="asset-tab" str-asset-key-entity="eth60">
 <span class="title-ticker">ETH/BRL</span><span class="title-period">60Min</span></div>
<div class="graphic-order-crypto">
 <span data-testid="account-selector-name">SIM TEST</span>
 <div class="leverage" style="display:none">20x</div>
 <div onboarding-id="chart-input-price">Preço
  <input-number step="1" str-complementary-info="BRL" data-value="400.928"></input-number></div>
 <div onboarding-id="chart-input-quantity">Qtd
  <input-number step="0.00001" str-complementary-info="BTC"
   data-value="0,25000"></input-number></div>
 <div onboarding-id="chart-input-total">Total
  <input-number step="1" str-complementary-info="BRL" data-value="100.232,00"></input-number></div>
 <button onclick="window.clicked=true">Comprar</button>
</div>
<div class="asset-position__crypto-info">
 <div class="info"><span class="key" data-key="qty">BTC</span><span class="value">0</span></div>
 <div class="info"><span class="key" data-key="qty">BRL</span>
  <span class="value">246.436,00</span></div>
</div>
<script>
customElements.define('input-number', class extends HTMLElement {
 connectedCallback() {
  const input=document.createElement('input'); input.value=this.getAttribute('data-value');
  this.attachShadow({mode:'open'}).append(input);
 }
});
</script>"""


@pytest.mark.asyncio
async def test_live_shadow_inputs_read_fractional_quantity_and_balance(tmp_path: Path) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(FIXTURE)
            result = await VectorAdapter.read_browser_settings([page])
            assert result["fields"] == {
                "symbol": "BTC/BRL",
                "primary_timeframe": "15Min",
                "quantity": 0.25,
                "tick_size": 1,
                "tick_value": 1,
            }
            assert result["balance"] == {"amount": 246436, "currency": "BRL"}
            assert len(result["charts"]) == 2
            assert result["order"]["quantity"]["step"] == 0.00001
            assert await page.evaluate("Boolean(window.clicked)") is False
            assert "SIM TEST" not in str(result)
            # No tick is derived from displayed price decimals when step metadata is missing.
            await page.locator("input-number").first.evaluate("el => el.removeAttribute('step')")
            result = await VectorAdapter.read_browser_settings([page])
            assert "tick_size" not in result["fields"]
            assert "tick_value" not in result["fields"]
            # Missing balance cannot silently become zero.
            await page.locator(".asset-position__crypto-info").evaluate("el => el.remove()")
            assert (await VectorAdapter.read_browser_settings([page]))["balance"] is None
            # Mismatched ticket units fail closed rather than importing another instrument.
            await page.locator('[onboarding-id="chart-input-quantity"] input-number').evaluate(
                "el => el.setAttribute('str-complementary-info', 'ETH')"
            )
            with pytest.raises(ValueError, match="unidades"):
                await VectorAdapter.read_browser_settings([page])
        finally:
            await browser.close()
