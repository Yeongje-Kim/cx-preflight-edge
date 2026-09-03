"""골든 시험절차서에서 결함을 주입한 변형 12개를 만든다.

python scripts/make_defective.py  → data/defective/*.json + data/defective/index.json
index.json의 expected_codes가 test_preflight의 정답이다.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "data" / "golden" / "plan.json"
OUT = ROOT / "data" / "defective"


def load() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def step(plan: dict, sid: str) -> dict:
    return next(s for s in plan["steps"] if s["id"] == sid)


def variants() -> dict[str, tuple[dict, list[str], str]]:
    out: dict[str, tuple[dict, list[str], str]] = {}

    p = load(); step(p, "S3")["preconditions"] = []
    out["pf01_s3_no_precond"] = (p, ["PF01_MISSING_PRECONDITION"], "S3 사전조건 삭제")

    p = load(); step(p, "S5")["preconditions"] = []
    out["pf01_s5_no_precond"] = (p, ["PF01_MISSING_PRECONDITION"], "S5 사전조건 삭제")

    p = load(); s = p["steps"]; i4, i5 = 3, 4; s[i4], s[i5] = s[i5], s[i4]
    out["pf02_s5_before_s4"] = (p, ["PF02_ORDER_CONTRADICTION"], "S4·S5 순서 뒤바뀜: S5 사전조건 CH2_CMD=1이 뒤 단계에서만 성립")

    p = load(); step(p, "S7")["preconditions"] = [{"tag": "CH2_STATUS", "op": "==", "value": 0}]
    out["pf02_s7_precond_ch2_off"] = (p, ["PF02_ORDER_CONTRADICTION"], "S7 사전조건이 S5 결과(CH2 운전)와 모순, S8에서만 성립")

    p = load(); step(p, "S6")["expected"] = []
    out["pf03_s6_no_expected"] = (p, ["PF03_NO_EXPECTED_RESULT"], "S6 기대 결과 삭제")

    p = load(); step(p, "S4")["expected"] = []
    out["pf03_s4_no_expected"] = (p, ["PF03_NO_EXPECTED_RESULT"], "S4 기대 결과 삭제")

    p = load(); step(p, "S7")["expected"][0]["tag"] = "CHWS_TEMP_SUPPLY"
    out["pf04_s7_tag_renamed"] = (p, ["PF04_TAG_UNMAPPED"], "S7 기대 결과 태그가 태그리스트에 없음")

    p = load(); step(p, "S2")["expected"][0]["tag"] = "LOAD_KWH"
    out["pf04_s2_tag_typo"] = (p, ["PF04_TAG_UNMAPPED"], "S2 태그 오타")

    p = load(); step(p, "S3")["rollback"] = []
    out["pf05_s3_no_rollback"] = (p, ["PF05_MISSING_ROLLBACK"], "S3 복구 절차 삭제")

    p = load(); step(p, "S2")["rollback"] = []
    out["pf05_s2_no_rollback"] = (p, ["PF05_MISSING_ROLLBACK"], "S2 복구 절차 삭제")

    p = load(); step(p, "S7")["expected"][0]["band"] = [10, 12]
    out["pf06_s7_band_10_12"] = (p, ["PF06_THRESHOLD_CONFLICT"], "S7 온도 기준 [10,12]가 S1 [6,8]과 서로소")

    p = load()
    step(p, "S7")["expected"][0]["tag"] = "CHWS_TEMP_SUPPLY"
    step(p, "S6")["expected"] = []
    step(p, "S3")["rollback"] = []
    out["mixed_demo"] = (p, ["PF03_NO_EXPECTED_RESULT", "PF04_TAG_UNMAPPED", "PF05_MISSING_ROLLBACK"],
                         "데모용 복합 결함: 태그 미매핑 + 기대 결과 누락 + 복구 누락")
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    index = {}
    for name, (plan, codes, note) in variants().items():
        plan = copy.deepcopy(plan)
        plan["plan_id"] = f"{plan['plan_id']}-DEF-{name}"
        path = OUT / f"{name}.json"
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        index[name] = {"file": path.name, "expected_codes": sorted(codes), "note": note}
    (OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(index)} defective plans -> {OUT}")


if __name__ == "__main__":
    main()
