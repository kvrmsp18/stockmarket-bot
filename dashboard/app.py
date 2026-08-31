"""Streamlit entry point for deployments configured to dashboard/app.py.

The Streamlit app is implemented in the repository-root app.py.  Do not use
``import app`` here: when Streamlit executes dashboard/app.py directly, Python
can resolve that import back to this dashboard/app.py and never execute the
real root application, resulting in a blank page.
"""

from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parent.parent

# Ensure imports made by the real application resolve from the repository root.
root_str = str(ROOT)
if root_str not in sys.path:
    sys.path.insert(0, root_str)

# Execute the actual Streamlit application as the main module.
runpy.run_path(str(ROOT / "app.py"), run_name="__main__")
