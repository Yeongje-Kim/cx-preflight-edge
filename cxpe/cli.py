"""CLI: synth / preflight / replay / extract / serve.

python -m cxpe.cli replay --case fail_start
python -m cxpe.cli extract --llm auto --out sessions/_extract
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engine import run_stream
from .preflight import run_preflight
from .schemas import TagList, TestPlan
from .synth import CASES, make_case
from .telemetry import read_csv, write_csv

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PLAN = ROOT / "data/golden/plan.json"
GOLDEN_TAGS = ROOT / "data/golden/tags.json"
PLAN_MD = ROOT / "data/plan_n1_cooling.md"


def load_plan(path: Path = GOLDEN_PLAN) -> TestPlan:
    return TestPlan.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def load_tags(path: Path = GOLDEN_TAGS) -> TagList:
    return TagList.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def cmd_synth(a: argparse.Namespace) -> None:
    rows, labels, p = make_case(a.case, seed=a.seed)
    out = Path(a.out)
    write_csv(rows, out)
    out.with_suffix(".labels.json").write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{a.case}: {len(rows)} rows -> {out} (labels: {out.with_suffix('.labels.json').name})")


def cmd_preflight(a: argparse.Namespace) -> None:
    plan, tags = load_plan(Path(a.plan)), load_tags(Path(a.tags))
    findings = run_preflight(plan, tags)
    if not findings:
        print("Preflight OK: 결함 없음")
        return
    for f in findings:
        print(f"[{f.severity.value}] {f.code.value} {f.step_id or '-'}: {f.message}")
    sys.exit(2 if any(f.severity.value == "ERROR" for f in findings) else 0)


def cmd_replay(a: argparse.Namespace) -> None:
    plan = load_plan(Path(a.plan))
    if a.csv:
        rows = read_csv(Path(a.csv))
        labels = None
    else:
        rows, labels, _ = make_case(a.case, seed=a.seed)
    eng = run_stream(plan, rows)
    for v in eng.verdicts:
        print(f"{v.step_id:>3} {v.state.value:<7} t={('-' if v.t_end is None else f'{v.t_end:.0f}'):>4}s "
              f"{v.latency_ms:6.3f}ms  {v.reason_ko}")
    print("segments(exceeded):", [(s.step_id, s.t0, s.t1) for s in eng.segments if s.kind == "exceeded"])
    print("overall:", eng.summary()["overall"], "" if labels is None else f"(label: {labels['overall']})")


def cmd_extract(a: argparse.Namespace) -> None:
    from .llm.client import autodetect
    from .llm.extract import extract_plan, find_contradictions, map_tags
    from .metrics import extraction_f1
    client = autodetect(a.llm)
    if client is None:
        print("LLM 백엔드를 찾지 못했다 (CXPE_LLM=geniex|rust, 보드에서 실행)")
        sys.exit(1)
    md = Path(a.md).read_text(encoding="utf-8")
    golden = load_plan()
    plan, stats = extract_plan(md, client, golden=golden if a.golden_fallback else None)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    (out / "extract_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    f1 = extraction_f1(golden, plan)
    (out / "extraction_f1.json").write_text(json.dumps(f1, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"backend={stats['backend']} ok={stats['n_ok']}/{stats['n_total']} fallback={stats['fallback_ids']} "
          f"sec={stats['sec_total']:.1f}  F1={f1['f1']:.3f} (P {f1['precision']:.3f} R {f1['recall']:.3f})")
    if a.suggest:
        tags = load_tags()
        sugg = map_tags(plan, tags, client) + find_contradictions(plan, client)
        (out / "ai_suggestions.json").write_text(json.dumps([s.model_dump(mode="json") for s in sugg],
                                                            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"ai suggestions: {len(sugg)}")


def cmd_serve(a: argparse.Namespace) -> None:
    import uvicorn
    uvicorn.run("cxpe.server:app", host=a.host, port=a.port, reload=False)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="cxpe")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("synth"); s.add_argument("--case", choices=sorted(CASES), default="pass")
    s.add_argument("--seed", type=int, default=0); s.add_argument("--out", default="sessions/_synth.csv")
    s.set_defaults(fn=cmd_synth)

    s = sub.add_parser("preflight"); s.add_argument("--plan", default=str(GOLDEN_PLAN))
    s.add_argument("--tags", default=str(GOLDEN_TAGS)); s.set_defaults(fn=cmd_preflight)

    s = sub.add_parser("replay"); s.add_argument("--plan", default=str(GOLDEN_PLAN))
    s.add_argument("--case", choices=sorted(CASES), default="pass"); s.add_argument("--seed", type=int, default=0)
    s.add_argument("--csv"); s.set_defaults(fn=cmd_replay)

    s = sub.add_parser("extract"); s.add_argument("--md", default=str(PLAN_MD)); s.add_argument("--llm", default="auto")
    s.add_argument("--out", default="sessions/_extract"); s.add_argument("--golden-fallback", action="store_true")
    s.add_argument("--suggest", action="store_true"); s.set_defaults(fn=cmd_extract)

    s = sub.add_parser("serve"); s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8080)
    s.set_defaults(fn=cmd_serve)

    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
