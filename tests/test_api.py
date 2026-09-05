from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from synthetic_chart import bearish_engulfing_chart

from providency.api import create_app
from providency.config import AnalysisConfiguration, ExecutionMode, Settings, TelegramConfiguration
from providency.context import ConfluenceItem, ConfluenceResult, ConfluenceStatus, PriceAnchor
from providency.evidence import file_sha256
from providency.telegram import TelegramCallback, TelegramPollBatch
from providency.vector import (
    AccountEnvironment,
    AppliedVectorState,
    CaptureDisposition,
    CaptureRegion,
    ChartCapture,
    ClosedCandleObservation,
    DemoAccountState,
    DemoExecutionResult,
    DesiredVectorState,
    OrderState,
    OrderStateObservation,
    PositionState,
    PositionStateObservation,
    ProtectionExecutionResult,
    ProtectionOrderObservation,
    ProtectionOrderState,
    ProtectionStateObservation,
    StateSyncResult,
    TradeSide,
)

ROOT = Path(__file__).parents[1]


def settings(tmp_path: Path, configuration: AnalysisConfiguration | None = None) -> Settings:
    return Settings(
        data_dir=tmp_path,
        patterns_dir=ROOT / "patterns",
        analysis_configuration=configuration,
    )


class FakeVectorAdapter:
    def __init__(self, capture_path: Path) -> None:
        self.capture_path = capture_path
        self.stopped = False
        self.sha256 = "a" * 64
        self.demo_states: list[DemoAccountState] = []
        self.demo_submit_calls = 0
        self.protection: ProtectionOrderObservation = ProtectionOrderObservation(
            ProtectionOrderState.NONE, None, None, None, None
        )

    async def health_check(self) -> dict[str, str]:
        return {"state": "OPEN"}

    async def open_vector(self) -> dict[str, str]:
        return {"state": "WAITING_FOR_MANUAL_LOGIN", "url": "https://vector.example/app"}

    async def capture_primary_chart(self) -> ChartCapture:
        if not self.capture_path.exists():
            self.capture_path.write_bytes(b"webp")
        return ChartCapture(
            disposition=CaptureDisposition.USABLE,
            captured_at="2026-09-05T12:30:00+00:00",
            symbol="BTCUSD",
            timeframe="15m",
            region=CaptureRegion(x=1, y=2, width=640, height=360),
            path=self.capture_path,
            sha256=self.sha256,
        )

    async def sync_analysis_state(self, desired: DesiredVectorState) -> StateSyncResult:
        applied = AppliedVectorState(
            desired.symbol,
            desired.timeframe,
            {period: float(period) for period in desired.moving_average_periods},
            (),
            (
                PriceAnchor(0, 110, "DOM_GEOMETRY"),
                PriceAnchor(100, 60, "DOM_GEOMETRY"),
            ),
        )
        return StateSyncResult(desired, applied, applied)

    async def capture_primary_context(
        self, desired: DesiredVectorState, context_timeframe: str
    ) -> tuple[ChartCapture, ChartCapture, StateSyncResult]:
        primary = await self.capture_primary_chart()
        context = await self.capture_primary_chart()
        applied = AppliedVectorState(
            desired.symbol,
            desired.timeframe,
            {period: float(period) for period in desired.moving_average_periods},
            (),
            (
                PriceAnchor(0, 110, "DOM_GEOMETRY"),
                PriceAnchor(100, 60, "DOM_GEOMETRY"),
            ),
        )
        return primary, context, StateSyncResult(desired, applied, applied)

    async def observe_demo_state(self) -> DemoAccountState:
        return self.demo_states[0]

    async def prepare_demo_order(self, quantity: int) -> DemoExecutionResult:
        pre = self.demo_states.pop(0)
        post = self.demo_states[0]
        assert post.quantity == quantity
        return DemoExecutionResult(pre, post, "SET_QUANTITY")

    async def select_demo_account(self) -> DemoExecutionResult:
        pre = self.demo_states.pop(0)
        post = self.demo_states[0]
        return DemoExecutionResult(pre, post, "SELECT_DEMO_ACCOUNT")

    async def submit_demo_order(
        self, side: TradeSide, *, symbol: str, quantity: int
    ) -> DemoExecutionResult:
        self.demo_submit_calls += 1
        pre = self.demo_states.pop(0)
        post = self.demo_states[0]
        assert side is TradeSide.SELL
        assert symbol == "BTC/BRL"
        assert quantity == 2
        return DemoExecutionResult(pre, post, side.value)

    async def observe_protection_state(self, timeframe: str) -> ProtectionStateObservation:
        return ProtectionStateObservation(
            self.demo_states[0],
            self.protection,
            ClosedCandleObservation(timeframe, None, None, None),
        )

    async def apply_demo_stop(
        self, *, side: TradeSide, quantity: int, stop_price: float, timeframe: str
    ) -> ProtectionExecutionResult:
        pre = await self.observe_protection_state(timeframe)
        self.protection = ProtectionOrderObservation(
            ProtectionOrderState.ACTIVE, "stop-1", side, quantity, stop_price
        )
        return ProtectionExecutionResult(
            pre, await self.observe_protection_state(timeframe), "APPLY_STOP"
        )

    async def cancel_demo_protection(
        self, *, order_id: str, timeframe: str
    ) -> ProtectionExecutionResult:
        pre = await self.observe_protection_state(timeframe)
        assert self.protection.order_id == order_id
        self.protection = ProtectionOrderObservation(
            ProtectionOrderState.CANCELLED, order_id, None, None, None
        )
        return ProtectionExecutionResult(
            pre, await self.observe_protection_state(timeframe), "CANCEL_PROTECTION"
        )

    async def close_demo_position(
        self, *, side: TradeSide, quantity: int, timeframe: str
    ) -> ProtectionExecutionResult:
        pre = await self.observe_protection_state(timeframe)
        current = self.demo_states[0]
        self.demo_states[0] = DemoAccountState(
            current.account,
            current.symbol,
            current.quantity,
            current.order,
            PositionStateObservation(PositionState.FLAT, 0, None),
        )
        return ProtectionExecutionResult(
            pre, await self.observe_protection_state(timeframe), "CLOSE_POSITION"
        )

    async def stop(self) -> None:
        self.stopped = True


class FakeTelegramClient:
    def __init__(self) -> None:
        self.yes_callback = ""
        self.no_callback = ""
        self.callbacks: list[TelegramCallback] = []
        self.answers: list[str] = []

    async def health_check(self) -> dict[str, str]:
        return {"state": "READY", "bot_username": "providency_test_bot"}

    async def send_proposal(
        self, *, chat_id: int, text: str, yes_callback: str, no_callback: str
    ) -> dict[str, int]:
        assert chat_id == 10
        assert "WOULD_EXECUTE" in text or "one demo submission" in text
        self.yes_callback = yes_callback
        self.no_callback = no_callback
        return {"message_id": 7, "chat_id": chat_id}

    async def poll(self, *, offset: int | None = None) -> TelegramPollBatch:
        callbacks = tuple(self.callbacks)
        self.callbacks.clear()
        next_offset = callbacks[-1].update_id + 1 if callbacks else offset
        return TelegramPollBatch(callbacks, next_offset)

    async def answer_callback(self, callback_query_id: str, text: str) -> None:
        self.answers.append(text)

    def queue(self, data: str, *, chat_id: int, user_id: int) -> None:
        self.callbacks.append(
            TelegramCallback(
                update_id=len(self.answers) + len(self.callbacks) + 1,
                callback_query_id=f"callback-{len(self.answers) + len(self.callbacks) + 1}",
                data=data,
                chat_id=chat_id,
                user_id=user_id,
            )
        )


def all_context_checks_pass(**_: object) -> ConfluenceResult:
    passed = ConfluenceItem(ConfluenceStatus.PASS, "Verified in test.")
    return ConfluenceResult(
        {
            "trend": passed,
            "structure": passed,
            "support_resistance": passed,
            "context_timeframe": passed,
        }
    )


def demo_state(
    *, order: OrderState = OrderState.NONE, position: PositionState = PositionState.FLAT
) -> DemoAccountState:
    return DemoAccountState(
        account=AccountEnvironment.DEMO,
        symbol="BTC/BRL",
        quantity=2,
        order=OrderStateObservation(
            order, "vector-1" if order is not OrderState.NONE else None, 2, 2
        ),
        position=PositionStateObservation(
            position, 0 if position is PositionState.FLAT else 2, 100.0
        ),
    )


def test_run_stop_and_activity_api(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path), recover=False)

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/state").json() == {"engine_state": "STOPPED", "session": None}

        started = client.post("/run").json()
        assert started["status"] == "RUNNING"
        assert client.get("/state").json()["engine_state"] == "RUNNING"

        stopped = client.post("/stop").json()
        assert stopped["id"] == started["id"]
        assert stopped["status"] == "STOPPED"

        events = client.get("/events").json()
        assert [event["event_type"] for event in events] == [
            "SESSION_STOPPED",
            "SESSION_STARTED",
        ]


def test_vector_open_capture_and_activity_api(tmp_path: Path) -> None:
    adapter = FakeVectorAdapter(tmp_path / "capture.webp")
    app = create_app(settings(tmp_path), recover=False, vector_adapter=adapter)

    with TestClient(app) as client:
        assert client.get("/vector/health").json() == {"state": "OPEN"}
        assert client.post("/vector/open").json()["state"] == "WAITING_FOR_MANUAL_LOGIN"

        capture = client.post("/vector/capture").json()
        assert capture["disposition"] == "USABLE"
        assert capture["symbol"] == "BTCUSD"
        assert capture["timeframe"] == "15m"

        event = client.get("/events").json()[0]
        assert event["event_type"] == "VECTOR_CAPTURE_USABLE"
        assert event["details"]["sha256"] == "a" * 64

    assert adapter.stopped


def test_pattern_analysis_api_returns_visual_evidence_and_persists_it(tmp_path: Path) -> None:
    capture_path = bearish_engulfing_chart(tmp_path / "capture.webp")
    adapter = FakeVectorAdapter(capture_path)

    async def valid_capture() -> ChartCapture:
        return ChartCapture(
            disposition=CaptureDisposition.USABLE,
            captured_at="2026-09-05T12:30:00+00:00",
            symbol="BTCUSD",
            timeframe="15m",
            region=CaptureRegion(x=1, y=2, width=320, height=200),
            path=capture_path,
            sha256="b" * 64,
        )

    adapter.capture_primary_chart = valid_capture  # type: ignore[method-assign]
    app = create_app(settings(tmp_path), recover=False, vector_adapter=adapter)

    with TestClient(app) as client:
        result = client.post("/pattern/analyze").json()
        assert result["status"] == "MATCH"
        assert result["pattern_id"] == "bearish_engulfing"
        assert result["pattern_version"] == "0.1.0"
        assert len(result["evidence"]["candles"]) == 5
        assert len(result["measures"]) == 2
        assert result["reason"]

        persisted = client.get("/pattern/detections").json()
        assert persisted[0]["screenshot_sha256"] == "b" * 64
        assert persisted[0]["result"] == "MATCH"


def test_candidate_api_exposes_complete_auditable_preflight(tmp_path: Path) -> None:
    configuration = AnalysisConfiguration(
        symbol="BTC/BRL",
        primary_timeframe="15min",
        context_timeframe="1h",
        trailing_timeframe="30min",
        short_ma_period=7,
        long_ma_period=70,
        quantity=2,
        tick_size=0.5,
        tick_value=1.0,
        stop_buffer_ticks=1,
        min_rr=2,
        max_trades=3,
        max_consecutive_losses=2,
        max_session_loss=100,
        support_resistance_tolerance=5,
    )
    adapter = FakeVectorAdapter(tmp_path / "capture.webp")
    bearish_engulfing_chart(adapter.capture_path)
    app = create_app(settings(tmp_path, configuration), recover=False, vector_adapter=adapter)

    with TestClient(app) as client:
        client.post("/run")
        sync = client.post("/vector/sync")
        assert sync.status_code == 200
        capture_pair = client.post("/vector/capture-analysis").json()
        response = client.post(
            "/candidate/evaluate",
            json={
                "analysis_capture_id": capture_pair["analysis_capture_id"],
                "pattern_detection_id": capture_pair["pattern_detection_id"],
            },
        )

        assert response.status_code == 200
        candidate = response.json()
        assert candidate["decision"] in {"ALLOWED", "BLOCKED"}
        assert candidate["entry"] is not None
        assert candidate["stop"] is not None
        assert candidate["quantity"] == 2
        assert candidate["configuration_snapshot"]["version"] == configuration.version
        assert candidate["applied_state_id"] == capture_pair["applied_state_id"]
        assert client.get("/candidates").json()[0]["candidate_id"] == candidate["candidate_id"]


def test_telegram_approval_rechecks_and_records_would_execute_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("providency.api.analyze_context", all_context_checks_pass)
    configuration = AnalysisConfiguration(
        symbol="BTC/BRL",
        primary_timeframe="15min",
        context_timeframe="1h",
        trailing_timeframe="30min",
        short_ma_period=7,
        long_ma_period=70,
        quantity=2,
        tick_size=0.5,
        tick_value=1.0,
        max_trades=3,
        max_consecutive_losses=2,
        max_session_loss=100,
        support_resistance_tolerance=5,
    )
    configured = settings(tmp_path, configuration)
    configured = replace(
        configured,
        telegram_configuration=TelegramConfiguration(
            chat_id=10, user_id=20, approval_ttl_seconds=60
        ),
    )
    adapter = FakeVectorAdapter(tmp_path / "capture.webp")
    bearish_engulfing_chart(adapter.capture_path)
    telegram = FakeTelegramClient()
    app = create_app(
        configured,
        recover=False,
        vector_adapter=adapter,
        telegram_client=telegram,
        telegram_polling=False,
    )

    with TestClient(app) as client:
        client.post("/run")
        captured = client.post("/vector/capture-analysis").json()
        candidate = client.post(
            "/candidate/evaluate",
            json={
                "analysis_capture_id": captured["analysis_capture_id"],
                "pattern_detection_id": captured["pattern_detection_id"],
            },
        ).json()
        assert candidate["decision"] == "ALLOWED", candidate

        sent = client.post(f"/approvals/{candidate['candidate_id']}")
        assert sent.status_code == 200
        assert sent.json()["status"] == "PENDING"
        assert sent.json()["telegram"] == {"chat_id": 10, "message_id": 7}

        telegram.queue(telegram.yes_callback, chat_id=11, user_id=20)
        wrong = client.post("/telegram/poll-once")
        assert wrong.json() == []
        assert telegram.answers[-1] == "Rejected"
        telegram.queue(telegram.yes_callback, chat_id=10, user_id=20)
        approved = client.post("/telegram/poll-once")
        assert approved.status_code == 200
        assert approved.json()[0]["status"] == "WOULD_EXECUTE", approved.json()[0]["reason"]
        telegram.queue(telegram.yes_callback, chat_id=10, user_id=20)
        duplicate = client.post("/telegram/poll-once")
        assert duplicate.json() == []
        events = client.get("/events").json()
        assert [event["event_type"] for event in events].count("WOULD_EXECUTE") == 1


def test_approved_proposal_is_cancelled_when_market_capture_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("providency.api.analyze_context", all_context_checks_pass)
    configuration = AnalysisConfiguration(
        symbol="BTC/BRL",
        primary_timeframe="15min",
        context_timeframe="1h",
        trailing_timeframe="30min",
        short_ma_period=7,
        long_ma_period=70,
        quantity=2,
        tick_size=0.5,
        tick_value=1.0,
        max_trades=3,
        max_consecutive_losses=2,
        max_session_loss=100,
        support_resistance_tolerance=5,
    )
    configured = settings(tmp_path, configuration)
    configured = replace(
        configured,
        telegram_configuration=TelegramConfiguration(chat_id=10, user_id=20),
    )
    adapter = FakeVectorAdapter(tmp_path / "capture.webp")
    bearish_engulfing_chart(adapter.capture_path)
    telegram = FakeTelegramClient()
    app = create_app(
        configured,
        recover=False,
        vector_adapter=adapter,
        telegram_client=telegram,
        telegram_polling=False,
    )

    with TestClient(app) as client:
        client.post("/run")
        captured = client.post("/vector/capture-analysis").json()
        candidate = client.post(
            "/candidate/evaluate",
            json={
                "analysis_capture_id": captured["analysis_capture_id"],
                "pattern_detection_id": captured["pattern_detection_id"],
            },
        ).json()
        client.post(f"/approvals/{candidate['candidate_id']}")
        adapter.sha256 = "b" * 64

        telegram.queue(telegram.yes_callback, chat_id=10, user_id=20)
        result = client.post("/telegram/poll-once").json()[0]

        assert result["status"] == "CANCELLED"
        assert "primary_capture_sha256 changed" in result["reason"]


def test_demo_mode_executes_one_rechecked_operation_and_exposes_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("providency.api.analyze_context", all_context_checks_pass)
    configuration = AnalysisConfiguration(
        symbol="BTC/BRL",
        primary_timeframe="15min",
        context_timeframe="1h",
        trailing_timeframe="30min",
        short_ma_period=7,
        long_ma_period=70,
        quantity=2,
        tick_size=0.5,
        tick_value=1.0,
        max_trades=3,
        max_consecutive_losses=2,
        max_session_loss=100,
        support_resistance_tolerance=5,
    )
    configured = replace(
        settings(tmp_path, configuration),
        telegram_configuration=TelegramConfiguration(chat_id=10, user_id=20),
        execution_mode=ExecutionMode.DEMO,
    )
    adapter = FakeVectorAdapter(tmp_path / "capture.webp")
    bearish_engulfing_chart(adapter.capture_path)
    adapter.demo_states = [
        demo_state(),
        demo_state(),
        demo_state(),
        demo_state(position=PositionState.SHORT, order=OrderState.FILLED),
    ]
    telegram = FakeTelegramClient()
    app = create_app(
        configured,
        recover=False,
        vector_adapter=adapter,
        telegram_client=telegram,
        telegram_polling=False,
    )

    with TestClient(app) as client:
        client.post("/run")
        captured = client.post("/vector/capture-analysis").json()
        candidate = client.post(
            "/candidate/evaluate",
            json={
                "analysis_capture_id": captured["analysis_capture_id"],
                "pattern_detection_id": captured["pattern_detection_id"],
            },
        ).json()
        client.post(f"/approvals/{candidate['candidate_id']}")
        telegram.queue(telegram.yes_callback, chat_id=10, user_id=20)

        operation = client.post("/telegram/poll-once").json()[0]

        assert operation["status"] == "FILLED"
        assert operation["side"] == "SELL"
        assert client.get("/execution/status").json()["mode"] == "DEMO"
        assert client.get("/operations").json()[0]["operation_id"] == operation["operation_id"]
        assert client.get("/protections").json()[0]["status"] == "PROTECTED"
        assert client.get("/execution/status").json()["blocking_protection"] is False
        assert adapter.demo_submit_calls == 1
        assert "WOULD_EXECUTE" not in [
            event["event_type"] for event in client.get("/events").json()
        ]
        refused = client.post(
            f"/operations/{operation['operation_id']}/emergency-stop",
            json={"confirm_demo_close": False},
        )
        assert refused.status_code == 400
        emergency = client.post(
            f"/operations/{operation['operation_id']}/emergency-stop",
            json={"confirm_demo_close": True},
        )
        assert emergency.json()["status"] == "CONFIRMED"


def test_daily_review_report_metrics_and_sanitized_evidence_api(tmp_path: Path) -> None:
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir()
    capture_path = bearish_engulfing_chart(capture_dir / "capture.png")
    adapter = FakeVectorAdapter(capture_path)
    adapter.sha256 = file_sha256(capture_path)
    app = create_app(settings(tmp_path), recover=False, vector_adapter=adapter)

    with TestClient(app) as client:
        session = client.post("/run").json()
        analyzed = client.post("/vector/capture-analysis").json()
        detection_id = analyzed["pattern_detection_id"]
        sanitized = client.post(
            "/evidence/sanitize",
            json={"detection_id": detection_id, "redactions": []},
        ).json()
        review = client.post(
            "/reviews",
            json={
                "detection_id": detection_id,
                "label": "TRUE_POSITIVE",
                "notes": "Closed candle and geometry checked locally.",
                "reviewer_id": "local:operator",
                "evidence_sha256": sanitized["source_sha256"],
                "evidence_path": sanitized["path"],
            },
        )

        assert review.status_code == 200
        assert review.json()["revision"] == 1
        metrics = client.get("/metrics/patterns/bearish_engulfing").json()
        assert metrics["precision"]["denominator"] == 1
        report = client.get(f"/sessions/{session['id']}/report").json()
        assert report["funnel"]["detections"] == 1
        exported = client.post(f"/sessions/{session['id']}/report/export").json()
        assert Path(exported["path"]).is_file()
