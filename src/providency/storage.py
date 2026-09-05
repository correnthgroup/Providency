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
