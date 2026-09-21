"""陶胎火候辨证台 - SQLite 存储与事件重放层。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
FIXTURE_DIR = ROOT / "fixtures"
DEFAULT_DB = ROOT / "taotai.db"


def now_ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def connect(db_path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS packs (
            pack_id TEXT PRIMARY KEY,
            version TEXT NOT NULL,
            label TEXT NOT NULL,
            payload TEXT NOT NULL,
            is_fixture INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            material TEXT NOT NULL,
            pack_version TEXT NOT NULL,
            is_fixture INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS branches (
            run_id TEXT NOT NULL,
            branch TEXT NOT NULL,
            parent_branch TEXT,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, branch)
        );
        CREATE TABLE IF NOT EXISTS observations (
            run_id TEXT NOT NULL,
            branch TEXT NOT NULL,
            obs_id TEXT NOT NULL,
            feature_key TEXT NOT NULL,
            op TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            contaminated INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, branch, obs_id)
        );
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            branch TEXT NOT NULL,
            seq INTEGER NOT NULL,
            ts TEXT NOT NULL,
            actor TEXT NOT NULL DEFAULT 'user',
            kind TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS events_run_branch ON events(run_id, branch, seq);
        """
    )
    conn.commit()


# ---------- 规则包 ----------

def upsert_pack(conn: sqlite3.Connection, pack: dict[str, Any],
                is_fixture: bool = False) -> None:
    conn.execute(
        "INSERT INTO packs(pack_id, version, label, payload, is_fixture) "
        "VALUES(?,?,?,?,?) ON CONFLICT(pack_id) DO UPDATE SET "
        "version=excluded.version, label=excluded.label, payload=excluded.payload, "
        "is_fixture=excluded.is_fixture",
        (pack["pack_id"], pack["version"], pack["label"],
         json.dumps(pack, ensure_ascii=False, sort_keys=True),
         1 if is_fixture else 0),
    )
    conn.commit()


def get_pack(conn: sqlite3.Connection, pack_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT payload FROM packs WHERE pack_id=?", (pack_id,)).fetchone()
    if row is None:
        raise KeyError(f"unknown pack: {pack_id}")
    return json.loads(row["payload"])


def get_pack_by_version(conn: sqlite3.Connection, version: str) -> dict[str, Any]:
    row = conn.execute("SELECT payload FROM packs WHERE version=?", (version,)).fetchone()
    if row is None:
        raise KeyError(f"unknown rule version: {version}")
    return json.loads(row["payload"])


def list_packs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT pack_id, version, label, is_fixture FROM packs ORDER BY version"
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- 事件日志 ----------

def _next_seq(conn: sqlite3.Connection, run_id: str, branch: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) AS s FROM events WHERE run_id=? AND branch=?",
        (run_id, branch),
    ).fetchone()
    return int(row["s"]) + 1


def log_event(conn: sqlite3.Connection, run_id: str, branch: str, kind: str,
              payload: dict[str, Any], actor: str = "user", commit: bool = True) -> dict[str, Any]:
    event = {
        "event_id": uuid.uuid4().hex,
        "run_id": run_id,
        "branch": branch,
        "seq": _next_seq(conn, run_id, branch),
        "ts": now_ts(),
        "actor": actor,
        "kind": kind,
        "payload": payload,
    }
    conn.execute(
        "INSERT INTO events(event_id, run_id, branch, seq, ts, actor, kind, payload) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (event["event_id"], run_id, branch, event["seq"], event["ts"], actor,
         kind, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
    )
    if commit:
        conn.commit()
    return event


def list_events(conn: sqlite3.Connection, run_id: str, branch: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM events WHERE run_id=? AND branch=? ORDER BY seq",
        (run_id, branch),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        out.append(d)
    return out


# ---------- 状态读取 ----------

def list_runs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT run_id, name, material, pack_version, is_fixture, created_at "
        "FROM runs ORDER BY is_fixture DESC, created_at"
    ).fetchall()
    return [dict(r) for r in rows]


def _require_run(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(f"unknown run: {run_id}")
    return row


def get_run_state(conn: sqlite3.Connection, run_id: str, branch: str) -> dict[str, Any]:
    run = _require_run(conn, run_id)
    brow = conn.execute(
        "SELECT note, parent_branch, created_at FROM branches WHERE run_id=? AND branch=?",
        (run_id, branch),
    ).fetchone()
    if brow is None:
        raise KeyError(f"unknown branch: {run_id}/{branch}")
    obs_rows = conn.execute(
        "SELECT obs_id, feature_key, op, weight, contaminated, note, created_at "
        "FROM observations WHERE run_id=? AND branch=? ORDER BY created_at, obs_id",
        (run_id, branch),
    ).fetchall()
    observations = []
    for r in obs_rows:
        d = dict(r)
        d["id"] = d.pop("obs_id")
        d["contaminated"] = bool(d["contaminated"])
        observations.append(d)
    return {
        "run_id": run_id,
        "name": run["name"],
        "material": run["material"],
        "pack_version": run["pack_version"],
        "is_fixture": bool(run["is_fixture"]),
        "branch": branch,
        "branch_note": brow["note"],
        "parent_branch": brow["parent_branch"],
        "branch_created_at": brow["created_at"],
        "observations": observations,
    }


def list_branches(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    _require_run(conn, run_id)
    rows = conn.execute(
        "SELECT branch, parent_branch, note, created_at FROM branches "
        "WHERE run_id=? ORDER BY CASE branch WHEN 'main' THEN 0 ELSE 1 END, created_at",
        (run_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- 变更操作（均落事件日志） ----------

def create_run(conn: sqlite3.Connection, run_id: str, name: str, material: str,
               pack_version: str, branch_note: str = "") -> None:
    if conn.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone():
        raise ValueError(f"run already exists: {run_id}")
    get_pack_by_version(conn, pack_version)
    ts = now_ts()
    conn.execute(
        "INSERT INTO runs(run_id, name, material, pack_version, is_fixture, created_at) "
        "VALUES(?,?,?,?,0,?)",
        (run_id, name, material, pack_version, ts),
    )
    conn.execute(
        "INSERT INTO branches(run_id, branch, parent_branch, note, created_at) "
        "VALUES(?, 'main', NULL, ?, ?)",
        (run_id, branch_note, ts),
    )
    log_event(conn, run_id, "main", "create_run",
              {"name": name, "material": material, "pack_version": pack_version,
               "branch_note": branch_note},
              commit=False)
    conn.commit()


def switch_pack(conn: sqlite3.Connection, run_id: str, branch: str,
                pack_version: str) -> None:
    get_pack_by_version(conn, pack_version)
    _require_run(conn, run_id)
    conn.execute("UPDATE runs SET pack_version=? WHERE run_id=?", (pack_version, run_id))
    log_event(conn, run_id, branch, "switch_pack", {"pack_version": pack_version})


def _upsert_observation_row(conn: sqlite3.Connection, run_id: str, branch: str,
                            obs: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO observations(run_id, branch, obs_id, feature_key, op, weight, "
        "contaminated, note, created_at) VALUES(?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(run_id, branch, obs_id) DO UPDATE SET "
        "feature_key=excluded.feature_key, op=excluded.op, weight=excluded.weight, "
        "contaminated=excluded.contaminated, note=excluded.note",
        (run_id, branch, obs["id"], obs["feature_key"], obs.get("op", "present"),
         float(obs.get("weight", 1.0)), 1 if obs.get("contaminated") else 0,
         obs.get("note", ""), now_ts()),
    )


def set_observation(conn: sqlite3.Connection, run_id: str, branch: str,
                    obs: dict[str, Any]) -> None:
    _require_run(conn, run_id)
    _upsert_observation_row(conn, run_id, branch, obs)
    log_event(conn, run_id, branch, "set_observation",
              {"observation": _obs_event_payload(obs)})


def _obs_event_payload(obs: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": obs["id"],
        "feature_key": obs["feature_key"],
        "op": obs.get("op", "present"),
        "weight": float(obs.get("weight", 1.0)),
        "contaminated": bool(obs.get("contaminated", False)),
        "note": obs.get("note", ""),
    }


def set_weight(conn: sqlite3.Connection, run_id: str, branch: str, obs_id: str,
               weight: float) -> None:
    row = conn.execute(
        "SELECT 1 FROM observations WHERE run_id=? AND branch=? AND obs_id=?",
        (run_id, branch, obs_id),
    ).fetchone()
    if row is None:
        raise KeyError(f"unknown observation: {obs_id}")
    conn.execute(
        "UPDATE observations SET weight=? WHERE run_id=? AND branch=? AND obs_id=?",
        (float(weight), run_id, branch, obs_id),
    )
    log_event(conn, run_id, branch, "set_weight",
              {"observation_id": obs_id, "weight": float(weight)})


def mark_contamination(conn: sqlite3.Connection, run_id: str, branch: str,
                       obs_id: str, contaminated: bool) -> None:
    conn.execute(
        "UPDATE observations SET contaminated=? WHERE run_id=? AND branch=? AND obs_id=?",
        (1 if contaminated else 0, run_id, branch, obs_id),
    )
    log_event(conn, run_id, branch, "mark_contamination",
              {"observation_id": obs_id, "contaminated": bool(contaminated)})


def remove_observation(conn: sqlite3.Connection, run_id: str, branch: str,
                       obs_id: str) -> None:
    conn.execute(
        "DELETE FROM observations WHERE run_id=? AND branch=? AND obs_id=?",
        (run_id, branch, obs_id),
    )
    log_event(conn, run_id, branch, "remove_observation", {"observation_id": obs_id})


def create_branch(conn: sqlite3.Connection, run_id: str, new_branch: str,
                  from_branch: str, note: str = "") -> None:
    _require_run(conn, run_id)
    if conn.execute(
        "SELECT 1 FROM branches WHERE run_id=? AND branch=?", (run_id, new_branch)
    ).fetchone():
        raise ValueError(f"branch already exists: {new_branch}")
    src = conn.execute(
        "SELECT 1 FROM branches WHERE run_id=? AND branch=?", (run_id, from_branch)
    ).fetchone()
    if src is None:
        raise KeyError(f"unknown source branch: {from_branch}")
    ts = now_ts()
    conn.execute(
        "INSERT INTO branches(run_id, branch, parent_branch, note, created_at) "
        "VALUES(?,?,?,?,?)",
        (run_id, new_branch, from_branch, note, ts),
    )
    conn.execute(
        "INSERT INTO observations(run_id, branch, obs_id, feature_key, op, weight, "
        "contaminated, note, created_at) "
        "SELECT ?, ?, obs_id, feature_key, op, weight, contaminated, note, created_at "
        "FROM observations WHERE run_id=? AND branch=?",
        (run_id, new_branch, run_id, from_branch),
    )
    log_event(conn, run_id, new_branch, "create_branch",
              {"from_branch": from_branch, "note": note}, commit=False)
    log_event(conn, run_id, from_branch, "fork_branch",
              {"new_branch": new_branch, "note": note})
    conn.commit()


# ---------- 固定 fixture ----------

def load_fixture_packs() -> list[dict[str, Any]]:
    packs = []
    for path in sorted(FIXTURE_DIR.glob("rulepack_*.json")):
        packs.append(json.loads(path.read_text(encoding="utf-8")))
    return packs


def load_fixture_runs() -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / "demo_runs.json").read_text(encoding="utf-8"))


def seed_fixtures(conn: sqlite3.Connection) -> None:
    """幂等写入固定规则包与演示 run。"""
    for pack in load_fixture_packs():
        upsert_pack(conn, pack, is_fixture=True)
    data = load_fixture_runs()
    for run in data["runs"]:
        exists = conn.execute(
            "SELECT 1 FROM runs WHERE run_id=?", (run["run_id"],)
        ).fetchone()
        if exists:
            continue
        ts = now_ts()
        conn.execute(
            "INSERT INTO runs(run_id, name, material, pack_version, is_fixture, created_at) "
            "VALUES(?,?,?,?,1,?)",
            (run["run_id"], run["name"], run["material"], run["pack_version"], ts),
        )
        conn.execute(
            "INSERT INTO branches(run_id, branch, parent_branch, note, created_at) "
            "VALUES(?, 'main', NULL, ?, ?)",
            (run["run_id"], run.get("note", ""), ts),
        )
        for obs in run["observations"]:
            _upsert_observation_row(conn, run["run_id"], "main", obs)
        log_event(
            conn, run["run_id"], "main", "seed_fixture",
            {"name": run["name"], "material": run["material"],
             "pack_version": run["pack_version"],
             "observations": [_obs_event_payload(o) for o in run["observations"]],
             "note": run.get("note", "")},
            commit=False,
        )
    conn.commit()


# ---------- 清空 ----------

def clear_all(conn: sqlite3.Connection, keep_packs: bool = True) -> None:
    """删除全部 run/分支/观测/事件。keep_packs=True 时保留规则包。"""
    conn.execute("DELETE FROM events")
    conn.execute("DELETE FROM observations")
    conn.execute("DELETE FROM branches")
    conn.execute("DELETE FROM runs")
    if not keep_packs:
        conn.execute("DELETE FROM packs")
    conn.commit()


def clear_runtime_runs(conn: sqlite3.Connection) -> None:
    """只删除用户自建 run，保留固定演示与规则包，随后重新种入 fixture。"""
    conn.execute("DELETE FROM events WHERE run_id IN (SELECT run_id FROM runs WHERE is_fixture=0)")
    conn.execute("DELETE FROM observations WHERE run_id IN (SELECT run_id FROM runs WHERE is_fixture=0)")
    conn.execute("DELETE FROM branches WHERE run_id IN (SELECT run_id FROM runs WHERE is_fixture=0)")
    conn.execute("DELETE FROM runs WHERE is_fixture=0")
    conn.commit()
    for vid in [r["pack_id"] for r in list_packs(conn)]:
        pass
    seed_fixtures(conn)


# ---------- 事件重放 ----------

def _delete_run_everything(conn: sqlite3.Connection, run_id: str) -> None:
    conn.execute("DELETE FROM events WHERE run_id=?", (run_id,))
    conn.execute("DELETE FROM observations WHERE run_id=?", (run_id,))
    conn.execute("DELETE FROM branches WHERE run_id=?", (run_id,))
    conn.execute("DELETE FROM runs WHERE run_id=?", (run_id,))


def replay_run(conn: sqlite3.Connection, run_record: dict[str, Any],
               events: list[dict[str, Any]]) -> None:
    """从 create_run/seed_fixture 事件开始重放单个 run 的完整历史。"""
    _delete_run_everything(conn, run_record["run_id"])
    for event in sorted(events, key=lambda e: (e["branch"], e["seq"])):
        kind = event["kind"]
        payload = event["payload"]
        if kind in ("create_run", "seed_fixture"):
            ts = event["ts"]
            conn.execute(
                "INSERT INTO runs(run_id, name, material, pack_version, is_fixture, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (run_record["run_id"], payload["name"], payload["material"],
                 payload["pack_version"], 1 if kind == "seed_fixture" else 0, ts),
            )
            conn.execute(
                "INSERT INTO branches(run_id, branch, parent_branch, note, created_at) "
                "VALUES(?, 'main', NULL, ?, ?)",
                (run_record["run_id"], payload.get("note", ""), ts),
            )
            for obs in payload.get("observations", []):
                _upsert_observation_row(conn, run_record["run_id"], "main", obs)
        elif kind == "switch_pack":
            conn.execute(
                "UPDATE runs SET pack_version=? WHERE run_id=?",
                (payload["pack_version"], run_record["run_id"]),
            )
        elif kind == "set_observation":
            _upsert_observation_row(conn, run_record["run_id"], event["branch"],
                                    payload["observation"])
        elif kind == "set_weight":
            conn.execute(
                "UPDATE observations SET weight=? WHERE run_id=? AND branch=? AND obs_id=?",
                (float(payload["weight"]), run_record["run_id"], event["branch"],
                 payload["observation_id"]),
            )
        elif kind == "mark_contamination":
            conn.execute(
                "UPDATE observations SET contaminated=? WHERE run_id=? AND branch=? AND obs_id=?",
                (1 if payload["contaminated"] else 0, run_record["run_id"],
                 event["branch"], payload["observation_id"]),
            )
        elif kind == "remove_observation":
            conn.execute(
                "DELETE FROM observations WHERE run_id=? AND branch=? AND obs_id=?",
                (run_record["run_id"], event["branch"], payload["observation_id"]),
            )
        elif kind == "create_branch":
            ts = event["ts"]
            conn.execute(
                "INSERT INTO branches(run_id, branch, parent_branch, note, created_at) "
                "VALUES(?,?,?,?,?)",
                (run_record["run_id"], event["branch"], payload["from_branch"],
                 payload.get("note", ""), ts),
            )
            conn.execute(
                "INSERT INTO observations(run_id, branch, obs_id, feature_key, op, weight, "
                "contaminated, note, created_at) "
                "SELECT ?, ?, obs_id, feature_key, op, weight, contaminated, note, created_at "
                "FROM observations WHERE run_id=? AND branch=?",
                (run_record["run_id"], event["branch"], run_record["run_id"],
                 payload["from_branch"]),
            )
        elif kind == "fork_branch":
            continue
        else:
            raise ValueError(f"unknown event kind during replay: {kind}")
        conn.execute(
            "INSERT OR REPLACE INTO events(event_id, run_id, branch, seq, ts, actor, kind, payload) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (event["event_id"], run_record["run_id"], event["branch"], event["seq"],
             event["ts"], event.get("actor", "user"), kind,
             json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        )
    conn.commit()


# ---------- 导出 / 导入复核 ----------

def _canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def state_hash(conn: sqlite3.Connection) -> str:
    """对全部 run 的分支状态与事件做规范化哈希，供清空-重导入后比对。"""
    digest = hashlib.sha256()
    run_ids = [r["run_id"] for r in list_runs(conn)]
    for run_id in sorted(run_ids):
        run = conn.execute(
            "SELECT run_id, name, material, pack_version, is_fixture FROM runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        digest.update(_canonical(dict(run)).encode("utf-8"))
        for branch in list_branches(conn, run_id):
            state = get_run_state(conn, run_id, branch["branch"])
            compact = {
                "branch": state["branch"],
                "parent_branch": state["parent_branch"],
                "branch_note": state["branch_note"],
                "material": state["material"],
                "pack_version": state["pack_version"],
                "observations": [
                    {"id": o["id"], "feature_key": o["feature_key"], "op": o["op"],
                     "weight": round(float(o["weight"]), 6),
                     "contaminated": o["contaminated"], "note": o["note"]}
                    for o in state["observations"]
                ],
            }
            digest.update(_canonical(compact).encode("utf-8"))
        for branch in list_branches(conn, run_id):
            events = list_events(conn, run_id, branch["branch"])
            digest.update(_canonical([
                {"seq": e["seq"], "kind": e["kind"], "payload": e["payload"]}
                for e in events
            ]).encode("utf-8"))
    return digest.hexdigest()


def export_bundle(conn: sqlite3.Connection, only_fixtures: bool = False) -> dict[str, Any]:
    packs = [json.loads(r["payload"]) for r in conn.execute(
        "SELECT payload FROM packs ORDER BY version").fetchall()]
    query = "SELECT run_id FROM runs ORDER BY created_at"
    if only_fixtures:
        query = "SELECT run_id FROM runs WHERE is_fixture=1 ORDER BY created_at"
    run_ids = [r["run_id"] for r in conn.execute(query).fetchall()]
    run_records = []
    for run_id in run_ids:
        run = _require_run(conn, run_id)
        branches = list_branches(conn, run_id)
        branch_states = {}
        events = {}
        for b in branches:
            name = b["branch"]
            branch_states[name] = get_run_state(conn, run_id, name)
            events[name] = list_events(conn, run_id, name)
        run_records.append({
            "run": {
                "run_id": run["run_id"], "name": run["name"],
                "material": run["material"], "pack_version": run["pack_version"],
                "is_fixture": bool(run["is_fixture"]), "created_at": run["created_at"],
            },
            "branches": branch_states,
            "events": events,
        })
    bundle = {
        "format": "taotai-bundle/1",
        "exported_at": now_ts(),
        "state_hash": state_hash(conn),
        "packs": packs,
        "runs": run_records,
    }
    return bundle


def import_bundle(conn: sqlite3.Connection, bundle: dict[str, Any],
                  replace: bool = True) -> dict[str, Any]:
    """导入导出包：先重放事件，再比对结构状态哈希。"""
    if bundle.get("format") != "taotai-bundle/1":
        raise ValueError("unsupported bundle format")
    for pack in bundle["packs"]:
        upsert_pack(conn, pack, is_fixture=False)
    if replace:
        ids = [rec["run"]["run_id"] for rec in bundle["runs"]]
        for run_id in ids:
            _delete_run_everything(conn, run_id)
    for rec in bundle["runs"]:
        all_events = []
        for branch_events in rec["events"].values():
            all_events.extend(branch_events)
        replay_run(conn, rec["run"], all_events)
    actual_hash = state_hash(conn)
    return {
        "expected_hash": bundle["state_hash"],
        "actual_hash": actual_hash,
        "hash_match": actual_hash == bundle["state_hash"],
        "imported_runs": [rec["run"]["run_id"] for rec in bundle["runs"]],
    }
