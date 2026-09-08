"""텔레메트리 CSV 입출력과 재생. 빈칸은 결측(None)."""
from __future__ import annotations

import csv
import math
import time
from pathlib import Path
from typing import Iterator, Optional

from .schemas import Sample


def write_csv(rows: list[Sample], path: Path, columns: Optional[list[str]] = None) -> None:
    if not rows and not columns:
        raise ValueError("빈 측정값을 저장할 때는 열 이름이 필요합니다")
    cols = columns or list(rows[0].keys())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow(["" if r.get(c) is None else r.get(c) for c in cols])


def read_csv(path: Path) -> list[Sample]:
    out: list[Sample] = []
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        columns = [c.strip() for c in (reader.fieldnames or [])]
        if "t_sec" not in columns or len(set(columns)) != len(columns) or any(not c for c in columns):
            raise ValueError("CSV는 중복 없는 열 이름과 t_sec 열이 필요합니다")
        reader.fieldnames = columns
        previous = None
        for number, record in enumerate(reader, 2):
            if None in record:
                raise ValueError(f"CSV {number}행: 열 수가 헤더보다 많습니다")
            row: Sample = {}
            for key, value in record.items():
                value = (value or "").strip()
                try:
                    parsed = None if not value else float(value)
                except ValueError as error:
                    raise ValueError(f"CSV {number}행 {key}: 숫자 또는 빈칸이 필요합니다") from error
                row[key] = parsed if parsed is not None and math.isfinite(parsed) else None
            timestamp = row["t_sec"]
            if timestamp is None or previous is not None and timestamp <= previous:
                raise ValueError(f"CSV {number}행: t_sec는 유한하고 증가해야 합니다")
            previous = timestamp
            out.append(row)
    if not out:
        raise ValueError("CSV에 측정값이 없습니다")
    return out


def replay(rows: list[Sample], speed: float = 1.0, start_at: float = 0.0) -> Iterator[Sample]:
    """t_sec 간격을 실제 시간으로 재생한다. speed=0이면 지연 없이 즉시."""
    prev_t: Optional[float] = None
    wall0 = time.perf_counter()
    for r in rows:
        t = float(r["t_sec"])  # type: ignore[arg-type]
        if t < start_at:
            continue
        if speed > 0 and prev_t is not None:
            target = wall0 + (t - start_at) / speed
            delay = target - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
        prev_t = t
        yield r
