import json
from pathlib import Path
import pytest
from cxpe.llm.client import FakeBackend
from cxpe.llm.extract import chunk_steps, extract_plan, extract_step
from cxpe.llm.source_guard import validate_source, validate_plan_source
from cxpe.schemas import TestPlan

ROOT=Path(__file__).resolve().parents[1]
MD=(ROOT/'data/plan_n1_cooling.md').read_text(encoding='utf-8')
PLAN=TestPlan.model_validate(json.loads((ROOT/'data/golden/plan.json').read_text(encoding='utf-8')))

@pytest.mark.parametrize('field,value', [('within_sec',10),('hold_sec',30),('op','>'),('value',201),('tag','CH2_STATUS')])
def test_detects_semantic_errors_despite_valid_schema(field,value):
    step=PLAN.step('S2').model_copy(deep=True)
    setattr(step.expected[0],field,value)
    step=type(step).model_validate(step.model_dump())
    with pytest.raises(ValueError,match=field):
        validate_source(step,chunk_steps(MD)[1][2])


def test_semantic_retry_uses_source_not_successful_json_shape():
    good=PLAN.step('S2').model_dump(mode='json', exclude={'source_text'})
    bad=json.loads(json.dumps(good));bad['expected'][0].update(within_sec=10,hold_sec=30,op='>')
    backend=FakeBackend(responses=[json.dumps(bad),json.dumps(good)])
    step,stats=extract_step(backend,'S2','부하 안정 확인',chunk_steps(MD)[1][2])
    assert stats['attempts']==2 and stats['source_verified']
    assert step.expected[0].within_sec==30 and step.expected[0].hold_sec==10
    assert '원문 값' in backend.calls[1][-1]['content']


def test_changed_source_values_are_not_hardcoded():
    text=chunk_steps(MD)[1][2].replace('200','350').replace('허용시간 30초','허용시간 45초')
    step=PLAN.step('S2').model_copy(deep=True)
    step.expected[0].value=350;step.expected[0].within_sec=45
    step.action=step.action.replace('200','350')
    validate_source(step,text)
    with pytest.raises(ValueError):validate_source(PLAN.step('S2'),text)


@pytest.mark.parametrize('change', ['missing','duplicate','unsupported','state','precondition'])
def test_rejects_unverifiable_or_mismatched_source(change):
    step=PLAN.step('S2').model_copy(deep=True);text=chunk_steps(MD)[1][2]
    if change=='missing':text=text.replace('허용시간 30초.','')
    if change=='duplicate':text+='\n- 기대 결과: LOAD_KW > 2 이 3초 이내'
    if change=='unsupported':text=text.replace('200 kW 이상','충분한 부하')
    if change=='state':step.changes_state=False
    if change=='precondition':step.preconditions=[]
    with pytest.raises(ValueError):validate_source(step,text)


def test_bad_semantic_extraction_falls_back_only_to_verified_reference():
    def responder(messages):
        text=messages[1]['content'];sid=next(sid for sid,_,chunk in chunk_steps(MD) if chunk in text)
        data=PLAN.step(sid).model_dump(mode='json', exclude={'source_text'})
        if sid=='S2':data['expected'][0].update(within_sec=10,hold_sec=30)
        return json.dumps(data)
    plan,stats=extract_plan(MD,FakeBackend(responder=responder),golden=PLAN)
    assert stats['fallback_ids']==['S2'] and stats['n_ok']==7
    validate_plan_source(plan,MD)
    corrupt=PLAN.model_copy(deep=True);corrupt.step('S2').expected[0].within_sec=10
    with pytest.raises(ValueError):extract_plan(MD,FakeBackend(responder=responder),golden=corrupt)


def test_missing_step_rejected_at_plan_boundary():
    short=PLAN.model_copy(deep=True);short.steps.pop()
    with pytest.raises(ValueError):validate_plan_source(short,MD)



def test_json_unicode_and_escaped_quotes_do_not_drop_outer_object():
    from cxpe.llm.extract import parse_json_loose
    data={'title':'부하 안정 확인', 'rollback':['복구 "확인" }, {'], 'expected':[{'within_sec':30,'hold_sec':10}]}
    assert parse_json_loose(json.dumps(data,ensure_ascii=True))==data
    assert parse_json_loose('prefix {"old":true} '+json.dumps(data,ensure_ascii=False))==data


@pytest.mark.parametrize("field,value", [("action", "원문에 없는 조작"), ("rollback", ["원문에 없는 복구"]),
                                         ("title", "다른 단계"), ("source_text", "바꾼 원문")])
def test_instruction_mutations_are_rejected(field,value):
    step=PLAN.step("S5").model_copy(deep=True)
    setattr(step,field,value)
    with pytest.raises(ValueError,match=field):validate_source(step,chunk_steps(MD)[4][2])


def test_extraction_preserves_instruction_and_recovery_verbatim():
    source=chunk_steps(MD)[4][2]
    response=PLAN.step("S5").model_dump(mode="json")
    response.update(title="잘못된 제목",action="센서를 교체한다.",rollback=["임의 복구"],precond_wait_sec=999)
    step,stats=extract_step(FakeBackend(responses=[json.dumps(response)]),"S5","CH-2 운전 확인",source)
    assert step.action == "CH-2 기동 및 운전 상태를 확인한다."
    assert step.rollback == ["CH-2를 수동 정지한다."]
    assert step.title == "CH-2 운전 확인" and step.precond_wait_sec == 10
    assert {"title","action","rollback"} <= set(stats["narrative_restored_fields"])


def test_wrapped_original_instruction_is_preserved_without_model_rewrite():
    source=chunk_steps(MD)[4][2].replace("- 조치: CH-2 기동 및 운전 상태를 확인한다.",
        "* 조치: CH-2 기동 및\n  운전 상태를 확인한다.")
    response=PLAN.step("S5").model_dump(mode="json")
    step,stats=extract_step(FakeBackend(responses=[json.dumps(response)]),"S5","CH-2 운전 확인",source)
    assert stats["ok"] and step.action=="CH-2 기동 및 운전 상태를 확인한다."


def test_narrative_change_cannot_sneak_through_fallback():
    corrupt=PLAN.model_copy(deep=True);corrupt.steps[0].rollback=["임의 복구"]
    with pytest.raises(ValueError,match="rollback"):
        extract_plan(MD,FakeBackend(responder=lambda _:"bad"),golden=corrupt)


@pytest.mark.parametrize("replacement", ["200 kW 이상 또는 100 kW 이하", "충분한 부하", "200 kW 이상 (허용시간 0초)"])
def test_unsupported_source_does_not_waste_model_calls(replacement):
    source=chunk_steps(MD)[1][2].replace("200 kW 이상",replacement)
    backend=FakeBackend()
    step,stats=extract_step(backend,"S2","부하 안정 확인",source)
    assert step is None and stats["failure_kind"]=="unsupported_source"
    assert not backend.calls


@pytest.mark.parametrize("text", [
    "PUMP_PRESSURE >= 1,000 (허용시간 10초)",
    "PUMP_PRESSURE >= 1~5 (허용시간 10초)",
    "PUMP_PRESSURE >= 1이 아닌 상태 (허용시간 10초)",
    "PUMP_PRESSURE >= 1 또는 0 (허용시간 10초)",
    "PUMP_PRESSURE >= 1 (허용시간 10초, 2분 유지)",
    "PUMP_PRESSURE >= 1e309 (허용시간 10초)",
])
def test_ambiguous_numeric_clauses_are_not_partially_accepted(text):
    from cxpe.llm.source_guard import source_reference
    with pytest.raises(ValueError):
        source_reference("### S1 압력 확인\n- 조치: 압력을 확인한다.\n- 기대 결과: " + text)


def test_scientific_and_leading_decimal_values_are_preserved():
    from cxpe.llm.source_guard import conditions
    assert conditions("PUMP_PRESSURE >= 1e3")[0]["value"] == 1000
    assert conditions("PUMP_PRESSURE >= .5")[0]["value"] == .5


def test_retry_reports_all_timing_and_count_mismatches_together():
    source="### S1 밸브 확인\n- 조치: 피드백을 확인한다.\n- 기대 결과: VALVE_FB == 75.5를 2초 유지. 허용시간 8초.\n- 상태 변경: 없음"
    from cxpe.schemas import Step
    expected=Step(id="S1",title="밸브 확인",action="피드백을 확인한다.",expected=[{"tag":"VALVE_FB","op":"==","value":75.5,"within_sec":8,"hold_sec":2}])
    bad=expected.model_dump(mode="json")
    bad["expected"][0].update(within_sec=2,hold_sec=0)
    bad["expected"].append(dict(bad["expected"][0]))
    backend=FakeBackend(responses=[json.dumps(bad),expected.model_dump_json()])
    step,stats=extract_step(backend,"S1","밸브 확인",source)
    assert step is not None and stats["attempts"]==2
    feedback=backend.calls[1][-1]["content"]
    assert "원문 조건 1개, 추출 2개" in feedback
    assert "within_sec: 원문 값 8.0" in feedback
    assert "hold_sec: 원문 값 2.0" in feedback
