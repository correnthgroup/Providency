import asyncio
import io
import json
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import urlsplit
from urllib.request import Request

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from streamlit.testing.v1 import AppTest
from test_protection import filled_operation

from providency.api import create_app
from providency.config import Settings
from providency.storage import Storage
from providency.vector import VectorAdapter


def test_shutdown_stops_session_and_rejects_further_commands(tmp_path: Path) -> None:
    requested: list[bool] = []
    app = create_app(
        Settings(data_dir=tmp_path),
        telegram_polling=False,
        request_shutdown=lambda: requested.append(True),
    )
    with TestClient(app) as client:
        client.post('/run').raise_for_status()
        response = client.post('/shutdown', json={'confirm': True})
        assert response.status_code == 202
        assert response.json()['status'] == 'SHUTTING_DOWN'
        assert requested == [True]
        assert client.get('/state').json()['engine_state'] == 'STOPPED'
        assert client.post('/run').status_code == 409
        assert client.post('/vector/open').status_code == 409
        assert client.post('/shutdown', json={'confirm': True}).status_code == 202
        assert requested == [True]


def test_shutdown_requires_explicit_json_and_rejects_foreign_origin(tmp_path: Path) -> None:
    requested: list[bool] = []
    app = create_app(
        Settings(data_dir=tmp_path),
        telegram_polling=False,
        request_shutdown=lambda: requested.append(True),
    )
    with TestClient(app) as client:
        assert client.post('/shutdown').status_code == 422
        assert client.post('/shutdown', json={'confirm': False}).status_code == 422
        response = client.post(
            '/shutdown', json={'confirm': True}, headers={'Origin': 'https://untrusted.example'}
        )
        assert response.status_code == 403
        assert requested == []


def test_embedded_api_does_not_pretend_to_stop_without_runtime_hook(tmp_path: Path) -> None:
    with TestClient(create_app(Settings(data_dir=tmp_path), telegram_polling=False)) as client:
        client.post('/run').raise_for_status()
        assert client.post('/shutdown', json={'confirm': True}).status_code == 409
        assert client.get('/state').json()['engine_state'] == 'RUNNING'


@pytest.mark.parametrize('status', ['FILLED', 'PENDING', 'AMBIGUOUS', 'PARTIAL'])
def test_shutdown_cannot_abandon_persisted_demo_exposure(tmp_path: Path, status: str) -> None:
    settings = Settings(data_dir=tmp_path)
    storage = Storage(settings.database_path)
    storage.initialize()
    operation = filled_operation(storage)
    with storage.connect() as connection:
        connection.execute(
            'UPDATE operations SET status = ? WHERE id = ?', (status, operation['operation_id'])
        )
        connection.commit()
    requested: list[bool] = []
    app = create_app(
        settings, recover=False, telegram_polling=False,
        request_shutdown=lambda: requested.append(True),
    )
    with TestClient(app) as client:
        response = client.post('/shutdown', json={'confirm': True})
        assert response.status_code == 409
        assert 'operação pendente' in response.json()['detail']
        assert requested == []
        assert client.get('/state').json()['engine_state'] == 'RUNNING'


@pytest.mark.asyncio
async def test_vector_driver_stops_even_when_browser_close_fails(tmp_path: Path) -> None:
    adapter = VectorAdapter(Settings(data_dir=tmp_path))
    context = AsyncMock()
    context.close.side_effect = RuntimeError('Already disconnected')
    driver = AsyncMock()
    adapter._context = context
    adapter._playwright = driver
    with pytest.raises(RuntimeError, match='Already disconnected'):
        await adapter.stop()
    driver.stop.assert_awaited_once()
    assert adapter._context is None
    assert adapter._playwright is None
    await adapter.stop()


@pytest.mark.asyncio
async def test_shutdown_waits_for_an_in_flight_vector_command(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    adapter = VectorAdapter(settings)

    async def opening() -> dict[str, str]:
        entered.set()
        await release.wait()
        return {'state': 'WAITING_FOR_MANUAL_LOGIN'}

    adapter.open_vector = AsyncMock(side_effect=opening)  # type: ignore[method-assign]
    requested: list[bool] = []
    app = create_app(
        settings, telegram_polling=False, vector_adapter=adapter,
        request_shutdown=lambda: requested.append(True),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        opening_task = asyncio.create_task(client.post('/vector/open'))
        await asyncio.wait_for(entered.wait(), timeout=5)
        try:
            response = await client.post('/shutdown', json={'confirm': True})
            assert response.status_code == 409
            assert requested == []
        finally:
            release.set()
            await opening_task
        assert (await client.post('/shutdown', json={'confirm': True})).status_code == 202


def test_shutdown_cancels_pending_approval_without_sending_any_order(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    storage = Storage(settings.database_path)
    storage.initialize()
    filled_operation(storage)
    with storage.connect() as connection:
        connection.execute("UPDATE operations SET status = 'REJECTED'")
        connection.execute("UPDATE approvals SET status = 'PENDING'")
        connection.commit()
    app = create_app(
        settings, recover=False, telegram_polling=False, request_shutdown=lambda: None,
    )
    with TestClient(app) as client:
        assert client.post('/shutdown', json={'confirm': True}).status_code == 202
        assert client.get('/approvals').json()[0]['status'] == 'CANCELLED'
        assert client.get('/operations').json()[0]['status'] == 'REJECTED'


def test_streamlit_shutdown_button_calls_api_and_displays_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[bool] = []
    app = create_app(
        Settings(data_dir=tmp_path), telegram_polling=False,
        request_shutdown=lambda: requested.append(True),
    )
    with TestClient(app) as client:
        def local_request(request: Request, **_kwargs: object) -> io.BytesIO:
            url = urlsplit(request.full_url)
            response = client.request(
                request.get_method(), url.path + ('?' + url.query if url.query else ''),
                json=json.loads(request.data) if request.data else None,
            )
            response.raise_for_status()
            return io.BytesIO(response.content)

        monkeypatch.setattr('urllib.request.urlopen', local_request)
        ui = AppTest.from_file(
            str(Path(__file__).parents[1] / 'src/providency/ui.py'), default_timeout=15,
        ).run()
        assert not ui.exception
        button = next(button for button in ui.button if button.label == 'Encerrar Providency')
        button.click().run()
        assert not ui.exception
        assert requested == [True]
        assert any('Encerramento solicitado' in message.value for message in ui.success)
