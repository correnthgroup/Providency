# ruff: noqa: E501
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Storage:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS telegram_configuration (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    configuration_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'STOPPED', 'INTERRUPTED'))
                );

                CREATE UNIQUE INDEX IF NOT EXISTS one_running_session
                    ON sessions(status) WHERE status = 'RUNNING';

                CREATE TABLE IF NOT EXISTS session_risk_state (
                    session_id TEXT PRIMARY KEY,
                    trades INTEGER NOT NULL DEFAULT 0 CHECK (trades >= 0),
                    consecutive_losses INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_losses >= 0),
                    loss REAL NOT NULL DEFAULT 0 CHECK (loss >= 0),
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );

                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    created_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    component TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );

                CREATE INDEX IF NOT EXISTS events_created_at ON events(created_at DESC);

                CREATE TABLE IF NOT EXISTS pattern_detections (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    created_at TEXT NOT NULL,
                    screenshot_sha256 TEXT NOT NULL,
                    screenshot_path TEXT NOT NULL,
                    pattern_id TEXT NOT NULL,
                    pattern_version TEXT NOT NULL,
                    result TEXT NOT NULL CHECK (result IN ('MATCH', 'FORMING', 'NO_MATCH')),
                    reason TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );

                CREATE INDEX IF NOT EXISTS pattern_detections_created_at
                    ON pattern_detections(created_at DESC);

                CREATE TABLE IF NOT EXISTS configurations (
                    version TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    desired_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS applied_states (
                    id TEXT PRIMARY KEY,
                    configuration_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    matches_desired INTEGER NOT NULL CHECK (matches_desired IN (0, 1)),
                    state_json TEXT NOT NULL,
                    FOREIGN KEY (configuration_version) REFERENCES configurations(version)
                );

                CREATE INDEX IF NOT EXISTS applied_states_created_at
                    ON applied_states(created_at DESC);

                CREATE TABLE IF NOT EXISTS analysis_captures (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    created_at TEXT NOT NULL,
                    applied_state_id TEXT NOT NULL,
                    primary_json TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id),
                    FOREIGN KEY (applied_state_id) REFERENCES applied_states(id)
                );

                CREATE TABLE IF NOT EXISTS trade_candidates (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    decision TEXT NOT NULL CHECK (decision IN ('ALLOWED', 'BLOCKED')),
                    candidate_json TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );

                CREATE INDEX IF NOT EXISTS trade_candidates_created_at
                    ON trade_candidates(created_at DESC);

                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    decided_at TEXT,
                    status TEXT NOT NULL CHECK (status IN (
                        'PENDING', 'APPROVED', 'REJECTED', 'EXPIRED', 'CANCELLED',
                        'WOULD_EXECUTE'
                    )),
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    callback_digest TEXT NOT NULL UNIQUE,
                    candidate_json TEXT NOT NULL,
                    telegram_json TEXT,
                    recheck_json TEXT,
                    reason TEXT,
                    FOREIGN KEY (candidate_id) REFERENCES trade_candidates(id),
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );

                CREATE INDEX IF NOT EXISTS approvals_created_at
                    ON approvals(created_at DESC);

                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY,
                    approval_id TEXT NOT NULL UNIQUE,
                    candidate_id TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN (
                        'CREATED', 'PRECHECKED', 'SUBMITTED_UNCONFIRMED', 'PENDING',
                        'PARTIAL', 'FILLED', 'REJECTED', 'CANCELLED', 'AMBIGUOUS', 'BLOCKED'
                    )),
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    configuration_version TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    precheck_json TEXT,
                    submission_json TEXT,
                    reconciliation_json TEXT,
                    reason TEXT,
                    FOREIGN KEY (approval_id) REFERENCES approvals(id),
                    FOREIGN KEY (candidate_id) REFERENCES trade_candidates(id),
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );

                CREATE INDEX IF NOT EXISTS operations_created_at ON operations(created_at DESC);

                CREATE TABLE IF NOT EXISTS operation_observations (
                    id TEXT PRIMARY KEY,
                    operation_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    observation_json TEXT NOT NULL,
                    FOREIGN KEY (operation_id) REFERENCES operations(id)
                );

                CREATE TABLE IF NOT EXISTS protection_policies (
                    id TEXT PRIMARY KEY,
                    operation_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN (
                        'UNPROTECTED', 'APPLYING_INITIAL', 'PROTECTED', 'BREAKEVEN',
                        'TRAILING', 'SAFE_STOP', 'EMERGENCY_PENDING',
                        'EMERGENCY_UNCONFIRMED', 'CLOSED'
                    )),
                    policy_json TEXT NOT NULL,
                    observation_json TEXT,
                    action_json TEXT,
                    reason TEXT,
                    FOREIGN KEY (operation_id) REFERENCES operations(id)
                );

                CREATE INDEX IF NOT EXISTS protection_policies_updated_at
                    ON protection_policies(updated_at DESC);

                CREATE TABLE IF NOT EXISTS emergency_operations (
                    id TEXT PRIMARY KEY,
                    protection_policy_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN (
                        'CREATED', 'CANCEL_UNCONFIRMED', 'CLOSE_UNCONFIRMED',
                        'CONFIRMED', 'AMBIGUOUS'
                    )),
                    snapshot_json TEXT NOT NULL,
                    result_json TEXT,
                    reason TEXT,
                    FOREIGN KEY (protection_policy_id) REFERENCES protection_policies(id)
                );

                CREATE TABLE IF NOT EXISTS human_reviews (
                    id TEXT PRIMARY KEY,
                    review_key TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK (revision > 0),
                    previous_review_id TEXT,
                    session_id TEXT,
                    detection_id TEXT,
                    candidate_id TEXT,
                    created_at TEXT NOT NULL,
                    label TEXT NOT NULL CHECK (label IN (
                        'TRUE_POSITIVE', 'FALSE_POSITIVE', 'FALSE_NEGATIVE',
                        'TRUE_NEGATIVE', 'NO_DECISION', 'OPERATIONAL_FAILURE'
                    )),
                    notes TEXT NOT NULL,
                    reviewer_id TEXT NOT NULL,
                    evidence_sha256 TEXT NOT NULL,
                    evidence_path TEXT NOT NULL,
                    detector_version TEXT NOT NULL,
                    pattern_id TEXT NOT NULL,
                    pattern_version TEXT NOT NULL,
                    UNIQUE(review_key, revision),
                    CHECK ((detection_id IS NULL) != (candidate_id IS NULL)),
                    FOREIGN KEY (previous_review_id) REFERENCES human_reviews(id),
                    FOREIGN KEY (session_id) REFERENCES sessions(id),
                    FOREIGN KEY (detection_id) REFERENCES pattern_detections(id),
                    FOREIGN KEY (candidate_id) REFERENCES trade_candidates(id)
                );

                CREATE INDEX IF NOT EXISTS human_reviews_created_at
                    ON human_reviews(created_at DESC);
                CREATE INDEX IF NOT EXISTS human_reviews_pattern
                    ON human_reviews(pattern_id, pattern_version, created_at DESC);

                CREATE TABLE IF NOT EXISTS observation_configuration (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    updated_at TEXT NOT NULL,
                    configuration_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS observation_sessions (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'STOPPED', 'ERROR', 'INTERRUPTED')),
                    configuration_json TEXT NOT NULL,
                    error TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_running_observation_session
                    ON observation_sessions(status) WHERE status = 'RUNNING';

                CREATE TABLE IF NOT EXISTS observation_cycles (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL CHECK (status IN ('COMPLETE', 'PARTIAL', 'FAILED', 'CANCELLED')),
                    summary TEXT NOT NULL,
                    chart_count INTEGER NOT NULL CHECK (chart_count >= 0),
                    FOREIGN KEY (session_id) REFERENCES observation_sessions(id)
                );
                CREATE INDEX IF NOT EXISTS observation_cycles_started_at
                    ON observation_cycles(started_at DESC);

                CREATE TABLE IF NOT EXISTS observation_results (
                    id TEXT PRIMARY KEY,
                    cycle_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    FOREIGN KEY (cycle_id) REFERENCES observation_cycles(id)
                );

                CREATE TABLE IF NOT EXISTS observation_outbox (
                    id TEXT PRIMARY KEY,
                    cycle_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('PENDING', 'SENDING', 'SENT', 'FAILED', 'UNKNOWN_DELIVERY')),
                    text TEXT NOT NULL,
                    response_json TEXT,
                    error TEXT,
                    UNIQUE(cycle_id)
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_meta(version, applied_at) VALUES (1, ?)",
                (utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_meta(version, applied_at) VALUES (2, ?)",
                (utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_meta(version, applied_at) VALUES (3, ?)",
                (utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_meta(version, applied_at) VALUES (4, ?)",
                (utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_meta(version, applied_at) VALUES (5, ?)",
                (utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_meta(version, applied_at) VALUES (6, ?)",
                (utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_meta(version, applied_at) VALUES (7, ?)",
                (utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_meta(version, applied_at) VALUES (8, ?)",
                (utc_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO session_risk_state(session_id) SELECT id FROM sessions"
            )
            connection.commit()

    def recover_interrupted_session(self) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id FROM sessions WHERE status = 'RUNNING' LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            session_id = str(row["id"])
            ended_at = utc_now()
            connection.execute(
                "UPDATE sessions SET status = 'INTERRUPTED', ended_at = ? WHERE id = ?",
                (ended_at, session_id),
            )
            connection.execute(
                "UPDATE approvals SET status = 'CANCELLED', decided_at = ?, "
                "reason = 'Session interrupted.' WHERE session_id = ? "
                "AND status IN ('PENDING', 'APPROVED')",
                (ended_at, session_id),
            )
            self._insert_event(
                connection,
                session_id=session_id,
                level="WARNING",
                component="engine",
                event_type="SESSION_RECOVERED",
                message="Previous running session was marked as interrupted during startup.",
            )
            connection.commit()
            return session_id

    def running_session(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, started_at, ended_at, status FROM sessions "
                "WHERE status = 'RUNNING' LIMIT 1"
            ).fetchone()
            return dict(row) if row is not None else None

    def start_session(self) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT id, started_at, ended_at, status FROM sessions "
                "WHERE status = 'RUNNING' LIMIT 1"
            ).fetchone()
            if existing is not None:
                connection.commit()
                return dict(existing)

            session = {
                "id": str(uuid4()),
                "started_at": utc_now(),
                "ended_at": None,
                "status": "RUNNING",
            }
            connection.execute(
                "INSERT INTO sessions(id, started_at, ended_at, status) VALUES (?, ?, ?, ?)",
                tuple(session.values()),
            )
            connection.execute(
                "INSERT INTO session_risk_state(session_id) VALUES (?)", (session["id"],)
            )
            self._insert_event(
                connection,
                session_id=session["id"],
                level="INFO",
                component="engine",
                event_type="SESSION_STARTED",
                message="Operational session started.",
            )
            connection.commit()
            return session

    def stop_session(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id, started_at FROM sessions WHERE status = 'RUNNING' LIMIT 1"
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            ended_at = utc_now()
            connection.execute(
                "UPDATE sessions SET status = 'STOPPED', ended_at = ? WHERE id = ?",
                (ended_at, row["id"]),
            )
            connection.execute(
                "UPDATE approvals SET status = 'CANCELLED', decided_at = ?, "
                "reason = 'Session stopped.' WHERE session_id = ? "
                "AND status IN ('PENDING', 'APPROVED')",
                (ended_at, row["id"]),
            )
            self._insert_event(
                connection,
                session_id=str(row["id"]),
                level="INFO",
                component="engine",
                event_type="SESSION_STOPPED",
                message="Operational session stopped.",
            )
            connection.commit()
            return {
                "id": str(row["id"]),
                "started_at": str(row["started_at"]),
                "ended_at": ended_at,
                "status": "STOPPED",
            }

    def list_events(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 500)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, session_id, created_at, level, component, event_type, "
                "message, details_json FROM events ORDER BY created_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            event = dict(row)
            event["details"] = json.loads(event.pop("details_json"))
            events.append(event)
        return events

    def record_event(
        self,
        *,
        session_id: str | None,
        level: str,
        component: str,
        event_type: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        with self.connect() as connection:
            self._insert_event(
                connection,
                session_id=session_id,
                level=level,
                component=component,
                event_type=event_type,
                message=message,
                details=details,
            )
            connection.commit()

    def record_pattern_detection(
        self,
        *,
        session_id: str | None,
        screenshot_sha256: str,
        screenshot_path: str,
        pattern_id: str,
        pattern_version: str,
        result: str,
        reason: str,
        evidence: Mapping[str, Any],
    ) -> str:
        detection_id = str(uuid4())
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO pattern_detections("
                "id, session_id, created_at, screenshot_sha256, screenshot_path, "
                "pattern_id, pattern_version, result, reason, evidence_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    detection_id,
                    session_id,
                    utc_now(),
                    screenshot_sha256,
                    screenshot_path,
                    pattern_id,
                    pattern_version,
                    result,
                    reason,
                    json.dumps(dict(evidence), separators=(",", ":"), sort_keys=True),
                ),
            )
            connection.commit()
        return detection_id

    def list_pattern_detections(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 500)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, session_id, created_at, screenshot_sha256, screenshot_path, "
                "pattern_id, pattern_version, result, reason, evidence_json "
                "FROM pattern_detections ORDER BY created_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        detections: list[dict[str, Any]] = []
        for row in rows:
            detection = dict(row)
            detection["evidence"] = json.loads(detection.pop("evidence_json"))
            detections.append(detection)
        return detections

    def get_pattern_detection(self, detection_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, session_id, created_at, screenshot_sha256, screenshot_path, "
                "pattern_id, pattern_version, result, reason, evidence_json "
                "FROM pattern_detections WHERE id = ?",
                (detection_id,),
            ).fetchone()
        if row is None:
            return None
        detection = dict(row)
        detection["evidence"] = json.loads(detection.pop("evidence_json"))
        return detection

    def record_configuration(self, version: str, desired: Mapping[str, Any]) -> None:
        payload = json.dumps(dict(desired), separators=(",", ":"), sort_keys=True)
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO configurations(version, created_at, desired_json) "
                "VALUES (?, ?, ?)",
                (version, utc_now(), payload),
            )
            existing = connection.execute(
                "SELECT desired_json FROM configurations WHERE version = ?", (version,)
            ).fetchone()
            if existing is None or str(existing["desired_json"]) != payload:
                raise ValueError("Configuration version is immutable.")
            connection.commit()

    def latest_configuration(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT version, created_at, desired_json FROM configurations "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["desired"] = json.loads(result.pop("desired_json"))
        return result

    def telegram_configuration(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT configuration_json FROM telegram_configuration WHERE id = 1"
            ).fetchone()
        return dict(json.loads(row[0])) if row else None

    def save_telegram_configuration(self, configuration: Mapping[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO telegram_configuration VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET configuration_json = excluded.configuration_json",
                (json.dumps(dict(configuration)),),
            )
            connection.commit()

    def observation_configuration(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT configuration_json FROM observation_configuration WHERE id = 1"
            ).fetchone()
        return json.loads(str(row[0])) if row is not None else None

    def save_observation_configuration(self, configuration: Mapping[str, Any]) -> None:
        payload = json.dumps(dict(configuration), separators=(",", ":"), sort_keys=True)
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO observation_configuration(id, updated_at, configuration_json) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at, configuration_json = excluded.configuration_json",
                (utc_now(), payload),
            )
            connection.commit()

    def start_observation_session(self, configuration: Mapping[str, Any]) -> str:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id FROM observation_sessions WHERE status = 'RUNNING' LIMIT 1"
            ).fetchone()
            if row is not None:
                connection.commit()
                return str(row["id"])
            session_id = str(uuid4())
            connection.execute(
                "INSERT INTO observation_sessions(id, started_at, status, configuration_json) VALUES (?, ?, 'RUNNING', ?)",
                (
                    session_id,
                    utc_now(),
                    json.dumps(dict(configuration), separators=(",", ":"), sort_keys=True),
                ),
            )
            connection.commit()
            return session_id

    def recover_interrupted_observation_session(self) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id FROM observation_sessions WHERE status = 'RUNNING' LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            session_id = str(row["id"])
            connection.execute(
                "UPDATE observation_sessions SET status = 'INTERRUPTED', ended_at = ?, "
                "error = 'Processo reiniciado antes do término da sessão.' WHERE id = ?",
                (utc_now(), session_id),
            )
            connection.commit()
            return session_id

    def stop_observation_session(self, session_id: str, error: str | None = None) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE observation_sessions SET status = ?, ended_at = ?, error = ? WHERE id = ? AND status = 'RUNNING'",
                ("ERROR" if error else "STOPPED", utc_now(), error, session_id),
            )
            connection.commit()

    def record_observation_event(self, session_id: str, level: str, message: str) -> None:
        self.record_event(
            session_id=session_id,
            level=level,
            component="observation",
            event_type=f"OBSERVATION_{level}",
            message=message,
        )

    def record_observation_cycle(
        self,
        cycle_id: str,
        session_id: str | None,
        started_at: Any,
        status: str,
        summary: str,
        chart_count: int,
    ) -> None:
        if session_id is None:
            raise ValueError("Observation cycle requires a session.")
        started = started_at.isoformat() if hasattr(started_at, "isoformat") else str(started_at)
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT id FROM observation_cycles WHERE id = ?", (cycle_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO observation_cycles(id, session_id, started_at, completed_at, status, summary, chart_count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (cycle_id, session_id, started, utc_now(), status, summary, chart_count),
                )
            else:
                connection.execute(
                    "UPDATE observation_cycles SET completed_at = ?, status = ?, summary = ?, chart_count = ? WHERE id = ?",
                    (utc_now(), status, summary, chart_count, cycle_id),
                )
            connection.commit()

    def record_observation_result(self, cycle_id: str, result: Mapping[str, Any]) -> str:
        result_id = str(uuid4())
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO observation_results(id, cycle_id, created_at, result_json) VALUES (?, ?, ?, ?)",
                (
                    result_id,
                    cycle_id,
                    utc_now(),
                    json.dumps(dict(result), separators=(",", ":"), sort_keys=True),
                ),
            )
            connection.commit()
        return result_id

    def list_observation_cycles(
        self, *, session_id: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 500)
        safe_offset = max(offset, 0)
        with self.connect() as connection:
            if session_id is None:
                rows = connection.execute(
                    "SELECT id, session_id, started_at, completed_at, status, summary, chart_count "
                    "FROM observation_cycles ORDER BY started_at DESC LIMIT ? OFFSET ?",
                    (safe_limit, safe_offset),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT id, session_id, started_at, completed_at, status, summary, chart_count "
                    "FROM observation_cycles WHERE session_id = ? ORDER BY started_at DESC LIMIT ? OFFSET ?",
                    (session_id, safe_limit, safe_offset),
                ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                children = connection.execute(
                    "SELECT result_json FROM observation_results WHERE cycle_id = ? ORDER BY created_at",
                    (item["id"],),
                ).fetchall()
                item["results"] = [json.loads(str(child[0])) for child in children]
                result.append(item)
            return result

    def create_observation_outbox(self, cycle_id: str, text: str) -> str:
        outbox_id = str(uuid4())
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO observation_outbox(id, cycle_id, created_at, updated_at, status, text) "
                "VALUES (?, ?, ?, ?, 'PENDING', ?)",
                (outbox_id, cycle_id, utc_now(), utc_now(), text),
            )
            connection.commit()
        return outbox_id

    def finish_observation_outbox(
        self,
        outbox_id: str,
        status: str,
        *,
        response: Mapping[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE observation_outbox SET updated_at = ?, status = ?, response_json = ?, error = ? WHERE id = ?",
                (
                    utc_now(),
                    status,
                    json.dumps(dict(response)) if response is not None else None,
                    error,
                    outbox_id,
                ),
            )
            connection.commit()

    def mark_observation_outbox_sending(self, outbox_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE observation_outbox SET updated_at = ?, status = 'SENDING' "
                "WHERE id = ? AND status = 'PENDING'",
                (utc_now(), outbox_id),
            )
            connection.commit()

    def record_applied_state(
        self,
        *,
        configuration_version: str,
        matches_desired: bool,
        state: Mapping[str, Any],
    ) -> str:
        state_id = str(uuid4())
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO applied_states(id, configuration_version, created_at, "
                "matches_desired, state_json) VALUES (?, ?, ?, ?, ?)",
                (
                    state_id,
                    configuration_version,
                    utc_now(),
                    int(matches_desired),
                    json.dumps(dict(state), separators=(",", ":"), sort_keys=True),
                ),
            )
            connection.commit()
        return state_id

    def latest_applied_state(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, configuration_version, created_at, matches_desired, state_json "
                "FROM applied_states ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["matches_desired"] = bool(result["matches_desired"])
        result["state"] = json.loads(result.pop("state_json"))
        return result

    def record_analysis_capture(
        self,
        *,
        session_id: str | None,
        applied_state_id: str,
        primary: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> str:
        capture_id = str(uuid4())
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO analysis_captures(id, session_id, created_at, applied_state_id, "
                "primary_json, context_json) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    capture_id,
                    session_id,
                    utc_now(),
                    applied_state_id,
                    json.dumps(dict(primary), separators=(",", ":"), sort_keys=True),
                    json.dumps(dict(context), separators=(",", ":"), sort_keys=True),
                ),
            )
            connection.commit()
        return capture_id

    def get_analysis_capture(self, capture_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, session_id, created_at, applied_state_id, primary_json, "
                "context_json FROM analysis_captures WHERE id = ?",
                (capture_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["primary"] = json.loads(result.pop("primary_json"))
        result["context"] = json.loads(result.pop("context_json"))
        return result

    def get_applied_state(self, state_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, configuration_version, created_at, matches_desired, state_json "
                "FROM applied_states WHERE id = ?",
                (state_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["matches_desired"] = bool(result["matches_desired"])
        result["state"] = json.loads(result.pop("state_json"))
        return result

    def record_trade_candidate(
        self,
        candidate: Mapping[str, Any],
        *,
        max_trades: int,
        max_consecutive_losses: int,
        max_session_loss: float,
    ) -> str:
        candidate_id = str(candidate["candidate_id"])
        session_id = str(candidate["session_id"])
        decision = str(candidate["decision"])
        payload = json.dumps(dict(candidate), separators=(",", ":"), sort_keys=True)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT candidate_json FROM trade_candidates WHERE id = ?", (candidate_id,)
            ).fetchone()
            if existing is not None:
                if str(existing["candidate_json"]) != payload:
                    raise ValueError("Trade candidate is immutable.")
                connection.commit()
                return candidate_id
            if decision == "ALLOWED":
                limits = connection.execute(
                    "SELECT trades, consecutive_losses, loss FROM session_risk_state "
                    "WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                if limits is None:
                    raise ValueError("Session risk state is missing transactionally.")
                if (
                    int(limits["trades"]) >= max_trades
                    or int(limits["consecutive_losses"]) >= max_consecutive_losses
                    or float(limits["loss"]) >= max_session_loss
                ):
                    raise ValueError("Session risk limit was reached transactionally.")
            connection.execute(
                "INSERT INTO trade_candidates(id, session_id, created_at, decision, "
                "candidate_json) VALUES (?, ?, ?, ?, ?)",
                (candidate_id, session_id, utc_now(), decision, payload),
            )
            connection.commit()
        return candidate_id

    def session_limits(self, session_id: str) -> dict[str, int | float] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT trades, consecutive_losses, loss FROM session_risk_state "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_trade_candidates(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 500)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT candidate_json FROM trade_candidates ORDER BY created_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [json.loads(str(row["candidate_json"])) for row in rows]

    def get_trade_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT candidate_json FROM trade_candidates WHERE id = ?", (candidate_id,)
            ).fetchone()
        return json.loads(str(row["candidate_json"])) if row is not None else None

    @staticmethod
    def _approval_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["candidate_snapshot"] = json.loads(result.pop("candidate_json"))
        for source, target in (("telegram_json", "telegram"), ("recheck_json", "recheck")):
            raw = result.pop(source)
            result[target] = json.loads(str(raw)) if raw is not None else None
        result.pop("callback_digest", None)
        return result

    def create_approval(
        self,
        *,
        approval_id: str,
        candidate: Mapping[str, Any],
        chat_id: int,
        user_id: int,
        callback_digest: str,
        created_at: str,
        expires_at: str,
    ) -> dict[str, Any]:
        candidate_id = str(candidate["candidate_id"])
        payload = json.dumps(dict(candidate), separators=(",", ":"), sort_keys=True)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            stored = connection.execute(
                "SELECT candidate_json, decision, sessions.status AS session_status "
                "FROM trade_candidates JOIN sessions ON sessions.id = trade_candidates.session_id "
                "WHERE trade_candidates.id = ?",
                (candidate_id,),
            ).fetchone()
            if (
                stored is None
                or stored["decision"] != "ALLOWED"
                or stored["session_status"] != "RUNNING"
            ):
                raise ValueError(
                    "Candidate is missing, not allowed, or its session is not running."
                )
            if str(stored["candidate_json"]) != payload:
                raise ValueError("Candidate snapshot does not match persisted candidate.")
            existing = connection.execute(
                "SELECT * FROM approvals WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
            if existing is not None:
                connection.commit()
                return self._approval_row(existing)
            connection.execute(
                "INSERT INTO approvals(id, candidate_id, session_id, created_at, expires_at, "
                "status, chat_id, user_id, callback_digest, candidate_json) "
                "VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?)",
                (
                    approval_id,
                    candidate_id,
                    str(candidate["session_id"]),
                    created_at,
                    expires_at,
                    chat_id,
                    user_id,
                    callback_digest,
                    payload,
                ),
            )
            row = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            connection.commit()
        assert row is not None
        return self._approval_row(row)

    def mark_approval_sent(self, approval_id: str, telegram: Mapping[str, Any]) -> dict[str, Any]:
        payload = json.dumps(dict(telegram), separators=(",", ":"), sort_keys=True)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE approvals SET telegram_json = COALESCE(telegram_json, ?) WHERE id = ?",
                (payload, approval_id),
            )
            row = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            connection.commit()
        if row is None:
            raise ValueError("Approval is missing.")
        return self._approval_row(row)

    def consume_approval(
        self,
        *,
        callback_digest: str,
        chat_id: int,
        user_id: int,
        status: str,
        decided_at: str,
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE approvals SET status = CASE WHEN expires_at <= ? "
                "THEN 'EXPIRED' ELSE ? END, "
                "decided_at = ?, reason = CASE WHEN expires_at <= ? THEN 'Approval TTL expired.' "
                "ELSE NULL END WHERE callback_digest = ? AND chat_id = ? AND user_id = ? "
                "AND status = 'PENDING'",
                (decided_at, status, decided_at, decided_at, callback_digest, chat_id, user_id),
            )
            if cursor.rowcount != 1:
                connection.commit()
                return None
            row = connection.execute(
                "SELECT * FROM approvals WHERE callback_digest = ?", (callback_digest,)
            ).fetchone()
            connection.commit()
        assert row is not None
        result = self._approval_row(row)
        return result if result["status"] == status else None

    def expire_approvals(self, now: str) -> int:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE approvals SET status = 'EXPIRED', decided_at = ?, "
                "reason = 'Approval TTL expired.' WHERE status = 'PENDING' AND expires_at <= ?",
                (now, now),
            )
            connection.commit()
            return int(cursor.rowcount)

    def cancel_pending_approvals(self, *, session_id: str, reason: str, decided_at: str) -> int:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE approvals SET status = 'CANCELLED', decided_at = ?, reason = ? "
                "WHERE session_id = ? AND status = 'PENDING'",
                (decided_at, reason, session_id),
            )
            connection.commit()
            return int(cursor.rowcount)

    def finish_approval_recheck(
        self,
        *,
        approval_id: str,
        status: str,
        reason: str,
        recheck: Mapping[str, Any],
        decided_at: str,
        emit_would_execute: bool = True,
    ) -> dict[str, Any] | None:
        payload = json.dumps(dict(recheck), separators=(",", ":"), sort_keys=True)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE approvals SET status = ?, decided_at = ?, reason = ?, recheck_json = ? "
                "WHERE id = ? AND status = 'APPROVED' "
                "AND EXISTS (SELECT 1 FROM sessions WHERE sessions.id = approvals.session_id "
                "AND sessions.status = 'RUNNING')",
                (status, decided_at, reason, payload, approval_id),
            )
            if cursor.rowcount != 1:
                connection.commit()
                return None
            row = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            if status == "WOULD_EXECUTE" and emit_would_execute:
                self._insert_event(
                    connection,
                    session_id=str(row["session_id"]),
                    level="INFO",
                    component="dry_run",
                    event_type="WOULD_EXECUTE",
                    message="Approved proposal passed recheck; no financial action was sent.",
                    details={"approval_id": approval_id, "candidate_id": str(row["candidate_id"])},
                )
            connection.commit()
        assert row is not None
        return self._approval_row(row)

    def list_approvals(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 500)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM approvals ORDER BY created_at DESC LIMIT ?", (safe_limit,)
            ).fetchall()
        return [self._approval_row(row) for row in rows]

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
        return self._approval_row(row) if row is not None else None

    def get_approval_for_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
        return self._approval_row(row) if row is not None else None

    @staticmethod
    def _operation_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["operation_id"] = result.pop("id")
        for source, target in (
            ("snapshot_json", "snapshot"),
            ("precheck_json", "precheck"),
            ("submission_json", "submission"),
            ("reconciliation_json", "reconciliation"),
        ):
            raw = result.pop(source)
            result[target] = json.loads(str(raw)) if raw is not None else None
        return result

    def create_operation(
        self,
        *,
        approval_id: str,
        candidate_id: str,
        session_id: str,
        symbol: str,
        side: str,
        quantity: float,
        configuration_version: str,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any]:
        now = utc_now()
        operation_id = str(uuid4())
        payload = json.dumps(dict(snapshot), separators=(",", ":"), sort_keys=True)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM operations WHERE approval_id = ? OR candidate_id = ? LIMIT 1",
                (approval_id, candidate_id),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["approval_id"]) != approval_id
                    or str(existing["candidate_id"]) != candidate_id
                ):
                    raise ValueError(
                        "Approval or candidate is already linked to another operation."
                    )
                connection.commit()
                return self._operation_row(existing)
            approval = connection.execute(
                "SELECT approvals.status, approvals.session_id, sessions.status AS session_status "
                "FROM approvals JOIN sessions ON sessions.id = approvals.session_id "
                "WHERE approvals.id = ? AND approvals.candidate_id = ?",
                (approval_id, candidate_id),
            ).fetchone()
            if (
                approval is None
                or approval["status"] != "WOULD_EXECUTE"
                or approval["session_id"] != session_id
                or approval["session_status"] != "RUNNING"
            ):
                raise ValueError("Approval is not rechecked, linked, and active.")
            connection.execute(
                "INSERT INTO operations(id, approval_id, candidate_id, session_id, created_at, "
                "updated_at, status, symbol, side, quantity, configuration_version, snapshot_json) "
                "VALUES (?, ?, ?, ?, ?, ?, 'CREATED', ?, ?, ?, ?, ?)",
                (
                    operation_id,
                    approval_id,
                    candidate_id,
                    session_id,
                    now,
                    now,
                    symbol,
                    side,
                    quantity,
                    configuration_version,
                    payload,
                ),
            )
            row = connection.execute(
                "SELECT * FROM operations WHERE id = ?", (operation_id,)
            ).fetchone()
            if row is not None:
                self._insert_event(
                    connection,
                    session_id=str(row["session_id"]),
                    level="INFO",
                    component="execution",
                    event_type="DEMO_OPERATION_CREATED",
                    message="Demo operation intent was persisted before external action.",
                    details={"operation_id": operation_id, "status": "CREATED"},
                )
            connection.commit()
        assert row is not None
        return self._operation_row(row)

    def update_operation(
        self,
        operation_id: str,
        *,
        status: str,
        reason: str | None = None,
        precheck: Mapping[str, Any] | None = None,
        submission: Mapping[str, Any] | None = None,
        reconciliation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        fields = ["status = ?", "updated_at = ?"]
        values: list[Any] = [status, utc_now()]
        for column, value in (
            ("reason", reason),
            ("precheck_json", precheck),
            ("submission_json", submission),
            ("reconciliation_json", reconciliation),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                values.append(
                    json.dumps(dict(value), separators=(",", ":"), sort_keys=True)
                    if isinstance(value, Mapping)
                    else value
                )
        values.append(operation_id)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                f"UPDATE operations SET {', '.join(fields)} WHERE id = ?",  # noqa: S608
                values,
            )
            row = connection.execute(
                "SELECT * FROM operations WHERE id = ?", (operation_id,)
            ).fetchone()
            if row is not None:
                self._insert_event(
                    connection,
                    session_id=str(row["session_id"]),
                    level=("WARNING" if status in {"AMBIGUOUS", "BLOCKED"} else "INFO"),
                    component="execution",
                    event_type=f"DEMO_OPERATION_{status}",
                    message=f"Demo operation changed to {status}.",
                    details={"operation_id": operation_id, "status": status},
                )
            connection.commit()
        if row is None:
            raise ValueError("Operation is missing.")
        return self._operation_row(row)

    def record_operation_observation(
        self, operation_id: str, *, phase: str, observation: Mapping[str, Any]
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO operation_observations(id, operation_id, created_at, phase, "
                "observation_json) VALUES (?, ?, ?, ?, ?)",
                (
                    str(uuid4()),
                    operation_id,
                    utc_now(),
                    phase,
                    json.dumps(dict(observation), separators=(",", ":"), sort_keys=True),
                ),
            )
            connection.commit()

    def get_operation_for_approval(self, approval_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM operations WHERE approval_id = ?", (approval_id,)
            ).fetchone()
        return self._operation_row(row) if row is not None else None

    def get_operation(self, operation_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM operations WHERE id = ?", (operation_id,)
            ).fetchone()
        return self._operation_row(row) if row is not None else None

    def list_operations(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 500)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM operations ORDER BY created_at DESC LIMIT ?", (safe_limit,)
            ).fetchall()
        return [self._operation_row(row) for row in rows]

    def open_operations(self) -> list[dict[str, Any]]:
        terminal = ("FILLED", "REJECTED", "CANCELLED", "BLOCKED")
        placeholders = ",".join("?" for _ in terminal)
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM operations WHERE status NOT IN ({placeholders}) "  # noqa: S608
                "ORDER BY created_at",
                terminal,
            ).fetchall()
        return [self._operation_row(row) for row in rows]

    def has_blocking_operation(self) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM operations WHERE status IN "
                "('CREATED', 'PRECHECKED', 'SUBMITTED_UNCONFIRMED', 'PENDING', "
                "'PARTIAL', 'AMBIGUOUS') LIMIT 1"
            ).fetchone()
        return row is not None

    @staticmethod
    def _protection_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["protection_policy_id"] = result.pop("id")
        for source, target in (
            ("policy_json", "policy"),
            ("observation_json", "observation"),
            ("action_json", "action"),
        ):
            raw = result.pop(source)
            result[target] = json.loads(str(raw)) if raw is not None else None
        return result

    def create_protection_policy(
        self, operation_id: str, policy: Mapping[str, Any]
    ) -> dict[str, Any]:
        now = utc_now()
        payload = json.dumps(dict(policy), separators=(",", ":"), sort_keys=True)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM protection_policies WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if existing is None:
                operation = connection.execute(
                    "SELECT status FROM operations WHERE id = ?", (operation_id,)
                ).fetchone()
                if operation is None or operation["status"] != "FILLED":
                    raise ValueError("Protection requires a filled demo operation.")
                connection.execute(
                    "INSERT INTO protection_policies(id, operation_id, created_at, updated_at, "
                    "status, policy_json) VALUES (?, ?, ?, ?, 'UNPROTECTED', ?)",
                    (str(uuid4()), operation_id, now, now, payload),
                )
                existing = connection.execute(
                    "SELECT * FROM protection_policies WHERE operation_id = ?", (operation_id,)
                ).fetchone()
            connection.commit()
        assert existing is not None
        return self._protection_row(existing)

    def update_protection_policy(
        self,
        policy_id: str,
        *,
        status: str,
        policy: Mapping[str, Any] | None = None,
        observation: Mapping[str, Any] | None = None,
        action: Mapping[str, Any] | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        fields = ["status = ?", "updated_at = ?", "reason = ?"]
        values: list[Any] = [status, utc_now(), reason]
        for column, value in (
            ("policy_json", policy),
            ("observation_json", observation),
            ("action_json", action),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                values.append(json.dumps(dict(value), separators=(",", ":"), sort_keys=True))
        values.append(policy_id)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                f"UPDATE protection_policies SET {', '.join(fields)} WHERE id = ?",  # noqa: S608
                values,
            )
            row = connection.execute(
                "SELECT * FROM protection_policies WHERE id = ?", (policy_id,)
            ).fetchone()
            if row is not None:
                operation = connection.execute(
                    "SELECT session_id FROM operations WHERE id = ?", (row["operation_id"],)
                ).fetchone()
                self._insert_event(
                    connection,
                    session_id=str(operation["session_id"]) if operation else None,
                    level="WARNING" if status in {"SAFE_STOP", "EMERGENCY_UNCONFIRMED"} else "INFO",
                    component="protection",
                    event_type=f"PROTECTION_{status}",
                    message=f"Demo protection changed to {status}.",
                    details={"protection_policy_id": policy_id},
                )
            connection.commit()
        if row is None:
            raise ValueError("Protection policy is missing.")
        return self._protection_row(row)

    def get_protection_policy(self, operation_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM protection_policies WHERE operation_id = ?", (operation_id,)
            ).fetchone()
        return self._protection_row(row) if row is not None else None

    def list_protection_policies(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 500)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM protection_policies ORDER BY updated_at DESC LIMIT ?", (safe_limit,)
            ).fetchall()
        return [self._protection_row(row) for row in rows]

    def filled_operations_requiring_recovery(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT operations.* FROM operations LEFT JOIN protection_policies "
                "ON protection_policies.operation_id = operations.id "
                "WHERE operations.status = 'FILLED' AND "
                "(protection_policies.id IS NULL OR protection_policies.status != 'CLOSED') "
                "ORDER BY operations.created_at"
            ).fetchall()
        return [self._operation_row(row) for row in rows]

    def has_blocking_protection(self) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM operations LEFT JOIN protection_policies "
                "ON protection_policies.operation_id = operations.id "
                "WHERE operations.status = 'FILLED' AND "
                "(protection_policies.id IS NULL OR protection_policies.status IN "
                "('UNPROTECTED', 'APPLYING_INITIAL', 'SAFE_STOP', 'EMERGENCY_PENDING', "
                "'EMERGENCY_UNCONFIRMED')) LIMIT 1"
            ).fetchone()
        return row is not None

    @staticmethod
    def _emergency_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["emergency_operation_id"] = result.pop("id")
        for source, target in (("snapshot_json", "snapshot"), ("result_json", "result")):
            raw = result.pop(source)
            result[target] = json.loads(str(raw)) if raw is not None else None
        return result

    def create_emergency_operation(
        self, policy_id: str, snapshot: Mapping[str, Any]
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM emergency_operations WHERE protection_policy_id = ?", (policy_id,)
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO emergency_operations(id, protection_policy_id, created_at, "
                    "updated_at, status, snapshot_json) VALUES (?, ?, ?, ?, 'CREATED', ?)",
                    (
                        str(uuid4()),
                        policy_id,
                        now,
                        now,
                        json.dumps(dict(snapshot), separators=(",", ":"), sort_keys=True),
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM emergency_operations WHERE protection_policy_id = ?",
                    (policy_id,),
                ).fetchone()
            connection.commit()
        assert row is not None
        return self._emergency_row(row)

    def update_emergency_operation(
        self,
        emergency_id: str,
        *,
        status: str,
        result: Mapping[str, Any] | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        payload = (
            json.dumps(dict(result), separators=(",", ":"), sort_keys=True)
            if result is not None
            else None
        )
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE emergency_operations SET status = ?, updated_at = ?, "
                "result_json = COALESCE(?, result_json), reason = ? WHERE id = ?",
                (status, utc_now(), payload, reason, emergency_id),
            )
            row = connection.execute(
                "SELECT * FROM emergency_operations WHERE id = ?", (emergency_id,)
            ).fetchone()
            connection.commit()
        if row is None:
            raise ValueError("Emergency operation is missing.")
        return self._emergency_row(row)

    @staticmethod
    def _human_review_row(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    def create_human_review(
        self,
        *,
        session_id: str | None,
        detection_id: str | None,
        candidate_id: str | None,
        label: str,
        notes: str,
        reviewer_id: str,
        evidence_sha256: str,
        evidence_path: str,
        detector_version: str,
        pattern_id: str,
        pattern_version: str,
    ) -> dict[str, Any]:
        review_key = (
            f"detection:{detection_id}" if detection_id is not None else f"candidate:{candidate_id}"
        )
        review_id = str(uuid4())
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT id, revision FROM human_reviews WHERE review_key = ? "
                "ORDER BY revision DESC LIMIT 1",
                (review_key,),
            ).fetchone()
            revision = int(previous["revision"]) + 1 if previous is not None else 1
            previous_id = str(previous["id"]) if previous is not None else None
            connection.execute(
                "INSERT INTO human_reviews(id, review_key, revision, previous_review_id, "
                "session_id, detection_id, candidate_id, created_at, label, notes, "
                "reviewer_id, evidence_sha256, evidence_path, detector_version, pattern_id, "
                "pattern_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    review_id,
                    review_key,
                    revision,
                    previous_id,
                    session_id,
                    detection_id,
                    candidate_id,
                    utc_now(),
                    label,
                    notes,
                    reviewer_id,
                    evidence_sha256,
                    evidence_path,
                    detector_version,
                    pattern_id,
                    pattern_version,
                ),
            )
            row = connection.execute(
                "SELECT * FROM human_reviews WHERE id = ?", (review_id,)
            ).fetchone()
            connection.commit()
        assert row is not None
        return self._human_review_row(row)

    def list_human_reviews(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 500)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM human_reviews ORDER BY created_at DESC, revision DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [self._human_review_row(row) for row in rows]

    def list_latest_human_reviews(
        self,
        *,
        pattern_id: str,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 500)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT review.* FROM human_reviews review "
                "WHERE review.pattern_id = ? AND review.revision = ("
                "SELECT MAX(latest.revision) FROM human_reviews latest "
                "WHERE latest.review_key = review.review_key) "
                "ORDER BY review.created_at DESC LIMIT ?",
                (pattern_id, safe_limit),
            ).fetchall()
        return [self._human_review_row(row) for row in rows]

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        *,
        session_id: str | None,
        level: str,
        component: str,
        event_type: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO events(id, session_id, created_at, level, component, event_type, "
            "message, details_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid4()),
                session_id,
                utc_now(),
                level,
                component,
                event_type,
                message,
                json.dumps(dict(details or {}), separators=(",", ":"), sort_keys=True),
            ),
        )
