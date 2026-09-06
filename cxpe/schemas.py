"""도메인 스키마 전부. pydantic v2.

설계 원칙
- Pass/Fail은 규칙 엔진(engine.py)만 결정한다. LLM은 이 스키마로 검증된 JSON을 만들 뿐이다.
- 시험절차서 1건 = TestPlan. 단계(Step)마다 사전조건/트리거/기대결과/중단조건/복구절차를 가진다.
- 조건(Cond)은 태그 1개에 대한 비교식이다. evaluate()는 값이 없으면 None(결측)을 돌려준다.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

Sample = dict[str, Optional[float]]  # {"t_sec": 12.0, "CH1_STATUS": 1.0, "CHWS_T_SUP": None, ...}


class Op(str, Enum):
    EQ = "=="
    NE = "!="
    LT = "<"
    LE = "<="
    GT = ">"
    GE = ">="
    IN_BAND = "in_band"


class Cond(BaseModel):
    tag: str
    op: Op
    value: Optional[float] = None
    band: Optional[tuple[float, float]] = None

    @model_validator(mode="after")
    def _check_operands(self) -> "Cond":
        if self.op == Op.IN_BAND:
            if self.band is None:
                raise ValueError("in_band 조건은 band=[lo, hi]가 필요하다")
            lo, hi = self.band
            if lo > hi:
                raise ValueError("band는 lo <= hi 여야 한다")
        elif self.value is None:
            raise ValueError(f"{self.op.value} 조건은 value가 필요하다")
        return self

    def evaluate(self, v: Optional[float]) -> Optional[bool]:
        """값이 없으면 None(결측). 있으면 bool."""
        if v is None:
            return None
        if self.op == Op.IN_BAND:
            lo, hi = self.band  # type: ignore[misc]
            return lo <= v <= hi
        x = self.value  # type: ignore[assignment]
        return {
            Op.EQ: v == x,
            Op.NE: v != x,
            Op.LT: v < x,
            Op.LE: v <= x,
            Op.GT: v > x,
            Op.GE: v >= x,
        }[self.op]

    def threshold_text(self) -> str:
        if self.op == Op.IN_BAND:
            lo, hi = self.band  # type: ignore[misc]
            return f"{self.tag} in [{lo:g}, {hi:g}]"
        return f"{self.tag} {self.op.value} {self.value:g}"


class Expected(Cond):
    """기대 결과: within_sec 안에 성립하기 시작해 hold_sec 동안 연속 유지."""

    within_sec: float = Field(gt=0)
    hold_sec: float = Field(default=0.0, ge=0)


class Step(BaseModel):
    id: str
    title: str
    action: str = ""
    preconditions: list[Cond] = Field(default_factory=list)
    trigger: Optional[Cond] = None
    expected: list[Expected] = Field(default_factory=list)
    abort_conditions: list[Cond] = Field(default_factory=list)
    rollback: list[str] = Field(default_factory=list)
    changes_state: bool = False
    precond_wait_sec: float = Field(default=10.0, ge=0)
    trigger_wait_sec: float = Field(default=120.0, ge=0)
    source_text: str = ""

    def tags(self) -> set[str]:
        out = {c.tag for c in self.preconditions} | {e.tag for e in self.expected}
        out |= {c.tag for c in self.abort_conditions}
        if self.trigger is not None:
            out.add(self.trigger.tag)
        return out


class TestPlan(BaseModel):
    __test__ = False  # pytest 수집 제외
    plan_id: str
    title: str
    equipment: list[str] = Field(default_factory=list)
    steps: list[Step]
    tag_aliases: dict[str, str] = Field(default_factory=dict)  # 논리명 -> 실제 태그

    @model_validator(mode="after")
    def _unique_ids(self) -> "TestPlan":
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("단계 id가 중복된다")
        return self

    def step(self, step_id: str) -> Step:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(step_id)

    def index_of(self, step_id: str) -> int:
        return [s.id for s in self.steps].index(step_id)

    def all_tags(self) -> set[str]:
        out: set[str] = set()
        for s in self.steps:
            out |= s.tags()
        return out


class Tag(BaseModel):
    tag: str
    equipment: str = ""
    description: str = ""
    unit: str = ""
    kind: Literal["bool", "float"] = "float"


class TagList(BaseModel):
    tags: list[Tag]

    def names(self) -> set[str]:
        return {t.tag for t in self.tags}


# ---------------------------------------------------------------- Preflight

class Severity(str, Enum):
    WARN = "WARN"
    ERROR = "ERROR"


class PreflightCode(str, Enum):
    PF01_MISSING_PRECONDITION = "PF01_MISSING_PRECONDITION"
    PF02_ORDER_CONTRADICTION = "PF02_ORDER_CONTRADICTION"
    PF03_NO_EXPECTED_RESULT = "PF03_NO_EXPECTED_RESULT"
    PF04_TAG_UNMAPPED = "PF04_TAG_UNMAPPED"
    PF05_MISSING_ROLLBACK = "PF05_MISSING_ROLLBACK"
    PF06_THRESHOLD_CONFLICT = "PF06_THRESHOLD_CONFLICT"


class Finding(BaseModel):
    code: PreflightCode
    severity: Severity
    step_id: Optional[str] = None
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    source: Literal["rule", "ai_suggestion"] = "rule"


# ---------------------------------------------------------------- Runtime

class StepState(str, Enum):
    PENDING = "PENDING"
    ARMED = "ARMED"
    RUNNING = "RUNNING"
    PASS = "PASS"
    FAIL = "FAIL"
    ABORT = "ABORT"
    HOLD = "HOLD"        # 판정에 필요한 값이 없어 시스템이 판정하지 않음. 엔지니어 확인 대상
    SKIPPED = "SKIPPED"


class ReasonCode(str, Enum):
    EXPECTED_MET = "EXPECTED_MET"
    STANDBY_START_TIMEOUT = "STANDBY_START_TIMEOUT"
    TEMP_RECOVERY_TIMEOUT = "TEMP_RECOVERY_TIMEOUT"
    STEP_TIMEOUT = "STEP_TIMEOUT"
    PRECONDITION_NOT_MET = "PRECONDITION_NOT_MET"
    TRIGGER_NOT_OBSERVED = "TRIGGER_NOT_OBSERVED"
    ABORT_CONDITION_HIT = "ABORT_CONDITION_HIT"
    TAG_MISSING_DATA = "TAG_MISSING_DATA"          # HOLD 사유: 판정에 필요한 태그 값 부재
    STREAM_ENDED_EARLY = "STREAM_ENDED_EARLY"      # HOLD 사유: 판정 마감 전 계측 스트림 종료
    SKIPPED_AFTER_FAIL = "SKIPPED_AFTER_FAIL"
    SKIPPED_AFTER_HOLD = "SKIPPED_AFTER_HOLD"


class Evidence(BaseModel):
    tag: Optional[str] = None
    t: Optional[float] = None
    value: Optional[float] = None
    threshold: Optional[str] = None
    deadline: Optional[float] = None
    elapsed_sec: Optional[float] = None
    extra: dict[str, Any] = Field(default_factory=dict)


class Verdict(BaseModel):
    step_id: str
    state: StepState
    t_start: Optional[float] = None
    t_end: Optional[float] = None
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    reason_ko: str = ""
    evidence: Evidence = Field(default_factory=Evidence)
    latency_ms: float = 0.0


class Segment(BaseModel):
    step_id: str
    kind: Literal["state", "exceeded"]
    state: Optional[StepState] = None
    t0: float
    t1: Optional[float] = None


class Event(BaseModel):
    step_id: str
    from_state: StepState
    to_state: StepState
    t: float
    codes: list[ReasonCode] = Field(default_factory=list)
    evidence: Evidence = Field(default_factory=Evidence)


class SessionMeta(BaseModel):
    id: str
    created_unix_ms: int
    plan_id: str
    case: str = ""
    backend: str = ""
    offline: dict[str, Any] = Field(default_factory=dict)
    timings: dict[str, float] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
