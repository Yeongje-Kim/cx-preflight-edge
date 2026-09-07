"""프롬프트 4종. 지시는 영어, 본문은 한국어 원문 그대로. 출력은 항상 JSON 객체 하나.

4B 모델(컨텍스트 4096)을 위해 입력을 단계 1개 단위로 자른다. 키 이름은 schemas.py와 동일하게 고정한다.
"""
from __future__ import annotations

import json

STEP_SCHEMA_EXAMPLE = {
    "id": "S3", "title": "CH-1 모의 트립", "action": "CH-1 트립 접점을 모의 입력한다.",
    "preconditions": [{"tag": "CH1_STATUS", "op": "==", "value": 1}],
    "trigger": {"tag": "CH1_TRIP", "op": "==", "value": 1},
    "expected": [{"tag": "CH1_STATUS", "op": "==", "value": 0, "within_sec": 5, "hold_sec": 0},
                 {"tag": "CHWS_T_SUP", "op": "in_band", "band": [6, 8], "within_sec": 60, "hold_sec": 10}],
    "abort_conditions": [{"tag": "CHWS_T_SUP", "op": ">", "value": 14}],
    "rollback": ["트립 접점 복구", "CH-1 재기동"],
    "changes_state": True,
}

EXTRACT_SYSTEM = (
    "You convert ONE step of a Korean data-center commissioning test procedure into a JSON object. "
    "Rules: output ONLY one JSON object, no prose, no markdown fences. Use exactly these keys: "
    "id, title, action, preconditions, trigger, expected, abort_conditions, rollback, changes_state. "
    "Each condition is {\"tag\", \"op\", \"value\"} or {\"tag\", \"op\": \"in_band\", \"band\": [lo, hi]}; "
    "op is one of ==, !=, <, <=, >, >=, in_band. Expected results add within_sec (seconds) and hold_sec "
    "(seconds of continuous hold, 0 if not stated). Every item of expected is ONE object that "
    "contains tag, op, value or band, within_sec and hold_sec together. Never split the timing "
    "fields into a separate object. Use the BMS tag names that appear in the text "
    "(e.g. CH1_STATUS, CHWS_T_SUP). trigger is null when the text has no 트리거. rollback is a list of short "
    "Korean strings from 복구. changes_state is true only when the text says 상태 변경: 있음. "
    "Korean timing: 허용시간 30초 means within_sec=30; 10초 이상 유지 means hold_sec=10. "
    "Do not swap them. 이상 means >= (not >); 이하 means <=; 초과 means >; 미만 means <. "
    "Example: LOAD_KW 200 kW 이상을 10초 이상 유지. 허용시간 30초 => "
    "tag=LOAD_KW, op=>=, value=200, within_sec=30, hold_sec=10. "
    "When 상태 변경: 있음 is absent, changes_state must be false even if 복구 mentions restarting. "
    "Keep Korean text for title/action/rollback. Do not invent values that are not in the text."
)


def extraction_messages(step_text: str) -> list[dict[str, str]]:
    user = (
        "Example output for a different step:\n"
        + json.dumps(STEP_SCHEMA_EXAMPLE, ensure_ascii=False)
        + "\n\nNow convert this step. Output only the JSON object.\n\n"
        + step_text.strip()
    )
    return [{"role": "system", "content": EXTRACT_SYSTEM}, {"role": "user", "content": user}]


MAP_SYSTEM = (
    "You map logical equipment/point names from a Korean test procedure to BMS tags. "
    "Output ONLY one JSON object: {\"mappings\": [{\"name\": str, \"tag\": str|null, \"confidence\": 0.0-1.0}]}. "
    "Use only tags from the provided tag list. If no tag fits, tag=null and confidence=0."
)


def mapping_messages(names: list[str], tag_rows: list[dict]) -> list[dict[str, str]]:
    rows = "\n".join(f"{r['tag']}\t{r.get('equipment','')}\t{r.get('description','')}" for r in tag_rows[:40])
    user = f"Names to map:\n{json.dumps(names, ensure_ascii=False)}\n\nTag list (tag\tequipment\tdescription):\n{rows}"
    return [{"role": "system", "content": MAP_SYSTEM}, {"role": "user", "content": user}]


CONTRA_SYSTEM = (
    "You review a commissioning test plan for logical problems between steps. Look ONLY for: "
    "(a) a precondition of a step that is established only by a later step or contradicts an earlier "
    "step's expected result (kind PF02), (b) two steps requiring non-overlapping thresholds for the same "
    "measurement (kind PF06). Output ONLY one JSON object: "
    "{\"candidates\": [{\"step_id\": str, \"kind\": \"PF02\"|\"PF06\", \"message\": str}]}. "
    "Message in Korean, one sentence. If nothing is found, output {\"candidates\": []}."
)


def contradiction_messages(step_summaries: list[dict]) -> list[dict[str, str]]:
    user = "Steps (in order):\n" + json.dumps(step_summaries, ensure_ascii=False)
    return [{"role": "system", "content": CONTRA_SYSTEM}, {"role": "user", "content": user}]


REPORT_SYSTEM = (
    "You write the closing remarks of a Korean data-center commissioning test record. "
    "You receive FACTS as JSON (step verdicts, reason codes, evidence numbers). "
    "Output ONLY one JSON object: {\"summary_ko\": str, \"actions_ko\": [str], \"reason_codes\": [str]}. "
    "summary_ko: 2-3 sentences in Korean describing the outcome. actions_ko: up to 3 short Korean "
    "recommendations. reason_codes: the codes from FACTS that explain the outcome. "
    "Use ONLY numbers that appear in FACTS. Never decide pass/fail yourself; FACTS already contain it."
)


def report_messages(facts: dict) -> list[dict[str, str]]:
    user = "FACTS:\n" + json.dumps(facts, ensure_ascii=False)
    return [{"role": "system", "content": REPORT_SYSTEM}, {"role": "user", "content": user}]
