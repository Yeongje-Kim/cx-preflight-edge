import hashlib
import json
from pathlib import Path

import pytest

from cxpe.engine import run_stream
from cxpe.schemas import TestPlan
from cxpe.synth import CASES, make_case
from cxpe.telemetry import read_csv, write_csv

ROOT = Path(__file__).resolve().parents[1]
TOL = 3.0


@pytest.fixture(scope="module")
def plan() -> TestPlan:
    return TestPlan.model_validate(json.loads((ROOT / "data/golden/plan.json").read_text(encoding="utf-8")))


def _digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def test_deterministic_for_seed():
    a, _, _ = make_case("pass", seed=7)
    b, _, _ = make_case("pass", seed=7)
    c, _, _ = make_case("pass", seed=8)
    assert _digest(a) == _digest(b) and _digest(a) != _digest(c)


@pytest.mark.parametrize("name", sorted(CASES))
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_engine_matches_labels(plan, name, seed):
    rows, labels, _ = make_case(name, seed=seed)
    eng = run_stream(plan, rows)
    got = {rt.step.id: rt.state.value for rt in eng.rts}
    exp = {sid: lab["state"] for sid, lab in labels["steps"].items()}
    assert got == exp, f"{name}/{seed}: {got} vs {exp}"
    for v in eng.verdicts:
        lab = labels["steps"][v.step_id]
        assert [c.value for c in v.reason_codes] == lab["codes"], (name, v.step_id, v.reason_ko)
        assert abs(v.t_end - lab["t_end"]) <= TOL, (name, v.step_id, v.t_end, lab["t_end"])
    assert eng.summary()["overall"] == labels["overall"]
    ex_got = [(s.step_id, s.t0, s.t1) for s in eng.segments if s.kind == "exceeded"]
    for lab in labels["exceeded"]:
        match = [e for e in ex_got if e[0] == lab["step_id"]]
        assert match, (name, "exceeded segment missing", ex_got)
        sid, t0, t1 = match[0]
        assert abs(t0 - lab["t0"]) <= TOL
        if lab["t1"] is None:
            assert t1 is None
        else:
            assert t1 is not None and abs(t1 - lab["t1"]) <= TOL


def test_csv_roundtrip(tmp_path):
    rows, _, _ = make_case("fail_dropout")
    p = tmp_path / "t.csv"
    write_csv(rows, p)
    back = read_csv(p)
    assert len(back) == len(rows)
    assert back[65]["CHWS_T_SUP"] is None and back[10]["CHWS_T_SUP"] is not None
    assert back[0]["t_sec"] == 0.0
