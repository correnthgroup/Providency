from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from platformdirs import user_data_path


@dataclass(frozen=True, slots=True)
class TelegramConfiguration:
    chat_id: int = 0
    user_id: int = 0
    approval_ttl_seconds: int = 60
    recheck_price_tolerance_ticks: int = 1

    @property
    def missing_fields(self) -> tuple[str, ...]:
        missing: list[str] = []
        if self.chat_id == 0:
            missing.append("chat_id")
        if self.user_id <= 0:
            missing.append("user_id")
        if self.approval_ttl_seconds <= 0:
            missing.append("approval_ttl_seconds")
        if self.recheck_price_tolerance_ticks < 0:
            missing.append("recheck_price_tolerance_ticks")
        return tuple(missing)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "missing_fields": self.missing_fields, "token": "MASKED"}

    @classmethod
    def from_env(cls) -> TelegramConfiguration:
        return cls(
            chat_id=int(os.getenv("PROVIDENCY_TELEGRAM_CHAT_ID", "0")),
            user_id=int(os.getenv("PROVIDENCY_TELEGRAM_USER_ID", "0")),
            approval_ttl_seconds=int(os.getenv("PROVIDENCY_APPROVAL_TTL_SECONDS", "60")),
            recheck_price_tolerance_ticks=int(
                os.getenv("PROVIDENCY_RECHECK_PRICE_TOLERANCE_TICKS", "1")
            ),
        )


@dataclass(frozen=True, slots=True)
class AnalysisConfiguration:
    schema_version: int = 1
    symbol: str = ""
    primary_timeframe: str = ""
    context_timeframe: str = ""
    short_ma_period: int | None = None
    long_ma_period: int | None = None
    quantity: int = 0
    tick_size: float = 0.0
    tick_value: float = 0.0
    stop_buffer_ticks: int = 1
    min_rr: float = 2.0
    max_trades: int = 0
    max_consecutive_losses: int = 0
    max_session_loss: float = 0.0
    pivot_window: int = 3
    support_resistance_tolerance: float = 0.0

    @property
    def version(self) -> str:
        canonical = json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    @property
    def moving_average_periods(self) -> tuple[int, ...]:
        return tuple(
            value for value in (self.short_ma_period, self.long_ma_period) if value is not None
        )

    @property
    def missing_fields(self) -> tuple[str, ...]:
        required_text: dict[str, str] = {
            "symbol": self.symbol,
            "primary_timeframe": self.primary_timeframe,
            "context_timeframe": self.context_timeframe,
        }
        required_positive: dict[str, int | float] = {
            "quantity": self.quantity,
            "tick_size": self.tick_size,
            "tick_value": self.tick_value,
            "max_trades": self.max_trades,
            "max_consecutive_losses": self.max_consecutive_losses,
            "max_session_loss": self.max_session_loss,
            "pivot_window": self.pivot_window,
            "support_resistance_tolerance": self.support_resistance_tolerance,
        }
        return tuple(
            [name for name, value in required_text.items() if not value]
            + [name for name, value in required_positive.items() if value <= 0]
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "version": self.version, "missing_fields": self.missing_fields}

    @classmethod
    def from_env(cls) -> AnalysisConfiguration:
        def optional_int(name: str) -> int | None:
            value = os.getenv(name, "").strip()
            return int(value) if value else None

        return cls(
            symbol=os.getenv("PROVIDENCY_SYMBOL", "").strip(),
            primary_timeframe=os.getenv("PROVIDENCY_PRIMARY_TIMEFRAME", "").strip(),
            context_timeframe=os.getenv("PROVIDENCY_CONTEXT_TIMEFRAME", "").strip(),
            short_ma_period=optional_int("PROVIDENCY_SHORT_MA_PERIOD"),
            long_ma_period=optional_int("PROVIDENCY_LONG_MA_PERIOD"),
            quantity=int(os.getenv("PROVIDENCY_QUANTITY", "0")),
            tick_size=float(os.getenv("PROVIDENCY_TICK_SIZE", "0")),
            tick_value=float(os.getenv("PROVIDENCY_TICK_VALUE", "0")),
            stop_buffer_ticks=int(os.getenv("PROVIDENCY_STOP_BUFFER_TICKS", "1")),
            min_rr=float(os.getenv("PROVIDENCY_MIN_RR", "2")),
            max_trades=int(os.getenv("PROVIDENCY_MAX_TRADES", "0")),
            max_consecutive_losses=int(os.getenv("PROVIDENCY_MAX_CONSECUTIVE_LOSSES", "0")),
            max_session_loss=float(os.getenv("PROVIDENCY_MAX_SESSION_LOSS", "0")),
            pivot_window=int(os.getenv("PROVIDENCY_PIVOT_WINDOW", "3")),
            support_resistance_tolerance=float(
                os.getenv("PROVIDENCY_SUPPORT_RESISTANCE_TOLERANCE", "0")
            ),
        )


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    api_host: str = "127.0.0.1"
    api_port: int = 8765
    ui_port: int = 8501
    vector_url: str = ""
    patterns_dir: Path | None = None
    analysis_configuration: AnalysisConfiguration | None = None
    telegram_configuration: TelegramConfiguration | None = None

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

    @property
    def trading_configuration(self) -> AnalysisConfiguration:
        return self.analysis_configuration or AnalysisConfiguration.from_env()

    @property
    def telegram(self) -> TelegramConfiguration:
        return self.telegram_configuration or TelegramConfiguration.from_env()

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
