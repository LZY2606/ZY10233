"""SQLite 持久层：样品、观测权重、运行记录、人工解释分支。

规则包本身固定在代码里（app/rules.py）；数据库存的是可变状态
（样品观测的确认/降权/停用、求解运行快照、人工分支），
因此可以整库导出、清空后导入，再按快照重放复核。
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .domain import Observation
from .rules import DEFAULT_PACK, FIXTURE_SAMPLES, PACKS


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_db_path() -> str:
    return os.environ.get("POTTERY_DB_PATH", str(Path("data/pottery.db")))


SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    sample_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    material TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
    obs_id TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    feature_id TEXT NOT NULL,
    value TEXT NOT NULL,
    weight TEXT NOT NULL,
    contaminated INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (sample_id, obs_id),
    FOREIGN KEY (sample_id) REFERENCES samples(sample_id)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    sample_id TEXT NOT NULL,
    pack_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    result_json TEXT NOT NULL,
    observations_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS branches (
    branch_id TEXT PRIMARY KEY,
    sample_id TEXT NOT NULL,
    name TEXT NOT NULL,
    pack_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    observations_json TEXT NOT NULL,
    result_json TEXT NOT NULL
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


# ---------------------------------------------------------------------------
# 固定 fixture 播种 / 重置
# ---------------------------------------------------------------------------


def seed_fixtures(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM observations")
    conn.execute("DELETE FROM samples")
    for sample_id, spec in FIXTURE_SAMPLES.items():
        conn.execute(
            "INSERT INTO samples(sample_id, name, material) VALUES (?,?,?)",
            (sample_id, spec["name"], spec["material"]),
        )
        for obs in spec["observations"]:
            conn.execute(
                "INSERT INTO observations(obs_id, sample_id, feature_id, value, weight, contaminated, note)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    obs.obs_id,
                    sample_id,
                    obs.feature_id,
                    obs.value,
                    obs.weight,
                    1 if obs.contaminated else 0,
                    obs.note,
                ),
            )
    conn.commit()


def ensure_seeded(conn: sqlite3.Connection) -> None:
    n = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
    if n == 0:
        seed_fixtures(conn)


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------


def obs_from_row(row: sqlite3.Row) -> Observation:
    return Observation(
        row["obs_id"],
        row["feature_id"],
        row["value"],
        row["weight"],
        bool(row["contaminated"]),
        row["note"],
    )


def list_samples(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM samples ORDER BY sample_id")]


def get_sample(conn: sqlite3.Connection, sample_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM samples WHERE sample_id=?", (sample_id,)).fetchone()
    return dict(row) if row else None


def list_observations(conn: sqlite3.Connection, sample_id: str) -> list[Observation]:
    rows = conn.execute(
        "SELECT * FROM observations WHERE sample_id=? ORDER BY obs_id", (sample_id,)
    ).fetchall()
    return [obs_from_row(r) for r in rows]


def set_weight(conn: sqlite3.Connection, sample_id: str, obs_id: str, weight: str) -> Observation:
    cur = conn.execute(
        "UPDATE observations SET weight=? WHERE sample_id=? AND obs_id=?",
        (weight, sample_id, obs_id),
    )
    if cur.rowcount == 0:
        raise LookupError(f"observation {obs_id} not found")
    conn.commit()
    row = conn.execute(
        "SELECT * FROM observations WHERE sample_id=? AND obs_id=?", (sample_id, obs_id)
    ).fetchone()
    return obs_from_row(row)


def observations_snapshot(conn: sqlite3.Connection, sample_id: str) -> list[dict]:
    return [vars(o) for o in list_observations(conn, sample_id)]


def obs_from_snapshot(items: list[dict]) -> list[Observation]:
    return [
        Observation(
            i["obs_id"],
            i["feature_id"],
            i["value"],
            i["weight"],
            i.get("contaminated", False),
            i.get("note", ""),
        )
        for i in items
    ]


def save_run(
    conn: sqlite3.Connection,
    run_id: str,
    sample_id: str,
    pack_id: str,
    result: dict,
    snapshot: list[dict],
    note: str = "",
) -> None:
    conn.execute(
        "INSERT INTO runs(run_id, sample_id, pack_id, created_at, note, result_json, observations_json)"
        " VALUES (?,?,?,?,?,?,?)",
        (run_id, sample_id, pack_id, utc_now(), note, json.dumps(result, ensure_ascii=False),
         json.dumps(snapshot, ensure_ascii=False)),
    )
    conn.commit()


def list_runs(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT run_id, sample_id, pack_id, created_at, note FROM runs ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_run(conn: sqlite3.Connection, run_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["result"] = json.loads(d.pop("result_json"))
    d["observations"] = json.loads(d.pop("observations_json"))
    return d


def save_branch(
    conn: sqlite3.Connection,
    branch_id: str,
    sample_id: str,
    name: str,
    pack_id: str,
    snapshot: list[dict],
    result: dict,
    note: str = "",
) -> None:
    conn.execute(
        "INSERT INTO branches(branch_id, sample_id, name, pack_id, created_at, note, observations_json, result_json)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (branch_id, sample_id, name, pack_id, utc_now(), note,
         json.dumps(snapshot, ensure_ascii=False), json.dumps(result, ensure_ascii=False)),
    )
    conn.commit()


def list_branches(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT branch_id, sample_id, name, pack_id, created_at, note FROM branches ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_branch(conn: sqlite3.Connection, branch_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM branches WHERE branch_id=?", (branch_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["result"] = json.loads(d.pop("result_json"))
    d["observations"] = json.loads(d.pop("observations_json"))
    return d


# ---------------------------------------------------------------------------
# 全量导出 / 清空 / 导入
# ---------------------------------------------------------------------------


def export_all(conn: sqlite3.Connection) -> dict:
    return {
        "format": "taotai-huohou-v1",
        "exported_at": utc_now(),
        "default_pack": DEFAULT_PACK,
        "packs": sorted(PACKS.keys()),
        "samples": list_samples(conn),
        "observations": [
            dict(r)
            for r in conn.execute(
                "SELECT obs_id, sample_id, feature_id, value, weight, contaminated, note"
                " FROM observations ORDER BY sample_id, obs_id"
            )
        ],
        "runs": [
            {
                "run_id": r["run_id"],
                "sample_id": r["sample_id"],
                "pack_id": r["pack_id"],
                "created_at": r["created_at"],
                "note": r["note"],
                "result_json": r["result_json"],
                "observations_json": r["observations_json"],
            }
            for r in conn.execute("SELECT * FROM runs ORDER BY created_at")
        ],
        "branches": [
            {
                "branch_id": r["branch_id"],
                "sample_id": r["sample_id"],
                "name": r["name"],
                "pack_id": r["pack_id"],
                "created_at": r["created_at"],
                "note": r["note"],
                "observations_json": r["observations_json"],
                "result_json": r["result_json"],
            }
            for r in conn.execute("SELECT * FROM branches ORDER BY created_at")
        ],
    }


def clear_all(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM runs")
    conn.execute("DELETE FROM branches")
    conn.execute("DELETE FROM observations")
    conn.execute("DELETE FROM samples")
    conn.commit()


def import_all(conn: sqlite3.Connection, data: dict) -> dict:
    if data.get("format") != "taotai-huohou-v1":
        raise ValueError("未知导出格式（format 不匹配）")
    clear_all(conn)
    n = {"samples": 0, "observations": 0, "runs": 0, "branches": 0}
    for s in data.get("samples", []):
        conn.execute(
            "INSERT INTO samples(sample_id, name, material) VALUES (?,?,?)",
            (s["sample_id"], s["name"], s["material"]),
        )
        n["samples"] += 1
    for o in data.get("observations", []):
        conn.execute(
            "INSERT INTO observations(obs_id, sample_id, feature_id, value, weight, contaminated, note)"
            " VALUES (?,?,?,?,?,?,?)",
            (o["obs_id"], o["sample_id"], o["feature_id"], o["value"], o["weight"],
             int(o.get("contaminated", 0)), o.get("note", "")),
        )
        n["observations"] += 1
    for r in data.get("runs", []):
        conn.execute(
            "INSERT INTO runs(run_id, sample_id, pack_id, created_at, note, result_json, observations_json)"
            " VALUES (?,?,?,?,?,?,?)",
            (r["run_id"], r["sample_id"], r["pack_id"], r["created_at"], r.get("note", ""),
             r["result_json"], r["observations_json"]),
        )
        n["runs"] += 1
    for b in data.get("branches", []):
        conn.execute(
            "INSERT INTO branches(branch_id, sample_id, name, pack_id, created_at, note, observations_json, result_json)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (b["branch_id"], b["sample_id"], b["name"], b["pack_id"], b["created_at"], b.get("note", ""),
             b["observations_json"], b["result_json"]),
        )
        n["branches"] += 1
    conn.commit()
    return n
