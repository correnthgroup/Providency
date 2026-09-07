from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

CHECKPOINTS = {
    "STARTING_UI": (0.15, "Abrindo a interface do Providency…"),
    "UI_READY": (0.35, "Interface pronta. Preparando o motor local…"),
    "STARTING_ENGINE": (0.65, "Carregando configurações, histórico e reconhecimento…"),
    "READY": (1.0, "Providency pronto para começar."),
}


class StartupProgress:
    """Launcher-owned checkpoints; the UI reads them before the API exists."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "startup-progress.json"
        self.cancel_path = data_dir / "startup-cancel.json"

    @staticmethod
    def _write(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{uuid4().hex}.tmp")
        temporary.write_text(json.dumps(value), encoding="utf-8")
        temporary.replace(path)

    def read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return dict(value) if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def begin(self, launch_id: str) -> None:
        self._write(self.path, {"launch_id": launch_id})
        self.advance("STARTING_UI")

    def advance(self, stage: str) -> None:
        progress, message = CHECKPOINTS[stage]
        self._write(
            self.path,
            {
                **self.read(),
                "stage": stage,
                "progress": progress,
                "message": message,
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )

    def fail(self, message: str) -> None:
        self._write(self.path, {**self.read(), "stage": "ERROR", "message": message})

    def cancel(self, launch_id: str) -> None:
        self._write(self.cancel_path, {"launch_id": launch_id})

    def cancelled(self) -> bool:
        try:
            request = json.loads(self.cancel_path.read_text(encoding="utf-8"))
            return bool(request.get("launch_id") == self.read().get("launch_id"))
        except (OSError, ValueError, AttributeError):
            return False
