"""엔드투엔드 검증 스크립트: DART 재무데이터 -> Scope1+2 실측/추정 -> Scope3 추정 -> AI 리포트.

실행: python main.py
"""
import sys

from analysis import build_llm_context, run_analysis
from llm_report import generate_report

sys.stdout.reconfigure(encoding="utf-8")

TARGET = "005930"  # 삼성전자 (배출권거래제 공시 대상이라 실측값과 비교 가능)


def main() -> None:
    result = run_analysis(TARGET)
    corp = result.corp

    print(f"대상 기업: {corp['corp_name']} ({corp['stock_code']})")
    print(f"업종: {result.industry} ({result.industry_source})\n")

    print("=== 재무데이터 (2024년) ===")
    print(f"매출액: {result.financials.get('매출액'):,.0f}원")
    print(f"매출원가: {result.financials.get('매출원가', 0):,.0f}원\n")

    print("=== Scope 1+2 배출량 ===")
    if result.actual_scope12 is not None:
        print(f"실제 공시된 값: {result.actual_scope12:,.0f} tCO2eq")
    print(f"회귀모델 추정치: {result.model_estimate:,.0f} tCO2eq")
    print(f"업종평균 추정치: {result.benchmark_estimate:,.0f} tCO2eq ({result.benchmark_source})")

    print(f"\n=== Scope 3 추정치 (CDP 평균 배율 x3 적용) ===")
    print(f"{result.scope3_estimate:,.0f} tCO2eq")

    print(f"\n=== 모델 성능 (홀드아웃 검증) ===")
    m = result.model_metrics
    print(f"R2: {m['r2']:.3f} (학습 {m['n_train']}개 / 검증 {m['n_test']}개 기업)")

    for w in result.warnings:
        print(f"[안내] {w}")

    print("\n=== AI 리포트 생성 중... ===")
    context = build_llm_context(result)
    try:
        report = generate_report(context)
        print("\n" + report)
    except Exception as e:  # noqa: BLE001
        print(f"[경고] 리포트 생성 실패: {e}")


if __name__ == "__main__":
    main()
