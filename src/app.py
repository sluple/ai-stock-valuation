"""AI 탄소배출량(Scope 1/2/3) 추정 대시보드 (Streamlit).

다크 "데이터 콘솔" 컨셉 — 시스템 구성 10개 항목과 이중 추정 구조/신뢰도 장치
4개 항목을 각각 Plotly 표준 차트(막대/게이지/도넛/불릿)로 보여준다.

실행: streamlit run app.py
"""
import statistics

import plotly.graph_objects as go
import streamlit as st

import analysis
import db
import emissions_model
import fetch_dart
from llm_report import generate_report

st.set_page_config(page_title="AI 탄소배출량 추정기", page_icon="🌍", layout="wide")

# ── 팔레트: dataviz 스킬 검증 팔레트의 다크모드 값 ────────────────────────────
PAGE = "#0d0d0d"
SURFACE = "#1a1a19"
SURFACE2 = "#202220"
INK = "#f0efec"
INK2 = "#c3c2b7"
MUT = "#898781"
LINE = "#2c2c2a"
BASELINE = "#383835"
BLUE = "#3987e5"      # 카테고리컬 slot 1(다크) — 회귀모델
ORANGE = "#d95926"    # 카테고리컬 slot 2(다크) — 업종평균
GOOD = "#0ca30c"
WARNING = "#fab219"
SERIOUS = "#ec835a"
GOOD_TINT = "rgba(12,163,12,0.16)"
WARNING_TINT = "rgba(250,178,25,0.16)"

SYSTEM_COMPONENTS = [
    ("STREAMLIT", "웹 대시보드", "Plotly 차트 + 회사명 검색·결과 화면 단일 페이지"),
    ("SEARCH", "회사명 검색 플로우", "코드가 아닌 이름으로 검색 → 결과 화면까지 한 흐름"),
    ("PYTHON", "분석 파이프라인", "재무데이터 조회 / 추정 모델 / 분석 오케스트레이션 모듈"),
    ("DART", "재무제표 조회", "전자공시시스템에서 매출액·매출원가 실시간 조회"),
    ("SKLEARN", "RandomForestRegressor", "타겟 인코딩 방식으로 업종을 압축"),
    ("EEIO", "업종평균 벤치마크", "업종별 배출집약도 중앙값 계산"),
    ("PICKLE", "모델 파일 직렬화", "학습된 모델 저장, 배포 서버가 즉시 로드"),
    ("SUPABASE", "PostgreSQL", "모든 분석 이력 저장"),
    ("K-ETS", "실측 데이터 대조", "온실가스종합정보센터 공시 배출량과 자동 매칭"),
    ("CLAUDE", "API 리포트 생성", "비전문가용 자연어 설명 리포트 자동 생성"),
]


def fmt_ton(x: float | None) -> str:
    return f"{x:,.0f} tCO2eq" if x is not None else "정보 없음"


def fmt_won(x: float | None) -> str:
    return f"{x:,.0f}원" if x is not None else "정보 없음"


PLOTLY_LAYOUT = dict(
    paper_bgcolor=SURFACE,
    plot_bgcolor=SURFACE,
    font=dict(color=INK2, family="JetBrains Mono, Pretendard Variable, monospace", size=12),
    margin=dict(t=28, b=28, l=16, r=16),
)


def chart_comparison(points, threshold):
    """실측값·회귀모델·업종평균을 막대로 비교 + 전형 범위를 띠로 표시.

    points의 각 항목에 "error"(표준편차, tCO2eq)가 있으면 해당 막대에 오차막대를 그린다
    (회귀모델 추정에는 나무 300개 예측치의 표준편차를 신뢰구간으로 붙인다)."""
    labels = [p["label"] for p in points]
    values = [p["value"] for p in points]
    colors = [p["color"] for p in points]
    errors = [p.get("error", 0) for p in points]
    has_error = any(e for e in errors)
    fig = go.Figure(
        go.Bar(
            x=labels, y=values, marker_color=colors, text=[f"{v:,.0f}" for v in values],
            textposition="outside", textfont=dict(color=INK, size=13), width=0.5,
            error_y=dict(type="data", array=errors, color=INK2, thickness=1.5, width=5) if has_error else None,
        )
    )
    lo = min(values)
    hi = lo * (1 + threshold)
    fig.add_hrect(y0=lo, y1=hi, fillcolor=GOOD_TINT, line_width=0, layer="below")
    fig.add_hline(y=hi, line_dash="dot", line_color=MUT, line_width=1,
                   annotation_text="30% 기준선", annotation_font_color=MUT, annotation_font_size=10)
    fig.update_layout(
        **PLOTLY_LAYOUT, height=300, showlegend=False,
        yaxis=dict(title="tCO2eq", gridcolor=LINE, zerolinecolor=BASELINE, title_font=dict(size=11)),
        xaxis=dict(linecolor=BASELINE),
    )
    return fig


def chart_divergence_gauge(ratio, threshold):
    pct = ratio * 100
    fill = GOOD if ratio <= threshold else WARNING
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=min(pct, 150),
        number=dict(suffix="%", font=dict(color=INK, size=30)),
        gauge=dict(
            axis=dict(range=[0, 150], tickcolor=MUT, tickfont=dict(color=MUT, size=10)),
            bar=dict(color=fill, thickness=0.3),
            bgcolor=SURFACE2,
            borderwidth=0,
            steps=[
                dict(range=[0, 30], color=GOOD_TINT),
                dict(range=[30, 150], color=WARNING_TINT),
            ],
            threshold=dict(line=dict(color=INK2, width=2), thickness=0.85, value=30),
        ),
    ))
    if pct > 150:
        fig.add_annotation(text=f"실제 {pct:,.0f}% (범위 초과)", x=0.5, y=-0.15, showarrow=False,
                            font=dict(color=MUT, size=11), xref="paper", yref="paper")
    fig.update_layout(**PLOTLY_LAYOUT, height=220)
    return fig


def chart_scope3_donut(scope12, scope3):
    fig = go.Figure(go.Pie(
        values=[scope12, scope3], labels=["Scope 1+2", "Scope 3 (추정)"],
        hole=0.66, marker=dict(colors=[BLUE, ORANGE], line=dict(color=SURFACE, width=3)),
        textinfo="label+percent", textfont=dict(color=INK, size=12),
        sort=False, direction="clockwise",
    ))
    total = scope12 + scope3
    fig.add_annotation(text=f"×3<br><span style='font-size:11px;color:{MUT}'>CDP 배율</span>",
                        x=0.5, y=0.5, showarrow=False, font=dict(color=INK, size=20))
    fig.update_layout(**PLOTLY_LAYOUT, height=300, showlegend=True,
                       legend=dict(orientation="h", y=-0.1, font=dict(color=INK2, size=11)))
    return fig


def _won_unit(x):
    """원 단위 숫자를 조/억원 중 보기 좋은 단위로 환산 (차트에 14자리 숫자가 그대로
    찍히면 영역 밖으로 밀려나가므로, 억/조원 단위로 줄여서 짧게 표시한다)."""
    if x >= 1e12:
        return 1e12, "조원"
    return 1e8, "억원"


def chart_scale_bullet(revenue, median_revenue, ratio, scale_warning):
    fill = WARNING if scale_warning else GOOD
    divisor, unit = _won_unit(max(revenue, median_revenue))
    rev_u = revenue / divisor
    med_u = median_revenue / divisor
    axis_max = max(rev_u, med_u * 5) * 1.15
    fig = go.Figure(go.Indicator(
        mode="number+gauge",
        value=rev_u,
        number=dict(valueformat=",.1f", suffix=f" {unit}", font=dict(color=INK, size=22)),
        domain=dict(x=[0.08, 0.95], y=[0.35, 0.65]),
        gauge=dict(
            shape="bullet",
            axis=dict(range=[0, axis_max], tickcolor=MUT, tickfont=dict(color=MUT, size=9)),
            bar=dict(color=fill, thickness=0.55),
            bgcolor=SURFACE2,
            borderwidth=0,
            steps=[dict(range=[0, med_u], color=LINE)],
            threshold=dict(line=dict(color=INK2, width=2), thickness=0.9, value=med_u * 5),
        ),
    ))
    fig.add_annotation(text=f"업종 중앙값의 {ratio:.1f}배", x=0.08, y=0.95, showarrow=False,
                        xref="paper", yref="paper", xanchor="left", font=dict(color=MUT, size=11))
    fig.add_annotation(text="⏷ 5배 경고선", x=min(med_u * 5 / axis_max, 0.97), y=0.08,
                        showarrow=False, xref="paper", yref="paper", font=dict(color=WARNING, size=10))
    layout = {**PLOTLY_LAYOUT, "margin": dict(t=24, b=24, l=16, r=16)}
    fig.update_layout(**layout, height=140)
    return fig


def _pipe_box(label, value, cls=""):
    return (
        f'<div class="pipe-box {cls}">'
        f'<div class="pb-label">{label}</div><div class="pb-value">{value}</div>'
        f"</div>"
    )


_PIPE_DOWN = '<div class="pipe-center"><div class="pipe-down">↓</div></div>'


def render_pipeline_inputs(revenue, cogs, benchmark_estimate):
    arrow = '<div class="pipe-arrow">→</div>'
    html = '<div class="pipe-wrap"><div class="pipe-row">'
    html += _pipe_box("매출액", f"{revenue:,.0f}원")
    html += arrow
    html += _pipe_box("매출원가", f"{cogs:,.0f}원")
    html += arrow
    html += _pipe_box("업종추정치 (EEIO)", f"{benchmark_estimate:,.0f} t", cls="pipe-eeio")
    html += "</div>"
    html += (
        '<div class="pipe-note">↑ 업종추정치는 "① Scope 1+2 비교" 차트의 업종평균 추정값을 '
        "그대로 재사용한 것입니다 (업종 원-핫 인코딩 대신 타겟 인코딩)</div>"
    )
    html += _PIPE_DOWN
    html += f'<div class="pipe-center">{_pipe_box("log1p 변환", "3개 값 모두 log(1+x)")}</div>'
    html += _PIPE_DOWN
    html += "</div>"
    return html


def render_pipeline_outputs(model_estimate):
    html = '<div class="pipe-wrap">'
    html += _PIPE_DOWN
    html += f'<div class="pipe-center">{_pipe_box("expm1 역변환", "로그 → tCO2eq 단위 복원")}</div>'
    html += _PIPE_DOWN
    html += f'<div class="pipe-center">{_pipe_box("회귀모델 추정치", f"{model_estimate:,.0f} tCO2eq", cls="pipe-final")}</div>'
    html += "</div>"
    return html


def chart_tree_scatter(tree_predictions, model_estimate):
    """RandomForest 나무 300개 각각의 개별 예측치를 실제 값 그대로 산점도로 보여준다."""
    n = len(tree_predictions)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=list(range(1, n + 1)), y=tree_predictions, mode="markers",
        marker=dict(size=5, color=BLUE, opacity=0.55),
        name="개별 나무 예측치", hovertemplate="나무 %{x}: %{y:,.0f} tCO2eq<extra></extra>",
    ))
    fig.add_hline(
        y=model_estimate, line_color=ORANGE, line_width=2,
        annotation_text=f"평균(최종 예측값) {model_estimate:,.0f} t",
        annotation_font_color=ORANGE, annotation_position="top left",
    )
    fig.update_layout(
        **PLOTLY_LAYOUT, height=260, showlegend=False,
        xaxis=dict(title="나무 번호 (1~300)", gridcolor=LINE, linecolor=BASELINE, title_font=dict(size=11)),
        yaxis=dict(title="예측치 (tCO2eq)", gridcolor=LINE, zerolinecolor=BASELINE, title_font=dict(size=11)),
    )
    return fig


def chart_feature_importance(feature_importance: dict):
    """회귀모델이 세 입력 중 무엇을 가장 많이 참고했는지 (scikit-learn feature_importances_)."""
    items = sorted(feature_importance.items(), key=lambda kv: kv[1])
    labels = [k for k, _ in items]
    values = [v for _, v in items]
    fig = go.Figure(go.Bar(
        x=values, y=labels, orientation="h", marker_color=[BLUE, BLUE, ORANGE][: len(values)],
        text=[f"{v * 100:.1f}%" for v in values], textposition="outside", textfont=dict(color=INK, size=12),
    ))
    fig.update_layout(
        **PLOTLY_LAYOUT, height=200, showlegend=False,
        xaxis=dict(title="중요도", gridcolor=LINE, zerolinecolor=BASELINE, tickformat=".0%", title_font=dict(size=11)),
        yaxis=dict(linecolor=BASELINE),
    )
    return fig


def chart_industry_benchmark(intensities, target_intensity, target_label):
    """같은 업종 기업들의 배출집약도 분포 안에서 이 기업의 위치를 보여준다."""
    fig = go.Figure()
    fig.add_trace(go.Box(
        x=intensities, name="업종 내 기업들", marker_color=INK2, line_color=INK2,
        boxpoints="all", jitter=0.5, pointpos=0, marker=dict(size=4, opacity=0.35, color=MUT),
        fillcolor=SURFACE2,
    ))
    fig.add_trace(go.Scatter(
        x=[target_intensity], y=["업종 내 기업들"], mode="markers",
        marker=dict(size=14, color=ORANGE, symbol="diamond", line=dict(color=INK, width=1.5)),
        name=target_label, hovertemplate=f"{target_label}: %{{x:,.0f}}<extra></extra>",
    ))
    fig.update_layout(
        **PLOTLY_LAYOUT, height=180, showlegend=False,
        xaxis=dict(title="배출집약도 (tCO2eq / 매출 1억원)", gridcolor=LINE, zerolinecolor=BASELINE, title_font=dict(size=11)),
        yaxis=dict(visible=False),
    )
    return fig


def chart_year_trend(trend):
    years = [t["연도"] for t in trend]
    model_vals = [t["회귀모델_추정"] for t in trend]
    bench_vals = [t["업종평균_추정"] for t in trend]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=years, y=model_vals, mode="lines+markers", name="회귀모델 추정",
                              line=dict(color=BLUE, width=2), marker=dict(size=8)))
    fig.add_trace(go.Scatter(x=years, y=bench_vals, mode="lines+markers", name="업종평균 추정",
                              line=dict(color=ORANGE, width=2), marker=dict(size=8)))
    fig.update_layout(
        **PLOTLY_LAYOUT, height=260, showlegend=True,
        legend=dict(orientation="h", y=-0.2, font=dict(color=INK2, size=11)),
        xaxis=dict(title="연도", gridcolor=LINE, linecolor=BASELINE, dtick=1, title_font=dict(size=11)),
        yaxis=dict(title="tCO2eq", gridcolor=LINE, zerolinecolor=BASELINE, title_font=dict(size=11)),
    )
    return fig


# ── 전역 스타일 ──────────────────────────────────────────────────────────────
st.markdown(
    f"""
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@500;700;800&display=swap" rel="stylesheet">
    <link rel="preconnect" href="https://cdn.jsdelivr.net" crossorigin>
    <link rel="stylesheet"
        href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css">
    <style>
        #MainMenu {{ visibility: hidden; }}
        footer {{ visibility: hidden; }}
        header[data-testid="stHeader"] {{ background: transparent; height: 0; overflow: hidden; }}
        [data-testid="stToolbar"] {{ visibility: hidden; display: none; }}
        [data-testid="stSidebarCollapsedControl"] {{ display: none; }}
        [data-testid="stStatusWidget"] {{ display: none !important; }}
        .stDeployButton {{ display: none; }}
        #stDecoration {{ display: none; }}

        html, body, [class*="css"] {{
            font-family: "Pretendard Variable", Pretendard, -apple-system, "Segoe UI", system-ui, sans-serif;
        }}
        .stApp {{ background: {PAGE}; }}
        .block-container {{ padding-top: 1.3rem; max-width: 1140px; }}

        h1, h2, h3, h4, h5 {{ font-weight: 800 !important; letter-spacing: -0.01em; color: {INK} !important; }}

        div[data-testid="stExpander"] {{ border: 1px solid {LINE}; border-radius: 10px; background: {SURFACE}; }}
        div[data-testid="stVerticalBlockBorderWrapper"] {{
            border-radius: 12px !important; border-color: {LINE} !important; background: {SURFACE};
        }}

        div[data-testid="stAlert"] * {{ color: {INK} !important; }}
        .stMarkdown, .stMarkdown p, .stMarkdown li, .stMarkdown span,
        .stMarkdown strong, .stMarkdown em {{ color: {INK2} !important; }}

        .stButton > button[kind="primary"] {{
            background: {BLUE} !important; color: #fff !important; border: none !important;
            font-weight: 700 !important; border-radius: 8px !important;
        }}
        .stButton > button[kind="primary"]:hover {{ background: #5a9fec !important; }}
        .stButton > button[kind="primary"]:disabled {{ background: {LINE} !important; color: {MUT} !important; }}

        div[data-testid="stTextInput"] input {{
            border-radius: 8px !important; border-color: {LINE} !important; background: {SURFACE2} !important;
            color: {INK} !important; font-family: "JetBrains Mono", monospace;
        }}
        div[data-testid="stTextInput"] input:focus {{
            border-color: {BLUE} !important; box-shadow: 0 0 0 3px rgba(57,135,229,0.2) !important;
        }}

        .top-header {{
            display: flex; align-items: center; gap: 12px; padding: 0.1rem 0 1.1rem 0;
            border-bottom: 1px solid {LINE}; margin-bottom: 1.2rem;
        }}
        .top-header .mark {{
            width: 36px; height: 36px; border-radius: 8px; background: {BLUE};
            display: flex; align-items: center; justify-content: center; font-size: 1.1rem; flex-shrink: 0;
        }}
        .page-title {{
            font-family: "JetBrains Mono", monospace; font-size: 1.22rem; font-weight: 800;
            color: {INK}; letter-spacing: -0.02em;
        }}
        .page-sub {{ color: {MUT}; font-size: 0.83rem; margin-top: 2px; }}
        .live-dot {{
            display: inline-flex; align-items: center; gap: 6px; margin-left: auto;
            font-size: 0.72rem; font-weight: 700; color: {MUT}; font-family: "JetBrains Mono", monospace;
        }}
        .live-dot i {{
            width: 7px; height: 7px; border-radius: 50%; background: {GOOD};
            box-shadow: 0 0 0 3px {GOOD_TINT}; display: inline-block;
        }}

        .sec-label {{
            display: flex; align-items: center; gap: 8px; font-size: 0.7rem; font-weight: 800;
            letter-spacing: 0.08em; color: {MUT}; text-transform: uppercase; margin: 1.4rem 0 0.6rem 0;
            font-family: "JetBrains Mono", monospace;
        }}
        .sec-label i {{ width: 5px; height: 5px; border-radius: 50%; background: {BLUE}; display: inline-block; flex-shrink: 0; }}
        .sec-label::after {{ content: ""; flex: 1; height: 1px; background: {LINE}; }}

        .comp-grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 9px; margin-bottom: 0.3rem; }}
        .comp-card {{
            background: {SURFACE}; border: 1px solid {LINE}; border-left: 2px solid {BLUE};
            border-radius: 8px; padding: 10px 12px;
        }}
        .comp-card .tag {{
            font-family: "JetBrains Mono", monospace; font-size: 0.62rem; font-weight: 700;
            color: {BLUE}; letter-spacing: 0.04em;
        }}
        .comp-card .ti {{ font-weight: 700; font-size: 0.8rem; color: {INK}; margin-top: 5px; line-height: 1.25; }}
        .comp-card .de {{ font-size: 0.68rem; color: {MUT}; margin-top: 3px; line-height: 1.35; }}
        @media (max-width: 900px) {{ .comp-grid {{ grid-template-columns: repeat(2, 1fr); }} }}

        .card-head {{ display: flex; align-items: center; gap: 10px; margin-bottom: 0.2rem; }}
        .card-head .num {{
            width: 24px; height: 24px; border-radius: 6px; background: {SURFACE2}; border: 1px solid {LINE};
            color: {BLUE}; font-family: "JetBrains Mono", monospace; font-size: 0.74rem; font-weight: 800;
            display: flex; align-items: center; justify-content: center; flex-shrink: 0;
        }}
        .card-head h5 {{ margin: 0; font-size: 0.96rem; }}
        .viz-desc {{ font-size: 0.79rem; color: {MUT}; margin: 0.3rem 0 0.1rem 34px; }}

        .badge {{
            display: inline-flex; align-items: center; gap: 6px; font-weight: 700; font-size: 0.76rem;
            padding: 0.3rem 0.75rem; border-radius: 6px; font-family: "JetBrains Mono", monospace;
        }}
        .badge-good {{ background: {GOOD_TINT}; color: #5fd65f; }}
        .badge-warn {{ background: {WARNING_TINT}; color: {WARNING}; }}

        .report-head {{ display: flex; align-items: center; gap: 8px; margin-bottom: 0.7rem; }}
        .report-head .dot {{ width: 8px; height: 8px; border-radius: 50%; background: {ORANGE}; box-shadow: 0 0 0 3px rgba(217,89,38,0.2); }}
        .report-head span {{
            font-size: 0.76rem; font-weight: 700; color: {MUT}; font-family: "JetBrains Mono", monospace;
            letter-spacing: 0.03em;
        }}

        [data-testid="stMetricValue"] {{ font-family: "JetBrains Mono", monospace; font-variant-numeric: tabular-nums; }}

        .pipe-wrap {{ padding: 0.4rem 0 0.2rem 0; }}
        .pipe-row {{ display: flex; align-items: stretch; gap: 8px; flex-wrap: wrap; justify-content: center; }}
        .pipe-center {{ display: flex; justify-content: center; }}
        .pipe-box {{
            background: {SURFACE2}; border: 1px solid {LINE}; border-radius: 8px;
            padding: 10px 16px; text-align: center; min-width: 130px;
        }}
        .pipe-box .pb-label {{
            font-size: 0.66rem; color: {MUT}; text-transform: uppercase; letter-spacing: 0.04em;
            font-family: "JetBrains Mono", monospace;
        }}
        .pipe-box .pb-value {{
            font-size: 0.82rem; color: {INK}; font-weight: 700; margin-top: 4px;
            font-family: "JetBrains Mono", monospace;
        }}
        .pipe-box.pipe-eeio {{ border-color: {ORANGE}; background: rgba(217,89,38,0.08); }}
        .pipe-box.pipe-forest {{ border-color: {INK2}; min-width: 220px; }}
        .pipe-box.pipe-final {{ border-color: {BLUE}; background: rgba(57,135,229,0.1); min-width: 220px; }}
        .pipe-box.pipe-final .pb-value {{ font-size: 1rem; color: {BLUE}; }}
        .pipe-arrow {{ display: flex; align-items: center; color: {MUT}; font-size: 1.1rem; }}
        .pipe-down {{ color: {MUT}; font-size: 1.1rem; text-align: center; padding: 2px 0; }}
        .pipe-note {{
            text-align: center; font-size: 0.68rem; color: {MUT}; margin: 6px 0 2px 0; line-height: 1.4;
        }}
        @media (max-width: 700px) {{ .pipe-row {{ flex-direction: column; align-items: center; }} .pipe-arrow {{ transform: rotate(90deg); }} }}
    </style>
    """,
    unsafe_allow_html=True,
)

# ── 상단 바 ──────────────────────────────────────────────────────────────────
st.markdown(
    """
    <div class="top-header">
        <div>
            <div class="page-title">AI 탄소배출량 추정기</div>
            <div class="page-sub">재무데이터로 Scope 1·2·3을 추정하고, 추정 과정 자체를 그대로 보여줍니다.</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

if "sb_client" not in st.session_state:
    try:
        st.session_state.sb_client = db.create_client()
    except Exception:  # noqa: BLE001
        st.session_state.sb_client = None
sb = st.session_state.sb_client

if "corp_cache_warm" not in st.session_state:
    with st.spinner("기업 목록을 불러오는 중이에요 (최초 1회, 몇 초 걸려요)..."):
        try:
            fetch_dart.warm_up_corp_cache()
            st.session_state.corp_cache_ok = True
        except Exception as e:  # noqa: BLE001
            st.session_state.corp_cache_ok = False
            st.session_state.corp_cache_error = str(e)
    st.session_state.corp_cache_warm = True

if not st.session_state.get("corp_cache_ok", True):
    st.error(
        f"DART에서 기업 목록을 불러오지 못했어요 ({st.session_state.get('corp_cache_error', '')}). "
        "DART 쪽이 일시적으로 불안정할 수 있어요 — 잠시 후 새로고침해보세요."
    )

try:
    industries = analysis.list_known_industries()
except Exception as e:  # noqa: BLE001
    industries = []
    st.error(
        "학습된 모델을 불러오지 못했어요. "
        "`python build_emissions_dataset.py` 후 `python train_model.py`를 먼저 실행했는지 확인하세요. "
        f"(오류: {e})"
    )

try:
    industry_display = analysis.industry_display_map()
except Exception:  # noqa: BLE001
    industry_display = {}


def fmt_industry(key: str) -> str:
    return industry_display.get(key, key)

with st.container(border=True):
    c1, c2 = st.columns([4, 1])
    with c1:
        company_query = st.text_input(
            "회사 이름", value="삼성전자", placeholder="예: 삼성전자, SK하이닉스, 포스코",
            label_visibility="collapsed",
        )
    try:
        candidates = fetch_dart.search_corp_by_name(company_query) if company_query else []
    except Exception as e:  # noqa: BLE001
        candidates = []
        st.error(f"회사 검색 중 오류가 발생했어요: {e}")

    selected_corp = None
    if candidates:
        option_labels = [f"{c['corp_name']}  ·  {c['stock_code'] or '비상장'}" for c in candidates]
        picked = st.selectbox(
            "찾은 회사 중에서 선택하세요", range(len(candidates)), format_func=lambda i: option_labels[i],
            label_visibility="collapsed",
        )
        selected_corp = candidates[picked]
    elif company_query:
        st.caption("검색 결과가 없어요.")

    with c2:
        run_button = st.button("분석 시작", type="primary", width="stretch", disabled=selected_corp is None)

    with st.expander("업종을 직접 선택하고 싶다면 (배출권거래제 공시 대상 기업은 자동 감지)"):
        st.caption("공시 대상이 아닌 기업은 업종을 직접 골라야 해요. 아래 칸을 클릭하고 업종 이름 일부를 타이핑하면 바로 검색돼요 (예: '철강', '화학').")
        industry_choice = st.selectbox(
            "업종", ["(자동 감지)"] + industries, label_visibility="collapsed",
            placeholder="업종 이름을 입력해 검색...",
            format_func=lambda v: v if v == "(자동 감지)" else fmt_industry(v),
        )

with st.expander("지금까지 분석한 기록 보기 (Supabase에 저장된 전체 이력)"):
    if not db.is_enabled():
        st.info("기록 저장 기능이 꺼져 있어요. SUPABASE_URL / SUPABASE_KEY 환경변수를 설정하면 켜져요.")
    else:
        try:
            history_rows = db.fetch_recent_estimates(sb, limit=20)
        except Exception as e:  # noqa: BLE001
            history_rows = []
            st.error(f"기록을 불러오지 못했어요: {e}")
        if history_rows:
            import pandas as pd

            history_df = pd.DataFrame(history_rows)[
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

# ── 시스템 구성: 보고서의 10개 항목을 그대로 카드 그리드로 ────────────────────
st.markdown('<div class="sec-label"><i></i>시스템 구성</div>', unsafe_allow_html=True)
cards_html = '<div class="comp-grid">'
for tag, title, desc in SYSTEM_COMPONENTS:
    cards_html += f'<div class="comp-card"><div class="tag">{tag}</div><div class="ti">{title}</div><div class="de">{desc}</div></div>'
cards_html += "</div>"
st.markdown(cards_html, unsafe_allow_html=True)

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

st.write("")
st.markdown(
    f'<div class="sec-label"><i></i>{corp["corp_name"]} · {fmt_industry(result.industry)} · 매출 {fmt_won(result.financials.get("매출액"))}</div>',
    unsafe_allow_html=True,
)

for w in result.warnings:
    st.warning(w)

# ── 1) 실측값·회귀모델·업종평균 세 수치 비교 ──────────────────────────────────
with st.container(border=True):
    st.markdown(
        '<div class="card-head"><div class="num">1</div><h5>세 가지 방법으로 Scope 1+2를 각각 추정해 비교합니다</h5></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="viz-desc">실측값(있는 경우)·회귀모델·업종평균을 막대로 비교하고, 서로 30% 이내로 가까운지로 신뢰도를 판단합니다.</div>',
        unsafe_allow_html=True,
    )
    tree_std = statistics.pstdev(result.tree_predictions) if len(result.tree_predictions) > 1 else 0.0
    points = []
    if result.actual_scope12 is not None:
        points.append({"label": "실제 공시값", "value": result.actual_scope12, "color": INK2})
    points.append({"label": "회귀모델 추정", "value": result.model_estimate, "color": BLUE, "error": tree_std})
    points.append({"label": "업종평균 추정", "value": result.benchmark_estimate, "color": ORANGE})
    st.plotly_chart(chart_comparison(points, analysis.TYPICAL_PATTERN_THRESHOLD), width="stretch", config={"displayModeBar": False})
    badge_cls = "badge-good" if result.is_typical_pattern else "badge-warn"
    badge_txt = "✓ 업종 내 전형적인 배출 패턴" if result.is_typical_pattern else "⚠ 방법론 간 편차가 큼"
    st.markdown(f'<span class="badge {badge_cls}">{badge_txt}</span>', unsafe_allow_html=True)
    st.caption(
        f"회귀모델 추정 신뢰구간: {result.model_estimate:,.0f} ± {tree_std:,.0f} tCO2eq "
        "(나무 300개 개별 예측치의 표준편차 기준)"
    )

st.write("")

# ── 2) 회귀모델 내부 계산 파이프라인 ──────────────────────────────────────────
with st.container(border=True):
    st.markdown(
        '<div class="card-head"><div class="num">2</div><h5>회귀모델은 내부적으로 이렇게 계산합니다</h5></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="viz-desc">매출액·매출원가·업종추정치(EEIO) 세 값을 로그로 변환해 RandomForest 300개 나무 각각에 넣고, 그 개별 예측치들을 평균내 다시 원래 단위로 되돌립니다.</div>',
        unsafe_allow_html=True,
    )
    revenue_val = result.financials.get("매출액", 0) or 0
    cogs_val = result.financials.get("매출원가") or revenue_val * 0.7
    st.markdown(render_pipeline_inputs(revenue_val, cogs_val, result.benchmark_estimate), unsafe_allow_html=True)
    st.plotly_chart(
        chart_tree_scatter(result.tree_predictions, result.model_estimate),
        width="stretch", config={"displayModeBar": False},
    )
    st.markdown(render_pipeline_outputs(result.model_estimate), unsafe_allow_html=True)

st.write("")

# ── 3) 편차율 게이지 ───────────────────────────────────────────────────────────
with st.container(border=True):
    st.markdown(
        '<div class="card-head"><div class="num">3</div><h5>편차율이 30% 기준선 안에 들어오는지 측정합니다</h5></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="viz-desc">(최댓값 − 최솟값) ÷ 최솟값. 게이지 바늘이 30% 기준선을 넘으면 경고 구간입니다.</div>',
        unsafe_allow_html=True,
    )
    st.plotly_chart(
        chart_divergence_gauge(result.estimate_divergence_ratio, analysis.TYPICAL_PATTERN_THRESHOLD),
        width="stretch", config={"displayModeBar": False},
    )

st.write("")

# ── 3) Scope 3 = Scope1+2 × 3 (도넛) ──────────────────────────────────────────
with st.container(border=True):
    st.markdown(
        '<div class="card-head"><div class="num">4</div><h5>Scope 3는 CDP 배율(×3)을 곱해 확장 산출합니다</h5></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="viz-desc">CDP 조사: Scope 3가 전체 배출량(1+2+3)의 평균 약 75%를 차지 → Scope 3 ≈ Scope 1+2 × 3</div>',
        unsafe_allow_html=True,
    )
    st.plotly_chart(
        chart_scope3_donut(scope12_display, result.scope3_estimate),
        width="stretch", config={"displayModeBar": False},
    )

st.write("")

# ── 4) 규모 이상치 경고 (불릿 차트) ────────────────────────────────────────────
with st.container(border=True):
    st.markdown(
        '<div class="card-head"><div class="num">5</div><h5>매출 규모가 업종 중앙값의 5배를 넘는지 확인합니다</h5></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="viz-desc">넘으면 복합기업·지주회사일 가능성이 높아 추정 신뢰도가 낮아질 수 있다는 경고를 띄웁니다.</div>',
        unsafe_allow_html=True,
    )
    revenue = result.financials.get("매출액", 0) or 0
    median_revenue = revenue / result.scale_ratio if result.scale_ratio else revenue
    st.plotly_chart(
        chart_scale_bullet(revenue, median_revenue, result.scale_ratio, result.scale_warning),
        width="stretch", config={"displayModeBar": False},
    )

st.write("")

# ── 6) 피처 중요도 ─────────────────────────────────────────────────────────────
with st.container(border=True):
    st.markdown(
        '<div class="card-head"><div class="num">6</div><h5>회귀모델은 세 입력 중 무엇을 가장 많이 참고했을까요</h5></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="viz-desc">scikit-learn이 학습 시점에 계산해 둔 feature_importances_ 값입니다. 업종추정치(EEIO)의 비중이 크다면, 결국 업종 평균을 기준점으로 삼고 있다는 뜻입니다.</div>',
        unsafe_allow_html=True,
    )
    st.plotly_chart(
        chart_feature_importance(result.feature_importance),
        width="stretch", config={"displayModeBar": False},
    )

st.write("")

# ── 7) 업종 내 위치 비교 (벤치마킹) ───────────────────────────────────────────
with st.container(border=True):
    st.markdown(
        '<div class="card-head"><div class="num">7</div><h5>같은 업종 다른 기업들과 비교하면 어디쯤일까요</h5></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="viz-desc">학습 데이터에 있는 같은 업종 기업들의 배출집약도 분포 위에, 이 기업(◆)의 위치를 표시합니다.</div>',
        unsafe_allow_html=True,
    )
    industry_intensities = emissions_model.get_industry_intensities(result.industry)
    target_intensity = (scope12_display / (revenue / 1e8)) if revenue else 0
    if len(industry_intensities) >= 3:
        percentile = sum(1 for v in industry_intensities if v <= target_intensity) / len(industry_intensities) * 100
        st.plotly_chart(
            chart_industry_benchmark(industry_intensities, target_intensity, corp["corp_name"]),
            width="stretch", config={"displayModeBar": False},
        )
        st.caption(f"{fmt_industry(result.industry)} 내 {len(industry_intensities)}개 기업 중 배출집약도 상위 {100 - percentile:.0f}% 구간입니다.")
    else:
        st.info("같은 업종 내 비교할 기업 수가 너무 적어요.")

st.write("")

# ── 8) 연도별 추이 (지연 로딩 — DART 호출이 추가로 필요해 버튼을 눌러야 조회) ──
with st.container(border=True):
    st.markdown(
        '<div class="card-head"><div class="num">8</div><h5>연도별로는 어떻게 바뀌어 왔을까요</h5></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="viz-desc">최근 3개 연도 재무데이터를 추가로 조회해 추정 배출량 추이를 보여줍니다 (DART 호출이 늘어나 버튼을 눌러야 조회됩니다).</div>',
        unsafe_allow_html=True,
    )
    trend_key = f"trend_{corp['corp_code']}"
    if st.button("연도별 추이 조회", key="trend_button"):
        with st.spinner("최근 3개 연도 재무데이터를 조회하는 중이에요..."):
            years = [analysis.TARGET_YEAR - 2, analysis.TARGET_YEAR - 1, analysis.TARGET_YEAR]
            st.session_state[trend_key] = analysis.get_multi_year_trend(corp, result.industry, years)
    trend_data = st.session_state.get(trend_key)
    if trend_data:
        if len(trend_data) >= 2:
            st.plotly_chart(chart_year_trend(trend_data), width="stretch", config={"displayModeBar": False})
        else:
            st.info("조회 가능한 연도가 1개뿐이라 추이를 그릴 수 없어요.")

st.write("")

# ── 분석 결과 다운로드 ─────────────────────────────────────────────────────────
summary_lines = [
    f"# {corp['corp_name']} 탄소배출량 분석 결과",
    f"- 업종: {fmt_industry(result.industry)} ({result.industry_source})",
    f"- 매출액: {fmt_won(result.financials.get('매출액'))}",
    "",
    "## Scope 1+2 추정",
    f"- 실제 공시값: {fmt_ton(result.actual_scope12)}",
    f"- 회귀모델 추정: {fmt_ton(result.model_estimate)} (±{tree_std:,.0f})",
    f"- 업종평균 추정: {fmt_ton(result.benchmark_estimate)}",
    f"- 방법론 간 편차율: {result.estimate_divergence_ratio * 100:.1f}% "
    f"({'전형적 배출 패턴' if result.is_typical_pattern else '편차가 큼'})",
    "",
    "## Scope 3 확장",
    f"- Scope 3 추정(CDP ×3): {fmt_ton(result.scope3_estimate)}",
    f"- 총 배출량: {fmt_ton(scope12_display + result.scope3_estimate)}",
    "",
    "## 규모 이상치",
    f"- 업종 중앙값 대비 매출 배율: {result.scale_ratio:.2f}배 ({'경고' if result.scale_warning else '정상'})",
    "",
    "## 피처 중요도",
] + [f"- {k}: {v * 100:.1f}%" for k, v in result.feature_importance.items()]
if st.session_state.get("report_text"):
    summary_lines += ["", "## AI 리포트", st.session_state["report_text"]]
summary_md = "\n".join(summary_lines)

st.download_button(
    "📥 분석 결과 다운로드 (.md)",
    data=summary_md.encode("utf-8"),
    file_name=f"{corp['corp_name']}_탄소배출량_분석결과.md",
    mime="text/markdown",
)

st.write("")

# ── AI 리포트 ────────────────────────────────────────────────────────────────
st.markdown('<div class="sec-label"><i></i>Claude API 리포트</div>', unsafe_allow_html=True)
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
        st.markdown(
            '<div class="report-head"><div class="dot"></div><span>CLAUDE-GENERATED REPORT</span></div>',
            unsafe_allow_html=True,
        )
        st.markdown(st.session_state.report_text)
