"""Compatibility entry point for the current dual-database initializer.

Preferred command (from repository root):
    backend/.venv/Scripts/python.exe database/bootstrap.py
"""

from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    bootstrap = Path(__file__).resolve().parents[2] / "database" / "bootstrap.py"
    runpy.run_path(str(bootstrap), run_name="__main__")
