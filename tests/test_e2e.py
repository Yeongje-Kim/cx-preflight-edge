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
    st.llm = FakeBackend(responder=lambda messages: json.dumps({"action_ids": [
        json.loads(messages[1]["content"])["CANDIDATES"][0]["id"]]}))
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

@pytest.mark.parametrize("force", [False, True])
def test_semantic_source_mismatch_blocks_approval_even_force(client, force):
    sid = client.post("/api/v1/sessions", json={"case": "pass", "mode": "golden"}).json()["id"]
    st = server.app.state.cx
    st.store.update_meta(sid, summary={"mode": "llm"})
    plan = st.store.load_json(sid, "plan.json")
    plan["steps"][1]["expected"][0].update(within_sec=10, hold_sec=30)
    r = client.post(f"/api/v1/sessions/{sid}/approve", json={"plan": plan, "force": force})
    assert r.status_code == 409 and "원문 대조 실패" in r.json()["detail"]
    assert not st.store.has(sid, "rules.approved.json")


def test_old_approved_bad_rules_cannot_be_replayed(client):
    sid = client.post("/api/v1/sessions", json={"case": "pass", "mode": "golden"}).json()["id"]
    assert client.post(f"/api/v1/sessions/{sid}/approve", json={}).status_code == 200
    st = server.app.state.cx
    st.store.update_meta(sid, summary={"mode": "llm"})
    plan = st.store.load_json(sid, "rules.approved.json")
    plan["steps"][1]["expected"][0].update(op=">", within_sec=10, hold_sec=30)
    st.store.save_json(sid, "rules.approved.json", plan)
    r = client.post(f"/api/v1/sessions/{sid}/run", json={"speed": 0})
    assert r.status_code == 409 and "원문 대조 실패" in r.json()["detail"]


def test_source_verified_ai_plan_passes_normal_telemetry(client):
    sid = client.post("/api/v1/sessions", json={"case": "pass", "mode": "golden"}).json()["id"]
    server.app.state.cx.store.update_meta(sid, summary={"mode": "llm"})
    assert client.post(f"/api/v1/sessions/{sid}/approve", json={}).status_code == 200
    assert client.post(f"/api/v1/sessions/{sid}/run", json={"speed": 0}).status_code == 200
    assert wait_finished(client, sid)["meta"]["summary"]["overall"] == "PASS"


def prepared_session(client, case="pass"):
    sid = client.post("/api/v1/sessions", json={"case": case, "mode": "golden"}).json()["id"]
    assert client.post(f"/api/v1/sessions/{sid}/approve", json={"approver": "original"}).status_code == 200
    return sid


def completed_session(client, case="pass"):
    sid = prepared_session(client, case)
    assert client.post(f"/api/v1/sessions/{sid}/run", json={"speed": 0}).status_code == 200
    wait_finished(client, sid)
    return sid


def read_events(client, sid):
    with client.stream("GET", f"/api/v1/sessions/{sid}/stream") as response:
        return [json.loads(line[5:]) for line in response.iter_lines() if line.startswith("data:")]


def test_completed_run_is_preserved_when_replay_requested(client):
    sid = completed_session(client)
    store = server.app.state.cx.store
    names = ("telemetry.csv", "verdicts.jsonl", "rules.executed.json", "report.md")
    before = {name: (store.path(sid) / name).read_bytes() for name in names}
    response = client.post(f"/api/v1/sessions/{sid}/run", json={"speed": 0, "telemetry": "fail_start"})
    assert response.status_code == 409
    assert before == {name: (store.path(sid) / name).read_bytes() for name in names}
    events = read_events(client, sid)
    assert sum(e["type"] == "run_start" for e in events) == 1
    assert sum(e["type"] == "done" for e in events) == 1


@pytest.mark.parametrize("force", [False, True])
def test_completed_reapproval_cannot_change_report(client, force):
    sid = completed_session(client)
    store = server.app.state.cx.store
    before = store.load_text(sid, "report.md")
    original = store.load_json(sid, "rules.approved.json")
    changed = json.loads(json.dumps(original))
    changed["steps"][0]["title"] = "Changed after execution"
    response = client.post(f"/api/v1/sessions/{sid}/approve",
                           json={"plan": changed, "approver": "replacement", "force": force})
    assert response.status_code == 409
    assert store.load_json(sid, "rules.approved.json") == original
    assert store.read_meta(sid).summary["approved_by"] == "original"
    assert client.post(f"/api/v1/sessions/{sid}/report").status_code == 200
    assert store.load_text(sid, "report.md") == before


def test_new_trial_has_its_own_results_and_stream(client):
    first = completed_session(client)
    second = completed_session(client, "fail_start")
    assert first != second
    for sid, expected in ((first, "PASS"), (second, "FAIL")):
        events = read_events(client, sid)
        assert next(e for e in events if e["type"] == "run_start")["session"] == sid
        assert next(e for e in events if e["type"] == "run_done")["summary"]["overall"] == expected
        assert any(e["type"] == "report" for e in events)
        assert events[-1]["type"] == "done"
        assert f"종합 판정: {expected}" in client.get(f"/api/v1/sessions/{sid}/report").text


def test_simultaneous_run_requests_start_only_one_worker(client, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    sid = prepared_session(client)
    entered, release = threading.Event(), threading.Event()
    original_replay = server.replay

    def blocked_replay(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        yield from original_replay(*args, **kwargs)

    monkeypatch.setattr(server, "replay", blocked_replay)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            requests = [pool.submit(client.post, f"/api/v1/sessions/{sid}/run", json={"speed": 0}) for _ in range(2)]
            assert sorted(r.result().status_code for r in requests) == [200, 409]
        assert entered.wait(2)
        assert client.post(f"/api/v1/sessions/{sid}/approve", json={"force": True}).status_code == 409
        assert client.post(f"/api/v1/sessions/{sid}/report").status_code == 409
        assert client.delete(f"/api/v1/sessions/{sid}").status_code == 409
    finally:
        release.set()
        wait_finished(client, sid)


def test_completed_session_stays_locked_after_server_restart(client):
    sid = completed_session(client)
    previous = server.app.state.cx
    replacement = server.State()
    replacement.store = SessionStore(previous.store.root)
    replacement.llm, replacement.llm_checked = previous.llm, previous.llm_checked
    server.app.state.cx = replacement
    assert client.get(f"/api/v1/sessions/{sid}").json()["has_run"]
    assert client.post(f"/api/v1/sessions/{sid}/run", json={"speed": 0}).status_code == 409
    assert client.post(f"/api/v1/sessions/{sid}/approve", json={"force": True}).status_code == 409
    assert client.post(f"/api/v1/sessions/{sid}/report").status_code == 200


def test_report_uses_execution_snapshot_if_approval_file_changes(client):
    sid = completed_session(client)
    store = server.app.state.cx.store
    original_facts = store.load_json(sid, "facts.json")
    changed = store.load_json(sid, "rules.approved.json")
    changed["steps"][0]["title"] = "Changed outside the application"
    store.save_json(sid, "rules.approved.json", changed)
    assert client.post(f"/api/v1/sessions/{sid}/report").status_code == 200
    assert store.load_json(sid, "facts.json") == original_facts
    assert "Changed outside the application" not in store.load_text(sid, "report.md")


def test_legacy_execution_files_prevent_reapproval_and_rerun(client):
    sid = completed_session(client)
    store = server.app.state.cx.store
    (store.path(sid) / "rules.executed.json").unlink()
    store.update_meta(sid, summary={"status": "approved"})
    assert client.post(f"/api/v1/sessions/{sid}/approve", json={"force": True}).status_code == 409
    assert client.post(f"/api/v1/sessions/{sid}/run", json={"speed": 0}).status_code == 409
    assert client.post(f"/api/v1/sessions/{sid}/report").status_code == 200


def test_stream_delivers_events_published_just_after_subscription(client, monkeypatch):
    sid = prepared_session(client)
    bus = server.app.state.cx.bus(sid)
    subscribe = bus.subscribe

    def subscribe_at_completion():
        result = subscribe()
        for event in ({"type": "verdict", "state": "PASS"}, {"type": "report"}, {"type": "done"}):
            bus.publish(event)
        return result

    monkeypatch.setattr(bus, "subscribe", subscribe_at_completion)
    assert [e["type"] for e in read_events(client, sid)][-3:] == ["verdict", "report", "done"]


@pytest.mark.parametrize("body", [{"speed":"bad"},{"speed":None},{"speed":-1},{"speed":"NaN"},
                                    {"speed":"Infinity"},{"seed":1.5},{"seed":True},{"telemetry":[]}])
def test_invalid_run_options_do_not_consume_session(client,body):
    sid=client.post("/api/v1/sessions",json={"case":"pass"}).json()["id"]
    assert client.post(f"/api/v1/sessions/{sid}/approve",json={}).status_code==200
    assert client.post(f"/api/v1/sessions/{sid}/run",json=body).status_code==400
    assert not server._st().store.has(sid,"rules.executed.json")
    assert client.get(f"/api/v1/sessions/{sid}").json()["meta"]["summary"]["status"]=="approved"


@pytest.mark.parametrize("body", [{"mode":"unknown"},{"mode":[]},{"case":[]},{"case":None}])
def test_invalid_session_inputs_return_client_error(client,body):
    before=len(client.get("/api/v1/sessions").json()["sessions"])
    assert client.post("/api/v1/sessions",json=body).status_code==400
    assert len(client.get("/api/v1/sessions").json()["sessions"])==before


def test_string_force_is_not_a_boolean_override(client):
    sid=client.post("/api/v1/sessions",json={"case":"preflight_warn"}).json()["id"]
    assert client.post(f"/api/v1/sessions/{sid}/approve",json={"force":"false"}).status_code==400
    assert not server._st().store.has(sid,"rules.approved.json")


def test_active_extraction_cannot_be_deleted(client):
    sid=client.post("/api/v1/sessions",json={"case":"pass"}).json()["id"]
    server._st().store.update_meta(sid,summary={"status":"extracting"})
    assert client.delete(f"/api/v1/sessions/{sid}").status_code==409
    assert server._st().store.exists(sid)


@pytest.mark.parametrize("field,value",[("action","원문에 없는 조치"),("rollback",["원문에 없는 복구"])])
def test_source_instruction_change_cannot_be_force_approved(client,field,value):
    sid=client.post("/api/v1/sessions",json={"case":"pass"}).json()["id"]
    server._st().store.update_meta(sid,summary={"mode":"llm"})
    plan=server._st().store.load_json(sid,"plan.json")
    plan["steps"][4][field]=value
    assert client.post(f"/api/v1/sessions/{sid}/approve",json={"plan":plan,"force":True}).status_code==409
    assert not server._st().store.has(sid,"rules.approved.json")
