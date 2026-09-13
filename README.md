# 분석 도구 모음

| 도구 | 설명 | 문서 |
| --- | --- | --- |
| `dispatch_order_analysis.py` | 한국 발전소 업체별 급전순위(merit order) 분석 | 아래 참조 |
| `daegyo_site_polygons.py` | 여의도 대교아파트 신축 배치도 → QGIS 일조권 분석용 건물 폴리곤 생성 | [초보자 매뉴얼](docs/daegyo_beginner_manual.md) · [실무 가이드](docs/daegyo_qgis_guide.md) |
| `hwarang_massing_study.py` | 여의도 화랑아파트 재건축 – 주변 학교 일조영향 최소화 배치안 도출 | [설계안 보고서](docs/hwarang_massing_design.md) |
| `hwarang_tower_shape_study.py` | 화랑아파트 1개동 – 최적 평면 형상(직사각형 방위) 도출 | [형상 검토 보고서](docs/hwarang_tower_shape.md) |
| `hwarang_site_placement.py` | 화랑아파트 1개동 – 법정 이격 내 최적 입지 도출 | [입지 검토 보고서](docs/hwarang_placement.md) |
| `hwarang_final_massing.py` | 화랑아파트 – 발코니 반영 최종 형상·배치도 산출 | [최종 배치 보고서](docs/hwarang_final_massing.md) |
| `hwarang_unit_blocks.py` | 화랑아파트 – 평형별 단위세대 평면 블럭(조합 최적화 준비) | [블럭 라이브러리](docs/hwarang_unit_blocks.md) |
| `hwarang_floor_plan_optimizer.py` | 화랑아파트 – 확정 배치도 내 세대 구성 최적화(최종 배치평면) | [최종 배치평면 보고서](docs/hwarang_floorplan.md) |
| `hwarang_stepped_height_study.py` | 화랑아파트 – 대교식 계단형(저층/고층 분할) 매싱의 일조권 효과 검증 | [계단형 매싱 검토 보고서](docs/hwarang_stepped_height.md) |
| `daegyo_school_sunlight.py` | 대교아파트 재건축 인가안 – 주변 학교 일조영향 정량 분석 | [학교 일조영향 보고서](docs/daegyo_school_sunlight.md) |

---

# 한국 발전소 업체별 급전순위 분석

한국 발전소/발전기 목록을 입력하면 단가를 감안한 급전순위(merit order)를 산출하고, 업체별 누계용량을 함께 확인할 수 있는 간단한 CSV 기반 분석 도구입니다.

## 분석 기준

기본 정렬 우선순위는 다음과 같습니다.

1. `단가` 오름차순: 낮은 단가 발전기를 우선 배치합니다.
2. `회사명` 오름차순: 동일 단가 내에서 업체명을 기준으로 정렬합니다.
3. `발전기명` 오름차순: 동일 업체 내 발전기명을 기준으로 정렬합니다.
4. `용량` 내림차순: 동일 단가/업체/발전기명인 경우 큰 용량을 우선합니다.

출력에는 전체 급전순위, 발전기별 용량, 전체 누계용량, 업체별 누계용량, 단가가 포함됩니다.

## 입력 CSV 형식

필수 컬럼은 아래 4개입니다. 한글 또는 영문 별칭을 사용할 수 있습니다.

| 표준 컬럼 | 허용 별칭 |
| --- | --- |
| 회사명 | `회사명`, `company`, `owner`, `업체명` |
| 발전기명 | `발전기명`, `generator`, `unit`, `plant`, `발전소명` |
| 용량 | `용량`, `capacity`, `mw`, `설비용량` |
| 단가 | `단가`, `price`, `unit_price`, `cost`, `변동비` |

예시는 `data/sample_generators.csv`를 참고하세요.

## 실행 방법

```bash
python3 dispatch_order_analysis.py data/sample_generators.csv --output dispatch_order.csv --company-summary company_summary.csv
```

## 출력 CSV 컬럼

### 급전순위 파일

| 컬럼 | 설명 |
| --- | --- |
| `급전순위` | 단가 기준 전체 급전순위 |
| `회사명` | 발전기 보유/운영 업체명 |
| `발전기명` | 발전기 또는 발전소명 |
| `용량` | 해당 발전기의 용량(MW 등 입력 단위 유지) |
| `누계용량` | 전체 급전순위 기준 누계용량 |
| `업체별누계용량` | 같은 업체 내 급전순위 기준 누계용량 |
| `단가` | 입력 단가 |

### 업체별 요약 파일

| 컬럼 | 설명 |
| --- | --- |
| `회사명` | 업체명 |
| `발전기수` | 업체별 발전기 수 |
| `총용량` | 업체별 총 용량 |
| `최소단가` | 업체 내 최저 단가 |
| `가중평균단가` | 용량 가중 평균 단가 |
| `최대단가` | 업체 내 최고 단가 |

## 주의사항

- 실제 급전은 계통 제약, 정비 상태, 연료 계약, 재생에너지 출력, 송전 제약 등 다양한 운영 조건을 반영할 수 있습니다.
- 이 도구는 입력된 단가와 용량만으로 업체별/발전기별 우선순위를 확인하는 분석용 템플릿입니다.

---

# 여의도 대교아파트 신축안 – QGIS 일조권 분석용 폴리곤 생성

사업시행계획인가 제원(대지면적 26,869.5㎡ / 건폐율 57.02% / 용적률 469.99% / 4개동 912세대)과
첨부 단지배치도를 근거로, 101~104동 타워·저층부 건물 폴리곤을 실좌표(EPSG:5186)로 생성합니다.

```bash
pip install pyproj shapely
python3 daegyo_site_polygons.py
```

출력: `outputs/daegyo/` (GeoJSON 5186·WGS84, CSV+WKT, 트레이싱 검수 SVG, 인가제원 검증 리포트)

**재건축안의 학교 일조영향**: 인가안(최고 48층) 신축 시 주변 4개 학교의 동지일
일조 충족률이 **88.4% → 82.8%(−5.6%p)**, 교실 창면 38개소가 신규 불충족으로
전환됩니다. 최대 피해는 여의도여자고등학교(−9.3%p)이며, 주원인은 가장 높은
101·102동(48층)이 아니라 **학교와 남남서 방위선상에 놓인 104·103동**입니다.
[docs/daegyo_school_sunlight.md](docs/daegyo_school_sunlight.md) 참조.

- **파이썬·QGIS를 처음 쓰신다면** → [docs/daegyo_beginner_manual.md](docs/daegyo_beginner_manual.md)
  (설치부터 메뉴 클릭 단위로 설명. 파이썬 없이 QGIS만으로 하는 방법 포함)
- 옵션·좌표 보정·분석 절차 요약 → [docs/daegyo_qgis_guide.md](docs/daegyo_qgis_guide.md)
- 인접 화랑아파트 일조권 분석용 파일 추출 → [docs/hwarang_sunlight_analysis.md](docs/hwarang_sunlight_analysis.md)

---

# 여의도 화랑아파트 재건축 – 학교 일조영향 최소화 배치안

GIS건물통합정보(AL_D010)와 대교아파트 신축 폴리곤을 조합하여, 준주거 용적률 400% /
건폐율 60% 범위에서 주변 학교 교실 창면의 동지일 일조를 최대로 확보하는 배치안을
정량 비교합니다.

```bash
python3 hwarang_massing_study.py --buildings <AL_D010.gpkg> --outdir outputs/hwarang
```

**결론**: 탑상형 1개동 · 55층 · 기준층 683㎡ · 대지 남동측 배치 (용적률 400% 충족).
가장 가까운 학교의 일조 충족률이 현황 64.9% → 84.8%로 **개선**됩니다.
근거와 대안 비교는 [docs/hwarang_massing_design.md](docs/hwarang_massing_design.md) 참조.

평면 형상은 **직사각형 29.2 × 23.4 m, 장변 방위 345°** 가 최적입니다(198개 형상 비교).
[docs/hwarang_tower_shape.md](docs/hwarang_tower_shape.md) 참조.

입지는 **E194,319.2 / N546,962.7**(대지 중심에서 동측 16.4m, 경계 이격 12.0m)이 최적입니다.
[docs/hwarang_placement.md](docs/hwarang_placement.md) 참조.

발코니 1.5m를 반영한 **최종안은 직사각형 2:1(내부 36.97 × 18.48m), 장변 방위 345°, 55층**입니다.
용적률 400.0% · 건폐율 7.67% · 서비스면적 6,100㎡ · 학교 일조 현황 대비 +3.1%p.
[docs/hwarang_final_massing.md](docs/hwarang_final_massing.md) 참조.

평형별 단위세대 블럭(84A·84B·88A·93A·102A·105A·116A)은 단위세대 평면도 치수열에서
생성했습니다. 기준층 조합 최적화의 입력으로 쓰며, 실좌표 캔버스는 확정 연면적선과
99.97% 일치합니다. [docs/hwarang_unit_blocks.md](docs/hwarang_unit_blocks.md) 참조.

확정 배치도(683.3㎡) 안에 평형 블럭을 채우는 최적화 결과: **1개층 102A×4세대**가
전용면적 비율(59.7%)을 최대화하는 해입니다(무제한 배낭 알고리즘 + 전수열거로 검증).
[docs/hwarang_floorplan.md](docs/hwarang_floorplan.md) 참조.

대교아파트식 "동별 계단형(저층/고층 분할)" 매싱을 검토한 결과, **1개동인
화랑아파트에는 유의미한 일조권 개선 효과가 없습니다**(분할비·저층수·방향
92가지 조합 전수 검증, 균일 55층 대비 최대 ±0.1%p). 확정 균일 55층안을
그대로 유지할 것을 권장합니다. [docs/hwarang_stepped_height.md](docs/hwarang_stepped_height.md) 참조.
