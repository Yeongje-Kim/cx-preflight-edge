# Cx-Preflight Edge

폐쇄망 데이터센터 통합시운전(IST/FPT) 시험절차를 **실행 전에 검증**하고, 승인된 같은 규칙으로 **시험 중 BMS 텔레메트리를 판정**해 근거 타임라인과 한국어 시험 기록 초안을 만드는 온디바이스 시스템이다. 대상 보드는 Radxa Airbox Q900(Qualcomm QCS9075, Qwen3-4B on NPU core 1)이며, 외부 클라우드 연결 없이 동작한다.

제1회 HIMEC AI 활용 아이디어 공모전 출품작 (분야 ② 시공·품질관리).

## 무엇이 다른가

| | 이 시스템 |
|---|---|
| 생성이 아니라 **승인 전 검증** | 시험절차서를 구조화한 뒤 사전조건 누락·순서 모순·기대결과 누락·태그 미매핑·복구절차 누락·기준값 충돌 6가지를 결정적으로 검사한다 |
| 체크오프가 아니라 **텔레메트리 근거 판정** | 엔지니어가 승인한 규칙을 1Hz 시계열에 대조해 단계별 PASS/FAIL/ABORT와 허용시간 초과 구간을 남긴다 |
| 단순 오프라인이 아니라 **폐쇄망 AI + 동일 승인 규칙** | 추출·매핑·모순 후보·보고서는 온디바이스 LLM이, 판정은 규칙 엔진이, 최종 판단은 엔지니어가 맡는다 |

조사한 대표 상용제품(CxPlanner CxAI, PingCx, Vitralogy SmartMOP, CxAlloy, NIST HVAC-Cx)의 공개 자료에서는 이 세 가지를 하나의 폐쇄망 시스템에서 제공하는 동일 구성을 확인하지 못했다.

## 역할 분리

- **AI(온디바이스 LLM)**: 절차서 → JSON 구조화, 태그 매핑 제안, 모순 후보, 시험 기록 종합 의견
- **규칙·시계열 엔진**: Preflight 6검사, 단계별 Pass/Fail·시간 조건 판정, 근거 캡처
- **엔지니어**: 기준값·규칙 승인, 최종 판단

AI는 합격·불합격을 결정하지 않는다. LLM 출력은 스키마 검증과 근거 대조를 거치며, 실패하면 승인본(골든)으로 폴백한다.

## 빠른 시작 (PC, LLM 없이)

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"   # Windows: .venv/Scripts/pip
make test                                                    # pytest 55개
python -m cxpe.cli preflight --plan data/defective/mixed_demo.json
python -m cxpe.cli replay --case fail_start
python -m cxpe.metrics --out docs/metrics.md
make serve                                                   # http://127.0.0.1:8080
```

## 보드 실행

```bash
git clone <this repo> && cd cx-preflight-edge
python3 -m venv .venv && .venv/bin/pip install -e .
./scripts/run_board.sh              # LLM 자동 탐지: GenieX(18181) → Rust llm 서비스(8091)
./scripts/wan_off.sh                # 데모: 기본 경로 제거 → 화면 배지 "External Cloud Connection: OFF"
./scripts/smoke.sh                  # 3케이스 자동 점검
```

온디바이스 LLM 런타임(Qualcomm Genie, NPU 코어 1)은 보드에 미리 설치되어 있어야 한다. 이 저장소는 런타임을 포함하지 않고 로컬 HTTP로 호출만 한다.

## 데모 시나리오 (합성 SIL 데이터)

| 케이스 | 절차서 | 텔레메트리 | 기대 결과 |
|---|---|---|---|
| preflight_warn | 결함 주입본 | — | PF03·PF04·PF05 경고, ERROR 2건으로 승인 차단 |
| pass | 승인본 | 정상 | 8단계 PASS |
| fail_start | 승인본 | 대기기 기동 45초 지연 | S5 FAIL(STANDBY_START_TIMEOUT), 초과 구간 58→72s, 이후 SKIPPED |
| fail_temp | 승인본 | 회복 시정수 60s | S7 FAIL(TEMP_RECOVERY_TIMEOUT) |
| fail_dropout | 승인본 | 공급온도 10초 결측 | S7 FAIL(TAG_MISSING_DATA) |

모든 텔레메트리는 `cxpe/synth.py`가 만든 합성 데이터이며 정답 라벨을 함께 낸다. 시간·온도 기준은 기능검증용 임의 값이다. 실제 적용 시 프로젝트별 승인 시험계획서의 기준을 사용한다.

## 구조

```
cxpe/schemas.py    TestPlan/Step/Cond/Expected, Finding, Verdict, Segment
cxpe/preflight.py  PF01~PF06 결정적 검사
cxpe/engine.py     단계 상태기계 (PENDING→ARMED→RUNNING→PASS|FAIL|ABORT, SKIPPED)
cxpe/synth.py      냉동기 N+1 전환 합성 시계열 + 해석적 정답 라벨
cxpe/llm/          client(GenieX/Rust/Fake), prompts, extract, report
cxpe/server.py     FastAPI: 세션 → Preflight → 승인 → 재생(SSE) → 보고서
web/               라이브 화면(uPlot vendored), 히스토리
data/              시험절차서 원문, SOO 발췌, 태그리스트, 골든 JSON, 결함 변형 12개
docs/metrics.md    검증 결과 (python -m cxpe.metrics)
```

## 검증 결과

`docs/metrics.md` 참고. 합성 데이터 기준으로 Preflight 정밀도·재현율, 단계 판정 정확도, 판정 지연을 기록한다. 보드에서 LLM 추출을 실행하면 골든 대비 추출 F1을 추가로 산출한다.

## 라이선스

Apache-2.0. 제3자 구성요소와 선행 프로젝트 크레딧은 NOTICE에 있다.
