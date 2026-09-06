"""규칙 엔진: 승인된 시험절차(TestPlan)를 1Hz 텔레메트리에 대조해 단계별 PASS/FAIL/ABORT를 판정한다.

상태 전이
  PENDING → ARMED → RUNNING → PASS | FAIL | ABORT | HOLD   (뒤 단계는 SKIPPED)

ARMED   : 사전조건 대기(precond_wait_sec) → 트리거가 있으면 트리거 대기(trigger_wait_sec)
          조건이 값으로 반증되면 FAIL, 값 자체가 없으면 HOLD.
RUNNING : 중단 조건 → ABORT. 기대 결과가 within_sec 안에 성립하고 hold_sec 동안 유지되면 PASS.
          t > t_run_start + within_sec + hold_sec 인데 미확정이면 FAIL (코드는 태그로 결정).
          FAIL 뒤에도 그 조건이 실제로 성립하는 시각까지 추적해 exceeded 구간을 남긴다.
          필요한 태그가 missing_tolerance_sec 이상 결측이면 HOLD TAG_MISSING_DATA.

HOLD(보류)는 "판정하지 않음"이다. 판정에 필요한 값이 없을 때 실패로 낮추지 않고 엔지니어 확인으로
넘긴다. 값이 있어서 기준을 벗어난 경우만 FAIL이다. 이 구분이 없으면 계측 장애가 설비 불합격으로
기록된다.

판정은 이 모듈만 내린다. LLM은 관여하지 않는다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from .reasons import render_ko, timeout_code_for
from .schemas import (
    Cond, Event, Evidence, Expected, ReasonCode, Sample, Segment, Step, StepState, TestPlan, Verdict,
)


def _or(v: Optional[float], default: float) -> float:
    """0.0 을 falsy 로 흘려보내지 않는 폴백. t=0 에 시작한 단계의 경과시간이 0으로 계산되던 버그."""
    return default if v is None else v


@dataclass
class EngineConfig:
    stop_on_fail: bool = True
    missing_tolerance_sec: float = 5.0


@dataclass
class _ExpTrack:
    met_since: Optional[float] = None
    confirmed_at: Optional[float] = None
    first_met_at: Optional[float] = None
    exceeded_t0: Optional[float] = None   # FAIL 시점의 마감(deadline)
    exceeded_t1: Optional[float] = None   # 실제 성립(hold 유지 시작) 시각
    post_met_since: Optional[float] = None


@dataclass
class StepRuntime:
    step: Step
    state: StepState = StepState.PENDING
    t_armed: Optional[float] = None
    t_run_start: Optional[float] = None
    t_end: Optional[float] = None
    tracks: list[_ExpTrack] = field(default_factory=list)
    missing_since: dict[str, float] = field(default_factory=dict)
    last_values: dict[str, Optional[float]] = field(default_factory=dict)
    codes: list[ReasonCode] = field(default_factory=list)
    evidence: Evidence = field(default_factory=Evidence)

    def __post_init__(self) -> None:
        self.tracks = [_ExpTrack() for _ in self.step.expected]


class RunEngine:
    def __init__(self, plan: TestPlan, cfg: Optional[EngineConfig] = None) -> None:
        self.plan = plan
        self.cfg = cfg or EngineConfig()
        self.rts: list[StepRuntime] = [StepRuntime(s) for s in plan.steps]
        self.active = 0
        self.verdicts: list[Verdict] = []
        self.segments: list[Segment] = []
        self.events: list[Event] = []
        self.last_t: Optional[float] = None
        self._pending_arm = True  # 첫 샘플에서 S1을 ARMED로

    # ------------------------------------------------------------ helpers
    def _val(self, sample: Sample, tag: str) -> Optional[float]:
        real = self.plan.tag_aliases.get(tag, tag)
        return sample.get(real)

    def _eval(self, sample: Sample, cond: Cond) -> Optional[bool]:
        return cond.evaluate(self._val(sample, cond.tag))

    def _transition(self, rt: StepRuntime, to: StepState, t: float,
                    codes: Optional[list[ReasonCode]] = None, ev: Optional[Evidence] = None) -> Event:
        e = Event(step_id=rt.step.id, from_state=rt.state, to_state=to, t=t,
                  codes=list(codes or []), evidence=ev or Evidence())
        rt.state = to
        self.events.append(e)
        return e

    def _finish_step(self, rt: StepRuntime, to: StepState, t: float, codes: list[ReasonCode],
                     ev: Evidence, t0_perf: float) -> Event:
        rt.t_end = t
        rt.codes = codes
        rt.evidence = ev
        e = self._transition(rt, to, t, codes, ev)
        self.segments.append(Segment(step_id=rt.step.id, kind="state", state=to,
                                     t0=rt.t_run_start if rt.t_run_start is not None else _or(rt.t_armed, t),
                                     t1=t))
        self.verdicts.append(Verdict(
            step_id=rt.step.id, state=to, t_start=rt.t_run_start, t_end=t,
            reason_codes=codes, reason_ko=" / ".join(render_ko(c, ev) for c in codes),
            evidence=ev, latency_ms=(time.perf_counter() - t0_perf) * 1000.0,
        ))
        return e

    def _skip_rest(self, t: float, t0_perf: float,
                   code: ReasonCode = ReasonCode.SKIPPED_AFTER_FAIL) -> list[Event]:
        out = []
        for rt in self.rts[self.active + 1:]:
            if rt.state in (StepState.PENDING, StepState.ARMED):
                out.append(self._finish_step(rt, StepState.SKIPPED, t, [code], Evidence(), t0_perf))
        self.active = len(self.rts)
        return out

    @staticmethod
    def _skip_code(state: StepState) -> ReasonCode:
        return ReasonCode.SKIPPED_AFTER_HOLD if state == StepState.HOLD else ReasonCode.SKIPPED_AFTER_FAIL

    # ------------------------------------------------------------ main
    def feed(self, sample: Sample) -> list[Event]:
        t0_perf = time.perf_counter()
        t = float(sample["t_sec"])  # type: ignore[arg-type]
        self.last_t = t
        events: list[Event] = []
        self._track_exceeded(sample, t)
        if self.active >= len(self.rts):
            return events
        rt = self.rts[self.active]
        if rt.state == StepState.PENDING:
            rt.t_armed = t
            events.append(self._transition(rt, StepState.ARMED, t))

        if rt.state == StepState.ARMED:
            ev_armed = self._step_armed(rt, sample, t, t0_perf)
            events.extend(ev_armed)
            if rt.state != StepState.RUNNING:
                if rt.state in (StepState.FAIL, StepState.HOLD):
                    if self.cfg.stop_on_fail:
                        events.extend(self._skip_rest(t, t0_perf, self._skip_code(rt.state)))
                    else:
                        self.active += 1
                return events

        if rt.state == StepState.RUNNING:
            ev_run = self._step_running(rt, sample, t, t0_perf)
            events.extend(ev_run)
            if rt.state == StepState.PASS:
                self.active += 1
            elif rt.state in (StepState.FAIL, StepState.ABORT, StepState.HOLD):
                if self.cfg.stop_on_fail:
                    events.extend(self._skip_rest(t, t0_perf, self._skip_code(rt.state)))
                else:
                    self.active += 1
        return events

    def _step_armed(self, rt: StepRuntime, sample: Sample, t: float, t0_perf: float) -> list[Event]:
        step = rt.step
        unmet: Optional[Cond] = None      # 값이 있는데 조건을 벗어남 → FAIL
        unknown: Optional[Cond] = None    # 값 자체가 없음 → HOLD
        for c in step.preconditions:
            r = self._eval(sample, c)
            rt.last_values[c.tag] = self._val(sample, c.tag)
            if r is False and unmet is None:
                unmet = c
            elif r is None and unknown is None:
                unknown = c
        if unmet is not None or unknown is not None:
            if t - _or(rt.t_armed, t) > step.precond_wait_sec:
                waited = t - _or(rt.t_armed, t)
                if unmet is not None:
                    ev = Evidence(tag=unmet.tag, t=t, value=rt.last_values.get(unmet.tag),
                                  threshold=unmet.threshold_text(), elapsed_sec=waited)
                    return [self._finish_step(rt, StepState.FAIL, t, [ReasonCode.PRECONDITION_NOT_MET], ev, t0_perf)]
                ev = Evidence(tag=unknown.tag, t=t, value=None,
                              threshold=unknown.threshold_text(), elapsed_sec=waited)
                return [self._finish_step(rt, StepState.HOLD, t, [ReasonCode.TAG_MISSING_DATA], ev, t0_perf)]
            return []
        if step.trigger is not None:
            r = self._eval(sample, step.trigger)
            rt.last_values[step.trigger.tag] = self._val(sample, step.trigger.tag)
            if r is not True:
                if t - _or(rt.t_armed, t) > step.trigger_wait_sec:
                    waited = t - _or(rt.t_armed, t)
                    ev = Evidence(tag=step.trigger.tag, t=t, value=rt.last_values.get(step.trigger.tag),
                                  threshold=step.trigger.threshold_text(), elapsed_sec=waited)
                    if r is None:
                        return [self._finish_step(rt, StepState.HOLD, t, [ReasonCode.TAG_MISSING_DATA], ev, t0_perf)]
                    return [self._finish_step(rt, StepState.FAIL, t, [ReasonCode.TRIGGER_NOT_OBSERVED], ev, t0_perf)]
                return []
        rt.t_run_start = t
        return [self._transition(rt, StepState.RUNNING, t)]

    def _step_running(self, rt: StepRuntime, sample: Sample, t: float, t0_perf: float) -> list[Event]:
        step = rt.step
        assert rt.t_run_start is not None
        # 1) 중단 조건
        for c in step.abort_conditions:
            v = self._val(sample, c.tag)
            if c.evaluate(v) is True:
                ev = Evidence(tag=c.tag, t=t, value=v, threshold=c.threshold_text(),
                              elapsed_sec=t - rt.t_run_start)
                return [self._finish_step(rt, StepState.ABORT, t, [ReasonCode.ABORT_CONDITION_HIT], ev, t0_perf)]
        # 2) 결측
        for tag in sorted({e.tag for e in step.expected}):
            v = self._val(sample, tag)
            if v is None:
                rt.missing_since.setdefault(tag, t)
                if t - rt.missing_since[tag] >= self.cfg.missing_tolerance_sec:
                    ev = Evidence(tag=tag, t=t, value=None, threshold=None,
                                  elapsed_sec=t - rt.missing_since[tag])
                    return [self._finish_step(rt, StepState.HOLD, t, [ReasonCode.TAG_MISSING_DATA], ev, t0_perf)]
            else:
                rt.missing_since.pop(tag, None)
        # 3) 기대 결과
        all_confirmed = True
        for i, exp in enumerate(step.expected):
            tr = rt.tracks[i]
            v = self._val(sample, exp.tag)
            rt.last_values[exp.tag] = v
            r = exp.evaluate(v)
            if tr.confirmed_at is not None:
                continue
            if r is True:
                tr.met_since = t if tr.met_since is None else tr.met_since
                tr.first_met_at = tr.first_met_at if tr.first_met_at is not None else t
                if t - tr.met_since >= exp.hold_sec:
                    tr.confirmed_at = t
                else:
                    all_confirmed = False
            else:
                tr.met_since = None
                all_confirmed = False
        if all_confirmed and step.expected:
            last = max(range(len(step.expected)), key=lambda i: rt.tracks[i].confirmed_at or 0)
            exp = step.expected[last]
            ev = Evidence(tag=exp.tag, t=t, value=rt.last_values.get(exp.tag), threshold=exp.threshold_text(),
                          deadline=rt.t_run_start + exp.within_sec, elapsed_sec=t - rt.t_run_start,
                          extra={"within_sec": exp.within_sec, "hold_sec": exp.hold_sec})
            return [self._finish_step(rt, StepState.PASS, t, [ReasonCode.EXPECTED_MET], ev, t0_perf)]
        # 4) 허용시간 초과
        for i, exp in enumerate(step.expected):
            tr = rt.tracks[i]
            if tr.confirmed_at is not None:
                continue
            fail_deadline = rt.t_run_start + exp.within_sec + exp.hold_sec
            if t > fail_deadline:
                tr.exceeded_t0 = rt.t_run_start + exp.within_sec
                code = timeout_code_for(exp.tag)
                ev = Evidence(tag=exp.tag, t=t, value=rt.last_values.get(exp.tag), threshold=exp.threshold_text(),
                              deadline=rt.t_run_start + exp.within_sec, elapsed_sec=t - rt.t_run_start,
                              extra={"within_sec": exp.within_sec, "hold_sec": exp.hold_sec, "met_t": None})
                e = self._finish_step(rt, StepState.FAIL, t, [code], ev, t0_perf)
                self._track_exceeded(sample, t)  # 현재 샘플부터 실제 성립 시각 추적
                return [e]
        return []

    def _track_exceeded(self, sample: Sample, t: float) -> None:
        """FAIL한 단계의 기대 조건이 나중에 실제로 성립하는 시각을 찾아 exceeded 구간을 닫는다."""
        for rt in self.rts:
            if rt.state != StepState.FAIL:
                continue
            for i, exp in enumerate(rt.step.expected):
                tr = rt.tracks[i]
                if tr.exceeded_t0 is None or tr.exceeded_t1 is not None:
                    continue
                if exp.evaluate(self._val(sample, exp.tag)) is True:
                    if tr.post_met_since is None:
                        tr.post_met_since = t
                    if t - tr.post_met_since >= exp.hold_sec:
                        met_t = tr.post_met_since
                        tr.exceeded_t1 = met_t
                        self.segments.append(Segment(step_id=rt.step.id, kind="exceeded", t0=tr.exceeded_t0, t1=met_t))
                        rt.evidence.extra["met_t"] = met_t
                        for v in self.verdicts:
                            if v.step_id == rt.step.id and v.state == StepState.FAIL:
                                v.evidence.extra["met_t"] = met_t
                                v.reason_ko = " / ".join(render_ko(c, v.evidence) for c in v.reason_codes)
                else:
                    tr.post_met_since = None

    def finish(self) -> list[Verdict]:
        """스트림 종료. 판정 마감 전에 끊긴 단계는 HOLD, 열린 exceeded 구간은 종료 시각으로 닫는다.

        마감을 넘겼다면 feed() 안에서 이미 FAIL이 나왔다. 여기 남아 있다는 것은 마감 전에
        데이터가 끊겼다는 뜻이므로 판정하지 않고 보류한다.
        """
        t = self.last_t if self.last_t is not None else 0.0
        t0_perf = time.perf_counter()
        if self.active < len(self.rts):
            rt = self.rts[self.active]
            if rt.state == StepState.RUNNING:
                exp = next((e for i, e in enumerate(rt.step.expected) if rt.tracks[i].confirmed_at is None), None)
                ev = Evidence(tag=exp.tag if exp else None, t=t, value=rt.last_values.get(exp.tag) if exp else None,
                              threshold=exp.threshold_text() if exp else None,
                              deadline=_or(rt.t_run_start, t) + (exp.within_sec if exp else 0),
                              elapsed_sec=t - _or(rt.t_run_start, t),
                              extra={"within_sec": exp.within_sec if exp else None, "stream_ended": True})
                self._finish_step(rt, StepState.HOLD, t, [ReasonCode.STREAM_ENDED_EARLY], ev, t0_perf)
            elif rt.state in (StepState.ARMED, StepState.PENDING):
                step = rt.step
                c = step.trigger or (step.preconditions[0] if step.preconditions else None)
                ev = Evidence(tag=c.tag if c else None, t=t, threshold=c.threshold_text() if c else None,
                              elapsed_sec=t - _or(rt.t_armed, t), extra={"stream_ended": True})
                self._finish_step(rt, StepState.HOLD, t, [ReasonCode.STREAM_ENDED_EARLY], ev, t0_perf)
            self._skip_rest(t, t0_perf, self._skip_code(rt.state))
        for rt in self.rts:
            for i, exp in enumerate(rt.step.expected):
                tr = rt.tracks[i]
                if tr.exceeded_t0 is not None and tr.exceeded_t1 is None:
                    self.segments.append(Segment(step_id=rt.step.id, kind="exceeded", t0=tr.exceeded_t0, t1=None))
        return self.verdicts

    def summary(self) -> dict:
        states = {rt.step.id: rt.state.value for rt in self.rts}
        overall = "PASS"
        if any(s == StepState.ABORT.value for s in states.values()):
            overall = "ABORT"
        elif any(s == StepState.FAIL.value for s in states.values()):
            overall = "FAIL"
        elif any(s == StepState.HOLD.value for s in states.values()):
            overall = "HOLD"
        return {"overall": overall, "states": states,
                "n_pass": sum(1 for s in states.values() if s == "PASS"),
                "n_fail": sum(1 for s in states.values() if s == "FAIL"),
                "n_hold": sum(1 for s in states.values() if s == "HOLD"),
                "n_skipped": sum(1 for s in states.values() if s == "SKIPPED")}


def run_stream(plan: TestPlan, samples: list[Sample], cfg: Optional[EngineConfig] = None) -> RunEngine:
    eng = RunEngine(plan, cfg)
    for s in samples:
        eng.feed(s)
    eng.finish()
    return eng
