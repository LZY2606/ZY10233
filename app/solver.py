"""区间约束求解内核。

关键设计：
1. 温度 × 气氛 × 保温时间是 *联合窗*。所有集合运算都在三维盒
   (Box: 温度区间 × 气氛集合 × 时间区间) 上进行，绝不把盒投影成
   三个独立区间再取交——那样会丢掉“某温度段只在某气氛、某保温时间
   组合下成立”的条件依赖，产生虚假可行区（见 naive_projection）。
2. 区间显式携带开闭端点，阈值处的观测按规则包声明处理。
3. 降权观测不参与硬排除，只在硬可行区内标注“软张力”。
4. 冲突时输出最小冲突核（minimal conflict cores），用最小命中集
   枚举实现，并按总权重给出最省力的缓解方向；不扩宽规则。
"""
from __future__ import annotations

from itertools import combinations

from .domain import (
    ATMOSPHERES,
    HOLD_DOMAIN,
    TEMPERATURE_DOMAIN,
    UNIVERSE,
    Box,
    Interval,
    Observation,
    Region,
    RulePack,
    box_to_json,
    region_to_json,
)

# ---------------------------------------------------------------------------
# 区间 / 盒 代数
# ---------------------------------------------------------------------------


def intersect_intervals(a: Interval, b: Interval) -> Interval:
    if a.lo > b.lo:
        lo, lo_kind = a.lo, a.lo_kind
    elif a.lo < b.lo:
        lo, lo_kind = b.lo, b.lo_kind
    else:
        lo = a.lo
        lo_kind = "closed" if (a.lo_kind == "closed" and b.lo_kind == "closed") else "open"

    if a.hi < b.hi:
        hi, hi_kind = a.hi, a.hi_kind
    elif a.hi > b.hi:
        hi, hi_kind = b.hi, b.hi_kind
    else:
        hi = a.hi
        hi_kind = "closed" if (a.hi_kind == "closed" and b.hi_kind == "closed") else "open"

    return Interval(lo, hi, lo_kind, hi_kind)


def intersect_boxes(a: Box, b: Box) -> Box:
    atm = a.atm & b.atm
    return Box(
        intersect_intervals(a.temp, b.temp),
        frozenset(atm),
        intersect_intervals(a.hold, b.hold),
    )


def intersect_regions(regions: list[Region]) -> Region:
    """多个盒并集的交：逐盒配对求交，再规范化。"""
    if not regions:
        return (UNIVERSE,)
    acc: Region = tuple(regions[0])
    for nxt in regions[1:]:
        acc = _normalise(tuple(
            ib for b in acc for c in nxt if not (ib := intersect_boxes(b, c)).empty()
        ))
        if not acc:
            return ()
    return acc


def _box_contained(a: Box, b: Box) -> bool:
    """a 是否被 b 包含（端点严格比较）。"""
    if not a.atm <= b.atm:
        return False
    for x, y in ((a.temp, b.temp), (a.hold, b.hold)):
        if x.lo < y.lo or x.hi > y.hi:
            return False
        if x.lo == y.lo:
            if y.lo_kind == "open" and x.lo_kind == "closed":
                return False
        if x.hi == y.hi:
            if y.hi_kind == "open" and x.hi_kind == "closed":
                return False
    return True


def _merge_boxes(a: Box, b: Box) -> Box | None:
    """两个盒可合并，当且仅当气氛相同、一个轴区间完全相同，
    另一轴在闭合端点处恰好相邻。其余情况保持为独立盒——
    跨条件依赖（不同气氛/时间段）的盒绝不拼合。"""
    if a.atm != b.atm:
        return None

    def glue(x: Interval, y: Interval) -> Interval | None:
        if x.hi == y.lo and x.hi_kind == "closed" and y.lo_kind == "closed":
            return Interval(x.lo, y.hi, x.lo_kind, y.hi_kind)
        if y.hi == x.lo and y.hi_kind == "closed" and x.lo_kind == "closed":
            return Interval(y.lo, x.hi, y.lo_kind, x.hi_kind)
        return None

    if a.hold == b.hold:
        g = glue(a.temp, b.temp)
        if g is not None:
            return Box(g, a.atm, a.hold)
    if a.temp == b.temp:
        g = glue(a.hold, b.hold)
        if g is not None:
            return Box(a.temp, a.atm, g)
    return None


def _drop_contained(boxes: list[Box]) -> list[Box]:
    kept: list[Box] = []
    for b in boxes:
        if b.empty():
            continue
        if any(_box_contained(b, k) for k in kept):
            continue
        kept = [k for k in kept if not _box_contained(k, b)]
        kept.append(b)
    return kept


def _normalise(boxes: Region) -> Region:
    kept = _drop_contained(list(boxes))
    changed = True
    while changed:
        changed = False
        for i, j in combinations(range(len(kept)), 2):
            merged = _merge_boxes(kept[i], kept[j])
            if merged is not None:
                kept = [k for idx, k in enumerate(kept) if idx not in (i, j)]
                kept.append(merged)
                kept = _drop_contained(kept)
                changed = True
                break
    kept.sort(key=lambda b: (sorted(b.atm), b.temp.lo, b.hold.lo, b.temp.hi))
    return tuple(kept)


# ---------------------------------------------------------------------------
# 差集（用于“被某证据排除的区域”与软张力区域）
# ---------------------------------------------------------------------------


def _point_in(v: float, iv: Interval) -> bool:
    if not (iv.lo <= v <= iv.hi):
        return False
    if v == iv.lo and iv.lo_kind == "open":
        return False
    if v == iv.hi and iv.hi_kind == "open":
        return False
    return True


def _subtract_interval(x: Interval, y: Interval) -> tuple[Interval, ...]:
    ix = intersect_intervals(x, y)
    if ix.empty:
        return (x,)
    parts: list[Interval] = []
    # 左侧残余 [x.lo, ix.lo]
    if x.lo < ix.lo or (x.lo == ix.lo and x.lo_kind == "closed" and ix.lo_kind == "open"):
        right_kind = "open" if ix.lo_kind == "closed" else "closed"
        cand = Interval(x.lo, ix.lo, x.lo_kind, right_kind)
        if not cand.empty:
            parts.append(cand)
    # 右侧残余 [ix.hi, x.hi]
    if ix.hi < x.hi or (x.hi == ix.hi and x.hi_kind == "closed" and ix.hi_kind == "open"):
        left_kind = "open" if ix.hi_kind == "closed" else "closed"
        cand = Interval(ix.hi, x.hi, left_kind, x.hi_kind)
        if not cand.empty:
            parts.append(cand)
    return tuple(parts)


def subtract_boxes(a: Box, b: Box) -> Region:
    ib = intersect_boxes(a, b)
    if ib.empty():
        return (a,)
    pieces: list[Box] = []
    # 温度左侧
    for iv in _subtract_interval(a.temp, ib.temp):
        pieces.append(Box(iv, a.atm, a.hold))
    # 温度带内、时间两侧
    for iv in _subtract_interval(a.hold, ib.hold):
        pieces.append(Box(ib.temp, a.atm, iv))
    # 温度带 × 时间带、气氛差集
    atm_left = a.atm - b.atm
    if atm_left:
        pieces.append(Box(ib.temp, frozenset(atm_left), ib.hold))
    return tuple(p for p in pieces if not p.empty())


def subtract_region(region: Region, other: Region) -> Region:
    """region \\ other（精确盒运算）。"""
    frags: list[Box] = list(region)
    for ob in other:
        nxt: list[Box] = []
        for frag in frags:
            nxt.extend(subtract_boxes(frag, ob))
        frags = nxt
        if not frags:
            break
    return _normalise(tuple(frags))


# ---------------------------------------------------------------------------
# 栅格：精确的“签名栅格”用于冲突判定，较粗的展示栅格用于页面
# ---------------------------------------------------------------------------


def axis_points(intervals, domain: Interval, step: float | None = None) -> list[float]:
    if step is not None:
        n_lo = round(domain.lo / step)
        n_hi = round(domain.hi / step)
        return [round(n * step, 6) for n in range(n_lo, n_hi + 1)]
    cuts: set[float] = {domain.lo, domain.hi}
    for iv in intervals:
        cuts.add(iv.lo)
        cuts.add(iv.hi)
    pts = sorted(cuts)
    mids = [(a + b) / 2 for a, b in zip(pts, pts[1:]) if a < b]
    return sorted(set(pts + mids))


def _mask_for_box(
    box: Box,
    t_pts: list[float],
    atm_ids: list[str],
    h_pts: list[float],
) -> "np.ndarray":
    import numpy as np

    t_ok = np.array([_point_in(t, box.temp) for t in t_pts])
    h_ok = np.array([_point_in(h, box.hold) for h in h_pts])
    a_ok = np.array([a in box.atm for a in atm_ids])
    return a_ok[:, None, None] & t_ok[None, :, None] & h_ok[None, None, :]


def region_mask(
    region: Region,
    t_pts: list[float],
    atm_ids: list[str],
    h_pts: list[float],
):
    import numpy as np

    mask = np.zeros((len(atm_ids), len(t_pts), len(h_pts)), dtype=bool)
    for b in region:
        mask |= _mask_for_box(b, t_pts, atm_ids, h_pts)
    return mask


def signature_grid(evidence_regions: list[Region]):
    """由所有盒端点构造的栅格：每个开区间段取中点、阈值点单独保留。

    在该栅格上，任意盒交集的“空 / 非空”判定与精确盒运算一致，
    因此可用 numpy 位掩码做快速的冲突核枚举。
    """
    t_ivs, h_ivs = [], []
    for region in evidence_regions:
        for b in region:
            t_ivs.append(b.temp)
            h_ivs.append(b.hold)
    t_pts = axis_points(t_ivs, TEMPERATURE_DOMAIN)
    h_pts = axis_points(h_ivs, HOLD_DOMAIN)
    return t_pts, list(ATMOSPHERES), h_pts


# ---------------------------------------------------------------------------
# 最小冲突核 = “每个不可行栅格单元所违反的证据集合”这个冲突族的最小命中集
# ---------------------------------------------------------------------------


def minimal_hitting_sets(conflicts: list[frozenset[str]]) -> list[frozenset[str]]:
    """枚举最小命中集（Reiter 命中集树的递归实现，含剪枝）。"""
    sets = sorted({c for c in conflicts if c}, key=lambda s: (len(s), sorted(s)))
    # 删除被其他冲突包含的冲突（它们不影响最小命中集）
    pruned: list[frozenset[str]] = []
    for i, ci in enumerate(sets):
        if not any(j != i and sets[j] <= ci for j in range(len(sets))):
            pruned.append(ci)

    results: list[frozenset[str]] = []

    def search(remaining: list[frozenset[str]], picked: frozenset[str]) -> None:
        if not remaining:
            if not any(r <= picked for r in results):
                results.append(picked)
            return
        if any(r <= picked for r in results):
            return
        conflict = min(remaining, key=len)
        for elem in sorted(conflict):
            new_picked = picked | {elem}
            # 已找到的解包含当前选择则不可能更优
            if any(r <= new_picked for r in results):
                continue
            new_remaining = [c for c in remaining if elem not in c]
            search(new_remaining, new_picked)

    search(pruned, frozenset())
    return results


# ---------------------------------------------------------------------------
# 联合窗求解
# ---------------------------------------------------------------------------


def _hull(ivs: list[Interval]) -> Interval:
    ivs = [i for i in ivs if not i.empty]
    lo = min(i.lo for i in ivs)
    hi = max(i.hi for i in ivs)
    lo_kind = "closed" if any(i.lo == lo and i.lo_kind == "closed" for i in ivs) else "open"
    hi_kind = "closed" if any(i.hi == hi and i.hi_kind == "closed" for i in ivs) else "open"
    return Interval(lo, hi, lo_kind, hi_kind)


def _project_naive(regions: list[Region]) -> Region:
    """错误示范：把每个证据外包到温度/时间/气氛三条单轴，
    各轴独立求交，再回填成一个全空间盒。

    条件依赖在此丢失：例如“高温只在氧化下成立、低温只在还原下成立”
    会被错误拼成一个不存在的可行盒，且多段温度会被外包成一整段。
    """
    t_hull = _hull([TEMPERATURE_DOMAIN])
    h_hull = _hull([HOLD_DOMAIN])
    atm_hull = frozenset(ATMOSPHERES)
    for region in regions:
        t_hull = intersect_intervals(t_hull, _hull([b.temp for b in region]))
        h_hull = intersect_intervals(h_hull, _hull([b.hold for b in region]))
        atm_hull &= frozenset(a for b in region for a in b.atm)
    if t_hull.empty or h_hull.empty or not atm_hull:
        return ()
    return (Box(t_hull, atm_hull, h_hull),)


def _interval_text(iv: Interval, unit: str) -> str:
    lb = "[" if iv.lo_kind == "closed" else "("
    rb = "]" if iv.hi_kind == "closed" else ")"
    lo = int(iv.lo) if float(iv.lo).is_integer() else iv.lo
    hi = int(iv.hi) if float(iv.hi).is_integer() else iv.hi
    return f"{lb}{lo}, {hi}{rb} {unit}"


def region_text(region: Region) -> list[str]:
    lines = []
    for b in region:
        atms = "/".join(a for a in ATMOSPHERES if a in b.atm)
        lines.append(
            f"{atms}气氛，温度 {_interval_text(b.temp, '℃')}，保温 {_interval_text(b.hold, 'h')}"
        )
    return lines


def solve(pack: RulePack, observations: list[Observation], material: str) -> dict:
    import numpy as np

    evidence: list[dict] = []
    active_regions: list[Region] = []
    hard_regions: list[Region] = []
    soft_regions: list[Region] = []

    for obs in observations:
        if obs.weight == "off":
            evidence.append({"obs": obs, "region": (), "status": "off"})
            continue
        rule = pack.lookup(obs.feature_id, obs.value, material)
        region = rule.region() if rule else ()
        status = "hard" if obs.weight == "hard" else "soft"
        evidence.append({"obs": obs, "region": region, "status": status, "rule_id": rule.rule_id if rule else None})
        active_regions.append(region)
        if status == "hard":
            hard_regions.append(region)
        else:
            soft_regions.append(region)

    feasible = intersect_regions(hard_regions) if hard_regions else (UNIVERSE,)

    # 朴素单轴投影（仅作对照展示，不参与结论）
    naive = _project_naive(hard_regions) if hard_regions else (UNIVERSE,)

    # ---- 精确签名栅格 + 掩码 ----
    sig_t, sig_a, sig_h = signature_grid(active_regions or [(UNIVERSE,)])
    hard_masks = [region_mask(e["region"], sig_t, sig_a, sig_h) for e in evidence if e["status"] == "hard"]
    hard_ids = [e["obs"].obs_id for e in evidence if e["status"] == "hard"]
    hard_feasible_mask = np.ones((len(sig_a), len(sig_t), len(sig_h)), dtype=bool)
    for m in hard_masks:
        hard_feasible_mask &= m

    # 校验栅格与精确盒判定一致（防止实现偏差）
    exact_empty = len(feasible) == 0
    grid_empty = not bool(hard_feasible_mask.any())
    assert exact_empty == grid_empty, "signature grid disagrees with exact boxes"

    # ---- 最小冲突核 ----
    conflicts: list[frozenset[str]] = []
    if hard_masks and grid_empty:
        stack = np.stack(hard_masks, axis=-1)  # A,T,H,K
        violated = ~stack
        flat = violated.reshape(-1, len(hard_masks))
        rows, cols = np.nonzero(flat)
        grouped: dict[int, set[str]] = {}
        for r, k in zip(rows, cols):
            grouped.setdefault(int(r), set()).add(hard_ids[k])
        conflicts = [frozenset(s) for s in grouped.values()]
    cores = minimal_hitting_sets(conflicts) if conflicts else []

    weights = {o.obs_id: o.weight_value() for o in observations}

    def core_payload(core: frozenset[str]) -> dict:
        wsum = round(sum(weights.get(x, 1.0) for x in core), 4)
        return {
            "members": sorted(core),
            "weight_sum": wsum,
            "cardinality": len(core),
        }

    cores_payload = sorted(
        (core_payload(c) for c in cores),
        key=lambda c: (c["weight_sum"], c["cardinality"], c["members"]),
    )

    # ---- 软张力：降权证据与硬可行区冲突的部分 ----
    tensions = []
    for e in evidence:
        if e["status"] != "soft":
            continue
        conflict_part = subtract_region(feasible, e["region"])
        tensions.append(
            {
                "obs_id": e["obs"].obs_id,
                "feature_id": e["obs"].feature_id,
                "region": region_to_json(conflict_part),
                "region_text": region_text(conflict_part),
            }
        )

    # ---- 敏感性：逐条忽略证据后重算 ----
    sensitivity = []
    hard_evidence = [e for e in evidence if e["status"] == "hard"]
    for e in hard_evidence:
        rest = [x["region"] for x in hard_evidence if x is not e]
        new_feasible = intersect_regions(rest) if rest else (UNIVERSE,)
        m = region_mask(new_feasible, sig_t, sig_a, sig_h)
        drops_conflict = bool(m.any())
        sensitivity.append(
            {
                "obs_id": e["obs"].obs_id,
                "feature_id": e["obs"].feature_id,
                "resolves_conflict": grid_empty and drops_conflict,
                "feasible_text": region_text(new_feasible)[:4],
            }
        )

    # ---- 展示栅格 ----
    disp_t = axis_points([], TEMPERATURE_DOMAIN, step=20.0)
    disp_h = axis_points([], HOLD_DOMAIN, step=0.5)
    disp_a = list(ATMOSPHERES)
    feas_mask = region_mask(feasible, disp_t, disp_a, disp_h)
    naive_mask = region_mask(naive, disp_t, disp_a, disp_h)

    def pack_mask(mask):
        import base64

        return {
            "shape": list(mask.shape),
            "data": base64.b64encode(np.packbits(mask.ravel()).tobytes()).decode("ascii"),
        }

    evidence_payload = []
    for e in evidence:
        obs = e["obs"]
        em = region_mask(e["region"], disp_t, disp_a, disp_h)
        excluded = subtract_region((UNIVERSE,), e["region"]) if e["status"] != "off" else ()
        evidence_payload.append(
            {
                "obs_id": obs.obs_id,
                "feature_id": obs.feature_id,
                "value": obs.value,
                "weight": obs.weight,
                "contaminated": obs.contaminated,
                "note": obs.note,
                "rule_id": e.get("rule_id"),
                "status": e["status"],
                "region": region_to_json(e["region"]),
                "region_text": region_text(e["region"]),
                "excluded_text": region_text(excluded)[:6],
                "mask": pack_mask(em),
            }
        )

    for t in tensions:
        tbox = None
        for e in evidence:
            if e["status"] == "soft" and e["obs"].obs_id == t["obs_id"]:
                tbox = subtract_region(feasible, e["region"])
        if tbox is not None:
            t["mask"] = pack_mask(region_mask(tbox, disp_t, disp_a, disp_h))

    return {
        "pack_id": pack.pack_id,
        "material": material,
        "axes": {
            "temperatures": disp_t,
            "atmospheres": disp_a,
            "holds": disp_h,
        },
        "feasible": {
            "empty": len(feasible) == 0,
            "region": region_to_json(feasible),
            "region_text": region_text(feasible),
            "cell_count": int(feas_mask.sum()),
            "mask": pack_mask(feas_mask),
        },
        "naive_projection": {
            "region": region_to_json(naive),
            "region_text": region_text(naive),
            "cell_count": int(naive_mask.sum()),
            "mask": pack_mask(naive_mask),
            "spurious_extra": int((naive_mask & ~feas_mask).sum()),
        },
        "has_conflict": grid_empty and bool(hard_evidence),
        "conflict_cores": cores_payload,
        "tensions": tensions,
        "evidence": evidence_payload,
        "sensitivity": sorted(sensitivity, key=lambda s: (not s["resolves_conflict"], s["obs_id"])),
    }
