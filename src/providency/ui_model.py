from __future__ import annotations

from datetime import datetime
from typing import Any

NAVIGATION = (
    "Parâmetros e Configurações",
    "Gerenciamento e Operação",
    "Atividades e Resultados",
)

OPERATION_STAGES = (
    "Configuração",
    "Vector",
    "Observação",
    "Padrão",
    "Confluência",
    "Risco",
    "Aprovação",
    "Execução",
    "Proteção",
    "Resultado",
)

FIELD_LABELS = {
    "symbol": "Ativo",
    "primary_timeframe": "Timeframe principal",
    "context_timeframe": "Timeframe de contexto",
    "trailing_timeframe": "Timeframe de trailing",
    "short_ma_period": "Média curta",
    "long_ma_period": "Média longa",
    "quantity": "Quantidade",
    "tick_size": "Tamanho do tick",
    "tick_value": "Valor do tick",
    "stop_buffer_ticks": "Buffer do stop (ticks)",
    "min_rr": "Risco/retorno mínimo",
    "max_trades": "Máximo de operações",
    "max_consecutive_losses": "Máximo de perdas consecutivas",
    "max_session_loss": "Perda máxima da sessão",
    "pivot_window": "Janela de pivô",
    "support_resistance_tolerance": "Tolerância de suporte/resistência",
}

EVENT_TRANSLATIONS = {
    "Operational session started.": "Sessão operacional iniciada.",
    "Operational session stopped.": "Sessão operacional encerrada com segurança.",
    "Vector chart capture is usable.": "A captura do gráfico da Vector está utilizável.",
}


def human_datetime(value: str | None) -> str:
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed.astimezone().strftime("%d/%m/%Y · %H:%M")


def human_event_message(message: str) -> str:
    if message in EVENT_TRANSLATIONS:
        return EVENT_TRANSLATIONS[message]
    if message.startswith("Vector chart capture was blocked:"):
        reason = message.removeprefix("Vector chart capture was blocked:").strip(" .")
        return f"A captura da Vector foi bloqueada por segurança: {reason}."
    return message


def desired_applied_rows(
    desired: dict[str, Any], applied_payload: dict[str, Any] | None
) -> list[dict[str, Any]]:
    applied = (applied_payload or {}).get("state") or {}
    moving_averages = applied.get("moving_averages") or {}
    applied_values = {
        "symbol": applied.get("symbol"),
        "primary_timeframe": applied.get("timeframe"),
        "short_ma_period": _applied_ma(desired.get("short_ma_period"), moving_averages),
        "long_ma_period": _applied_ma(desired.get("long_ma_period"), moving_averages),
    }
    rows: list[dict[str, Any]] = []
    for key, label in FIELD_LABELS.items():
        expected = desired.get(key)
        actual = applied_values.get(key)
        rows.append(
            {
                "Parâmetro": label,
                "Desejado": _display(expected),
                "Aplicado": _display(actual),
                "Estado": "Confirmado"
                if actual is not None and str(actual) == str(expected)
                else "Pendente",
            }
        )
    return rows


def operational_stage(
    *,
    running: bool,
    missing_configuration: bool,
    vector_state: str,
    candidates: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
    operations: list[dict[str, Any]],
    protections: list[dict[str, Any]],
) -> int:
    if missing_configuration:
        return 0
    if vector_state != "OPEN":
        return 1
    if not running:
        return 2
    if not candidates:
        return 3
    if candidates[0].get("decision") != "ALLOWED":
        return 5
    if not approvals:
        return 6
    if approvals[0].get("status") == "PENDING":
        return 6
    if not operations:
        return 7
    if not protections:
        return 8
    return 9


def _applied_ma(period: Any, values: dict[Any, Any]) -> Any:
    if period is None:
        return None
    return period if str(period) in {str(item) for item in values} else None


def _display(value: Any) -> str:
    return "—" if value is None or value == "" else str(value)
