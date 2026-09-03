"""Preflight: 시험절차서(TestPlan)를 실행 전에 결정적으로 검사한다. 6개 코드.

PF01 changes_state인데 사전조건이 없다
PF02 사전조건이 앞 단계 결과와 모순되거나, 뒤 단계에서만 성립한다
PF03 기대 결과가 없는 단계
PF04 조건의 태그가 태그리스트(또는 alias)에 없다
PF05 상태를 바꾸는 단계에 복구(rollback) 절차가 없다
PF06 같은 float 태그에 서로소인 기준값을 요구하는 단계 쌍

LLM 제안은 여기 들어오지 않는다. 이 모듈의 결과만이 "rule" 근거다.
"""
from __future__ import annotations

import math
from typing import Optional

from .schemas import Cond, Finding, Op, PreflightCode, Severity, Step, TagList, TestPlan

Interval = tuple[float, float, bool, bool]  # lo, hi, lo_closed, hi_closed


def interval_of(c: Cond) -> Optional[Interval]:
    """조건이 허용하는 값의 구간. NE는 구간으로 표현하지 않는다(None)."""
    inf = math.inf
    if c.op == Op.IN_BAND:
        lo, hi = c.band  # type: ignore[misc]
        return (lo, hi, True, True)
    v = float(c.value)  # type: ignore[arg-type]
    table: dict[Op, Optional[Interval]] = {
        Op.EQ: (v, v, True, True),
        Op.LT: (-inf, v, False, False),
        Op.LE: (-inf, v, False, True),
        Op.GT: (v, inf, False, False),
        Op.GE: (v, inf, True, False),
        Op.NE: None,
    }
    return table[c.op]


def disjoint(a: Interval, b: Interval) -> bool:
    alo, ahi, alc, ahc = a
    blo, bhi, blc, bhc = b
    if ahi < blo or bhi < alo:
        return True
    if ahi == blo and not (ahc and blc):
        return True
    if bhi == alo and not (bhc and alc):
        return True
    return False


def contains(outer: Interval, inner: Interval) -> bool:
    olo, ohi, olc, ohc = outer
    ilo, ihi, ilc, ihc = inner
    lo_ok = olo < ilo or (olo == ilo and (olc or not ilc))
    hi_ok = ihi < ohi or (ihi == ohi and (ohc or not ihc))
    return lo_ok and hi_ok


def implies(a: Cond, b: Cond) -> Optional[bool]:
    """a가 참이면 b도 참인가. 판단 불가(NE 등)는 None."""
    ia, ib = interval_of(a), interval_of(b)
    if ia is None or ib is None:
        return None
    return contains(ib, ia)


def contradicts(a: Cond, b: Cond) -> Optional[bool]:
    ia, ib = interval_of(a), interval_of(b)
    if ia is None or ib is None:
        return None
    return disjoint(ia, ib)


def _assertions(step: Step) -> list[Cond]:
    """단계가 끝났을 때 참이 되는 사실: 기대 결과와 트리거."""
    out: list[Cond] = list(step.expected)
    if step.trigger is not None:
        out.append(step.trigger)
    return out


# ------------------------------------------------------------------ checks

def check_pf01(plan: TestPlan) -> list[Finding]:
    out = []
    for s in plan.steps:
        if s.changes_state and not s.preconditions:
            out.append(Finding(
                code=PreflightCode.PF01_MISSING_PRECONDITION, severity=Severity.ERROR, step_id=s.id,
                message=f"{s.id} {s.title}: 설비 상태를 바꾸는 단계인데 사전조건이 없다",
                evidence={"changes_state": True},
            ))
    return out


def check_pf02(plan: TestPlan) -> list[Finding]:
    out = []
    steps = plan.steps
    for i, s in enumerate(steps):
        for p in s.preconditions:
            first: Optional[tuple[int, Cond]] = None
            for j, o in enumerate(steps):
                for a in _assertions(o):
                    if a.tag == p.tag:
                        first = (j, a)
                        break
                if first is not None:
                    break
            known: Optional[tuple[int, Cond]] = None
            for j in range(i):
                for a in _assertions(steps[j]):
                    if a.tag == p.tag:
                        known = (j, a)
            if known is not None and contradicts(known[1], p):
                later = next(
                    ((j, a) for j in range(i + 1, len(steps)) for a in _assertions(steps[j])
                     if a.tag == p.tag and implies(a, p)),
                    None,
                )
                if later is not None:
                    msg = (f"{s.id} 사전조건 '{p.threshold_text()}'은 {steps[known[0]].id} 결과"
                           f"('{known[1].threshold_text()}')와 모순되고 {steps[later[0]].id}에서만 성립한다")
                else:
                    msg = (f"{s.id} 사전조건 '{p.threshold_text()}'은 {steps[known[0]].id} 결과"
                           f"('{known[1].threshold_text()}')와 모순된다")
                out.append(Finding(
                    code=PreflightCode.PF02_ORDER_CONTRADICTION, severity=Severity.ERROR, step_id=s.id,
                    message=msg,
                    evidence={"precondition": p.threshold_text(), "known_from": steps[known[0]].id,
                              "established_in": steps[later[0]].id if later else None},
                ))
            elif known is None and first is not None and first[0] > i and implies(first[1], p):
                out.append(Finding(
                    code=PreflightCode.PF02_ORDER_CONTRADICTION, severity=Severity.ERROR, step_id=s.id,
                    message=(f"{s.id} 사전조건 '{p.threshold_text()}'은 뒤 단계 {steps[first[0]].id}에서만"
                             f" 성립한다 (단계 순서 확인 필요)"),
                    evidence={"precondition": p.threshold_text(), "established_in": steps[first[0]].id},
                ))
    return out


def check_pf03(plan: TestPlan) -> list[Finding]:
    return [
        Finding(code=PreflightCode.PF03_NO_EXPECTED_RESULT, severity=Severity.ERROR, step_id=s.id,
                message=f"{s.id} {s.title}: 기대 결과가 정의되지 않아 판정할 수 없다")
        for s in plan.steps if not s.expected
    ]


def check_pf04(plan: TestPlan, tags: TagList) -> list[Finding]:
    names = tags.names()
    out = []
    for s in plan.steps:
        for t in sorted(s.tags()):
            resolved = plan.tag_aliases.get(t, t)
            if resolved not in names:
                out.append(Finding(
                    code=PreflightCode.PF04_TAG_UNMAPPED, severity=Severity.ERROR, step_id=s.id,
                    message=f"{s.id}: 태그 '{t}'가 BMS 태그리스트에 없다 (매핑 필요)",
                    evidence={"tag": t},
                ))
    return out


def check_pf05(plan: TestPlan) -> list[Finding]:
    return [
        Finding(code=PreflightCode.PF05_MISSING_ROLLBACK, severity=Severity.WARN, step_id=s.id,
                message=f"{s.id} {s.title}: 상태를 바꾸는 단계인데 복구(rollback) 절차가 없다")
        for s in plan.steps if s.changes_state and not s.rollback
    ]


def check_pf06(plan: TestPlan, tags: TagList) -> list[Finding]:
    float_tags = {t.tag for t in tags.tags if t.kind == "float"}
    out = []
    seen: set[tuple[str, str, str]] = set()
    for i, a in enumerate(plan.steps):
        for ea in a.expected:
            tag = plan.tag_aliases.get(ea.tag, ea.tag)
            if tag not in float_tags:
                continue
            for b in plan.steps[i + 1:]:
                for eb in b.expected:
                    if plan.tag_aliases.get(eb.tag, eb.tag) != tag:
                        continue
                    key = (tag, a.id, b.id)
                    if key in seen:
                        continue
                    if contradicts(ea, eb):
                        seen.add(key)
                        out.append(Finding(
                            code=PreflightCode.PF06_THRESHOLD_CONFLICT, severity=Severity.WARN, step_id=b.id,
                            message=(f"{a.id}와 {b.id}가 같은 계측값 {tag}에 서로 겹치지 않는 기준"
                                     f"('{ea.threshold_text()}' vs '{eb.threshold_text()}')을 요구한다"),
                            evidence={"tag": tag, "a": a.id, "b": b.id},
                        ))
    return out


def run_preflight(plan: TestPlan, tags: TagList) -> list[Finding]:
    findings: list[Finding] = []
    findings += check_pf01(plan)
    findings += check_pf02(plan)
    findings += check_pf03(plan)
    findings += check_pf04(plan, tags)
    findings += check_pf05(plan)
    findings += check_pf06(plan, tags)
    order = {s.id: i for i, s in enumerate(plan.steps)}
    findings.sort(key=lambda f: (order.get(f.step_id or "", 999), f.code.value))
    return findings
