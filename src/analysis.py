"""전체 분석 파이프라인: DART 재무데이터 조회 -> Scope 1+2 실측 대조/추정 -> Scope 3 추정.

CLI(main.py)와 웹 대시보드(app.py)가 동일한 로직을 중복 없이 재사용하기 위한 모듈.
"""
from dataclasses import dataclass, field

import pandas as pd

import emissions_model
from emissions_data import build_industry_display_map, load_emissions_data, normalize_company_name
from fetch_dart import find_corp_code, get_financial_statements
from normalize import build_financial_table

# 신규 기업(학습 데이터에 없는 기업)을 조회할 때 DART에서 가져올 재무제표 연도.
# 학습 데이터는 emissions_data.EMISSIONS_CSV_FILES에 등록된 모든 연도(2024+2025)를
# 풀링해서 쓰지만, "지금 이 기업은 어떤지" 새로 조회할 때는 그중 가장 최신 연도를
# 쓴다. K-ETS 원자료에 새 연도가 추가되면, emissions_data.EMISSIONS_CSV_FILES에
# 파일을 더하고 재학습(build_emissions_dataset.py -> train_model.py)한 뒤 이 값도
# 같이 올려야 한다 — 자동으로 "올해"를 따라가지 않는다.
TARGET_YEAR = 2025

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
    tree_predictions: list[float]
    feature_importance: dict[str, float]
    warnings: list[str] = field(default_factory=list)


def list_known_industries() -> list[str]:
    """학습 데이터에 존재하는 업종(계획업종, 공백 제거된 내부 매칭 키) 목록."""
    df = emissions_model.load_training_data()
    return sorted(df["계획업종"].dropna().unique().tolist())


def industry_display_map() -> dict[str, str]:
    """내부 매칭 키(공백 없음) -> 화면에 보여줄 원래 표기(띄어쓰기 포함).

    list_known_industries()가 돌려주는 값은 2024/2025 데이터를 합치려고 공백을
    지운 내부 키라 "곡물가공품,전분및전분제품제조업"처럼 읽기 불편하다. 드롭다운이나
    화면에 업종명을 보여줄 땐 반드시 이 맵을 거쳐서 보여준다."""
    return build_industry_display_map()


def has_actual_disclosure(corp_code: str) -> bool:
    """이 기업이 K-ETS 실측 데이터에 있는지만 가볍게 확인한다 (화면에서 업종 추천을
    보여줄지 말지 미리 판단하는 용도 — 실측이 있으면 업종 선택 자체가 무시되므로
    추천을 보여줄 필요가 없다)."""
    return _find_actual_disclosure(corp_code)[0] is not None


def _find_actual_disclosure(corp_code: str) -> tuple[float | None, str | None]:
    """이 기업이 K-ETS 배출권거래제 대상이라 실제 배출량이 이미 공시돼 있는지 확인한다.

    학습 데이터에 같은 기업의 여러 연도가 있을 수 있어(2024·2025 등 pooled), 그중
    가장 최근 연도(대상년도)의 실측값을 우선으로 쓴다 — "현재" 기준 비교가 되도록."""
    try:
        df = emissions_model.load_training_data()
    except FileNotFoundError:
        return None, None
    rows = df[df["corp_code"] == corp_code]
    if rows.empty:
        return None, None
    if "대상년도" in rows.columns:
        rows = rows.sort_values("대상년도", ascending=False)
    row = rows.iloc[0]
    return float(row["배출량_tCO2eq"]), row["계획업종"]


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
        # 합병·분할 등으로 같은 이름의 corp_code가 여러 개 존재하는 경우가 있다
        # (예: '삼성물산' — 2015년 합병으로 소멸한 구법인과, 그 이름을 이어받은 현존 법인).
        # 선택된 corp_code에 데이터가 없으면, 같은 이름의 다른 상장 corp_code로 자동 재시도한다.
        original_corp = corp
        for alt in find_corp_code(corp["corp_name"]):
            if alt["corp_code"] == corp["corp_code"]:
                continue
            alt_data = get_financial_statements(alt["corp_code"], TARGET_YEAR, fs_div="OFS")
            if alt_data.get("status") != "000":
                alt_data = get_financial_statements(alt["corp_code"], TARGET_YEAR, fs_div="CFS")
            if alt_data.get("status") == "000":
                corp = alt
                data = alt_data
                warnings.append(
                    f"'{original_corp['corp_name']}'(종목코드 {original_corp['stock_code']})는 재무데이터가 없어, "
                    f"같은 이름의 다른 종목코드({alt['stock_code']})로 자동 전환해 분석했습니다. "
                    "(합병 등으로 같은 이름의 법인이 여러 개 등록된 경우일 수 있어요.)"
                )
                break
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
        tree_predictions=preds["tree_predictions"],
        feature_importance=preds["feature_importance"],
        warnings=warnings,
    )


def get_multi_year_trend(corp: dict, industry: str, years: list[int]) -> list[dict]:
    """여러 연도에 걸쳐 재무데이터를 조회해 추정 배출량 추이를 계산한다 (지연 로딩 전용).

    연도별로 DART 호출이 추가로 필요해 분석 실행 시 자동으로는 돌리지 않고,
    사용자가 "연도별 추이 보기"를 눌렀을 때만 호출한다. 조회 실패한 연도는 건너뛴다.
    """
    artifact = emissions_model.load_model()
    trend = []
    for year in years:
        try:
            data = get_financial_statements(corp["corp_code"], year, fs_div="OFS")
            if data.get("status") != "000":
                data = get_financial_statements(corp["corp_code"], year, fs_div="CFS")
            if data.get("status") != "000":
                continue
            fin = build_financial_table(data.get("list", []))
            if "매출액" not in fin:
                continue
            preds = emissions_model.predict_scope12(
                artifact, revenue=fin["매출액"], cogs=fin.get("매출원가"), industry=industry
            )
            trend.append({
                "연도": year,
                "매출액": fin["매출액"],
                "회귀모델_추정": preds["model_estimate"],
                "업종평균_추정": preds["benchmark_estimate"],
            })
        except Exception:  # noqa: BLE001
            continue
    return trend


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
    display = industry_display_map()
    return {
        "기업명": result.corp["corp_name"],
        "기준연도": TARGET_YEAR,
        "업종": display.get(result.industry, result.industry),
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
