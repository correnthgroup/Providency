import io
import json
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from providency.api import create_app
from providency.config import AnalysisConfiguration, Settings
from providency.onboarding import Onboarding
from providency.vector import VectorAdapter


def test_refresh_preserves_manual_limits_and_records_only_verified_market_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manual = AnalysisConfiguration(
        symbol="BTC/BRL", min_rr=3, max_trades=4, max_consecutive_losses=2, max_session_loss=100
    )
    monkeypatch.setattr(Onboarding, "pages", AsyncMock(return_value=[]))
    reader = AsyncMock(
        return_value={
            "fields": {"symbol": "BTC/BRL", "primary_timeframe": "15Min", "quantity": 0.25},
            "balance": {"amount": 246436, "currency": "BRL"},
        }
    )
    monkeypatch.setattr(VectorAdapter, "read_browser_settings", reader)
    with TestClient(
        create_app(
            Settings(data_dir=tmp_path, analysis_configuration=manual), telegram_polling=False
        )
    ) as client:
        assert (
            client.post(
                "/configuration/refresh", json={}, headers={"Origin": "https://external.example"}
            ).status_code
            == 403
        )
        assert reader.await_count == 0
        result = client.post("/configuration/refresh", json={})
        assert result.status_code == 200
        desired = result.json()["desired"]
        assert desired["quantity"] == 0.25
        assert desired["primary_timeframe"] == "15Min"
        assert desired["min_rr"] == 3
        assert desired["max_trades"] == 4
        assert desired["max_consecutive_losses"] == 2
        assert desired["max_session_loss"] == 100
        assert result.json()["applied"] is None
        reader.side_effect = ValueError("Vector disconnected")
        assert client.post("/configuration/refresh", json={}).status_code == 409
        after = client.get("/configuration").json()
        assert after["desired"] == desired
        assert after["vector_snapshot"] is None
        client.post("/run")
        calls_before = reader.await_count
        assert client.post("/configuration/refresh", json={}).status_code == 409
        assert reader.await_count == calls_before


def test_settings_update_button_populates_readonly_market_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PROVIDENCY_LAUNCH_ID", raising=False)
    monkeypatch.setattr(Onboarding, "pages", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        VectorAdapter,
        "read_browser_settings",
        AsyncMock(
            return_value={
                "fields": {
                    "symbol": "BTC/BRL",
                    "primary_timeframe": "15Min",
                    "quantity": 0.25,
                    "tick_size": 1,
                    "tick_value": 1,
                },
                "balance": {"amount": 246436, "currency": "BRL"},
                "source": "Vector Web",
                "observed_at": "2026-09-08T22:00:00+00:00",
                "charts": [],
                "notes": [],
                "order": {
                    "price": {"text": "400.928", "unit": "BRL"},
                    "total": {"text": "100.232,00", "unit": "BRL"},
                },
            }
        ),
    )
    configuration = AnalysisConfiguration(min_rr=3, max_trades=4, max_consecutive_losses=2)
    with TestClient(
        create_app(
            Settings(data_dir=tmp_path, analysis_configuration=configuration),
            telegram_polling=False,
        )
    ) as client:

        def local_request(request, **_kwargs):
            url = urlsplit(request.full_url)
            response = client.request(
                request.get_method(),
                url.path,
                json=json.loads(request.data) if request.data else None,
            )
            response.raise_for_status()
            return io.BytesIO(response.content)

        monkeypatch.setattr("urllib.request.urlopen", local_request)
        ui = AppTest.from_file(
            str(Path(__file__).parents[1] / "src/providency/ui.py"), default_timeout=15
        ).run()
        ui.radio[0].set_value("Configurações").run()
        assert not ui.exception
        next(b for b in ui.button if b.label == "Atualizar").click().run()
        assert not ui.exception
        quantity = next(n for n in ui.number_input if n.label == "Quantidade")
        assert quantity.value == 0.25 and quantity.disabled
        assert next(n for n in ui.number_input if n.label == "Risco/retorno mínimo").value == 3
        assert (
            next(n for n in ui.number_input if n.label == "Máximo de operações diárias").value == 4
        )
        assert (
            next(m for m in ui.metric if m.label == "Saldo disponível na Vector").value
            == "246.436,00 BRL"
        )
