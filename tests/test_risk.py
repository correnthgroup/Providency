from providency.context import ConfluenceItem, ConfluenceResult, ConfluenceStatus
from providency.risk import CandidateDecision, RiskPolicy, SessionLimits, build_candidate


def passing_confluence() -> ConfluenceResult:
    return ConfluenceResult(
        items={
            "trend": ConfluenceItem(ConfluenceStatus.PASS, "Bearish alignment."),
            "structure": ConfluenceItem(ConfluenceStatus.PASS, "Lower structure."),
        }
    )


def test_candidate_freezes_prices_risk_limits_and_provenance() -> None:
    candidate = build_candidate(
        session_id="session-1",
        symbol="BTC/BRL",
        side="SHORT",
        pattern_id="bearish_engulfing",
        pattern_version="0.1.0",
        configuration_version="1.0.0",
        primary_capture_sha256="a" * 64,
        context_capture_sha256="b" * 64,
        pattern_high=101.0,
        pattern_low=99.0,
        quantity=2,
        confluence=passing_confluence(),
        policy=RiskPolicy(tick_size=0.5, tick_value=1.0, stop_buffer_ticks=1, min_rr=2),
        limits=SessionLimits(trades=0, consecutive_losses=0, loss=0),
    )

    assert candidate.decision is CandidateDecision.ALLOWED
    assert candidate.entry == 98.5
    assert candidate.stop == 101.5
    assert candidate.risk_amount == 12.0
    assert candidate.reference_target == 92.5
    assert candidate.candidate_id
    assert candidate.to_dict()["limits"]["trades"] == 0


def test_missing_session_limit_blocks_candidate() -> None:
    candidate = build_candidate(
        session_id="session-1",
        symbol="BTC/BRL",
        side="SHORT",
        pattern_id="bearish_engulfing",
        pattern_version="0.1.0",
        configuration_version="1.0.0",
        primary_capture_sha256="a" * 64,
        context_capture_sha256="b" * 64,
        pattern_high=101,
        pattern_low=99,
        quantity=1,
        confluence=passing_confluence(),
        policy=RiskPolicy(tick_size=0.5, tick_value=1, stop_buffer_ticks=1, min_rr=2),
        limits=SessionLimits(trades=None, consecutive_losses=0, loss=0),
    )

    assert candidate.decision is CandidateDecision.BLOCKED
    assert any("trades" in reason for reason in candidate.blocking_reasons)


def test_limit_boundary_blocks_candidate() -> None:
    candidate = build_candidate(
        session_id="session-1",
        symbol="BTC/BRL",
        side="SHORT",
        pattern_id="bearish_engulfing",
        pattern_version="0.1.0",
        configuration_version="1.0.0",
        primary_capture_sha256="a" * 64,
        context_capture_sha256="b" * 64,
        pattern_high=101,
        pattern_low=99,
        quantity=1,
        confluence=passing_confluence(),
        policy=RiskPolicy(
            tick_size=0.5,
            tick_value=1,
            stop_buffer_ticks=1,
            min_rr=2,
            max_trades=3,
        ),
        limits=SessionLimits(trades=3, consecutive_losses=0, loss=0),
    )

    assert candidate.decision is CandidateDecision.BLOCKED
    assert "Maximum trades reached." in candidate.blocking_reasons
