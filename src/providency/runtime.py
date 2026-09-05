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
    )


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

    package_dir = Path(__file__).resolve().parent
    engine: subprocess.Popen[bytes] | None = None
    ui: subprocess.Popen[bytes] | None = None
    try:
        engine = subprocess.Popen([sys.executable, "-m", "providency.engine"])
        _wait_for_health(settings.api_url)
        ui = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(package_dir / "ui.py"),
                "--server.port",
                str(settings.ui_port),
                "--server.headless",
                "true",
            ]
        )
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
