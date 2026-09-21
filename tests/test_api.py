"""HTTP 端到端：页面、求解、降权、版本、运行记录、导出/清空/导入/重放。"""
from __future__ import annotations

import copy


def test_index_title(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "陶胎火候辨证台" in res.text


def test_meta_has_fixtures(client):
    meta = client.get("/api/meta").json()
    assert {p["pack_id"] for p in meta["packs"]} == {"v1", "v2"}
    assert {s["sample_id"] for s in meta["samples"]} == {"NC-01", "NC-02"}


def _hard_weights():
    return {f"o{i}": "hard" for i in range(1, 7)}


def test_solve_conflict_then_deweight(client):
    body = {"pack_id": "v1", "provisional": [{"obs_id": k, "weight": v} for k, v in _hard_weights().items()]}
    r = client.post("/api/samples/NC-01/solve", json=body).json()
    assert r["result"]["has_conflict"] is True
    assert r["result"]["feasible"]["empty"] is True
    cores = [tuple(c["members"]) for c in r["result"]["conflict_cores"]]
    assert ("o1", "o2") in cores

    weights = _hard_weights()
    weights["o1"] = "soft"
    body2 = {"pack_id": "v1", "provisional": [{"obs_id": k, "weight": v} for k, v in weights.items()]}
    r2 = client.post("/api/samples/NC-01/solve", json=body2).json()
    assert r2["result"]["has_conflict"] is False
    assert r2["result"]["feasible"]["cell_count"] > 0
    assert {t["obs_id"] for t in r2["result"]["tensions"]} == {"o1"}


def test_persisted_weight_and_version_switch(client):
    assert client.post("/api/samples/NC-01/observations/o1/weight", json={"weight": "soft"}).status_code == 200
    r1 = client.post("/api/samples/NC-01/solve", json={"pack_id": "v1"}).json()
    r2 = client.post("/api/samples/NC-01/solve", json={"pack_id": "v2"}).json()
    assert r1["result"]["feasible"]["cell_count"] > r2["result"]["feasible"]["cell_count"]


def test_nc02_shows_spurious_uniaxial_region(client):
    r = client.post("/api/samples/NC-02/solve", json={"pack_id": "v1"}).json()
    assert r["result"]["feasible"]["empty"] is True
    assert r["result"]["naive_projection"]["spurious_extra"] > 0


def test_run_save_replay_export_reset_import_roundtrip(client):
    # 1. 保存两条运行：一条硬冲突，一条降权
    client.post("/api/samples/NC-01/solve",
                json={"pack_id": "v1", "save": True, "note": "hard"})
    client.post("/api/samples/NC-01/observations/o1/weight", json={"weight": "soft"})
    client.post("/api/samples/NC-01/solve",
                json={"pack_id": "v1", "save": True, "note": "deweighted"})
    runs = client.get("/api/runs").json()["runs"]
    assert len(runs) == 2

    # 2. 重放摘要一致
    run_id = runs[0]["run_id"]
    rep = client.post(f"/api/runs/{run_id}/replay").json()
    assert rep["matches"] is True
    assert rep["stored_digest"] == rep["replay_digest"]
    detail = client.get(f"/api/runs/{run_id}").json()
    assert "result" in detail

    # 3. 导出
    export = client.get("/api/export").json()
    assert export["format"] == "taotai-huohou-v1"
    assert len(export["runs"]) == 2
    snapshot = copy.deepcopy(export)

    # 4. 清空并重置 fixture：运行记录消失，观测回到 hard
    client.post("/api/admin/reset")
    assert client.get("/api/runs").json()["runs"] == []
    detail_sample = client.get("/api/samples/NC-01").json()
    assert all(o["weight"] == "hard" for o in detail_sample["observations"])

    # 5. 导入旧快照后记录恢复，重放仍一致（清空数据库后重新导入复核）
    imp = client.post("/api/import", json={"data": snapshot}).json()
    assert imp["counts"]["runs"] == 2
    runs2 = client.get("/api/runs").json()["runs"]
    assert {r["run_id"] for r in runs2} == {r["run_id"] for r in runs}
    rep2 = client.post(f"/api/runs/{run_id}/replay").json()
    assert rep2["matches"] is True


def test_branch_persists_manual_interpretation(client):
    client.post("/api/samples/NC-01/observations/o1/weight", json={"weight": "soft"})
    b = client.post("/api/samples/NC-01/branches", json={"name": "降权解释", "pack_id": "v1"}).json()
    branches = client.get("/api/branches").json()["branches"]
    assert any(x["branch_id"] == b["branch_id"] for x in branches)
    detail = client.get(f"/api/branches/{b['branch_id']}").json()
    assert detail["result"]["feasible"]["empty"] is False


def test_bad_pack_and_weight_rejected(client):
    assert client.post("/api/samples/NC-01/solve", json={"pack_id": "v9"}).status_code == 400
    assert client.post("/api/samples/NC-01/observations/o1/weight",
                       json={"weight": "maybe"}).status_code == 422
