from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from threading import Event, Thread
from typing import TYPE_CHECKING
from uuid import uuid4

import uvicorn

from providency.config import Settings
from providency.locking import InstanceAlreadyRunning, InstanceLock
from providency.startup import StartupProgress

if TYPE_CHECKING:
    from fastapi import FastAPI


ENGINE_SHUTDOWN_TIMEOUT_SECONDS = 35


def create_app(settings: Settings, *, request_shutdown: Callable[[], None]) -> FastAPI:
    # Import the engine only in its own process, after the loading UI is visible.
    from providency.api import create_app as factory

    return factory(settings, request_shutdown=request_shutdown)


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


def _wait_for_health(
    url: str,
    timeout_seconds: float = 20,
    *,
    process: subprocess.Popen[bytes] | None = None,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError("Engine exited during startup.")
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
            timeout_graceful_shutdown=5,
        )
    )
    server.run()


def _watch_engine(api_url: str, finished: Event) -> None:
    from streamlit.runtime import Runtime

    failures = 0
    while not finished.wait(1):
        progress = StartupProgress(Settings.from_env().data_dir).read()
        if progress.get("stage") not in {None, "READY"}:
            continue
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
        if _healthy(settings.api_url) or _ui_healthy(ui_url):
            webbrowser.open(ui_url)
            return
        raise

    engine: subprocess.Popen[bytes] | None = None
    ui: subprocess.Popen[bytes] | None = None
    progress = StartupProgress(settings.data_dir)
    launch_id = uuid4().hex
    progress.begin(launch_id)
    previous_launch_id = os.environ.get("PROVIDENCY_LAUNCH_ID")
    os.environ["PROVIDENCY_LAUNCH_ID"] = launch_id
    try:
        ui_command = _role_command("ui")
        ui = subprocess.Popen(ui_command)
        _wait_for_ui(ui_url, ui, progress)
        progress.advance("UI_READY")
        webbrowser.open(ui_url)
        if progress.cancelled():
            return
        progress.advance("STARTING_ENGINE")
        engine = subprocess.Popen(_role_command("engine"))
        try:
            _wait_for_health(settings.api_url, process=engine)
        except RuntimeError:
            if engine.poll() == 0:
                return
            progress.fail(
                "Não foi possível iniciar o motor local. Encerre e abra o Providency novamente."
            )
            while ui.poll() is None and not progress.cancelled():
                time.sleep(0.2)
            return
        progress.advance("READY")
        while engine.poll() is None:
            if ui.poll() is not None:
                if _request_engine_shutdown(settings.api_url):
                    # Observation cleanup may wait up to 25 seconds for an in-flight
                    # capture or Telegram request. Leave enough time for that bounded
                    # cleanup, but let the launcher finally block terminate a stuck
                    # process instead of surfacing TimeoutExpired to the operator.
                    with suppress(subprocess.TimeoutExpired):
                        engine.wait(timeout=ENGINE_SHUTDOWN_TIMEOUT_SECONDS)
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
        if previous_launch_id is None:
            os.environ.pop("PROVIDENCY_LAUNCH_ID", None)
        else:
            os.environ["PROVIDENCY_LAUNCH_ID"] = previous_launch_id


def _ui_healthy(ui_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{ui_url}/_stcore/health", timeout=0.5) as response:
            return bool(response.status == 200)
    except (OSError, urllib.error.URLError):
        return False


def _wait_for_ui(
    ui_url: str,
    ui: subprocess.Popen[bytes],
    progress: StartupProgress,
) -> None:
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if _ui_healthy(ui_url):
            return
        if ui.poll() is not None or progress.cancelled():
            raise RuntimeError("Providency interface did not start.")
        time.sleep(0.2)
    raise RuntimeError("Providency interface startup timed out.")
