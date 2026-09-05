from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

import streamlit as st
from PIL import Image, ImageDraw

from providency.config import Settings


def api_request(method: str, path: str) -> Any:
    settings = Settings.from_env()
    request = urllib.request.Request(f"{settings.api_url}{path}", method=method)
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", {})
            message = detail.get("human_message", "Vector Web is not ready.")
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
open_column, capture_column, analyze_column = st.columns(3)
with open_column:
    if st.button("Open Vector", use_container_width=True):
        try:
            api_request("POST", "/vector/open")
            st.info("Complete login manually in the Vector Web window.")
        except RuntimeError as exc:
            st.error(str(exc))
with capture_column:
    if st.button(
        "Capture primary chart",
        disabled=vector_health["state"] != "OPEN",
        use_container_width=True,
    ):
        try:
            capture = api_request("POST", "/vector/capture")
            if capture["disposition"] == "USABLE":
                st.success(f"{capture['symbol']} · {capture['timeframe']} · usable")
                st.image(capture["path"], caption=f"SHA-256 {capture['sha256']}")
            else:
                st.warning(f"No decision: {capture['issue']}")
        except RuntimeError as exc:
            st.error(str(exc))
with analyze_column:
    if st.button(
        "Capture and analyze",
        disabled=vector_health["state"] != "OPEN",
        use_container_width=True,
    ):
        try:
            st.session_state["last_pattern_analysis"] = api_request("POST", "/pattern/analyze")
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
