from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    api_host: str = "127.0.0.1"
    api_port: int = 8765
    ui_port: int = 8501

    @property
    def database_path(self) -> Path:
        return self.data_dir / "providency.db"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / "providency.lock"

    @property
    def api_url(self) -> str:
        return f"http://{self.api_host}:{self.api_port}"

    @classmethod
    def from_env(cls) -> Settings:
        configured_dir = os.getenv("PROVIDENCY_DATA_DIR", "").strip()
        data_dir = (
            Path(configured_dir).expanduser()
            if configured_dir
            else Path(user_data_path("Providency", "Correnth"))
        )
        return cls(
            data_dir=data_dir.resolve(),
            api_host=os.getenv("PROVIDENCY_API_HOST", "127.0.0.1"),
            api_port=int(os.getenv("PROVIDENCY_API_PORT", "8765")),
            ui_port=int(os.getenv("PROVIDENCY_UI_PORT", "8501")),
        )
