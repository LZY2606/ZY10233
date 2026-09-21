"""陶胎火候辨证台 - FastAPI 本地服务。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import engine
import store

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("TAOTAI_DB", str(store.DEFAULT_DB)))


def get_conn() -> store.sqlite3.Connection:
    conn = store.connect(DB_PATH)
    return conn


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = get_conn()
    try:
        store.init_db(conn)
        store.seed_fixtures(conn)
    finally:
        conn.close()
    yield


app = FastAPI(title="陶胎火候辨证台", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(str(ROOT / "static" / "index.html"))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------- 分析辅助 ----------

def _analyze_branch(conn: store.sqlite3.Connection, run_id: str,
                    branch: str) -> dict[str, Any]:
    state = store.get_run_state(conn, run_id, branch)
    pack = store.get_pack_by_version(conn, state["pack_version"])
    result = engine.analyze(pack, state["material"], state["observations"])
    feature_index = {
        f["key"]: {"label": f["label"], "contamination_note": f.get("contamination_note", "")}
        for f in pack["features"]
    }
    result["state"] = state
    result["branches"] = store.list_branches(conn, run_id)
    result["events"] = store.list_events(conn, run_id, branch)
    result["feature_index"] = feature_index
    return result


# ---------- 规则包 ----------

@app.get("/api/packs")
def api_packs() -> dict[str, Any]:
    conn = get_conn()
    try:
        packs = store.list_packs(conn)
        current = None
        out = []
        for p in packs:
            payload = store.get_pack(conn, p["pack_id"])
            out.append({
                "pack_id": p["pack_id"], "version": p["version"],
                "label": p["label"], "is_fixture": bool(p["is_fixture"]),
                "description": payload.get("description", ""),
                "grid": payload["grid"],
                "features": payload["features"],
            })
        return {"packs": out}
    finally:
        conn.close()


# ---------- Runs ----------

@app.get("/api/runs")
def api_runs() -> dict[str, Any]:
    conn = get_conn()
    try:
        return {"runs": store.list_runs(conn)}
    finally:
        conn.close()


class CreateRunIn(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)
    name: str
    material: str
    pack_version: str = "v1"
    branch_note: str = ""


@app.post("/api/runs")
def api_create_run(body: CreateRunIn) -> dict[str, Any]:
    conn = get_conn()
    try:
        if body.material not in engine.MATERIALS:
            raise HTTPException(400, f"material must be one of {sorted(engine.MATERIALS)}")
        try:
            store.create_run(conn, body.run_id, body.name, body.material,
                             body.pack_version, body.branch_note)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        except KeyError as exc:
            raise HTTPException(400, str(exc))
        return {"ok": True, "run_id": body.run_id}
    finally:
        conn.close()


@app.get("/api/runs/{run_id}")
def api_run_detail(run_id: str, branch: str = "main") -> dict[str, Any]:
    conn = get_conn()
    try:
        return _analyze_branch(conn, run_id, branch)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    finally:
        conn.close()


class SwitchPackIn(BaseModel):
    pack_version: str


@app.post("/api/runs/{run_id}/pack")
def api_switch_pack(run_id: str, body: SwitchPackIn, branch: str = "main") -> dict[str, Any]:
    conn = get_conn()
    try:
        store.switch_pack(conn, run_id, branch, body.pack_version)
        return _analyze_branch(conn, run_id, branch)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    finally:
        conn.close()


# ---------- 分支 ----------

class BranchIn(BaseModel):
    new_branch: str
    from_branch: str = "main"
    note: str = ""


@app.post("/api/runs/{run_id}/branches")
def api_create_branch(run_id: str, body: BranchIn) -> dict[str, Any]:
    conn = get_conn()
    try:
        store.create_branch(conn, run_id, body.new_branch, body.from_branch, body.note)
        return {"ok": True, "branches": store.list_branches(conn, run_id)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(400 if isinstance(exc, ValueError) else 404, str(exc))
    finally:
        conn.close()


# ---------- 观测 ----------

class ObservationIn(BaseModel):
    id: str
    feature_key: str
    op: str = "present"
    weight: float = 1.0
    contaminated: bool = False
    note: str = ""


@app.post("/api/runs/{run_id}/observations")
def api_set_observation(run_id: str, body: ObservationIn,
                        branch: str = "main") -> dict[str, Any]:
    conn = get_conn()
    try:
        if body.op not in ("present", "absent"):
            raise HTTPException(400, "op must be present or absent")
        if not 0.0 <= body.weight <= 1.0:
            raise HTTPException(400, "weight must be within [0, 1]")
        store.set_observation(conn, run_id, branch, body.model_dump())
        return _analyze_branch(conn, run_id, branch)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    finally:
        conn.close()


class WeightIn(BaseModel):
    weight: float


@app.post("/api/runs/{run_id}/observations/{obs_id}/weight")
def api_set_weight(run_id: str, obs_id: str, body: WeightIn,
                   branch: str = "main") -> dict[str, Any]:
    conn = get_conn()
    try:
        if not 0.0 <= body.weight <= 1.0:
            raise HTTPException(400, "weight must be within [0, 1]")
        store.set_weight(conn, run_id, branch, obs_id, body.weight)
        return _analyze_branch(conn, run_id, branch)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    finally:
        conn.close()


class ContaminationIn(BaseModel):
    contaminated: bool


@app.post("/api/runs/{run_id}/observations/{obs_id}/contamination")
def api_mark_contamination(run_id: str, obs_id: str, body: ContaminationIn,
                           branch: str = "main") -> dict[str, Any]:
    conn = get_conn()
    try:
        store.mark_contamination(conn, run_id, branch, obs_id, body.contaminated)
        return _analyze_branch(conn, run_id, branch)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    finally:
        conn.close()


@app.delete("/api/runs/{run_id}/observations/{obs_id}")
def api_remove_observation(run_id: str, obs_id: str,
                           branch: str = "main") -> dict[str, Any]:
    conn = get_conn()
    try:
        store.remove_observation(conn, run_id, branch, obs_id)
        return _analyze_branch(conn, run_id, branch)
    finally:
        conn.close()


# ---------- 证据排除区域查询 ----------

class EvidenceMaskIn(BaseModel):
    feature_key: str
    op: str = "present"


@app.post("/api/runs/{run_id}/evidence-mask")
def api_evidence_mask(run_id: str, body: EvidenceMaskIn,
                      branch: str = "main") -> dict[str, Any]:
    conn = get_conn()
    try:
        state = store.get_run_state(conn, run_id, branch)
        pack = store.get_pack_by_version(conn, state["pack_version"])
        grid = engine.build_grid(pack)
        excluded = engine.evidence_excluded_mask(
            pack, grid.shape, state["material"], body.feature_key, body.op
        )
        return {
            "feature_key": body.feature_key, "op": body.op,
            "excluded": excluded,
            "grid": {
                "temps": [int(t) for t in grid.temps],
                "atmospheres": [a for a in grid.atmo_ids],
                "hold_times": [t for t in grid.time_ids],
            },
        }
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    finally:
        conn.close()


# ---------- 导出 / 导入 / 清空 ----------

@app.get("/api/export")
def api_export(only_fixtures: bool = False) -> dict[str, Any]:
    conn = get_conn()
    try:
        return store.export_bundle(conn, only_fixtures=only_fixtures)
    finally:
        conn.close()


class ImportIn(BaseModel):
    bundle: dict[str, Any]
    replace: bool = True


@app.post("/api/import")
def api_import(body: ImportIn) -> dict[str, Any]:
    conn = get_conn()
    try:
        return store.import_bundle(conn, body.bundle, replace=body.replace)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


class AdminIn(BaseModel):
    reseed: bool = True
    keep_packs: bool = True


@app.post("/api/admin/clear")
def api_admin_clear(body: AdminIn) -> dict[str, Any]:
    conn = get_conn()
    try:
        store.clear_all(conn, keep_packs=body.keep_packs)
        if body.reseed:
            store.seed_fixtures(conn)
        return {"ok": True, "state_hash": store.state_hash(conn),
                "runs": store.list_runs(conn)}
    finally:
        conn.close()
