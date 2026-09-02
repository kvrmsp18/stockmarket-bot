from __future__ import annotations

import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
runpy.run_path(str(ROOT / "pages" / "01_Dhan_Auth_Request_Diagnostic.py"), run_name="__main__")
