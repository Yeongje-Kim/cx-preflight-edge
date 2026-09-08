
"""Regressions found while reviewing actual board report output."""
import json
import pytest
from cxpe.cli import load_plan
from cxpe.engine import run_stream
from cxpe.llm.client import FakeBackend
from cxpe.llm.report import build_facts, draft_report_ko, render_report_md
from cxpe.synth import make_case


def facts_for(case):
    plan = load_plan()
    rows, _, _ = make_case(case)
    engine = run_stream(plan, rows)
    return build_facts(plan, engine.verdicts, engine.segments)


def answer(actions, summary=""):
    return FakeBackend(responses=[json.dumps({"summary_ko": summary, "actions_ko": actions,
                                              "reason_codes": ["EXPECTED_MET"]}, ensure_ascii=False)])


def test_observed_duration_cannot_become_a_new_time_limit():
    facts = facts_for("pass")
    assert any(step["t_end"] - step["t_start"] == 40 for step in facts["steps"])
    result = draft_report_ko(facts, answer(["CH2 상태의 조건을 40초 이내에 회복한다."]))
    assert result["source"] == "template(llm-guard)"
    assert all("40초" not in action for action in result["actions_ko"])


def test_model_summary_cannot_reclassify_skipped_steps():
    facts = facts_for("fail_start")
    result = draft_report_ko(facts, answer(["인터록과 기동 이력을 검토한다."],
                                         "나머지 모든 단계는 통과했다."))
    assert result["source"] == "fake"
    assert "이후 3개 단계는 실행하지 않았다" in result["summary_ko"]
    assert "나머지 모든 단계는 통과했다" not in render_report_md(facts, result)


@pytest.mark.parametrize("bad", ["추가 감시 및 데이터 수집ĠìļĶì²Ń", "계측 기록을 확�인한다.", "기록을 보관한다.Ã"])
def test_corrupted_korean_actions_use_template(bad):
    result = draft_report_ko(facts_for("fail_dropout"), answer([bad]))
    assert result["source"] == "template(llm-guard)"
    assert bad not in result["actions_ko"]


def test_digits_in_known_equipment_and_step_ids_remain_usable():
    action = "S5 단계의 CH-2 기동 상태와 CH2_STATUS 계측 기록을 검토한다."
    result = draft_report_ko(facts_for("fail_start"), answer([action]))
    assert result["source"] == "fake"
    assert result["actions_ko"] == [action]


def test_report_render_uses_facts_even_for_legacy_remarks():
    remarks = {"summary_ko": "모든 단계가 합격했다.", "actions_ko": ["기록을 보관한다."],
               "reason_codes": [], "source": "legacy"}
    md = render_report_md(facts_for("fail_start"), remarks)
    assert "모든 단계가 합격했다." not in md
    assert "종합 판정: FAIL" in md and "이후 3개 단계는 실행하지 않았다" in md
    assert "판정 요약: 엔진 템플릿" in md


@pytest.mark.parametrize("actions", ["기록을 보관한다.", [{"text": "기록을 보관한다."}]])
def test_invalid_action_shapes_are_not_displayed(actions):
    result = draft_report_ko(facts_for("pass"), answer(actions))
    assert result["source"].startswith("template")
