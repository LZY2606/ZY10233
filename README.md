# 陶胎火候辨证台

把陶器薄片矿物、XRD 相组成、孔隙率与热改性观测，组合成对
**烧成温度 × 烧成气氛 × 保温时间** 的联合区间约束。
页面给出可行烧成窗、每条证据单独排除的区域、最小冲突观测集与敏感性，
并保留多个人工解释分支。

> 一个矿物相的出现/消失通常只给出**区间证据**而非精确温度点。
> 本工具不输出“最佳温度点”，而是保留离散联合网格上的多个可行窗。

## 安装

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 演示

```bash
.venv/bin/python -m pytest -q
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 5573
```

浏览器访问 <http://127.0.0.1:5573>，页面标题为 **陶胎火候辨证台**。
SQLite 数据库默认写在仓库根目录 `taotai.db`，可用环境变量
`TAOTAI_DB=/path/to.db` 指定（测试自动使用临时库，不落盘）。

## 数据口径

### 参数网格（联合，不拆单轴）

- 温度：400–1200℃，步长 10℃（81 个点）。
- 气氛：`oxid` 氧化 / `reduct` 还原（离散类别，互不相通）。
- 保温时间：`short` <30min / `medium` 0.5–4h / `long` >4h（有序类）。

每个候选点是一个三维格 `(气氛, 保温时间, 温度)`，共 2×3×81=486 格。
规则与观测始终作用在三维区域上；系统**不会**先分别求温度/气氛/时间
区间再做一维交集——那会丢掉“某相只在特定气氛×保温时间组合下出现”的
条件依赖（例如高岭石 500℃ 只在中/长保温保留，短保温 500℃ 不成立；
赤铁矿致红只在氧化气氛成立）。

### 规则子句（闭世界 + 开闭端点）

规则包见 `fixtures/rulepack_v1.json`、`fixtures/rulepack_v2.json`。
一条特征由若干 `present` 子句的并集给出可能区域；每条子句显式声明
`atmo`（气氛）、`time`（保温时间）、`temp_min/temp_max` 与
`max_inclusive`：

- `max_inclusive=true`：温度上界闭，网格点取 `<= temp_max`。
- `max_inclusive=false`：上界开，网格点取 `< temp_max`
  （例如 v1 莫来石 1000℃≤T<1200℃，1200℃ 网格点被排除）。
- 未在任何子句中列出的 `(气氛, 时间)` 组合，特征**不可能**出现。
- `present` 观测要求格落在可能区域；`absent` 观测要求落在区域外
  （闭世界推断）。因此“恰好在阈值上”的观测由开闭端点严格决定。
- 特征可用 `material` 限定原料（`any` / `calcareous` / `common`）。
  观测继承分析单原料；原料不适用的特征（如普通泥料上的方解石）处处不可见。

### 证据权重与冲突处理

- 权重 `1.0`：硬证据。所有硬证据的三维交集必须非空才算存在可行窗。
- 权重 `0.3`：降权软证据（验收用于受污染特征）。它**不着色为可行**，
  只在硬可行窗上叠加“软证据满足比例”，并在硬冲突时标出全网格最佳折中区。
- 权重 `0`：停用，不参与计算。
- 硬交集为空时，系统**不扩宽任何规则**制造虚假可行区，而是给出
  **最小冲突观测集**（去掉核内任意一项即恢复非空交集）与逐项敏感性
  （`core` 冲突核成员 / `binding` 放宽可恢复格数 / `redundant` 冗余）。

### 受污染观察

`fixtures/demo_runs.json` 的 `demo-conflict` 分析单中，方解石峰被标记
`contaminated=true`（备注采样处有碳酸盐脉，可能为埋藏次生碳酸盐）。
在页面对其降权即可看到：冲突核 `[方解石, 莫来石]` 消失、恢复
“还原气氛 + 中/长保温 + 1000–1190℃”的可行窗；该变化可与主线并存于
人工解释分支。

### 两个固定演示

- `demo-conflict`：钙质泥料，受污染方解石与莫来石构成最小冲突核。
- `demo-twowindows`：普通泥料仅有“赤铁矿致红消失”，氧化与还原各自
  保留一个独立可行窗（气氛离散不连通，窗不被合并）。

## 操作与接口要点

- 规则版本切换：顶部选择 v1/v2，切换记入事件流水并立即重算。
  v2 相对 v1 修订了方解石分解上限（600→580℃）与莫来石长保温下限
  （1000→950℃），用于查看窗口对规则版本的敏感性。
- 人工解释分支：从当前分支复制观测另开分支（如“埋藏污染解释”），
  在分支上的降权/删除/切版本不影响 `main`。
- 证据排除区：观测表“叠加排除区”按三维 `(气氛, 时间, 温度)` 渲染该证据
  单独排除的范围（红色描边），不是单轴区间。
- 全部变更写 `events` 流水（`create_run / set_observation / set_weight /
  mark_contamination / remove_observation / switch_pack / create_branch /
  seed_fixture`）。

主要接口：`GET /api/runs`、`GET /api/runs/{id}?branch=`、
`POST /api/runs/{id}/observations`、
`POST /api/runs/{id}/observations/{oid}/weight`、
`POST /api/runs/{id}/observations/{oid}/contamination`、
`POST /api/runs/{id}/pack`、`POST /api/runs/{id}/branches`、
`POST /api/runs/{id}/evidence-mask`、`GET /api/export`、
`POST /api/import`、`POST /api/admin/clear`。交互式文档见 `/docs`。

## 运行记录导出与清空后复核

1. 页面“导出运行记录”（或 `GET /api/export`）得到
   `taotai-bundle/1` JSON：含规则包、每个 run 的全分支状态、完整事件流水
   与规范化 `state_hash`（SHA-256，对分支状态与事件序列排序后取哈希）。
2. “清空并重导 fixture”（或 `POST /api/admin/clear`）删除全部
   run/分支/观测/事件后重新种入 `fixtures/`，数据库回到出厂状态。
3. “导入复核”（或 `POST /api/import`）**先按事件流水逐步重放**重建状态，
   再计算实际 `state_hash` 与导出包比对，返回 `hash_match`。
   哈希一致即说明重放结果与导出时逐格相同。

命令行复核示例：

```bash
curl -s http://127.0.0.1:5573/api/export -o bundle.json
curl -s -X POST http://127.0.0.1:5573/api/admin/clear \
  -H 'Content-Type: application/json' \
  -d '{"reseed": true, "keep_packs": true}'
.venv/bin/python - <<'PY'
import json, urllib.request
bundle = json.load(open("bundle.json"))
req = urllib.request.Request(
    "http://127.0.0.1:5573/api/import",
    data=json.dumps({"bundle": bundle, "replace": True}).encode(),
    headers={"Content-Type": "application/json"})
print(json.load(urllib.request.urlopen(req)))  # hash_match: True
PY
```

## 目录结构

```
app.py                 FastAPI 路由
engine.py              三维联合网格、连通窗、最小冲突核、敏感性
store.py               SQLite、事件流水、fixture 播种、导出/重放/哈希
fixtures/rulepack_v1.json, rulepack_v2.json   固定规则包
fixtures/demo_runs.json                        固定演示分析单
static/index.html, app.js, app.css            操作页面
tests/test_engine.py, tests/test_api.py       自动化测试
conftest.py
```

## 测试

```bash
.venv/bin/python -m pytest -q
```

覆盖：开闭端点阈值、气氛/时间联合依赖、不可拆单轴、冲突核枚举、
降权后窗口恢复、双可行窗保留、规则版本切换、分支隔离，以及
导出—清空—事件重放—哈希复核全链路。
