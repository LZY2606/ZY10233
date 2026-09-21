"""陶胎火候辨证台 HTTP 服务。"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db as dbmod
from . import services

app = FastAPI(title="陶胎火候辨证台", version="1.0")

DB_PATH = dbmod.default_db_path()


def get_conn():
    conn = dbmod.connect(DB_PATH)
    dbmod.ensure_seeded(conn)
    return conn


STATIC_DIR = Path(__file__).parent / "static"


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/api/meta")
def meta():
    conn = get_conn()
    try:
        return {
            "title": "陶胎火候辨证台",
            "packs": services.pack_catalog(),
            "catalog": services.feature_catalog(),
            "samples": dbmod.list_samples(conn),
        }
    finally:
        conn.close()


@app.get("/api/samples/{sample_id}")
def sample_detail(sample_id: str):
    conn = get_conn()
    try:
        sample = dbmod.get_sample(conn, sample_id)
        if not sample:
            raise HTTPException(404, "样品不存在")
        return {
            "sample": sample,
            "observations": [vars(o) for o in dbmod.list_observations(conn, sample_id)],
        }
    finally:
        conn.close()


class WeightBody(BaseModel):
    weight: str = Field(pattern="^(hard|soft|off)$")


@app.post("/api/samples/{sample_id}/observations/{obs_id}/weight")
def update_weight(sample_id: str, obs_id: str, body: WeightBody):
    conn = get_conn()
    try:
        obs = dbmod.set_weight(conn, sample_id, obs_id, body.weight)
        return vars(obs)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    finally:
        conn.close()


class SolveBody(BaseModel):
    pack_id: str | None = None
    # 允许临时假设（不写库）：每项需给 obs_id + weight
    provisional: list[dict] | None = None
    save: bool = False
    note: str = ""


@app.post("/api/samples/{sample_id}/solve")
def solve_sample(sample_id: str, body: SolveBody):
    conn = get_conn()
    try:
        observations = dbmod.list_observations(conn, sample_id)
        if body.provisional is not None:
            by_id = {o.obs_id: o for o in observations}
            overrides = {item["obs_id"]: item.get("weight", "hard") for item in body.provisional}
            for obs_id, w in overrides.items():
                if obs_id not in by_id:
                    raise HTTPException(400, f"未知观测 {obs_id}")
                if w not in ("hard", "soft", "off"):
                    raise HTTPException(400, f"非法权重 {w}")
            observations = [
                type(o)(o.obs_id, o.feature_id, o.value, overrides.get(o.obs_id, o.weight), o.contaminated, o.note)
                for o in observations
            ]
        pack_id = body.pack_id or "v1"
        try:
            services.get_pack(pack_id)
        except KeyError as exc:
            raise HTTPException(400, str(exc)) from exc
        computed = services.compute(conn, sample_id, pack_id, observations)
        run_id = None
        if body.save:
            run_id = services.new_run_id()
            dbmod.save_run(
                conn,
                run_id,
                sample_id,
                pack_id,
                computed["result"],
                services.snapshot_from_obs(observations),
                body.note,
            )
        return {**computed, "run_id": run_id}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    finally:
        conn.close()


@app.get("/api/runs")
def runs():
    conn = get_conn()
    try:
        return {"runs": dbmod.list_runs(conn)}
    finally:
        conn.close()


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str):
    conn = get_conn()
    try:
        record = dbmod.get_run(conn, run_id)
        if not record:
            raise HTTPException(404, "运行记录不存在")
        return record
    finally:
        conn.close()


@app.post("/api/runs/{run_id}/replay")
def replay(run_id: str):
    conn = get_conn()
    try:
        return services.replay_run(conn, run_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    finally:
        conn.close()


class BranchBody(BaseModel):
    name: str
    pack_id: str | None = None
    note: str = ""


@app.post("/api/samples/{sample_id}/branches")
def create_branch(sample_id: str, body: BranchBody):
    conn = get_conn()
    try:
        computed = services.compute(conn, sample_id, body.pack_id or "v1")
        branch_id = services.new_branch_id()
        dbmod.save_branch(
            conn,
            branch_id,
            sample_id,
            body.name,
            body.pack_id or "v1",
            services.snapshot_from_obs(
                dbmod.obs_from_snapshot(computed["observations"])
            ),
            computed["result"],
            body.note,
        )
        return {"branch_id": branch_id}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    finally:
        conn.close()


@app.get("/api/branches")
def branches():
    conn = get_conn()
    try:
        return {"branches": dbmod.list_branches(conn)}
    finally:
        conn.close()


@app.get("/api/branches/{branch_id}")
def branch_detail(branch_id: str):
    conn = get_conn()
    try:
        record = dbmod.get_branch(conn, branch_id)
        if not record:
            raise HTTPException(404, "分支不存在")
        return record
    finally:
        conn.close()


@app.get("/api/export")
def export_data():
    conn = get_conn()
    try:
        data = dbmod.export_all(conn)
        return JSONResponse(data, headers={"Content-Disposition": "attachment; filename=pottery-export.json"})
    finally:
        conn.close()


@app.post("/api/admin/reset")
def reset_to_fixtures():
    conn = get_conn()
    try:
        dbmod.clear_all(conn)
        dbmod.seed_fixtures(conn)
        return {"status": "reset", "samples": dbmod.list_samples(conn)}
    finally:
        conn.close()


class ImportBody(BaseModel):
    data: dict


@app.post("/api/import")
def import_data(body: ImportBody):
    conn = get_conn()
    try:
        try:
            counts = dbmod.import_all(conn, body.data)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, f"导入失败：{exc}") from exc
        return {"status": "imported", "counts": counts}
    finally:
        conn.close()


def main():  # 便于 python -m
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=5573)
