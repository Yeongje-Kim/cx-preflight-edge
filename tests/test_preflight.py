import json
from pathlib import Path

import pytest

from cxpe.preflight import contradicts, implies, run_preflight
from cxpe.schemas import Cond, TagList, TestPlan

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def load_plan(path: Path) -> TestPlan:
    return TestPlan.model_validate(json.loads(path.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def tags() -> TagList:
    return TagList.model_validate(json.loads((DATA / "golden" / "tags.json").read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def index() -> dict:
    return json.loads((DATA / "defective" / "index.json").read_text(encoding="utf-8"))


def test_golden_has_no_findings(tags):
    plan = load_plan(DATA / "golden" / "plan.json")
    assert run_preflight(plan, tags) == []


def _names():
    idx = json.loads((DATA / "defective" / "index.json").read_text(encoding="utf-8"))
    return sorted(idx)


@pytest.mark.parametrize("name", _names())
def test_defective_plans_are_detected(name, tags, index):
    entry = index[name]
    plan = load_plan(DATA / "defective" / entry["file"])
    findings = run_preflight(plan, tags)
    got = sorted({f.code.value for f in findings})
    assert got == entry["expected_codes"], f"{name}: {[f.message for f in findings]}"


def test_interval_algebra():
    a = Cond(tag="X", op="==", value=1)
    b = Cond(tag="X", op="==", value=0)
    band = Cond(tag="X", op="in_band", band=(6, 8))
    hi = Cond(tag="X", op=">", value=14)
    assert contradicts(a, b) is True
    assert implies(a, a) is True
    assert contradicts(band, hi) is True
    assert contradicts(band, Cond(tag="X", op="<=", value=8)) is False
    assert implies(Cond(tag="X", op="in_band", band=(6.5, 7.5)), band) is True
    assert implies(band, Cond(tag="X", op="in_band", band=(6.5, 7.5))) is False
    assert contradicts(Cond(tag="X", op="!=", value=1), a) is None
