import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import store


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test_taotai.db"
    monkeypatch.setenv("TAOTAI_DB", str(db_path))

    import importlib
    import app as app_module
    importlib.reload(app_module)
    app_module.DB_PATH = db_path

    from fastapi.testclient import TestClient
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture()
def packs():
    import json
    return {
        "v1": json.loads((ROOT / "fixtures" / "rulepack_v1.json").read_text("utf-8")),
        "v2": json.loads((ROOT / "fixtures" / "rulepack_v2.json").read_text("utf-8")),
    }
