from __future__ import annotations

import importlib.util
from pathlib import Path


def _build_module():
    path = Path(__file__).parents[1] / "packaging" / "build_release.py"
    specification = importlib.util.spec_from_file_location("providency_build_release", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_native_artifact_names_follow_final_package_contract() -> None:
    module = _build_module()
    assert module.artifact_name("win32") == "Providency WIN"
    assert module.artifact_name("darwin") == "Providency MAC"


def test_package_build_bundles_streamlit_playwright_browser_and_assets() -> None:
    module = _build_module()
    command = module.pyinstaller_command("win32")
    rendered = " ".join(command)
    assert "--onefile" in command
    assert "--windowed" in command
    assert "streamlit" in command
    assert "providency.ui_model" in command
    assert "playwright-browsers" in rendered
    assert "providency.svg" not in rendered
    assert "assets" in rendered


def test_release_readme_documents_single_instance_and_safe_default() -> None:
    readme = (Path(__file__).parents[1] / "packaging" / "README.txt").read_text(encoding="utf-8")
    assert "um único Core Engine" in readme
    assert "DRY_RUN" in readme
