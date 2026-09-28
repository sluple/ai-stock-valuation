"""DCF(현금흐름할인법) 기반 기업가치 평가.

방법론 및 단순화 가정 (반드시 발표자료/보고서에 명시할 것):
1. FCFF는 이론적 정의(NOPAT + 감가상각비 - CAPEX - 운전자본증감) 대신
   영업활동현금흐름(CFO) - CAPEX 로 근사한다.
   이유: DART 연결현금흐름표에 감가상각비가 별도 라인으로 분리되지 않고
   "조정" 항목에 합산되어 있어, 재구성 시 왜곡 위험이 크기 때문 (실제 삼성전자
   데이터로 확인됨).
2. 세후 이자비용 add-back은 생략한다 (한국 기업은 이자지급을 영업/재무활동 중
   회사 재량으로 분류해 일관된 조정이 어려움). 즉 여기서 계산하는 값은 엄밀한
   교과서적 FCFF가 아니라 "CFO 기반 근사 FCFF"임을 명확히 표기해야 한다.
3. WACC 산출에 필요한 베타·무위험이자율·시장위험프리미엄·타인자본비용은
   MarketAssumptions로 명시적으로 주입받는다 (하드코딩 금지, 출처를 항상 남길 것).
"""
from dataclasses import dataclass

import pandas as pd


@dataclass
class MarketAssumptions:
    risk_free_rate: float          # 무위험이자율 (예: 국고채 3년물 금리)
    beta: float                    # 주가 베타
    market_risk_premium: float     # 시장위험프리미엄 (Rm - Rf)
    cost_of_debt: float            # 세전 타인자본비용 (평균 차입이자율)
    tax_rate: float                # 법인세 실효세율
    terminal_growth_rate: float    # 영구성장률 (장기 GDP 성장률 수준)
    forecast_years: int = 5


def cost_of_equity(a: MarketAssumptions) -> float:
    """CAPM: Re = Rf + beta * 시장위험프리미엄"""
    return a.risk_free_rate + a.beta * a.market_risk_premium


def compute_wacc(a: MarketAssumptions, market_cap: float, total_debt: float) -> float:
    total_capital = market_cap + total_debt
    if total_capital <= 0:
        raise ValueError("market_cap + total_debt 가 0 이하입니다.")
    e_weight = market_cap / total_capital
    d_weight = total_debt / total_capital
    re = cost_of_equity(a)
    rd_after_tax = a.cost_of_debt * (1 - a.tax_rate)
    return e_weight * re + d_weight * rd_after_tax


def compute_fcff_series(df: pd.DataFrame) -> pd.Series:
    """FCFF ≈ 영업활동현금흐름 - CAPEX (단순화 가정 1번 참고)"""
    if not {"영업활동현금흐름", "capex"}.issubset(df.columns):
        raise ValueError("영업활동현금흐름 또는 capex 컬럼이 없습니다.")
    return (df["영업활동현금흐름"] - df["capex"]).rename("FCFF")


def estimate_growth_rate(fcff: pd.Series, min_rate: float = -0.3, max_rate: float = 0.3) -> float:
    """과거 FCFF의 연평균 성장률(CAGR)로 향후 성장률을 추정한다.

    FCFF는 CAPEX 변동(특히 반도체 등 설비투자 사이클)에 따라 연도별 부호가 뒤집히는 경우가
    흔한데, 이 경우 CAGR 자체가 수학적으로 무의미해진다(예: 음수 -> 양수 전환 시 -217% 같은
    값이 나옴). 아래에서는 이런 상황을 감지해 보수적인 기본값으로 대체하고 경고를 출력한다.
    실전에서는 매출 대비 FCFF 마진을 평균 내 추정하는 방식으로 고도화하는 것을 권장한다.
    """
    fcff = fcff.dropna()
    if len(fcff) < 2:
        raise ValueError("성장률을 추정하려면 최소 2개 연도의 FCFF가 필요합니다.")
    n_periods = len(fcff) - 1
    start, end = fcff.iloc[0], fcff.iloc[-1]

    if start <= 0 or end <= 0:
        print(
            f"[경고] FCFF 부호가 기간 중 바뀌어(start={start:,.0f}, end={end:,.0f}) "
            "CAGR을 신뢰할 수 없습니다. 성장률을 0%로 보수적으로 가정합니다."
        )
        return 0.0

    growth = (end / start) ** (1 / n_periods) - 1
    if not (min_rate <= growth <= max_rate):
        clamped = max(min(growth, max_rate), min_rate)
        print(
            f"[경고] 계산된 성장률({growth:.1%})이 비현실적으로 커서 {clamped:.1%}로 제한합니다. "
            "(변동성 큰 FCFF 이력 때문 — 관측 기간을 늘리거나 매출 기반 마진 모델로 개선 권장)"
        )
        return clamped
    return growth


def estimate_revenue_growth(revenue: pd.Series, min_rate: float = -0.15, max_rate: float = 0.15) -> float:
    """매출액 CAGR (마진 기반 투사의 성장률로 사용, FCFF보다 변동성이 훨씬 작다)."""
    revenue = revenue.dropna()
    if len(revenue) < 2 or revenue.iloc[0] <= 0:
        return 0.0
    n_periods = len(revenue) - 1
    growth = (revenue.iloc[-1] / revenue.iloc[0]) ** (1 / n_periods) - 1
    clamped = max(min(growth, max_rate), min_rate)
    if clamped != growth:
        print(f"[정보] 매출 성장률({growth:.1%})을 {clamped:.1%}로 제한합니다.")
    return clamped


def compute_avg_fcff_margin(df: pd.DataFrame) -> float:
    """매출액 대비 FCFF 비율의 평균. CAPEX 사이클 변동을 매출 대비 비율로 흡수시켜 완만하게 만든다."""
    if not {"영업활동현금흐름", "capex", "매출액"}.issubset(df.columns):
        raise ValueError("영업활동현금흐름/capex/매출액 컬럼이 모두 필요합니다.")
    fcff = df["영업활동현금흐름"] - df["capex"]
    margin = (fcff / df["매출액"]).dropna()
    if margin.empty:
        raise ValueError("FCFF 마진을 계산할 데이터가 없습니다.")
    return margin.mean()


def _discount_and_value(
    projected_fcff: list[float],
    assumptions: MarketAssumptions,
    wacc: float,
    net_debt: float,
    shares_outstanding: float,
) -> dict:
    discounted = [cf / (1 + wacc) ** (t + 1) for t, cf in enumerate(projected_fcff)]

    terminal_value = projected_fcff[-1] * (1 + assumptions.terminal_growth_rate) / (
        wacc - assumptions.terminal_growth_rate
    )
    discounted_terminal_value = terminal_value / (1 + wacc) ** assumptions.forecast_years

    enterprise_value = sum(discounted) + discounted_terminal_value
    equity_value = enterprise_value - net_debt
    value_per_share = equity_value / shares_outstanding if shares_outstanding else None

    return {
        "wacc": wacc,
        "projected_fcff": projected_fcff,
        "discounted_fcff": discounted,
        "terminal_value": terminal_value,
        "discounted_terminal_value": discounted_terminal_value,
        "enterprise_value": enterprise_value,
        "net_debt": net_debt,
        "equity_value": equity_value,
        "value_per_share": value_per_share,
    }


def dcf_valuation(
    fcff: pd.Series,
    assumptions: MarketAssumptions,
    wacc: float,
    net_debt: float,
    shares_outstanding: float,
) -> dict:
    """방법 A: 과거 FCFF의 CAGR을 그대로 미래에 적용 (단순하지만 CAPEX 변동에 취약)."""
    last_fcff = fcff.dropna().iloc[-1]
    growth = estimate_growth_rate(fcff)

    projected = []
    cash_flow = last_fcff
    for _ in range(assumptions.forecast_years):
        cash_flow = cash_flow * (1 + growth)
        projected.append(cash_flow)

    result = _discount_and_value(projected, assumptions, wacc, net_debt, shares_outstanding)
    result["method"] = "fcff_cagr"
    result["historical_growth_rate"] = growth
    return result


def dcf_valuation_margin(
    df: pd.DataFrame,
    assumptions: MarketAssumptions,
    wacc: float,
    net_debt: float,
    shares_outstanding: float,
) -> dict:
    """방법 B(권장): 매출 대비 FCFF 평균 마진 x 매출 성장 전망으로 미래 FCFF를 투사.

    삼성전자처럼 설비투자(CAPEX) 사이클이 커서 FCFF 부호가 뒤집히는 기업에서는
    이 방법이 방법 A(CAGR 그대로 적용)보다 훨씬 안정적인 결과를 준다.
    """
    avg_margin = compute_avg_fcff_margin(df)
    revenue_growth = estimate_revenue_growth(df["매출액"])
    last_revenue = df["매출액"].dropna().iloc[-1]

    projected_revenue = []
    revenue = last_revenue
    for _ in range(assumptions.forecast_years):
        revenue = revenue * (1 + revenue_growth)
        projected_revenue.append(revenue)

    projected_fcff = [r * avg_margin for r in projected_revenue]

    result = _discount_and_value(projected_fcff, assumptions, wacc, net_debt, shares_outstanding)
    result["method"] = "revenue_margin"
    result["avg_fcff_margin"] = avg_margin
    result["revenue_growth_rate"] = revenue_growth
    return result
