from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Must run before `app.*` is imported: config resolves DATA_DIR at import time.
os.environ["DAYSEAL_DATA"] = tempfile.mkdtemp(prefix="dayseal-test-")

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest  # noqa: E402

from app import seed  # noqa: E402
from app.config import DB_PATH  # noqa: E402


@pytest.fixture(scope="session")
def seeded_template(tmp_path_factory) -> Path:
    """Seed once, then reuse the file — the DDL script is slow on this FS."""
    seed.seed_if_empty()
    template = tmp_path_factory.getbasetemp() / "dayseal-template.db"
    if template.exists():
        template.unlink()
    template.write_bytes(DB_PATH.read_bytes())
    return template
