"""AI 탄소배출량(Scope 1/2/3) 추정 대시보드 (Streamlit).

"보고서에 적힌 것만, 전부 시각적으로" 컨셉 — 시스템 구성 10개 항목과 이중 추정
구조/신뢰도 장치 4개 항목을 각각 전용 시각 컴포넌트로 보여준다.

실행: streamlit run app.py
"""
import streamlit as st

import analysis
import db
import fetch_dart
from llm_report import generate_report

st.set_page_config(page_title="AI 탄소배출량 추정기", page_icon="🌍", layout="wide")

# ── 팔레트: dataviz 스킬 검증 팔레트(카테고리컬 slot1=blue/slot2=orange, 상태색) ──
SURFACE = "#fcfcfb"
PAGE = "#f9f9f7"
INK = "#0b0b0b"
INK2 = "#52514e"
MUT = "#898781"
LINE = "#e1e0d9"
BASELINE = "#c3c2b7"
BLUE = "#2a78d6"      # 카테고리컬 slot 1 — 회귀모델
ORANGE = "#eb6834"    # 카테고리컬 slot 2 — 업종평균
GOOD = "#0ca30c"
WARNING = "#fab219"
SERIOUS = "#ec835a"
GOOD_TINT = "rgba(12,163,12,0.12)"
WARNING_TINT = "rgba(250,178,25,0.18)"

SYSTEM_COMPONENTS = [
    ("🖥", "Streamlit 웹 대시보드", "Plotly 차트 렌더링 + 회사명 검색·결과 화면 단일 페이지"),
    ("🔎", "회사명 검색 플로우", "코드가 아닌 이름으로 검색 → 결과 화면까지 한 흐름"),
    ("🐍", "분석 파이프라인", "재무데이터 조회 / 추정 모델 / 분석 오케스트레이션 모듈"),
    ("📄", "DART 재무제표 조회", "전자공시시스템에서 매출액·매출원가 실시간 조회"),
    ("🌲", "RandomForestRegressor", "scikit-learn, 타겟 인코딩 방식으로 업종을 압축"),
    ("📊", "EEIO 업종평균 벤치마크", "업종별 배출집약도 중앙값 계산"),
    ("💾", "모델 파일 직렬화", "학습된 모델을 pickle로 저장, 배포 서버가 즉시 로드"),
    ("🗄", "Supabase(PostgreSQL)", "모든 분석 이력 저장"),
    ("🏭", "K-ETS 실측 데이터 대조", "온실가스종합정보센터 공시 배출량과 자동 매칭"),
    ("✨", "Claude API 리포트 생성", "비전문가용 자연어 설명 리포트 자동 생성"),
]


def fmt_ton(x: float | None) -> str:
    return f"{x:,.0f} tCO2eq" if x is not None else "정보 없음"


def fmt_won(x: float | None) -> str:
    return f"{x:,.0f}원" if x is not None else "정보 없음"


# ── 전역 스타일 ──────────────────────────────────────────────────────────────
st.markdown(
    f"""
    <link rel="preconnect" href="https://cdn.jsdelivr.net" crossorigin>
    <link rel="stylesheet"
        href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css">
    <style>
        #MainMenu {{ visibility: hidden; }}
        footer {{ visibility: hidden; }}
        header[data-testid="stHeader"] {{ background: transparent; height: 0; }}
        [data-testid="stToolbar"] {{ visibility: hidden; }}
        [data-testid="stSidebarCollapsedControl"] {{ display: none; }}
        .stDeployButton {{ display: none; }}
        #stDecoration {{ display: none; }}

        html, body, [class*="css"] {{
            font-family: "Pretendard Variable", Pretendard, -apple-system, "Segoe UI", system-ui, sans-serif;
        }}
        .stApp {{ background: {PAGE}; }}
        .block-container {{ padding-top: 1.4rem; max-width: 1120px; }}

        h1, h2, h3, h4 {{ font-weight: 800 !important; letter-spacing: -0.015em; color: {INK}; }}

        div[data-testid="stExpander"] {{ border: 1px solid {LINE}; border-radius: 10px; background: {SURFACE}; }}
        div[data-testid="stVerticalBlockBorderWrapper"] {{
            border-radius: 12px !important; border-color: {LINE} !important; background: {SURFACE};
        }}

        /* Streamlit 기본 경고/정보 박스는 브라우저 다크모드 감지에 따라 내부 글자색이
           배경색과 거의 같아져 안 보이는 사고가 난다 — 명시적으로 짙은 글자색을 강제한다. */
        div[data-testid="stAlert"] * {{ color: {INK} !important; }}

        .stButton > button[kind="primary"] {{
            background: {INK} !important; color: #fff !important; border: none !important;
            font-weight: 700 !important; border-radius: 8px !important;
        }}
        .stButton > button[kind="primary"]:hover {{ background: #2a2a28 !important; }}
        .stButton > button[kind="primary"]:disabled {{ background: {LINE} !important; color: {MUT} !important; }}

        .stTabs [data-baseweb="tab-list"] {{ gap: 4px; border-bottom: 1px solid {LINE}; }}
        .stTabs [data-baseweb="tab"] {{ font-weight: 700; color: {MUT}; }}
        .stTabs [aria-selected="true"] {{ color: {INK} !important; }}

        .page-title {{ font-size: 1.5rem; font-weight: 800; color: {INK}; margin-bottom: 0.1rem; }}
        .page-sub {{ color: {INK2}; font-size: 0.92rem; margin-bottom: 1rem; }}

        .sec-label {{
            display: inline-flex; align-items: center; gap: 6px; font-size: 0.72rem; font-weight: 800;
            letter-spacing: 0.04em; color: {MUT}; text-transform: uppercase; margin: 1.1rem 0 0.5rem 0;
        }}
        .sec-label i {{ width: 5px; height: 5px; border-radius: 50%; background: {BLUE}; display: inline-block; }}

        .comp-grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; margin-bottom: 0.3rem; }}
        .comp-card {{
            background: {SURFACE}; border: 1px solid {LINE}; border-radius: 10px; padding: 10px 11px;
        }}
        .comp-card .ic {{ font-size: 1.1rem; }}
        .comp-card .ti {{ font-weight: 700; font-size: 0.78rem; color: {INK}; margin-top: 3px; line-height: 1.25; }}
        .comp-card .de {{ font-size: 0.68rem; color: {MUT}; margin-top: 2px; line-height: 1.3; }}
        @media (max-width: 900px) {{ .comp-grid {{ grid-template-columns: repeat(2, 1fr); }} }}

        h5 {{ margin: 0 0 0.15rem 0; font-size: 0.98rem; font-weight: 800; color: {INK} !important; }}
        .viz-desc {{ font-size: 0.8rem; color: {INK2}; margin-bottom: 0.6rem; }}

        .badge {{ display: inline-flex; align-items: center; gap: 6px; font-weight: 800; font-size: 0.8rem; padding: 0.3rem 0.75rem; border-radius: 100px; }}
        .badge-good {{ background: {GOOD_TINT}; color: #0a7a0a; }}
        .badge-warn {{ background: {WARNING_TINT}; color: #8a6200; }}
    </style>
    """,
    unsafe_allow_html=True,
)


def svg_number_line(points, highlight=None, width=660, height=96, value_fmt=None, unit=""):
    """points: [{label, value, color}], highlight: (lo, hi, color_tint) or None."""
    value_fmt = value_fmt or (lambda v: f"{v:,.0f}")
    vals = [p["value"] for p in points]
    vmax = max(vals) * 1.15 if max(vals) > 0 else 1.0
    vmin = 0
    ml, mr = 20, 20
    plot_w = width - ml - mr
    base_y = height - 34

    def x(v):
        return ml + (v - vmin) / (vmax - vmin) * plot_w

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" xmlns="http://www.w3.org/2000/svg">']
    if highlight:
        lo, hi, tint = highlight
        parts.append(
            f'<rect x="{x(lo):.1f}" y="{base_y - 16}" width="{(x(hi) - x(lo)):.1f}" height="32" rx="6" fill="{tint}"/>'
        )
    parts.append(f'<line x1="{ml}" y1="{base_y}" x2="{width - mr}" y2="{base_y}" stroke="{BASELINE}" stroke-width="2"/>')

    sorted_pts = sorted(points, key=lambda p: p["value"])
    for i, p in enumerate(sorted_pts):
        cx = x(p["value"])
        above = (i % 2 == 0)
        ly = base_y - 20 if above else base_y + 34
        parts.append(f'<circle cx="{cx:.1f}" cy="{base_y}" r="7" fill="{p["color"]}" stroke="{SURFACE}" stroke-width="2.5"/>')
        label_text = f'{p["label"]} · {value_fmt(p["value"])}{unit}'
        parts.append(
            f'<text x="{cx:.1f}" y="{ly}" text-anchor="middle" font-size="11.5" font-weight="700" fill="{INK}">{label_text}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def svg_divergence_meter(ratio, threshold=0.30, width=660, height=58):
    cap = 1.5
    display_ratio = min(ratio, cap)
    track_w = width - 40
    fill_color = GOOD if ratio <= threshold else WARNING
    fill_w = max(6, display_ratio / cap * track_w)
    thresh_x = 20 + threshold / cap * track_w
    overflow = ratio > cap
    label = f"{ratio * 100:,.0f}%" if not overflow else f"{ratio * 100:,.0f}% (그래프 범위 초과)"
    return f"""
    <svg viewBox="0 0 {width} {height}" width="100%" height="{height}" xmlns="http://www.w3.org/2000/svg">
      <rect x="20" y="22" width="{track_w}" height="14" rx="7" fill="{LINE}"/>
      <rect x="20" y="22" width="{fill_w:.1f}" height="14" rx="7" fill="{fill_color}"/>
      <line x1="{thresh_x:.1f}" y1="14" x2="{thresh_x:.1f}" y2="42" stroke="{INK2}" stroke-width="1.5" stroke-dasharray="3,2"/>
      <text x="{thresh_x:.1f}" y="52" text-anchor="middle" font-size="10.5" fill="{MUT}" font-weight="700">30% 기준선</text>
      <text x="20" y="12" font-size="13" font-weight="800" fill="{INK}">편차율 {label}</text>
    </svg>
    """


def svg_scope3_multiplier(scope12, scope3, width=660, height=150):
    ml = 20
    unit_w = 150
    unit_h = 46
    gap = 2
    y1 = 34
    y2 = 104
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" xmlns="http://www.w3.org/2000/svg">']
    parts.append(f'<rect x="{ml}" y="{y1}" width="{unit_w}" height="{unit_h}" rx="8" fill="{BLUE}"/>')
    parts.append(f'<text x="{ml + unit_w / 2}" y="{y1 - 8}" text-anchor="middle" font-size="12" font-weight="800" fill="{INK}">Scope 1+2</text>')
    parts.append(f'<text x="{ml + unit_w / 2}" y="{y1 + unit_h / 2 + 5}" text-anchor="middle" font-size="12.5" font-weight="800" fill="#fff">{scope12:,.0f}</text>')

    times_x = ml + unit_w + 26
    parts.append(f'<text x="{times_x}" y="{(y1 + y2) / 2 + unit_h / 2 - 2}" text-anchor="middle" font-size="22" font-weight="800" fill="{MUT}">×3</text>')

    bx = times_x + 26
    for i in range(3):
        seg_x = bx + i * (unit_w + gap)
        parts.append(f'<rect x="{seg_x:.1f}" y="{y2}" width="{unit_w}" height="{unit_h}" rx="8" fill="{ORANGE}"/>')
    total_w = unit_w * 3 + gap * 2
    parts.append(f'<text x="{bx + total_w / 2:.1f}" y="{y2 - 8}" text-anchor="middle" font-size="12" font-weight="800" fill="{INK}">Scope 3 (추정)</text>')
    parts.append(f'<text x="{bx + total_w / 2:.1f}" y="{y2 + unit_h / 2 + 5:.1f}" text-anchor="middle" font-size="12.5" font-weight="800" fill="#fff">{scope3:,.0f}</text>')
    parts.append("</svg>")
    return "".join(parts)


def svg_scale_strip(revenue, median_revenue, ratio, scale_warning, width=660, height=96):
    import math
    ml, mr = 20, 20
    plot_w = width - ml - mr
    lo_log = math.log10(max(median_revenue, 1))
    hi_log = math.log10(max(revenue, median_revenue * 5, 1)) + 0.25
    lo_log = min(lo_log, hi_log - 0.5)

    def x(v):
        v = max(v, 1)
        return ml + (math.log10(v) - lo_log) / (hi_log - lo_log) * plot_w

    base_y = height - 34
    warn_x = x(median_revenue * 5)
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" xmlns="http://www.w3.org/2000/svg">']
    parts.append(f'<rect x="{warn_x:.1f}" y="{base_y - 16}" width="{max(0, width - mr - warn_x):.1f}" height="32" rx="6" fill="{WARNING_TINT}"/>')
    parts.append(f'<line x1="{ml}" y1="{base_y}" x2="{width - mr}" y2="{base_y}" stroke="{BASELINE}" stroke-width="2"/>')
    parts.append(f'<line x1="{warn_x:.1f}" y1="{base_y - 20}" x2="{warn_x:.1f}" y2="{base_y + 20}" stroke="{WARNING}" stroke-width="1.5" stroke-dasharray="3,2"/>')
    parts.append(f'<text x="{warn_x:.1f}" y="{base_y + 34}" text-anchor="middle" font-size="10.5" fill="#8a6200" font-weight="700">5배 경고선</text>')

    mx = x(median_revenue)
    parts.append(f'<circle cx="{mx:.1f}" cy="{base_y}" r="7" fill="{MUT}" stroke="{SURFACE}" stroke-width="2.5"/>')
    parts.append(f'<text x="{mx:.1f}" y="{base_y - 20}" text-anchor="middle" font-size="11" font-weight="700" fill="{INK}">업종 매출 중앙값</text>')

    cx = x(revenue)
    color = WARNING if scale_warning else GOOD
    parts.append(f'<circle cx="{cx:.1f}" cy="{base_y}" r="7" fill="{color}" stroke="{SURFACE}" stroke-width="2.5"/>')
    parts.append(f'<text x="{cx:.1f}" y="{base_y + 34 if cx - mx < 60 else base_y - 20}" text-anchor="middle" font-size="11" font-weight="700" fill="{INK}">이 기업 ({ratio:.1f}배)</text>')
    parts.append("</svg>")
    return "".join(parts)


# ── 상단 바 ──────────────────────────────────────────────────────────────────
st.markdown('<div class="page-title">AI 탄소배출량 추정기</div>', unsafe_allow_html=True)
st.markdown('<div class="page-sub">재무데이터로 Scope 1·2·3을 추정하고, 추정 과정 자체를 그대로 보여줍니다.</div>', unsafe_allow_html=True)

if "sb_client" not in st.session_state:
    st.session_state.sb_client = db.create_client()
sb = st.session_state.sb_client

if "corp_cache_warm" not in st.session_state:
    with st.spinner("기업 목록을 불러오는 중이에요 (최초 1회, 몇 초 걸려요)..."):
        fetch_dart.warm_up_corp_cache()
    st.session_state.corp_cache_warm = True

try:
    industries = analysis.list_known_industries()
except FileNotFoundError:
    industries = []
    st.error("학습된 모델이 없습니다. `python build_emissions_dataset.py` 후 `python train_model.py`를 먼저 실행하세요.")

with st.container(border=True):
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
        st.caption("검색 결과가 없어요.")

    with c2:
        run_button = st.button("분석 시작", type="primary", width="stretch", disabled=selected_corp is None)

    with st.expander("업종을 직접 선택하고 싶다면 (배출권거래제 공시 대상 기업은 자동 감지)"):
        industry_choice = st.selectbox("업종", ["(자동 감지)"] + industries, label_visibility="collapsed")

# ── 시스템 구성: 보고서의 10개 항목을 그대로 카드 그리드로 ────────────────────
st.markdown('<div class="sec-label"><i></i>이 시스템은 이렇게 만들어졌습니다 — 시스템 구성</div>', unsafe_allow_html=True)
cards_html = '<div class="comp-grid">'
for icon, title, desc in SYSTEM_COMPONENTS:
    cards_html += f'<div class="comp-card"><div class="ic">{icon}</div><div class="ti">{title}</div><div class="de">{desc}</div></div>'
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
    f'<div class="sec-label"><i></i>{corp["corp_name"]} · {result.industry} · 매출 {fmt_won(result.financials.get("매출액"))}</div>',
    unsafe_allow_html=True,
)

for w in result.warnings:
    st.warning(w)

# ── 1) 실측값·회귀모델·업종평균 세 수치 비교 ──────────────────────────────────
with st.container(border=True):
    st.markdown("<h5>① 세 가지 방법으로 Scope 1+2를 각각 추정해 비교합니다</h5>", unsafe_allow_html=True)
    st.markdown(
        '<div class="viz-desc">실측값(있는 경우)·회귀모델·업종평균을 같은 선 위에 놓고, 서로 얼마나 가까운지로 신뢰도를 판단합니다.</div>',
        unsafe_allow_html=True,
    )
    points = []
    if result.actual_scope12 is not None:
        points.append({"label": "실제 공시값", "value": result.actual_scope12, "color": INK})
    points.append({"label": "회귀모델 추정", "value": result.model_estimate, "color": BLUE})
    points.append({"label": "업종평균 추정", "value": result.benchmark_estimate, "color": ORANGE})
    vals = [p["value"] for p in points]
    lo, hi = min(vals), max(vals) * (1 + analysis.TYPICAL_PATTERN_THRESHOLD)
    tint = GOOD_TINT if result.is_typical_pattern else WARNING_TINT
    svg = svg_number_line(points, highlight=(min(vals), lo * (1 + analysis.TYPICAL_PATTERN_THRESHOLD), tint), value_fmt=lambda v: f"{v:,.0f}", unit=" t")
    st.markdown(svg, unsafe_allow_html=True)
    badge_cls = "badge-good" if result.is_typical_pattern else "badge-warn"
    badge_txt = "업종 내 전형적인 배출 패턴" if result.is_typical_pattern else "방법론 간 편차가 큼"
    st.markdown(f'<span class="badge {badge_cls}">{badge_txt}</span>', unsafe_allow_html=True)

st.write("")

# ── 2) 편차율 30% 기준선 ──────────────────────────────────────────────────────
with st.container(border=True):
    st.markdown("<h5>② 편차율이 30% 기준선 안에 들어오는지 측정합니다</h5>", unsafe_allow_html=True)
    st.markdown(
        '<div class="viz-desc">(최댓값 − 최솟값) ÷ 최솟값. 30% 이내면 "전형적 배출 패턴"으로 판정합니다.</div>',
        unsafe_allow_html=True,
    )
    st.markdown(svg_divergence_meter(result.estimate_divergence_ratio), unsafe_allow_html=True)

st.write("")

# ── 3) Scope 3 = Scope1+2 × 3 ─────────────────────────────────────────────────
with st.container(border=True):
    st.markdown("<h5>③ Scope 3는 CDP 배율(×3)을 곱해 확장 산출합니다</h5>", unsafe_allow_html=True)
    st.markdown(
        '<div class="viz-desc">CDP 조사: Scope 3가 전체 배출량(1+2+3)의 평균 약 75%를 차지 → Scope 3 ≈ Scope 1+2 × 3</div>',
        unsafe_allow_html=True,
    )
    st.markdown(svg_scope3_multiplier(scope12_display, result.scope3_estimate), unsafe_allow_html=True)

st.write("")

# ── 4) 규모 이상치 경고 (매출 5배 기준) ───────────────────────────────────────
with st.container(border=True):
    st.markdown("<h5>④ 매출 규모가 업종 중앙값의 5배를 넘는지 확인합니다</h5>", unsafe_allow_html=True)
    st.markdown(
        '<div class="viz-desc">넘으면 복합기업·지주회사일 가능성이 높아 추정 신뢰도가 낮아질 수 있다는 경고를 띄웁니다.</div>',
        unsafe_allow_html=True,
    )
    revenue = result.financials.get("매출액", 0) or 0
    median_revenue = revenue / result.scale_ratio if result.scale_ratio else revenue
    st.markdown(
        svg_scale_strip(revenue, median_revenue, result.scale_ratio, result.scale_warning),
        unsafe_allow_html=True,
    )

st.write("")

# ── AI 리포트 / 분석 이력 ──────────────────────────────────────────────────────
tab1, tab2 = st.tabs(["Claude API 리포트", "Supabase 분석 이력"])

with tab1:
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

with tab2:
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
