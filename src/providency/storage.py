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
