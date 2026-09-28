"""전체 분석 파이프라인: DART 재무데이터 조회 -> Scope 1+2 실측 대조/추정 -> Scope 3 추정.

CLI(main.py)와 웹 대시보드(app.py)가 동일한 로직을 중복 없이 재사용하기 위한 모듈.
"""
from dataclasses import dataclass, field

import pandas as pd

import emissions_model
from emissions_data import load_emissions_data, normalize_company_name
from fetch_dart import find_corp_code, get_financial_statements
from normalize import build_financial_table

TARGET_YEAR = 2024

# 실제 공시값·회귀모델·업종평균 세 추정치가 서로 이 비율 이내로 근접하면
# "업종 내 전형적인 배출 패턴"으로 판정한다. (max-min)/min 기준.
TYPICAL_PATTERN_THRESHOLD = 0.3


@dataclass
class EmissionsResult:
    corp: dict
    financials: dict
    industry: str
    industry_source: str  # "실제 공시 데이터 매칭" 또는 "사용자 선택"
    actual_scope12: float | None
    model_estimate: float
    benchmark_estimate: float
    benchmark_source: str
    scope3_estimate: float
    model_metrics: dict
    scale_ratio: float
    scale_warning: bool
    estimate_divergence_ratio: float
    is_typical_pattern: bool
    warnings: list[str] = field(default_factory=list)


def list_known_industries() -> list[str]:
    """학습 데이터에 존재하는 업종(계획업종) 목록 — 사용자가 업종을 고를 때 쓴다."""
    df = emissions_model.load_training_data()
    return sorted(df["계획업종"].dropna().unique().tolist())


def _find_actual_disclosure(corp_code: str) -> tuple[float | None, str | None]:
    """이 기업이 K-ETS 배출권거래제 대상이라 실제 배출량이 이미 공시돼 있는지 확인한다."""
    try:
        df = emissions_model.load_training_data()
    except FileNotFoundError:
        return None, None
    row = df[df["corp_code"] == corp_code]
    if row.empty:
        return None, None
    return float(row.iloc[0]["배출량_tCO2eq"]), row.iloc[0]["계획업종"]


def run_analysis(stock_code: str, industry_override: str | None = None) -> EmissionsResult:
    """종목코드/기업명 완전일치로 찾아서 분석한다 (CLI 등에서 사용)."""
    matches = find_corp_code(stock_code)
    if not matches:
        raise ValueError(f"'{stock_code}' 기업을 찾지 못했습니다.")
    return run_analysis_for_corp(matches[0], industry_override)


def run_analysis_for_corp(corp: dict, industry_override: str | None = None) -> EmissionsResult:
    """이미 찾아놓은 기업 정보(corp_code 포함)로 바로 분석한다 (이름 검색 UI 등에서 사용)."""
    warnings: list[str] = []

    # 학습 데이터와 반드시 같은 기준(별도재무제표 우선)을 써야 한다 — 안 그러면 학습은 OFS
    # 매출로, 예측은 CFS 매출로 하는 불일치가 생겨 지주회사·복합기업에서 큰 오차가 난다.
    data = get_financial_statements(corp["corp_code"], TARGET_YEAR, fs_div="OFS")
    if data.get("status") != "000":
        data = get_financial_statements(corp["corp_code"], TARGET_YEAR, fs_div="CFS")
    if data.get("status") != "000":
        raise ValueError(f"{TARGET_YEAR}년 재무제표 조회 실패: {data.get('message')}")
    fin = build_financial_table(data.get("list", []))
    if "매출액" not in fin:
        raise ValueError(f"{TARGET_YEAR}년 매출액 데이터를 찾을 수 없습니다.")

    actual_scope12, matched_industry = _find_actual_disclosure(corp["corp_code"])

    if matched_industry:
        industry = matched_industry
        industry_source = "실제 배출권거래제 공시 데이터와 매칭됨"
    elif industry_override:
        industry = industry_override
        industry_source = "사용자 선택"
    else:
        raise ValueError("이 기업은 배출량 공시 대상이 아니라 업종을 직접 선택해야 합니다.")

    artifact = emissions_model.load_model()
    preds = emissions_model.predict_scope12(
        artifact, revenue=fin["매출액"], cogs=fin.get("매출원가"), industry=industry
    )

    base_for_scope3 = actual_scope12 if actual_scope12 is not None else preds["model_estimate"]
    scope3 = emissions_model.estimate_scope3(base_for_scope3)

    if actual_scope12 is None:
        warnings.append("이 기업은 배출권거래제 공시 대상이 아니어서 Scope 1+2도 모델 추정치입니다.")

    if preds["scale_warning"]:
        warnings.append(
            f"이 기업의 매출액은 같은 업종 내 전형적 기업의 약 {preds['scale_ratio']:.0f}배입니다. "
            "여러 사업을 겸영하는 대기업/복합기업일 가능성이 높아, 추정 신뢰도가 낮을 수 있습니다."
        )

    # 실측값(있는 경우)·회귀모델·업종평균 추정치가 서로 얼마나 근접한지로
    # "업종 내 전형적인 배출 패턴"인지 판정한다.
    compare_values = [preds["model_estimate"], preds["benchmark_estimate"]]
    if actual_scope12 is not None:
        compare_values.append(actual_scope12)
    lo, hi = min(compare_values), max(compare_values)
    divergence_ratio = (hi - lo) / lo if lo > 0 else float("inf")
    is_typical_pattern = divergence_ratio <= TYPICAL_PATTERN_THRESHOLD

    return EmissionsResult(
        corp=corp,
        financials=fin,
        industry=industry,
        industry_source=industry_source,
        actual_scope12=actual_scope12,
        model_estimate=preds["model_estimate"],
        benchmark_estimate=preds["benchmark_estimate"],
        benchmark_source=preds["benchmark_source"],
        scope3_estimate=scope3,
        model_metrics=artifact["metrics"],
        scale_ratio=preds["scale_ratio"],
        scale_warning=preds["scale_warning"],
        estimate_divergence_ratio=divergence_ratio,
        is_typical_pattern=is_typical_pattern,
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


def build_llm_context(result: EmissionsResult) -> dict:
    return {
        "기업명": result.corp["corp_name"],
        "기준연도": TARGET_YEAR,
        "업종": result.industry,
        "업종_판별방법": result.industry_source,
        "재무데이터": {
            "매출액": safe_round(result.financials.get("매출액"), 0),
            "매출원가": safe_round(result.financials.get("매출원가"), 0),
            "영업이익": safe_round(result.financials.get("영업이익"), 0),
        },
        "실제_공시된_Scope12": safe_round(result.actual_scope12, 0),
        "모델_추정_Scope12": safe_round(result.model_estimate, 0),
        "업종평균_추정_Scope12": safe_round(result.benchmark_estimate, 0),
        "업종평균_출처": result.benchmark_source,
        "Scope3_추정치": safe_round(result.scope3_estimate, 0),
        "업종대비_매출규모_배율": safe_round(result.scale_ratio, 1),
        "규모_이상치_경고": result.scale_warning,
        "추정방법간_편차율": safe_round(result.estimate_divergence_ratio, 2),
        "업종내_전형적_배출패턴_여부": result.is_typical_pattern,
        "모델_성능": {
            "R2": safe_round(result.model_metrics.get("r2"), 3),
            "학습표본수": result.model_metrics.get("n_train"),
            "검증표본수": result.model_metrics.get("n_test"),
        },
        "모델_한계": [
            "Scope3는 CDP 평균치(전체 배출량의 약 75%) 기반 배율(x3)을 곱한 값으로, 업종별 정교화는 안 되어 있음",
            "회귀모델은 약 250여개 배출권거래제 대상 기업 데이터로 학습되어, 그 분포를 벗어난 기업(예: 서비스업)에서는 정확도가 낮을 수 있음",
            "매출원가가 공시되지 않은 경우 매출액의 70%로 임의 대체함",
            "여러 사업을 겸영하는 대기업/복합기업은 매출 전체가 하나의 업종으로 뭉뚱그려져 정확도가 낮아짐 (실측: 매출 상위 20% 기업의 3배 이내 정확도 70% vs 하위 80% 83%)",
        ],
    }
