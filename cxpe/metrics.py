"""지표 산출. 전부 합성 SIL 데이터 기준이며 그 사실을 결과 문서에 명시한다.

python -m cxpe.metrics --out docs/metrics.md [--extraction sessions/<id>/plan.json]
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Optional

from .engine import run_stream
from .preflight import run_preflight
from .schemas import TagList, TestPlan
from .synth import CASES, make_case

ROOT = Path(__file__).resolve().parents[1]


def _load_plan(p: Path) -> TestPlan:
    return TestPlan.model_validate(json.loads(p.read_text(encoding="utf-8")))


def preflight_pr(tags: TagList, defective_dir: Path = ROOT / "data/defective") -> dict:
    index = json.loads((defective_dir / "index.json").read_text(encoding="utf-8"))
    tp = fp = fn = 0
    rows = []
    for name, entry in sorted(index.items()):
        plan = _load_plan(defective_dir / entry["file"])
        got = {f.code.value for f in run_preflight(plan, tags)}
        exp = set(entry["expected_codes"])
        tp += len(got & exp)
        fp += len(got - exp)
        fn += len(exp - got)
        rows.append({"plan": name, "expected": sorted(exp), "got": sorted(got), "ok": got == exp})
    golden_findings = run_preflight(_load_plan(ROOT / "data/golden/plan.json"), tags)
    fp += len(golden_findings)
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    return {"n_plans": len(rows) + 1, "tp": tp, "fp": fp, "fn": fn, "precision": prec, "recall": rec,
            "golden_false_positives": len(golden_findings), "rows": rows}


def verdict_accuracy(plan: TestPlan, seeds: range = range(0, 6)) -> dict:
    total = correct = 0
    lat: list[float] = []
    per_case = {}
    for name in sorted(CASES):
        c_total = c_correct = 0
        for seed in seeds:
            rows, labels, _ = make_case(name, seed=seed)
            eng = run_stream(plan, rows)
            for v in eng.verdicts:
                lab = labels["steps"][v.step_id]
                ok = v.state.value == lab["state"] and [c.value for c in v.reason_codes] == lab["codes"] \
                    and abs(v.t_end - lab["t_end"]) <= 3.0
                c_total += 1
                c_correct += int(ok)
                lat.append(v.latency_ms)
        per_case[name] = {"steps": c_total, "correct": c_correct}
        total += c_total
        correct += c_correct
    return {"runs": len(CASES) * len(seeds), "steps": total, "correct": correct,
            "accuracy": correct / total if total else 1.0, "per_case": per_case,
            "latency_ms": latency_stats(lat)}


def latency_stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    vals = sorted(values)
    return {"n": len(vals), "mean": statistics.fmean(vals), "p50": vals[len(vals) // 2],
            "p95": vals[min(len(vals) - 1, int(len(vals) * 0.95))], "max": vals[-1]}


def _flatten(plan: TestPlan) -> set[tuple]:
    out = set()
    for s in plan.steps:
        out.add((s.id, "title", s.title.strip()))
        out.add((s.id, "changes_state", s.changes_state))
        for c in s.preconditions:
            out.add((s.id, "precondition", c.threshold_text()))
        if s.trigger:
            out.add((s.id, "trigger", s.trigger.threshold_text()))
        for e in s.expected:
            out.add((s.id, "expected", e.threshold_text(), float(e.within_sec), float(e.hold_sec)))
        for c in s.abort_conditions:
            out.add((s.id, "abort", c.threshold_text()))
        for r in s.rollback:
            out.add((s.id, "rollback", r.strip()))
    return out


def extraction_f1(golden: TestPlan, pred: TestPlan) -> dict:
    g, p = _flatten(golden), _flatten(pred)
    tp = len(g & p)
    prec = tp / len(p) if p else 0.0
    rec = tp / len(g) if g else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    by_field: dict[str, dict] = {}
    for field in sorted({x[1] for x in g | p}):
        gf = {x for x in g if x[1] == field}
        pf = {x for x in p if x[1] == field}
        tpf = len(gf & pf)
        by_field[field] = {"golden": len(gf), "pred": len(pf), "tp": tpf,
                           "precision": tpf / len(pf) if pf else 0.0, "recall": tpf / len(gf) if gf else 0.0}
    return {"precision": prec, "recall": rec, "f1": f1, "golden_items": len(g), "pred_items": len(p),
            "by_field": by_field, "missing": sorted(str(x) for x in g - p)[:20],
            "extra": sorted(str(x) for x in p - g)[:20]}


def render_md(pf: dict, va: dict, ex: Optional[dict], extra: Optional[dict] = None) -> str:
    L = ["# 검증 결과 (합성 SIL 데이터 기준)", "",
         "> 모든 수치는 기능검증용 합성 데이터와 임의 기준값에서 측정한 값이다. 실제 현장 성능을 뜻하지 않는다.", "",
         "## 1. Preflight 검출", "",
         f"- 대상: 결함 주입 절차서 {pf['n_plans'] - 1}개 + 정상 절차서 1개",
         f"- 정밀도 {pf['precision']:.3f}, 재현율 {pf['recall']:.3f} (TP {pf['tp']}, FP {pf['fp']}, FN {pf['fn']})",
         f"- 정상 절차서 오탐: {pf['golden_false_positives']}건", "",
         "| 변형 | 기대 코드 | 검출 코드 | 일치 |", "|---|---|---|---|"]
    for r in pf["rows"]:
        L.append(f"| {r['plan']} | {', '.join(r['expected'])} | {', '.join(r['got'])} | {'O' if r['ok'] else 'X'} |")
    L += ["", "## 2. 단계 판정 정확도", "",
          f"- 시나리오 {len(va['per_case'])}종 × 시드 → {va['runs']}회 재생, 단계 {va['steps']}건",
          f"- 정확도 {va['accuracy']:.3f} (상태·사유코드·종료시각 ±3초 모두 일치)", "",
          "| 케이스 | 단계 | 일치 |", "|---|---|---|"]
    for k, v in va["per_case"].items():
        L.append(f"| {k} | {v['steps']} | {v['correct']} |")
    lat = va["latency_ms"]
    if lat.get("n"):
        L += ["", f"- 판정 지연(ms, 샘플 수신→판정 생성): 평균 {lat['mean']:.3f}, p50 {lat['p50']:.3f}, "
                  f"p95 {lat['p95']:.3f}, 최대 {lat['max']:.3f}"]
    L += ["", "## 3. LLM 추출 (골든 대비)", ""]
    if ex:
        L += [f"- 정밀도 {ex['precision']:.3f}, 재현율 {ex['recall']:.3f}, F1 {ex['f1']:.3f} "
              f"(골든 항목 {ex['golden_items']}, 추출 항목 {ex['pred_items']})", "",
              "| 필드 | 골든 | 추출 | 일치 | 정밀도 | 재현율 |", "|---|---|---|---|---|---|"]
        for f, v in ex["by_field"].items():
            L.append(f"| {f} | {v['golden']} | {v['pred']} | {v['tp']} | {v['precision']:.2f} | {v['recall']:.2f} |")
        if ex["missing"]:
            L += ["", "누락(일부): " + "; ".join(ex["missing"][:8])]
    else:
        L += ["- 미측정 (보드에서 LLM 추출 세션 실행 후 --extraction 옵션으로 산출)"]
    if extra:
        L += ["", "## 4. 보드 실행 지표", ""]
        for k, v in extra.items():
            L.append(f"- {k}: {v}")
    return "\n".join(L) + "\n"


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs/metrics.md"))
    ap.add_argument("--extraction", help="LLM 추출 결과 plan.json 경로 (세션 디렉터리)")
    ap.add_argument("--extra", help="보드 지표 JSON 파일")
    args = ap.parse_args(argv)
    tags = TagList.model_validate(json.loads((ROOT / "data/golden/tags.json").read_text(encoding="utf-8")))
    golden = _load_plan(ROOT / "data/golden/plan.json")
    pf = preflight_pr(tags)
    va = verdict_accuracy(golden)
    ex = extraction_f1(golden, _load_plan(Path(args.extraction))) if args.extraction else None
    extra = json.loads(Path(args.extra).read_text(encoding="utf-8")) if args.extra else None
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_md(pf, va, ex, extra), encoding="utf-8")
    print(f"wrote {out}: preflight P={pf['precision']:.3f} R={pf['recall']:.3f}, verdict acc={va['accuracy']:.3f}")


if __name__ == "__main__":
    main()
