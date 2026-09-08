# 보드 실측 원자료 (2026-09-06)

최신 세션 보존·문안 보완 코드의 실제 NPU 결과는 [후속 재검증](recheck-2026-09-07/README.md)에 있습니다. 아래 자료는 당시 측정 상태를 보존합니다.

Radxa Fogwise AIRbox Q900 (Qualcomm IQ-9075 / QCS9075), Ubuntu 24.04.3 LTS, aarch64 8코어, RAM 35,213 MB.
온디바이스 LLM은 Qwen3-4B-Instruct-2507(w4a16)을 GenieX 서버(127.0.0.1:18181)로 띄워 사용했다.
집계 결과는 `../metrics.md` 4절에 있다.

| 파일 | 내용 | 재현 명령 |
|---|---|---|
| `plan.json` | 온디바이스 LLM이 시험절차서에서 추출한 구조화본 8단계 | `python -m cxpe.cli extract --md data/plan_n1_cooling.md --llm auto --out sessions/_extract` |
| `extract_stats.json` | 단계별 시도 횟수·소요 시간·폴백 여부 | 위와 같음 |
| `extraction_f1.json` | 승인본 대비 필드별 정밀도·재현율·F1과 누락 목록 | `python -m cxpe.metrics --extraction sessions/_extract/plan.json` |
| `airgap_namespace.out` | 루프백만 존재하는 네트워크·마운트 네임스페이스에서 전체 세션을 돌린 원본 출력 | `./scripts/airgap_test.sh` |
| `socket_probe.out` | LLM 추출 중 0.4초 간격으로 수집한 TCP 상대 주소 집계 | `./scripts/socket_probe.sh` |
| `board_extra.json` | `metrics.md` 4절에 들어간 항목 원본 | — |

## 읽는 법

이 자료는 2026-09-06 측정 당시 상태를 보존한 것입니다. 원문 대조와 세션 보존을 보완한 최신 코드의 재측정 결과는 아닙니다.

- **추출 F1 0.843은 완전일치 채점이다.** 복구 문구의 문자열 차이뿐 아니라 S2 기대조건의 허용시간·유지시간 불일치도 포함한다. 시간 조건은 판정에 영향을 주므로 단순 문체 차이로 볼 수 없다. 후속 원문 대조 수정과 재검증은 [추출 보완 기록](../source-extraction-fix.md)을 참고한다.
- **판정 지연은 규칙 엔진 구간만** 잰다. 샘플 수신부터 판정 생성까지이며 LLM은 이 경로에 없다.
- **폐쇄망 증거 두 가지는 성격이 다르다.** 네임스페이스 시험은 LLM을 끈 상태에서 기준 규칙·판정·템플릿 기록 경로가 완결됨을 보인다. AI까지 포함한 전체 흐름의 완전 격리 검증은 아니다. 소켓 표본은 LLM을 포함한 실제 실행에서 상대 주소가 루프백뿐임을 보인다.
- `socket_probe.out` 마지막 줄의 WAN 송신 증가분은 **원격 접속(SSH) 관리 트래픽이 포함된 값**이다. 인터페이스 카운터만으로 앱의 송신량을 분리할 수 없다. 소켓 상대 주소의 표본 관측은 보조 근거이며 전 구간 무송신을 증명하는 패킷 전수 검사는 아니다.
