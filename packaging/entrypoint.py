from __future__ import annotations

import os
import sys
from pathlib import Path

from providency.runtime import run_engine, run_launcher, run_ui

if __name__ == "__main__":
    if getattr(sys, "frozen", False):
        bundle_dir = Path(str(sys._MEIPASS))
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(bundle_dir / "playwright-browsers"))
    if "--engine" in sys.argv:
        run_engine()
    elif "--ui" in sys.argv:
        run_ui()
    else:
        run_launcher()
