import json
import pytest
from cxpe.cli import load_plan
from cxpe.llm.client import FakeBackend
from cxpe.llm.extract import map_tags, find_contradictions
from cxpe.schemas import TagList

@pytest.mark.parametrize("value", ["invalid", [None], [123], {}, 42])
def test_malformed_suggestion_collections_do_not_abort_extraction(value):
    plan=load_plan()
    assert map_tags(plan,TagList(tags=[]),FakeBackend(responses=[json.dumps({"mappings":value})]))[0].source=="ai_suggestion"
    assert find_contradictions(plan,FakeBackend(responses=[json.dumps({"candidates":value})]))[0].source=="ai_suggestion"

@pytest.mark.parametrize("confidence", ["invalid",None,{},[],float("nan"),float("inf"),-1,2])
def test_invalid_mapping_confidence_is_ignored(confidence):
    backend=FakeBackend(responses=[json.dumps({"mappings":[{"name":"CH1_STATUS","tag":"CH1_STATUS","confidence":confidence}]})])
    assert map_tags(load_plan(),TagList(tags=[]),backend)==[]

@pytest.mark.parametrize("entry", [{"step_id":[]}, {"step_id":"S1","message":[]}])
def test_invalid_contradiction_entry_is_ignored(entry):
    assert find_contradictions(load_plan(),FakeBackend(responses=[json.dumps({"candidates":[entry]})]))==[]
