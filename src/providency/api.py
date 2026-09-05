from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from providency.config import Settings
from providency.service import EngineService
from providency.storage import Storage
from providency.vector import VectorAdapter, VectorAdapterContract, VectorAdapterError


class SessionPayload(BaseModel):
    id: str
    started_at: str
    ended_at: str | None
    status: Literal["RUNNING", "STOPPED", "INTERRUPTED"]


class StatePayload(BaseModel):
    engine_state: Literal["STOPPED", "RUNNING"]
    session: SessionPayload | None


class EventPayload(BaseModel):
    id: str
    session_id: str | None
    created_at: str
    level: str
    component: str
    event_type: str
    message: str
    details: dict[str, Any]


def create_app(
    settings: Settings | None = None,
    *,
    recover: bool = True,
    vector_adapter: VectorAdapterContract | None = None,
) -> FastAPI:
    resolved = settings or Settings.from_env()
    service = EngineService(Storage(resolved.database_path), recover=recover)
    adapter = vector_adapter or VectorAdapter(resolved)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await adapter.stop()

    app = FastAPI(title="Providency Core Engine", version="0.2.0", lifespan=lifespan)

    def vector_error(exc: VectorAdapterError) -> HTTPException:
        session = service.storage.running_session()
        return HTTPException(
            status_code=409,
            detail={
                "error_code": exc.code,
                "short_title": "Vector Web is not ready",
                "human_message": str(exc),
                "impact": "No market decision was produced.",
                "suggested_actions": [
                    "Open Vector Web from Providency.",
                    "Complete login manually and keep the chart visible.",
                    "Try the capture again.",
                ],
                "technical_details": {"adapter_error": exc.code},
                "component": "vector",
                "session_id": str(session["id"]) if session else None,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/state", response_model=StatePayload)
    def state() -> dict[str, Any]:
        return service.snapshot()

    @app.post("/run", response_model=SessionPayload)
    def run() -> dict[str, Any]:
        return service.run()

    @app.post("/stop", response_model=SessionPayload | None)
    def stop() -> dict[str, Any] | None:
        return service.stop()

    @app.get("/events", response_model=list[EventPayload])
    def events(limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
        return service.storage.list_events(limit)

    @app.get("/vector/health")
    async def vector_health() -> dict[str, str]:
        return await adapter.health_check()

    @app.post("/vector/open")
    async def vector_open() -> dict[str, str]:
        try:
            return await adapter.open_vector()
        except VectorAdapterError as exc:
            raise vector_error(exc) from exc

    @app.post("/vector/capture")
    async def vector_capture() -> dict[str, Any]:
        try:
            capture = await adapter.capture_primary_chart()
        except VectorAdapterError as exc:
            raise vector_error(exc) from exc
        payload = capture.to_dict()
        session = service.storage.running_session()
        usable = payload["disposition"] == "USABLE"
        service.storage.record_event(
            session_id=str(session["id"]) if session else None,
            level="INFO" if usable else "WARNING",
            component="vector",
            event_type="VECTOR_CAPTURE_USABLE" if usable else "VECTOR_CAPTURE_BLOCKED",
            message=(
                "Vector chart capture is usable."
                if usable
                else f"Vector chart capture was blocked: {payload['issue']}."
            ),
            details=payload,
        )
        return payload

    return app
