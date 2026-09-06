// Cx-Preflight Edge 라이브 화면. 서버(/api/v1)에서 상태·세션·SSE를 받아 그린다. 판정은 서버 규칙 엔진이 한다.
// 한 페이지 두 모드: body.mode-prep(세션·절차서·규칙·Preflight·승인·실행 버튼) / body.mode-run(모식도·차트·타임라인·실측 로그·판정).
const API = "/api/v1";
const $ = (id) => document.getElementById(id);
const CHART_H = 300;

const S = {
  sid: null, cases: [], plan: null, steps: [], nSamples: 0, dt: 1,
  data: { t: [], temp: [], ch1: [], ch2: [], pump: [] }, exceeded: [], states: {}, tRun: {}, tArm: {}, verdicts: [],
  chart: null, es: null, now: 0, running: false, mode: "prep",
  active: null, lastVals: null, frozen: {}, hasRun: false, overall: null,
  mimicFreeze: null,   // FAIL/HOLD/ABORT 시점의 계통 상태 고정 {vals, t, step, state}
};

// ---------------------------------------------------------------- mode
function setMode(m) {
  S.mode = m;
  document.body.classList.toggle("mode-prep", m === "prep");
  document.body.classList.toggle("mode-run", m === "run");
  $("nav-run").hidden = !S.hasRun;
  if (m === "run" && S.chart) requestAnimationFrame(() => S.chart.setSize({ width: $("chart").clientWidth, height: CHART_H }));
  setModeBadge();
}

function setModeBadge() {
  const b = $("badge-mode");
  if (S.mode === "prep") { b.className = "mode prep"; b.textContent = "준비"; return; }
  if (S.running) { b.className = "mode running"; b.textContent = "RUNNING"; return; }
  if (S.overall) { b.className = `mode done ${S.overall}`; b.textContent = `완료 ${S.overall}`; return; }
  b.className = "mode run"; b.textContent = "실행";
}

function totalSec() { return Math.max(S.nSamples ? (S.nSamples - 1) * S.dt : 200, S.now); }

function setElapsed() { $("badge-t").textContent = `t ${S.now.toFixed(0)} / ${totalSec().toFixed(0)} s`; }

// ---------------------------------------------------------------- status
async function statusLoop() {
  while (true) {
    try {
      const d = await (await fetch(`${API}/status`, { cache: "no-store" })).json();
      const o = d.offline || {};
      const off = $("badge-offline");
      if (o.is_offline) { off.className = "badge ok"; off.textContent = "외부 통신 차단 확인"; }
      else { off.className = "badge bad"; off.textContent = `외부 통신 가능 ${o.offline_score}/4 (DNS ${o.dns_blocked ? "차단" : "가능"}, TCP ${o.connect_blocked ? "차단" : "가능"}, 기본경로 ${o.default_route === false ? "없음" : o.default_route === true ? "있음" : "미확인"})`; }
      const llm = $("badge-llm");
      if (d.llm && d.llm.alive) { llm.className = "badge ok"; llm.textContent = `LLM ${d.llm.backend} on-device`; }
      else { llm.className = "badge unknown"; llm.textContent = "LLM 없음, 골든/템플릿 폴백"; }
      if (!S.running) $("badge-lat").textContent = `판정 지연 ${d.last_latency_ms == null ? "-" : d.last_latency_ms.toFixed(3) + " ms"}`;
      $("badge-mem").textContent = `RAM ${o.mem_used_mb ?? "-"}/${o.mem_total_mb ?? "-"} MB${o.cpu_load1 != null ? `  load ${o.cpu_load1.toFixed(2)}` : ""}`;
      if (!S.cases.length && d.cases) { S.cases = d.cases; fillCases(d); }
    } catch (e) { $("badge-offline").className = "badge unknown"; }
    await new Promise((r) => setTimeout(r, 2000));
  }
}

function fillCases(d) {
  const sel = $("sel-case");
  sel.innerHTML = "";
  for (const c of d.cases) { const o = document.createElement("option"); o.value = c.name; o.textContent = `${c.name}: ${c.desc}`; sel.appendChild(o); }
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
  $("extract-status").textContent = body.mode === "llm" ? "온디바이스 LLM이 절차서를 단계별로 구조화하는 중입니다 (4B 모델, 단계당 수 초)" : "승인본(골든) 규칙을 불러왔습니다.";
  await waitPlanReady();
  $("btn-create").disabled = false;
}

async function waitPlanReady() {
  for (let i = 0; i < 600; i++) {
    const pf = await (await fetch(`${API}/sessions/${S.sid}/preflight`, { cache: "no-store" })).json();
    if (pf.status === "ready") {
      const d = await (await fetch(`${API}/sessions/${S.sid}`)).json();
      S.plan = d.plan; S.steps = d.plan.steps.map((s) => s.id);
      $("rules-backend").textContent = `출처 ${d.meta.backend}` + (d.meta.timings && d.meta.timings.extract_sec ? `, 추출 ${d.meta.timings.extract_sec.toFixed(1)}s` : "");
      $("extract-status").textContent = d.meta.summary && d.meta.summary.note ? d.meta.summary.note : (d.meta.backend.startsWith("golden") ? "승인본(골든) 규칙" : `LLM 추출 완료 (${d.meta.summary.extract ? d.meta.summary.extract.n_ok + "/" + d.meta.summary.extract.n_total + " 단계" : ""})`);
      renderRules(d.plan);
      $("approve-status").textContent = "";
      renderPreflight(pf);
      renderTimeline();
      $("btn-approve").disabled = false;
      $("btn-run").disabled = true;
      return;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
}

function condText(c) { return c.op === "in_band" ? `${c.tag} ∈ [${c.band[0]}, ${c.band[1]}]` : `${c.tag} ${c.op} ${c.value}`; }
function critText(c) { return c.op === "in_band" ? `${c.band[0]}~${c.band[1]}` : `${c.op} ${c.value}`; }
function condMet(c, v) {
  if (v == null) return null;
  switch (c.op) {
    case "==": return v === c.value; case "!=": return v !== c.value;
    case ">": return v > c.value; case ">=": return v >= c.value;
    case "<": return v < c.value; case "<=": return v <= c.value;
    case "in_band": return v >= c.band[0] && v <= c.band[1];
    default: return null;
  }
}

function renderRules(plan) {
  const tb = $("rules-table").querySelector("tbody");
  tb.innerHTML = "";
  for (const s of plan.steps) {
    const tr = document.createElement("tr");
    const exp = s.expected.map((e) => `${condText(e)} (${e.within_sec}/${e.hold_sec})`).join("<br>") || '<span class="dim">없음</span>';
    tr.innerHTML = `<td class="step"><b class="mono">${s.id}</b><br><span class="dim">${s.title}</span></td>
      <td>${s.preconditions.map(condText).join("<br>") || '<span class="dim">없음</span>'}</td>
      <td>${s.trigger ? condText(s.trigger) : '<span class="dim">없음</span>'}</td>
      <td>${exp}</td>
      <td>${s.abort_conditions.map(condText).join("<br>") || '<span class="dim">없음</span>'}</td>
      <td>${s.rollback.join("; ") || '<span class="dim">없음</span>'}</td>
      <td>${s.changes_state ? "있음" : "없음"}</td>`;
    tb.appendChild(tr);
  }
}

function renderPreflight(pf) {
  const ul = $("pf-rule"); ul.innerHTML = "";
  if (!pf.rule.length) { const li = document.createElement("li"); li.className = "empty"; li.textContent = "결함 없음: 6개 검사 통과 (사전조건, 순서, 기대결과, 태그, 복구, 기준값)"; ul.appendChild(li); }
  for (const f of pf.rule) { const li = document.createElement("li"); li.className = f.severity; li.innerHTML = `<span class="code">${f.code}</span>${f.message}`; ul.appendChild(li); }
  $("pf-summary").textContent = `ERROR ${pf.n_error ?? 0}  WARN ${pf.n_warn ?? 0}`; $("pf-summary").classList.add("mono");
  $("pf-summary").style.color = pf.n_error ? "var(--bad)" : (pf.n_warn ? "var(--warn)" : "var(--ok)");
  // ERROR가 있으면 승인 게이트가 닫힌 상태임을 미리 보여준다 (서버가 승인 요청을 거부한다)
  if (pf.n_error && !$("approve-status").textContent.startsWith("승인됨")) {
    $("approve-status").textContent = `승인 차단: ERROR ${pf.n_error}건. 절차서를 수정해 재검증하거나 강제 승인 사유를 남겨야 합니다`;
    $("approve-status").style.color = "var(--bad)";
  }
  const ai = $("pf-ai"); ai.innerHTML = "";
  if (!pf.ai || !pf.ai.length) { const li = document.createElement("li"); li.className = "dim"; li.textContent = "AI 제안 없음 (골든 모드이거나 LLM 미연결)"; ai.appendChild(li); }
  for (const f of (pf.ai || [])) { const li = document.createElement("li"); li.className = f.severity; li.innerHTML = `<span class="code">${f.code}</span>${f.message}`; ai.appendChild(li); }
}

async function approve() {
  const body = { approver: $("approver").value, force: $("force").checked };
  const r = await fetch(`${API}/sessions/${S.sid}/approve`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const d = await r.json();
  if (!r.ok) { $("approve-status").textContent = d.detail || "승인 실패"; $("approve-status").style.color = "var(--bad)"; return; }
  $("approve-status").textContent = `승인됨, ${d.approver}, ${new Date().toLocaleTimeString()}${d.n_error ? `, ERROR ${d.n_error}건 강제` : ""}`;
  $("approve-status").style.color = d.n_error ? "var(--warn)" : "var(--ok)";
  $("btn-run").disabled = false;
}

// ---------------------------------------------------------------- run + SSE
function resetRun() {
  if (S.es) { S.es.close(); S.es = null; }
  S.data = { t: [], temp: [], ch1: [], ch2: [], pump: [] }; S.exceeded = []; S.states = {}; S.tRun = {}; S.tArm = {}; S.verdicts = []; S.now = 0; S.running = false;
  S.active = null; S.lastVals = null; S.frozen = {}; S.overall = null; S.mimicFreeze = null;
  $("verdict-list").innerHTML = ""; $("verdict-count").textContent = "";
  $("report").textContent = "실행이 끝나면 한국어 기록 초안이 생성됩니다."; $("report-meta").textContent = "";
  $("tl-overall").textContent = ""; $("run-status").textContent = "";
  setVerdictCard(null);
  renderMimic(null); renderLiveExpect();
  if (S.chart) { S.chart.setData([[], [], [], [], []]); }
  setElapsed(); setModeBadge();
}

async function run() {
  resetRun();
  const speed = parseFloat($("sel-speed").value);
  $("btn-run").disabled = true;
  S.hasRun = true;
  S.es = new EventSource(`${API}/sessions/${S.sid}/stream`);
  S.es.onmessage = (m) => handleEvent(JSON.parse(m.data));
  S.es.onerror = () => { $("run-status").textContent += " (stream 끊김)"; };
  const r = await fetch(`${API}/sessions/${S.sid}/run`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ telemetry: $("sel-tel").value, speed }) });
  if (!r.ok) { $("run-status").textContent = (await r.json()).detail; $("btn-run").disabled = false; return; }
  setMode("run");
}

function handleEvent(ev) {
  switch (ev.type) {
    case "run_start":
      S.nSamples = ev.n_samples; S.steps = ev.steps; S.running = true;
      $("run-status").textContent = `${ev.telemetry}  ${ev.speed}x  0/${ev.n_samples}`;
      renderTimeline(); setModeBadge();
      break;
    case "sample": {
      const v = ev.values; S.now = ev.t;
      S.data.t.push(ev.t); S.data.temp.push(v.CHWS_T_SUP ?? null); S.data.ch1.push(v.CH1_STATUS ?? null); S.data.ch2.push(v.CH2_STATUS ?? null); S.data.pump.push(v.CHWP2_STATUS ?? null);
      S.states = ev.states; S.lastVals = v; if (ev.active) S.active = ev.active;
      if (S.data.t.length % 2 === 0 || !S.running) updateChart();
      renderTimeline(); renderMimic(S.mimicFreeze ? S.mimicFreeze.vals : v, S.mimicFreeze ? S.mimicFreeze : { t: ev.t }); renderLiveExpect(); setElapsed();
      $("run-status").textContent = `${$("sel-tel").value}  ${S.data.t.length}/${S.nSamples}`;
      break;
    }
    case "event":
      if (ev.to_state === "ARMED") { S.tArm[ev.step_id] = ev.t; S.tRun[ev.step_id] = S.tRun[ev.step_id] || { t0: ev.t, t1: null, armed: true }; }
      if (ev.to_state === "RUNNING") S.tRun[ev.step_id] = { t0: ev.t, t1: null };
      if (["PASS", "FAIL", "ABORT", "HOLD"].includes(ev.to_state)) {
        if (S.tRun[ev.step_id]) S.tRun[ev.step_id].t1 = ev.t;
        S.frozen[ev.step_id] = { vals: { ...(S.lastVals || {}) }, t: ev.t, state: ev.to_state };
        S.states[ev.step_id] = ev.to_state; S.active = ev.step_id;
        // 실패·보류·중단 시점의 계통 상태를 고정해 둔다 (이후 샘플은 판정 대상이 아님)
        if (ev.to_state !== "PASS" && !S.mimicFreeze) { S.mimicFreeze = { ...S.frozen[ev.step_id], step: ev.step_id }; renderMimic(S.mimicFreeze.vals, S.mimicFreeze); }
        updateChart(); renderLiveExpect();
      }
      break;
    case "verdict":
      S.verdicts.push(ev);
      addVerdict(ev);
      if (ev.state !== "SKIPPED") setVerdictCard(ev);
      $("badge-lat").textContent = `판정 지연 ${ev.latency_ms.toFixed(3)} ms`;
      break;
    case "segment":
      if (ev.kind === "exceeded") { S.exceeded.push(ev); updateChart(); renderTimeline(); const cur = S.verdicts.find((x) => x.step_id === ev.step_id && x.state !== "SKIPPED"); if (cur) setVerdictCard(cur); }
      break;
    case "run_done":
      S.running = false; S.overall = ev.summary.overall; updateChart(); renderTimeline(); renderLiveExpect(); setModeBadge();
      setStats(ev.summary, ev.timings.run_wall_sec);
      $("run-status").textContent += `  완료  외부 전송 ${ev.offline.tx_delta_bytes == null ? "미측정" : ev.offline.tx_delta_bytes + " B"}`;
      $("report").textContent = "보고서 생성 중 (온디바이스 LLM 종합 의견)";
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
  $("report-meta").textContent = `문안 출처 ${ev.source}, ${(ev.sec || 0).toFixed(1)}s`;
}

async function regenReport() {
  $("btn-report").disabled = true; $("report").textContent = "재생성 중";
  const d = await (await fetch(`${API}/sessions/${S.sid}/report`, { method: "POST" })).json();
  await loadReport(d); $("btn-report").disabled = false;
}

function setStats(sm, wall) {
  const el = $("tl-overall");
  if (!sm || !sm.overall) { el.innerHTML = ""; return; }
  const item = (k, v, color) => `<span>${k} <b style="color:${color || "var(--text)"}">${v}</b></span>`;
  el.innerHTML = [item("종합", sm.overall, overallColor(sm.overall)), item("PASS", sm.n_pass ?? "-", "var(--ok)"), item("FAIL", sm.n_fail ?? "-", sm.n_fail ? "var(--bad)" : null),
    item("HOLD", sm.n_hold ?? 0, sm.n_hold ? "var(--hold)" : null), item("SKIPPED", sm.n_skipped ?? "-", null), wall != null ? item("s", wall.toFixed(1), null) : ""].join("");
}

function overallColor(o) {
  if (o === "PASS") return "var(--ok)";
  if (o === "HOLD") return "var(--hold)";
  return "var(--bad)";
}

// ---------------------------------------------------------------- ① 계통 모식도
function setEq(id, cls, state, badge) {
  const g = $(id);
  g.setAttribute("class", `eq ${cls}`);
  const st = g.querySelector(".eq-state"); if (st && state != null) st.textContent = state;
  const b = g.querySelector(".eq-badge"); if (b) b.style.display = badge ? "" : "none";
}

function renderMimic(v, at) {
  const has = (k) => v != null && v[k] != null;
  const note = $("mimic-note");
  if (!at || at.t == null) note.textContent = "";
  else if (at.step) { note.textContent = `${at.step} ${at.state} 시점 t=${at.t.toFixed(0)}s에서 고정`; note.style.color = stateColor(at.state); }
  else { note.textContent = `t=${at.t.toFixed(0)}s`; note.style.color = ""; }
  // CH-1: 트립 접점이 들어오면 TRIP, 아니면 운전/정지
  if (!has("CH1_STATUS") && !has("CH1_TRIP")) setEq("m-ch1", "na", "계측 없음", false);
  else if (v.CH1_TRIP === 1) setEq("m-ch1", "trip", "TRIP", true);
  else setEq("m-ch1", v.CH1_STATUS === 1 ? "run" : "stop", v.CH1_STATUS === 1 ? "RUN" : "STOP", false);
  // CH-2: 운전 중이면 RUN, 지령만 들어온 상태는 점선 + CMD 배지
  if (!has("CH2_STATUS") && !has("CH2_CMD")) setEq("m-ch2", "na", "계측 없음", false);
  else if (v.CH2_STATUS === 1) setEq("m-ch2", "run", "RUN", v.CH2_CMD === 1);
  else if (v.CH2_CMD === 1) setEq("m-ch2", "cmd", "기동 지령", true);
  else setEq("m-ch2", "stop", "STOP", false);
  // 펌프
  if (!has("CHWP2_STATUS")) setEq("m-pump", "na", "계측 없음", false);
  else setEq("m-pump", v.CHWP2_STATUS === 1 ? "run" : "stop", v.CHWP2_STATUS === 1 ? "RUN" : "STOP", false);
  // 공급온도: 허용밴드 6~8 안 녹색, 밖 주황, 중단기준 14 초과 적색, 결측 빗금
  const sup = $("m-sup"), supVal = sup.querySelector(".eq-value");
  if (!has("CHWS_T_SUP")) { sup.setAttribute("class", "eq na"); supVal.textContent = "계측 없음"; }
  else {
    const t = v.CHWS_T_SUP; supVal.textContent = `${t.toFixed(1)} °C`;
    sup.setAttribute("class", `eq ${t > 14 ? "bad" : (t >= 6 && t <= 8 ? "ok" : "warn")}`);
  }
  $("m-ret").textContent = has("CHWR_T_RET") ? `${v.CHWR_T_RET.toFixed(1)} °C` : "계측 없음";
  const load = $("m-load"), loadVal = load.querySelector(".eq-value");
  if (!has("LOAD_KW")) { load.setAttribute("class", "eq na"); loadVal.textContent = "계측 없음"; }
  else { load.setAttribute("class", `eq ${v.LOAD_KW > 0 ? "run" : "stop"}`); loadVal.textContent = `${v.LOAD_KW.toFixed(0)} kW`; }
}

// ---------------------------------------------------------------- ④ 실측 로그 (진행 중 단계의 기대조건별 실측)
function liveStepId() {
  if (S.active && ["ARMED", "RUNNING"].includes(S.states[S.active])) return S.active;
  const term = S.steps.filter((id) => ["FAIL", "HOLD", "ABORT"].includes(S.states[id]));
  if (term.length) return term[term.length - 1];
  const ran = S.steps.filter((id) => S.tRun[id]);
  if (ran.length) return ran[ran.length - 1];
  return S.active;
}

function renderLiveExpect() {
  const el = $("live-expect");
  const id = liveStepId();
  const s = S.plan && id ? S.plan.steps.find((x) => x.id === id) : null;
  if (!s) { el.innerHTML = '<div class="dim small">실행이 시작되면 진행 중인 단계의 기대조건별 실측값이 표시됩니다.</div>'; $("live-step").textContent = ""; $("live-state").textContent = ""; return; }
  const st = S.states[id] || "PENDING";
  const fz = S.frozen[id];
  const vals = fz ? fz.vals : (S.lastVals || {});
  const tNow = fz ? fz.t : S.now;
  $("live-step").innerHTML = `<span class="mono">${id}</span> ${s.title}`;
  $("live-state").textContent = `${st}  t=${tNow.toFixed(0)}s`; $("live-state").style.color = stateColor(st);
  $("live-action").textContent = s.action || "";
  const rows = [];
  const KIND = { precond: "사전", trigger: "트리거", expected: "기대", abort: "중단" };
  const push = (kind, c, t0, allowed) => {
    const v = vals[c.tag];
    const met = condMet(c, v);
    const elapsed = t0 != null ? Math.max(0, tNow - t0) : null;
    let cls, label;
    if (kind === "abort") { // 중단 조건은 성립하면 나쁜 것
      if (v == null) { cls = "na"; label = "결측"; } else if (met) { cls = "bad"; label = "발동"; } else { cls = "idle"; label = "미발동"; }
    }
    else if (v == null) { cls = "na"; label = "결측"; }
    else if (met) { cls = "ok"; label = "성립"; }
    else if (elapsed != null && allowed != null && elapsed > allowed) { cls = "bad"; label = "초과"; }
    else { cls = "wait"; label = "대기"; }
    if (fz && kind === "expected") { // 종료된 단계는 상태로 확정 표시
      if (st === "PASS") { cls = "ok"; label = "성립"; }
      else if (st === "FAIL" && v != null && !met) { cls = "bad"; label = "초과"; }
      else if (st === "HOLD") { cls = "na"; label = "결측, 보류"; }
      else if (st === "ABORT") { cls = "bad"; label = "중단"; }
    }
    const showTime = kind !== "abort" && elapsed != null;
    rows.push(`<div class="lv-row ${cls}"><span class="lv-kind">${KIND[kind]}</span><span class="lv-tag mono">${c.tag}</span><span class="lv-crit mono">${critText(c)}</span><span class="lv-val mono">실측 ${v == null ? "-" : (typeof v === "number" ? (Number.isInteger(v) ? v : v.toFixed(1)) : v)}</span><span class="lv-time mono">${showTime ? `경과 ${elapsed.toFixed(0)}s${allowed != null ? ` / 허용 ${allowed}s` : ""}` : ""}</span><span class="lv-st">${label}</span></div>`);
  };
  const tr = S.tRun[id];
  const armedOnly = !tr || tr.armed;
  const tArm = S.tArm[id] ?? (tr ? tr.t0 : null);
  if (st === "ARMED" || st === "PENDING" || armedOnly) {
    // 사전조건·트리거 대기 중이거나 그 단계에서 끝난 경우
    for (const c of s.preconditions) push("precond", c, tArm, s.precond_wait_sec ?? 10);
    if (s.trigger) push("trigger", s.trigger, tArm, s.trigger_wait_sec ?? 120);
    if (!(st === "ARMED" || st === "PENDING")) for (const c of s.expected) push("expected", c, null, c.within_sec);
  } else {
    for (const c of s.preconditions) push("precond", c, null, null);
    if (s.trigger) push("trigger", s.trigger, null, null);
    for (const c of s.expected) push("expected", c, tr.t0, c.within_sec);
    for (const c of s.abort_conditions) push("abort", c, null, null);
  }
  if (!rows.length) rows.push('<div class="dim small">이 단계에는 조건이 없습니다.</div>');
  el.innerHTML = rows.join("");
}

function stateColor(st) {
  return { PASS: "var(--ok)", FAIL: "var(--bad)", ABORT: "var(--abort)", HOLD: "var(--hold)", RUNNING: "var(--run)", ARMED: "var(--armed)", SKIPPED: "var(--skip)" }[st] || "var(--dim)";
}

// ---------------------------------------------------------------- ⑤ 판정 카드 / ⑥ 판정 기록
function setVerdictCard(v) {
  const c = $("verdict-card");
  if (!v) { c.className = "verdict unknown"; c.querySelector(".vstate").textContent = "-"; c.querySelector(".vstep").textContent = ""; c.querySelector(".vreason").textContent = "아직 판정 없음"; c.querySelector(".vchips").innerHTML = ""; return; }
  c.className = `verdict ${v.state}`;
  c.querySelector(".vstate").textContent = v.state;
  const s = S.plan ? S.plan.steps.find((x) => x.id === v.step_id) : null;
  c.querySelector(".vstep").innerHTML = `<span class="mono">${v.step_id}</span> ${s ? s.title : ""}`;
  c.querySelector(".vreason").textContent = v.reason_ko;
  const e = v.evidence || {}, x = e.extra || {};
  const seg = S.exceeded.find((g) => g.step_id === v.step_id);
  const met = x.met_t ?? (seg && seg.t1 != null ? seg.t1 : null);
  const chips = [];
  if (v.reason_codes && v.reason_codes.length) chips.push(["code", v.reason_codes.join(" / ")]);
  if (e.tag) chips.push(["tag", e.tag]);
  if (e.threshold) chips.push(["기준", e.threshold]);
  if (x.within_sec != null) chips.push(["허용", `${x.within_sec} s`]);
  if (e.deadline != null) chips.push(["마감", `t=${e.deadline} s`]);
  if (met != null) chips.push(["실제 성립", `t=${met} s`]);
  if (met == null && e.t != null) chips.push(["판정 시각", `t=${e.t} s`]);
  if (e.value != null) chips.push(["마지막 값", `${e.value}`]);
  if (e.elapsed_sec != null && x.within_sec == null) chips.push(["경과", `${e.elapsed_sec} s`]);
  chips.push(["지연", `${v.latency_ms.toFixed(3)} ms`]);
  c.querySelector(".vchips").innerHTML = chips.map(([k, val]) => `<span class="chip"><b>${k}</b>${val}</span>`).join("");
}

function addVerdict(v) {
  const li = document.createElement("li");
  li.innerHTML = `<span class="mono">${v.t_end == null ? "-" : v.t_end.toFixed(0)}s</span><span class="st ${v.state}">${v.step_id} ${v.state}</span><span>${v.reason_ko}</span>`;
  $("verdict-list").prepend(li);
  $("verdict-count").textContent = `${S.verdicts.length}건`;
}

// ---------------------------------------------------------------- ③ 타임라인
function renderTimeline() {
  const el = $("timeline");
  const total = totalSec();
  el.innerHTML = "";
  for (const id of S.steps) {
    const st = S.states[id] || "PENDING";
    const s = S.plan ? S.plan.steps.find((x) => x.id === id) : null;
    const row = document.createElement("div"); row.className = "tl-row";
    const label = document.createElement("div"); label.innerHTML = `<b>${id}</b> <span class="dim">${s ? s.title : ""}</span> <span class="tl-state" style="color:${stateColor(st)}">${st}</span>`;
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

// ---------------------------------------------------------------- ② 차트 (uPlot)
function deadlines() {
  // 활성(RUNNING) 단계와 FAIL로 끝난 단계의 기대조건 마감선. 근거는 t_run_start + within_sec (판정 카드 evidence.deadline과 같은 값).
  const out = [];
  if (!S.plan) return out;
  for (const s of S.plan.steps) {
    const st = S.states[s.id], tr = S.tRun[s.id];
    if (!tr || tr.armed || !["RUNNING", "FAIL"].includes(st)) continue;
    const seen = new Set();
    for (const e of s.expected) { const t = tr.t0 + e.within_sec; if (!seen.has(t)) { seen.add(t); out.push({ step: s.id, t, state: st }); } }
  }
  return out;
}

function makeChart() {
  const css = getComputedStyle(document.documentElement);
  const col = (n) => css.getPropertyValue(n).trim();
  const opts = {
    width: $("chart").clientWidth || 800, height: CHART_H,
    scales: {
      x: { time: false, range: (u, min, max) => [0, Math.max(S.nSamples ? (S.nSamples - 1) * S.dt : 200, max || 0)] },
      temp: { range: [4, 18] }, bin: { range: [-0.1, 1.3] },
    },
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
        const x0 = u.bbox.left, x1 = u.bbox.left + u.bbox.width, top = u.bbox.top, bot = u.bbox.top + u.bbox.height;
        const dpr = devicePixelRatio || 1;
        ctx.font = `${11 * dpr}px -apple-system, "Segoe UI", "Malgun Gothic", sans-serif`;
        // 허용 band [6, 8]
        const yb0 = u.valToPos(8, "temp", true), yb1 = u.valToPos(6, "temp", true);
        ctx.fillStyle = col("--band"); ctx.fillRect(x0, yb0, x1 - x0, yb1 - yb0);
        // 중단 기준 14 °C
        const y14 = u.valToPos(14, "temp", true);
        ctx.strokeStyle = col("--bad"); ctx.setLineDash([6, 4]); ctx.beginPath(); ctx.moveTo(x0, y14); ctx.lineTo(x1, y14); ctx.stroke(); ctx.setLineDash([]);
        ctx.fillStyle = col("--bad"); ctx.textAlign = "left"; ctx.fillText("중단 14 °C", x0 + 4 * dpr, y14 - 3 * dpr);
        // 허용시간 초과 구간
        for (const ex of S.exceeded) {
          const t1 = ex.t1 != null ? ex.t1 : S.now;
          const px0 = u.valToPos(ex.t0, "x", true), px1 = u.valToPos(t1, "x", true);
          ctx.fillStyle = col("--ex"); ctx.fillRect(px0, top, Math.max(2, px1 - px0), u.bbox.height);
          ctx.fillStyle = col("--bad"); ctx.textAlign = "left"; ctx.fillText(`초과 ${ex.t0.toFixed(0)}→${t1.toFixed(0)}s`, px0 + 4 * dpr, top + 38 * dpr);
        }
        // 단계 경계 마커 (각 단계의 RUNNING 시작 시각). 가까운 라벨은 아래로 계단식 배치
        ctx.textAlign = "center";
        let lastPx = -1e9, stair = 0;
        for (const id of S.steps) {
          const tr = S.tRun[id]; if (!tr || tr.armed) continue;
          const px = u.valToPos(tr.t0, "x", true); if (px < x0 || px > x1) continue;
          ctx.strokeStyle = col("--line2"); ctx.globalAlpha = 1; ctx.beginPath(); ctx.moveTo(px, top); ctx.lineTo(px, bot); ctx.stroke();
          stair = px - lastPx < 24 * dpr ? stair + 1 : 0; lastPx = px;
          ctx.fillStyle = col("--dim"); ctx.fillText(id, px, top + (11 + 12 * stair) * dpr);
        }
        // 활성 단계 마감 세로선
        for (const d of deadlines()) {
          const px = u.valToPos(d.t, "x", true); if (px < x0 || px > x1) continue;
          const c = d.state === "FAIL" ? col("--bad") : col("--warn");
          ctx.strokeStyle = c; ctx.setLineDash([3, 3]); ctx.lineWidth = 1.5 * dpr; ctx.beginPath(); ctx.moveTo(px, top); ctx.lineTo(px, bot); ctx.stroke(); ctx.setLineDash([]); ctx.lineWidth = 1;
          ctx.fillStyle = c; ctx.textAlign = "left"; ctx.fillText(`${d.step} 마감 ${d.t.toFixed(0)}s`, px + 4 * dpr, top + 24 * dpr);
        }
        // 현재 시각
        if (S.running) { const px = u.valToPos(S.now, "x", true); ctx.strokeStyle = "#fff"; ctx.globalAlpha = 0.5; ctx.beginPath(); ctx.moveTo(px, top); ctx.lineTo(px, bot); ctx.stroke(); }
        ctx.restore();
      }],
    },
  };
  S.chart = new uPlot(opts, [[], [], [], [], []], $("chart"));
  window.addEventListener("resize", () => { if (S.mode === "run") S.chart.setSize({ width: $("chart").clientWidth, height: CHART_H }); });
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
  $("approve-status").textContent = "";
  renderPreflight({ status: "ready", ...(d.preflight || { rule: [], ai: [] }) });
  $("btn-approve").disabled = false;
  if (d.approved) { $("approve-status").textContent = `승인됨, ${d.meta.summary.approved_by || ""}`; $("approve-status").style.color = "var(--ok)"; $("btn-run").disabled = false; }
  if (d.has_telemetry) {
    const tel = await (await fetch(`${API}/sessions/${sid}/telemetry`)).json();
    S.nSamples = tel.rows.length;
    for (const r of tel.rows) { S.data.t.push(r.t_sec); S.data.temp.push(r.CHWS_T_SUP ?? null); S.data.ch1.push(r.CH1_STATUS ?? null); S.data.ch2.push(r.CH2_STATUS ?? null); S.data.pump.push(r.CHWP2_STATUS ?? null); }
    S.now = S.data.t[S.data.t.length - 1] || 0;
    S.exceeded = (d.segments || []).filter((g) => g.kind === "exceeded");
    const rowAt = (t) => tel.rows.find((r) => r.t_sec >= t) || tel.rows[tel.rows.length - 1];
    const strip = (r) => { const o = { ...r }; delete o.t_sec; return o; };
    for (const v of d.verdicts || []) {
      S.states[v.step_id] = v.state; S.tRun[v.step_id] = { t0: v.t_start ?? v.t_end, t1: v.t_end, armed: v.t_start == null }; S.verdicts.push(v); addVerdict(v);
      if (v.state !== "SKIPPED") { setVerdictCard(v); if (v.t_end != null) S.frozen[v.step_id] = { vals: strip(rowAt(v.t_end)), t: v.t_end, state: v.state }; }
    }
    S.lastVals = strip(tel.rows[tel.rows.length - 1]);
    const s = d.meta.summary || {};
    S.overall = s.overall || null; S.hasRun = true;
    const bad = (d.verdicts || []).find((v) => ["FAIL", "HOLD", "ABORT"].includes(v.state) && v.t_end != null);
    if (bad) S.mimicFreeze = { ...S.frozen[bad.step_id], step: bad.step_id };
    updateChart(); renderTimeline(); renderMimic(S.mimicFreeze ? S.mimicFreeze.vals : S.lastVals, S.mimicFreeze || { t: S.now }); renderLiveExpect(); setElapsed();
    setStats(s, null);
    $("run-status").textContent = `${s.telemetry || ""}  저장본  외부 전송 ${d.meta.offline && d.meta.offline.tx_delta_bytes != null ? d.meta.offline.tx_delta_bytes + " B" : "미측정"}`;
    if (d.has_report) { $("report").textContent = await (await fetch(`${API}/sessions/${sid}/report`)).text(); $("report-meta").textContent = `문안 출처 ${(d.remarks || {}).source || "-"}`; $("btn-report").disabled = false; }
    setMode("run");
  }
}

// ---------------------------------------------------------------- boot
$("btn-create").onclick = createSession;
$("btn-approve").onclick = approve;
$("btn-run").onclick = run;
$("btn-report").onclick = regenReport;
$("btn-toggle-md").onclick = () => $("plan-md").classList.toggle("collapsed");
$("nav-prep").onclick = (e) => { e.preventDefault(); setMode("prep"); };
$("nav-run").onclick = (e) => { e.preventDefault(); setMode("run"); };
makeChart();
renderMimic(null);
setMode("prep");
statusLoop();
{
  const q = new URLSearchParams(location.search).get("session");
  if (q) loadExisting(q);
}
