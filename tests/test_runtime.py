from pathlib import Path
from typing import Any

import providency.runtime as runtime
from providency.config import Settings


class CompletedProcess:
    def wait(self, timeout: float | None = None) -> int:
        return 0

    def poll(self) -> int:
        return 0


def test_launcher_binds_streamlit_to_localhost(
    tmp_path: Path, monkeypatch: Any
) -> None:
    settings = Settings(data_dir=tmp_path)
    commands: list[list[str]] = []

    monkeypatch.setattr(runtime.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(runtime, "_wait_for_health", lambda _url: None)
    monkeypatch.setattr(runtime.webbrowser, "open", lambda _url: True)

    def start(command: list[str]) -> CompletedProcess:
        commands.append(command)
        return CompletedProcess()

    monkeypatch.setattr(runtime.subprocess, "Popen", start)

    runtime.run_launcher()

    ui_command = commands[1]
    assert ui_command[ui_command.index("--server.address") + 1] == "127.0.0.1"
    assert ui_command[ui_command.index("--browser.gatherUsageStats") + 1] == "false"
