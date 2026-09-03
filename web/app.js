// Cx-Preflight Edge 라이브 화면. 서버(/api/v1)에서 상태·세션·SSE를 받아 그린다. 판정은 서버 규칙 엔진이 한다.
const API = "/api/v1";
const $ = (id) => document.getElementById(id);

const S = {
  sid: null, cases: [], plan: null, steps: [], nSamples: 0, dt: 1,
  data: { t: [], temp: [], ch1: [], ch2: [], pump: [] }, exceeded: [], states: {}, tRun: {}, verdicts: [],
  chart: null, es: null, now: 0, running: false,
};

// ---------------------------------------------------------------- status
async function statusLoop() {
  while (true) {
    try {
      const d = await (await fetch(`${API}/status`, { cache: "no-store" })).json();
      const o = d.offline || {};
      const off = $("badge-offline");
      if (o.is_offline) { off.className = "badge ok"; off.textContent = "External Cloud Connection: OFF (폐쇄망 확인)"; }
      else { off.className = "badge bad"; off.textContent = `External Cloud Connection: ${o.offline_score}/4 (DNS ${o.dns_blocked ? "차단" : "가능"}, TCP ${o.connect_blocked ? "차단" : "가능"}, 기본경로 ${o.default_route === false ? "없음" : o.default_route === true ? "있음" : "미확인"}, tx ${o.tx_delta_bytes ?? "미측정"})`; }
      const llm = $("badge-llm");
      if (d.llm && d.llm.alive) { llm.className = "badge ok"; llm.textContent = `LLM: ${d.llm.backend} (on-device)`; }
      else { llm.className = "badge unknown"; llm.textContent = "LLM: 없음 (골든·템플릿 폴백)"; }
      $("badge-lat").textContent = `판정 지연 ${d.last_latency_ms == null ? "—" : d.last_latency_ms.toFixed(3)} ms`;
      $("badge-mem").textContent = `RAM ${o.mem_used_mb ?? "—"}/${o.mem_total_mb ?? "—"} MB${o.cpu_load1 != null ? ` · load ${o.cpu_load1.toFixed(2)}` : ""}`;
      if (!S.cases.length && d.cases) { S.cases = d.cases; fillCases(d); }
    } catch (e) { $("badge-offline").className = "badge unknown"; }
    await new Promise((r) => setTimeout(r, 2000));
  }
}

function fillCases(d) {
  const sel = $("sel-case");
  sel.innerHTML = "";
  for (const c of d.cases) { const o = document.createElement("option"); o.value = c.name; o.textContent = `${c.name} — ${c.desc}`; sel.appendChild(o); }
  const tel = $("sel-tel");
  tel.innerHTML = "";
  for (const t of d.telemetry_cases) { const o = document.createElement("option"); o.value = t; o.textContent = t; tel.appendChild(o); }
  sel.onchange = () => { const c = S.cases.find((x) => x.name === sel.value); $("case-desc").textContent = c ? c.desc : ""; tel.value = c ? c.telemetry : "pass"; };
  sel.onchange();
}

// ---------------------------------------------------------------- session
async function createSession() {
  const body = { case: $("sel-case").value, mode: $("sel-mode").value };
  $("btn-create").disabled = true;
  resetRun();
  const meta = await (await fetch(`${API}/sessions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
  S.sid = meta.id;
  $("session-id").textContent = meta.id;
  $("plan-md").textContent = await (await fetch(`${API}/sessions/${S.sid}/plan_md`)).text();
  $("extract-status").textContent = body.mode === "llm" ? "온디바이스 LLM이 절차서를 단계별로 구조화하는 중… (4B 모델, 단계당 수 초)" : "승인본(골든) 규칙을 불러왔습니다.";
  await waitPlanReady();
  $("btn-create").disabled = false;
}

async function waitPlanReady() {
  for (let i = 0; i < 600; i++) {
    const pf = await (await fetch(`${API}/sessions/${S.sid}/preflight`, { cache: "no-store" })).json();
    if (pf.status === "ready") {
      const d = await (await fetch(`${API}/sessions/${S.sid}`)).json();
      S.plan = d.plan; S.steps = d.plan.steps.map((s) => s.id);
      $("rules-backend").textContent = `출처: ${d.meta.backend}` + (d.meta.timings && d.meta.timings.extract_sec ? ` · 추출 ${d.meta.timings.extract_sec.toFixed(1)}s` : "");
      $("extract-status").textContent = d.meta.summary && d.meta.summary.note ? d.meta.summary.note : (d.meta.backend.startsWith("golden") ? "승인본(골든) 규칙" : `LLM 추출 완료 (${d.meta.summary.extract ? d.meta.summary.extract.n_ok + "/" + d.meta.summary.extract.n_total + " 단계" : ""})`);
      renderRules(d.plan);
      renderPreflight(pf);
      renderTimeline();
      $("btn-approve").disabled = false;
      $("btn-run").disabled = true;
      $("approve-status").textContent = "";
      return;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
}

function condText(c) { return c.op === "in_band" ? `${c.tag} ∈ [${c.band[0]}, ${c.band[1]}]` : `${c.tag} ${c.op} ${c.value}`; }

function renderRules(plan) {
  const tb = $("rules-table").querySelector("tbody");
  tb.innerHTML = "";
  for (const s of plan.steps) {
    const tr = document.createElement("tr");
    const exp = s.expected.map((e) => `${condText(e)} (${e.within_sec}/${e.hold_sec})`).join("<br>") || '<span class="dim">없음</span>';
    tr.innerHTML = `<td class="mono"><b>${s.id}</b><br><span class="dim">${s.title}</span></td>
      <td>${s.preconditions.map(condText).join("<br>") || '<span class="dim">없음</span>'}</td>
      <td>${s.trigger ? condText(s.trigger) : '<span class="dim">—</span>'}</td>
      <td>${exp}</td>
      <td>${s.abort_conditions.map(condText).join("<br>") || '<span class="dim">—</span>'}</td>
      <td>${s.rollback.join("; ") || '<span class="dim">없음</span>'}</td>
      <td>${s.changes_state ? "있음" : "—"}</td>`;
    tb.appendChild(tr);
  }
}

function renderPreflight(pf) {
  const ul = $("pf-rule"); ul.innerHTML = "";
  if (!pf.rule.length) { const li = document.createElement("li"); li.className = "empty"; li.textContent = "결함 없음: 6개 검사 통과 (사전조건·순서·기대결과·태그·복구·기준값)"; ul.appendChild(li); }
  for (const f of pf.rule) { const li = document.createElement("li"); li.className = f.severity; li.innerHTML = `<span class="code">${f.code}</span>${f.message}`; ul.appendChild(li); }
  $("pf-summary").textContent = `ERROR ${pf.n_error ?? 0} · WARN ${pf.n_warn ?? 0}`;
  $("pf-summary").style.color = pf.n_error ? "var(--bad)" : (pf.n_warn ? "var(--warn)" : "var(--ok)");
  const ai = $("pf-ai"); ai.innerHTML = "";
  if (!pf.ai || !pf.ai.length) { const li = document.createElement("li"); li.className = "dim"; li.textContent = "AI 제안 없음 (골든 모드이거나 LLM 미연결)"; ai.appendChild(li); }
  for (const f of (pf.ai || [])) { const li = document.createElement("li"); li.className = f.severity; li.innerHTML = `<span class="code">${f.code}</span>${f.message}`; ai.appendChild(li); }
}

async function approve() {
  const body = { approver: $("approver").value, force: $("force").checked };
  const r = await fetch(`${API}/sessions/${S.sid}/approve`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const d = await r.json();
  if (!r.ok) { $("approve-status").textContent = d.detail || "승인 실패"; $("approve-status").style.color = "var(--bad)"; return; }
  $("approve-status").textContent = `승인됨 · ${d.approver} · ${new Date().toLocaleTimeString()}${d.n_error ? ` · ERROR ${d.n_error}건 강제` : ""}`;
  $("approve-status").style.color = d.n_error ? "var(--warn)" : "var(--ok)";
  $("btn-run").disabled = false;
}

// ---------------------------------------------------------------- run + SSE
function resetRun() {
  if (S.es) { S.es.close(); S.es = null; }
  S.data = { t: [], temp: [], ch1: [], ch2: [], pump: [] }; S.exceeded = []; S.states = {}; S.tRun = {}; S.verdicts = []; S.now = 0; S.running = false;
  $("verdict-list").innerHTML = ""; $("verdict-count").textContent = "";
  $("report").textContent = "실행이 끝나면 한국어 기록 초안이 생성됩니다."; $("report-meta").textContent = "";
  $("tl-overall").textContent = ""; $("run-status").textContent = "";
  setVerdictCard(null);
  if (S.chart) { S.chart.setData([[], [], [], [], []]); }
}

async function run() {
  resetRun();
  const speed = parseFloat($("sel-speed").value);
  $("btn-run").disabled = true;
  S.es = new EventSource(`${API}/sessions/${S.sid}/stream`);
  S.es.onmessage = (m) => handleEvent(JSON.parse(m.data));
  S.es.onerror = () => { $("run-status").textContent += " (stream 끊김)"; };
  const r = await fetch(`${API}/sessions/${S.sid}/run`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ telemetry: $("sel-tel").value, speed }) });
  if (!r.ok) { $("run-status").textContent = (await r.json()).detail; $("btn-run").disabled = false; }
}

function handleEvent(ev) {
  switch (ev.type) {
    case "run_start":
      S.nSamples = ev.n_samples; S.steps = ev.steps; S.running = true;
      $("run-status").textContent = `${ev.telemetry} · ${ev.speed}x · 0/${ev.n_samples}`;
      renderTimeline();
      break;
    case "sample": {
      const v = ev.values; S.now = ev.t;
      S.data.t.push(ev.t); S.data.temp.push(v.CHWS_T_SUP ?? null); S.data.ch1.push(v.CH1_STATUS ?? null); S.data.ch2.push(v.CH2_STATUS ?? null); S.data.pump.push(v.CHWP2_STATUS ?? null);
      S.states = ev.states;
      if (S.data.t.length % 2 === 0 || !S.running) updateChart();
      renderTimeline();
      $("run-status").textContent = `${$("sel-tel").value} · t=${ev.t.toFixed(0)}s · ${S.data.t.length}/${S.nSamples}`;
      break;
    }
    case "event":
      if (ev.to_state === "RUNNING") S.tRun[ev.step_id] = { t0: ev.t, t1: null };
      if (["PASS", "FAIL", "ABORT"].includes(ev.to_state) && S.tRun[ev.step_id]) S.tRun[ev.step_id].t1 = ev.t;
      if (ev.to_state === "ARMED") S.tRun[ev.step_id] = S.tRun[ev.step_id] || { t0: ev.t, t1: null, armed: true };
      break;
    case "verdict":
      S.verdicts.push(ev);
      addVerdict(ev);
      if (ev.state !== "SKIPPED") setVerdictCard(ev);
      $("badge-lat").textContent = `판정 지연 ${ev.latency_ms.toFixed(3)} ms`;
      break;
    case "segment":
      if (ev.kind === "exceeded") { S.exceeded.push(ev); updateChart(); renderTimeline(); }
      break;
    case "run_done":
      S.running = false; updateChart(); renderTimeline();
      $("tl-overall").textContent = `종합 ${ev.summary.overall} · PASS ${ev.summary.n_pass} · FAIL ${ev.summary.n_fail} · SKIPPED ${ev.summary.n_skipped} · ${ev.timings.run_wall_sec.toFixed(1)}s`;
      $("tl-overall").style.color = ev.summary.overall === "PASS" ? "var(--ok)" : "var(--bad)";
      $("run-status").textContent += ` · 완료 · 외부 전송 ${ev.offline.tx_delta_bytes == null ? "미측정" : ev.offline.tx_delta_bytes + " B"}`;
      $("report").textContent = "보고서 생성 중… (온디바이스 LLM 종합 의견)";
      break;
    case "report":
      loadReport(ev);
      break;
    case "error":
      $("run-status").textContent = "오류: " + ev.message; break;
    case "done":
      if (S.es) { S.es.close(); S.es = null; }
      $("btn-run").disabled = false; $("btn-report").disabled = false;
      break;
  }
}

async function loadReport(ev) {
  $("report").textContent = await (await fetch(`${API}/sessions/${S.sid}/report`, { cache: "no-store" })).text();
  $("report-meta").textContent = `문안 출처 ${ev.source} · ${(ev.sec || 0).toFixed(1)}s`;
}

async function regenReport() {
  $("btn-report").disabled = true; $("report").textContent = "재생성 중…";
  const d = await (await fetch(`${API}/sessions/${S.sid}/report`, { method: "POST" })).json();
  await loadReport(d); $("btn-report").disabled = false;
}

// ---------------------------------------------------------------- verdict UI
function setVerdictCard(v) {
  const c = $("verdict-card");
  if (!v) { c.className = "verdict unknown"; c.querySelector(".vstate").textContent = "—"; c.querySelector(".vstep").textContent = ""; c.querySelector(".vreason").textContent = "아직 판정 없음"; c.querySelector(".vev").textContent = ""; return; }
  c.className = `verdict ${v.state}`;
  c.querySelector(".vstate").textContent = v.state;
  const s = S.plan ? S.plan.steps.find((x) => x.id === v.step_id) : null;
  c.querySelector(".vstep").textContent = `${v.step_id} ${s ? s.title : ""}`;
  c.querySelector(".vreason").textContent = v.reason_ko;
  const e = v.evidence || {};
  c.querySelector(".vev").textContent = [e.tag ? `tag ${e.tag}` : null, e.t != null ? `t=${e.t}s` : null, e.value != null ? `값 ${e.value}` : null, e.deadline != null ? `마감 ${e.deadline}s` : null, `지연 ${v.latency_ms.toFixed(3)}ms`].filter(Boolean).join(" · ");
}

function addVerdict(v) {
  const li = document.createElement("li");
  li.innerHTML = `<span class="mono">${v.t_end == null ? "-" : v.t_end.toFixed(0)}s</span><span class="st ${v.state}">${v.step_id} ${v.state}</span><span>${v.reason_ko}</span>`;
  $("verdict-list").prepend(li);
  $("verdict-count").textContent = `${S.verdicts.length}건`;
}

// ---------------------------------------------------------------- timeline
function renderTimeline() {
  const el = $("timeline");
  const total = Math.max(S.nSamples ? (S.nSamples - 1) * S.dt : 200, S.now + 1);
  el.innerHTML = "";
  for (const id of S.steps) {
    const st = S.states[id] || "PENDING";
    const s = S.plan ? S.plan.steps.find((x) => x.id === id) : null;
    const row = document.createElement("div"); row.className = "tl-row";
    const label = document.createElement("div"); label.innerHTML = `<b>${id}</b> <span class="dim">${s ? s.title : ""}</span> <span class="tl-state">${st}</span>`;
    const track = document.createElement("div"); track.className = "tl-track";
    const tr = S.tRun[id];
    if (tr) {
      const t1 = tr.t1 != null ? tr.t1 : S.now;
      const bar = document.createElement("div"); bar.className = `tl-bar ${st === "PENDING" ? "ARMED" : st}`;
      bar.style.left = `${(tr.t0 / total) * 100}%`; bar.style.width = `${Math.max(0.5, ((t1 - tr.t0) / total) * 100)}%`;
      track.appendChild(bar);
    }
    for (const ex of S.exceeded.filter((x) => x.step_id === id)) {
      const d = document.createElement("div"); d.className = "tl-ex";
      const t1 = ex.t1 != null ? ex.t1 : S.now;
      d.style.left = `${(ex.t0 / total) * 100}%`; d.style.width = `${Math.max(0.5, ((t1 - ex.t0) / total) * 100)}%`;
      track.appendChild(d);
    }
    if (S.running) { const n = document.createElement("div"); n.className = "tl-now"; n.style.left = `${(S.now / total) * 100}%`; track.appendChild(n); }
    row.appendChild(label); row.appendChild(track); el.appendChild(row);
  }
}

// ---------------------------------------------------------------- chart (uPlot)
function makeChart() {
  const css = getComputedStyle(document.documentElement);
  const col = (n) => css.getPropertyValue(n).trim();
  const opts = {
    width: $("chart").clientWidth || 600, height: 300,
    scales: { x: { time: false }, temp: { range: [4, 18] }, bin: { range: [-0.1, 1.3] } },
    axes: [
      { stroke: col("--dim"), grid: { stroke: col("--line") }, label: "t (s)" },
      { scale: "temp", stroke: col("--dim"), grid: { stroke: col("--line") }, label: "°C" },
      { scale: "bin", side: 1, stroke: col("--dim"), grid: { show: false }, values: (u, v) => v.map((x) => (x === 0 || x === 1 ? x : "")) },
    ],
    series: [
      {},
      { label: "CHWS_T_SUP", scale: "temp", stroke: col("--temp"), width: 2, spanGaps: false },
      { label: "CH1", scale: "bin", stroke: col("--ch1"), width: 1.5, paths: uPlot.paths.stepped({ align: 1 }) },
      { label: "CH2", scale: "bin", stroke: col("--ch2"), width: 1.5, paths: uPlot.paths.stepped({ align: 1 }) },
      { label: "CHWP2", scale: "bin", stroke: col("--pump"), width: 1.5, paths: uPlot.paths.stepped({ align: 1 }), dash: [4, 3] },
    ],
    hooks: {
      drawClear: [(u) => {
        const ctx = u.ctx; ctx.save();
        const x0 = u.bbox.left, x1 = u.bbox.left + u.bbox.width;
        // 허용 band [6, 8]
        const yb0 = u.valToPos(8, "temp", true), yb1 = u.valToPos(6, "temp", true);
        ctx.fillStyle = col("--band"); ctx.fillRect(x0, yb0, x1 - x0, yb1 - yb0);
        // 중단 기준 14 °C
        const y14 = u.valToPos(14, "temp", true);
        ctx.strokeStyle = col("--bad"); ctx.setLineDash([6, 4]); ctx.beginPath(); ctx.moveTo(x0, y14); ctx.lineTo(x1, y14); ctx.stroke(); ctx.setLineDash([]);
        // 허용시간 초과 구간
        for (const ex of S.exceeded) {
          const t1 = ex.t1 != null ? ex.t1 : S.now;
          const px0 = u.valToPos(ex.t0, "x", true), px1 = u.valToPos(t1, "x", true);
          ctx.fillStyle = col("--ex"); ctx.fillRect(px0, u.bbox.top, Math.max(2, px1 - px0), u.bbox.height);
        }
        // 현재 시각
        if (S.running) { const px = u.valToPos(S.now, "x", true); ctx.strokeStyle = "#fff"; ctx.globalAlpha = 0.5; ctx.beginPath(); ctx.moveTo(px, u.bbox.top); ctx.lineTo(px, u.bbox.top + u.bbox.height); ctx.stroke(); }
        ctx.restore();
      }],
    },
  };
  S.chart = new uPlot(opts, [[], [], [], [], []], $("chart"));
  window.addEventListener("resize", () => S.chart.setSize({ width: $("chart").clientWidth, height: 300 }));
}

function updateChart() {
  if (!S.chart) return;
  S.chart.setData([S.data.t, S.data.temp, S.data.ch1, S.data.ch2, S.data.pump]);
}

// ---------------------------------------------------------------- 저장된 세션 열기 (?session=ID)
async function loadExisting(sid) {
  const d = await (await fetch(`${API}/sessions/${sid}`, { cache: "no-store" })).json();
  if (!d.meta) return;
  resetRun();
  S.sid = sid; $("session-id").textContent = sid;
  $("plan-md").textContent = await (await fetch(`${API}/sessions/${sid}/plan_md`)).text();
  S.plan = d.plan; S.steps = d.plan.steps.map((s) => s.id);
  $("rules-backend").textContent = `출처: ${d.meta.backend}`;
  $("extract-status").textContent = `저장된 세션 (${d.meta.case})`;
  renderRules(d.plan);
  renderPreflight({ status: "ready", ...(d.preflight || { rule: [], ai: [] }) });
  $("btn-approve").disabled = false;
  if (d.approved) { $("approve-status").textContent = `승인됨 · ${d.meta.summary.approved_by || ""}`; $("approve-status").style.color = "var(--ok)"; $("btn-run").disabled = false; }
  if (d.has_telemetry) {
    const tel = await (await fetch(`${API}/sessions/${sid}/telemetry`)).json();
    S.nSamples = tel.rows.length;
    for (const r of tel.rows) { S.data.t.push(r.t_sec); S.data.temp.push(r.CHWS_T_SUP ?? null); S.data.ch1.push(r.CH1_STATUS ?? null); S.data.ch2.push(r.CH2_STATUS ?? null); S.data.pump.push(r.CHWP2_STATUS ?? null); }
    S.now = S.data.t[S.data.t.length - 1] || 0;
    S.exceeded = (d.segments || []).filter((g) => g.kind === "exceeded");
    for (const v of d.verdicts || []) { S.states[v.step_id] = v.state; S.tRun[v.step_id] = { t0: v.t_start ?? v.t_end, t1: v.t_end }; S.verdicts.push(v); addVerdict(v); if (v.state !== "SKIPPED") setVerdictCard(v); }
    updateChart(); renderTimeline();
    const s = d.meta.summary || {};
    $("tl-overall").textContent = `종합 ${s.overall || "-"} · PASS ${s.n_pass ?? "-"} · FAIL ${s.n_fail ?? "-"} · SKIPPED ${s.n_skipped ?? "-"}`;
    $("tl-overall").style.color = s.overall === "PASS" ? "var(--ok)" : "var(--bad)";
    $("run-status").textContent = `${s.telemetry || ""} · 저장본 · 외부 전송 ${d.meta.offline && d.meta.offline.tx_delta_bytes != null ? d.meta.offline.tx_delta_bytes + " B" : "미측정"}`;
    if (d.has_report) { $("report").textContent = await (await fetch(`${API}/sessions/${sid}/report`)).text(); $("report-meta").textContent = `문안 출처 ${(d.remarks || {}).source || "-"}`; $("btn-report").disabled = false; }
  }
}

// ---------------------------------------------------------------- boot
$("btn-create").onclick = createSession;
$("btn-approve").onclick = approve;
$("btn-run").onclick = run;
$("btn-report").onclick = regenReport;
$("btn-toggle-md").onclick = () => $("plan-md").classList.toggle("collapsed");
makeChart();
statusLoop();
{
  const q = new URLSearchParams(location.search).get("session");
  if (q) loadExisting(q);
}
