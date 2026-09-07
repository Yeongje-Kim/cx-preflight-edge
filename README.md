# Cx-Preflight Edge

데이터센터 냉각설비 시운전 절차를 구조화하고, 엔지니어가 승인한 기준으로 측정값을 판정해 시험 기록 초안을 만드는 프로토타입입니다. 제1회 HIMEC AI 활용 아이디어 공모전의 **시공·품질관리** 분야 출품작입니다.

현재 예제는 냉동기 N+1 전환 시험 8단계입니다. 웹 화면은 합성 시계열을 재생하며, CLI에서는 같은 형식의 CSV도 사용할 수 있습니다. 실제 BMS/BACnet 실시간 수집과 PDF·이미지 OCR은 향후 연동 범위입니다.

## 주요 기능

1. **절차 검토**: 태그가 명시된 Markdown 절차서를 JSON 규칙으로 구조화합니다. 사전조건, 순서, 기대결과, 태그, 복구절차, 기준값의 6개 항목을 사전검증합니다.
2. **원문 대조와 승인**: AI가 추출한 태그·비교 연산자·수치·허용시간·유지시간을 지원 형식의 원문과 대조합니다. 불일치하면 재추출하고, 기준본으로 대체한 단계는 표시합니다. 엔지니어가 원문과 규칙을 검토하고 승인합니다.
3. **판정과 기록**: 규칙 엔진이 단계별 PASS·FAIL·ABORT·HOLD와 시간 초과 구간을 남깁니다. AI는 종합 의견과 후속 조치의 문안을 보조하며, 판정값과 측정 근거는 엔진 결과로 작성합니다.
4. **시험 이력 보존**: 한 세션은 한 번만 실행합니다. 실행 당시 승인 규칙을 별도로 저장하고, 재시험은 새 세션에서 시작합니다. 완료한 세션은 재승인·재실행할 수 없습니다.

AI 미연결 시에는 제공된 기준 규칙과 보고서 템플릿으로 기능을 시연할 수 있습니다. 이 모드는 AI 추론 검증에 해당하지 않습니다. 원문 대조의 지원 범위와 한계는 [추출 보완 기록](docs/source-extraction-fix.md)에 정리했습니다.

## 온디바이스를 사용하는 이유

절차서·설비 태그·측정 기록을 현장 보드에서 처리해 외부 클라우드로 자료를 보내야 하는 범위를 줄이고, 외부 연결에 대한 의존을 낮추는 것이 목적입니다. 엔지니어는 현장에서 규칙과 관측값, 판정 근거를 함께 확인할 수 있습니다.

보드에서는 Qwen3-4B-Instruct-2507을 로컬 LLM 서비스로 호출합니다. 로컬 AI 실행과 외부 인터페이스를 제거한 판정 시험은 각각 확인했으며, **AI를 포함한 전체 흐름의 완전 격리 시험은 아직 수행하지 않았습니다.** 비용 절감률이나 현장 업무시간 단축률도 아직 측정하지 않았습니다.

## 빠른 시작

Python 3.11 이상이 필요합니다. 소스 저장소를 내려받아 편집 가능 모드로 설치합니다. 아래 명령은 PC에서 LLM 없이 시연하는 구성입니다.

### Windows PowerShell

```powershell
git clone https://github.com/Yeongje-Kim/cx-preflight-edge.git
cd cx-preflight-edge
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
$env:CXPE_LLM = "none"
.\.venv\Scripts\python.exe -m cxpe.cli serve --host 127.0.0.1 --port 8080
```

### Linux

```bash
git clone https://github.com/Yeongje-Kim/cx-preflight-edge.git
cd cx-preflight-edge
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest
CXPE_LLM=none python -m cxpe.cli serve --host 127.0.0.1 --port 8080
```

브라우저에서 <http://127.0.0.1:8080>을 엽니다. **상황 선택 → 사전검증 시작 → 규칙 검토 확인 → 승인 → 시험 재생 → 시험 기록 확인** 순서입니다. 완료 후 준비 화면의 **새 시험 준비**로 다음 시험을 시작합니다. 이전 시험은 이력에 남습니다.

서버는 단일 프로세스로 실행합니다. 현재 프로토타입은 사용자 인증이나 여러 서버 프로세스 간 상태 공유를 제공하지 않습니다.

## CLI 재현

아래의 `python`은 설치한 가상환경의 실행 파일을 뜻합니다. Windows에서는 `.\.venv\Scripts\python.exe`로 바꿔 실행합니다.

```bash
python -m cxpe.cli preflight --plan data/golden/plan.json
python -m cxpe.cli preflight --plan data/defective/mixed_demo.json
python -m cxpe.cli replay --case fail_start
python -m cxpe.cli synth --case pass --out sessions/_verification/telemetry.csv
python -m cxpe.cli replay --csv sessions/_verification/telemetry.csv
python -m cxpe.metrics --out sessions/_verification/metrics-local.md
```

결함 예제의 사전검증 명령은 ERROR 2건을 발견해 종료 코드 2를 반환합니다. 이는 의도한 결과입니다. 판정·CSV 예제와 새 성능 집계는 `sessions/_verification/`에 저장하므로 기존 보드 측정 자료를 덮어쓰지 않습니다.

## 보드에서 AI 실행

확인한 장비는 Radxa Fogwise AIRbox Q900, Qualcomm IQ-9075 / QCS9075입니다. 모델은 Qwen3-4B-Instruct-2507(w4a16), 실행 환경은 Qualcomm QAIRT / GenieX입니다.

이 저장소에 모델 가중치와 NPU 런타임은 포함되어 있지 않습니다. 보드에 호환 런타임과 모델을 설치하고 로컬 서비스를 실행한 뒤 앱을 시작합니다.

```bash
source .venv/bin/activate
unset CXPE_LLM
python -m cxpe.cli serve --host 127.0.0.1 --port 8080
```

앱은 GenieX(127.0.0.1:18181), 네이티브 LLM 서비스(127.0.0.1:8091)를 순서대로 탐색합니다. 연결된 상태에서 화면의 AI 추출 모드를 선택합니다. `scripts/run_board.sh`는 현장 LAN 접속을 위한 별도 실행 도우미입니다. 네트워크 차단 시험은 관리 접속에도 영향을 줄 수 있으므로 [격리 시험 스크립트](scripts/airgap_test.sh)의 조건을 확인해 별도로 수행합니다.

## 시연 시나리오

| 케이스 | 확인할 결과 |
|---|---|
| preflight_warn | 기대결과·태그·복구절차 결함을 표시하고 ERROR 2건으로 승인 차단 |
| pass | 8단계 PASS |
| fail_start | 대기기 기동 지연으로 S5 FAIL |
| fail_temp | 온도 회복 지연으로 S7 FAIL |
| fail_dropout | 계측 결측으로 S7 HOLD, 종합 HOLD |

CLI와 지표 집계에서는 기동이 발생하지 않는 `no_start`도 확인합니다. 합성 데이터와 시간·온도 기준은 기능검증용이며, 실제 적용에는 프로젝트에서 승인한 시험계획서와 태그 목록이 필요합니다. 설비를 직접 제어하는 기능은 없습니다.

## 검증 자료

- [최신 제출 코드 검증](docs/submission-check.md): 2026-09-07 PC 자동화 테스트 87개, 브라우저 흐름 및 이력 보존 검사.
- [기존 보드 측정](docs/metrics.md): 2026-09-06 측정 기록. 합성 시계열 30회·240단계의 상태·사유코드·종료시각(±3초)을 대조했습니다. 당시 추출 F1 0.843은 원문 대조 보완 이전 결과입니다.
- [보드 원자료와 조건](docs/board/README.md): NPU 실행, 소켓 표본, LLM을 제외한 격리 판정 시험.
- [원문 대조 보완과 보드 확인](docs/source-extraction-fix.md): 2026-09-07 원문 수치 대조 보완 및 예제 1건의 AI 추출 8/8단계·기준본 대체 0건 확인. 범용 추출 정확도를 뜻하지 않습니다.

PC 회귀 테스트는 FakeBackend 또는 기준 규칙을 사용합니다. 이번 세션 보존 수정 후 NPU 추론과 완전 격리 환경을 새로 측정한 결과는 아닙니다.

## 코드 위치

| 경로 | 역할 |
|---|---|
| `cxpe/preflight.py` | 6개 항목 사전검증 |
| `cxpe/engine.py` | 상태·시간 조건 판정 |
| `cxpe/llm/` | 로컬 모델 호출, 구조화, 원문 대조, 기록 문안 |
| `cxpe/server.py`, `cxpe/sessions.py` | API, 실행 당시 규칙과 시험 결과 보관 |
| `web/` | 규칙 검토, 차트, 판정 근거, 시험 이력 |
| `data/`, `tests/` | 시연 입력과 회귀 테스트 |

## 라이선스

Apache-2.0. 외부 구성요소와 개별 라이선스는 [NOTICE](NOTICE)에 명시했습니다.
