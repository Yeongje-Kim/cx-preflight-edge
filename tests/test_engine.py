import json
from pathlib import Path

import pytest

from cxpe.engine import EngineConfig, RunEngine, run_stream
from cxpe.schemas import ReasonCode, StepState, TestPlan

ROOT = Path(__file__).resolve().parents[1]


def mini_plan(**kw) -> TestPlan:
    base = {
        "plan_id": "MINI", "title": "mini", "steps": [
            {"id": "A", "title": "a", "preconditions": [{"tag": "P", "op": "==", "value": 1}],
             "expected": [{"tag": "X", "op": "==", "value": 1, "within_sec": 5, "hold_sec": 0}],
             "abort_conditions": [{"tag": "T", "op": ">", "value": 14}], "changes_state": True,
             "rollback": ["r"], "precond_wait_sec": 3, "trigger_wait_sec": 4},
            {"id": "B", "title": "b", "preconditions": [{"tag": "X", "op": "==", "value": 1}],
             "expected": [{"tag": "Y", "op": "in_band", "band": [6, 8], "within_sec": 4, "hold_sec": 2}],
             "rollback": ["r"], "changes_state": True},
        ],
    }
    base.update(kw)
    return TestPlan.model_validate(base)


def rows(spec):
    """spec: list of (t, dict) -> samples with defaults."""
    out = []
    for t, d in spec:
        r = {"t_sec": float(t), "P": 1.0, "X": 0.0, "Y": 10.0, "T": 7.0}
        r.update(d)
        out.append(r)
    return out


def states(eng):
    return {rt.step.id: rt.state for rt in eng.rts}


def codes(eng, sid):
    return [v.reason_codes for v in eng.verdicts if v.step_id == sid][0]


def test_pass_path_with_hold():
    eng = run_stream(mini_plan(), rows([(0, {}), (1, {"X": 1}), (2, {"X": 1, "Y": 7}), (3, {"X": 1, "Y": 7}),
                                       (4, {"X": 1, "Y": 7}), (5, {"X": 1, "Y": 7})]))
    assert states(eng) == {"A": StepState.PASS, "B": StepState.PASS}
    assert codes(eng, "A") == [ReasonCode.EXPECTED_MET]
    vb = [v for v in eng.verdicts if v.step_id == "B"][0]
    assert vb.t_start == 2.0 and vb.t_end == 4.0  # met from 2, hold 2 -> confirmed at 4


def test_hold_resets_on_flicker():
    eng = run_stream(mini_plan(), rows([(0, {"X": 1}), (1, {"X": 1, "Y": 7}), (2, {"X": 1, "Y": 9}),
                                       (3, {"X": 1, "Y": 7}), (4, {"X": 1, "Y": 7}), (5, {"X": 1, "Y": 7})]))
    vb = [v for v in eng.verdicts if v.step_id == "B"][0]
    assert vb.state == StepState.PASS and vb.t_end == 5.0


def test_timeout_and_exceeded_segment():
    samples = rows([(t, {}) for t in range(0, 9)] + [(9, {"X": 1}), (10, {"X": 1})])
    eng = run_stream(mini_plan(), samples)
    assert states(eng)["A"] == StepState.FAIL
    assert codes(eng, "A") == [ReasonCode.STEP_TIMEOUT]
    assert states(eng)["B"] == StepState.SKIPPED
    ex = [s for s in eng.segments if s.kind == "exceeded"]
    assert ex and ex[0].t0 == 5.0 and ex[0].t1 == 9.0
    v = [v for v in eng.verdicts if v.step_id == "A"][0]
    assert v.evidence.extra["met_t"] == 9.0
    assert "허용시간" in v.reason_ko


def test_precondition_not_met():
    eng = run_stream(mini_plan(), rows([(t, {"P": 0}) for t in range(0, 6)]))
    assert states(eng)["A"] == StepState.FAIL
    assert codes(eng, "A") == [ReasonCode.PRECONDITION_NOT_MET]


def test_trigger_wait():
    plan = mini_plan()
    plan.steps[0].trigger = plan.steps[0].preconditions[0].model_copy(update={"tag": "G"})
    eng = run_stream(plan, [dict(r, G=0.0) for r in rows([(t, {"X": 1}) for t in range(0, 7)])])
    assert states(eng)["A"] == StepState.FAIL
    assert codes(eng, "A") == [ReasonCode.TRIGGER_NOT_OBSERVED]


def test_abort_condition():
    eng = run_stream(mini_plan(), rows([(0, {}), (1, {"T": 15})]))
    assert states(eng)["A"] == StepState.ABORT
    assert codes(eng, "A") == [ReasonCode.ABORT_CONDITION_HIT]
    assert eng.summary()["overall"] == "ABORT"


def test_missing_data():
    samples = rows([(0, {"X": 1}), (1, {"X": 1})]) + rows([(t, {"X": 1, "Y": None}) for t in range(2, 9)])
    eng = run_stream(mini_plan(), samples)
    assert states(eng)["B"] == StepState.FAIL
    assert codes(eng, "B") == [ReasonCode.TAG_MISSING_DATA]


def test_stream_end_marks_running_step_fail():
    eng = run_stream(mini_plan(), rows([(0, {}), (1, {})]))
    assert states(eng)["A"] == StepState.FAIL
    assert codes(eng, "A") == [ReasonCode.STEP_TIMEOUT]
    assert eng.verdicts[0].evidence.extra.get("stream_ended") is True


def test_stop_on_fail_false_continues():
    samples = rows([(t, {}) for t in range(0, 7)]) + rows([(t, {"X": 1, "Y": 7}) for t in range(7, 12)])
    eng = run_stream(mini_plan(), samples, EngineConfig(stop_on_fail=False))
    assert states(eng)["A"] == StepState.FAIL and states(eng)["B"] == StepState.PASS


def test_tag_alias_resolution():
    plan = mini_plan(tag_aliases={"Y": "Y_REAL"})
    samples = [dict(r, Y_REAL=r.pop("Y")) for r in rows([(0, {"X": 1}), (1, {"X": 1, "Y": 7}), (2, {"X": 1, "Y": 7}),
                                                          (3, {"X": 1, "Y": 7})])]
    eng = run_stream(plan, samples)
    assert states(eng)["B"] == StepState.PASS


def test_latency_recorded():
    eng = run_stream(mini_plan(), rows([(0, {"X": 1}), (1, {"X": 1, "Y": 7}), (2, {"X": 1, "Y": 7}), (3, {"X": 1, "Y": 7})]))
    assert all(v.latency_ms >= 0 for v in eng.verdicts)
