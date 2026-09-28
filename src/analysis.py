"""전체 분석 파이프라인을 하나의 함수로 묶는다.

CLI(main.py)와 웹 대시보드(app.py)가 동일한 로직을 중복 없이 재사용하기 위한 모듈.
"""
from dataclasses import dataclass, field

import pandas as pd

from dcf import MarketAssumptions, compute_fcff_series, compute_wacc, dcf_valuation, dcf_valuation_margin
from fetch_dart import find_corp_code, get_common_shares_outstanding, get_financial_statements
from market_data import get_krx_price
from normalize import get_standardized_financials
from ratios import compute_ratios
from relative_valuation import CompanyMultiples, compute_multiples, implied_value_from_peers

DEFAULT_YEARS = [2020, 2021, 2022, 2023, 2024]


def default_assumptions() -> MarketAssumptions:
    return MarketAssumptions(
        risk_free_rate=0.028,        # TODO: 한국은행 국고채 3년물 최신 금리로 교체
        beta=1.0,                    # TODO: 증권사 HTS/블룸버그 베타값으로 교체
        market_risk_premium=0.05,    # 통상적으로 쓰이는 한국 시장 추정치
        cost_of_debt=0.04,           # TODO: 회사 평균 차입이자율로 교체
        tax_rate=0.22,               # 실효세율은 실행 시 실제값으로 덮어씀
        terminal_growth_rate=0.02,   # 장기 GDP 성장률 수준 가정
        forecast_years=5,
    )


@dataclass
class AnalysisResult:
    corp: dict
    df: pd.DataFrame
    ratio_df: pd.DataFrame
    fcff: pd.Series
    net_debt: float
    shares: int | None
    current_price: float | None
    market_cap: float
    wacc: float
    assumptions: MarketAssumptions
    result_cagr: dict
    result_margin: dict
    target_multiples: CompanyMultiples | None
    peer_multiples: list[CompanyMultiples] = field(default_factory=list)
    implied: dict | None = None
    warnings: list[str] = field(default_factory=list)


def run_analysis(
    target_code: str,
    peer_codes: list[str] | None = None,
    years: list[int] | None = None,
) -> AnalysisResult:
    peer_codes = peer_codes or []
    years = years or DEFAULT_YEARS
    warnings: list[str] = []

    matches = find_corp_code(target_code)
    if not matches:
        raise ValueError(f"'{target_code}' 기업을 찾지 못했습니다.")
    corp = matches[0]

    df = get_standardized_financials(get_financial_statements, corp["corp_code"], years)
    ratio_df = compute_ratios(df)

    assumptions = default_assumptions()
    if "실효세율" in ratio_df.columns and pd.notna(ratio_df["실효세율"].iloc[-1]):
        assumptions.tax_rate = float(ratio_df["실효세율"].iloc[-1])

    fcff = compute_fcff_series(df)

    total_debt = df["총차입금"].iloc[-1] if "총차입금" in df.columns else 0.0
    cash = df["현금및현금성자산"].iloc[-1] if "현금및현금성자산" in df.columns else 0.0
    net_debt = total_debt - cash

    shares = get_common_shares_outstanding(corp["corp_code"], years[-1])
    current_price = get_krx_price(corp["stock_code"])

    if current_price is not None and shares:
        market_cap = current_price * shares
    else:
        market_cap = df["자본총계"].iloc[-1]
        warnings.append("실시간 시세 조회 실패 -> 자본총계(장부가)를 시가총액 근사치로 사용 (WACC 정확도 낮음)")

    wacc = compute_wacc(assumptions, market_cap=market_cap, total_debt=total_debt)

    result_cagr = dcf_valuation(
        fcff=fcff, assumptions=assumptions, wacc=wacc, net_debt=net_debt, shares_outstanding=shares or 1
    )
    result_margin = dcf_valuation_margin(
        df=df, assumptions=assumptions, wacc=wacc, net_debt=net_debt, shares_outstanding=shares or 1
    )

    latest = df.iloc[-1]
    target_multiples = None
    if current_price is not None and shares:
        target_multiples = compute_multiples(
            name=corp["corp_name"],
            price=current_price,
            shares=shares,
            net_income=latest["당기순이익"],
            equity=latest["자본총계"],
            ebit=latest["영업이익"],
            net_debt=net_debt,
        )
    else:
        warnings.append("현재가/주식수 조회 실패로 타겟 멀티플 계산 불가")

    peer_multiples: list[CompanyMultiples] = []
    for peer_code in peer_codes:
        peer_matches = find_corp_code(peer_code)
        if not peer_matches:
            warnings.append(f"피어 {peer_code} corp_code 조회 실패")
            continue
        peer = peer_matches[0]

        peer_df = get_standardized_financials(get_financial_statements, peer["corp_code"], [years[-1]])
        if peer_df.empty:
            warnings.append(f"피어 {peer['corp_name']} 재무데이터 조회 실패")
            continue
        peer_latest = peer_df.iloc[-1]

        peer_price = get_krx_price(peer["stock_code"])
        peer_shares = get_common_shares_outstanding(peer["corp_code"], years[-1])
        if peer_price is None or not peer_shares:
            warnings.append(f"피어 {peer['corp_name']} 시세/주식수 조회 실패")
            continue

        peer_net_debt = peer_latest.get("총차입금", 0.0) - peer_latest.get("현금및현금성자산", 0.0)
        pm = compute_multiples(
            name=peer["corp_name"],
            price=peer_price,
            shares=peer_shares,
            net_income=peer_latest["당기순이익"],
            equity=peer_latest["자본총계"],
            ebit=peer_latest["영업이익"],
            net_debt=peer_net_debt,
        )
        peer_multiples.append(pm)

    implied = None
    if peer_multiples and target_multiples:
        implied = implied_value_from_peers(
            peers=peer_multiples,
            target_eps=target_multiples.eps,
            target_bps=target_multiples.bps,
            target_ebit=latest["영업이익"],
            target_net_debt=net_debt,
            target_shares=shares,
        )

    return AnalysisResult(
        corp=corp,
        df=df,
        ratio_df=ratio_df,
        fcff=fcff,
        net_debt=net_debt,
        shares=shares,
        current_price=current_price,
        market_cap=market_cap,
        wacc=wacc,
        assumptions=assumptions,
        result_cagr=result_cagr,
        result_margin=result_margin,
        target_multiples=target_multiples,
        peer_multiples=peer_multiples,
        implied=implied,
        warnings=warnings,
    )


def safe_round(x, n: int = 4):
    """LLM 프롬프트/JSON 직렬화 전 numpy·NaN 값을 순수 파이썬 값으로 정리한다."""
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    if xf != xf:  # NaN
        return None
    return round(xf, n)


def build_llm_context(result: AnalysisResult, target_year: int) -> dict:
    latest_ratios = result.ratio_df.iloc[-1]
    return {
        "기업명": result.corp["corp_name"],
        "기준연도": target_year,
        "현재가": result.current_price,
        "재무비율_최근연도": {k: safe_round(v, 4) for k, v in latest_ratios.items()},
        "DCF_방법A_CAGR방식": {
            "성장률가정": safe_round(result.result_cagr["historical_growth_rate"], 4),
            "주당가치": safe_round(result.result_cagr["value_per_share"], 0),
        },
        "DCF_방법B_매출마진방식": {
            "평균FCFF마진": safe_round(result.result_margin["avg_fcff_margin"], 4),
            "매출성장률가정": safe_round(result.result_margin["revenue_growth_rate"], 4),
            "주당가치": safe_round(result.result_margin["value_per_share"], 0),
        },
        "상대가치평가": {
            "타겟_PER": safe_round(result.target_multiples.per, 2) if result.target_multiples else None,
            "타겟_PBR": safe_round(result.target_multiples.pbr, 2) if result.target_multiples else None,
            "타겟_EV_EBIT": safe_round(result.target_multiples.ev_ebit, 2) if result.target_multiples else None,
            "피어목록": [p.name for p in result.peer_multiples],
            "PER기준_내재가치": safe_round(result.implied["value_per_share_per"], 0) if result.implied else None,
            "PBR기준_내재가치": safe_round(result.implied["value_per_share_pbr"], 0) if result.implied else None,
            "EV_EBIT기준_내재가치": safe_round(result.implied["value_per_share_ev_ebit"], 0)
            if result.implied
            else None,
        },
        "모델_가정치": {
            "무위험이자율": result.assumptions.risk_free_rate,
            "베타": result.assumptions.beta,
            "시장위험프리미엄": result.assumptions.market_risk_premium,
            "타인자본비용": result.assumptions.cost_of_debt,
            "실효세율": safe_round(result.assumptions.tax_rate, 4),
            "영구성장률": result.assumptions.terminal_growth_rate,
            "WACC": safe_round(result.wacc, 4),
        },
        "모델_한계": [
            "FCFF는 감가상각비를 분리할 수 없어 영업활동현금흐름-CAPEX로 근사함",
            "EV/EBITDA 대신 EV/EBIT 사용 (감가상각비 미분리로 인한 대체)",
            "피어 기업 수가 적어 상대가치평가가 특정 피어의 특이치에 좌우될 수 있음",
        ],
    }
