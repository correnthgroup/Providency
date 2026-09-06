from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

from providency.api import create_app
from providency.config import Settings
from providency.locking import InstanceAlreadyRunning, InstanceLock


def _healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=0.5) as response:
            payload: object = json.loads(response.read().decode("utf-8"))
            return (
                response.status == 200
                and isinstance(payload, dict)
                and payload.get("status") == "ok"
            )
    except (OSError, ValueError, urllib.error.URLError):
        return False


def _wait_for_health(url: str, timeout_seconds: float = 20) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _healthy(url):
            return
        time.sleep(0.2)
    raise RuntimeError("Core Engine did not become healthy before the startup timeout.")


def run_engine() -> None:
    settings = Settings.from_env()
    uvicorn.run(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
        use_colors=False,
    )


def _streamlit_options(settings: Settings) -> list[str]:
    return [
        "--server.port",
        str(settings.ui_port),
        "--server.address",
        "127.0.0.1",
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--global.developmentMode",
        "false",
        "--theme.base",
        "light",
        "--theme.primaryColor",
        "#147A5A",
        "--theme.backgroundColor",
        "#F7F8F5",
        "--theme.secondaryBackgroundColor",
        "#E9F5F0",
        "--theme.textColor",
        "#10231D",
    ]


def run_ui() -> None:
    from streamlit.web import cli as streamlit_cli

    settings = Settings.from_env()
    package_dir = Path(__file__).resolve().parent
    sys.argv = [
        "streamlit",
        "run",
        str(package_dir / "ui.py"),
        *_streamlit_options(settings),
    ]
    streamlit_cli.main()


def _role_command(role: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, f"--{role}"]
    if role == "engine":
        return [sys.executable, "-m", "providency.engine"]
    return [sys.executable, "-m", "streamlit", "run", str(Path(__file__).parent / "ui.py")]


def run_launcher() -> None:
    settings = Settings.from_env()
    lock = InstanceLock(settings.lock_path)
    ui_url = f"http://127.0.0.1:{settings.ui_port}"
    try:
        lock.acquire()
    except InstanceAlreadyRunning:
        if _healthy(settings.api_url):
            webbrowser.open(ui_url)
            return
        raise

    engine: subprocess.Popen[bytes] | None = None
    ui: subprocess.Popen[bytes] | None = None
    try:
        engine = subprocess.Popen(_role_command("engine"))
        _wait_for_health(settings.api_url)
        ui_command = _role_command("ui")
        if not getattr(sys, "frozen", False):
            ui_command.extend(_streamlit_options(settings))
        ui = subprocess.Popen(ui_command)
        webbrowser.open(ui_url)
        ui.wait()
    finally:
        for process in (ui, engine):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        lock.release()
