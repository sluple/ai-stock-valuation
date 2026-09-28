"""AI 탄소배출량(Scope 1/2/3) 추정 대시보드 (Streamlit).

실행: streamlit run app.py
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import analysis
import db
import fetch_dart
from llm_report import generate_report

st.set_page_config(page_title="AI 탄소배출량 추정기", page_icon="leaf", layout="wide")

# ── 색상 팔레트: masstige.io 레퍼런스(흰 배경 + 검정 텍스트 + 라임 포인트) 참고 ──
# 기본 배경/글자색은 .streamlit/config.toml의 테마로 통일한다 — CSS로 개별 위젯을
# 덮어쓰면 다크모드 브라우저에서 위젯 내부 기본 글자색과 충돌해 안 보이는 사고가 난다.
INK = "#0B0B0B"
INK2 = "#43433F"
MUT = "#8A8A85"
LINE = "#E4E4E0"
DARK = "#0B0B0B"
ACCENT = "#DDFF4F"
ACCENT2 = "#B9E000"

COLOR_ACTUAL = INK          # 실제 공시값 (가장 신뢰도 높음 — 검정)
COLOR_MODEL = "#7FA8E0"     # 회귀모델 추정치 (파랑)
COLOR_BENCHMARK = ACCENT2   # 업종평균 추정치 (올리브라임)
COLOR_SCOPE3 = MUT          # Scope 3 (중립 회색)

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


# ── 전역 스타일: 테마(config.toml)가 기본 색을 맡고, 여기서는 커스텀 HTML 블록과
#    포인트 요소(버튼·히어로·카드 모서리)만 다듬는다 ──────────────────────────
st.markdown(
    f"""
    <link rel="preconnect" href="https://cdn.jsdelivr.net" crossorigin>
    <link rel="stylesheet"
        href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css">
    <style>
        html, body, [class*="css"] {{
            font-family: "Pretendard Variable", Pretendard, -apple-system, "Apple SD Gothic Neo", system-ui, sans-serif;
        }}
        .block-container {{ padding-top: 2rem; max-width: 1040px; }}

        h1, h2, h3, h4 {{ font-weight: 800 !important; letter-spacing: -0.03em; }}

        [data-testid="stMetric"] {{ border: 1px solid {LINE}; border-radius: 16px; padding: 1rem 1.1rem; }}
        [data-testid="stMetricLabel"] {{
            font-weight: 700; font-size: 0.78rem; letter-spacing: 0.02em; text-transform: uppercase;
        }}
        [data-testid="stMetricValue"] {{ font-weight: 800; letter-spacing: -0.03em; }}

        div[data-testid="stExpander"] {{ border: 1px solid {LINE}; border-radius: 14px; }}
        div[data-testid="stExpander"] details summary {{ font-weight: 700; }}
        div[data-testid="stVerticalBlockBorderWrapper"] {{ border-radius: 16px !important; }}

        .stButton > button[kind="primary"] {{
            background: {ACCENT} !important;
            color: {INK} !important;
            border: none !important;
            font-weight: 800 !important;
            border-radius: 100px !important;
            letter-spacing: -0.01em;
        }}
        .stButton > button[kind="primary"]:hover {{ background: {ACCENT2} !important; color: {INK} !important; }}
        .stButton > button[kind="primary"]:disabled {{ background: {LINE} !important; color: {MUT} !important; }}

        .stTabs [data-baseweb="tab"] {{ font-weight: 700; }}

        .hero {{ background: {DARK}; padding: 2.4rem 2.2rem; border-radius: 22px; margin-bottom: 1.75rem; }}
        .hero__eyebrow {{
            display: inline-flex; align-items: center; gap: 8px;
            color: #9A9A93; font-size: 0.78rem; font-weight: 700; letter-spacing: 0.04em; margin-bottom: 0.7rem;
        }}
        .hero__eyebrow i {{ width: 6px; height: 6px; border-radius: 50%; background: {ACCENT}; display: inline-block; }}
        .hero h1 {{
            color: #fff !important; margin: 0; font-size: clamp(1.7rem, 3.6vw, 2.5rem);
            letter-spacing: -0.045em; line-height: 1.18;
        }}
        .hero h1 mark {{ background: none; color: {ACCENT}; }}
        .hero p {{ color: #B7B7B0; margin: 0.75rem 0 0 0; font-size: 0.97rem; line-height: 1.65; max-width: 60ch; }}

        .section-tag {{
            display: inline-flex; align-items: center; gap: 7px; font-size: 0.76rem; font-weight: 700;
            letter-spacing: 0.03em; color: {MUT}; text-transform: uppercase; margin-bottom: 0.3rem;
        }}
        .section-tag i {{ width: 5px; height: 5px; border-radius: 50%; background: {ACCENT2}; display: inline-block; }}
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="hero">
        <div class="hero__eyebrow"><i></i>SCOPE 1 · 2 · 3 ESTIMATOR</div>
        <h1>AI가 재무제표만 보고,<br><mark>탄소배출량</mark>을 추정합니다.</h1>
        <p>기업의 재무데이터(매출액·매출원가)로 Scope 1+2 배출량을 추정하고,
        측정이 거의 불가능한 Scope 3(공급망 전체 배출량)까지 확장 추정합니다.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

if "sb_client" not in st.session_state:
    st.session_state.sb_client = db.create_client()
sb = st.session_state.sb_client

try:
    industries = analysis.list_known_industries()
except FileNotFoundError:
    industries = []
    st.error("학습된 모델이 없습니다. `python build_emissions_dataset.py` 후 `python train_model.py`를 먼저 실행하세요.")

# ── 검색 카드: 사이드바 대신 히어로 바로 아래 중앙에 배치 ───────────────────────
with st.container(border=True):
    st.markdown('<div class="section-tag"><i></i>회사 검색</div>', unsafe_allow_html=True)
    c1, c2 = st.columns([4, 1])
    with c1:
        company_query = st.text_input(
            "회사 이름", value="삼성전자", placeholder="예: 삼성전자, SK하이닉스, 포스코",
            label_visibility="collapsed",
        )
    candidates = fetch_dart.search_corp_by_name(company_query) if company_query else []

    selected_corp = None
    if candidates:
        option_labels = [f"{c['corp_name']}  ·  {c['stock_code'] or '비상장'}" for c in candidates]
        picked = st.selectbox(
            "찾은 회사 중에서 선택하세요", range(len(candidates)), format_func=lambda i: option_labels[i],
            label_visibility="collapsed",
        )
        selected_corp = candidates[picked]
    elif company_query:
        st.caption("검색 결과가 없어요. 정식 회사명을 입력해보세요.")

    with c2:
        run_button = st.button("분석 시작", type="primary", width="stretch", disabled=selected_corp is None)

    with st.expander("업종을 직접 선택하고 싶다면 (배출권거래제 공시 대상 기업은 자동 감지돼요)"):
        industry_choice = st.selectbox("업종", ["(자동 감지)"] + industries, label_visibility="collapsed")

with st.expander("어려운 용어가 있으면 여기를 눌러 확인하세요"):
    for term, desc in GLOSSARY:
        st.markdown(f"**{term}** — {desc}")

if "result" not in st.session_state:
    st.session_state.result = None
    st.session_state.error = None

if run_button and selected_corp:
    industry_override = None if industry_choice == "(자동 감지)" else industry_choice
    with st.spinner("재무데이터를 불러오고 배출량을 추정하는 중이에요..."):
        try:
            st.session_state.result = analysis.run_analysis_for_corp(
                selected_corp, industry_override=industry_override
            )
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
                st.session_state.error = "이 기업은 배출권거래제 공시 대상이 아니에요. 위에서 업종을 직접 선택하고 다시 눌러주세요."
            else:
                st.session_state.error = str(e)
        except Exception as e:  # noqa: BLE001
            st.session_state.result = None
            st.session_state.error = f"예상치 못한 오류: {e}"

if st.session_state.error:
    st.error(st.session_state.error)

result = st.session_state.result

if result is None:
    st.info("회사 이름을 검색하고 '분석 시작'을 눌러주세요.")
    st.stop()

corp = result.corp
st.write("")
st.subheader(f"{corp['corp_name']}  ·  {corp['stock_code'] or '비상장'}")
st.caption(f"업종: {result.industry}   |   {result.industry_source}")

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

st.write("")
tab1, tab2, tab3, tab4 = st.tabs(["배출량 추정 결과", "모델 설명", "AI 리포트", "최근 분석 기록"])

with tab1:
    with st.container(border=True):
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
        fig1.update_layout(yaxis_title="tCO2eq", margin=dict(t=30, b=20), height=360, showlegend=False)
        st.plotly_chart(fig1, width="stretch")

        if result.is_typical_pattern:
            st.success(
                f"방법론 간 추정치가 서로 {result.estimate_divergence_ratio * 100:.0f}% 이내로 근접해요. "
                "이 회사는 업종 내에서 전형적인 배출 패턴을 보인다는 뜻이라, 추정치를 신뢰할 수 있는 편이에요."
            )
        else:
            st.info(
                f"방법론 간 추정치 차이가 {result.estimate_divergence_ratio * 100:.0f}%로 커요. "
                "이 회사가 업종 평균과 다른 배출 특성(설비 효율, 사업 구성 등)을 가지고 있을 수 있으니 참고만 해주세요."
            )

    st.write("")
    with st.container(border=True):
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
        fig2.update_layout(yaxis_title="tCO2eq", margin=dict(t=30, b=20), height=360, showlegend=False)
        st.plotly_chart(fig2, width="stretch")
        st.caption(
            "Scope 3는 CDP(글로벌 ESG 공시기구) 조사에서 나온 '전산업 평균 Scope3 비중(약 75%)'을 적용한 값이에요. "
            "즉 Scope1+2의 약 3배로 계산했고, 업종별 정교화는 아직 안 되어 있어요."
        )

with tab2:
    with st.container(border=True):
        st.markdown("#### 모델 성능 (홀드아웃 검증)")
        m = result.model_metrics
        st.write(f"- R² (설명력): {m['r2']:.3f}")
        st.write(f"- 학습 데이터: {m['n_train']}개 기업 / 검증 데이터: {m['n_test']}개 기업")
        st.caption("R²는 1에 가까울수록 모델이 실제 배출량 패턴을 잘 설명한다는 뜻이에요.")

    st.write("")
    with st.container(border=True):
        st.markdown("#### 방법론")
        st.markdown(
            "1. **회귀모델**: 매출액·매출원가·업종을 특징으로 RandomForest 모델이 배출량을 예측해요.\n"
            "2. **업종평균(EEIO 방식)**: 같은 업종 기업들의 '배출량 ÷ 매출액' 중앙값을 이 회사 매출액에 곱해요.\n"
            "3. 두 방법을 같이 보여주는 이유: 하나만 보면 그 추정치를 얼마나 믿어야 할지 판단하기 어렵기 때문이에요. "
            "두 값이 비슷하면 추정이 안정적이라는 뜻이고, 크게 다르면 이 회사가 업종 평균과 다른 특징(예: 최신 저탄소 설비)을 "
            "가지고 있다는 신호일 수 있어요."
        )
        st.write(
            f"이 회사에 적용된 업종평균 배출집약도: "
            f"**{result.benchmark_estimate / (result.financials.get('매출액', 1) / 1e8):,.0f} tCO2eq / 매출 1억원** "
            f"({result.benchmark_source})"
        )

with tab3:
    st.markdown("#### AI가 쉽게 풀어서 설명해주는 리포트")
    if st.session_state.get("report_key") != corp["corp_code"]:
        with st.spinner("AI가 리포트를 쓰는 중이에요..."):
            try:
                context = analysis.build_llm_context(result)
                st.session_state.report_text = generate_report(context)
                st.session_state.report_key = corp["corp_code"]
                try:
                    db.update_report(sb, st.session_state.get("record_id"), st.session_state.report_text)
                except Exception:  # noqa: BLE001
                    pass
            except Exception as e:  # noqa: BLE001
                st.session_state.report_text = None
                st.error(f"리포트 생성에 실패했어요: {e}")
    if st.session_state.get("report_text"):
        with st.container(border=True):
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
