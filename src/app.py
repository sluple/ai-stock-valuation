"""주식 밸류에이션 AI 대시보드 (Streamlit).

실행: streamlit run app.py
"""
import plotly.graph_objects as go
import streamlit as st

import db
from analysis import build_llm_context, run_analysis
from llm_report import generate_report

st.set_page_config(page_title="AI 주식 가치 분석기", page_icon="chart", layout="wide")

# 카테고리컬 색상은 고정 순서로 사용한다 (지표별 고정 의미 부여, 필터링 시에도 색 유지)
COLOR_A = "#2563EB"        # 방법 A / 1차 지표
COLOR_B = "#F97316"        # 방법 B / 2차 지표
COLOR_TERTIARY = "#059669"  # 3차 지표 (ROE, EV/EBIT 등)
COLOR_CURRENT = "#6B7280"  # 현재가 (중립 회색, 기준선)

GLOSSARY = [
    ("적정주가 (DCF)", "회사가 앞으로 벌어들일 현금을 예상해서, 지금 이 주식이 얼마짜리인지 역산한 값이에요. '이론적으로 계산한 몸값'이라고 생각하면 됩니다."),
    ("비교가치 (상대가치평가)", "비슷한 업종의 다른 회사와 비교해서 '이 정도는 받아야 한다'고 추정한 가격이에요. 예: 옆 가게 커피가 5,000원이면 우리 가게도 비슷해야 한다는 논리."),
    ("PER", "주가가 1주당 순이익의 몇 배인지 나타내는 숫자예요. 낮을수록 '이익 대비 싸다'는 뜻으로 흔히 해석돼요."),
    ("PBR", "주가가 1주당 순자산(회사가 가진 재산)의 몇 배인지 나타내는 숫자예요."),
    ("EV/EBIT", "회사 전체 가치가 1년 영업이익의 몇 배인지 나타내는 숫자예요. PER과 비슷하지만 빚까지 포함해서 계산해요."),
    ("WACC (자금조달비용)", "회사가 사업 자금을 마련하는 데 드는 평균 비용(%)이에요. 이 비율로 미래 현금을 '오늘 가치'로 할인해서 계산해요."),
    ("회사가 실제로 번 현금 (FCFF)", "회사가 영업으로 벌어들인 현금에서, 설비 투자 등에 쓴 돈을 뺀 '진짜 남는 현금'이에요."),
    ("피어 기업", "같은 업종에 속해서 비교 대상으로 삼는 다른 회사예요."),
]


def fmt_won(x: float | None) -> str:
    return f"{x:,.0f}원" if x is not None else "정보 없음"


st.title("AI 주식 가치 분석기")
st.caption("공시된 재무제표를 자동으로 읽어서 '이 주식이 지금 비싼지 싼지'를 두 가지 방법으로 계산하고, AI가 쉽게 풀어서 설명해줍니다.")

with st.expander("어려운 용어가 있으면 여기를 눌러 확인하세요"):
    for term, desc in GLOSSARY:
        st.markdown(f"**{term}** — {desc}")

with st.sidebar:
    st.header("분석하고 싶은 회사")
    target_code = st.text_input(
        "종목코드", value="005930", help="회사마다 있는 6자리 고유번호예요. 예: 삼성전자 = 005930"
    )
    peer_input = st.text_input(
        "비교할 같은 업종 회사 (쉼표로 구분)",
        value="000660",
        help="예: SK하이닉스 = 000660. 여러 개면 쉼표로 구분하세요.",
    )
    generate_ai_report = st.checkbox("AI 요약 리포트 만들기", value=True)
    run_button = st.button("분석 시작", type="primary", width="stretch")

if "result" not in st.session_state:
    st.session_state.result = None
    st.session_state.error = None

if run_button:
    peer_codes = [c.strip() for c in peer_input.split(",") if c.strip()]
    with st.spinner("재무제표를 불러오고 계산하는 중이에요..."):
        try:
            st.session_state.result = run_analysis(target_code, peer_codes=peer_codes)
            st.session_state.error = None
            st.session_state.report_text = None
            st.session_state.report_key = None
            st.session_state.record_id = None
            try:
                context = build_llm_context(st.session_state.result, int(st.session_state.result.df.index[-1]))
                st.session_state.record_id = db.save_analysis(
                    st.session_state.result, peer_codes, None, context
                )
            except Exception:  # noqa: BLE001
                pass  # 기록 저장은 부가 기능이므로 실패해도 분석 자체는 계속 보여준다
        except Exception as e:  # noqa: BLE001
            st.session_state.result = None
            st.session_state.error = str(e)

if st.session_state.error:
    st.error(f"분석에 실패했어요: {st.session_state.error}")

result = st.session_state.result

if result is None:
    st.info("왼쪽에서 종목코드를 입력하고 '분석 시작'을 눌러주세요.")
    st.stop()

corp = result.corp
st.subheader(f"{corp['corp_name']} ({corp['stock_code']})")

for w in result.warnings:
    st.warning(w)

col1, col2, col3, col4 = st.columns(4)
col1.metric("지금 주가", fmt_won(result.current_price))
col2.metric("자금조달비용 (WACC)", f"{result.wacc:.2%}", help="회사가 돈을 조달하는 데 드는 평균 비용이에요. 적정주가 계산에 쓰여요.")
col3.metric(
    "적정주가 · 방법 A",
    fmt_won(result.result_cagr["value_per_share"]),
    help="과거 현금 흐름의 증가 속도를 그대로 미래에 적용해서 계산한 값이에요.",
)
col4.metric(
    "적정주가 · 방법 B (추천)",
    fmt_won(result.result_margin["value_per_share"]),
    help="매출 대비 남는 현금 비율의 평균을 이용해서 계산한 값이에요. 설비투자가 큰 해에도 덜 흔들려요.",
)

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["회사 성적표", "① 적정주가 계산 (DCF)", "② 비슷한 회사와 비교", "AI 요약 리포트", "지난 분석 기록"]
)

with tab1:
    st.markdown("#### 최근 몇 년간 얼마나 돈을 잘 벌었나")
    ratio_df = result.ratio_df
    fig = go.Figure()
    series = [
        ("영업이익률", "본업으로 남긴 이익 비율", COLOR_A),
        ("순이익률", "전체 이익 비율", COLOR_B),
        ("ROE", "주주 돈 대비 수익률", COLOR_TERTIARY),
    ]
    for col, nickname, color in series:
        if col in ratio_df.columns:
            fig.add_trace(
                go.Scatter(
                    x=ratio_df.index,
                    y=ratio_df[col] * 100,
                    mode="lines+markers",
                    name=f"{col} ({nickname})",
                    line=dict(color=color, width=2),
                    marker=dict(size=8),
                    hovertemplate="%{x}년: %{y:.1f}%<extra>" + col + "</extra>",
                )
            )
    fig.update_layout(
        yaxis_title="비율 (%)",
        xaxis_title="연도",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(t=40, b=20),
        height=380,
    )
    st.plotly_chart(fig, width="stretch")

    st.markdown("#### 원본 재무 데이터 (단위: 원)")
    st.caption("전자공시시스템(DART)에서 자동으로 가져온 숫자예요.")
    st.dataframe(result.df.T.style.format("{:,.0f}", na_rep="-"), width="stretch")

with tab2:
    st.markdown("#### 방법 A vs 방법 B vs 지금 주가, 비교")
    labels = ["방법 A", "방법 B (추천)"]
    values = [result.result_cagr["value_per_share"], result.result_margin["value_per_share"]]
    colors = [COLOR_A, COLOR_B]
    if result.current_price is not None:
        labels.append("지금 주가")
        values.append(result.current_price)
        colors.append(COLOR_CURRENT)

    fig2 = go.Figure(
        go.Bar(
            x=labels,
            y=values,
            marker_color=colors,
            text=[fmt_won(v) for v in values],
            textposition="outside",
        )
    )
    fig2.update_layout(yaxis_title="원", margin=dict(t=30, b=20), height=380, showlegend=False)
    st.plotly_chart(fig2, width="stretch")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**방법 A: 과거 성장 속도를 그대로 적용**")
        st.write(f"- 최근 현금 증가율: {result.result_cagr['historical_growth_rate']:.2%}")
        st.write(f"- 회사 전체 가치: {fmt_won(result.result_cagr['enterprise_value'])}")
        st.write(f"- 주주 몫 가치: {fmt_won(result.result_cagr['equity_value'])}")
    with c2:
        st.markdown("**방법 B: 매출 대비 현금 비율로 계산 (추천)**")
        st.write(f"- 매출 대비 평균 현금 비율: {result.result_margin['avg_fcff_margin']:.2%}")
        st.write(f"- 매출 성장 가정: {result.result_margin['revenue_growth_rate']:.2%}")
        st.write(f"- 주주 몫 가치: {fmt_won(result.result_margin['equity_value'])}")

    st.caption(
        "이 계산은 회사가 설비에 얼마를 투자했는지를 감안한 '진짜 남는 현금'을 기준으로 해요. "
        "방법 A는 과거 증가 속도를 그대로 미래에 적용하고, 방법 B는 매출 대비 평균 비율을 써서 "
        "설비 투자가 유독 컸던 해의 영향을 덜 받아요."
    )

with tab3:
    st.markdown("#### 비슷한 회사와 비교했을 때 적정 가격은?")
    tm = result.target_multiples
    if tm:
        per_s = f"{tm.per:.1f}배" if tm.per else "정보 없음"
        pbr_s = f"{tm.pbr:.1f}배" if tm.pbr else "정보 없음"
        ev_s = f"{tm.ev_ebit:.1f}배" if tm.ev_ebit else "정보 없음"
        st.write(f"**{tm.name} (분석 대상)** — PER {per_s} · PBR {pbr_s} · EV/EBIT {ev_s}")
    else:
        st.write("지금 주가 정보를 못 가져와서 이 계산은 할 수 없어요.")
    for pm in result.peer_multiples:
        per_s = f"{pm.per:.1f}배" if pm.per else "정보 없음"
        pbr_s = f"{pm.pbr:.1f}배" if pm.pbr else "정보 없음"
        ev_s = f"{pm.ev_ebit:.1f}배" if pm.ev_ebit else "정보 없음"
        st.write(f"**{pm.name} (비교 대상 회사)** — PER {per_s} · PBR {pbr_s} · EV/EBIT {ev_s}")

    if result.implied:
        implied = result.implied
        labels3 = ["PER로 계산", "PBR로 계산", "EV/EBIT로 계산"]
        values3 = [
            implied["value_per_share_per"],
            implied["value_per_share_pbr"],
            implied["value_per_share_ev_ebit"],
        ]
        fig3 = go.Figure(
            go.Bar(
                x=labels3,
                y=values3,
                marker_color=[COLOR_A, COLOR_B, COLOR_TERTIARY],
                text=[fmt_won(v) for v in values3],
                textposition="outside",
            )
        )
        if result.current_price is not None:
            fig3.add_hline(
                y=result.current_price,
                line_dash="dash",
                line_color=COLOR_CURRENT,
                annotation_text=f"지금 주가 {result.current_price:,.0f}원",
            )
        fig3.update_layout(yaxis_title="원", margin=dict(t=30, b=20), height=380, showlegend=False)
        st.plotly_chart(fig3, width="stretch")
        st.caption("비교 대상 회사가 적을수록, 그 회사 한 곳의 특이한 사정에 결과가 쏠릴 수 있어요.")
    else:
        st.info("비교할 회사 데이터가 부족해서 이 계산은 할 수 없어요.")

with tab4:
    st.markdown("#### AI가 쉽게 풀어서 설명해주는 리포트")
    if not generate_ai_report:
        st.info("왼쪽에서 'AI 요약 리포트 만들기'를 체크하면 생성돼요.")
    else:
        cache_key = (target_code, peer_input)
        if st.session_state.get("report_key") != cache_key:
            with st.spinner("AI가 리포트를 쓰는 중이에요..."):
                try:
                    context = build_llm_context(result, int(result.df.index[-1]))
                    st.session_state.report_text = generate_report(context)
                    st.session_state.report_key = cache_key
                    try:
                        db.update_report(st.session_state.get("record_id"), st.session_state.report_text)
                    except Exception:  # noqa: BLE001
                        pass  # 기록 저장 실패는 화면에 보여줄 필요 없는 부가 기능
                except Exception as e:  # noqa: BLE001
                    st.session_state.report_text = None
                    st.error(f"리포트 생성에 실패했어요: {e}")
        if st.session_state.get("report_text"):
            st.markdown(st.session_state.report_text)

with tab5:
    st.markdown("#### 지금까지 분석했던 기록")
    if not db.is_enabled():
        st.info(
            "기록 저장 기능이 꺼져 있어요. SUPABASE_URL / SUPABASE_KEY 환경변수를 설정하면 "
            "분석할 때마다 자동으로 기록이 쌓여요."
        )
    else:
        try:
            rows = db.fetch_recent_analyses(limit=20)
        except Exception as e:  # noqa: BLE001
            rows = []
            st.error(f"기록을 불러오지 못했어요: {e}")
        if rows:
            import pandas as pd

            history_df = pd.DataFrame(rows)[
                ["created_at", "corp_name", "stock_code", "current_price", "dcf_value_a", "dcf_value_b"]
            ].rename(
                columns={
                    "created_at": "분석 시각",
                    "corp_name": "회사명",
                    "stock_code": "종목코드",
                    "current_price": "당시 주가",
                    "dcf_value_a": "적정주가(방법A)",
                    "dcf_value_b": "적정주가(방법B)",
                }
            )
            st.dataframe(history_df, width="stretch")
        else:
            st.info("아직 쌓인 기록이 없어요. 분석을 실행하면 여기에 쌓여요.")
