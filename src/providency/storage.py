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
        quantity: int,
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
