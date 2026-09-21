def test_index_title(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "陶胎火候辨证台" in resp.text


def test_fixtures_seeded(client):
    runs = client.get("/api/runs").json()["runs"]
    ids = {r["run_id"] for r in runs}
    assert {"demo-conflict", "demo-twowindows"} <= ids


def test_demo_conflict_then_downweight(client):
    detail = client.get("/api/runs/demo-conflict").json()
    assert detail["conflict"] is True
    cores = detail["minimal_conflict_cores"]
    assert any(set(c) == {"cal", "mul"} for c in cores)

    down = client.post(
        "/api/runs/demo-conflict/observations/cal/weight",
        json={"weight": 0.3},
    ).json()
    assert down["conflict"] is False
    assert down["feasible_cell_count"] > 0
    assert down["minimal_conflict_cores"] == []
    assert "cal" in down["soft_observation_ids"]
    window_atmos = {a["id"] for a in down["windows"][0]["atmospheres"]}
    assert window_atmos == {"reduct"}


def test_demo_two_windows(client):
    detail = client.get("/api/runs/demo-twowindows").json()
    assert len(detail["windows"]) == 2
    atmos = [frozenset(a["id"] for a in w["atmospheres"]) for w in detail["windows"]]
    assert frozenset({"oxid"}) in atmos
    assert frozenset({"reduct"}) in atmos


def test_evidence_mask_is_multidim(client):
    resp = client.post(
        "/api/runs/demo-twowindows/evidence-mask",
        json={"feature_key": "hematite_red", "op": "present"},
    ).json()
    grid = resp["grid"]
    excluded = resp["excluded"]
    ai_oxid = grid["atmospheres"].index("oxid")
    ai_red = grid["atmospheres"].index("reduct")
    k = grid["temps"].index(900)
    assert excluded[ai_oxid][0][k] is False
    assert excluded[ai_red][0][k] is True


def test_pack_switch_changes_thresholds(client):
    v1 = client.get("/api/runs/demo-conflict").json()
    assert v1["pack_version"] == "v1"
    v2 = client.post("/api/runs/demo-conflict/pack", json={"pack_version": "v2"}).json()
    assert v2["pack_version"] == "v2"
    v1_again = client.post("/api/runs/demo-conflict/pack", json={"pack_version": "v1"}).json()
    assert v1_again["pack_version"] == "v1"
    kinds = [e["kind"] for e in v1_again["events"]]
    assert kinds.count("switch_pack") == 2


def test_branch_preserves_alternative_interpretation(client):
    client.post(
        "/api/runs/demo-conflict/observations/cal/weight",
        json={"weight": 0.3},
        params={"branch": "main"},
    )
    resp = client.post("/api/runs/demo-conflict/branches", json={
        "new_branch": "埋藏污染解释", "from_branch": "main", "note": "方解石视为次生",
    })
    assert resp.status_code == 200
    main = client.get("/api/runs/demo-conflict", params={"branch": "main"}).json()
    branch = client.get("/api/runs/demo-conflict",
                        params={"branch": "埋藏污染解释"}).json()
    assert any(o["id"] == "cal" and o["weight"] == 0.3 for o in branch["state"]["observations"])
    client.post(
        "/api/runs/demo-conflict/observations/cal/weight",
        json={"weight": 1.0},
        params={"branch": "埋藏污染解释"},
    )
    main2 = client.get("/api/runs/demo-conflict", params={"branch": "main"}).json()
    cal_main = next(o for o in main2["state"]["observations"] if o["id"] == "cal")
    assert cal_main["weight"] == 0.3
    assert main2["conflict"] is False


def test_export_clear_reimport_roundtrip(client):
    client.post(
        "/api/runs/demo-conflict/observations/cal/weight",
        json={"weight": 0.3},
    )
    client.post("/api/runs/demo-conflict/pack", json={"pack_version": "v2"})
    bundle = client.get("/api/export").json()
    expected = bundle["state_hash"]

    client.post("/api/admin/clear", json={"reseed": True, "keep_packs": True})
    reseeded = client.get("/api/runs/demo-conflict").json()
    assert reseeded["state"]["pack_version"] == "v1"
    cal = next(o for o in reseeded["state"]["observations"] if o["id"] == "cal")
    assert cal["weight"] == 1.0

    result = client.post("/api/import", json={"bundle": bundle, "replace": True}).json()
    assert result["hash_match"] is True
    assert result["actual_hash"] == expected
    restored = client.get("/api/runs/demo-conflict").json()
    assert restored["state"]["pack_version"] == "v2"
    cal = next(o for o in restored["state"]["observations"] if o["id"] == "cal")
    assert cal["weight"] == 0.3


def test_create_run_and_observe(client):
    resp = client.post("/api/runs", json={
        "run_id": "case-9", "name": "野外灰陶", "material": "common",
        "pack_version": "v1",
    })
    assert resp.status_code == 200
    detail = client.post("/api/runs/case-9/observations", json={
        "id": "o1", "feature_key": "mullite", "op": "present", "weight": 1.0,
    }).json()
    assert detail["feasible_cell_count"] > 0
    bad = client.post("/api/runs", json={
        "run_id": "case-9", "name": "x", "material": "common", "pack_version": "v1",
    })
    assert bad.status_code == 409
