"""DART 계정과목 표준화: 회사/연도별로 다르게 표기되는 계정명을 표준 계정으로 통합한다.

실제로 확인해보니(2022~2024 삼성전자 사례):
- "매출액" / "수익(매출액)" 이 연도별로 혼용됨
- "영업이익" / "영업이익(손실)" 혼용
- "당기순이익" / "당기순이익(손실)" 혼용
- CF에는 감가상각비가 별도 라인으로 없고 "조정"에 뭉쳐 있음
  → FCFF는 감가상각비를 재구성하지 않고, 영업활동현금흐름(CFO)을 그대로 사용해 근사한다.
"""
import re

import pandas as pd

# 일부 기업(예: 고려아연)은 계정명 앞에 "Ⅰ.", "Ⅷ. ", "XI. " 같은 로마자/숫자 번호를 붙인다.
# 별칭 매칭 전에 이런 번호 접두사를 제거해야 한다.
_NUMBERING_PREFIX = re.compile(r"^[0-9ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫIVXLCDM]{1,6}[.\)]\s*")


def _strip_numbering_prefix(name: str) -> str:
    return _NUMBERING_PREFIX.sub("", name)


# 표준계정명 -> DART에서 실제로 쓰일 수 있는 별칭 목록
ALIASES: dict[str, list[str]] = {
    "매출액": ["매출액", "수익(매출액)", "영업수익", "매출"],
    "매출원가": ["매출원가"],
    "영업이익": ["영업이익", "영업이익(손실)"],
    "당기순이익": ["당기순이익", "당기순이익(손실)"],
    "법인세비용차감전순이익": ["법인세비용차감전순이익", "법인세비용차감전순이익(손실)"],
    "법인세비용": ["법인세비용(수익)", "법인세비용"],
    "자산총계": ["자산총계"],
    "부채총계": ["부채총계"],
    "자본총계": ["자본총계"],
    "현금및현금성자산": ["현금및현금성자산"],
    "단기차입금": ["단기차입금"],
    "유동성장기부채": ["유동성장기부채", "유동성사채"],
    "장기차입금": ["장기차입금"],
    "사채": ["사채"],
    "영업활동현금흐름": ["영업활동현금흐름", "영업활동으로인한현금흐름", "영업활동 현금흐름"],
    "유형자산의취득": ["유형자산의 취득"],
    "무형자산의취득": ["무형자산의 취득"],
}

# 표준계정명 -> alias 역매핑 (alias -> canonical)
_ALIAS_TO_CANON = {alias: canon for canon, aliases in ALIASES.items() for alias in aliases}

# 같은 계정명이 IS/CIS/CF/SCE 등 여러 표에 중복 등장하는 문제(예: "당기순이익", "자본총계")를 막기 위해
# 표준계정마다 실제로 읽어도 되는 sj_div(재무제표 구분) 집합을 명시한다.
# 손익 계정은 IS/CIS 둘 다 허용한다 — 기업에 따라 손익계산서를 포괄손익계산서(CIS)와
# 분리해서 내기도 하고(예: 삼성전자), 하나로 합쳐서 CIS에만 담기도 한다(예: SK하이닉스).
# 다만 CF/SCE 쪽의 동명 계정(재구성용 합계 등)은 배제해 값이 덮어써지는 걸 막는다.
_CANON_SJ_DIV = {
    "매출액": {"IS", "CIS"},
    "매출원가": {"IS", "CIS"},
    "영업이익": {"IS", "CIS"},
    "당기순이익": {"IS", "CIS"},
    "법인세비용차감전순이익": {"IS", "CIS"},
    "법인세비용": {"IS", "CIS"},
    "자산총계": {"BS"},
    "부채총계": {"BS"},
    "자본총계": {"BS"},
    "현금및현금성자산": {"BS"},
    "단기차입금": {"BS"},
    "유동성장기부채": {"BS"},
    "장기차입금": {"BS"},
    "사채": {"BS"},
    "영업활동현금흐름": {"CF"},
    "유형자산의취득": {"CF"},
    "무형자산의취득": {"CF"},
}


def build_financial_table(raw_items: list[dict]) -> pd.DataFrame:
    """DART fnlttSinglAcntAll 응답의 list를 받아 (account -> amount) 한 행으로 정리한다."""
    values: dict[str, float] = {}
    for item in raw_items:
        account_nm = _strip_numbering_prefix((item.get("account_nm") or "").strip())
        canon = _ALIAS_TO_CANON.get(account_nm)
        if canon is None:
            continue
        allowed_sj_divs = _CANON_SJ_DIV.get(canon)
        if allowed_sj_divs is not None and item.get("sj_div") not in allowed_sj_divs:
            continue  # 다른 표(SCE/CF 등)에 동명 계정이 있는 경우 무시
        amount_str = (item.get("thstrm_amount") or "").replace(",", "")
        try:
            amount = float(amount_str)
        except ValueError:
            continue
        values[canon] = amount
    return values


def get_standardized_financials(fetch_fn, corp_code: str, years: list[int], fs_div: str = "CFS") -> pd.DataFrame:
    """연도별로 DART를 조회해 표준화된 재무 테이블(연도 x 표준계정)을 만든다.

    fetch_fn: fetch_dart.get_financial_statements 와 동일한 시그니처의 함수 (테스트 용이성을 위해 주입)
    """
    rows = {}
    for year in years:
        data = fetch_fn(corp_code, year, fs_div=fs_div)
        if data.get("status") != "000":
            print(f"[{year}] 조회 실패 status={data.get('status')} message={data.get('message')}")
            continue
        rows[year] = build_financial_table(data.get("list", []))

    df = pd.DataFrame.from_dict(rows, orient="index")
    df.index.name = "year"

    # CAPEX = 유형자산 취득 + 무형자산 취득 (DART 부호는 보통 양수로 표기됨에 유의)
    if "유형자산의취득" in df.columns or "무형자산의취득" in df.columns:
        df["capex"] = df.get("유형자산의취득", 0).fillna(0) + df.get("무형자산의취득", 0).fillna(0)

    # 이자부담부채 합계 (단기차입금 + 유동성장기부채 + 장기차입금 + 사채)
    debt_cols = ["단기차입금", "유동성장기부채", "장기차입금", "사채"]
    existing_debt_cols = [c for c in debt_cols if c in df.columns]
    if existing_debt_cols:
        df["총차입금"] = df[existing_debt_cols].fillna(0).sum(axis=1)

    return df.sort_index()
