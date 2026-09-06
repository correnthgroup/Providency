from pathlib import Path
from typing import Any

import providency.runtime as runtime
from providency.config import Settings


class CompletedProcess:
    def wait(self, timeout: float | None = None) -> int:
        return 0

    def poll(self) -> int:
        return 0


def test_engine_disables_terminal_colors_for_windowed_package(
    tmp_path: Path, monkeypatch: Any
) -> None:
    settings = Settings(data_dir=tmp_path)
    observed: dict[str, Any] = {}
    monkeypatch.setattr(runtime.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(runtime, "create_app", lambda _settings: "app")
    monkeypatch.setattr(
        runtime.uvicorn,
        "run",
        lambda app, **kwargs: observed.update({"app": app, **kwargs}),
    )

    runtime.run_engine()

    assert observed["use_colors"] is False


def test_streamlit_is_forced_out_of_development_mode() -> None:
    options = runtime._streamlit_options(Settings(data_dir=Path("data")))
    index = options.index("--global.developmentMode")
    assert options[index + 1] == "false"


def test_launcher_binds_streamlit_to_localhost(tmp_path: Path, monkeypatch: Any) -> None:
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
