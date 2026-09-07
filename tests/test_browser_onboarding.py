from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright

from providency.config import Settings
from providency.onboarding import Onboarding


def test_preparation_cannot_skip_discovery(tmp_path: Path) -> None:
    app = FastAPI()
    onboarding = Onboarding(Settings(data_dir=tmp_path))
    onboarding.install(app)
    with TestClient(app) as client:
        assert client.get('/onboarding').json()['stage'] == 'WELCOME'
        assert client.post('/onboarding/vector-confirm', json={}).status_code == 409
        assert client.post('/onboarding/start', json={}).json()['stage'] == 'VECTOR_WAIT'
        assert client.post('/onboarding/vector-check', json={}).status_code == 409
        assert client.post('/onboarding/pair', json={},
                           headers={'Origin': 'https://evil.example'}).status_code == 403


@pytest.mark.asyncio
async def test_real_extension_reads_internal_tabs_and_preserves_browser(tmp_path: Path) -> None:
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    onboarding = Onboarding(Settings(data_dir=tmp_path, api_port=port))
    app = FastAPI()
    onboarding.install(app)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(.05)
    extension = Path(__file__).parents[1] / 'browser-extension'
    try:
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                str(tmp_path / 'browser'), channel='chromium', headless=True,
                args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}'],
            )
            try:
                await context.route('https://web.vectorcrypto.com/**', lambda route: route.fulfill(
                    body='''<div data-testid="asset-tab" str-asset-key-entity="btc15">
                    <span class="title-ticker">BTC/BRL</span>
                    <span class="title-period">15Min</span></div>
                    <div data-testid="asset-tab" str-asset-key-entity="eth60">
                    <span class="title-ticker">ETH/BRL</span>
                    <span class="title-period">60Min</span></div>''',
                    content_type='text/html',
                ))
                page = await context.new_page()
                await page.goto('https://web.vectorcrypto.com/')
                worker = context.service_workers[0] if context.service_workers else (
                    await context.wait_for_event('serviceworker'))
                result = await worker.evaluate(
                    'async ({port, code}) => await connect(port, code)',
                    {'port': port, 'code': onboarding.bridge.begin_pairing()},
                )
                assert result['ok']
                from providency.vector import discover_browser_charts

                charts = await discover_browser_charts(
                    await onboarding.pages('web.vectorcrypto.com'))
                assert [(c['symbol'], c['timeframe']) for c in charts] == [
                    ('BTC/BRL', '15Min'), ('ETH/BRL', '60Min')]
                await context.route('https://web.telegram.org/**', lambda route: route.fulfill(
                    body='<div id="MiddleColumn"><div class="chat-info">'
                    '<div class="title">Grupo de teste</div></div></div>',
                    content_type='text/html',
                ))
                telegram = await context.new_page()
                await telegram.goto('https://web.telegram.org/a/#-100123456')
                async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}') as client:
                    async def advance(command: str, payload: dict | None = None) -> dict:
                        response = await client.post(f'/onboarding/{command}', json=payload or {})
                        assert response.status_code == 200, response.text
                        return response.json()

                    await advance('start')
                    assert (await advance('vector-check'))['stage'] == 'VECTOR_REVIEW'
                    await advance('vector-confirm')
                    # Allow the extension's tab-complete event to attach the new page.
                    for _ in range(50):
                        if await onboarding.pages('web.telegram.org'):
                            break
                        await asyncio.sleep(.1)
                    assert (await advance('telegram-check'))['stage'] == 'TELEGRAM_REVIEW'
                    final = await advance('telegram-confirm', {'index': 0})
                    assert final['stage'] == 'READY'
                    assert final['destination']['name'] == 'Grupo de teste'
                await onboarding.close()
                assert not page.is_closed()
                assert await page.locator('[data-testid="asset-tab"]').count() == 2
            finally:
                await context.close()
    finally:
        await onboarding.close()
        server.should_exit = True
        await asyncio.wait_for(task, 10)
