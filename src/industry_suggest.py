"""상장기업의 업종을 KRX KIND(상장공시시스템) 공식 업종 정보로 추천한다.

DART 기업개황 API의 induty_code는 검증 결과 신뢰할 수 없었다(완전히 다른 식품회사
3곳이 전부 같은 코드를 반환함 — DART가 실제로 어떤 분류 체계를 쓰는지조차 공식적으로
확인되지 않음). 대신 KIND 상장법인목록은 업종을 코드가 아니라 사람이 읽는 텍스트로
직접 제공하고("기타 식품 제조업"), 투자자 대상 공식 정보라 신뢰도가 더 높다. 실제로
오리온·농심·삼양사·CJ제일제당 4개 식품회사를 전부 정확히 "기타 식품 제조업"으로
반환하는 것을 확인했다.

한계: KIND 목록은 상장기업만 다뤄서 비상장기업은 추천이 안 된다. 또한 KIND의 158개
업종 중 K-ETS 계획업종 54개와 정확히 겹치는 건 42개뿐이다(나머지는 서비스업·도소매업
등 K-ETS 대상이 아닌 업종이라 애초에 우리 쪽에 대응되는 카테고리가 없는 게 정상).
겹치지 않으면 추천하지 않고 조용히 포기한다 — 억지로 비슷한 걸 추천하지 않는다.

데이터 출처: KIND(kind.krx.co.kr) 상장법인목록 다운로드.
"""
import os

import pandas as pd

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_KIND_CSV = os.path.join(_DATA_DIR, "kind_industry_list.csv")

_cache: dict[str, dict] | None = None


def _load() -> dict[str, dict]:
    global _cache
    if _cache is not None:
        return _cache
    df = pd.read_csv(_KIND_CSV, dtype=str)
    _cache = {
        row["종목코드"]: {"업종": row["업종"], "주요제품": row["주요제품"]}
        for _, row in df.iterrows()
    }
    return _cache


def suggest_industry(stock_code: str | None, known_industries: list[str]) -> dict | None:
    """stock_code로 KIND 업종을 찾아, 우리 54개 업종(공백 제거 키) 중 정확히 일치하는
    게 있으면 {"계획업종": 내부키, "업종_원문": KIND표기, "주요제품": ...}을 반환한다.
    일치하는 게 없거나 비상장/목록에 없으면 None(= 추천 안 함)."""
    if not stock_code:
        return None
    try:
        kind_map = _load()
    except FileNotFoundError:
        return None
    entry = kind_map.get(stock_code.zfill(6))
    if entry is None:
        return None
    kind_industry_stripped = entry["업종"].replace(" ", "")
    if kind_industry_stripped not in known_industries:
        return None
    return {
        "계획업종": kind_industry_stripped,
        "업종_원문": entry["업종"],
        "주요제품": entry.get("주요제품", ""),
    }
