"""텔레메트리 CSV 입출력과 재생. 빈칸은 결측(None)."""
from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Iterator, Optional

from .schemas import Sample


def write_csv(rows: list[Sample], path: Path, columns: Optional[list[str]] = None) -> None:
    cols = columns or list(rows[0].keys())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow(["" if r.get(c) is None else r.get(c) for c in cols])


def read_csv(path: Path) -> list[Sample]:
    out: list[Sample] = []
    with path.open(encoding="utf-8") as f:
        for rec in csv.DictReader(f):
            row: Sample = {}
            for k, v in rec.items():
                if k is None:
                    continue
                v = (v or "").strip()
                row[k] = None if v == "" else float(v)
            out.append(row)
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
