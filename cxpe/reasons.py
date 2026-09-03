"""사유 코드: 닫힌 enum + 근거 대조 + 결정적 한국어 렌더링.

LLM은 한국어를 쓰지 않는다. 엔진이 계산한 코드와 근거(Evidence)만 문장으로 바꾼다.
LLM이 보고서에서 코드를 언급하면 corroborate()로 실제 발생한 코드만 남긴다.
"""
from __future__ import annotations

from typing import Iterable

from .schemas import Evidence, ReasonCode

SEVERITY: dict[ReasonCode, int] = {
    ReasonCode.ABORT_CONDITION_HIT: 0,
    ReasonCode.TAG_MISSING_DATA: 1,
    ReasonCode.PRECONDITION_NOT_MET: 2,
    ReasonCode.TRIGGER_NOT_OBSERVED: 3,
    ReasonCode.STANDBY_START_TIMEOUT: 4,
    ReasonCode.TEMP_RECOVERY_TIMEOUT: 4,
    ReasonCode.STEP_TIMEOUT: 5,
    ReasonCode.SKIPPED_AFTER_FAIL: 8,
    ReasonCode.EXPECTED_MET: 9,
}


def timeout_code_for(tag: str) -> ReasonCode:
    """허용시간 초과의 세부 코드는 기대 결과의 태그로 정한다."""
    t = tag.upper()
    if t.endswith("_STATUS") and (t.startswith("CH") or t.startswith("CHWP")):
        return ReasonCode.STANDBY_START_TIMEOUT
    if "_T_" in t or t.endswith("_TEMP") or t.startswith("TT"):
        return ReasonCode.TEMP_RECOVERY_TIMEOUT
    return ReasonCode.STEP_TIMEOUT


def _num(v, nd=1) -> str:
    if v is None:
        return "-"
    return f"{v:.{nd}f}"


def render_ko(code: ReasonCode, ev: Evidence) -> str:
    thr = ev.threshold or (ev.tag or "")
    within = ev.extra.get("within_sec")
    hold = ev.extra.get("hold_sec")
    met_t = ev.extra.get("met_t")
    if code == ReasonCode.EXPECTED_MET:
        hold_txt = f", {_num(hold, 0)}초 유지" if hold else ""
        return f"기대 결과 충족: {thr} ({_num(ev.elapsed_sec)}초 소요{hold_txt})"
    if code in (ReasonCode.STANDBY_START_TIMEOUT, ReasonCode.TEMP_RECOVERY_TIMEOUT, ReasonCode.STEP_TIMEOUT):
        head = {
            ReasonCode.STANDBY_START_TIMEOUT: "대기기 기동 지연",
            ReasonCode.TEMP_RECOVERY_TIMEOUT: "온도 회복 지연",
            ReasonCode.STEP_TIMEOUT: "허용시간 초과",
        }[code]
        actual = f"실제 성립 {_num(met_t)}초" if met_t is not None else "종료 시까지 미성립"
        return (f"{head}: {thr} 조건이 허용시간 {_num(within, 0)}초 내 성립하지 않음"
                f" (마감 t={_num(ev.deadline)}초, {actual}, 마지막 값 {_num(ev.value, 2)})")
    if code == ReasonCode.PRECONDITION_NOT_MET:
        return (f"사전조건 미충족: {thr} ({_num(ev.elapsed_sec, 0)}초 대기, 마지막 값 {_num(ev.value, 2)})")
    if code == ReasonCode.TRIGGER_NOT_OBSERVED:
        return f"트리거 미관측: {thr} ({_num(ev.elapsed_sec, 0)}초 대기)"
    if code == ReasonCode.ABORT_CONDITION_HIT:
        return f"중단 조건 발생: {thr} (t={_num(ev.t)}초, 값 {_num(ev.value, 2)})"
    if code == ReasonCode.TAG_MISSING_DATA:
        return f"계측 결측: {ev.tag} 값이 {_num(ev.elapsed_sec, 0)}초 이상 수신되지 않음 (t={_num(ev.t)}초)"
    if code == ReasonCode.SKIPPED_AFTER_FAIL:
        return "선행 단계 실패로 미실행"
    return code.value


def corroborate(proposed: Iterable[str], actual: Iterable[ReasonCode], max_codes: int = 2) -> list[ReasonCode]:
    """LLM이 제안한 코드 중 실제 발생한 코드만, 심각도 순으로 최대 max_codes개."""
    actual_set = set(actual)
    kept: list[ReasonCode] = []
    for p in proposed:
        try:
            c = ReasonCode(p.strip().upper())
        except ValueError:
            continue
        if c in actual_set and c not in kept:
            kept.append(c)
    if not kept:
        kept = sorted(actual_set, key=lambda c: SEVERITY[c])
    kept.sort(key=lambda c: SEVERITY[c])
    return kept[:max_codes]
