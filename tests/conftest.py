import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test_pottery.db"
    monkeypatch.setenv("POTTERY_DB_PATH", str(db_file))
    # app.main 在导入时读取默认路径；重新加载以拿到临时库
    import importlib

    import app.db as dbmod
    import app.main as main

    importlib.reload(dbmod)
    importlib.reload(main)
    from fastapi.testclient import TestClient

    with TestClient(main.app) as c:
        yield c
