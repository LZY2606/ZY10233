"""陶胎火候辨证台 - 联合区间约束引擎。

温度 × 气氛 × 保温时间构成联合参数网格。每条规则给出某特征在
特定气氛与保温时间组合下的温度可能范围（含开闭端点）。

关键约束：
- 一个特征的“可能区域”是三维联合区域，不能拆成单轴区间再取交。
- 观测 present 要求落在区域内；absent 要求落在区域外（闭世界）。
- 阈值网格点严格按 temp_max / max_inclusive 的开闭端点处理。
- 硬证据（weight>=1）全部满足才算可行；降权证据（0<weight<1）
  只做软性覆盖，绝不靠放宽硬规则制造虚假可行区。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

MATERIALS = {"any", "calcareous", "common"}
HARD_WEIGHT = 1.0
DOWNWEIGHTED_WEIGHT = 0.3


@dataclass(frozen=True)
class Grid:
    temps: np.ndarray
    atmo_ids: tuple[str, ...]
    time_ids: tuple[str, ...]
    atmo_labels: dict[str, str]
    time_labels: dict[str, str]

    @property
    def shape(self) -> tuple[int, int, int]:
        return (len(self.atmo_ids), len(self.time_ids), len(self.temps))

    @property
    def n_cells(self) -> int:
        return int(np.prod(self.shape))


def build_grid(pack: dict[str, Any]) -> Grid:
    g = pack["grid"]
    temps = np.arange(g["temp_min"], g["temp_max"] + 1, g["temp_step"], dtype=int)
    atmo_ids = tuple(a["id"] for a in g["atmospheres"])
    time_ids = tuple(t["id"] for t in g["hold_times"])
    return Grid(
        temps=temps,
        atmo_ids=atmo_ids,
        time_ids=time_ids,
        atmo_labels={a["id"]: a["label"] for a in g["atmospheres"]},
        time_labels={t["id"]: t["label"] for t in g["hold_times"]},
    )


def feature_region(pack: dict[str, Any], grid: Grid, feature_key: str,
                   material: str) -> np.ndarray:
    """返回特征 present 的三维布尔区域 (n_atmo, n_time, n_temp)。

    若该原料下规则包不含此特征，返回全 False（该特征不适用于此原料）。
    """
    feature = next((f for f in pack["features"] if f["key"] == feature_key), None)
    region = np.zeros(grid.shape, dtype=bool)
    if feature is None:
        return region
    required = feature.get("material", "any")
    if required != "any" and required != material:
        return region

    temp_centers = grid.temps.astype(float)
    for clause in feature.get("clauses", []):
        if clause.get("op", "present") != "present":
            continue
        atmo_idx = [grid.atmo_ids.index(a) for a in clause["atmo"] if a in grid.atmo_ids]
        time_idx = [grid.time_ids.index(t) for t in clause["time"] if t in grid.time_ids]
        lo = float(clause["temp_min"])
        hi = float(clause["temp_max"])
        if clause.get("max_inclusive", True):
            temp_hit = (temp_centers >= lo) & (temp_centers <= hi)
        else:
            temp_hit = (temp_centers >= lo) & (temp_centers < hi)
        for ai in atmo_idx:
            for ti in time_idx:
                region[ai, ti, :] |= temp_hit
    return region


def observation_mask(pack: dict[str, Any], grid: Grid, obs: dict[str, Any]) -> np.ndarray:
    """满足单条观测的联合区域。absent = present 区域取补（闭世界）。"""
    present_region = feature_region(pack, grid, obs["feature_key"], obs.get("material", "any"))
    if obs.get("op", "present") == "present":
        return present_region
    return ~present_region


def _components(mask: np.ndarray) -> np.ndarray:
    """三维 6-邻域连通分量标注；气氛轴与时间轴不相邻于缺失分量。

    温度方向按网格步长相邻；气氛与时间为离散类，仅在自身轴上与
    同维相邻类连通，不同气氛永不连通。
    """
    labels = np.full(mask.shape, -1, dtype=int)
    current = 0
    n_a, n_t, n_te = mask.shape
    for a in range(n_a):
        for t in range(n_t):
            for k in range(n_te):
                if not mask[a, t, k] or labels[a, t, k] != -1:
                    continue
                current += 1
                stack = [(a, t, k)]
                labels[a, t, k] = current
                while stack:
                    ca, ct, ck = stack.pop()
                    # 气氛为离散类别，不同气氛永不连通；
                    # 仅在同一气氛面内沿保温时间（有序）与温度相邻。
                    neighbors = [
                        (ca, ct, ck - 1), (ca, ct, ck + 1),
                        (ca, ct - 1, ck), (ca, ct + 1, ck),
                    ]
                    for na, nt, nk in neighbors:
                        if not (0 <= na < n_a and 0 <= nt < n_t and 0 <= nk < n_te):
                            continue
                        if mask[na, nt, nk] and labels[na, nt, nk] == -1:
                            labels[na, nt, nk] = current
                            stack.append((na, nt, nk))
    return labels


def _window_records(labels: np.ndarray, grid: Grid) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for cid in sorted({int(v) for v in np.unique(labels) if v > 0}):
        coords = np.argwhere(labels == cid)
        atmospheres = sorted({grid.atmo_ids[a] for a, _, _ in coords})
        times = sorted({grid.time_ids[t] for _, t, _ in coords},
                       key=lambda x: grid.time_ids.index(x))
        temps = sorted({int(grid.temps[k]) for _, _, k in coords})
        windows.append({
            "window_id": cid,
            "cell_count": int(len(coords)),
            "atmospheres": [
                {"id": a, "label": grid.atmo_labels[a]} for a in atmospheres
            ],
            "hold_times": [
                {"id": t, "label": grid.time_labels[t]} for t in times
            ],
            "temp_min": min(temps),
            "temp_max": max(temps),
            "cells": [[int(a), int(t), int(k)] for a, t, k in coords],
        })
    return windows


def _minimal_hard_cores(hard_masks: dict[str, np.ndarray]) -> list[list[str]]:
    """枚举硬观测的最小不可满足子集（最小冲突核）。

    冲突核 C 满足：合取为空，但去掉其中任一观测后合取非空。
    观测数少（个位数），用增量枚举即可。
    """
    ids = list(hard_masks)
    n = len(ids)
    cores: list[list[str]] = []

    def conjunct(subset: tuple[int, ...]) -> np.ndarray | None:
        result = None
        for i in subset:
            m = hard_masks[ids[i]]
            result = m if result is None else (result & m)
        return result

    def search(prefix: tuple[int, ...], remaining: list[int]) -> None:
        base = conjunct(prefix)
        if base is not None and not base.any():
            minimal = True
            for j in range(len(prefix)):
                smaller = prefix[:j] + prefix[j + 1:]
                sub = conjunct(smaller)
                if sub is not None and not sub.any():
                    minimal = False
                    break
            if minimal:
                cores.append([ids[i] for i in prefix])
            return
        for pos, i in enumerate(remaining):
            search(prefix + (i,), remaining[pos + 1:])

    search((), list(range(n)))
    cores.sort(key=lambda c: (len(c), c))
    return cores


def analyze(pack: dict[str, Any], material: str,
            observations: list[dict[str, Any]]) -> dict[str, Any]:
    """对一组观测求联合可行区域、排除区域、最小冲突核与敏感性。"""
    grid = build_grid(pack)
    total = grid.n_cells

    masks: dict[str, np.ndarray] = {}
    meta: dict[str, dict[str, Any]] = {}
    for obs in observations:
        effective = dict(obs)
        effective["material"] = obs.get("material") in (None, "", "any") and material or obs["material"]
        masks[obs["id"]] = observation_mask(pack, grid, effective)
        meta[obs["id"]] = effective

    hard_ids = [o["id"] for o in observations if float(o.get("weight", 1.0)) >= HARD_WEIGHT]
    soft_ids = [o["id"] for o in observations
                if 0.0 < float(o.get("weight", 1.0)) < HARD_WEIGHT]

    feasible = np.ones(grid.shape, dtype=bool)
    for oid in hard_ids:
        feasible &= masks[oid]

    labels = _components(feasible)
    windows = _window_records(labels, grid)

    hard_masks = {oid: masks[oid] for oid in hard_ids}
    cores: list[list[str]] = [] if feasible.any() else _minimal_hard_cores(hard_masks)

    evidence: list[dict[str, Any]] = []
    for obs in observations:
        oid = obs["id"]
        weight = float(obs.get("weight", 1.0))
        excluded = (~masks[oid]).sum().item()
        if weight >= HARD_WEIGHT:
            others = np.ones(grid.shape, dtype=bool)
            for other in hard_ids:
                if other != oid:
                    others &= masks[other]
            relaxed = (others & feasible)
            gained = int(relaxed.sum()) - int(feasible.sum())
            in_core = any(oid in core for core in cores)
            sensitivity = "core" if in_core else ("binding" if gained > 0 else "redundant")
        else:
            gained = None
            sensitivity = "soft"
        evidence.append({
            "observation_id": oid,
            "feature_key": obs["feature_key"],
            "op": obs.get("op", "present"),
            "weight": weight,
            "contaminated": bool(obs.get("contaminated", False)),
            "note": obs.get("note", ""),
            "excluded_cells": excluded,
            "excluded_fraction": round(excluded / total, 4),
            "cells_gained_if_relaxed": gained,
            "sensitivity": sensitivity,
        })

    soft_score = np.zeros(grid.shape, dtype=float)
    soft_satisfied = np.zeros(grid.shape, dtype=np.int16)
    soft_total_weight = 0.0
    for oid in soft_ids:
        w = float(meta[oid]["weight"])
        soft_total_weight += w
        soft_score += np.where(masks[oid], w, 0.0)
        soft_satisfied += masks[oid].astype(np.int16)

    if soft_total_weight > 0:
        normalized = soft_score / soft_total_weight
    else:
        normalized = soft_score

    best_score = float(normalized.max()) if soft_ids else 0.0
    if feasible.any():
        feasible_soft = normalized[feasible]
        soft_in_feasible = {
            "min": round(float(feasible_soft.min()), 4),
            "max": round(float(feasible_soft.max()), 4),
            "mean": round(float(feasible_soft.mean()), 4),
        }
    else:
        soft_in_feasible = None

    return {
        "grid": {
            "temps": [int(t) for t in grid.temps],
            "atmospheres": [
                {"id": a, "label": grid.atmo_labels[a]} for a in grid.atmo_ids
            ],
            "hold_times": [
                {"id": t, "label": grid.time_labels[t]} for t in grid.time_ids
            ],
            "shape": list(grid.shape),
        },
        "material": material,
        "pack_id": pack["pack_id"],
        "pack_version": pack["version"],
        "feasible_cell_count": int(feasible.sum()),
        "total_cell_count": total,
        "feasible_labels": labels.tolist(),
        "feasible": feasible.tolist(),
        "windows": windows,
        "evidence": evidence,
        "hard_observation_ids": hard_ids,
        "soft_observation_ids": soft_ids,
        "soft_score": np.round(normalized, 4).tolist(),
        "soft_satisfied_count": soft_satisfied.tolist(),
        "soft_summary": {
            "total_weight": round(soft_total_weight, 4),
            "best_score_overall": round(best_score, 4),
            "score_within_feasible": soft_in_feasible,
        },
        "minimal_conflict_cores": cores,
        "conflict": not feasible.any(),
    }


def evidence_excluded_mask(pack: dict[str, Any], grid_shape: tuple[int, int, int],
                           material: str, feature_key: str,
                           op: str) -> list[list[list[bool]]]:
    """供前端叠加：该证据单独排除（不满足）的三维区域。"""
    grid = build_grid(pack)
    pseudo = {"id": "_q", "feature_key": feature_key, "op": op, "material": material}
    satisfied = observation_mask(pack, grid, pseudo)
    return (~satisfied).tolist()
