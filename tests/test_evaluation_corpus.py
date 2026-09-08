"""Fixture expectations are authored independently; validate their support boundaries."""
import json
from pathlib import Path
import pytest
from cxpe.llm.extract import extract_plan
from cxpe.llm.client import FakeBackend
from cxpe.llm.source_guard import validate_plan_source
from cxpe.schemas import TestPlan

CASES=json.loads((Path(__file__).resolve().parents[1]/"data/evaluation/procedures.json").read_text(encoding="utf-8"))

@pytest.mark.parametrize("case",CASES,ids=lambda c:c["id"])
def test_independently_specified_procedure(case):
    expected=TestPlan.model_validate(case["expected"])
    validate_plan_source(expected,case["markdown"])
    outputs=[s.model_dump_json() for s in expected.steps]
    plan,stats=extract_plan(case["markdown"],FakeBackend(responses=outputs),golden=None)
    assert stats["n_ok"]==len(expected.steps) and stats["fallback_ids"]==[]
    # Wait budgets are application defaults, not values extracted from the source.
    assert all(s.precond_wait_sec == 10 and s.trigger_wait_sec == 120 for s in plan.steps)
    assert [s.model_dump(exclude={"source_text", "precond_wait_sec", "trigger_wait_sec"}) for s in plan.steps]==[s.model_dump(exclude={"source_text", "precond_wait_sec", "trigger_wait_sec"}) for s in expected.steps]
