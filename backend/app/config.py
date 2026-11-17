from __future__ import annotations

import os
from pathlib import Path

PROJECT_NAME = "Dayseal"
API_PORT = 10700

_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
# DAYSEAL_DATA lets the container (and the test suite) relocate the SQLite file.
DATA_DIR = Path(os.environ.get("DAYSEAL_DATA") or _DEFAULT_DATA_DIR)
DB_PATH = DATA_DIR / "dayseal.db"
