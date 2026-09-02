from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

runpy.run_path(str(ROOT / "pages" / "04_Dhan_Official_SDK_Auth_Diagnostic.py"), run_name="__main__")
