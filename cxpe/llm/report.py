"""시험 기록 초안. 엔진이 판정과 근거를 기록하고 AI가 근거별 점검 항목을 선택한다."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Optional

from ..reasons import corroborate
from ..schemas import ReasonCode, Segment, StepState, TestPlan, Verdict
from .client import LlmClient
from .actions import action_candidates, report_actions, selected_actions
from .extract import parse_json_loose
from .prompts import report_messages

_NUM = re.compile(r"\d+(?:\.\d+)?")


def build_facts(plan: TestPlan, verdicts: list[Verdict], segments: list[Segment], meta: Optional[dict] = None) -> dict:
    steps = []
    for v in verdicts:
        s = plan.step(v.step_id)
        steps.append({
            "id": v.step_id, "title": s.title, "state": v.state.value,
            "reason_codes": [c.value for c in v.reason_codes], "reason_ko": v.reason_ko,
            "t_start": v.t_start, "t_end": v.t_end,
            "evidence": {k: val for k, val in v.evidence.model_dump().items() if val not in (None, {}, "")},
        })
    states = [v.state for v in verdicts]
    overall = "PASS"
    if StepState.ABORT in states:
        overall = "ABORT"
    elif StepState.FAIL in states:
        overall = "FAIL"
    elif StepState.HOLD in states:
        overall = "HOLD"
    exceeded = [{"step_id": g.step_id, "t0": g.t0, "t1": g.t1} for g in segments if g.kind == "exceeded"]
    return {
        "plan_id": plan.plan_id, "title": plan.title, "overall": overall,
        "n_pass": sum(1 for s in states if s == StepState.PASS),
        "n_fail": sum(1 for s in states if s == StepState.FAIL),
        "n_abort": sum(1 for s in states if s == StepState.ABORT),
        "n_hold": sum(1 for s in states if s == StepState.HOLD),
        "n_skipped": sum(1 for s in states if s == StepState.SKIPPED),
        "steps": steps, "exceeded": exceeded, "meta": meta or {},
    }


def _numbers(text: str) -> set[str]:
    return {m.group(0).rstrip("0").rstrip(".") if "." in m.group(0) else m.group(0) for m in _NUM.finditer(text)}


def _fact_numbers(facts: dict) -> set[str]:
    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                yield from walk(v)
        elif isinstance(x, list):
            for v in x:
                yield from walk(v)
        elif isinstance(x, (int, float)) and not isinstance(x, bool):
            s = f"{x:g}"
            yield s
            if isinstance(x, float):
                yield f"{x:.1f}".rstrip("0").rstrip(".")
                yield f"{x:.0f}"
        elif isinstance(x, str):
            for n in _numbers(x):
                yield n
    return set(walk(facts))


def guard_numbers(text: str, facts: dict) -> bool:
    """문단의 숫자가 전부 FACTS에 있으면 True."""
    allowed = _fact_numbers(facts) | {"1", "2", "3"}
    return all(n in allowed for n in _numbers(text))


def guard_action(text: str, facts: dict) -> bool:
    """Only catalog text grounded in these facts may appear as a recommendation."""
    return any(text == c["text"] for c in action_candidates(facts))


def draft_report_ko(facts: dict, client: Optional[LlmClient]) -> dict:
    """The model selects supported check IDs; no generated prose is published."""
    actual = [ReasonCode(c) for step in facts["steps"] for c in step["reason_codes"]]
    defaults = action_candidates(facts)[:2]
    out = {
        "summary_ko": _template_summary(facts), "summary_source": "engine-template",
        "actions_ko": [c["text"] for c in defaults], "action_ids": [c["id"] for c in defaults],
        "action_checks": defaults, "action_mode": "catalog-selection-v1",
        "reason_codes": [c.value for c in corroborate([], actual)],
        "source": "template", "sec": 0.0,
        "selection": {"attempts": 0, "accepted": 0, "rejected": 0, "errors": []},
    }
    if client is None:
        return out
    started = time.perf_counter()
    messages = report_messages(facts)
    for attempt in range(2):
        out["selection"]["attempts"] += 1
        try:
            raw = client.complete(messages, max_tokens=180, json_mode=True)
            data = parse_json_loose(raw)
            chosen, rejected = selected_actions(facts, data.get("action_ids"))
            out["selection"]["rejected"] += rejected
            if chosen:
                out.update(actions_ko=[c["text"] for c in chosen], action_ids=[c["id"] for c in chosen],
                           action_checks=chosen, source=getattr(client, "name", "llm"))
                out["selection"]["accepted"] = len(chosen)
                break
            out["source"] = "template(selection-guard)"
            out["selection"]["errors"].append("no_valid_action_id")
        except Exception as error:
            out["source"] = "template(selection-error)"
            out["selection"]["errors"].append(type(error).__name__)
        if attempt == 0:
            messages = messages + [{"role": "user", "content":
                'Select only IDs from CANDIDATES. Return {"action_ids":["exact candidate ID"]}. No other fields.'}]
    out["sec"] = time.perf_counter() - started
    return out


def _template_summary(facts: dict) -> str:
    n = len(facts["steps"])
    if facts["overall"] == "PASS":
        return f"총 {n}개 단계가 모두 승인 기준 내에서 성립하여 시험 결과는 합격이다."
    if facts["overall"] == "HOLD":
        held = [s for s in facts["steps"] if s["state"] == "HOLD"]
        h = held[0] if held else None
        if h is None:
            return "판정 결과를 확인할 수 없다."
        return (f"{h['id']} {h['title']} 단계는 판정에 필요한 계측값이 없어 시스템이 판정하지 않았다(보류). "
                f"보류는 설비 불합격이 아니다. 계측 경로를 확인한 뒤 새 세션에서 재시험하거나 "
                f"엔지니어가 근거를 직접 확인해 판정해야 한다. 이후 {facts['n_skipped']}개 단계는 실행하지 않았다.")
    bad = [s for s in facts["steps"] if s["state"] in ("FAIL", "ABORT")]
    first = bad[0] if bad else None
    if first is None:
        return "판정 결과를 확인할 수 없다."
    return (f"시험 결과는 {'중단' if facts['overall'] == 'ABORT' else '불합격'}이다. "
            f"{first['id']} {first['title']} 단계의 사유: {first['reason_ko']}. "
            f"이후 {facts['n_skipped']}개 단계는 실행하지 않았다.")


def _template_actions(facts: dict) -> list[str]:
    return [c["text"] for c in action_candidates(facts)[:2]]


def render_report_md(facts: dict, remarks: dict, meta: Optional[dict] = None) -> str:
    meta = meta or {}
    lines = [
        f"# 통합시운전 시험 기록 초안 — {facts['plan_id']}",
        "",
        f"- 시험명: {facts['title']}",
        f"- 작성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 세션: {meta.get('id', '-')}  · 케이스: {meta.get('case', '-')}  · 데이터: 합성 SIL(기능검증용, 임의 기준값)",
        f"- 판정 엔진: 규칙 엔진(승인 규칙)  · 판정 요약: 엔진 템플릿  · 조치 항목 선택: {remarks.get('source', '-')}  · 조치 문장: 근거별 고정 문안  · 인터페이스 송신 증가: {meta.get('uplink', '미측정')}",
        f"- **종합 판정: {facts['overall']}** (PASS {facts['n_pass']} / FAIL {facts['n_fail']} / "
        f"ABORT {facts['n_abort']} / HOLD {facts['n_hold']} / SKIPPED {facts['n_skipped']})",
        "- 보류(HOLD)는 판정에 필요한 값이 없어 시스템이 판정하지 않은 단계다. 설비 불합격을 뜻하지 않는다.",
        "",
        "## 단계별 판정",
        "",
        "| 단계 | 제목 | 판정 | 시작(s) | 종료(s) | 사유 |",
        "|---|---|---|---|---|---|",
    ]
    for s in facts["steps"]:
        ts = "-" if s["t_start"] is None else f"{s['t_start']:.0f}"
        te = "-" if s["t_end"] is None else f"{s['t_end']:.0f}"
        lines.append(f"| {s['id']} | {s['title']} | {s['state']} | {ts} | {te} | {s['reason_ko']} |")
    if facts["exceeded"]:
        lines += ["", "## 허용시간 초과 구간", ""]
        for g in facts["exceeded"]:
            t1 = "미성립" if g["t1"] is None else f"{g['t1']:.0f}s"
            lines.append(f"- {g['step_id']}: 마감 {g['t0']:.0f}s → 실제 성립 {t1}")
    lines += ["", "## 종합 의견", "", _template_summary(facts), "", "## 조치 권고", ""]
    lines += [f"- {a}" for a in [c["text"] for c in report_actions(facts, remarks)]]
    lines += ["", "## 사유 코드", "", ", ".join(sorted({code for step in facts["steps"] for code in step["reason_codes"]})) or "-", ""]
    lines += ["> 본 기록의 수치·기준은 기능검증용 임의 예시이며 실제 적용 시 프로젝트별 승인 시험계획서의 기준을 사용한다."]
    return "\n".join(lines) + "\n"
