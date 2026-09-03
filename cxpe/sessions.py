"""세션 저장소: sessions/<id>/ 아래 파일로만 상태를 남긴다 (DB 없음, 폐쇄망 로컬).

파일: plan.md, plan.json, tags.json, preflight.json, rules.approved.json, telemetry.csv, labels.json,
      verdicts.jsonl, segments.json, events.jsonl, remarks.json, report.md, meta.json
"""
from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .schemas import SessionMeta

ROOT = Path(__file__).resolve().parents[1]


def sessions_root() -> Path:
    return Path(os.environ.get("CXPE_SESSIONS", ROOT / "sessions"))


def new_session_id() -> str:
    return f"cx-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"


class SessionStore:
    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root else sessions_root()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, sid: str) -> Path:
        p = self.root / sid
        if not p.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("bad session id")
        return p

    def exists(self, sid: str) -> bool:
        return (self.path(sid) / "meta.json").exists()

    def create(self, plan_id: str, case: str = "", backend: str = "") -> SessionMeta:
        sid = new_session_id()
        p = self.path(sid)
        p.mkdir(parents=True, exist_ok=False)
        meta = SessionMeta(id=sid, created_unix_ms=int(time.time() * 1000), plan_id=plan_id, case=case, backend=backend)
        self.write_meta(meta)
        return meta

    def write_meta(self, meta: SessionMeta) -> None:
        self.save_json(meta.id, "meta.json", meta.model_dump(mode="json"))

    def read_meta(self, sid: str) -> SessionMeta:
        return SessionMeta.model_validate(self.load_json(sid, "meta.json"))

    def update_meta(self, sid: str, **fields: Any) -> SessionMeta:
        meta = self.read_meta(sid)
        data = meta.model_dump()
        for k, v in fields.items():
            if isinstance(v, dict) and isinstance(data.get(k), dict):
                data[k].update(v)
            else:
                data[k] = v
        meta = SessionMeta.model_validate(data)
        self.write_meta(meta)
        return meta

    def save_json(self, sid: str, name: str, obj: Any) -> Path:
        p = self.path(sid) / name
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    def load_json(self, sid: str, name: str) -> Any:
        return json.loads((self.path(sid) / name).read_text(encoding="utf-8"))

    def has(self, sid: str, name: str) -> bool:
        return (self.path(sid) / name).exists()

    def save_text(self, sid: str, name: str, text: str) -> Path:
        p = self.path(sid) / name
        p.write_text(text, encoding="utf-8")
        return p

    def load_text(self, sid: str, name: str) -> str:
        return (self.path(sid) / name).read_text(encoding="utf-8")

    def append_jsonl(self, sid: str, name: str, obj: Any) -> None:
        with (self.path(sid) / name).open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def load_jsonl(self, sid: str, name: str) -> list[Any]:
        p = self.path(sid) / name
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]

    def list(self) -> list[SessionMeta]:
        out = []
        for d in self.root.iterdir():
            if d.is_dir() and (d / "meta.json").exists():
                try:
                    out.append(self.read_meta(d.name))
                except Exception:
                    continue
        out.sort(key=lambda m: m.created_unix_ms, reverse=True)
        return out

    def delete(self, sid: str) -> None:
        p = self.path(sid)
        for f in p.iterdir():
            f.unlink()
        p.rmdir()
