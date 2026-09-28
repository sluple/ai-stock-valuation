"""표준화된 재무 테이블로부터 핵심 재무비율을 계산한다."""
import pandas as pd


def compute_ratios(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)

    if {"영업이익", "매출액"}.issubset(df.columns):
        out["영업이익률"] = df["영업이익"] / df["매출액"]

    if {"당기순이익", "매출액"}.issubset(df.columns):
        out["순이익률"] = df["당기순이익"] / df["매출액"]

    if {"당기순이익", "자본총계"}.issubset(df.columns):
        out["ROE"] = df["당기순이익"] / df["자본총계"]

    if {"당기순이익", "자산총계"}.issubset(df.columns):
        out["ROA"] = df["당기순이익"] / df["자산총계"]

    if {"부채총계", "자본총계"}.issubset(df.columns):
        out["부채비율"] = df["부채총계"] / df["자본총계"]

    if {"법인세비용", "법인세비용차감전순이익"}.issubset(df.columns):
        out["실효세율"] = df["법인세비용"] / df["법인세비용차감전순이익"]

    if {"매출액"}.issubset(df.columns):
        out["매출액증가율"] = df["매출액"].pct_change(fill_method=None)

    return out
