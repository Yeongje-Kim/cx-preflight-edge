"""Conservative source checks for the tagged, single-line procedure format.

This is not a general Korean document parser. Unsupported or ambiguous numeric
clauses fail closed; no values are inferred from a golden plan or an LLM.
"""
from __future__ import annotations
import re
from ..schemas import Step, TestPlan

NUM = r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?"
TAG = re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b")


def line(text: str, label: str) -> str:
    rows = text.splitlines()
    pattern = re.compile(r"^\s*[-*]\s*" + re.escape(label) + r"\s*:\s*(.*)$")
    hits = []
    for i, row in enumerate(rows):
        match = pattern.match(row)
        if not match:
            continue
        pieces = [match[1].strip()]
        for following in rows[i + 1:]:
            if not following.strip() or re.match(r"^\s*[-*#]", following) or not following[:1].isspace():
                break
            pieces.append(following.strip())
        hits.append(" ".join(pieces).strip())
    if len(hits) > 1:
        raise ValueError(f"원문 대조 불가: {label} 중복")
    return hits[0] if hits else ""


def source_narrative(text: str) -> dict:
    heading = re.search(r"^###\s+(S\d+)\s+(.+?)\s*$", text, re.M)
    if not heading:
        raise ValueError("원문 대조 불가: 단계 제목 없음")
    action = line(text, "조치")
    if not action:
        raise ValueError("원문 대조 불가: 조치 없음")
    rollback = line(text, "복구")
    no_rollback = re.fullmatch(r"(?:해당\s*없음|해당사항\s*없음|없음)(?:\s*\([^)]*\))?\.?", rollback)
    return {"id": heading[1], "title": heading[2].strip(), "action": action,
            "rollback": [rollback] if rollback and not no_rollback else []}


def conditions(text: str, timed: bool = False) -> list[dict]:
    if not text:
        return []
    if re.search(r"또는|아니면|\bor\b", text, re.I):
        raise ValueError("원문 대조 불가: 대안 조건은 지원하지 않음")
    if re.search(r"\d,\d|않|아닌|아니면|제외|이외", text):
        raise ValueError("원문 대조 불가: 쉼표 숫자 또는 부정 표현")
    tags = list(TAG.finditer(text))
    if not tags:
        raise ValueError("원문 대조 불가: 명시적인 BMS 태그 필요")
    result = []
    for i, match in enumerate(tags):
        tail = text[match.end(): tags[i+1].start() if i+1 < len(tags) else len(text)]
        tail = tail.lstrip(') ').strip()
        row = {'tag': match.group()}
        comparison = re.match(r'(==|!=|>=|<=|>|<|=)\s*(' + NUM + ')', tail)
        band = re.match(r'(' + NUM + r')\s*~\s*(' + NUM + ')', tail)
        korean = re.match(r'(' + NUM + r')\s*(?:kW|°C)?\s*(이상|이하|초과|미만)', tail)
        expression = comparison or band or korean
        if comparison:
            row.update(op='==' if comparison[1]=='=' else comparison[1], value=float(comparison[2]))
        elif band:
            row.update(op='in_band', band=[float(band[1]), float(band[2])])
        elif korean:
            row.update(op={'이상':'>=', '이하':'<=', '초과':'>', '미만':'<'}[korean[2]], value=float(korean[1]))
        else:
            raise ValueError(f"원문 대조 불가: {match.group()} 조건 표현")
        remainder = tail[expression.end():]
        if timed:
            within = re.findall(r'허용시간\s*(' + NUM + r')\s*초|(' + NUM + r')\s*초\s*이내', tail)
            holds = re.findall(r'(' + NUM + r')\s*초\s*(?:이상\s*)?유지', tail)
            if len(within)!=1 or len(holds)>1:
                raise ValueError(f"원문 대조 불가: {match.group()} 허용시간·유지시간 모호함")
            row.update(within_sec=float(within[0][0] or within[0][1]), hold_sec=float(holds[0]) if holds else 0.0)
            remainder = re.sub(r'허용시간\s*(' + NUM + r')\s*초|(' + NUM + r')\s*초\s*이내', '', remainder)
            remainder = re.sub(r'(' + NUM + r')\s*초\s*(?:이상\s*)?유지', '', remainder)
        # Equipment labels before the following tag are descriptive, e.g. CH-2.
        remainder = re.sub(r'\b[A-Z][A-Z0-9]*-\d+(?:-\d+)*\b', '', remainder)
        if re.search(r'\d|[~<>!=]', remainder):
            raise ValueError(f"원문 대조 불가: {match.group()} 해석하지 못한 수치·연산자")
        result.append(row)
    return result


def source_reference(text: str) -> dict:
    source_narrative(text)
    expected_text = line(text, '기대 결과')
    if not expected_text:
        raise ValueError('원문 대조 불가: 기대 결과 없음')
    source = {
        'expected': conditions(expected_text, timed=True),
        'preconditions': conditions(line(text, '사전조건')),
        'abort_conditions': conditions(line(text, '중단 조건')),
        'trigger': conditions(line(text, '트리거')),
    }
    from ..schemas import Cond, Expected
    for field, references in source.items():
        for row in references:
            (Expected if field == 'expected' else Cond).model_validate(row)
    if len(source['trigger']) > 1:
        raise ValueError('원문 대조 불가: 트리거는 하나만 지원')
    if line(text, '상태 변경') not in ('', '있음', '없음'):
        raise ValueError('원문 대조 불가: 상태 변경 표현')
    return source


def validate_source(step: Step, text: str) -> None:
    # Report related mismatches together so one retry can repair all of them.
    # A condition-count error must not hide wrong timing in the common entries.
    issues = []
    narrative = source_narrative(text)
    for field, original in narrative.items():
        if getattr(step, field) != original:
            issues.append(f"{step.id}.{field}: 원문 문구와 다름")
    if step.source_text and step.source_text.strip() != text.strip():
        issues.append(f"{step.id}.source_text: 원문 보관본과 다름")
    source = source_reference(text)
    for field, reference in source.items():
        actual = ([step.trigger] if step.trigger else []) if field == 'trigger' else getattr(step, field)
        if len(actual) != len(reference):
            issues.append(f'{step.id}.{field}: 원문 조건 {len(reference)}개, 추출 {len(actual)}개')
        for i, (a, r) in enumerate(zip(actual, reference)):
            data = a.model_dump(mode='json')
            for key, value in r.items():
                if data[key] != value:
                    issues.append(f'{step.id}.{field}[{i}].{key}: 원문 값 {value!r}, 추출 값 {data[key]!r}')
    state = line(text, '상태 변경')
    if step.changes_state != (state == '있음'):
        issues.append(f'{step.id}.changes_state: 원문 값 {state == "있음"}')
    if issues:
        raise ValueError("; ".join(issues))


def validate_plan_source(plan: TestPlan, md: str) -> None:
    from .extract import chunk_steps
    chunks = chunk_steps(md)
    if not chunks or [x[0] for x in chunks] != [s.id for s in plan.steps]:
        raise ValueError('원문과 추출 단계 목록·순서 불일치')
    for step, (_, _, text) in zip(plan.steps, chunks):
        validate_source(step, text)
