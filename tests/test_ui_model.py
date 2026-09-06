from providency.ui_model import (
    NAVIGATION,
    desired_applied_rows,
    human_datetime,
    human_event_message,
    operational_stage,
)


def test_navigation_follows_operator_information_architecture() -> None:
    assert NAVIGATION == (
        "Parâmetros e Configurações",
        "Gerenciamento e Operação",
        "Atividades e Resultados",
    )


def test_human_datetime_uses_local_readable_format() -> None:
    assert human_datetime("2026-09-05T18:51:00+00:00").startswith("05/09/2026 ·")


def test_common_engine_events_are_presented_in_operator_language() -> None:
    assert human_event_message("Operational session started.") == "Sessão operacional iniciada."


def test_desired_applied_rows_make_pending_values_explicit() -> None:
    desired = {"symbol": "WIN", "primary_timeframe": "15m", "short_ma_period": 7}
    applied = {"state": {"symbol": "WIN", "timeframe": "5m", "moving_averages": {"7": 1}}}
    rows = desired_applied_rows(desired, applied)
    assert rows[0] == {
        "Parâmetro": "Ativo",
        "Desejado": "WIN",
        "Aplicado": "WIN",
        "Estado": "Confirmado",
    }
    assert rows[1]["Estado"] == "Pendente"


def test_operational_stage_stops_at_first_unmet_gate() -> None:
    assert (
        operational_stage(
            running=False,
            missing_configuration=True,
            vector_state="CLOSED",
            candidates=[],
            approvals=[],
            operations=[],
            protections=[],
        )
        == 0
    )
    assert (
        operational_stage(
            running=True,
            missing_configuration=False,
            vector_state="OPEN",
            candidates=[{"decision": "ALLOWED"}],
            approvals=[{"status": "APPROVED"}],
            operations=[{"status": "FILLED"}],
            protections=[{"status": "PROTECTED"}],
        )
        == 9
    )
