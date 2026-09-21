"""求解内核测试：端点、联合窗依赖、降权、冲突核、敏感性。"""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.domain import (
    ATMOSPHERES,
    HOLD_DOMAIN,
    TEMPERATURE_DOMAIN,
    UNIVERSE,
    Box,
    Interval,
    Observation,
)
from app.rules import FIXTURE_SAMPLES, V1, V2
from app.solver import (
    intersect_intervals,
    intersect_regions,
    region_mask,
    signature_grid,
    solve,
    subtract_boxes,
)


def obs(sample_id="NC-01"):
    return list(FIXTURE_SAMPLES[sample_id]["observations"])


# ---------------------------------------------------------------------------
# 开闭端点
# ---------------------------------------------------------------------------


def test_open_closed_endpoints_on_interval():
    # [600, 700) ∩ [700, 800] 应为空（阈值 700 不属于左侧）
    a = Interval(600, 700, "closed", "open")
    b = Interval(700, 800, "closed", "closed")
    assert intersect_intervals(a, b).empty
    # [600, 700] ∩ [700, 800] 恰含一点 700
    a2 = Interval(600, 700, "closed", "closed")
    ix = intersect_intervals(a2, b)
    assert not ix.empty and ix.lo == 700 and ix.hi == 700


def test_threshold_observation_long_hold_600_is_open_endpoint():
    """规则在阈值上按开端点处理：长时保温时方解石存在域为 T < 600。
    600℃ 本身既不在“存在”侧，也不在消失侧起点（消失侧为 [600,...]）。"""
    present = V1.lookup("calcite", "present", "nc").region()
    absent = V1.lookup("calcite", "absent", "nc").region()
    t_pts = [599.0, 600.0, 601.0]
    h_pts = [4.0]  # 长时保温段
    mp = region_mask(present, t_pts, list(ATMOSPHERES), h_pts)
    ma = region_mask(absent, t_pts, list(ATMOSPHERES), h_pts)
    i = t_pts.index(600.0)
    assert not mp[0, i, 0]          # 600 不属于存在侧（开）
    assert ma[0, i, 0]              # 600 属于消失侧（闭）
    assert mp[0, t_pts.index(599.0), 0]
    assert ma[0, t_pts.index(601.0), 0]


def test_present_absent_complement_on_threshold_short_hold():
    present = V1.lookup("calcite", "present", "nc").region()
    absent = V1.lookup("calcite", "absent", "nc").region()
    t, a, h = signature_grid([present, absent])
    # 650 是短时保温（hold < 2）阈值，存在侧 [..,650) 开、消失侧 (650,..] 开：
    # 650 本身两侧都不收；625 归存在，725 归消失。
    i650 = t.index(650.0)
    ih = next(k for k, hv in enumerate(h) if 0.1 <= hv < 2.0)
    assert not mp_at(present, t, a, h, 0, i650, ih)
    assert not mp_at(absent, t, a, h, 0, i650, ih)
    assert mp_at(present, t, a, h, 0, t.index(625.0), ih)
    assert mp_at(absent, t, a, h, 0, t.index(975.0), ih)


def mp_at(region, t, a, h, ai, ti, hi):
    return bool(region_mask(region, t, a, h)[ai, ti, hi])


# ---------------------------------------------------------------------------
# 矛盾、冲突核、降权恢复
# ---------------------------------------------------------------------------


def test_nc01_conflict_and_minimal_cores():
    r = solve(V1, obs("NC-01"), "nc")
    assert r["has_conflict"] is True
    assert r["feasible"]["empty"] is True
    members = sorted(tuple(c["members"]) for c in r["conflict_cores"])
    # o1 受污染方解石与三个高温证据分别构成最小冲突核
    assert ("o1", "o2") in members
    assert ("o1", "o4") in members
    assert ("o1", "o5") in members
    # 每个核都必须含 o1：只忽略其他证据不能解围
    assert all("o1" in c["members"] for c in r["conflict_cores"])
    assert {s["obs_id"]: s["resolves_conflict"] for s in r["sensitivity"]}["o1"] is True


def test_deweighting_contaminated_observation_reopens_window():
    observations = [
        replace(o, weight="soft") if o.contaminated else o for o in obs("NC-01")
    ]
    r = solve(V1, observations, "nc")
    assert r["has_conflict"] is False
    assert r["feasible"]["empty"] is False
    assert r["feasible"]["cell_count"] > 0
    # 可行窗仍是氧化气氛（受赤铁矿约束），且温度不低于莫来石阈值 950
    text = "\n".join(r["feasible"]["region_text"])
    assert "O气氛" in text
    assert "950" in text
    # 降权证据仍以软张力出现，不能被悄悄丢掉
    tension_ids = {t["obs_id"] for t in r["tensions"]}
    assert "o1" in tension_ids


def test_deweight_does_not_widen_rules():
    hard = solve(V1, obs("NC-01"), "nc")
    soft = solve(V1, [replace(o, weight="soft") if o.contaminated else o for o in obs("NC-01")], "nc")
    # 硬冲突时可行区严格为空；降权后非空，但张力区必须被报告
    assert hard["feasible"]["cell_count"] == 0
    assert soft["feasible"]["cell_count"] > 0
    assert any(t["obs_id"] == "o1" and t["region_text"] for t in soft["tensions"])


# ---------------------------------------------------------------------------
# 联合窗不能拆成单轴交集
# ---------------------------------------------------------------------------


def test_nc02_naive_uniaxial_intersection_is_spurious():
    r = solve(V1, obs("NC-02"), "nc")
    # 联合窗：高温氧化长时 与 低温还原短时 没有共同参数点
    assert r["feasible"]["empty"] is True
    assert r["feasible"]["cell_count"] == 0
    # 朴素三轴外包交集却“看起来可行”，全部是虚假单元
    assert r["naive_projection"]["spurious_extra"] > 0
    assert r["naive_projection"]["cell_count"] == r["naive_projection"]["spurious_extra"]


def test_joint_dependence_atmosphere_temperature():
    """构造：证据 A 只在氧化下高温；证据 B 只在还原下低温。
    单轴投影会给出 [950,1000] 且气氛={O,R} 的假盒；联合窗必须为空。"""
    high_o = (Box(Interval(950, 1300), frozenset("O"), HOLD_DOMAIN),)
    low_r = (Box(Interval(300, 500), frozenset("R"), HOLD_DOMAIN),)
    joint = intersect_regions([high_o, low_r])
    assert joint == ()
    r = solve(V1, obs("NC-01"), "nc")  # smoke: solver still runs


# ---------------------------------------------------------------------------
# 规则版本切换
# ---------------------------------------------------------------------------


def test_rule_pack_version_shifts_window():
    observations = [
        replace(o, weight="soft") if o.contaminated else o for o in obs("NC-01")
    ]
    r1 = solve(V1, observations, "nc")
    r2 = solve(V2, observations, "nc")
    # v2 莫来石阈值 950 → 1000，可行窗缩小
    assert r2["feasible"]["cell_count"] < r1["feasible"]["cell_count"]
    assert any("1000" in line for line in r2["feasible"]["region_text"])


# ---------------------------------------------------------------------------
# 差集
# ---------------------------------------------------------------------------


def test_subtract_box_keeps_atmosphere_piece():
    a = UNIVERSE
    b = Box(TEMPERATURE_DOMAIN, frozenset("O"), HOLD_DOMAIN)
    rest = subtract_boxes(a, b)
    atms = frozenset(x for box in rest for x in box.atm)
    assert "O" not in atms and atms == frozenset({"R", "N"})
