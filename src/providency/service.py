from __future__ import annotations

from typing import Any

from providency.storage import Storage


class EngineService:
    def __init__(self, storage: Storage, *, recover: bool = True) -> None:
        self.storage = storage
        self.storage.initialize()
        if recover:
            self.storage.recover_interrupted_session()

    def snapshot(self) -> dict[str, Any]:
        session = self.storage.running_session()
        return {
            "engine_state": "RUNNING" if session else "STOPPED",
            "session": session,
        }

    def run(self) -> dict[str, Any]:
        return self.storage.start_session()

    def stop(self) -> dict[str, Any] | None:
        return self.storage.stop_session()
