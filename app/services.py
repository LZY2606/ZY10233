"""应用服务层：组织规则包、观测、求解与复核摘要。"""
from __future__ import annotations

import hashlib
import json
import uuid

from . import db as dbmod
from .domain import (
    ATM_LABELS,
    FEATURE_LABELS,
    MATERIAL_LABELS,
    VALUE_LABELS,
    Observation,
)
from .rules import DEFAULT_PACK, PACKS
from .solver import solve


def get_pack(pack_id: str | None):
    pid = pack_id or DEFAULT_PACK
    if pid not in PACKS:
        raise KeyError(f"未知规则版本 {pid}")
    return PACKS[pid]


def pack_catalog() -> list[dict]:
    return [
        {
            "pack_id": p.pack_id,
            "name": p.name,
            "material": p.material,
            "note": p.note,
        }
        for p in PACKS.values()
    ]


def feature_catalog() -> dict:
    return {
        "features": FEATURE_LABELS,
        "values": VALUE_LABELS,
        "atmospheres": ATM_LABELS,
        "materials": MATERIAL_LABELS,
    }


def snapshot_from_obs(observations: list[Observation]) -> list[dict]:
    return [vars(o) for o in observations]


def compute(conn, sample_id: str, pack_id: str, override: list[Observation] | None = None) -> dict:
    sample = dbmod.get_sample(conn, sample_id)
    if not sample:
        raise LookupError(f"样品 {sample_id} 不存在")
    pack = get_pack(pack_id)
    observations = override if override is not None else dbmod.list_observations(conn, sample_id)
    result = solve(pack, observations, sample["material"])
    result["digest"] = digest_result(result)
    return {
        "sample": sample,
        "pack_id": pack.pack_id,
        "observations": [vars(o) for o in observations],
        "result": result,
    }


def digest_result(result: dict) -> str:
    """对结论性字段做规范哈希，供重放比对（掩码是派生展示量，不参与）。"""
    core = {
        "pack_id": result["pack_id"],
        "material": result["material"],
        "empty": result["feasible"]["empty"],
        "region": result["feasible"]["region"],
        "has_conflict": result["has_conflict"],
        "cores": result["conflict_cores"],
        "tensions": [
            {"obs_id": t["obs_id"], "region": t["region"]} for t in result["tensions"]
        ],
        "naive_spurious": result["naive_projection"]["spurious_extra"],
    }
    blob = json.dumps(core, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def new_run_id() -> str:
    return "run-" + uuid.uuid4().hex[:12]


def new_branch_id() -> str:
    return "br-" + uuid.uuid4().hex[:12]


def replay_run(conn, run_id: str) -> dict:
    record = dbmod.get_run(conn, run_id)
    if not record:
        raise LookupError("运行记录不存在")
    sample = dbmod.get_sample(conn, record["sample_id"])
    pack = get_pack(record["pack_id"])
    observations = dbmod.obs_from_snapshot(record["observations"])
    replayed = solve(pack, observations, sample["material"])
    old = record["result"]
    return {
        "run_id": run_id,
        "replay_digest": digest_result(replayed),
        "stored_digest": old.get("digest"),
        "matches": digest_result(replayed) == old.get("digest"),
        "has_conflict": replayed["has_conflict"],
        "feasible_empty": replayed["feasible"]["empty"],
        "region_text": replayed["feasible"]["region_text"],
        "conflict_cores": replayed["conflict_cores"],
    }
