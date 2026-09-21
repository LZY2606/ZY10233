"""固定规则库 fixture。

数据口径：
- 温度单位 ℃，保温时间单位 h，气氛 O/R/N。
- 规则只在“原料 × 气氛 × 保温时间”的特定组合下限定温度，
  每条规则的可行域是若干个带开闭端点的三维盒。
- 阈值端点：多数相变按半开区间处理（例如方解石存在 ~ T < 700，
  恰好 700 ℃ 的薄片点不计入“存在”），规则包显式声明开闭。
- v2 是规则修订版（莫来石阈值 950→1000 ℃），用于版本切换与敏感性。
这些数字是教学型 fixture，用于演示区间辨证，不替代真实考古定年。
"""
from __future__ import annotations

from .domain import (
    ATMOSPHERES,
    Box,
    Interval,
    Observation,
    Rule,
    RulePack,
)

_O, _R, _N = frozenset("O"), frozenset("R"), frozenset("N")
_ALL = frozenset(ATMOSPHERES)


def _iv(lo, hi, lok="closed", hik="closed"):
    return Interval(float(lo), float(hi), lok, hik)


def _nc_v1() -> tuple[Rule, ...]:
    return (
        Rule(
            "r-calcite-present",
            "calcite",
            "present",
            "nc",
            (
                # 短时保温：方解石可残存到 650；长时保温 600 即分解
                Box(_iv(300, 600, "closed", "open"), _ALL, _iv(2, 16, "closed", "closed")),
                Box(_iv(300, 650, "closed", "open"), _ALL, _iv(0.1, 2, "closed", "open")),
            ),
            "方解石残存：时间越长分解越早",
        ),
        Rule(
            "r-calcite-absent",
            "calcite",
            "absent",
            "nc",
            (
                Box(_iv(600, 1300, "closed", "closed"), _ALL, _iv(2, 16, "closed", "closed")),
                Box(_iv(650, 1300, "open", "closed"), _ALL, _iv(0.1, 2, "closed", "open")),
            ),
            "方解石消失的补集规则（阈值处与 present 开闭互补）",
        ),
        Rule(
            "r-mullite-present",
            "mullite",
            "present",
            "nc",
            (
                # 氧化 + 长时：950 起；氧化 + 短时需 1000；还原下需更高
                Box(_iv(950, 1300, "closed", "closed"), _O, _iv(2, 16, "closed", "closed")),
                Box(_iv(1000, 1300, "closed", "closed"), _O, _iv(0.1, 2, "closed", "open")),
                Box(_iv(1050, 1300, "closed", "closed"), _R | _N, _iv(2, 16, "closed", "closed")),
                Box(_iv(1100, 1300, "closed", "closed"), _R | _N, _iv(0.1, 2, "closed", "open")),
            ),
            "莫来石：温度-气氛-保温联合阈值",
        ),
        Rule(
            "r-hematite-present",
            "hematite",
            "present",
            "nc",
            (Box(_iv(600, 1300, "closed", "closed"), _O, _iv(0.1, 16, "closed", "closed")),),
            "赤铁矿只在氧化气氛形成/留存",
        ),
        Rule(
            "r-magnetite-present",
            "magnetite",
            "present",
            "nc",
            (Box(_iv(500, 1300, "closed", "closed"), _R | _N, _iv(0.1, 16, "closed", "closed")),),
            "磁铁矿指示还原/中性气氛",
        ),
        Rule(
            "r-chlorite-absent",
            "chlorite",
            "absent",
            "nc",
            (
                # 氧化下绿泥石 ~550 分解；还原下可残存到 ~650
                Box(_iv(550, 1300, "open", "closed"), _O, _iv(0.1, 16, "closed", "closed")),
                Box(_iv(650, 1300, "open", "closed"), _R | _N, _iv(0.1, 16, "closed", "closed")),
            ),
            "绿泥石消失阈值随气氛移动",
        ),
        Rule(
            "r-illite-absent",
            "illite",
            "absent",
            "nc",
            (
                # 长时保温分解更早
                Box(_iv(800, 1300, "open", "closed"), _ALL, _iv(2, 16, "closed", "closed")),
                Box(_iv(900, 1300, "open", "closed"), _ALL, _iv(0.1, 2, "closed", "open")),
            ),
            "伊利石消失：保温时间依赖",
        ),
        Rule(
            "r-vitrification-strong",
            "vitrification",
            "strong",
            "nc",
            (
                # 强烈玻化：氧化长时 850 起；氧化短时 900；还原 950/1000
                Box(_iv(850, 1300, "closed", "closed"), _O, _iv(2, 16, "closed", "closed")),
                Box(_iv(900, 1300, "closed", "closed"), _O, _iv(0.1, 2, "closed", "open")),
                Box(_iv(950, 1300, "closed", "closed"), _R | _N, _iv(2, 16, "closed", "closed")),
                Box(_iv(1000, 1300, "closed", "closed"), _R | _N, _iv(0.1, 2, "closed", "open")),
            ),
            "强烈玻化带：多维联合窗",
        ),
        Rule(
            "r-porosity-high",
            "porosity",
            "high",
            "nc",
            (
                # 高孔隙率（未烧结致密化）：氧化长时 < 900；短时 < 950
                Box(_iv(300, 900, "closed", "open"), _O, _iv(2, 16, "closed", "closed")),
                Box(_iv(300, 950, "closed", "open"), _O, _iv(0.1, 2, "closed", "open")),
                Box(_iv(300, 950, "closed", "open"), _R | _N, _iv(2, 16, "closed", "closed")),
                Box(_iv(300, 1000, "closed", "open"), _R | _N, _iv(0.1, 2, "closed", "open")),
            ),
            "高孔隙率：致密化之前",
        ),
    )


def _nc_v2(rules_v1: tuple[Rule, ...]) -> tuple[Rule, ...]:
    """规则修订：莫来石氧化长时阈值由 950 上修到 1000 ℃。"""
    out = []
    for r in rules_v1:
        if r.rule_id == "r-mullite-present":
            boxes = (
                Box(_iv(1000, 1300, "closed", "closed"), frozenset("O"), _iv(2, 16, "closed", "closed")),
                Box(_iv(1050, 1300, "closed", "closed"), frozenset("O"), _iv(0.1, 2, "closed", "open")),
                Box(_iv(1050, 1300, "closed", "closed"), frozenset(("R", "N")), _iv(2, 16, "closed", "closed")),
                Box(_iv(1100, 1300, "closed", "closed"), frozenset(("R", "N")), _iv(0.1, 2, "closed", "open")),
            )
            out.append(Rule(r.rule_id, r.feature_id, r.value, r.material, boxes, r.note + "（v2 修订）"))
        else:
            out.append(r)
    return tuple(out)


V1 = RulePack("v1", "规则包 2024 版", "nc", _nc_v1(), "初版区间规则")
V2 = RulePack(
    "v2",
    "规则包 2025 修订版",
    "nc",
    _nc_v2(_nc_v1()),
    "莫来石阈值上修 50 ℃，其余不变",
)
PACKS = {"v1": V1, "v2": V2}
DEFAULT_PACK = "v1"

# ---------------------------------------------------------------------------
# 固定样品与观测
# ---------------------------------------------------------------------------

SAMPLE_NC01_OBS = (
    Observation("o1", "calcite", "present", "hard", True, "陶片边缘重结晶方解石，疑似埋藏污染"),
    Observation("o2", "mullite", "present", "hard", False, "XRD 检出莫来石峰"),
    Observation("o3", "hematite", "present", "hard", False, "薄片见赤铁矿红点"),
    Observation("o4", "illite", "absent", "hard", False, "XRD 无伊利石"),
    Observation("o5", "vitrification", "strong", "hard", False, "断面强烈玻化"),
    Observation("o6", "chlorite", "absent", "hard", False, "薄片无绿泥石"),
)

# 对照样品：专门暴露“单轴交集”错误——高温氧化长时 与 低温还原短时
SAMPLE_NC02_OBS = (
    Observation("p1", "mullite", "present", "hard", False, "氧化长时高温证据"),
    Observation("p2", "porosity", "high", "hard", False, "还原短时低温证据"),
    Observation("p3", "calcite", "absent", "hard", False, "全气氛中高温证据"),
)

FIXTURE_SAMPLES = {
    "NC-01": {
        "name": "NC-01 泥质陶片（含争议方解石）",
        "material": "nc",
        "observations": SAMPLE_NC01_OBS,
    },
    "NC-02": {
        "name": "NC-02 泥质陶片（联合窗对照样）",
        "material": "nc",
        "observations": SAMPLE_NC02_OBS,
    },
}
