"""온디바이스 LLM 클라이언트. 백엔드 2종 + 테스트용 Fake.

- GenieXBackend : 보드의 GenieX OpenAI 호환 서버(127.0.0.1:18181). enable_json으로 JSON 제약 디코딩.
- NativeBackend : 보드에 설치된 네이티브 Genie LLM 서비스(127.0.0.1:8091). POST /api/v1/query.
                  쿼리 간 3초 갭이 강제되고, 갭 중 새 요청이 오면 이전 요청이 "superseded"로 실패한다.
                  그래서 Lock으로 직렬화하고 완료 후 3.5초를 기다린다.
- FakeBackend   : 테스트. 응답 목록 또는 콜백.

두 백엔드 모두 보드에 이미 설치된 런타임을 HTTP로 호출한다. 이 저장소는 런타임을 포함하지 않는다.

모든 백엔드는 complete(messages, max_tokens, json_mode) -> str 만 제공한다.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Callable, Optional, Protocol

import httpx

Messages = list[dict[str, str]]


class LlmClient(Protocol):
    name: str

    def complete(self, messages: Messages, max_tokens: int = 256, json_mode: bool = False) -> str: ...

    def alive(self) -> bool: ...


class LlmError(RuntimeError):
    pass


class NativeBackend:
    name = "native-genie-8091"
    QUERY_GAP_SEC = 3.5

    def __init__(self, base_url: str = "http://127.0.0.1:8091", timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._lock = threading.Lock()
        self._last_done = 0.0

    def alive(self) -> bool:
        try:
            r = httpx.get(f"{self.base_url}/healthz", timeout=3.0)
            return r.status_code == 200
        except Exception:
            return False

    def complete(self, messages: Messages, max_tokens: int = 256, json_mode: bool = False) -> str:
        body = {"messages": messages, "skip_thinking": True, "max_tokens": max_tokens}
        with self._lock:
            wait = self.QUERY_GAP_SEC - (time.monotonic() - self._last_done)
            if wait > 0:
                time.sleep(wait)
            for attempt in range(2):
                try:
                    r = httpx.post(f"{self.base_url}/api/v1/query", json=body, timeout=self.timeout)
                except httpx.HTTPError as e:
                    raise LlmError(f"rust backend: {e}") from e
                data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
                if r.status_code == 200 and "text" in data:
                    self._last_done = time.monotonic()
                    return str(data["text"])
                err = str(data.get("error", r.text))
                if "superseded" in err and attempt == 0:
                    time.sleep(4.0)
                    continue
                self._last_done = time.monotonic()
                raise LlmError(f"rust backend {r.status_code}: {err}")
            raise LlmError("rust backend: superseded twice")


class GenieXBackend:
    name = "geniex-18181"

    def __init__(self, base_url: str = "http://127.0.0.1:18181", model: Optional[str] = None,
                 timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._lock = threading.Lock()

    def alive(self) -> bool:
        try:
            r = httpx.get(f"{self.base_url}/v1/models", timeout=3.0)
            if r.status_code != 200:
                return False
            if self.model is None:
                data = r.json().get("data") or []
                if data:
                    self.model = data[0].get("id")
            return True
        except Exception:
            return False

    def complete(self, messages: Messages, max_tokens: int = 256, json_mode: bool = False) -> str:
        body: dict = {"model": self.model or "qwen3-4b-instruct-2507", "messages": messages,
                      "max_tokens": max_tokens, "temperature": 0}
        if json_mode:
            body["enable_json"] = True
        with self._lock:
            try:
                r = httpx.post(f"{self.base_url}/v1/chat/completions", json=body, timeout=self.timeout)
            except httpx.HTTPError as e:
                raise LlmError(f"geniex backend: {e}") from e
            if r.status_code != 200:
                raise LlmError(f"geniex backend {r.status_code}: {r.text[:200]}")
            data = r.json()
            try:
                return str(data["choices"][0]["message"]["content"])
            except (KeyError, IndexError, TypeError) as e:
                raise LlmError(f"geniex backend: unexpected response {data}") from e


class FakeBackend:
    name = "fake"

    def __init__(self, responses: Optional[list[str]] = None,
                 responder: Optional[Callable[[Messages], str]] = None) -> None:
        self.responses = list(responses or [])
        self.responder = responder
        self.calls: list[Messages] = []

    def alive(self) -> bool:
        return True

    def complete(self, messages: Messages, max_tokens: int = 256, json_mode: bool = False) -> str:
        self.calls.append(messages)
        if self.responder is not None:
            return self.responder(messages)
        if not self.responses:
            raise LlmError("fake backend: no responses left")
        return self.responses.pop(0)


def autodetect(prefer: Optional[str] = None) -> Optional[LlmClient]:
    """환경변수 CXPE_LLM=geniex|native|none 또는 자동 탐지. 살아 있는 백엔드를 돌려준다.

    이전 표기 CXPE_LLM=rust 도 native 와 같게 받아들인다.
    """
    prefer = (prefer or os.environ.get("CXPE_LLM") or "auto").lower()
    if prefer == "none":
        return None
    candidates: list[LlmClient] = []
    genie = GenieXBackend(os.environ.get("CXPE_GENIEX_URL", "http://127.0.0.1:18181"))
    native = NativeBackend(os.environ.get("CXPE_NATIVE_URL",
                                          os.environ.get("CXPE_RUST_URL", "http://127.0.0.1:8091")))
    if prefer == "geniex":
        candidates = [genie]
    elif prefer in ("native", "rust"):
        candidates = [native]
    else:
        candidates = [genie, native]
    for c in candidates:
        if c.alive():
            return c
    return None
