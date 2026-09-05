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

st.subheader("Analysis configuration")
configuration_state = api_request("GET", "/configuration")
desired = configuration_state["desired"]
missing = desired.get("missing_fields", [])
if missing:
    st.warning(
        "Candidate evaluation is blocked until these fields are configured: "
        + ", ".join(missing)
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
st.caption("ALLOWED candidates can be sent for Telegram approval; only dry-run is available.")
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
