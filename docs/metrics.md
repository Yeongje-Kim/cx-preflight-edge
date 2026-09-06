# 검증 결과 (합성 SIL 데이터 기준)

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

- 판정 지연(ms, 샘플 수신→판정 생성): 평균 0.016, p50 0.013, p95 0.034, 최대 0.048

## 3. LLM 추출 (골든 대비)

- 미측정 (보드에서 LLM 추출 세션 실행 후 --extraction 옵션으로 산출)
