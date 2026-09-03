import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import cxpe.server as server
from cxpe.llm.client import FakeBackend
from cxpe.sessions import SessionStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CXPE_SKIP_CONNECT_PROBE", "1")
    st = server.State()
    st.store = SessionStore(tmp_path / "sessions")
    st.llm = FakeBackend(responder=lambda m: json.dumps({
        "summary_ko": "규칙 엔진 판정 결과를 요약한다.", "actions_ko": ["기록을 보관한다."], "reason_codes": ["EXPECTED_MET"]},
        ensure_ascii=False))
    st.llm_checked = time.time() + 10**6  # autodetect 비활성
    server.app.state.cx = st
    return TestClient(server.app)


def wait_finished(client, sid, timeout=30.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = client.get(f"/api/v1/sessions/{sid}").json()
        if d["meta"]["summary"].get("status") in ("finished", "error") and d.get("has_report"):
            return d
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_status_and_cases(client):
    r = client.get("/api/v1/status")
    assert r.status_code == 200
    d = r.json()
    assert "offline" in d and d["llm"]["backend"] == "fake"
    assert {c["name"] for c in d["cases"]} >= {"pass", "fail_start", "preflight_warn"}


def test_full_flow_pass(client):
    sid = client.post("/api/v1/sessions", json={"case": "pass", "mode": "golden"}).json()["id"]
    pf = client.get(f"/api/v1/sessions/{sid}/preflight").json()
    assert pf["status"] == "ready" and pf["rule"] == []
    assert client.post(f"/api/v1/sessions/{sid}/run", json={"speed": 0}).status_code == 409  # 승인 전
    ap = client.post(f"/api/v1/sessions/{sid}/approve", json={"approver": "tester"})
    assert ap.status_code == 200 and ap.json()["n_error"] == 0
    r = client.post(f"/api/v1/sessions/{sid}/run", json={"speed": 0, "seed": 1})
    assert r.status_code == 200 and r.json()["n_samples"] > 100
    d = wait_finished(client, sid)
    assert d["meta"]["summary"]["overall"] == "PASS" == d["meta"]["summary"]["label_overall"]
    states = {v["step_id"]: v["state"] for v in d["verdicts"]}
    assert states == {sid_: lab["state"] for sid_, lab in d["labels"]["steps"].items()}
    assert d["remarks"]["source"] == "fake"
    rep = client.get(f"/api/v1/sessions/{sid}/report").text
    assert "종합 판정: PASS" in rep
    tel = client.get(f"/api/v1/sessions/{sid}/telemetry").json()
    assert "CHWS_T_SUP" in tel["columns"]
    # SSE 백로그에 run_start/verdict/report/done이 있다
    with client.stream("GET", f"/api/v1/sessions/{sid}/stream") as s:
        types = []
        for line in s.iter_lines():
            if line.startswith("data:"):
                ev = json.loads(line[5:])
                types.append(ev["type"])
                if ev["type"] == "done":
                    break
    assert "run_start" in types and "verdict" in types and "report" in types and types[-1] == "done"


def test_preflight_warn_blocks_approval(client):
    sid = client.post("/api/v1/sessions", json={"case": "preflight_warn", "mode": "golden"}).json()["id"]
    pf = client.get(f"/api/v1/sessions/{sid}/preflight").json()
    codes = {f["code"] for f in pf["rule"]}
    assert codes == {"PF03_NO_EXPECTED_RESULT", "PF04_TAG_UNMAPPED", "PF05_MISSING_ROLLBACK"}
    assert pf["n_error"] == 2
    r = client.post(f"/api/v1/sessions/{sid}/approve", json={"approver": "t"})
    assert r.status_code == 409
    r = client.post(f"/api/v1/sessions/{sid}/approve", json={"approver": "t", "force": True})
    assert r.status_code == 200 and r.json()["n_error"] == 2


def test_fail_start_has_exceeded_segment(client):
    sid = client.post("/api/v1/sessions", json={"case": "fail_start", "mode": "golden"}).json()["id"]
    client.post(f"/api/v1/sessions/{sid}/approve", json={"approver": "t"})
    client.post(f"/api/v1/sessions/{sid}/run", json={"speed": 0})
    d = wait_finished(client, sid)
    assert d["meta"]["summary"]["overall"] == "FAIL"
    v5 = next(v for v in d["verdicts"] if v["step_id"] == "S5")
    assert v5["reason_codes"] == ["STANDBY_START_TIMEOUT"] and v5["evidence"]["extra"]["met_t"] is not None
    ex = [g for g in d["segments"] if g["kind"] == "exceeded"]
    assert ex and ex[0]["step_id"] == "S5"
    lst = client.get("/api/v1/sessions").json()["sessions"]
    assert any(m["id"] == sid for m in lst)
    assert client.delete(f"/api/v1/sessions/{sid}").status_code == 200


def test_llm_mode_without_backend_falls_back(client):
    st = server.app.state.cx
    st.llm = None
    st.llm_checked = time.time() + 10**6
    sid = client.post("/api/v1/sessions", json={"case": "pass", "mode": "llm"}).json()["id"]
    d = client.get(f"/api/v1/sessions/{sid}").json()
    assert d["meta"]["backend"].startswith("golden")
