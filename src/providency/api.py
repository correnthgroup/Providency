from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, Query
from pydantic import BaseModel

from providency.config import Settings
from providency.service import EngineService
from providency.storage import Storage


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


def create_app(settings: Settings | None = None, *, recover: bool = True) -> FastAPI:
    resolved = settings or Settings.from_env()
    service = EngineService(Storage(resolved.database_path), recover=recover)
    app = FastAPI(title="Providency Core Engine", version="0.1.0")

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

    return app
