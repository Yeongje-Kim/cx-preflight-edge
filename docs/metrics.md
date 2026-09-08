# 검증 결과 (합성 SIL 데이터 기준)

출품작: **온디바이스 AI 기반 데이터센터 시운전 검증·기록 시스템**

2026-09-06 보드 측정 기록입니다. 아래 F1·추출 시간은 원문 대조 보완 이전 결과이며 최신 코드 성능으로 재해석하지 않습니다. 격리 네임스페이스 시험은 LLM을 제외한 판정·템플릿 경로입니다. 최신 PC 회귀는 [제출 코드 검증](submission-check.md), 후속 AI 확인은 [원문 대조 보완](source-extraction-fix.md)을 참고하세요.

> 모든 수치는 기능검증용 합성 데이터와 임의 기준값에서 측정한 값이다. 실제 현장 성능을 뜻하지 않는다.

## 1. Preflight 검출

- 대상: 결함 주입 절차서 12개 + 정상 절차서 1개
- 정밀도 1.000, 재현율 1.000 (TP 14, FP 0, FN 0)
- 정상 절차서 오탐: 0건

| 변형 | 기대 코드 | 검출 코드 | 일치 |
|---|---|---|---|
| mixed_demo | PF03_NO_EXPECTED_RESULT, PF04_TAG_UNMAPPED, PF05_MISSING_ROLLBACK | PF03_NO_EXPECTED_RESULT, PF04_TAG_UNMAPPED, PF05_MISSING_ROLLBACK | O |
| pf01_s3_no_precond | PF01_MISSING_PRECONDITION | PF01_MISSING_PRECONDITION | O |
| pf01_s5_no_precond | PF01_MISSING_PRECONDITION | PF01_MISSING_PRECONDITION | O |
| pf02_s5_before_s4 | PF02_ORDER_CONTRADICTION | PF02_ORDER_CONTRADICTION | O |
| pf02_s7_precond_ch2_off | PF02_ORDER_CONTRADICTION | PF02_ORDER_CONTRADICTION | O |
| pf03_s4_no_expected | PF03_NO_EXPECTED_RESULT | PF03_NO_EXPECTED_RESULT | O |
| pf03_s6_no_expected | PF03_NO_EXPECTED_RESULT | PF03_NO_EXPECTED_RESULT | O |
| pf04_s2_tag_typo | PF04_TAG_UNMAPPED | PF04_TAG_UNMAPPED | O |
| pf04_s7_tag_renamed | PF04_TAG_UNMAPPED | PF04_TAG_UNMAPPED | O |
| pf05_s2_no_rollback | PF05_MISSING_ROLLBACK | PF05_MISSING_ROLLBACK | O |
| pf05_s3_no_rollback | PF05_MISSING_ROLLBACK | PF05_MISSING_ROLLBACK | O |
| pf06_s7_band_10_12 | PF06_THRESHOLD_CONFLICT | PF06_THRESHOLD_CONFLICT | O |

## 2. 단계 판정 정확도

- 시나리오 5종 × 시드 → 30회 재생, 단계 240건
- 정확도 1.000 (상태·사유코드·종료시각 ±3초 모두 일치)

| 케이스 | 단계 | 일치 |
|---|---|---|
| fail_dropout | 48 | 48 |
| fail_start | 48 | 48 |
| fail_temp | 48 | 48 |
| no_start | 48 | 48 |
| pass | 48 | 48 |

- 판정 지연(ms, 샘플 수신→판정 생성): 평균 0.030, p50 0.024, p95 0.064, 최대 0.107

## 3. LLM 추출 (골든 대비)

- 정밀도 0.843, 재현율 0.843, F1 0.843 (골든 항목 51, 추출 항목 51)

| 필드 | 골든 | 추출 | 일치 | 정밀도 | 재현율 |
|---|---|---|---|---|---|
| abort | 5 | 5 | 5 | 1.00 | 1.00 |
| changes_state | 8 | 8 | 7 | 0.88 | 0.88 |
| expected | 9 | 9 | 8 | 0.89 | 0.89 |
| precondition | 10 | 10 | 10 | 1.00 | 1.00 |
| rollback | 9 | 9 | 3 | 0.33 | 0.33 |
| title | 8 | 8 | 8 | 1.00 | 1.00 |
| trigger | 2 | 2 | 2 | 1.00 | 1.00 |

누락(일부): ('S2', 'expected', 'LOAD_KW >= 200', 30.0, 10.0); ('S2', 'rollback', '부하기 출력을 0 kW로 하강'); ('S3', 'rollback', '트립 접점 복구'); ('S5', 'rollback', 'CH-2 수동 정지'); ('S6', 'rollback', 'CHWP-2 수동 정지'); ('S7', 'changes_state', False); ('S7', 'rollback', 'CH-1 수동 재기동'); ('S8', 'rollback', '수동 운전 전환')

## 4. 보드 실행 지표

- 측정 장비: Radxa Fogwise AIRbox Q900 (Qualcomm IQ-9075 / QCS9075), Ubuntu 24.04.3 LTS, aarch64 8코어, RAM 35,213 MB
- 온디바이스 LLM: Qwen3-4B-Instruct-2507 (w4a16), GenieX 서버 127.0.0.1:18181, Qualcomm QAIRT 2.45.0
- LLM 추출 시간: 절차서 8단계 76.4초 (2회 평균, 단계당 약 9.6초, 재시도 포함)
- LLM 폴백: 0건 (8/8 단계가 스키마 검증을 통과, 승인본 대체 없음)
- 판정 지연 (보드): 평균 0.030 ms, p50 0.024 ms, p95 0.066 ms, 최대 0.107 ms
- 서버 메모리: 판정 서버 프로세스 RSS 50 MB
- 폐쇄망 검증 방식: 루프백만 존재하는 네트워크·마운트 네임스페이스에서 전체 세션 실행 (사전검증 → 승인 → 판정 → 보고서)
- 폐쇄망 결과: 외부 인터페이스 0개, DNS 차단, TCP 연결 차단, 기본 경로 없음, 송신 바이트 0 → is_offline 4/4
- LLM 경로 외부 송신: 추출 76초 동안 0.4초 간격 1,778회 소켓 표본에서 관측된 TCP 상대 주소는 전부 127.0.0.1, 루프백 아닌 주소 0건
- 측정일: 2026-09-06
