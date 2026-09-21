import numpy as np

import engine


def obs(id, feature, op="present", material="any", weight=1.0, contaminated=False):
    return {"id": id, "feature_key": feature, "op": op, "material": material,
            "weight": weight, "contaminated": contaminated}


def test_threshold_endpoint_open_closed(packs):
    pack = packs["v1"]
    grid = engine.build_grid(pack)
    region = engine.feature_region(pack, grid, "mullite", "any")
    ti = grid.time_ids.index("medium")
    def temp_index(t):
        return int(np.where(grid.temps == t)[0][0])
    assert bool(region[0, ti, temp_index(990)]) is False
    assert bool(region[0, ti, temp_index(1000)]) is True
    assert bool(region[0, ti, temp_index(1190)]) is True
    assert bool(region[0, ti, temp_index(1200)]) is False
    cal = engine.feature_region(pack, grid, "calcite", "calcareous")
    assert bool(cal[0, 0, temp_index(600)]) is True
    assert bool(cal[0, 0, temp_index(610)]) is False


def test_feature_depends_on_atmosphere_jointly(packs):
    pack = packs["v1"]
    grid = engine.build_grid(pack)
    hem = engine.feature_region(pack, grid, "hematite_red", "any")
    ai_oxid = grid.atmo_ids.index("oxid")
    ai_red = grid.atmo_ids.index("reduct")
    k = int(np.where(grid.temps == 900)[0][0])
    assert bool(hem[ai_oxid, 0, k]) is True
    assert bool(hem[ai_red, 0, k]) is False
    assert bool(engine.feature_region(pack, grid, "calcite", "common").any()) is False


def test_mullite_time_gating(packs):
    pack = packs["v1"]
    grid = engine.build_grid(pack)
    m = engine.feature_region(pack, grid, "mullite", "any")
    k = int(np.where(grid.temps == 1050)[0][0])
    assert bool(m[:, grid.time_ids.index("short"), k].any()) is False
    assert bool(m[0, grid.time_ids.index("long"), k]) is True


def test_joint_dependency_not_univariate(packs):
    """单轴温度交集会把 500℃ 短保温误算为高岭石可行；联合规则下并非如此。"""
    pack = packs["v1"]
    result = engine.analyze(pack, "any", [obs("kao", "kaolinite", "present", "any")])
    temps = result["grid"]["temps"]
    k = temps.index(500)
    ai = next(i for i, a in enumerate(result["grid"]["atmospheres"]) if a["id"] == "oxid")
    times = {t["id"]: i for i, t in enumerate(result["grid"]["hold_times"])}
    assert result["feasible"][ai][times["short"]][k] is False
    assert result["feasible"][ai][times["long"]][k] is True

    char = engine.analyze(pack, "any", [obs("char", "charcoal_residue", "present", "any")])
    ar = next(i for i, a in enumerate(char["grid"]["atmospheres"]) if a["id"] == "reduct")
    ao = next(i for i, a in enumerate(char["grid"]["atmospheres"]) if a["id"] == "oxid")
    kk = char["grid"]["temps"].index(600)
    assert char["feasible"][ar][times["short"]][kk] is True
    assert char["feasible"][ao][times["short"]][kk] is False


def test_conflict_core_and_downweight(packs):
    pack = packs["v1"]
    observations = [
        obs("cal", "calcite", "present", "calcareous", contaminated=True),
        obs("mul", "mullite", "present", "calcareous"),
        obs("kao", "kaolinite", "absent", "calcareous"),
        obs("hem", "hematite_red", "absent", "calcareous"),
    ]
    result = engine.analyze(pack, "calcareous", observations)
    assert result["conflict"] is True
    assert result["feasible_cell_count"] == 0
    assert ["cal", "mul"] in result["minimal_conflict_cores"]
    assert all("kao" not in core for core in result["minimal_conflict_cores"])
    sens = {e["observation_id"]: e["sensitivity"] for e in result["evidence"]}
    assert sens["cal"] == "core" and sens["mul"] == "core"
    assert sens["kao"] == "redundant"

    observations[0]["weight"] = 0.3
    relaxed = engine.analyze(pack, "calcareous", observations)
    assert relaxed["conflict"] is False
    assert relaxed["feasible_cell_count"] > 0
    assert relaxed["minimal_conflict_cores"] == []
    assert "cal" in relaxed["soft_observation_ids"]
    window = relaxed["windows"][0]
    assert {"id": "reduct", "label": "还原"} in window["atmospheres"]
    assert {"id": "oxid", "label": "氧化"} not in window["atmospheres"]


def test_multiple_windows_kept(packs):
    pack = packs["v1"]
    observations = [obs("hem", "hematite_red", "absent", "common")]
    result = engine.analyze(pack, "common", observations)
    assert len(result["windows"]) == 2
    atmos_sets = [frozenset(a["id"] for a in w["atmospheres"]) for w in result["windows"]]
    assert frozenset({"oxid"}) in atmos_sets
    assert frozenset({"reduct"}) in atmos_sets
