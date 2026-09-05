from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

import streamlit as st
from PIL import Image, ImageDraw

from providency.config import Settings


def api_request(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    settings = Settings.from_env()
    request = urllib.request.Request(
        f"{settings.api_url}{path}",
        method=method,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", {})
            message = (
                detail.get("human_message", "Request was rejected.")
                if isinstance(detail, dict)
                else str(detail)
            )
        except (OSError, ValueError, AttributeError):
            message = "Vector Web is not ready."
        raise RuntimeError(str(message)) from exc
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise RuntimeError("Core Engine is unavailable.") from exc


st.set_page_config(page_title="Providency", page_icon="📈", layout="wide")
st.title("Providency")
st.caption("Local supervised market observation")

try:
    state = api_request("GET", "/state")
except RuntimeError as exc:
    st.error(str(exc))
    st.stop()

running = state["engine_state"] == "RUNNING"
st.metric("Core Engine", state["engine_state"])

run_column, stop_column = st.columns(2)
with run_column:
    if st.button("RUN", type="primary", disabled=running, use_container_width=True):
        api_request("POST", "/run")
        st.rerun()
with stop_column:
    if st.button("STOP", disabled=not running, use_container_width=True):
        api_request("POST", "/stop")
        st.rerun()

session = state.get("session")
if session:
    st.subheader("Current session")
    st.code(f"{session['id']} · {session['started_at']}")

overview_tab, review_tab, quality_tab = st.tabs(
    ["Session overview", "Human review", "Pattern quality"]
)
with overview_tab:
    if session:
        report = api_request("GET", f"/sessions/{session['id']}/report")
        funnel = report["funnel"]
        columns = st.columns(4)
        columns[0].metric("Captures", funnel["captures"])
        columns[1].metric("Confirmed patterns", funnel["confirmed_patterns"])
        columns[2].metric("Candidates", funnel["candidates"])
        columns[3].metric("Filled demo operations", funnel["filled_operations"])
        if report["blocking_reasons"]:
            st.warning("Blocking reasons are present in this session.")
            st.json(report["blocking_reasons"], expanded=False)
        if st.button("Export reproducible session report"):
            exported = api_request("POST", f"/sessions/{session['id']}/report/export")
            st.success(f"Report saved locally: {exported['path']}")
    else:
        st.info("Start a session to build its operational funnel.")

with review_tab:
    detections_for_review = api_request("GET", "/pattern/detections?limit=100")
    if not detections_for_review:
        st.info("Capture and analyze a chart before creating a human review.")
    else:
        detection_by_id = {item["id"]: item for item in detections_for_review}
        selected_detection_id = st.selectbox(
            "Detection",
            options=list(detection_by_id),
            format_func=lambda item: (
                f"{detection_by_id[item]['created_at']} · "
                f"{detection_by_id[item]['result']} · {item[:12]}"
            ),
        )
        selected_detection = detection_by_id[selected_detection_id]
        st.caption(selected_detection["reason"])
        if st.button("Create sanitized review copy"):
            st.session_state["sanitized_review_evidence"] = api_request(
                "POST",
                "/evidence/sanitize",
                {"detection_id": selected_detection_id, "redactions": []},
            )
        sanitized = st.session_state.get("sanitized_review_evidence")
        if sanitized and sanitized["source_sha256"] == selected_detection["screenshot_sha256"]:
            st.image(sanitized["path"], caption="Sanitized local evidence")
            label = st.selectbox(
                "Review label",
                options=[
                    "TRUE_POSITIVE",
                    "FALSE_POSITIVE",
                    "FALSE_NEGATIVE",
                    "TRUE_NEGATIVE",
                    "NO_DECISION",
                    "OPERATIONAL_FAILURE",
                ],
            )
            notes = st.text_area("Review notes", max_chars=500)
            if st.button("Save immutable review revision", disabled=not notes.strip()):
                saved = api_request(
                    "POST",
                    "/reviews",
                    {
                        "detection_id": selected_detection_id,
                        "label": label,
                        "notes": notes,
                        "reviewer_id": "local:operator",
                        "evidence_sha256": sanitized["source_sha256"],
                        "evidence_path": sanitized["path"],
                    },
                )
                st.success(f"Review saved as revision {saved['revision']}.")
                st.rerun()
        reviews = api_request("GET", "/reviews?limit=20")
        if reviews:
            st.dataframe(
                [
                    {
                        "created_at": item["created_at"],
                        "label": item["label"],
                        "revision": item["revision"],
                        "pattern_version": item["pattern_version"],
                        "notes": item["notes"],
                    }
                    for item in reviews
                ],
                use_container_width=True,
            )

with quality_tab:
    metrics = api_request("GET", "/metrics/patterns/bearish_engulfing")
    precision = metrics["precision"]
    recall = metrics["recall"]
    quality_columns = st.columns(3)
    quality_columns[0].metric(
        "Precision",
        "Insufficient sample"
        if precision["value"] is None
        else f"{precision['value'] * 100:.1f}%",
        help=f"{precision['numerator']}/{precision['denominator']} confirmed reviews",
    )
    quality_columns[1].metric(
        "Recall",
        "Not defensible" if recall["value"] is None else f"{recall['value'] * 100:.1f}%",
        help=f"{recall['numerator']}/{recall['denominator']} reviewed positives",
    )
    quality_columns[2].metric("Reviewed", metrics["reviewed_total"])
    if not metrics["sample_sufficient"]:
        st.info(
            "Precision is descriptive only until the confirmed-review denominator reaches "
            f"{metrics['minimum_sample']}."
        )
    st.json(
        {
            "counts": metrics["counts"],
            "window": metrics["window"],
            "detector_versions": metrics["detector_versions"],
            "pattern_versions": metrics["pattern_versions"],
        },
        expanded=False,
    )

st.subheader("Analysis configuration")
configuration_state = api_request("GET", "/configuration")
desired = configuration_state["desired"]
execution_state = api_request("GET", "/execution/status")
execution_mode = execution_state["mode"]
if execution_mode == "DEMO":
    st.warning("DEMO EXECUTION ENABLED — only the positively verified demo account may be used.")
else:
    st.info("DRY_RUN is active; no order action can reach Vector Web.")
if execution_state.get("blocking_protection"):
    st.error("SAFE_STOP: new exposure is blocked until demo position protection is understood.")
missing = desired.get("missing_fields", [])
if missing:
    st.warning(
        "Candidate evaluation is blocked until these fields are configured: " + ", ".join(missing)
    )
else:
    st.success(f"Configuration {desired['version']} is complete.")
st.json(configuration_state, expanded=False)

st.subheader("Activity")
events = api_request("GET", "/events?limit=100")
if not events:
    st.info("No activity recorded yet.")
else:
    for event in events:
        st.text(
            f"{event['created_at']}  {event['level']:<7}  "
            f"{event['component']:<10}  {event['message']}"
        )

st.subheader("Vector Web")
vector_health = api_request("GET", "/vector/health")
st.caption(f"Browser profile: {vector_health['state']}")
open_column, sync_column, capture_column, analyze_column = st.columns(4)
with open_column:
    if st.button("Open Vector", use_container_width=True):
        try:
            api_request("POST", "/vector/open")
            st.info("Complete login manually in the Vector Web window.")
        except RuntimeError as exc:
            st.error(str(exc))
with capture_column:
    if st.button(
        "Capture analysis pair",
        disabled=vector_health["state"] != "OPEN",
        use_container_width=True,
    ):
        try:
            capture = api_request("POST", "/vector/capture-analysis")
            st.session_state["last_analysis_capture"] = capture
            st.session_state["last_pattern_analysis"] = capture["pattern"]
            if (
                capture["primary"]["disposition"] == "USABLE"
                and capture["context"]["disposition"] == "USABLE"
            ):
                st.success("Primary and context captured; primary state restored.")
                st.image(
                    [capture["primary"]["path"], capture["context"]["path"]],
                    caption=["Primary", "Context"],
                )
            else:
                st.warning("No decision: one of the analysis captures is unusable.")
        except RuntimeError as exc:
            st.error(str(exc))
with sync_column:
    if st.button(
        "Sync analysis state",
        disabled=vector_health["state"] != "OPEN",
        use_container_width=True,
    ):
        try:
            synced = api_request("POST", "/vector/sync")
            if synced["matches_desired"]:
                st.success("Symbol, timeframe, moving averages, and price scale verified.")
            else:
                st.warning("Applied state is incomplete or divergent.")
            st.json(synced, expanded=False)
        except RuntimeError as exc:
            st.error(str(exc))
with analyze_column:
    if st.button(
        "Evaluate candidate",
        disabled=(
            vector_health["state"] != "OPEN"
            or not running
            or "last_analysis_capture" not in st.session_state
        ),
        use_container_width=True,
    ):
        try:
            captured = st.session_state["last_analysis_capture"]
            st.session_state["last_candidate"] = api_request(
                "POST",
                "/candidate/evaluate",
                {
                    "analysis_capture_id": captured["analysis_capture_id"],
                    "pattern_detection_id": captured["pattern_detection_id"],
                },
            )
        except RuntimeError as exc:
            st.error(str(exc))

analysis = st.session_state.get("last_pattern_analysis")
if analysis:
    status = analysis["status"]
    if status == "MATCH":
        st.success(f"{analysis['pattern_id']} · {status}")
    elif status == "FORMING":
        st.info(f"{analysis['pattern_id']} · {status}")
    else:
        st.warning(f"{analysis['pattern_id']} · {status}")
    st.caption(analysis["reason"])
    evidence = analysis.get("evidence")
    if evidence:
        with Image.open(evidence["screenshot_path"]) as source:
            annotated = source.convert("RGB")
        draw = ImageDraw.Draw(annotated)
        for index, item in enumerate(evidence["candles"]):
            box = item["box"]
            color = "#26a69a" if item["color"] == "BULLISH" else "#ef5350"
            draw.rectangle(
                (
                    box["x"],
                    box["y"],
                    box["x"] + box["width"],
                    box["y"] + box["height"],
                ),
                outline=color,
                width=2,
            )
            draw.text((box["x"], max(0, box["y"] - 12)), str(index), fill=color)
        st.image(annotated, caption=f"SHA-256 {evidence['screenshot_sha256']}")
        st.dataframe(
            [
                {
                    "candle": index,
                    "color": item["color"],
                    **item["candle"],
                    **item["box"],
                }
                for index, item in enumerate(evidence["candles"])
            ],
            use_container_width=True,
        )
    st.json({"measures": analysis["measures"]}, expanded=False)

st.subheader("Trade candidates")
st.caption(
    "ALLOWED candidates can be sent for Telegram approval; the configured mode is "
    f"{execution_mode}."
)
candidates = api_request("GET", "/candidates?limit=20")
if not candidates:
    st.info("No candidate has been evaluated yet.")
else:
    for candidate in candidates:
        decision = candidate["decision"]
        if decision == "ALLOWED":
            st.success(f"{candidate['candidate_id'][:12]} · {decision}")
        else:
            st.warning(f"{candidate['candidate_id'][:12]} · {decision}")
        st.write(
            {
                "entry": candidate["entry"],
                "stop": candidate["stop"],
                "quantity": candidate["quantity"],
                "risk_amount": candidate["risk_amount"],
                "reference_target": candidate["reference_target"],
                "reference_rr": candidate["reference_rr"],
                "confluence": (
                    f"{candidate['confluence']['passed_total']}/"
                    f"{candidate['confluence']['applicable_total']}"
                ),
                "limits": candidate["limits"],
                "blocking_reasons": candidate["blocking_reasons"],
            }
        )
        if decision == "ALLOWED" and st.button(
            "Send immutable Telegram proposal", key=f"proposal-{candidate['candidate_id']}"
        ):
            try:
                proposal = api_request("POST", f"/approvals/{candidate['candidate_id']}")
                st.success(f"Proposal {proposal['id'][:12]} · {proposal['status']}")
                st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))

st.subheader("Demo operations")
operations = api_request("GET", "/operations?limit=20")
if not operations:
    st.info("No persisted demo operation exists.")
else:
    for operation in operations:
        status = operation["status"]
        if status == "FILLED":
            st.success(f"{operation['operation_id'][:12]} · {status}")
        elif status in {"PENDING", "PARTIAL", "SUBMITTED_UNCONFIRMED"}:
            st.warning(f"{operation['operation_id'][:12]} · {status}")
        else:
            st.error(f"{operation['operation_id'][:12]} · {status}")
        st.json(
            {
                "account": (operation.get("reconciliation") or {}).get("account"),
                "symbol": operation["symbol"],
                "side": operation["side"],
                "quantity": operation["quantity"],
                "reason": operation["reason"],
                "order": (operation.get("reconciliation") or {}).get("order"),
                "position": (operation.get("reconciliation") or {}).get("position"),
            },
            expanded=False,
        )
        if status == "FILLED" and execution_mode == "DEMO":
            manage_column, emergency_column = st.columns(2)
            with manage_column:
                if st.button("Manage protection", key=f"manage-{operation['operation_id']}"):
                    api_request(
                        "POST", f"/operations/{operation['operation_id']}/protection/manage"
                    )
                    st.rerun()
            with emergency_column:
                if st.button(
                    "EMERGENCY STOP (demo)",
                    key=f"emergency-{operation['operation_id']}",
                    type="secondary",
                ):
                    api_request(
                        "POST",
                        f"/operations/{operation['operation_id']}/emergency-stop",
                        {"confirm_demo_close": True},
                    )
                    st.rerun()

st.subheader("Demo position protection")
protections = api_request("GET", "/protections?limit=20")
if not protections:
    st.info("No persisted protection policy exists.")
else:
    for protection in protections:
        status = protection["status"]
        if status in {"PROTECTED", "BREAKEVEN", "TRAILING", "CLOSED"}:
            st.success(f"{protection['protection_policy_id'][:12]} · {status}")
        else:
            st.error(f"{protection['protection_policy_id'][:12]} · {status}")
        st.json(
            {
                "operation_id": protection["operation_id"],
                "current_stop": protection["policy"].get("current_stop"),
                "trailing_timeframe": protection["policy"].get("timeframe"),
                "last_closed_candle": protection["policy"].get("last_closed_candle"),
                "reason": protection["reason"],
                "action_required": status
                in {"SAFE_STOP", "EMERGENCY_PENDING", "EMERGENCY_UNCONFIRMED"},
            },
            expanded=False,
        )
st.subheader("Telegram approvals")
telegram_state = api_request("GET", "/telegram/status")
telegram_missing = telegram_state["configuration"].get("missing_fields", [])
if telegram_missing:
    st.warning("Telegram is blocked until configured: " + ", ".join(telegram_missing))
else:
    st.success("Telegram approval polling is configured; the bot token remains masked.")
st.json(telegram_state, expanded=False)

approvals = api_request("GET", "/approvals?limit=20")
if not approvals:
    st.info("No Telegram proposal has been created yet.")
else:
    for approval in approvals:
        st.write(
            {
                "approval_id": approval["id"],
                "candidate_id": approval["candidate_id"],
                "status": approval["status"],
                "expires_at": approval["expires_at"],
                "decided_at": approval["decided_at"],
                "reason": approval["reason"],
                "recheck": approval["recheck"],
            }
        )
