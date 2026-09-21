"use strict";

const state = {
  packs: [],
  runs: [],
  current: null,
  runId: null,
  branch: "main",
  evidenceOverlay: {},
};

const $ = (sel) => document.querySelector(sel);
const api = async (path, opts) => {
  const resp = await fetch(path, opts || {});
  if (!resp.ok) {
    let detail = resp.statusText;
    try { detail = (await resp.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  return resp.json();
};
const post = (path, body) => api(path, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
});
const del = (path) => api(path, { method: "DELETE" });

async function loadPacks() {
  const data = await api("/api/packs");
  state.packs = data.packs;
  const sel = $("#packSelect");
  sel.innerHTML = "";
  data.packs.forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p.version;
    opt.textContent = `${p.label}（${p.version}）`;
    sel.appendChild(opt);
  });
}

async function loadRuns(selectId) {
  const data = await api("/api/runs");
  state.runs = data.runs;
  const sel = $("#runSelect");
  sel.innerHTML = "";
  data.runs.forEach((r) => {
    const opt = document.createElement("option");
    opt.value = r.run_id;
    opt.textContent = r.name;
    sel.appendChild(opt);
  });
  if (state.runId && data.runs.some((r) => r.run_id === state.runId)) {
    sel.value = state.runId;
  } else if (data.runs.length) {
    state.runId = data.runs[0].run_id;
    sel.value = state.runId;
  }
  await loadRun();
}

async function loadRun() {
  const runId = $("#runSelect").value || state.runId;
  if (!runId) return;
  state.runId = runId;
  state.branch = "main";
  await refreshRun();
}

async function refreshRun() {
  const runId = state.runId;
  const data = await api(`/api/runs/${encodeURIComponent(runId)}?branch=${encodeURIComponent(state.branch)}`);
  state.current = data;
  $("#packSelect").value = data.state.pack_version;
  $("#runName").textContent = data.state.name;

  const bsel = $("#branchSelect");
  bsel.innerHTML = "";
  data.branches.forEach((b) => {
    const opt = document.createElement("option");
    opt.value = b.branch;
    opt.textContent = b.branch === "main" ? "main（主线）" : `${b.branch}（人工解释）`;
    bsel.appendChild(opt);
  });
  bsel.value = state.branch;

  renderObsTable(data);
  renderAddFeature(data);
  renderVerdict(data);
  renderWindows(data);
  renderCores(data);
  renderSoft(data);
  renderHeatmaps(data);
  renderEvents(data);
}

function featureLabel(key) {
  const f = state.current.feature_index[key];
  return f ? f.label : key;
}

function renderAddFeature(data) {
  const sel = $("#addFeature");
  const prev = sel.value;
  sel.innerHTML = "";
  const pack = state.packs.find((p) => p.version === data.state.pack_version);
  (pack ? pack.features : []).forEach((f) => {
    const opt = document.createElement("option");
    opt.value = f.key;
    opt.textContent = f.label + (f.material !== "any" ? `（仅${f.material === "calcareous" ? "钙质" : f.material}）` : "");
    sel.appendChild(opt);
  });
  if (prev) sel.value = prev;
}

function sensTag(e) {
  const map = {
    core: ["core", "冲突核成员"],
    binding: ["binding", "有约束"],
    redundant: ["redundant", "冗余"],
    soft: ["soft", "软证据"],
  };
  const [cls, txt] = map[e.sensitivity] || ["redundant", e.sensitivity];
  const gained = e.cells_gained_if_relaxed;
  return `<span class="tag ${cls}">${txt}</span>` +
    (gained !== null && gained !== undefined ? ` <span class="muted">放宽可恢复 ${gained} 格</span>` : "");
}

function renderObsTable(data) {
  const tb = $("#obsTable tbody");
  tb.innerHTML = "";
  data.evidence.forEach((e) => {
    const tr = document.createElement("tr");
    const contam = e.contaminated
      ? `<span class="tag contam">受污染</span>` : "";
    const wLabel = e.weight >= 1 ? "硬 1.0" : (e.weight === 0 ? "停用 0" : `软 ${e.weight}`);
    tr.innerHTML = `
      <td>${featureLabel(e.feature_key)}${contam}
        <div class="muted" style="font-size:12px">${escapeHtml(e.note || "")}</div></td>
      <td><span class="tag ${e.op}">${e.op === "present" ? "出现" : "消失"}</span></td>
      <td>
        <select data-act="weight">
          <option value="1" ${e.weight >= 1 ? "selected" : ""}>硬证据 1.0</option>
          <option value="0.3" ${Math.abs(e.weight - 0.3) < 1e-6 ? "selected" : ""}>降权 0.3</option>
          <option value="0" ${e.weight === 0 ? "selected" : ""}>停用 0</option>
        </select>
        <div class="muted">${wLabel}</div>
      </td>
      <td><input type="checkbox" data-act="contam" ${e.contaminated ? "checked" : ""}></td>
      <td>${e.excluded_cells} 格<br><span class="muted">${(e.excluded_fraction * 100).toFixed(1)}%</span>
        <div><button class="linkish" data-act="overlay">叠加排除区</button></div></td>
      <td>${sensTag(e)}</td>
      <td><button class="linkish" data-act="remove">删除</button></td>`;
    tr.querySelectorAll("[data-act]").forEach((el) => {
      el.addEventListener("click" , () => onObsAction(e, el));
      el.addEventListener("change", () => onObsAction(e, el));
    });
    tb.appendChild(tr);
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function onObsAction(e, el) {
  const act = el.dataset.act;
  const base = `/api/runs/${encodeURIComponent(state.runId)}/observations/${encodeURIComponent(e.observation_id)}`;
  try {
    if (act === "weight") {
      await post(`${base}/weight?branch=${encodeURIComponent(state.branch)}`, { weight: parseFloat(el.value) });
    } else if (act === "contam") {
      await post(`${base}/contamination?branch=${encodeURIComponent(state.branch)}`, { contaminated: el.checked });
    } else if (act === "remove") {
      await del(`${base}?branch=${encodeURIComponent(state.branch)}`);
    } else if (act === "overlay") {
      await toggleOverlay(e);
      return;
    }
    await refreshRun();
  } catch (err) { alert(err.message); }
}

async function toggleOverlay(e) {
  const key = `${e.feature_key}:${e.op}`;
  if (state.evidenceOverlay[key]) {
    delete state.evidenceOverlay[key];
    renderHeatmaps(state.current);
    return;
  }
  const data = await post(
    `/api/runs/${encodeURIComponent(state.runId)}/evidence-mask?branch=${encodeURIComponent(state.branch)}`,
    { feature_key: e.feature_key, op: e.op }
  );
  state.evidenceOverlay[key] = data.excluded;
  renderHeatmaps(state.current);
}

function renderVerdict(data) {
  const el = $("#verdict");
  if (data.conflict) {
    el.innerHTML = `<p class="bad-text">硬证据联合可行区为空（0/${data.total_cell_count} 格）。</p>
      <p class="muted">系统不扩宽规则制造虚假可行区，请在下方查看最小冲突核与敏感性；对受污染特征降权可转为软证据后重算。</p>`;
  } else {
    const pct = (100 * data.feasible_cell_count / data.total_cell_count).toFixed(1);
    el.innerHTML = `<p><span class="ok-text">存在 ${data.windows.length} 个可行烧成窗</span>，
      共 ${data.feasible_cell_count}/${data.total_cell_count} 格（${pct}%）。
      各窗为三维联合连通区域，按气氛区分，不做单轴合并。</p>`;
  }
}

function renderWindows(data) {
  const el = $("#windows");
  if (!data.windows.length) { el.innerHTML = `<p class="muted">无硬可行窗。</p>`; return; }
  el.innerHTML = data.windows.map((w) => {
    const atmos = w.atmospheres.map((a) => a.label).join("、");
    const times = w.hold_times.map((t) => t.label).join("、");
    return `<div class="window">
      <div class="wtitle">窗 #${w.window_id}：${atmos} 气氛 · ${w.temp_min}–${w.temp_max}℃</div>
      <div>保温时间：${times}</div>
      <div class="muted">覆盖 ${w.cell_count} 个离散参数格；温度为 10℃ 步长的区间证据，非精确温度点。</div>
    </div>`;
  }).join("");
}

function renderCores(data) {
  const el = $("#cores");
  if (!data.conflict) {
    el.innerHTML = `<p class="muted">当前硬证据无冲突核。</p>`;
    return;
  }
  if (!data.minimal_conflict_cores.length) {
    el.innerHTML = `<p class="muted">未检出结构化冲突核（请检查规则）。</p>`;
    return;
  }
  el.innerHTML = data.minimal_conflict_cores.map((core, i) => {
    const items = core.map((id) => {
      const e = data.evidence.find((x) => x.observation_id === id);
      const label = e ? featureLabel(e.feature_key) : id;
      const op = e ? (e.op === "present" ? "出现" : "消失") : "";
      return `<span class="pill">${escapeHtml(label)}（${op}）</span>`;
    }).join("");
    return `<div class="corebox"><strong>冲突核 ${i + 1}（最小，${core.length} 项）</strong><br>${items}
      <div class="muted">核内任意移除一项硬约束，其余观测即恢复非空交集；核外观测不构成当前矛盾。</div></div>`;
  }).join("");
}

function renderSoft(data) {
  const el = $("#softInfo");
  const s = data.soft_summary;
  if (data.soft_observation_ids.length === 0) {
    el.innerHTML = `<p class="muted">尚无降权软证据。</p>`;
    return;
  }
  const within = s.score_within_feasible
    ? `硬可行窗内软证据满足比例 ${(s.score_within_feasible.min * 100).toFixed(0)}%–${(s.score_within_feasible.max * 100).toFixed(0)}%（均值 ${(s.score_within_feasible.mean * 100).toFixed(0)}%）。`
    : `硬可行区为空：全网格最佳软证据满足比例仅 ${(s.best_score_overall * 100).toFixed(0)}%，提示冲突观测本身值得复核而非放宽规则。`;
  el.innerHTML = `<p>降权观测：${data.soft_observation_ids.map(escapeHtml).join("、")}。<br>${within}</p>`;
}

const CELL_W = 14;
const CELL_H = 26;
const PAD_L = 78;
const PAD_T = 28;
const XSKIP = 8;

function renderHeatmaps(data) {
  const host = $("#heatmaps");
  host.innerHTML = "";
  const grid = data.grid;
  const overlays = Object.values(state.evidenceOverlay);

  grid.atmospheres.forEach((atmo, ai) => {
    const wrap = document.createElement("div");
    wrap.className = "pane";
    const width = PAD_L + grid.temps.length * CELL_W + 10;
    const height = PAD_T + grid.hold_times.length * CELL_H + 24;
    wrap.innerHTML = `<h4>${atmo.label}气氛</h4>`;
    const cv = document.createElement("canvas");
    cv.width = width * 2;
    cv.height = height * 2;
    cv.style.width = width + "px";
    cv.style.height = height + "px";
    const ctx = cv.getContext("2d");
    ctx.scale(2, 2);
    ctx.font = "11px sans-serif";
    ctx.textBaseline = "middle";

    ctx.fillStyle = "#7a6f62";
    grid.hold_times.forEach((t, ti) => {
      const y = PAD_T + ti * CELL_H + CELL_H / 2;
      const short = t.label.replace(/保温/g, "");
      ctx.fillText(short, 4, y);
    });
    grid.temps.forEach((temp, k) => {
      if (k % XSKIP !== 0 && k !== grid.temps.length - 1) return;
      const x = PAD_L + k * CELL_W;
      ctx.save();
      ctx.translate(x, PAD_T - 12);
      ctx.rotate(-Math.PI / 4);
      ctx.textAlign = "right";
      ctx.fillText(String(temp), 0, 0);
      ctx.restore();
    });
    ctx.textAlign = "left";

    for (let ti = 0; ti < grid.hold_times.length; ti++) {
      for (let k = 0; k < grid.temps.length; k++) {
        const x = PAD_L + k * CELL_W;
        const y = PAD_T + ti * CELL_H;
        const feasible = data.feasible[ai][ti][k];
        const soft = data.soft_score[ai][ti][k];
        ctx.fillStyle = cellColor(feasible, soft, data.conflict);
        ctx.fillRect(x, y, CELL_W - 1, CELL_H - 1);
        overlays.forEach((mask) => {
          if (mask[ai][ti][k]) {
            ctx.strokeStyle = "rgba(180,69,47,0.9)";
            ctx.lineWidth = 1.5;
            ctx.strokeRect(x + 1, y + 1, CELL_W - 3, CELL_H - 3);
          }
        });
      }
    }
    wrap.appendChild(cv);
    const tip = (ev) => {
      const rect = cv.getBoundingClientRect();
      const k = Math.floor((ev.clientX - rect.left - PAD_L) / CELL_W);
      const ti = Math.floor((ev.clientY - rect.top - PAD_T) / CELL_H);
      if (k < 0 || k >= grid.temps.length || ti < 0 || ti >= grid.hold_times.length) return;
      cv.title = `${atmo.label} · ${grid.hold_times[ti].label} · ${grid.temps[k]}℃\n` +
        (data.feasible[ai][ti][k] ? "硬可行" : "硬证据排除") +
        `\n软证据满足比例 ${(data.soft_score[ai][ti][k] * 100).toFixed(0)}%`;
    };
    cv.addEventListener("mousemove", tip);
    host.appendChild(wrap);
  });

  const legend = document.createElement("div");
  legend.className = "legend";
  legend.innerHTML =
    `<span class="sw" style="background:#3e7d4f"></span>硬可行` +
    `<span class="sw" style="background:#d9c9a8"></span>冲突下软证据最佳折中区（灰阶）` +
    `<span class="sw" style="background:#f0e9da;border:1px solid #ccc"></span>硬证据排除` +
    `<span class="sw" style="border:2px solid #b4452f;background:#fff"></span>勾选证据单独排除`;
  host.appendChild(legend);
}

function lerp(a, b, t) { return Math.round(a + (b - a) * t); }

function cellColor(feasible, soft, conflict) {
  if (feasible) {
    const g = lerp(0x9f, 0x3e, soft);
    const gg = lerp(0xc4, 0x7d, soft);
    const b = lerp(0xa8, 0x4f, soft);
    return `rgb(${g - 30},${gg},${b - 20})`;
  }
  if (conflict && soft > 0) {
    const t = soft;
    return `rgb(${lerp(0xf0, 0xc9, t)},${lerp(0xe9, 0xa1, t)},${lerp(0xda, 0x3e, t)})`;
  }
  return "#f0e9da";
}

function renderEvents(data) {
  const el = $("#eventLog");
  if (!data.events.length) { el.innerHTML = `<p class="muted">暂无操作。</p>`; return; }
  el.innerHTML = '<div class="eventlog">' + data.events.map((ev) => {
    const p = JSON.stringify(ev.payload);
    return `<div>[${ev.seq}] ${ev.ts} <b>${ev.kind}</b> @${ev.branch} ${escapeHtml(p)}</div>`;
  }).join("") + "</div>";
}

// ---------- 模态框 ----------
const modal = $("#modal");
function openModal(title, bodyHtml) {
  $("#modalTitle").textContent = title;
  $("#modalBody").innerHTML = bodyHtml;
  modal.showModal();
}
$("#modalCancel").addEventListener("click", () => modal.close());

async function promptNewRun() {
  openModal("新建分析单", `
    <div class="row">编号 <input id="mRunId" placeholder="如 case-007"></div>
    <div class="row">名称 <input id="mRunName" placeholder="样品名称"></div>
    <div class="row">原料
      <select id="mMaterial">
        <option value="common">普通泥料 common</option>
        <option value="calcareous">钙质泥料 calcareous</option>
      </select></div>
    <div class="row">规则版本 <select id="mPack"></select></div>
    <div class="row"><textarea id="mNote" placeholder="分支说明（可选）"></textarea></div>`);
  const ps = $("#mPack");
  state.packs.forEach((p) => {
    const o = document.createElement("option");
    o.value = p.version; o.textContent = p.label; ps.appendChild(o);
  });
  $("#modalOk").onclick = async () => {
    try {
      await post("/api/runs", {
        run_id: $("#mRunId").value.trim(),
        name: $("#mRunName").value.trim() || $("#mRunId").value.trim(),
        material: $("#mMaterial").value,
        pack_version: $("#mPack").value,
        branch_note: $("#mNote").value.trim(),
      });
      modal.close();
      await loadRuns();
    } catch (e) { alert(e.message); }
  };
}

async function promptNewBranch() {
  openModal("保留人工解释分支", `
    <p class="muted">从当前分支复制全部观测形成新分支；在新分支上的降权、删除、版本切换不影响主线结论。</p>
    <div class="row">新分支名 <input id="mBranch" placeholder="如 埋藏污染解释"></div>
    <div class="row"><textarea id="mBranchNote" placeholder="人工解释要点（可选）"></textarea></div>`);
  $("#modalOk").onclick = async () => {
    try {
      await post(`/api/runs/${encodeURIComponent(state.runId)}/branches`, {
        new_branch: $("#mBranch").value.trim(),
        from_branch: state.branch,
        note: $("#mBranchNote").value.trim(),
      });
      state.branch = $("#mBranch").value.trim();
      modal.close();
      await refreshRun();
    } catch (e) { alert(e.message); }
  };
}

function download(filename, text) {
  const blob = new Blob([text], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

$("#exportBtn").addEventListener("click", async () => {
  const bundle = await api("/api/export");
  download(`taotai-records-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.json`,
    JSON.stringify(bundle, null, 2));
});

$("#importFile").addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  if (!file) return;
  const text = await file.text();
  try {
    const bundle = JSON.parse(text);
    const res = await post("/api/import", { bundle, replace: true });
    const msg = res.hash_match
      ? `导入成功，状态哈希一致。\n期望：${res.expected_hash}\n实际：${res.actual_hash}`
      : `导入完成但哈希不一致，请复核！\n期望：${res.expected_hash}\n实际：${res.actual_hash}`;
    alert(msg);
    state.evidenceOverlay = {};
    await loadPacks();
    await loadRuns();
  } catch (e) { alert("导入失败：" + e.message); }
  ev.target.value = "";
});

$("#clearBtn").addEventListener("click", async () => {
  if (!confirm("将清空全部分析单与事件日志并重新种入固定 fixture（保留规则包），确定？")) return;
  const res = await post("/api/admin/clear", { reseed: true, keep_packs: true });
  $("#hashBadge").textContent = "重导哈希 " + res.state_hash.slice(0, 12);
  state.evidenceOverlay = {};
  await loadRuns();
});

$("#runSelect").addEventListener("change", async () => {
  state.runId = $("#runSelect").value;
  state.branch = "main";
  state.evidenceOverlay = {};
  await refreshRun();
});
$("#branchSelect").addEventListener("change", async () => {
  state.branch = $("#branchSelect").value;
  state.evidenceOverlay = {};
  await refreshRun();
});
$("#packSelect").addEventListener("change", async () => {
  const v = $("#packSelect").value;
  if (!state.current || v === state.current.state.pack_version) return;
  if (!confirm(`切换到规则版本 ${v}？该操作记入当前分支流水并立即重算。`)) {
    $("#packSelect").value = state.current.state.pack_version;
    return;
  }
  try {
    await post(`/api/runs/${encodeURIComponent(state.runId)}/pack?branch=${encodeURIComponent(state.branch)}`,
      { pack_version: v });
    state.evidenceOverlay = {};
    await refreshRun();
  } catch (e) {
    alert(e.message);
    $("#packSelect").value = state.current.state.pack_version;
  }
});
$("#newRunBtn").addEventListener("click", promptNewRun);
$("#newBranchBtn").addEventListener("click", promptNewBranch);
$("#addObsBtn").addEventListener("click", async () => {
  const id = $("#addId").value.trim() || `obs-${Date.now() % 100000}`;
  try {
    await post(`/api/runs/${encodeURIComponent(state.runId)}/observations?branch=${encodeURIComponent(state.branch)}`, {
      id,
      feature_key: $("#addFeature").value,
      op: $("#addOp").value,
      weight: 1.0,
      contaminated: false,
      note: "",
    });
    $("#addId").value = "";
    await refreshRun();
  } catch (e) { alert(e.message); }
});

(async function init() {
  await loadPacks();
  await loadRuns();
})();
