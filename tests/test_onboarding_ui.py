import io
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from providency.api import create_app
from providency.config import Settings
from providency.startup import StartupProgress


def test_start_button_prepares_connection_without_starting_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv('PROVIDENCY_LAUNCH_ID', raising=False)
    with TestClient(create_app(Settings(data_dir=tmp_path), telegram_polling=False)) as client:
        def local_request(request: object, **_kwargs: object) -> io.BytesIO:
            url = urlsplit(request.full_url)
            response = client.request(request.get_method(), url.path,
                                      json=json.loads(request.data) if request.data else None)
            response.raise_for_status()
            return io.BytesIO(response.content)

        monkeypatch.setattr('urllib.request.urlopen', local_request)
        ui = AppTest.from_file(str(Path(__file__).parents[1] / 'src/providency/ui.py'),
                               default_timeout=15).run()
        assert not ui.exception
        assert list(ui.radio[0].options) == ['Início', 'Configurações', 'Atividades']
        next(button for button in ui.button if button.label == 'Iniciar BOT').click().run()
        assert not ui.exception
        assert client.get('/onboarding').json()['stage'] == 'VECTOR_WAIT'
        assert client.get('/state').json()['engine_state'] == 'STOPPED'


def test_startup_screen_can_cancel_before_api_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('PROVIDENCY_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('PROVIDENCY_LAUNCH_ID', 'test-startup')
    progress = StartupProgress(tmp_path)
    progress.begin('test-startup')
    ui = AppTest.from_file(str(Path(__file__).parents[1] / 'src/providency/ui.py'),
                           default_timeout=15).run()
    assert not ui.exception
    next(button for button in ui.button if button.label == 'Encerrar Providency').click().run()
    assert progress.cancelled()
