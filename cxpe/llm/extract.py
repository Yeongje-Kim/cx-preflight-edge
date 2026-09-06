"""시험절차서(markdown) → TestPlan 추출, 태그 매핑 제안, 모순 후보. 전부 LLM 보조 기능.

- 추출은 단계 1개씩 직렬 호출한다(4B 컨텍스트 보호, 3초 갭 대응).
- 결과는 pydantic으로 검증한다. 실패하면 "JSON only" 지시를 붙여 1회 재시도하고, 그래도 실패하면
  해당 단계는 None으로 두어 호출자가 골든으로 폴백하게 한다.
- 태그 매핑·모순 후보는 Finding(source="ai_suggestion")으로만 나간다. 규칙에 자동 반영하지 않는다.
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from ..schemas import Finding, PreflightCode, Severity, Step, TagList, TestPlan
from .client import LlmClient, LlmError
from .prompts import contradiction_messages, extraction_messages, mapping_messages

_THINK = re.compile(r"<think>.*?</think>", re.S)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_HEAD = re.compile(r"^#\s+.*?(IST-[A-Z0-9-]+)", re.M)
_TITLE = re.compile(r"^##\s+(.+)$", re.M)
_STEP = re.compile(r"^###\s+(S\d+)\s+(.+?)\s*$", re.M)


def strip_think(text: str) -> str:
    return _THINK.sub("", text)


def extract_last_json_object(text: str) -> Optional[str]:
    """마지막으로 균형 잡힌 {...}를 돌려준다 (judge.rs extract_last_json_object와 같은 규칙)."""
    end = text.rfind("}")
    while end != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(end, -1, -1):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "}":
                depth += 1
            elif ch == "{":
                depth -= 1
                if depth == 0:
                    return text[i:end + 1]
        end = text.rfind("}", 0, end)
    return None


def parse_json_loose(text: str) -> dict:
    t = strip_think(text)
    m = _FENCE.search(t)
    if m:
        t = m.group(1)
    if "[BEGIN]:" in t:
        t = t.split("[BEGIN]:", 1)[1]
    if "[END]" in t:
        t = t.split("[END]", 1)[0]
    obj = extract_last_json_object(t)
    if obj is None:
        raise ValueError("no JSON object in LLM output")
    return json.loads(obj)


def chunk_steps(md: str) -> list[tuple[str, str, str]]:
    """(step_id, title, 본문) 목록. '### S1 제목' 헤더 기준."""
    heads = list(_STEP.finditer(md))
    out = []
    for i, h in enumerate(heads):
        start = h.start()
        end = heads[i + 1].start() if i + 1 < len(heads) else len(md)
        body = md[start:end]
        # 다음 상위 섹션(## 5. 기록 등)은 제외
        cut = re.search(r"^##\s", body[len(h.group(0)):], re.M)
        if cut:
            body = body[: len(h.group(0)) + cut.start()]
        out.append((h.group(1), h.group(2).strip(), body.strip()))
    return out


def plan_header(md: str) -> tuple[str, str]:
    pid = _HEAD.search(md)
    title = _TITLE.search(md)
    return (pid.group(1) if pid else "PLAN"), (title.group(1).strip() if title else "시험절차서")


def _merge_orphan_timing(items: object) -> object:
    """tag/op 없이 시간 필드만 있는 조건 객체를 바로 앞 조건에 병합한다.

    4B 모델이 기대 결과 하나를 조건 객체와 시간 객체로 쪼개 내보내는 실패를 결정적으로 복구한다.
    앞 조건이 없거나 병합할 수 없으면 그대로 두고 스키마 검증이 걸러낸다.
    """
    if not isinstance(items, list):
        return items
    out: list = []
    for it in items:
        if (isinstance(it, dict) and out and isinstance(out[-1], dict)
                and "tag" not in it and "op" not in it
                and set(it) <= {"within_sec", "hold_sec"} and it):
            for k, v in it.items():
                out[-1].setdefault(k, v)
            continue
        out.append(dict(it) if isinstance(it, dict) else it)
    return out


def _normalize_step(d: dict, step_id: str, title: str, source_text: str) -> dict:
    d = dict(d)
    d.setdefault("id", step_id)
    d.setdefault("title", title)
    d["id"] = step_id
    d["source_text"] = source_text
    if d.get("trigger") in ({}, "", "null"):
        d["trigger"] = None
    for k in ("preconditions", "expected", "abort_conditions", "rollback"):
        v = d.get(k)
        if v is None or v == "" or v == "null":
            d[k] = []
    d["expected"] = _merge_orphan_timing(d.get("expected"))
    cs = d.get("changes_state", False)
    if isinstance(cs, str):
        d["changes_state"] = cs.strip().lower() in ("true", "1", "yes", "있음")
    return d


def extract_step(client: LlmClient, step_id: str, title: str, text: str, max_tokens: int = 400,
                 retries: int = 2) -> tuple[Optional[Step], dict]:
    """단계 1개 추출. (Step|None, stats)."""
    stats = {"step_id": step_id, "attempts": 0, "ok": False, "error": None, "sec": 0.0}
    messages = extraction_messages(text)
    t0 = time.perf_counter()
    last_err: Optional[str] = None
    for attempt in range(retries + 1):
        stats["attempts"] += 1
        try:
            raw = client.complete(messages, max_tokens=max_tokens, json_mode=True)
            data = parse_json_loose(raw)
            step = Step.model_validate(_normalize_step(data, step_id, title, text))
            stats["ok"] = True
            stats["sec"] = time.perf_counter() - t0
            return step, stats
        except (LlmError, ValueError, json.JSONDecodeError) as e:
            last_err = f"{type(e).__name__}: {e}"
        except Exception as e:  # pydantic ValidationError 등
            last_err = f"{type(e).__name__}: {str(e)[:300]}"
        messages = messages + [
            {"role": "assistant", "content": "(invalid)"},
            {"role": "user", "content": "Your previous output did not fit the schema. Error:\n"
                                        + (last_err or "")[:400]
                                        + "\nFix exactly these fields and output ONLY the JSON object."},
        ]
    stats["error"] = last_err
    stats["sec"] = time.perf_counter() - t0
    return None, stats


def extract_plan(md: str, client: LlmClient, golden: Optional[TestPlan] = None,
                 max_tokens: int = 400) -> tuple[TestPlan, dict]:
    """전체 절차서 추출. 실패한 단계는 golden으로 채운다(있을 때). stats에 단계별 결과와 폴백 여부."""
    plan_id, title = plan_header(md)
    chunks = chunk_steps(md)
    steps: list[Step] = []
    per_step = []
    fallback_ids: list[str] = []
    for sid, stitle, text in chunks:
        step, st = extract_step(client, sid, stitle, text, max_tokens=max_tokens)
        per_step.append(st)
        if step is None:
            if golden is not None:
                try:
                    steps.append(golden.step(sid))
                    fallback_ids.append(sid)
                    continue
                except KeyError:
                    pass
            raise LlmError(f"step {sid} extraction failed and no golden fallback: {st['error']}")
        steps.append(step)
    plan = TestPlan(plan_id=plan_id, title=title, steps=steps,
                    equipment=golden.equipment if golden else [], tag_aliases=golden.tag_aliases if golden else {})
    stats = {"backend": getattr(client, "name", "?"), "steps": per_step, "fallback_ids": fallback_ids,
             "n_ok": sum(1 for s in per_step if s["ok"]), "n_total": len(per_step),
             "sec_total": sum(s["sec"] for s in per_step)}
    return plan, stats


def map_tags(plan: TestPlan, tags: TagList, client: LlmClient) -> list[Finding]:
    """태그리스트에 없는 이름을 LLM이 제안한 태그로 매핑한다. 결과는 ai_suggestion Finding."""
    names = sorted(t for t in plan.all_tags() if plan.tag_aliases.get(t, t) not in tags.names())
    if not names:
        return []
    rows = [t.model_dump() for t in tags.tags]
    try:
        raw = client.complete(mapping_messages(names, rows), max_tokens=300, json_mode=True)
        data = parse_json_loose(raw)
    except Exception as e:
        return [Finding(code=PreflightCode.PF04_TAG_UNMAPPED, severity=Severity.WARN, step_id=None,
                        message=f"AI 태그 매핑 제안 실패: {type(e).__name__}", source="ai_suggestion")]
    out = []
    valid = tags.names()
    for m in data.get("mappings", []) or []:
        name, tag = m.get("name"), m.get("tag")
        conf = float(m.get("confidence") or 0)
        if name not in names:
            continue
        if tag in valid:
            out.append(Finding(code=PreflightCode.PF04_TAG_UNMAPPED, severity=Severity.WARN, step_id=None,
                               message=f"AI 제안: '{name}' → '{tag}' (신뢰도 {conf:.2f}). 승인 시 alias에 반영",
                               evidence={"name": name, "tag": tag, "confidence": conf}, source="ai_suggestion"))
        else:
            out.append(Finding(code=PreflightCode.PF04_TAG_UNMAPPED, severity=Severity.WARN, step_id=None,
                               message=f"AI 제안 없음: '{name}'에 맞는 태그를 찾지 못함",
                               evidence={"name": name, "tag": None, "confidence": conf}, source="ai_suggestion"))
    return out


def find_contradictions(plan: TestPlan, client: LlmClient) -> list[Finding]:
    summaries = [{
        "id": s.id, "title": s.title,
        "preconditions": [c.threshold_text() for c in s.preconditions],
        "trigger": s.trigger.threshold_text() if s.trigger else None,
        "expected": [e.threshold_text() for e in s.expected],
    } for s in plan.steps]
    try:
        raw = client.complete(contradiction_messages(summaries), max_tokens=300, json_mode=True)
        data = parse_json_loose(raw)
    except Exception as e:
        return [Finding(code=PreflightCode.PF02_ORDER_CONTRADICTION, severity=Severity.WARN, step_id=None,
                        message=f"AI 모순 후보 탐색 실패: {type(e).__name__}", source="ai_suggestion")]
    ids = {s.id for s in plan.steps}
    out = []
    for c in data.get("candidates", []) or []:
        sid = c.get("step_id")
        kind = str(c.get("kind", "PF02")).upper()
        code = PreflightCode.PF06_THRESHOLD_CONFLICT if kind == "PF06" else PreflightCode.PF02_ORDER_CONTRADICTION
        if sid not in ids:
            continue
        out.append(Finding(code=code, severity=Severity.WARN, step_id=sid,
                           message=f"AI 후보: {c.get('message', '')}", source="ai_suggestion"))
    return out
