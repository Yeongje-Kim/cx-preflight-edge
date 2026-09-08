"""FastAPI 서버: 세션 생성(추출) → Preflight → 승인 → 재생 판정(SSE) → 보고서. 정적 UI 서빙.

판정은 이 프로세스에서, AI 추론은 설정된 로컬 LLM 서비스에서 수행한다.
네트워크 상태 점검은 별도이며 단일 서버 프로세스로 실행한다.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, netcheck
from .engine import RunEngine
from .llm.client import LlmClient, autodetect
from .llm.extract import extract_plan, find_contradictions, map_tags
from .llm.source_guard import validate_plan_source
from .llm.report import build_facts, draft_report_ko, render_report_md
from .preflight import run_preflight
from .schemas import Finding, TagList, TestPlan
from .sessions import SessionStore
from .synth import CASES as SYNTH_CASES, make_case
from .telemetry import replay, write_csv

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
DATA = ROOT / "data"

DEMO_CASES: dict[str, dict[str, str]] = {
    "preflight_warn": {"plan": "defective/mixed_demo.json", "telemetry": "pass",
                       "desc": "사전검증 경고: 태그 미매핑 + 기대 결과 누락 + 복구 절차 누락"},
    "pass": {"plan": "golden/plan.json", "telemetry": "pass", "desc": "정상: 대기기 기동과 온도 회복이 허용시간 내"},
    "fail_start": {"plan": "golden/plan.json", "telemetry": "fail_start", "desc": "불합격: 대기기 기동 지연"},
    "fail_temp": {"plan": "golden/plan.json", "telemetry": "fail_temp", "desc": "불합격: 온도 회복 지연"},
    "fail_dropout": {"plan": "golden/plan.json", "telemetry": "fail_dropout",
                     "desc": "보류: 공급온도 계측 결측으로 판정하지 않음(설비 불합격 아님)"},
}


class SessionBus:
    """세션별 이벤트 버퍼 + 구독 큐. 늦게 붙은 구독자도 백로그를 받는다."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.subs: list[queue.Queue] = []
        self.lock = threading.Lock()
        self.done = False

    def publish(self, ev: dict) -> None:
        with self.lock:
            self.events.append(ev)
            if ev.get("type") == "done":
                self.done = True
            for q in self.subs:
                q.put(ev)

    def subscribe(self) -> tuple[list[dict], queue.Queue]:
        q: queue.Queue = queue.Queue()
        with self.lock:
            backlog = list(self.events)
            self.subs.append(q)
        return backlog, q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)


class State:
    def __init__(self) -> None:
        self.store = SessionStore()
        self.llm: Optional[LlmClient] = None
        self.llm_checked = 0.0
        self.meter = netcheck.UplinkMeter()
        self.buses: dict[str, SessionBus] = {}
        self.session_locks: dict[str, Any] = {}
        self.last_latency_ms: Optional[float] = None
        self.lock = threading.Lock()

    def get_llm(self, force: bool = False) -> Optional[LlmClient]:
        if force or (self.llm is None and time.time() - self.llm_checked > 30):
            self.llm = autodetect()
            self.llm_checked = time.time()
        return self.llm

    def bus(self, sid: str) -> SessionBus:
        with self.lock:
            if sid not in self.buses:
                self.buses[sid] = SessionBus()
            return self.buses[sid]

    def session_lock(self, sid: str):
        """승인과 실행 시작을 같은 세션 안에서 직렬화한다."""
        with self.lock:
            if sid not in self.session_locks:
                self.session_locks[sid] = threading.RLock()
            return self.session_locks[sid]


app = FastAPI(title="Cx-Preflight Edge", version=__version__)
app.state.cx = State()
if WEB.exists():
    app.mount("/static", StaticFiles(directory=str(WEB)), name="static")


def _st() -> State:
    return app.state.cx


def _tags() -> TagList:
    return TagList.model_validate(json.loads((DATA / "golden/tags.json").read_text(encoding="utf-8")))


def _golden() -> TestPlan:
    return TestPlan.model_validate(json.loads((DATA / "golden/plan.json").read_text(encoding="utf-8")))


def _load_plan_file(rel: str) -> TestPlan:
    p = (DATA / rel).resolve()
    if not p.is_relative_to(DATA.resolve()) or not p.exists():
        raise HTTPException(400, f"unknown plan file {rel}")
    return TestPlan.model_validate(json.loads(p.read_text(encoding="utf-8")))


# ---------------------------------------------------------------- pages
@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/history")
def history_page() -> FileResponse:
    return FileResponse(WEB / "history.html")


# ---------------------------------------------------------------- status
@app.get("/api/v1/status")
def status() -> dict:
    st = _st()
    llm = st.get_llm()
    return {
        "version": __version__,
        "offline": netcheck.status(st.meter),
        "llm": {"backend": getattr(llm, "name", None), "alive": llm is not None},
        "last_latency_ms": st.last_latency_ms,
        "cases": [{"name": k, **v} for k, v in DEMO_CASES.items()],
        "telemetry_cases": sorted(SYNTH_CASES),
        "time": time.time(),
    }


@app.post("/api/v1/llm/recheck")
def llm_recheck() -> dict:
    llm = _st().get_llm(force=True)
    return {"backend": getattr(llm, "name", None), "alive": llm is not None}


# ---------------------------------------------------------------- sessions
@app.get("/api/v1/sessions")
def list_sessions() -> dict:
    return {"sessions": [m.model_dump(mode="json") for m in _st().store.list()]}


@app.post("/api/v1/sessions")
def create_session(body: dict = Body(default={})) -> dict:
    st = _st()
    case = body.get("case", "pass")
    if not isinstance(case, str) or case not in DEMO_CASES:
        raise HTTPException(400, f"unknown case {case}")
    mode = body.get("mode", "golden")
    if not isinstance(mode, str) or mode not in {"golden", "llm"}:
        raise HTTPException(400, "mode must be golden or llm")
    plan_md = (DATA / "plan_n1_cooling.md").read_text(encoding="utf-8")
    tags = _tags()
    base_plan = _load_plan_file(DEMO_CASES[case]["plan"])
    meta = st.store.create(plan_id=base_plan.plan_id, case=case, backend="golden")
    sid = meta.id
    st.store.save_text(sid, "plan.md", plan_md)
    st.store.save_json(sid, "tags.json", tags.model_dump(mode="json"))
    st.store.update_meta(sid, summary={"status": "extracting" if mode == "llm" else "ready",
                                        "telemetry": DEMO_CASES[case]["telemetry"], "mode": mode})
    bus = st.bus(sid)

    def finish_plan(plan: TestPlan, backend: str, extract_stats: Optional[dict]) -> None:
        st.store.save_json(sid, "plan.json", plan.model_dump(mode="json"))
        rule = run_preflight(plan, tags)
        ai: list[Finding] = []
        llm = st.get_llm()
        if llm is not None and mode == "llm":
            try:
                ai = map_tags(plan, tags, llm) + find_contradictions(plan, llm)
            except Exception as e:  # LLM 보조 실패는 세션을 막지 않는다
                ai = [Finding(code="PF04_TAG_UNMAPPED", severity="WARN", message=f"AI 제안 실패: {e}",
                              source="ai_suggestion")]
        st.store.save_json(sid, "preflight.json", {
            "rule": [f.model_dump(mode="json") for f in rule],
            "ai": [f.model_dump(mode="json") for f in ai],
            "n_error": sum(1 for f in rule if f.severity.value == "ERROR"),
            "n_warn": sum(1 for f in rule if f.severity.value == "WARN"),
        })
        summary: dict[str, Any] = {"status": "ready", "preflight_errors": sum(1 for f in rule if f.severity.value == "ERROR"),
                                   "preflight_warnings": sum(1 for f in rule if f.severity.value == "WARN")}
        if extract_stats:
            summary["extract"] = {k: v for k, v in extract_stats.items() if k != "steps"}
        st.store.update_meta(sid, backend=backend, summary=summary)
        bus.publish({"type": "plan_ready", "session": sid, "backend": backend,
                     "preflight": {"n_rule": len(rule), "n_ai": len(ai)}})

    if mode == "llm":
        llm = st.get_llm(force=True)
        if llm is None:
            st.store.update_meta(sid, summary={"status": "ready", "note": "LLM 백엔드 없음: 골든 사용"})
            finish_plan(base_plan, "golden(no-llm)", None)
        else:
            def work() -> None:
                t0 = time.perf_counter()
                try:
                    plan, stats = extract_plan(plan_md, llm, golden=base_plan)
                    # 데모 결함 플랜은 골든 절차서 원문에서 추출되므로, 결함 케이스는 골든 대신 결함 플랜을 유지한다
                    if case != "pass" and DEMO_CASES[case]["plan"].startswith("defective"):
                        plan = base_plan
                        stats["note"] = "defective demo plan kept"
                    backend = llm.name if not stats["fallback_ids"] else f"{llm.name}+golden-fallback"
                    st.store.save_json(sid, "extract_stats.json", stats)
                    st.store.update_meta(sid, timings={"extract_sec": time.perf_counter() - t0})
                    finish_plan(plan, backend, stats)
                except Exception as e:
                    st.store.update_meta(sid, summary={"status": "ready", "note": f"추출 실패, 골든 사용: {e}"})
                    finish_plan(base_plan, "golden(fallback)", None)
            threading.Thread(target=work, name=f"extract-{sid}", daemon=True).start()
    else:
        finish_plan(base_plan, "golden", None)
    return st.store.read_meta(sid).model_dump(mode="json")


def _session_or_404(sid: str) -> None:
    if not _st().store.exists(sid):
        raise HTTPException(404, "session not found")


def _has_run(store: SessionStore, sid: str) -> bool:
    # 실행 파일도 확인해 서버 재시작 및 이전 버전의 완료 세션을 보호한다.
    return (store.read_meta(sid).summary.get("status") in ("running", "reporting", "finished", "error")
            or any(store.has(sid, name) for name in
                   ("rules.executed.json", "telemetry.csv", "verdicts.jsonl", "events.jsonl")))


def _require_unrun(store: SessionStore, sid: str) -> None:
    if _has_run(store, sid):
        raise HTTPException(409, "실행한 세션의 승인 규칙과 결과는 변경할 수 없습니다. 새 시험을 준비하세요")


@app.get("/api/v1/sessions/{sid}")
def get_session(sid: str) -> dict:
    st = _st()
    _session_or_404(sid)
    s = st.store
    out: dict[str, Any] = {"meta": s.read_meta(sid).model_dump(mode="json")}
    for name, key in (("plan.json", "plan"), ("preflight.json", "preflight"), ("rules.approved.json", "approved"),
                      ("segments.json", "segments"), ("remarks.json", "remarks"), ("labels.json", "labels"),
                      ("extract_stats.json", "extract_stats"), ("rules.executed.json", "executed")):
        if s.has(sid, name):
            out[key] = s.load_json(sid, name)
    out["verdicts"] = s.load_jsonl(sid, "verdicts.jsonl")
    out["has_run"] = _has_run(s, sid)
    out["has_report"] = s.has(sid, "report.md")
    out["has_telemetry"] = s.has(sid, "telemetry.csv")
    return out


@app.delete("/api/v1/sessions/{sid}")
def delete_session(sid: str) -> dict:
    st = _st()
    with st.session_lock(sid):
        _session_or_404(sid)
        if st.store.read_meta(sid).summary.get("status") in ("extracting", "running", "reporting"):
            raise HTTPException(409, "구조화·실행·보고서 생성 중인 세션은 삭제할 수 없습니다")
        st.store.delete(sid)
        with st.lock:
            st.buses.pop(sid, None)
    return {"deleted": sid}


@app.get("/api/v1/sessions/{sid}/plan_md", response_class=PlainTextResponse)
def get_plan_md(sid: str) -> str:
    _session_or_404(sid)
    return _st().store.load_text(sid, "plan.md")


@app.get("/api/v1/sessions/{sid}/preflight")
def get_preflight(sid: str) -> dict:
    _session_or_404(sid)
    s = _st().store
    if not s.has(sid, "preflight.json"):
        return {"status": s.read_meta(sid).summary.get("status", "extracting"), "rule": [], "ai": []}
    return {"status": "ready", **s.load_json(sid, "preflight.json")}


@app.post("/api/v1/sessions/{sid}/approve")
def approve(sid: str, body: dict = Body(default={})) -> dict:
    st = _st()
    with st.session_lock(sid):
        return _approve(sid, body, st)


def _approve(sid: str, body: dict, st: State) -> dict:
    _session_or_404(sid)
    _require_unrun(st.store, sid)
    if not st.store.has(sid, "plan.json"):
        raise HTTPException(409, "절차서 구조화가 끝난 뒤 승인하세요")
    if not isinstance(body.get("force", False), bool):
        raise HTTPException(400, "force must be a boolean")
    plan_data = body.get("plan", st.store.load_json(sid, "plan.json"))
    try:
        plan = TestPlan.model_validate(plan_data)
    except Exception as e:
        raise HTTPException(400, f"invalid plan: {e}")
    meta = st.store.read_meta(sid)
    if meta.summary.get("mode") == "llm" and meta.case != "preflight_warn":
        try:
            validate_plan_source(plan, st.store.load_text(sid, "plan.md"))
        except ValueError as e:
            raise HTTPException(409, f"원문 대조 실패: {e}. 다시 추출하거나 원문과 규칙을 검토하세요") from e
    approver = str(body.get("approver") or "engineer").strip()[:40]
    findings = run_preflight(plan, _tags())
    n_err = sum(1 for f in findings if f.severity.value == "ERROR")
    if n_err and not body.get("force"):
        raise HTTPException(409, f"Preflight ERROR {n_err}건이 남아 있어 승인할 수 없다 (force=true로 강제 가능)")
    st.store.save_json(sid, "rules.approved.json", plan.model_dump(mode="json"))
    st.store.update_meta(sid, summary={"approved_by": approver, "approved_at": time.time(),
                                        "approved_with_errors": n_err, "status": "approved"})
    st.bus(sid).publish({"type": "approved", "session": sid, "approver": approver, "n_error": n_err})
    return {"approved": True, "approver": approver, "n_error": n_err}


@app.post("/api/v1/sessions/{sid}/run")
def run_session(sid: str, body: dict = Body(default={})) -> dict:
    st = _st()
    with st.session_lock(sid):
        return _start_run(sid, body, st)


def _start_run(sid: str, body: dict, st: State) -> dict:
    _session_or_404(sid)
    s = st.store
    _require_unrun(s, sid)
    meta = s.read_meta(sid)
    if not s.has(sid, "rules.approved.json"):
        raise HTTPException(409, "승인된 규칙이 없다. 먼저 승인하라")
    if meta.summary.get("mode") == "llm" and meta.case != "preflight_warn":
        try:
            validate_plan_source(TestPlan.model_validate(s.load_json(sid, "rules.approved.json")), s.load_text(sid, "plan.md"))
        except ValueError as e:
            raise HTTPException(409, f"승인 규칙 원문 대조 실패: {e}. 재추출·재승인이 필요합니다") from e
    tel = body.get("telemetry", meta.summary.get("telemetry") or "pass")
    if not isinstance(tel, str) or tel not in SYNTH_CASES:
        raise HTTPException(400, f"unknown telemetry case {tel}")
    try:
        speed = float(body.get("speed", 5.0))
        seed_value = body.get("seed", 0)
        if isinstance(seed_value, bool) or isinstance(seed_value, float) and not seed_value.is_integer():
            raise ValueError("seed must be an integer")
        seed = int(seed_value)
        import math
        if isinstance(body.get("speed"), bool) or not math.isfinite(speed) or speed < 0:
            raise ValueError("speed must be finite and nonnegative")
    except (ValueError, TypeError, OverflowError) as error:
        raise HTTPException(400, "speed는 0 이상의 유한한 수, seed는 정수여야 합니다") from error
    plan = TestPlan.model_validate(s.load_json(sid, "rules.approved.json"))
    rows, labels, _ = make_case(tel, seed=seed)
    # 시험 한 번당 세션 하나. 실행 당시 승인 규칙은 별도 보관한다.
    s.save_json(sid, "rules.executed.json", plan.model_dump(mode="json"))
    write_csv(rows, s.path(sid) / "telemetry.csv")
    s.save_json(sid, "labels.json", labels)
    (s.path(sid) / "verdicts.jsonl").write_text("", encoding="utf-8")
    (s.path(sid) / "events.jsonl").write_text("", encoding="utf-8")
    s.update_meta(sid, case=meta.case, summary={"status": "running", "telemetry": tel, "speed": speed, "seed": seed,
                                                             "run_started_at": time.time()})
    bus = st.bus(sid)
    meter = netcheck.UplinkMeter()

    def work() -> None:
        t0 = time.perf_counter()
        eng = RunEngine(plan)
        bus.publish({"type": "run_start", "session": sid, "telemetry": tel, "speed": speed,
                     "n_samples": len(rows), "steps": [x.id for x in plan.steps]})
        try:
            for sample in replay(rows, speed=speed):
                events = eng.feed(sample)
                bus.publish({"type": "sample", "t": sample["t_sec"], "values": {k: v for k, v in sample.items() if k != "t_sec"},
                             "active": eng.rts[eng.active].step.id if eng.active < len(eng.rts) else None,
                             "states": {rt.step.id: rt.state.value for rt in eng.rts}})
                for ev in events:
                    s.append_jsonl(sid, "events.jsonl", ev.model_dump(mode="json"))
                    bus.publish({"type": "event", **ev.model_dump(mode="json")})
                    if ev.to_state.value in ("PASS", "FAIL", "ABORT", "HOLD", "SKIPPED"):
                        v = next(x for x in reversed(eng.verdicts) if x.step_id == ev.step_id)
                        s.append_jsonl(sid, "verdicts.jsonl", v.model_dump(mode="json"))
                        st.last_latency_ms = v.latency_ms
                        bus.publish({"type": "verdict", **v.model_dump(mode="json")})
                new_segs = [g for g in eng.segments if g.kind == "exceeded" and not g.__dict__.get("_sent")]
                for g in new_segs:
                    g.__dict__["_sent"] = True
                    bus.publish({"type": "segment", **g.model_dump(mode="json")})
            eng.finish()
            # finish()가 추가한 verdict(스트림 종료 FAIL 등)를 파일에 반영
            written = {json.loads(l)["step_id"] for l in (s.path(sid) / "verdicts.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()}
            for v in eng.verdicts:
                if v.step_id not in written:
                    s.append_jsonl(sid, "verdicts.jsonl", v.model_dump(mode="json"))
                    bus.publish({"type": "verdict", **v.model_dump(mode="json")})
            # exceeded 구간에 나중에 met_t가 채워진 verdict 갱신본을 다시 저장
            (s.path(sid) / "verdicts.jsonl").write_text(
                "".join(json.dumps(v.model_dump(mode="json"), ensure_ascii=False) + "\n" for v in eng.verdicts), encoding="utf-8")
            s.save_json(sid, "segments.json", [g.model_dump(mode="json") for g in eng.segments])
            summary = eng.summary()
            offline = netcheck.status(meter)
            timings = {"run_wall_sec": time.perf_counter() - t0,
                       "latency_ms_max": max((v.latency_ms for v in eng.verdicts), default=0.0)}
            s.update_meta(sid, offline=offline, timings=timings,
                          summary={"status": "reporting", **summary, "label_overall": labels.get("overall")})
            bus.publish({"type": "run_done", "session": sid, "summary": summary, "offline": offline, "timings": timings})
            _make_report(sid, plan, eng, offline)
            s.update_meta(sid, summary={"status": "finished"})
        except Exception as e:
            s.update_meta(sid, summary={"status": "error", "error": str(e)})
            bus.publish({"type": "error", "message": str(e)})
        finally:
            bus.publish({"type": "done", "session": sid})

    threading.Thread(target=work, name=f"run-{sid}", daemon=True).start()
    return {"started": True, "telemetry": tel, "speed": speed, "n_samples": len(rows)}


def _make_report(sid: str, plan: TestPlan, eng: RunEngine, offline: dict) -> dict:
    st = _st()
    s = st.store
    meta = s.read_meta(sid)
    facts = build_facts(plan, eng.verdicts, eng.segments, {"case": meta.case, "telemetry": meta.summary.get("telemetry")})
    t0 = time.perf_counter()
    remarks = draft_report_ko(facts, st.get_llm())
    uplink = offline.get("tx_delta_bytes")
    md = render_report_md(facts, remarks, {"id": sid, "case": meta.summary.get("telemetry"),
                                           "uplink": "0 B" if uplink == 0 else ("미측정" if uplink is None else f"{uplink} B")})
    s.save_json(sid, "remarks.json", remarks)
    s.save_json(sid, "facts.json", facts)
    s.save_text(sid, "report.md", md)
    s.update_meta(sid, timings={"report_sec": time.perf_counter() - t0, "report_llm_sec": remarks.get("sec", 0.0)})
    st.bus(sid).publish({"type": "report", "session": sid, "source": remarks.get("source"), "sec": remarks.get("sec")})
    return remarks


@app.post("/api/v1/sessions/{sid}/report")
def make_report(sid: str) -> dict:
    st = _st()
    with st.session_lock(sid):
        return _regenerate_report(sid, st)


def _regenerate_report(sid: str, st: State) -> dict:
    _session_or_404(sid)
    s = st.store
    if s.read_meta(sid).summary.get("status") in ("running", "reporting"):
        raise HTTPException(409, "시험과 기록 생성이 끝난 뒤 다시 요청하세요")
    if not s.has(sid, "segments.json"):
        raise HTTPException(409, "실행 결과가 없다")
    # 이전 버전의 세션은 보관된 승인본으로 열되, 재승인·재실행은 차단한다.
    plan_file = "rules.executed.json" if s.has(sid, "rules.executed.json") else "rules.approved.json"
    plan = TestPlan.model_validate(s.load_json(sid, plan_file))
    from .schemas import Segment, Verdict  # 지역 import: 재구성용
    eng = RunEngine(plan)
    eng.verdicts = [Verdict.model_validate(v) for v in s.load_jsonl(sid, "verdicts.jsonl")]
    eng.segments = [Segment.model_validate(g) for g in s.load_json(sid, "segments.json")]
    remarks = _make_report(sid, plan, eng, s.read_meta(sid).offline)
    return {"source": remarks.get("source"), "sec": remarks.get("sec")}


@app.get("/api/v1/sessions/{sid}/report", response_class=PlainTextResponse)
def get_report(sid: str) -> str:
    _session_or_404(sid)
    s = _st().store
    if not s.has(sid, "report.md"):
        raise HTTPException(404, "report not generated")
    return s.load_text(sid, "report.md")


@app.get("/api/v1/sessions/{sid}/telemetry")
def get_telemetry(sid: str) -> dict:
    _session_or_404(sid)
    from .telemetry import read_csv
    p = _st().store.path(sid) / "telemetry.csv"
    if not p.exists():
        raise HTTPException(404, "no telemetry")
    rows = read_csv(p)
    return {"columns": list(rows[0].keys()) if rows else [], "rows": rows}


@app.get("/api/v1/sessions/{sid}/stream")
def stream(sid: str) -> StreamingResponse:
    _session_or_404(sid)
    bus = _st().bus(sid)

    def gen():
        backlog, q = bus.subscribe()
        try:
            for ev in backlog:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            if any(e.get("type") == "done" for e in backlog):
                return
            # subscribe 이후 완료된 경우에는 큐의 남은 판정·보고서도 전달한다.
            last = time.time()
            while True:
                try:
                    ev = q.get(timeout=1.0)
                except queue.Empty:
                    if time.time() - last > 15:
                        yield ": keepalive\n\n"
                        last = time.time()
                    continue
                last = time.time()
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                if ev.get("type") == "done":
                    break
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/v1/metrics", response_class=PlainTextResponse)
def metrics_md() -> str:
    p = ROOT / "docs/metrics.md"
    if not p.exists():
        raise HTTPException(404, "docs/metrics.md not generated (python -m cxpe.metrics)")
    return p.read_text(encoding="utf-8")
