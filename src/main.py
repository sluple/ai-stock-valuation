"""엔드투엔드 검증 스크립트: DART 데이터 수집 -> 표준화 -> 재무비율 -> DCF 밸류에이션 -> LLM 리포트.

실행: python main.py
"""
import sys

from analysis import DEFAULT_YEARS, build_llm_context, run_analysis
from llm_report import generate_report

sys.stdout.reconfigure(encoding="utf-8")

TARGET = "005930"  # 삼성전자
PEERS = ["000660"]  # SK하이닉스 (동일 업종 피어) — TODO: 필요시 피어 추가
YEARS = DEFAULT_YEARS


def _fmt(x: float | None, spec: str = ",.2f") -> str:
    return format(x, spec) if x is not None else "N/A"


def main() -> None:
    result = run_analysis(TARGET, peer_codes=PEERS, years=YEARS)
    corp = result.corp

    print(f"대상 기업: {corp['corp_name']} ({corp['stock_code']}, corp_code={corp['corp_code']})\n")

    print("=== 표준화된 재무 데이터 ===")
    print(result.df.T, "\n")

    print("=== 재무비율 ===")
    print(result.ratio_df.T, "\n")
    print(f"실효세율 반영: {result.assumptions.tax_rate:.2%}\n")

    print("=== FCFF (영업활동현금흐름 - CAPEX 근사) ===")
    print(result.fcff, "\n")

    print(f"보통주 유통주식수: {result.shares:,}" if result.shares else "보통주 유통주식수 조회 실패")
    if result.current_price is not None:
        print(f"현재가(자동 조회): {result.current_price:,.0f}원 -> 시가총액: {result.market_cap:,.0f}원")
    for w in result.warnings:
        print(f"[안내] {w}")
    print(f"WACC: {result.wacc:.2%}\n")

    rc = result.result_cagr
    print("=== 방법 A: FCFF CAGR 그대로 투사 ===")
    print(f"과거 FCFF 성장률(CAGR): {rc['historical_growth_rate']:.2%}")
    print(f"향후 {result.assumptions.forecast_years}년 예상 FCFF: {[f'{v:,.0f}' for v in rc['projected_fcff']]}")
    print(f"기업가치(EV): {rc['enterprise_value']:,.0f}원")
    print(f"자기자본가치(Equity Value): {rc['equity_value']:,.0f}원")
    if rc["value_per_share"]:
        print(f"주당 가치(적정주가 추정): {rc['value_per_share']:,.0f}원\n")

    rm = result.result_margin
    print("=== 방법 B(권장): 매출 대비 FCFF 평균 마진 기반 투사 ===")
    print(f"평균 FCFF 마진(매출액 대비): {rm['avg_fcff_margin']:.2%}")
    print(f"매출 성장률 가정(CAGR): {rm['revenue_growth_rate']:.2%}")
    print(f"향후 {result.assumptions.forecast_years}년 예상 FCFF: {[f'{v:,.0f}' for v in rm['projected_fcff']]}")
    print(f"기업가치(EV): {rm['enterprise_value']:,.0f}원")
    print(f"자기자본가치(Equity Value): {rm['equity_value']:,.0f}원")
    if rm["value_per_share"]:
        print(f"주당 가치(적정주가 추정): {rm['value_per_share']:,.0f}원")

    if result.current_price is not None:
        print(f"\n=== 현재가 대비 비교 (현재가 {result.current_price:,.0f}원 기준) ===")
        for label, r in [("방법 A", rc), ("방법 B", rm)]:
            if r["value_per_share"]:
                upside = r["value_per_share"] / result.current_price - 1
                print(f"{label}: {r['value_per_share']:,.0f}원 (현재가 대비 {upside:+.1%})")

    print("\n=== 상대가치평가: 동일 업종 피어 비교 ===")
    tm = result.target_multiples
    if tm:
        print(f"{tm.name} (타겟): PER={_fmt(tm.per)}, PBR={_fmt(tm.pbr)}, EV/EBIT={_fmt(tm.ev_ebit)}")
    for pm in result.peer_multiples:
        print(f"{pm.name} (피어): PER={_fmt(pm.per)}, PBR={_fmt(pm.pbr)}, EV/EBIT={_fmt(pm.ev_ebit)}")

    if result.implied:
        implied = result.implied
        print(
            f"\n피어 평균 PER={_fmt(implied['peer_per_avg'])}, PBR={_fmt(implied['peer_pbr_avg'])}, "
            f"EV/EBIT={_fmt(implied['peer_ev_ebit_avg'])}"
        )
        print(f"PER 기준 내재가치: {_fmt(implied['value_per_share_per'], ',.0f')}원")
        print(f"PBR 기준 내재가치: {_fmt(implied['value_per_share_pbr'], ',.0f')}원")
        print(f"EV/EBIT 기준 내재가치: {_fmt(implied['value_per_share_ev_ebit'], ',.0f')}원")

    print("\n=== LLM 애널리스트 리포트 생성 중... ===")
    context = build_llm_context(result, YEARS[-1])
    try:
        report = generate_report(context)
        print("\n" + report)
    except Exception as e:  # noqa: BLE001
        print(f"[경고] LLM 리포트 생성 실패: {e}")


if __name__ == "__main__":
    main()
