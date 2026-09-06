from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "package"
DIST = ROOT / "dist"
RELEASE = ROOT / "release" / "Providency"


def artifact_name(platform: str = sys.platform) -> str:
    return "Providency MAC" if platform == "darwin" else "Providency WIN"


def pyinstaller_command(platform: str = sys.platform, *, clean: bool = True) -> list[str]:
    separator = ";" if platform == "win32" else ":"
    name = artifact_name(platform)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--windowed",
        "--onefile",
        "--name",
        name,
        "--paths",
        str(ROOT / "src"),
        "--collect-all",
        "streamlit",
        "--collect-all",
        "playwright",
        "--hidden-import",
        "providency.ui_model",
        "--add-data",
        f"{ROOT / 'patterns'}{separator}providency/pattern_catalog",
        "--add-data",
        f"{BUILD / 'playwright-browsers'}{separator}playwright-browsers",
        "--add-data",
        f"{ROOT / 'assets'}{separator}assets",
        "--add-data",
        f"{ROOT / 'src' / 'providency' / 'ui.py'}{separator}providency",
        "--distpath",
        str(DIST),
        "--workpath",
        str(BUILD / "pyinstaller"),
        str(ROOT / "packaging" / "entrypoint.py"),
    ]
    if clean:
        command.insert(4, "--clean")
    return command


def build(*, clean: bool = True) -> Path:
    BUILD.mkdir(parents=True, exist_ok=True)
    browser_dir = BUILD / "playwright-browsers"
    environment = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(browser_dir)}
    if not any(browser_dir.glob("chromium-*")):
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
            cwd=ROOT,
            env=environment,
        )
    subprocess.run(pyinstaller_command(clean=clean), check=True, cwd=ROOT, env=environment)
    RELEASE.mkdir(parents=True, exist_ok=True)
    built = DIST / ("Providency MAC.app" if sys.platform == "darwin" else "Providency WIN.exe")
    destination = RELEASE / built.name
    if built.is_dir():
        shutil.copytree(built, destination, dirs_exist_ok=True)
    else:
        shutil.copy2(built, destination)
    shutil.copytree(ROOT / "assets", RELEASE / "assets", dirs_exist_ok=True)
    shutil.copy2(ROOT / "packaging" / "README.txt", RELEASE / "README.txt")
    return RELEASE


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the native Providency release package.")
    parser.add_argument("--plan", action="store_true", help="Print the build command only.")
    parser.add_argument(
        "--incremental", action="store_true", help="Reuse prior PyInstaller analysis."
    )
    args = parser.parse_args()
    if args.plan:
        print(" ".join(pyinstaller_command()))
        return
    print(build(clean=not args.incremental))


if __name__ == "__main__":
    main()
