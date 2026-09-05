from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from providency.storage import Storage


def build_session_report(storage: Storage, session_id: str) -> dict[str, Any]:
    with storage.connect() as connection:
        session_row = connection.execute(
            "SELECT id, started_at, ended_at, status FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if session_row is None:
            raise ValueError("Session was not found.")
        detections = connection.execute(
            "SELECT result, reason FROM pattern_detections WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        candidate_rows = connection.execute(
            "SELECT decision, candidate_json FROM trade_candidates WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        approvals = connection.execute(
            "SELECT status, reason FROM approvals WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        operations = connection.execute(
            "SELECT status, reason FROM operations WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        protections = connection.execute(
            "SELECT p.status, p.reason FROM protection_policies p "
            "JOIN operations o ON o.id = p.operation_id WHERE o.session_id = ?",
            (session_id,),
        ).fetchall()
        captures = int(
            connection.execute(
                "SELECT COUNT(*) FROM analysis_captures WHERE session_id = ?", (session_id,)
            ).fetchone()[0]
        )
        errors = connection.execute(
            "SELECT message FROM events WHERE session_id = ? AND level = 'ERROR'",
            (session_id,),
        ).fetchall()

    reasons: Counter[str] = Counter()
    for row in detections:
        if str(row["result"]) != "MATCH":
            reasons[str(row["reason"])] += 1
    candidates: list[dict[str, Any]] = []
    for row in candidate_rows:
        candidate = json.loads(str(row["candidate_json"]))
        candidates.append(candidate)
        if str(row["decision"]) == "BLOCKED":
            reasons.update(str(reason) for reason in candidate.get("blocking_reasons", []))
    for rows in (approvals, operations, protections):
        for row in rows:
            if row["reason"]:
                reasons[str(row["reason"])] += 1
    reasons.update(str(row["message"]) for row in errors)
    return {
        "report_version": 1,
        "session": dict(session_row),
        "funnel": {
            "captures": captures,
            "detections": len(detections),
            "confirmed_patterns": sum(str(row["result"]) == "MATCH" for row in detections),
            "candidates": len(candidates),
            "allowed_candidates": sum(item.get("decision") == "ALLOWED" for item in candidates),
            "approvals": len(approvals),
            "approved": sum(
                str(row["status"]) in {"APPROVED", "WOULD_EXECUTE"}
                for row in approvals
            ),
            "operations": len(operations),
            "filled_operations": sum(str(row["status"]) == "FILLED" for row in operations),
            "protections": len(protections),
            "closed": sum(str(row["status"]) == "CLOSED" for row in protections),
        },
        "blocking_reasons": dict(sorted(reasons.items())),
    }


def export_session_report(storage: Storage, session_id: str, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / f"session-{session_id}.json"
    payload = json.dumps(
        build_session_report(storage, session_id),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    path.write_text(payload + "\n", encoding="utf-8")
    return path
