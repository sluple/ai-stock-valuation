"""Scope 1+2 배출량 추정 모델 + Scope 3 확장.

방법론:
1. 산업평균 배출집약도(EEIO 방식): 같은 업종(계획업종) 기업들의 "배출량/매출액" 중앙값을
   구해서 매출액에 곱하는, 환경투입산출모델(EEIO)의 가장 단순한 형태. 해석이 쉽고 투명하다.
2. 회귀모델(RandomForest): log(매출액), log(매출원가), 업종을 특징으로 log(배출량)을 예측.
   업종 내에서도 기업마다 다른 배출집약도(설비 효율, 사업 구성 등)를 어느 정도 반영한다.
두 방법을 모두 계산해서 함께 보여준다 — 서로 다른 방법론이 얼마나 수렴/발산하는지 자체가
모델 신뢰도에 대한 중요한 정보이기 때문이다 (앞서 DCF/상대가치평가에서도 같은 원칙을 썼다).

Scope 3 확장: CDP 조사에 따르면 Scope 3는 평균적으로 전체 배출량(1+2+3)의 약 75%를 차지한다
(업종별로 식음료 87%, 금융업 99%+ 등 편차가 큼). 즉 Scope3 ≈ (Scope1+2) x 3 을 기본값으로 쓰되,
이는 전 산업 평균이라는 단순화 가정임을 명시한다. 업종별 정교화는 향후 과제.
출처: CDP Technical Note - Relevance of Scope 3 Categories by Sector.
"""
import os
import pickle

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_TRAINING_CSV = os.path.join(_DATA_DIR, "emissions_training_data.csv")
_MODEL_PATH = os.path.join(_DATA_DIR, "emissions_model.pkl")

SCOPE3_MULTIPLIER_DEFAULT = 3.0  # CDP: Scope3가 전체의 ~75% -> Scope3/Scope(1+2) = 0.75/0.25 = 3


def load_training_data() -> pd.DataFrame:
    # corp_code/stock_code는 앞자리가 0으로 시작하는 8자리 코드라, CSV를 그냥 읽으면
    # 숫자로 인식되어 앞자리 0이 잘린다(예: "00126380" -> 126380). 반드시 문자열로 읽고 되살린다.
    df = pd.read_csv(_TRAINING_CSV, dtype={"corp_code": str, "stock_code": str})
    df["corp_code"] = df["corp_code"].str.zfill(8)
    df = df.dropna(subset=["매출액", "배출량_tCO2eq"])
    df = df[df["매출액"] > 0]
    return df


def compute_industry_benchmark(df: pd.DataFrame) -> pd.DataFrame:
    """업종별 배출집약도(tCO2eq / 매출액 1억원) 중앙값."""
    df = df.copy()
    df["집약도"] = df["배출량_tCO2eq"] / (df["매출액"] / 1e8)
    benchmark = df.groupby("계획업종")["집약도"].median().reset_index()
    benchmark.columns = ["계획업종", "배출집약도_톤per억원"]
    return benchmark


# 대기업일수록 여러 사업을 겸영해 "매출 전체 vs 배출 사업장 하나"의 불일치가 커진다
# (실측: 매출 상위 20% 기업의 3배 이내 정확도 70% vs 하위 80% 83%). 매출 규모가 업종 내
# 전형적 기업보다 훨씬 크면, 추정 신뢰도가 낮다는 경고를 낼 수 있도록 업종별 매출 중앙값을 같이 저장한다.
SCALE_WARNING_MULTIPLE = 5.0


def compute_industry_revenue_stats(df: pd.DataFrame) -> pd.DataFrame:
    """업종별 매출액 중앙값 — 목표 기업의 규모가 업종 내에서 이상치인지 판단하는 데 쓴다."""
    stats = df.groupby("계획업종")["매출액"].median().reset_index()
    stats.columns = ["계획업종", "매출액_중앙값"]
    return stats


def train_model(df: pd.DataFrame) -> dict:
    """RandomForest 회귀모델을 학습하고, 홀드아웃 검증 성능과 함께 반환한다.

    특징 설계: 업종이 54개나 되는데 학습 표본은 252개뿐이라, 업종을 원-핫 인코딩(54차원)
    하면 표본이 너무 희소해져 과적합/과소적합이 반복된다(실제로 R2=0.28에 그쳤음).
    대신 "업종평균 배출집약도로 계산한 EEIO 추정치"를 하나의 숫자 특징으로 압축해 넣는다
    (타겟 인코딩과 비슷한 방식). 모델은 이 EEIO 추정치를 매출·매출원가로 보정하는
    역할만 하면 되므로, 적은 데이터로도 훨씬 안정적으로 학습된다.
    """
    df = df.copy()
    df["매출원가"] = df["매출원가"].fillna(df["매출액"] * 0.7)  # 결측 시 업계 평균 원가율로 대체

    industry_benchmark = compute_industry_benchmark(df)
    industry_revenue_stats = compute_industry_revenue_stats(df)
    overall_median_intensity = float((df["배출량_tCO2eq"] / (df["매출액"] / 1e8)).median())
    overall_median_revenue = float(df["매출액"].median())

    intensity_map = dict(zip(industry_benchmark["계획업종"], industry_benchmark["배출집약도_톤per억원"]))
    df["업종추정치"] = df["계획업종"].map(intensity_map).fillna(overall_median_intensity) * (df["매출액"] / 1e8)

    X = np.log1p(df[["매출액", "매출원가", "업종추정치"]]).values
    y = np.log1p(df["배출량_tCO2eq"])

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    model = RandomForestRegressor(n_estimators=300, max_depth=6, random_state=42, min_samples_leaf=3)
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    r2 = r2_score(y_test, y_pred)
    mae_log = mean_absolute_error(y_test, y_pred)
    # 로그 스케일 MAE를 대략적인 배수 오차로 환산 (참고용)
    approx_fold_error = float(np.expm1(mae_log))

    artifact = {
        "model": model,
        "industry_benchmark": industry_benchmark,
        "industry_revenue_stats": industry_revenue_stats,
        "metrics": {"r2": r2, "mae_log": mae_log, "approx_fold_error": approx_fold_error, "n_train": len(X_train), "n_test": len(X_test)},
        "overall_median_intensity": overall_median_intensity,
        "overall_median_revenue": overall_median_revenue,
    }
    return artifact


def save_model(artifact: dict) -> None:
    with open(_MODEL_PATH, "wb") as f:
        pickle.dump(artifact, f)


def load_model() -> dict:
    with open(_MODEL_PATH, "rb") as f:
        return pickle.load(f)


def predict_scope12(artifact: dict, revenue: float, cogs: float | None, industry: str) -> dict:
    """매출액/매출원가/업종으로 Scope1+2 배출량을 두 가지 방법으로 추정한다."""
    cogs_value = cogs if cogs and cogs > 0 else revenue * 0.7

    # 방법 2 먼저 계산: 업종 평균 배출집약도(EEIO 방식) — 회귀모델의 입력으로도 재사용
    bench = artifact["industry_benchmark"]
    row = bench[bench["계획업종"] == industry]
    if not row.empty:
        intensity = float(row["배출집약도_톤per억원"].iloc[0])
        benchmark_source = f"업종 평균 ({industry})"
    else:
        intensity = artifact["overall_median_intensity"]
        benchmark_source = "전산업 평균 (해당 업종 데이터 부족)"
    benchmark_estimate = intensity * (revenue / 1e8)

    # 방법 1: 회귀모델 (EEIO 추정치를 매출·매출원가로 보정)
    X = np.log1p([[revenue, cogs_value, benchmark_estimate]])
    pred_log = artifact["model"].predict(X)[0]
    model_estimate = float(np.expm1(pred_log))

    # 규모 이상치 경고: 이 기업 매출이 같은 업종의 전형적 기업보다 훨씬 크면, 여러 사업을
    # 겸영하는 대기업/복합기업일 가능성이 높다 — 실측상 이런 기업에서 오차가 유의하게 커진다.
    rev_stats = artifact.get("industry_revenue_stats")
    industry_median_revenue = artifact.get("overall_median_revenue")
    if rev_stats is not None:
        row_rev = rev_stats[rev_stats["계획업종"] == industry]
        if not row_rev.empty:
            industry_median_revenue = float(row_rev["매출액_중앙값"].iloc[0])
    scale_ratio = revenue / industry_median_revenue if industry_median_revenue else 1.0
    scale_warning = scale_ratio >= SCALE_WARNING_MULTIPLE

    return {
        "model_estimate": model_estimate,
        "benchmark_estimate": benchmark_estimate,
        "benchmark_source": benchmark_source,
        "benchmark_intensity_per_100m": intensity,
        "scale_ratio": scale_ratio,
        "scale_warning": scale_warning,
    }


def estimate_scope3(scope12_estimate: float, multiplier: float = SCOPE3_MULTIPLIER_DEFAULT) -> float:
    return scope12_estimate * multiplier
