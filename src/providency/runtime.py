from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from contextlib import suppress
from pathlib import Path
from threading import Event, Thread

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
    def request_exit() -> None:
        server.should_exit = True

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings, request_shutdown=request_exit),
            host=settings.api_host,
            port=settings.api_port,
            log_level="info",
            use_colors=False,
        )
    )
    server.run()


def _watch_engine(api_url: str, finished: Event) -> None:
    from streamlit.runtime import Runtime

    failures = 0
    while not finished.wait(1):
        failures = 0 if _healthy(api_url) else failures + 1
        if failures >= 3 and Runtime.exists():
            try:
                Runtime.instance().stop()
                return
            except RuntimeError:
                # Runtime exists before its event loop is ready during startup.
                continue


def _request_engine_shutdown(api_url: str) -> bool:
    request = urllib.request.Request(
        f"{api_url}/shutdown",
        method="POST",
        data=b'{"confirm": true}',
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return bool(response.status == 202)
    except (OSError, urllib.error.URLError):
        return False


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
    finished = Event()
    watcher = Thread(target=_watch_engine, args=(settings.api_url, finished), daemon=True)
    watcher.start()
    try:
        streamlit_cli.main()
    finally:
        finished.set()
        watcher.join(timeout=2)


def _role_command(role: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, f"--{role}"]
    if role == "engine":
        return [sys.executable, "-m", "providency.engine"]
    return [sys.executable, "-m", "providency.ui_server"]


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
        ui = subprocess.Popen(ui_command)
        webbrowser.open(ui_url)
        while engine.poll() is None:
            if ui.poll() is not None:
                if _request_engine_shutdown(settings.api_url):
                    engine.wait(timeout=20)
                    break
                # If a demo position blocks shutdown, restore the control panel.
                ui = subprocess.Popen(ui_command)
                webbrowser.open(ui_url)
                time.sleep(1)
            time.sleep(0.2)
        # run_ui observes the stopped engine and shuts Streamlit down gracefully.
        with suppress(subprocess.TimeoutExpired):
            ui.wait(timeout=10)
    finally:
        for process in (ui, engine):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        lock.release()
