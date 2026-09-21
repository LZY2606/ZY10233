"""领域模型：区间（开闭端点）、多维盒、规则包与观测。

参数空间是三个轴的联合空间：
- 温度 temperature (deg C)
- 气氛 atmosphere：O 氧化 / R 还原 / N 中性
- 保温时间 hold_hours

规则与观测的可行域都是盒的并集（Union[Box]），端点开闭被显式保留，
因此“恰好在相变阈值上”的观测可以按规则包声明的开闭端点处理，
而不是被栅格四舍五入。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

EndKind = Literal["open", "closed"]

ATMOSPHERES: tuple[str, ...] = ("O", "R", "N")
ATM_LABELS = {"O": "氧化", "R": "还原", "N": "中性"}

TEMPERATURE_DOMAIN: "Interval"  # 见下方构造
HOLD_DOMAIN: "Interval"

# 固定权重档位；受污染观测降权后变成“软张力”，不再硬性排除
WEIGHTS = {"hard": 1.0, "soft": 0.25, "off": 0.0}
WEIGHT_LABELS = {"hard": "确认", "soft": "降权（疑似污染）", "off": "停用"}


@dataclass(frozen=True)
class Interval:
    lo: float
    hi: float
    lo_kind: EndKind = "closed"
    hi_kind: EndKind = "closed"

    @property
    def empty(self) -> bool:
        if self.lo > self.hi:
            return True
        if self.lo == self.hi and (self.lo_kind == "open" or self.hi_kind == "open"):
            return True
        return False

    @property
    def width(self) -> float:
        return max(0.0, self.hi - self.lo)


@dataclass(frozen=True)
class Box:
    """联合参数空间中的一个盒：温度 × 气氛集合 × 保温时间。"""

    temp: Interval
    atm: frozenset[str]
    hold: Interval

    def empty(self) -> bool:
        return self.temp.empty or self.hold.empty or len(self.atm) == 0


TEMPERATURE_DOMAIN = Interval(300.0, 1300.0)
HOLD_DOMAIN = Interval(0.1, 16.0)
UNIVERSE = Box(TEMPERATURE_DOMAIN, frozenset(ATMOSPHERES), HOLD_DOMAIN)


@dataclass(frozen=True)
class Rule:
    rule_id: str
    feature_id: str
    value: str  # present / absent / strong 等离散观测值
    material: str  # 原料类型，例如 nc 泥质陶
    boxes: tuple[Box, ...] = field(default_factory=tuple)
    note: str = ""

    def region(self) -> "Region":
        return tuple(b for b in self.boxes if not b.empty())


# 一个可行区域 = 盒的并集
Region = tuple[Box, ...]


@dataclass(frozen=True)
class RulePack:
    pack_id: str
    name: str
    material: str
    rules: tuple[Rule, ...] = field(default_factory=tuple)
    note: str = ""

    def lookup(self, feature_id: str, value: str, material: str) -> Rule | None:
        for rule in self.rules:
            if (
                rule.feature_id == feature_id
                and rule.value == value
                and rule.material == material
            ):
                return rule
        return None


@dataclass
class Observation:
    obs_id: str
    feature_id: str
    value: str
    weight: str = "hard"  # hard / soft / off
    contaminated: bool = False
    note: str = ""

    def weight_value(self) -> float:
        return WEIGHTS[self.weight]


FEATURE_LABELS: dict[str, str] = {
    "calcite": "方解石（薄片/XRD）",
    "mullite": "莫来石（XRD）",
    "hematite": "赤铁矿（薄片）",
    "magnetite": "磁铁矿（薄片）",
    "chlorite": "绿泥石（薄片）",
    "illite": "伊利石（XRD）",
    "vitrification": "玻化程度",
    "porosity": "孔隙率",
}

VALUE_LABELS: dict[str, str] = {
    "present": "存在",
    "absent": "消失",
    "strong": "强烈/显著",
    "high": "高（多孔）",
    "low": "低（致密）",
}

MATERIAL_LABELS = {"nc": "泥质陶（非钙质）", "cc": "钙质陶"}


def interval_to_json(iv: Interval) -> dict:
    return {
        "lo": iv.lo,
        "hi": iv.hi,
        "lo_kind": iv.lo_kind,
        "hi_kind": iv.hi_kind,
    }


def box_to_json(box: Box) -> dict:
    return {
        "temp": interval_to_json(box.temp),
        "atms": sorted(box.atm),
        "hold": interval_to_json(box.hold),
    }


def region_to_json(region: Region) -> list[dict]:
    return [box_to_json(b) for b in region]
