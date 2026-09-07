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
