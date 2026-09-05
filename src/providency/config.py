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
    vector_url: str = ""
    patterns_dir: Path | None = None

    @property
    def database_path(self) -> Path:
        return self.data_dir / "providency.db"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / "providency.lock"

    @property
    def vector_profile_dir(self) -> Path:
        return self.data_dir / "vector-profile"

    @property
    def capture_dir(self) -> Path:
        return self.data_dir / "captures"

    @property
    def api_url(self) -> str:
        return f"http://{self.api_host}:{self.api_port}"

    @property
    def pattern_catalog_dir(self) -> Path:
        if self.patterns_dir is not None:
            return self.patterns_dir.resolve()
        workspace_catalog = Path.cwd() / "patterns"
        if workspace_catalog.is_dir():
            return workspace_catalog.resolve()
        return (Path(__file__).parent / "pattern_catalog").resolve()

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
            vector_url=os.getenv("PROVIDENCY_VECTOR_URL", "").strip(),
            patterns_dir=(
                Path(configured_patterns).expanduser()
                if (configured_patterns := os.getenv("PROVIDENCY_PATTERNS_DIR", "").strip())
                else None
            ),
        )
