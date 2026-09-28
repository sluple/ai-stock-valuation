"""AI 탄소배출량(Scope 1/2/3) 추정 대시보드 (Streamlit).

실행: streamlit run app.py
"""
import plotly.graph_objects as go
import streamlit as st

import analysis
import db
from llm_report import generate_report

st.set_page_config(page_title="AI 탄소배출량 추정기", page_icon="chart", layout="wide")

COLOR_ACTUAL = "#059669"    # 실제 공시값 (신뢰도 높음 — 초록)
COLOR_MODEL = "#2563EB"     # 회귀모델 추정치
COLOR_BENCHMARK = "#F97316"  # 업종평균 추정치
COLOR_SCOPE3 = "#6B7280"    # Scope 3 (중립 회색)

GLOSSARY = [
    ("Scope 1", "회사가 직접 태워서 나오는 배출 (공장 보일러, 회사 차량 연료 등)."),
    ("Scope 2", "회사가 전기를 사서 쓰는 데서 간접적으로 나오는 배출."),
    ("Scope 3", "협력사·물류·제품 사용 등 회사 울타리 밖 공급망 전체에서 나오는 배출. 측정이 어려워 대부분 기업이 공시를 안 함."),
    ("tCO2eq", "이산화탄소로 환산한 온실가스 배출량 단위 (톤)."),
    ("배출집약도", "매출 1억원당 배출되는 온실가스 양. 업종마다 크게 다름 (철강·시멘트는 높고 서비스업은 낮음)."),
    ("EEIO(환경투입산출모델)", "매출·구매비용 같은 경제 활동 데이터로 온실가스 배출량을 추정하는 방법론. 미국 EPA 등이 실제로 쓰는 방식."),
]


def fmt_ton(x: float | None) -> str:
    return f"{x:,.0f} tCO2eq" if x is not None else "정보 없음"


st.title("AI 탄소배출량 추정기")
st.caption(
    "기업의 재무데이터(매출액·매출원가)로 Scope 1+2 배출량을 추정하고, "
    "측정이 거의 불가능한 Scope 3(공급망 전체 배출량)까지 확장 추정합니다."
)

with st.expander("어려운 용어가 있으면 여기를 눌러 확인하세요"):
    for term, desc in GLOSSARY:
        st.markdown(f"**{term}** — {desc}")

if "sb_client" not in st.session_state:
    st.session_state.sb_client = db.create_client()
sb = st.session_state.sb_client

try:
    industries = analysis.list_known_industries()
except FileNotFoundError:
    industries = []
    st.error("학습된 모델이 없습니다. `python build_emissions_dataset.py` 후 `python train_model.py`를 먼저 실행하세요.")

with st.sidebar:
    st.header("분석하고 싶은 회사")
    target_code = st.text_input("종목코드", value="005930", help="예: 삼성전자 = 005930")
    industry_choice = st.selectbox(
        "업종 (배출권거래제 공시 대상 기업은 자동 감지되므로 그대로 두세요)",
        ["(자동 감지)"] + industries,
    )
    run_button = st.button("분석 시작", type="primary", width="stretch")

if "result" not in st.session_state:
    st.session_state.result = None
    st.session_state.error = None

if run_button:
    industry_override = None if industry_choice == "(자동 감지)" else industry_choice
    with st.spinner("재무데이터를 불러오고 배출량을 추정하는 중이에요..."):
        try:
            st.session_state.result = analysis.run_analysis(target_code, industry_override=industry_override)
            st.session_state.error = None
            st.session_state.report_text = None
            st.session_state.report_key = None
            st.session_state.record_id = None
            try:
                context = analysis.build_llm_context(st.session_state.result)
                st.session_state.record_id = db.save_estimate(sb, st.session_state.result, None, context)
            except Exception:  # noqa: BLE001
                pass
        except ValueError as e:
            st.session_state.result = None
            if "업종을 직접 선택" in str(e):
                st.session_state.error = "이 기업은 배출권거래제 공시 대상이 아니에요. 왼쪽에서 업종을 직접 선택하고 다시 눌러주세요."
            else:
                st.session_state.error = str(e)
        except Exception as e:  # noqa: BLE001
            st.session_state.result = None
            st.session_state.error = f"예상치 못한 오류: {e}"

if st.session_state.error:
    st.error(st.session_state.error)

result = st.session_state.result

if result is None:
    st.info("왼쪽에서 종목코드를 입력하고 '분석 시작'을 눌러주세요.")
    st.stop()

corp = result.corp
st.subheader(f"{corp['corp_name']} ({corp['stock_code'] or '비상장'})")
st.caption(f"업종: {result.industry}  ·  {result.industry_source}")

for w in result.warnings:
    st.warning(w)

col1, col2, col3, col4 = st.columns(4)
col1.metric("매출액", f"{result.financials.get('매출액', 0):,.0f}원")
scope12_display = result.actual_scope12 if result.actual_scope12 is not None else result.model_estimate
scope12_label = "Scope 1+2 (실제 공시)" if result.actual_scope12 is not None else "Scope 1+2 (모델 추정)"
col2.metric(scope12_label, fmt_ton(scope12_display))
col3.metric("Scope 3 (추정)", fmt_ton(result.scope3_estimate))
col4.metric(
    "총 배출량 추정",
    fmt_ton(scope12_display + result.scope3_estimate),
    help="Scope1+2 + Scope3 합계",
)

tab1, tab2, tab3, tab4 = st.tabs(["배출량 추정 결과", "모델 설명", "AI 리포트", "최근 분석 기록"])

with tab1:
    st.markdown("#### Scope 1+2 추정 방법 비교")
    labels = []
    values = []
    colors = []
    if result.actual_scope12 is not None:
        labels.append("실제 공시값")
        values.append(result.actual_scope12)
        colors.append(COLOR_ACTUAL)
    labels.append("회귀모델 추정")
    values.append(result.model_estimate)
    colors.append(COLOR_MODEL)
    labels.append("업종평균 추정")
    values.append(result.benchmark_estimate)
    colors.append(COLOR_BENCHMARK)

    fig1 = go.Figure(
        go.Bar(x=labels, y=values, marker_color=colors, text=[fmt_ton(v) for v in values], textposition="outside")
    )
    fig1.update_layout(yaxis_title="tCO2eq", margin=dict(t=30, b=20), height=380, showlegend=False)
    st.plotly_chart(fig1, width="stretch")

    st.markdown("#### Scope 1+2 vs Scope 3 비교")
    base = scope12_display
    fig2 = go.Figure(
        go.Bar(
            x=["Scope 1+2", "Scope 3 (추정)"],
            y=[base, result.scope3_estimate],
            marker_color=[COLOR_MODEL, COLOR_SCOPE3],
            text=[fmt_ton(base), fmt_ton(result.scope3_estimate)],
            textposition="outside",
        )
    )
    fig2.update_layout(yaxis_title="tCO2eq", margin=dict(t=30, b=20), height=380, showlegend=False)
    st.plotly_chart(fig2, width="stretch")
    st.caption(
        "Scope 3는 CDP(글로벌 ESG 공시기구) 조사에서 나온 '전산업 평균 Scope3 비중(약 75%)'을 적용한 값이에요. "
        "즉 Scope1+2의 약 3배로 계산했고, 업종별 정교화는 아직 안 되어 있어요."
    )

with tab2:
    st.markdown("#### 모델 성능 (홀드아웃 검증)")
    m = result.model_metrics
    st.write(f"- R² (설명력): {m['r2']:.3f}")
    st.write(f"- 학습 데이터: {m['n_train']}개 기업 / 검증 데이터: {m['n_test']}개 기업")
    st.caption("R²는 1에 가까울수록 모델이 실제 배출량 패턴을 잘 설명한다는 뜻이에요.")

    st.markdown("#### 방법론")
    st.markdown(
        "1. **회귀모델**: 매출액·매출원가·업종을 특징으로 RandomForest 모델이 배출량을 예측해요.\n"
        "2. **업종평균(EEIO 방식)**: 같은 업종 기업들의 '배출량 ÷ 매출액' 중앙값을 이 회사 매출액에 곱해요.\n"
        "3. 두 방법을 같이 보여주는 이유: 하나만 보면 그 추정치를 얼마나 믿어야 할지 판단하기 어렵기 때문이에요. "
        "두 값이 비슷하면 추정이 안정적이라는 뜻이고, 크게 다르면 이 회사가 업종 평균과 다른 특징(예: 최신 저탄소 설비)을 "
        "가지고 있다는 신호일 수 있어요."
    )
    st.write(f"이 회사에 적용된 업종평균 배출집약도: **{result.benchmark_estimate / (result.financials.get('매출액', 1) / 1e8):,.0f} tCO2eq / 매출 1억원** ({result.benchmark_source})")

with tab3:
    st.markdown("#### AI가 쉽게 풀어서 설명해주는 리포트")
    if st.session_state.get("report_key") != target_code:
        with st.spinner("AI가 리포트를 쓰는 중이에요..."):
            try:
                context = analysis.build_llm_context(result)
                st.session_state.report_text = generate_report(context)
                st.session_state.report_key = target_code
                try:
                    db.update_report(sb, st.session_state.get("record_id"), st.session_state.report_text)
                except Exception:  # noqa: BLE001
                    pass
            except Exception as e:  # noqa: BLE001
                st.session_state.report_text = None
                st.error(f"리포트 생성에 실패했어요: {e}")
    if st.session_state.get("report_text"):
        st.markdown(st.session_state.report_text)

with tab4:
    st.markdown("#### 지금까지 분석했던 기록")
    if not db.is_enabled():
        st.info("기록 저장 기능이 꺼져 있어요. SUPABASE_URL / SUPABASE_KEY 환경변수를 설정하면 켜져요.")
    else:
        try:
            rows = db.fetch_recent_estimates(sb, limit=20)
        except Exception as e:  # noqa: BLE001
            rows = []
            st.error(f"기록을 불러오지 못했어요: {e}")
        if rows:
            import pandas as pd

            history_df = pd.DataFrame(rows)[
                ["created_at", "corp_name", "industry", "actual_scope12", "model_estimate_scope12", "scope3_estimate"]
            ].rename(
                columns={
                    "created_at": "분석 시각",
                    "corp_name": "회사명",
                    "industry": "업종",
                    "actual_scope12": "실제 Scope1+2",
                    "model_estimate_scope12": "모델 Scope1+2",
                    "scope3_estimate": "Scope3 추정",
                }
            )
            st.dataframe(history_df, width="stretch")
        else:
            st.info("아직 쌓인 기록이 없어요. 분석을 실행하면 여기에 쌓여요.")
