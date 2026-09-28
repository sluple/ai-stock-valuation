# AI 주식 가치 분석기

DART(전자공시시스템) 재무제표를 자동으로 읽어와 DCF·상대가치평가로 적정주가를 계산하고, Claude API가 그 결과를 비전공자도 이해할 수 있게 풀어서 설명해주는 웹 대시보드입니다.

## 주요 기능

- OpenDART API로 재무제표 자동 수집 및 계정과목 표준화
- DCF 밸류에이션 (2가지 방식으로 교차검증)
- 동일 업종 피어와 PER/PBR/EV-EBIT 상대가치평가
- Claude API 기반 AI 리포트 생성 (숫자는 코드가 계산, LLM은 해설만 담당)
- Supabase에 분석 기록 저장 및 조회

## 로컬 실행

```bash
pip install -r requirements.txt
cd src
streamlit run app.py
```

## 환경변수 (.env)

`.env.example`을 복사해 `.env`를 만들고 아래 값을 채워주세요.

| 변수 | 설명 | 발급처 |
|---|---|---|
| `OPENDART_API_KEY` | 재무제표 조회용 | https://opendart.fss.or.kr (이메일 인증만으로 무료 발급) |
| `ANTHROPIC_API_KEY` | AI 리포트 생성용 | https://console.anthropic.com |
| `SUPABASE_URL` | 분석 기록 저장용 (선택) | Supabase 프로젝트 설정 > API |
| `SUPABASE_KEY` | 분석 기록 저장용 (선택) | Supabase 프로젝트 설정 > API |

Supabase 관련 변수가 없어도 대시보드의 나머지 기능은 정상 동작하고, "지난 분석 기록" 탭만 비활성화됩니다.

## Supabase 테이블 생성

Supabase 프로젝트의 SQL Editor에서 아래를 실행하세요.

```sql
create table if not exists analyses (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  corp_name text not null,
  stock_code text not null,
  current_price numeric,
  wacc numeric,
  dcf_value_a numeric,
  dcf_value_b numeric,
  per_implied_value numeric,
  pbr_implied_value numeric,
  ev_ebit_implied_value numeric,
  peer_codes text,
  report_text text,
  raw_context jsonb
);
```

## Railway 배포

1. 이 저장소를 GitHub에 올린 뒤 Railway에서 "New Project" → "Deploy from GitHub repo"로 연결
2. Railway 프로젝트 설정의 Variables에 위 환경변수 4개를 등록
3. `Procfile`을 자동으로 인식해 `streamlit run` 명령으로 배포됩니다 (별도 빌드 설정 불필요)

## 프로젝트 구조

```
src/
  fetch_dart.py       DART API 연동 (기업 검색, 재무제표, 주식총수)
  normalize.py        계정과목 표준화 (회사/연도별 계정명 불일치 처리)
  ratios.py           재무비율 계산
  dcf.py              DCF 밸류에이션 (2가지 방식)
  relative_valuation.py  PER/PBR/EV-EBIT 상대가치평가
  market_data.py      실시간 주가 조회
  llm_report.py       Claude API 기반 리포트 생성
  db.py               Supabase 저장/조회
  analysis.py         전체 파이프라인 통합 (CLI/대시보드 공용)
  main.py             CLI 실행 진입점
  app.py              Streamlit 대시보드
```
