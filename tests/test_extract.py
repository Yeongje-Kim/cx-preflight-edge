import json
from pathlib import Path

import httpx
import pytest

from cxpe.engine import run_stream
from cxpe.llm.client import FakeBackend, LlmError, NativeBackend
from cxpe.llm.extract import chunk_steps, extract_plan, extract_step, parse_json_loose, plan_header
from cxpe.llm.report import build_facts, draft_report_ko, guard_numbers, render_report_md
from cxpe.schemas import TestPlan
from cxpe.synth import make_case

ROOT = Path(__file__).resolve().parents[1]
MD = (ROOT / "data/plan_n1_cooling.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def golden() -> TestPlan:
    return TestPlan.model_validate(json.loads((ROOT / "data/golden/plan.json").read_text(encoding="utf-8")))


def golden_json_for(golden: TestPlan, step_id: str) -> str:
    s = golden.step(step_id).model_dump(mode="json", exclude={"source_text", "precond_wait_sec", "trigger_wait_sec"})
    return json.dumps(s, ensure_ascii=False)


def test_parse_json_loose_variants():
    assert parse_json_loose('```json\n{"a": 1}\n```')["a"] == 1
    assert parse_json_loose('<think>x</think> here {"a": {"b": [1, 2]}} trailing')["a"]["b"] == [1, 2]
    assert parse_json_loose('[BEGIN]: {"a": "}"} [END]')["a"] == "}"
    with pytest.raises(ValueError):
        parse_json_loose("no json here")


def test_chunk_steps_and_header():
    chunks = chunk_steps(MD)
    assert [c[0] for c in chunks] == [f"S{i}" for i in range(1, 9)]
    assert "S1 초기 상태 확인" not in chunks[1][2]
    assert "## 5. 기록" not in chunks[-1][2]
    pid, title = plan_header(MD)
    assert pid == "IST-COOL-N1-01" and "N+1" in title


def test_extract_plan_with_fake_backend_matches_golden(golden):
    def responder(messages):
        text = messages[-1]["content"]
        sid = next(c[0] for c in chunk_steps(MD) if c[2] in text)
        return "output:\n" + golden_json_for(golden, sid)

    plan, stats = extract_plan(MD, FakeBackend(responder=responder), golden=None)
    assert stats["n_ok"] == 8 and stats["fallback_ids"] == []
    skip = {"source_text", "precond_wait_sec", "trigger_wait_sec"}  # 대기시간은 추출 대상이 아니다(엔진 튜닝값)
    for s_pred, s_gold in zip(plan.steps, golden.steps):
        assert s_pred.model_dump(exclude=skip) == s_gold.model_dump(exclude=skip)


def test_extract_step_retries_then_succeeds(golden):
    fb = FakeBackend(responses=["garbage", golden_json_for(golden, "S3")])
    step, stats = extract_step(fb, "S3", "CH-1 모의 트립", "### S3 ...")
    assert step is not None and stats["attempts"] == 2 and stats["ok"]
    assert "invalid" in fb.calls[1][-2]["content"]


def test_extract_plan_falls_back_to_golden(golden):
    fb = FakeBackend(responder=lambda m: "not json at all")
    plan, stats = extract_plan(MD, fb, golden=golden)
    assert stats["n_ok"] == 0 and len(stats["fallback_ids"]) == 8
    assert plan.steps[0].model_dump(exclude={"source_text"}) == golden.steps[0].model_dump(exclude={"source_text"})


def test_extract_plan_raises_without_golden():
    with pytest.raises(LlmError):
        extract_plan(MD, FakeBackend(responder=lambda m: "nope"), golden=None)


def test_rust_backend_superseded_retry(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, json={"error": "superseded"})
        return httpx.Response(200, json={"text": "{\"ok\": true}"})

    transport = httpx.MockTransport(handler)
    real_post = httpx.post

    def fake_post(url, **kw):
        with httpx.Client(transport=transport) as c:
            return c.post(url, **kw)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr("time.sleep", lambda s: None)
    b = NativeBackend()
    b.QUERY_GAP_SEC = 0
    out = b.complete([{"role": "user", "content": "hi"}])
    assert json.loads(out)["ok"] is True and calls["n"] == 2
    monkeypatch.setattr(httpx, "post", real_post)


def test_report_guard_and_render(golden):
    rows, labels, _ = make_case("fail_start")
    eng = run_stream(golden, rows)
    facts = build_facts(golden, eng.verdicts, eng.segments, {"case": "fail_start"})
    assert facts["overall"] == "FAIL" and facts["n_skipped"] == 3
    assert guard_numbers("허용시간 30초 내 기동하지 않았다", facts)
    assert not guard_numbers("허용시간 999초 내", facts)
    hallucinated = FakeBackend(responses=[json.dumps({"summary_ko": "대기기가 999초 만에 기동했다", "actions_ko": [],
                                                      "reason_codes": ["STANDBY_START_TIMEOUT"]}, ensure_ascii=False)])
    r = draft_report_ko(facts, hallucinated)
    assert r["source"].startswith("template")
    good = FakeBackend(responses=[json.dumps({"summary_ko": "S5 단계에서 대기기 기동이 허용시간 30초를 넘겨 불합격이다.",
                                              "actions_ko": ["인터록을 점검한다."],
                                              "reason_codes": ["STANDBY_START_TIMEOUT", "EXPECTED_MET", "BOGUS"]},
                                             ensure_ascii=False)])
    r = draft_report_ko(facts, good)
    assert r["source"] == "fake" and r["reason_codes"][0] == "STANDBY_START_TIMEOUT" and "BOGUS" not in r["reason_codes"]
    md = render_report_md(facts, r, {"id": "s1", "case": "fail_start"})
    assert "종합 판정: FAIL" in md and "| S5 |" in md and "허용시간 초과 구간" in md
