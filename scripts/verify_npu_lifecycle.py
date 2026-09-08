
"""Run the submission lifecycle against an actual board LLM service."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from datetime import datetime, timezone


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-pid", type=int, required=True)
    ap.add_argument("--require-offline", action="store_true")
    ap.add_argument("--base", default="http://127.0.0.1:8089")
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    result = {"started_utc": datetime.now(timezone.utc).isoformat(), "trials": [], "success": False}
    def save():
        (args.out / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    def request(path, body=None, method=None):
        payload = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(args.base + "/api/v1" + path, data=payload,
                                     headers={"Content-Type": "application/json"}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                raw = response.read().decode()
                return response.status, json.loads(raw) if response.headers.get_content_type() == "application/json" else raw
        except urllib.error.HTTPError as error:
            raw = error.read().decode()
            try: raw = json.loads(raw)
            except ValueError: pass
            return error.code, raw
    def check(code, expected=200):
        assert code == expected, f"HTTP {code}, expected {expected}"
    def wait(sid, target, timeout=600):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            code, data = request(f"/sessions/{sid}")
            check(code)
            state = data["meta"]["summary"].get("status")
            if state == "error":
                raise AssertionError(data["meta"]["summary"])
            if state == target:
                return data
            time.sleep(1)
        raise TimeoutError(f"{sid} did not reach {target}")
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        mismatches = [name for name, digest in manifest["files"].items()
                      if hashlib.sha256((args.root / name).read_bytes()).hexdigest() != digest]
        assert not mismatches, mismatches
        result["code_commit"] = manifest["commit"]
        result["verified_file_count"] = len(manifest["files"])
        result["environment"] = {"machine": platform.machine(), "python": platform.python_version(),
                                 "platform": platform.platform(), "cpu_count": os.cpu_count()}
        pid = args.runtime_pid
        maps = Path(f"/proc/{pid}/maps").read_text(encoding="utf-8")
        libs = sorted({line.split("/")[-1] for line in maps.splitlines() if "libQnn" in line or "libGenie" in line})
        devices = sorted({os.readlink(p) for p in Path(f"/proc/{pid}/fd").iterdir()
                          if p.exists() and "/dev/fastrpc" in os.readlink(p)})
        assert any("Htp" in name for name in libs), "QNN HTP backend not observed"
        assert devices, "FastRPC NPU device not observed"
        result["npu_runtime"] = {"pid": pid, "libraries": libs, "devices": devices}
        code, status = request("/status"); check(code)
        assert status["llm"]["backend"] == "geniex-18181" and status["llm"]["alive"], status["llm"]
        result["llm"] = status["llm"]
        result["offline_start"] = status["offline"]
        if args.require_offline:
            assert status["offline"]["is_offline"] and status["offline"]["no_external_iface"]
            assert os.readlink(f"/proc/{pid}/ns/net") == os.readlink("/proc/self/ns/net")
        result["network_namespace"] = os.readlink(f"/proc/{pid}/ns/net")
        save()
        for case, expected in (("pass", "PASS"), ("fail_start", "FAIL"), ("fail_dropout", "HOLD")):
            trial = {"case": case, "expected": expected}
            result["trials"].append(trial)
            code, created = request("/sessions", {"case": case, "mode": "llm"}); check(code)
            sid = created["id"]; trial["session"] = sid
            print(f"START {case} {sid}", flush=True); save()
            data = wait(sid, "ready")
            trial["backend"] = data["meta"]["backend"]
            trial["extraction"] = data.get("extract_stats", {})
            assert trial["backend"].startswith("geniex-18181"), trial["backend"]
            stats = trial["extraction"]
            assert stats["n_ok"] == stats["n_total"] == 8
            trial["all_conditions_from_ai"] = stats["n_source_verified"] == 8 and not stats["fallback_ids"]
            assert trial["all_conditions_from_ai"], stats
            assert data["preflight"]["n_error"] == 0, data["preflight"]
            print(f"EXTRACTED {case} verified={stats['n_source_verified']}/8 fallback={stats['fallback_ids']}", flush=True)
            code, _ = request(f"/sessions/{sid}/approve", {"approver": "board-recheck"}); check(code)
            code, _ = request(f"/sessions/{sid}/run", {"speed": 0, "seed": 0}); check(code)
            finished = wait(sid, "finished")
            trial["overall"] = finished["meta"]["summary"]["overall"]
            trial["timings"] = finished["meta"]["timings"]
            trial["report_source"] = finished["remarks"]["source"]
            assert trial["overall"] == expected, trial
            assert trial["report_source"] == "geniex-18181", finished["remarks"]
            assert finished["remarks"]["action_mode"] == "catalog-selection-v1"
            trial["action_ids"] = finished["remarks"]["action_ids"]
            trial["selection"] = finished["remarks"]["selection"]
            assert finished["has_run"] and finished["has_report"]
            assert finished["executed"] == finished["approved"]
            expected_states = {step: row["state"] for step, row in finished["labels"]["steps"].items()}
            assert {v["step_id"]: v["state"] for v in finished["verdicts"]} == expected_states
            session_path = args.root / "sessions" / sid
            sys.path.insert(0, str(args.root))
            from cxpe.llm.report import guard_action, _template_summary
            facts = json.loads((session_path / "facts.json").read_text(encoding="utf-8"))
            assert finished["remarks"]["summary_source"] == "engine-template"
            assert finished["remarks"]["summary_ko"] == _template_summary(facts)
            assert all(guard_action(action, facts) for action in finished["remarks"]["actions_ko"])
            trial["report_review"] = {"summary_source": "engine-template", "actions_guard_passed": True,
                                      "actions_ko": finished["remarks"]["actions_ko"]}
            protected = ("rules.executed.json", "rules.approved.json", "facts.json", "verdicts.jsonl", "telemetry.csv", "report.md")
            before = {name: hashlib.sha256((session_path / name).read_bytes()).hexdigest() for name in protected}
            altered = json.loads(json.dumps(finished["approved"]))
            altered["steps"][0]["title"] = "Reapproval must not alter history"
            trial["blocked"] = {}
            for operation, body, label in (
                ("run", {"speed": 0, "telemetry": "fail_temp"}, "rerun"),
                ("approve", {"plan": altered, "approver": "replacement"}, "reapprove"),
                ("approve", {"plan": altered, "force": True}, "force_reapprove"),
            ):
                code, _ = request(f"/sessions/{sid}/{operation}", body)
                check(code, 409); trial["blocked"][label] = code
            assert before == {name: hashlib.sha256((session_path / name).read_bytes()).hexdigest() for name in protected}
            trial["history_unchanged_after_rejected_requests"] = True
            code, stream = request(f"/sessions/{sid}/stream"); check(code)
            events = [json.loads(line[5:]) for line in stream.splitlines() if line.startswith("data:")]
            types = [e["type"] for e in events]
            assert types.count("run_start") == 1 and types.count("done") == 1 and "verdict" in types and "report" in types
            assert types[-1] == "done"
            trial["sse"] = {"run_start": types.count("run_start"), "verdict": types.count("verdict"),
                            "report": types.count("report"), "done": types.count("done")}
            code, report = request(f"/sessions/{sid}/report"); check(code)
            assert f"종합 판정: {expected}" in report
            target = args.out / case; target.mkdir(exist_ok=True)
            for name in ("plan.json", "preflight.json", "rules.executed.json", "facts.json", "verdicts.jsonl", "report.md", "extract_stats.json", "meta.json"):
                (target / name).write_bytes((session_path / name).read_bytes())
            original_facts = (session_path / "facts.json").read_bytes()
            code, regen = request(f"/sessions/{sid}/report", {}, "POST"); check(code)
            assert (session_path / "facts.json").read_bytes() == original_facts
            trial["regenerated_report_kept_facts"] = True
            trial["regenerated_report_source"] = regen["source"]
            trial["success"] = True
            save(); print(f"DONE {case} {expected} report={trial['report_source']} history=protected", flush=True)
        assert len({t["session"] for t in result["trials"]}) == len(result["trials"])
        result["new_trial_isolation"] = True
        code, status = request("/status"); check(code)
        result["offline_end"] = status["offline"]
        if args.require_offline:
            assert status["offline"]["is_offline"] and status["offline"]["no_external_iface"]
        result["success"] = True
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        result["finished_utc"] = datetime.now(timezone.utc).isoformat()
        save()
    print(json.dumps({"success": result["success"], "commit": result["code_commit"],
                      "trials": [{k: t[k] for k in ("case", "overall", "all_conditions_from_ai", "report_source")} for t in result["trials"]]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

