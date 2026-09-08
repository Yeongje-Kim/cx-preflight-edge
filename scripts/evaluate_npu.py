"""Evaluate independent supported procedures and grounded report checks on a local LLM."""
from __future__ import annotations
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cxpe.llm.client import GenieXBackend
from cxpe.llm.extract import extract_plan
from cxpe.llm.report import build_facts, draft_report_ko, guard_action
from cxpe.schemas import TestPlan
from cxpe.cli import load_plan
from cxpe.engine import run_stream
from cxpe.synth import make_case


class RecordingClient:
    name = "geniex-18181"
    def __init__(self, client):
        self.client = client
        self.calls = []
    def complete(self, messages, max_tokens=256, json_mode=False):
        started = time.perf_counter()
        raw = self.client.complete(messages, max_tokens=max_tokens, json_mode=json_mode)
        self.calls.append({"messages": messages, "response": raw, "sec": time.perf_counter()-started})
        return raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--base", default="http://127.0.0.1:18181")
    ap.add_argument("--rounds", type=int, default=1)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    backend = GenieXBackend(args.base)
    assert backend.alive(), "Local model is unavailable"
    corpus = json.loads((ROOT/"data/evaluation/procedures.json").read_text(encoding="utf-8"))
    result = {"started_utc": datetime.now(timezone.utc).isoformat(), "model": backend.model,
              "comparison_scope": "Source conditions, timing, state change, title, action and recovery; engine wait budgets are explicit defaults, not extracted fields",
              "procedures": [], "reports": [], "success": False}
    def save():
        (args.out/"evaluation.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    for repetition in range(args.rounds):
        for case in corpus:
            name = f"{case['id']}-{repetition+1}"
            client = RecordingClient(backend)
            entry = {"id": name, "success": False}
            try:
                plan, stats = extract_plan(case["markdown"],client,golden=None)
                expected = TestPlan.model_validate(case["expected"])
                actual_steps = [s.model_dump(mode="json",exclude={"source_text", "precond_wait_sec", "trigger_wait_sec"}) for s in plan.steps]
                expected_steps = [s.model_dump(mode="json",exclude={"source_text", "precond_wait_sec", "trigger_wait_sec"}) for s in expected.steps]
                mismatches = [{"id":actual.get("id"),"field":key,"expected":wanted.get(key),"actual":actual.get(key)}
                              for actual,wanted in zip(actual_steps,expected_steps)
                              for key in wanted if actual.get(key)!=wanted.get(key)]
                entry.update(stats=stats,mismatches=mismatches,success=actual_steps==expected_steps)
                (args.out/f"{name}-plan.json").write_text(plan.model_dump_json(indent=2),encoding="utf-8")
            except Exception as error:
                entry["error"] = str(error)
            (args.out/f"{name}-calls.json").write_text(json.dumps(client.calls,ensure_ascii=False,indent=2),encoding="utf-8")
            result["procedures"].append(entry);save()
            print(json.dumps({"procedure":name,"success":entry["success"],"error":entry.get("error"),
                              "mismatches":entry.get("mismatches"),"steps":entry.get("stats",{}).get("n_ok")},ensure_ascii=False),flush=True)
    for case in ["pass","fail_start","fail_temp","fail_dropout","no_start","abort","short_stream"]:
        rows,_,_=make_case(case if case not in {"abort","short_stream"} else "pass")
        if case=="abort":
            for row in rows:
                if row["t_sec"]>=25:row["CHWS_T_SUP"]=20
        if case=="short_stream":rows=rows[:3]
        plan=load_plan();engine=run_stream(plan,rows);facts=build_facts(plan,engine.verdicts,engine.segments)
        client=RecordingClient(backend)
        remarks=draft_report_ko(facts,client)
        expected_overall={"pass":"PASS","fail_start":"FAIL","fail_temp":"FAIL","fail_dropout":"HOLD","no_start":"FAIL","abort":"ABORT","short_stream":"HOLD"}[case]
        entry={"case":case,"overall":facts["overall"],"expected_overall":expected_overall,"remarks":remarks,
               "success":facts["overall"]==expected_overall and remarks["source"]==client.name and bool(remarks["actions_ko"]) and all(guard_action(a,facts) for a in remarks["actions_ko"])}
        result["reports"].append(entry)
        (args.out/f"report-{case}-calls.json").write_text(json.dumps(client.calls,ensure_ascii=False,indent=2),encoding="utf-8")
        save();print(json.dumps({"report":case,"overall":facts["overall"],"source":remarks["source"],
                               "selection":remarks["selection"]},ensure_ascii=False),flush=True)
    result["success"]=all(e["success"] for e in result["procedures"]+result["reports"])
    result["finished_utc"]=datetime.now(timezone.utc).isoformat();save()
    return 0 if result["success"] else 1


if __name__=="__main__":raise SystemExit(main())
