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

st.set_page_config(page_title="AI 탄소배출량 추정기", page_icon="🌍", layout="wide")

# ── 색상 팔레트: "환경 리포트" 컨셉 — 크림 배경 + 짙은 파인그린 + 테라코타 포인트 ──
# 기본 배경/글자색은 .streamlit/config.toml의 테마로 통일한다 — CSS로 개별 위젯을
# 덮어쓰면 다크모드 브라우저에서 위젯 내부 기본 글자색과 충돌해 안 보이는 사고가 난다.
BG = "#F7F3EA"
SURFACE = "#FFFFFF"
INK = "#1B2B22"
MUT = "#6B7566"
LINE = "#E3DFD2"
PRIMARY = "#16362A"      # 짙은 파인그린 — 헤더/보조 버튼
ACCENT = "#E1672B"       # 테라코타 — 메인 액션/포인트
ACCENT_SOFT = "#F3E1D3"  # 테라코타 톤 배경
MOSS = "#4F7942"         # 모스그린 — 긍정 신호

COLOR_ACTUAL = PRIMARY       # 실제 공시값 (가장 신뢰도 높음 — 짙은 그린)
COLOR_MODEL = "#3E7CB1"      # 회귀모델 추정치 (스틸블루)
COLOR_BENCHMARK = ACCENT     # 업종평균 추정치 (테라코타)
COLOR_SCOPE3 = "#A79C87"     # Scope 3 (중립 타우프)

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


# ── 전역 스타일 ──────────────────────────────────────────────────────────────
st.markdown(
    f"""
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,600;8..60,700;8..60,800&display=swap" rel="stylesheet">
    <link rel="preconnect" href="https://cdn.jsdelivr.net" crossorigin>
    <link rel="stylesheet"
        href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css">
    <style>
        /* Streamlit 기본 크롬(햄버거 메뉴·사이드바 화살표·푸터·배포 버튼) 숨기기 */
        #MainMenu {{ visibility: hidden; }}
        footer {{ visibility: hidden; }}
        header[data-testid="stHeader"] {{ background: transparent; height: 0; }}
        [data-testid="stToolbar"] {{ visibility: hidden; }}
        [data-testid="stSidebarCollapsedControl"] {{ display: none; }}
        .stDeployButton {{ display: none; }}
        #stDecoration {{ display: none; }}

        html, body, [class*="css"] {{
            font-family: "Pretendard Variable", Pretendard, -apple-system, "Apple SD Gothic Neo", system-ui, sans-serif;
        }}
        .stApp {{ background: {BG}; }}
        .block-container {{ padding-top: 1.6rem; max-width: 1180px; }}

        h1, h2, h3, h4 {{
            font-family: "Source Serif 4", "Pretendard Variable", serif !important;
            font-weight: 700 !important; letter-spacing: -0.01em; color: {INK};
        }}

        [data-testid="stMetric"] {{
            background: {SURFACE}; border: 1px solid {LINE}; border-radius: 14px; padding: 0.9rem 1.05rem;
        }}
        [data-testid="stMetricLabel"] {{
            font-weight: 700; font-size: 0.74rem; letter-spacing: 0.03em; text-transform: uppercase; color: {MUT};
        }}
        [data-testid="stMetricValue"] {{ font-weight: 800; letter-spacing: -0.02em; color: {INK}; }}

        div[data-testid="stExpander"] {{ border: 1px solid {LINE}; border-radius: 12px; background: {SURFACE}; }}
        div[data-testid="stExpander"] details summary {{ font-weight: 700; }}
        div[data-testid="stVerticalBlockBorderWrapper"] {{ border-radius: 16px !important; }}

        .stButton > button[kind="primary"] {{
            background: {ACCENT} !important; color: #fff !important; border: none !important;
            font-weight: 700 !important; border-radius: 10px !important; letter-spacing: -0.01em;
        }}
        .stButton > button[kind="primary"]:hover {{ background: #C4551F !important; }}
        .stButton > button[kind="primary"]:disabled {{ background: {LINE} !important; color: {MUT} !important; }}

        .stTabs [data-baseweb="tab-list"] {{ gap: 4px; border-bottom: 1px solid {LINE}; }}
        .stTabs [data-baseweb="tab"] {{
            font-weight: 700; color: {MUT}; border-radius: 10px 10px 0 0; padding: 0.5rem 1rem;
        }}
        .stTabs [aria-selected="true"] {{ color: {INK} !important; background: {ACCENT_SOFT}; }}

        /* 상단 브랜드 바: 예전의 큰 다크 히어로 대신 얇은 파인그린 스트립 */
        .topbar {{
            background: {PRIMARY}; padding: 1.1rem 1.6rem; border-radius: 16px; margin-bottom: 1rem;
            display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 0.6rem;
        }}
        .topbar__brand {{ display: flex; flex-direction: column; }}
        .topbar__kicker {{
            color: {ACCENT}; font-size: 0.7rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
        }}
        .topbar__title {{
            color: #fff; font-family: "Source Serif 4", serif; font-weight: 700;
            font-size: 1.35rem; letter-spacing: -0.01em; margin-top: 0.15rem;
        }}

        .panel-tag {{
            display: inline-flex; align-items: center; gap: 6px; font-size: 0.72rem; font-weight: 700;
            letter-spacing: 0.04em; color: {MUT}; text-transform: uppercase; margin-bottom: 0.4rem;
        }}
        .panel-tag i {{ width: 5px; height: 5px; border-radius: 50%; background: {ACCENT}; display: inline-block; }}

        .badge-typical {{
            display: inline-block; background: {MOSS}; color: #fff; font-weight: 700; font-size: 0.76rem;
            padding: 0.25rem 0.7rem; border-radius: 100px; margin-bottom: 0.4rem;
        }}
        .badge-atypical {{
            display: inline-block; background: {ACCENT_SOFT}; color: {ACCENT}; font-weight: 700; font-size: 0.76rem;
            padding: 0.25rem 0.7rem; border-radius: 100px; margin-bottom: 0.4rem;
        }}
    </style>
    """,
    unsafe_allow_html=True,
)

# ── 상단 브랜드 바 ────────────────────────────────────────────────────────────
st.markdown(
    """
    <div class="topbar">
        <div class="topbar__brand">
            <span class="topbar__kicker">Scope 1 · 2 · 3 Estimator</span>
            <span class="topbar__title">AI가 재무제표만 보고 탄소배출량을 추정합니다</span>
        </div>
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

# ── 검색·컨트롤 바: 하나의 가로형 카드로 통합 ─────────────────────────────────
with st.container(border=True):
    st.markdown('<div class="panel-tag"><i></i>회사 검색</div>', unsafe_allow_html=True)
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

    exp_col1, exp_col2 = st.columns(2)
    with exp_col1:
        with st.expander("업종을 직접 선택하고 싶다면"):
            st.caption("배출권거래제 공시 대상 기업은 자동 감지돼요.")
            industry_choice = st.selectbox("업종", ["(자동 감지)"] + industries, label_visibility="collapsed")
    with exp_col2:
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
scope12_display = result.actual_scope12 if result.actual_scope12 is not None else result.model_estimate
scope12_label = "Scope 1+2 (실제 공시)" if result.actual_scope12 is not None else "Scope 1+2 (모델 추정)"

st.write("")
main_col, side_col = st.columns([2, 1], gap="medium")

# ── 오른쪽 요약 패널: 핵심 지표 + 신뢰도 신호를 한곳에 모음 ────────────────────
with side_col:
    with st.container(border=True):
        st.markdown('<div class="panel-tag"><i></i>핵심 지표</div>', unsafe_allow_html=True)
        st.markdown(f"##### {corp['corp_name']}")
        st.caption(f"{corp['stock_code'] or '비상장'}  ·  {result.industry}")
        st.caption(result.industry_source)
        st.divider()
        st.metric("매출액", f"{result.financials.get('매출액', 0):,.0f}원")
        st.metric(scope12_label, fmt_ton(scope12_display))
        st.metric("Scope 3 (추정)", fmt_ton(result.scope3_estimate))
        st.metric("총 배출량 추정", fmt_ton(scope12_display + result.scope3_estimate), help="Scope1+2 + Scope3 합계")

        st.write("")
        if result.is_typical_pattern:
            st.markdown('<span class="badge-typical">전형적 배출 패턴</span>', unsafe_allow_html=True)
        else:
            st.markdown('<span class="badge-atypical">방법론 간 편차 큼</span>', unsafe_allow_html=True)
        st.caption(f"방법론 간 편차율 {result.estimate_divergence_ratio * 100:.0f}%")

    if result.warnings:
        st.write("")
        with st.container(border=True):
            st.markdown('<div class="panel-tag"><i></i>참고 사항</div>', unsafe_allow_html=True)
            for w in result.warnings:
                st.warning(w)

# ── 왼쪽 메인 패널: 탭으로 구성된 상세 분석 ───────────────────────────────────
with main_col:
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
            fig1.update_layout(
                yaxis_title="tCO2eq", margin=dict(t=30, b=20), height=340, showlegend=False,
                plot_bgcolor=SURFACE, paper_bgcolor=SURFACE, font=dict(color=INK),
            )
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
            fig2.update_layout(
                yaxis_title="tCO2eq", margin=dict(t=30, b=20), height=340, showlegend=False,
                plot_bgcolor=SURFACE, paper_bgcolor=SURFACE, font=dict(color=INK),
            )
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
