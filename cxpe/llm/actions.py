"""Grounded follow-up checks. The model selects IDs; application text is fixed."""
from __future__ import annotations

CHECKS = {
    "STANDBY_START_TIMEOUT": [
        ("start_sequence", "기동 지령과 운전 상태의 기록을 대조한다."),
        ("interlock_record", "기동 시퀀스와 인터록의 동작 기록을 검토한다."),
    ],
    "TEMP_RECOVERY_TIMEOUT": [
        ("temperature_trend", "공급온도 추이와 운전 상태의 기록을 대조한다."),
        ("flow_record", "냉수 유량과 펌프 운전 기록을 확인한다."),
    ],
    "TAG_MISSING_DATA": [
        ("measurement_path", "계측점의 통신 상태와 수집 경로를 확인한다."),
        ("collection_record", "원본 측정 기록과 수집 로그를 대조해 결측 구간을 확인한다."),
    ],
    "STREAM_ENDED_EARLY": [("collection_window", "승인 기준을 확인하고 시험 종료까지 측정 기록이 확보됐는지 검토한다.")],
    "PRECONDITION_NOT_MET": [("precondition_record", "시험 시작 전 상태 기록을 승인된 사전조건과 대조한다.")],
    "TRIGGER_NOT_OBSERVED": [("trigger_record", "트리거 입력 기록과 해당 계측점의 수신 기록을 확인한다.")],
    "ABORT_CONDITION_HIT": [("abort_record", "중단 당시 측정 기록과 승인된 중단 조건을 대조한다.")],
    "STEP_TIMEOUT": [("expected_record", "측정 기록과 승인된 기대조건을 대조한다.")],
}


def action_candidates(facts: dict) -> list[dict]:
    out = []
    for step in facts["steps"]:
        if step["state"] not in {"FAIL", "ABORT", "HOLD"}:
            continue
        for code in step["reason_codes"]:
            for kind, text in CHECKS.get(code, []):
                out.append({"id": f"{step['id']}.{kind}", "step_id": step["id"],
                            "reason_code": code, "text": f"{step['id']} 단계: {text}"})
        if step["state"] in {"FAIL", "ABORT"}:
            out.append({"id": f"{step['id']}.recovery_review", "step_id": step["id"],
                        "reason_code": step["reason_codes"][0] if step["reason_codes"] else "",
                        "text": f"{step['id']} 단계의 승인된 복구절차를 검토하고 담당 엔지니어와 재시험 조건을 확인한다."})
    if not out:
        out.append({"id": "record.archive", "step_id": None, "reason_code": "EXPECTED_MET",
                    "text": "시험 기록을 승인 절차서와 함께 보관한다."})
        out.append({"id": "record.signoff", "step_id": None, "reason_code": "EXPECTED_MET",
                    "text": "담당 엔지니어가 판정 근거를 검토하고 시험 기록의 최종 승인 여부를 확인한다."})
    return list({c["id"]: c for c in out}.values())


def selected_actions(facts: dict, ids: object) -> tuple[list[dict], int]:
    candidates = {c["id"]: c for c in action_candidates(facts)}
    if not isinstance(ids, list):
        return [], 1
    chosen = []
    seen = set()
    rejected = 0
    for item in ids[:20]:
        if not isinstance(item, str) or item not in candidates:
            rejected += 1
            continue
        if item not in seen and len(chosen) < 3:
            chosen.append(dict(candidates[item])); seen.add(item)
    return chosen, rejected + max(0, len(ids) - 20)


def report_actions(facts: dict, remarks: dict) -> list[dict]:
    """Revalidate stored IDs on render; never display legacy free-form actions."""
    selected, _ = selected_actions(facts, remarks.get("action_ids"))
    return selected or action_candidates(facts)[:2]
