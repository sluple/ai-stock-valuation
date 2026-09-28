# AI 탄소배출량(Scope 3) 추정기

기업의 재무데이터(매출액·매출원가)만으로 온실가스 배출량을 추정하는 도구입니다. 특히
**Scope 3(협력사·물류·제품 사용 등 공급망 전체 배출량)**는 전체 배출량의 70~90%를
차지함에도 측정이 거의 불가능해 대부분 기업이 공시하지 않는데, 이 프로젝트는 실제
배출권거래제(K-ETS) 공시 데이터로 학습한 머신러닝 모델로 이를 추정합니다.

## 문제의식

- ESG 공시(KSSB 1호·2호 등)가 의무화되고 있지만, Scope 3는 측정 방법 자체가 난제
- 환경투입산출모델(EEIO, 미국 EPA 등이 실제로 쓰는 방식)을 참고해 "매출액 대비 배출집약도"를
  업종별로 학습하면, 공시하지 않은 기업도 대략적인 추정치를 낼 수 있음

## 데이터 출처

- **재무데이터**: OpenDART API (전자공시시스템)
- **배출량 학습 데이터**: 온실가스종합정보센터 배출권거래제(K-ETS) 인증 배출량 공개 데이터
  (data.go.kr, 법인명·배출량(tCO2eq) 포함, 약 780개 사업장 중 재무데이터와 매칭된 약 600여개 기업으로 학습)

## 방법론

1. **기업명 매칭**: K-ETS 공시 데이터의 법인명(예: "에스케이하이닉스주식회사")을 정규화해
   DART 기업명(예: "SK하이닉스")과 매칭 — 회사형태 표기 제거 + 그룹명 음차 변환
2. **Scope 1+2 추정**: 두 가지 방법을 함께 계산해 교차검증
   - 회귀모델(RandomForest): 매출액·매출원가·업종 → 배출량 예측
   - 업종평균(EEIO 방식): 같은 업종 기업들의 "배출량/매출액" 중앙값 적용
3. **Scope 3 확장**: CDP 조사의 "Scope3가 전체 배출량의 평균 75%" 기준을 적용해 Scope1+2의
   약 3배로 추정 (전산업 평균이라는 단순화 가정을 명시)
4. **AI 리포트**: Claude API가 위 결과를 비전공자도 이해할 수 있게 풀어서 설명 (숫자는
   전부 코드가 계산, LLM은 해석만 담당해 할루시네이션 위험을 차단)

## 로컬 실행

```bash
pip install -r requirements.txt
cd src

# 1) 학습 데이터 구축 (K-ETS 배출량 + DART 재무데이터 매칭, 약 10~20분 소요)
python build_emissions_dataset.py

# 2) 모델 학습
python train_model.py

# 3) 대시보드 실행
streamlit run app.py
```

## 환경변수 (.env)

| 변수 | 설명 | 발급처 |
|---|---|---|
| `OPENDART_API_KEY` | 재무제표 조회용 | https://opendart.fss.or.kr (이메일 인증만으로 무료 발급) |
| `ANTHROPIC_API_KEY` | AI 리포트 생성용 | https://console.anthropic.com |
| `SUPABASE_URL` | 분석 기록 저장용 (선택) | Supabase 프로젝트 설정 > API |
| `SUPABASE_KEY` | 분석 기록 저장용 (선택) | Supabase 프로젝트 설정 > API |

## Supabase 테이블 생성

```sql
create table if not exists emissions_estimates (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  corp_name text not null,
  stock_code text,
  industry text,
  industry_source text,
  revenue numeric,
  actual_scope12 numeric,
  model_estimate_scope12 numeric,
  benchmark_estimate_scope12 numeric,
  scope3_estimate numeric,
  report_text text,
  raw_context jsonb
);

alter table emissions_estimates enable row level security;
create policy "public insert" on emissions_estimates for insert to anon with check (true);
create policy "public select" on emissions_estimates for select to anon using (true);
create policy "public update" on emissions_estimates for update to anon using (true) with check (true);
```

## Railway 배포

1. 이 저장소를 GitHub에 올린 뒤 Railway에서 "New Project" → "Deploy from GitHub repo"로 연결
2. Railway 프로젝트 설정의 Variables에 위 환경변수 4개를 등록
3. `data/emissions_training_data.csv`, `data/emissions_model.pkl`은 미리 학습해서 저장소에
   커밋해두어야 배포 환경에서 바로 동작함 (배포 서버에서 재학습하지 않음)
4. `Procfile`을 자동으로 인식해 `streamlit run` 명령으로 배포됨

## 프로젝트 구조

```
src/
  fetch_dart.py             DART API 연동 (기업 검색, 재무제표)
  normalize.py              계정과목 표준화 (매출액/매출원가 등)
  emissions_data.py         K-ETS 배출량 데이터 로드 + DART 기업명 매칭
  emissions_model.py        회귀모델 학습/예측 + Scope 3 확장 로직
  build_emissions_dataset.py  학습 데이터셋 구축 스크립트 (1회 실행)
  train_model.py            모델 학습 스크립트 (1회 실행)
  llm_report.py             Claude API 기반 리포트 생성
  db.py                     Supabase 저장/조회
  analysis.py               전체 파이프라인 통합 (CLI/대시보드 공용)
  main.py                   CLI 실행 진입점
  app.py                    Streamlit 대시보드
data/
  emissions_raw.csv          K-ETS 원본 배출량 데이터
  emissions_training_data.csv  매칭+결합된 학습 데이터셋
  emissions_model.pkl         학습된 모델 (커밋 대상)
```

## 모델의 한계 (투명하게 공개)

- Scope 3 배율(x3)은 전산업 평균이며 업종별 정교화는 향후 과제
- 학습 데이터가 K-ETS 대상(주로 제조·에너지·폐기물 등 배출 다배출 업종)에 편중되어,
  서비스업 등 분포 밖 기업에서는 정확도가 낮을 수 있음
- 매출원가 미공시 기업은 매출액의 70%로 임의 대체
