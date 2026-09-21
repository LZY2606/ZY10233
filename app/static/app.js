"use strict";
const state = { meta: null, sampleId: null, packId: "v1", computed: null, weights: {}, highlight: null };

const $ = (id) => document.getElementById(id);

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  return res.json();
}

function unpackMask(packed) {
  const bin = atob(packed.data);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const n = packed.shape[0] * packed.shape[1] * packed.shape[2];
  const bits = [];
  for (const b of bytes) for (let k = 7; k >= 0; k--) bits.push((b >> k) & 1);
  return Uint8Array.from(bits.slice(0, n));
}

const atmName = { O: "氧化", R: "还原", N: "中性" };
const atmColor = { good: "#4caf7d", soft: "#c9a24b", excluded: "#3a434d", naive: "#7e6a3a", empty: "#0f1317" };

async function init() {
  state.meta = await api("/api/meta");
  for (const s of state.meta.samples) {
    const opt = document.createElement("option");
    opt.value = s.sample_id; opt.textContent = s.name;
    $("sampleSel").appendChild(opt);
  }
  for (const p of state.meta.packs) {
    const opt = document.createElement("option");
    opt.value = p.pack_id; opt.textContent = `${p.pack_id} · ${p.name}`;
    $("packSel").appendChild(opt);
  }
  $("sampleSel").value = "NC-01";
  $("packSel").value = state.packId;
  state.sampleId = "NC-01";

  $("sampleSel").onchange = async (e) => { state.sampleId = e.target.value; await loadSample(); await solve(); };
  $("packSel").onchange = async (e) => {
    state.packId = e.target.value;
    const p = state.meta.packs.find(x => x.pack_id === state.packId);
    $("packNote").textContent = p ? p.note : "";
    await solve();
  };
  $("solveBtn").onclick = () => solve();
  $("saveRunBtn").onclick = saveRun;
  $("branchBtn").onclick = saveBranch;
  $("exportBtn").onclick = () => { window.location = "/api/export"; };
  $("resetBtn").onclick = resetDb;
  $("importBtn").onclick = importData;

  $("map").addEventListener("mousemove", onMapHover);
  $("map").addEventListener("mouseleave", () => { $("tooltip").style.display = "none"; });

  await loadSample();
  await solve();
  await refreshRunsBranches();
}

async function loadSample() {
  const detail = await api(`/api/samples/${state.sampleId}`);
  state.weights = {};
  for (const o of detail.observations) state.weights[o.obs_id] = o.weight;
  const p = state.meta.packs.find(x => x.pack_id === state.packId);
  $("packNote").textContent = p ? p.note : "";
}

function provisionalPayload() {
  return Object.entries(state.weights).map(([obs_id, weight]) => ({ obs_id, weight }));
}

async function solve() {
  const body = { pack_id: state.packId, provisional: provisionalPayload() };
  state.computed = await api(`/api/samples/${state.sampleId}/solve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  renderEvidence();
  renderStatusAndMap();
  renderConflicts();
}

function setWeight(obsId, weight) {
  state.weights[obsId] = weight;
  solve();
}

function renderEvidence() {
  const { result, observations } = state.computed;
  const evs = result.evidence;
  const labels = state.meta.catalog.features;
  const vlabels = state.meta.catalog.values;
  const box = $("evidenceList");
  box.innerHTML = "";
  evs.forEach((ev) => {
    const div = document.createElement("div");
    div.className = "ev";
    const contamTag = ev.contaminated ? `<span class="tag contam">疑似污染</span>` : "";
    const cls = ev.weight === "hard" ? "hardb" : ev.weight === "soft" ? "softb" : "offb";
    div.innerHTML = `
      <div class="top">
        <div><b>${labels[ev.feature_id] || ev.feature_id}</b> · ${vlabels[ev.value] || ev.value}</div>
        <span class="tag ${cls}">${{hard:"确认", soft:"降权", off:"停用"}[ev.weight]}</span>
      </div>
      <div class="muted" style="font-size:12px">${ev.note || ""} ${contamTag}</div>
      <div class="row seg" style="margin-top:6px">
        ${["hard","soft","off"].map(w =>
          `<button data-id="${ev.obs_id}" data-w="${w}" class="${ev.weight === w ? "on" : ""}">${{hard:"确认",soft:"降权",off:"停用"}[w]}</button>`
        ).join("")}
      </div>
      <details><summary>规则区域 / 排除区域</summary>
        <div class="region">支持：\n${(ev.region_text || []).join("\n") || "（停用，不参与）"}</div>
        <div class="region" style="color:#c8a">排除：\n${(ev.excluded_text || []).join("\n") || "—"}</div>
      </details>`;
    div.querySelectorAll(".seg button").forEach(btn => {
      btn.onclick = () => setWeight(btn.dataset.id, btn.dataset.w);
    });
    box.appendChild(div);
  });
}

function flatIdx(mask, a, t, h) {
  const [A, T, H] = mask.shape;
  return (a * T + t) * H + h;
}

function anyAlong(mask, a, t) {
  const H = mask.shape[2];
  for (let h = 0; h < H; h++) if (mask.data[flatIdx(mask, a, t, h)]) return true;
  return false;
}

function holdRanges(mask, a, t) {
  const H = mask.shape[2];
  const holds = state.computed.result.axes.holds;
  const ranges = [];
  let start = null;
  for (let h = 0; h < H; h++) {
    const on = mask.data[flatIdx(mask, a, t, h)];
    if (on && start === null) start = h;
    if (start !== null && (!on || h === H - 1)) {
      const end = on ? h : h - 1;
      ranges.push([holds[start], holds[end]]);
      start = null;
    }
  }
  return ranges;
}

function fmtRange(r) {
  const f = (x) => (Number.isInteger(x) ? x : x.toFixed(1));
  return `${f(r[0])}–${f(r[1])}h`;
}

let masksCache = {};

function renderStatusAndMap() {
  const r = state.computed.result;
  masksCache = {
    feas: { shape: r.feasible.mask.shape, data: unpackMask(r.feasible.mask) },
    naive: { shape: r.naive_projection.mask.shape, data: unpackMask(r.naive_projection.mask) },
    tension: { shape: r.feasible.mask.shape, data: new Uint8Array(r.feasible.mask.shape.reduce((a,b)=>a*b,1)) },
    evidence: {},
  };
  for (const t of r.tensions) {
    if (!t.mask) continue;
    const m = unpackMask(t.mask);
    m.forEach((v, i) => { if (v) masksCache.tension.data[i] = 1; });
  }
  for (const ev of r.evidence) {
    if (ev.status === "off") continue;
    masksCache.evidence[ev.obs_id] = { shape: ev.mask.shape, data: unpackMask(ev.mask), ev };
  }

  const line = $("statusLine");
  if (r.has_conflict) {
    line.innerHTML = `<span class="badline"><b>当前硬证据互相矛盾：联合窗为空。</b></span>
      <span class="muted">系统不扩宽规则；请查看下方最小冲突核心，或对受污染证据降权。</span>`;
  } else {
    line.innerHTML = `<span class="okline"><b>存在可行烧成窗（${r.feasible.cell_count} 个参数单元）。</b></span>`;
  }
  $("feasibleText").textContent = r.feasible.empty
    ? "（空）"
    : r.feasible.region_text.map((x, i) => `窗 ${i + 1}：${x}`).join("\n");

  drawMap();
}

function emptyMask(shape) {
  const n = shape.reduce((a, b) => a * b, 1);
  return { shape, data: new Uint8Array(n) };
}

function drawMap() {
  const cv = $("map");
  const ctx = cv.getContext("2d");
  const r = state.computed.result;
  const T = r.axes.temperatures.length;
  const cellW = 14, rowH = 82, labelW = 44;
  cv.width = labelW + T * cellW;
  cv.height = 3 * rowH + 24;
  ctx.fillStyle = "#0f1317";
  ctx.fillRect(0, 0, cv.width, cv.height);

  for (let a = 0; a < 3; a++) {
    for (let t = 0; t < T; t++) {
      const x = labelW + t * cellW, y = a * rowH;
      const feas = anyAlong(masksCache.feas, a, t);
      const tension = anyAlong(masksCache.tension, a, t);
      const naive = anyAlong(masksCache.naive, a, t);
      let fill = "#232b33";
      if (feas) fill = tension ? atmColor.soft : atmColor.good;
      if (!feas && state.highlight) {
        const em = masksCache.evidence[state.highlight];
        if (em && !anyAlong(em, a, t)) fill = "#33404c";
      }
      ctx.fillStyle = fill;
      ctx.fillRect(x, y, cellW - 1, rowH - 2);
      if (naive && !feas) {
        ctx.strokeStyle = atmColor.naive;
        ctx.lineWidth = 2;
        ctx.strokeRect(x + 1, y + 1, cellW - 3, rowH - 4);
      }
    }
  }
  ctx.fillStyle = "#97a3ad";
  ctx.font = "12px sans-serif";
  ["氧化", "还原", "中性"].forEach((name, a) => {
    ctx.fillText(name, 6, a * rowH + rowH / 2);
  });
  for (let t = 0; t < T; t += 5) {
    ctx.save();
    ctx.fillStyle = "#97a3ad";
    ctx.font = "10px sans-serif";
    ctx.fillText(String(r.axes.temperatures[t]), labelW + t * cellW - 8, 3 * rowH + 14);
    ctx.restore();
  }
  ctx.fillStyle = "#97a3ad";
  ctx.fillText("温度 ℃ →", labelW, 3 * rowH + 14);
}

function onMapHover(e) {
  if (!state.computed) return;
  const cv = $("map");
  const rect = cv.getBoundingClientRect();
  const x = e.clientX - rect.left, y = e.clientY - rect.top;
  const cellW = 14, rowH = 82, labelW = 44;
  const a = Math.floor(y / rowH);
  const t = Math.floor((x - labelW) / cellW);
  const T = state.computed.result.axes.temperatures.length;
  const tip = $("tooltip");
  if (a < 0 || a > 2 || t < 0 || t >= T) { tip.style.display = "none"; return; }
  const r = state.computed.result;
  const atm = r.axes.atmospheres[a];
  const temp = r.axes.temperatures[t];
  const feasRanges = holdRanges(masksCache.feas, a, t);
  const naiveRanges = holdRanges(masksCache.naive, a, t);
  let html = `<b>${atmName[atm]} · ${temp}℃</b><br>`;
  html += feasRanges.length ? `可行保温：${feasRanges.map(fmtRange).join("，")}<br>` : `该列无联合可行保温段<br>`;
  if (naiveRanges.length && !feasRanges.length) {
    html += `<span style="color:#c9a24b">朴素单轴交集（虚假）：${naiveRanges.map(fmtRange).join("，")}</span><br>`;
  }
  if (state.highlight) {
    const em = masksCache.evidence[state.highlight];
    if (em && !anyAlong(em, a, t)) {
      html += `<span style="color:#e06a5a">被证据 ${state.highlight}（${state.meta.catalog.features[em.ev.feature_id]}）排除</span>`;
    }
  }
  tip.innerHTML = html;
  tip.style.display = "block";
  tip.style.left = Math.min(e.clientX + 14, window.innerWidth - 340) + "px";
  tip.style.top = (e.clientY + 14) + "px";
}

function renderConflicts() {
  const r = state.computed.result;
  const box = $("conflictBox");
  if (!r.has_conflict) {
    box.innerHTML = `<div class="okline">无硬冲突。当前确认证据存在非空联合烧成窗。</div>`;
  } else {
    box.innerHTML = r.conflict_cores.map((c, i) => {
      const names = c.members.map(id => {
        const ev = r.evidence.find(x => x.obs_id === id);
        return ev ? `${id}·${state.meta.catalog.features[ev.feature_id]}` : id;
      });
      return `<div class="conflict"><b>冲突核 ${i + 1}</b>（证据数 ${c.cardinality}，总权重 ${c.weight_sum}）：<br>${names.join(" ＋ ")}
        <div class="muted" style="font-size:12px">至少否定/降权其中一项才能解除冲突；权重最低的核是最省力缓解方向。</div></div>`;
    }).join("");
  }

  const sens = $("sensBox");
  sens.innerHTML = "<h2 style='font-size:14px;color:#d9a441;margin:6px 0'>敏感性：忽略单条确认证据</h2>" +
    "<table><tr><th>证据</th><th>是否解除冲突</th><th>忽略后可行窗（示例）</th></tr>" +
    r.sensitivity.map(sv => {
      const ev = r.evidence.find(x => x.obs_id === sv.obs_id);
      const name = ev ? state.meta.catalog.features[ev.feature_id] : sv.obs_id;
      return `<tr><td>${sv.obs_id}·${name}</td>
        <td class="${sv.resolves_conflict ? "softline" : "muted"}">${sv.resolves_conflict ? "是" : "否"}</td>
        <td class="region">${(sv.feasible_text || []).join("\n") || "（空）"}</td></tr>`;
    }).join("") + "</table>";

  const nb = $("naiveBox");
  const sp = r.naive_projection.spurious_extra;
  nb.innerHTML = sp > 0
    ? `<div class="warn"><b>单轴交集警告：</b>把各证据先投影到温度/气氛/时间三轴再独立取交会虚增 <b>${sp}</b> 个参数单元（图中黄褐色描边）。
       这些单元并不满足任一证据的原始条件组合——联合窗必须整体求交，不能拆成单轴区间。</div>`
    : `<div class="muted">本样当前朴素单轴交集与联合窗一致（未虚增）；可切换到 NC-02 对照样查看典型虚假区。</div>`;
}

async function saveRun() {
  const note = prompt("运行记录备注（可留空）：", "") || "";
  await api(`/api/samples/${state.sampleId}/solve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pack_id: state.packId, provisional: provisionalPayload(), save: true, note }),
  });
  await refreshRunsBranches();
  alert("运行记录已保存");
}

async function saveBranch() {
  const name = prompt("人工解释分支名称：", `分支 @ ${new Date().toLocaleString()}`);
  if (!name) return;
  // 分支保存当前页面权重：先落库权重再建分支（简单口径：用当前权重重写）
  for (const [obs_id, weight] of Object.entries(state.weights)) {
    await api(`/api/samples/${state.sampleId}/observations/${obs_id}/weight`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ weight }),
    });
  }
  await api(`/api/samples/${state.sampleId}/branches`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, pack_id: state.packId }),
  });
  await refreshRunsBranches();
  alert("分支已保存");
}

async function resetDb() {
  if (!confirm("清空样品/观测/运行/分支并恢复固定 fixture？")) return;
  await api("/api/admin/reset", { method: "POST" });
  await loadSample();
  await solve();
  await refreshRunsBranches();
}

async function importData() {
  const f = $("importFile").files[0];
  if (!f) return alert("请先选择导出的 JSON 文件");
  const data = JSON.parse(await f.text());
  const res = await api("/api/import", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ data }),
  });
  alert(`导入完成：${JSON.stringify(res.counts)}`);
  await loadSample();
  await solve();
  await refreshRunsBranches();
}

async function refreshRunsBranches() {
  const runs = (await api("/api/runs")).runs;
  $("runs").innerHTML = "<h2 style='font-size:14px;color:#d9a441;margin:10px 0 4px'>运行记录</h2>" +
    (runs.length ? "<table><tr><th>ID</th><th>样品</th><th>版本</th><th>时间</th><th></th></tr>" +
      runs.map(rn => `<tr><td class="mono">${rn.run_id}</td><td>${rn.sample_id}</td><td>${rn.pack_id}</td>
        <td class="mono">${rn.created_at}</td>
        <td><button data-run="${rn.run_id}" class="replay">重放</button></td></tr>`).join("") + "</table>"
      : `<div class="muted">暂无</div>`);
  document.querySelectorAll("button.replay").forEach(b => b.onclick = async () => {
    const rep = await api(`/api/runs/${b.dataset.run}/replay`, { method: "POST" });
    alert(`重放摘要：${rep.stored_digest} → ${rep.replay_digest}\n一致：${rep.matches}\n冲突：${rep.has_conflict}，可行窗空：${rep.feasible_empty}`);
  });

  const brs = (await api("/api/branches")).branches;
  $("branches").innerHTML = "<h2 style='font-size:14px;color:#d9a441;margin:10px 0 4px'>人工解释分支</h2>" +
    (brs.length ? brs.map(b => `<div class="region">${b.branch_id} · ${b.name}（${b.sample_id}/${b.pack_id}，${b.created_at}）</div>`).join("")
      : `<div class="muted">暂无</div>`);
}

init().catch(err => {
  console.error(err);
  document.body.insertAdjacentHTML("afterbegin", `<div class="warn">加载失败：${err.message}</div>`);
});
