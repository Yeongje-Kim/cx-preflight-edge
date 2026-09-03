"""합성 텔레메트리(SIL): 냉동기 N+1 전환 시나리오. 정답 라벨을 파라미터에서 해석적으로 만든다.

이 데이터는 실측이 아니다. 기능검증용이며 모든 시간·온도 상수는 임의 설정값이다.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, replace
from typing import Optional

from .schemas import Sample

COLUMNS = ["t_sec", "CH1_STATUS", "CH1_TRIP", "CH2_CMD", "CH2_STATUS", "CHWP2_STATUS",
           "CHWS_T_SUP", "CHWR_T_RET", "LOAD_KW"]


@dataclass
class SynthParams:
    duration_sec: float = 200.0
    dt: float = 1.0
    seed: int = 0
    t_trip: float = 25.0
    cmd_delay: float = 2.0            # 트립 → CH2_CMD
    start_delay: float = 12.0         # CH2_CMD → CH2_STATUS (no_start면 무시)
    pump_delay: float = 2.0           # CH2_STATUS → CHWP2_STATUS
    no_start: bool = False
    t_set: float = 7.0
    t_max: float = 16.0
    tau_rise: float = 25.0
    tau_rec: float = 15.0
    noise: float = 0.05
    load_kw: float = 220.0
    t_restore: float = 130.0          # CH1_TRIP → 0
    restore_ch1_delay: float = 15.0   # → CH1_STATUS = 1
    restore_ch2_delay: float = 40.0   # → CH2_STATUS = 0, CH2_CMD = 0
    dropout: Optional[tuple[float, float]] = None   # CHWS_T_SUP 결측 구간 [a, b)
    rename: dict[str, str] = field(default_factory=dict)


CASES: dict[str, SynthParams] = {
    "pass": SynthParams(),
    "fail_start": SynthParams(start_delay=45.0),
    "fail_temp": SynthParams(tau_rec=60.0, duration_sec=240.0, t_restore=170.0),
    "fail_dropout": SynthParams(dropout=(60.0, 70.0)),
    "no_start": SynthParams(no_start=True),
}


def _t_ch2(p: SynthParams) -> float:
    return math.inf if p.no_start else p.t_trip + p.cmd_delay + p.start_delay


def _temp(p: SynthParams, t: float) -> float:
    t_ch2 = _t_ch2(p)
    if t < p.t_trip:
        return p.t_set
    if t < t_ch2:
        return p.t_set + (p.t_max - p.t_set) * (1.0 - math.exp(-(t - p.t_trip) / p.tau_rise))
    peak = p.t_set + (p.t_max - p.t_set) * (1.0 - math.exp(-(t_ch2 - p.t_trip) / p.tau_rise))
    return p.t_set + (peak - p.t_set) * math.exp(-(t - t_ch2) / p.tau_rec)


def generate(p: SynthParams) -> tuple[list[Sample], dict]:
    rng = random.Random(p.seed)
    t_ch2 = _t_ch2(p)
    t_pump = t_ch2 + p.pump_delay
    rows: list[Sample] = []
    n = int(p.duration_sec / p.dt) + 1
    for k in range(n):
        t = k * p.dt
        restored = t >= p.t_restore
        ch1_trip = 1.0 if (p.t_trip <= t and not restored) else 0.0
        ch1_status = 1.0 if (t < p.t_trip + 1 or t >= p.t_restore + p.restore_ch1_delay) else 0.0
        ch2_cmd = 1.0 if (p.t_trip + p.cmd_delay <= t < p.t_restore + p.restore_ch2_delay) else 0.0
        ch2_status = 1.0 if (t_ch2 <= t < p.t_restore + p.restore_ch2_delay) else 0.0
        chwp2 = 1.0 if (t_pump <= t < p.t_restore + p.restore_ch2_delay + 2) else 0.0
        temp = _temp(p, t) + rng.gauss(0.0, p.noise)
        if t >= p.t_restore + p.restore_ch1_delay:
            temp = p.t_set + rng.gauss(0.0, p.noise)
        ret = temp + 5.0 + rng.gauss(0.0, p.noise)
        load = p.load_kw + rng.gauss(0.0, 1.0)
        row: Sample = {
            "t_sec": t, "CH1_STATUS": ch1_status, "CH1_TRIP": ch1_trip, "CH2_CMD": ch2_cmd,
            "CH2_STATUS": ch2_status, "CHWP2_STATUS": chwp2, "CHWS_T_SUP": round(temp, 3),
            "CHWR_T_RET": round(ret, 3), "LOAD_KW": round(load, 1),
        }
        if p.dropout is not None and p.dropout[0] <= t < p.dropout[1]:
            row["CHWS_T_SUP"] = None
        if p.rename:
            row = {p.rename.get(k, k): v for k, v in row.items()}
        rows.append(row)
    return rows, expected_labels(p)


def _first_in_band_after(p: SynthParams, t_from: float, hi: float = 8.0) -> Optional[float]:
    """CH2 기동 후 공급온도가 hi 이하로 내려오는 첫 정수 시각(노이즈 제외)."""
    t_ch2 = _t_ch2(p)
    if math.isinf(t_ch2):
        return None
    t = max(t_from, t_ch2)
    while t <= p.duration_sec:
        if _temp(p, t) <= hi:
            return float(t)
        t += p.dt
    return None


def expected_labels(p: SynthParams) -> dict:
    """골든 절차서(IST-COOL-N1-01) 기준의 해석적 정답. 시각은 ±3초 허용으로 비교한다."""
    steps: dict[str, dict] = {}
    exceeded: list[dict] = []
    t1 = 10.0                                     # S1: hold 10 from t=0
    steps["S1"] = {"state": "PASS", "codes": ["EXPECTED_MET"], "t_end": t1}
    t2 = t1 + 1 + 10                              # S2: RUNNING at 11, hold 10
    steps["S2"] = {"state": "PASS", "codes": ["EXPECTED_MET"], "t_end": t2}
    t3 = p.t_trip + 1                             # S3: trigger at t_trip, CH1 stops at +1
    steps["S3"] = {"state": "PASS", "codes": ["EXPECTED_MET"], "t_end": t3}
    t4 = max(t3 + 1, p.t_trip + p.cmd_delay)      # S4: CH2_CMD
    steps["S4"] = {"state": "PASS", "codes": ["EXPECTED_MET"], "t_end": t4}
    run5 = t4 + 1
    t_ch2 = _t_ch2(p)
    dead5 = run5 + 30.0
    if t_ch2 > dead5:
        steps["S5"] = {"state": "FAIL", "codes": ["STANDBY_START_TIMEOUT"], "t_end": dead5 + 1}
        exceeded.append({"step_id": "S5", "t0": dead5, "t1": None if math.isinf(t_ch2) else t_ch2})
        for sid in ("S6", "S7", "S8"):
            steps[sid] = {"state": "SKIPPED", "codes": ["SKIPPED_AFTER_FAIL"], "t_end": dead5 + 1}
        return {"steps": steps, "exceeded": exceeded, "overall": "FAIL"}
    t5 = max(run5, t_ch2)
    steps["S5"] = {"state": "PASS", "codes": ["EXPECTED_MET"], "t_end": t5}
    run6 = t5 + 1
    t6 = max(run6, t_ch2 + p.pump_delay)
    steps["S6"] = {"state": "PASS", "codes": ["EXPECTED_MET"], "t_end": t6}
    run7 = t6 + 1
    dead7 = run7 + 60.0
    if p.dropout is not None and p.dropout[0] >= run7 and p.dropout[0] < dead7:
        t_fail = p.dropout[0] + 5.0
        steps["S7"] = {"state": "FAIL", "codes": ["TAG_MISSING_DATA"], "t_end": t_fail}
        for sid in ("S8",):
            steps[sid] = {"state": "SKIPPED", "codes": ["SKIPPED_AFTER_FAIL"], "t_end": t_fail}
        return {"steps": steps, "exceeded": exceeded, "overall": "FAIL"}
    t_in = _first_in_band_after(p, run7)
    if t_in is None or t_in + 10.0 > dead7 + 10.0:
        steps["S7"] = {"state": "FAIL", "codes": ["TEMP_RECOVERY_TIMEOUT"], "t_end": dead7 + 10.0 + 1}
        exceeded.append({"step_id": "S7", "t0": dead7, "t1": t_in})
        steps["S8"] = {"state": "SKIPPED", "codes": ["SKIPPED_AFTER_FAIL"], "t_end": dead7 + 10.0 + 1}
        return {"steps": steps, "exceeded": exceeded, "overall": "FAIL"}
    t7 = t_in + 10.0
    steps["S7"] = {"state": "PASS", "codes": ["EXPECTED_MET"], "t_end": t7}
    t8 = p.t_restore + p.restore_ch2_delay
    steps["S8"] = {"state": "PASS", "codes": ["EXPECTED_MET"], "t_end": t8}
    return {"steps": steps, "exceeded": exceeded, "overall": "PASS"}


def make_case(name: str, seed: Optional[int] = None) -> tuple[list[Sample], dict, SynthParams]:
    p = CASES[name]
    if seed is not None:
        p = replace(p, seed=seed)
    rows, labels = generate(p)
    labels["case"] = name
    labels["seed"] = p.seed
    return rows, labels, p
