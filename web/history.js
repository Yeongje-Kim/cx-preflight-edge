// 세션 히스토리: 목록 → 상세(판정표, 텔레메트리 차트, 보고서). 데이터는 sessions/<id>/ 파일에서 온다.
const API = "/api/v1";
const $ = (id) => document.getElementById(id);
let chart = null;

async function loadList() {
  const d = await (await fetch(`${API}/sessions`, { cache: "no-store" })).json();
  const el = $("list"); el.innerHTML = "";
  $("count").textContent = `${d.sessions.length}건`;
  for (const m of d.sessions) {
    const s = m.summary || {};
    const div = document.createElement("div"); div.className = "card sess";
    div.innerHTML = `<div class="mono small">${m.id}</div>
      <div><span class="ov ${s.overall || ""}">${s.overall || s.status || "—"}</span> · ${m.case} · ${s.telemetry || ""}</div>
      <div class="small dim">${new Date(m.created_unix_ms).toLocaleString()} · ${m.backend} · 승인 ${s.approved_by || "—"}</div>
      <div class="small dim">외부 전송 ${m.offline && m.offline.tx_delta_bytes != null ? m.offline.tx_delta_bytes + " B" : "미측정"} · 폐쇄망 ${m.offline && m.offline.is_offline ? "OFF 확인" : "—"}</div>`;
    div.onclick = () => showDetail(m.id);
    el.appendChild(div);
  }
}

async function showDetail(id) {
  const d = await (await fetch(`${API}/sessions/${id}`, { cache: "no-store" })).json();
  $("detail").hidden = false;
  $("d-title").textContent = `${id} · ${d.meta.plan_id}`;
  const t = d.meta.timings || {};
  $("d-meta").textContent = `case ${d.meta.case} · backend ${d.meta.backend} · overall ${(d.meta.summary || {}).overall || "-"} · run ${t.run_wall_sec ? t.run_wall_sec.toFixed(1) + "s" : "-"} · report ${t.report_sec ? t.report_sec.toFixed(1) + "s" : "-"}`;
  const tb = $("d-table").querySelector("tbody"); tb.innerHTML = "";
  for (const v of d.verdicts || []) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="mono">${v.step_id}</td><td class="st ${v.state}">${v.state}</td><td>${v.t_start ?? "-"}</td><td>${v.t_end ?? "-"}</td><td>${v.reason_ko}</td><td class="mono">${v.latency_ms.toFixed(3)}</td>`;
    tb.appendChild(tr);
  }
  $("d-report").textContent = d.has_report ? await (await fetch(`${API}/sessions/${id}/report`)).text() : "보고서 없음";
  $("d-del").onclick = async () => { if (confirm("세션을 삭제할까요?")) { await fetch(`${API}/sessions/${id}`, { method: "DELETE" }); $("detail").hidden = true; loadList(); } };
  if (d.has_telemetry) drawChart(id, d.segments || []);
}

async function drawChart(id, segments) {
  const tel = await (await fetch(`${API}/sessions/${id}/telemetry`)).json();
  const rows = tel.rows;
  const data = [rows.map((r) => r.t_sec), rows.map((r) => r.CHWS_T_SUP), rows.map((r) => r.CH1_STATUS), rows.map((r) => r.CH2_STATUS), rows.map((r) => r.CHWP2_STATUS)];
  const ex = segments.filter((s) => s.kind === "exceeded");
  if (chart) { chart.destroy(); chart = null; }
  const css = getComputedStyle(document.documentElement); const col = (n) => css.getPropertyValue(n).trim();
  chart = new uPlot({
    width: $("d-chart").clientWidth, height: 260,
    scales: { x: { time: false }, temp: { range: [4, 18] }, bin: { range: [-0.1, 1.3] } },
    axes: [{ stroke: col("--dim"), grid: { stroke: col("--line") } }, { scale: "temp", stroke: col("--dim"), grid: { stroke: col("--line") } }, { scale: "bin", side: 1, stroke: col("--dim"), grid: { show: false } }],
    series: [{}, { scale: "temp", stroke: col("--temp"), width: 2, spanGaps: false }, { scale: "bin", stroke: col("--ch1"), paths: uPlot.paths.stepped({ align: 1 }) }, { scale: "bin", stroke: col("--ch2"), paths: uPlot.paths.stepped({ align: 1 }) }, { scale: "bin", stroke: col("--pump"), dash: [4, 3], paths: uPlot.paths.stepped({ align: 1 }) }],
    hooks: { drawClear: [(u) => {
      const ctx = u.ctx; ctx.save();
      const x0 = u.bbox.left, x1 = u.bbox.left + u.bbox.width;
      ctx.fillStyle = col("--band"); const y0 = u.valToPos(8, "temp", true), y1 = u.valToPos(6, "temp", true); ctx.fillRect(x0, y0, x1 - x0, y1 - y0);
      for (const s of ex) { const t1 = s.t1 != null ? s.t1 : data[0][data[0].length - 1]; const px0 = u.valToPos(s.t0, "x", true), px1 = u.valToPos(t1, "x", true); ctx.fillStyle = col("--ex"); ctx.fillRect(px0, u.bbox.top, Math.max(2, px1 - px0), u.bbox.height); }
      ctx.restore();
    }] },
  }, data, $("d-chart"));
}

loadList();
