"""배출권거래제(K-ETS) 인증 배출량 데이터 로드 + DART 기업명 매칭.

데이터 출처: 온실가스종합정보센터 배출권등록부시스템(ETRS) 정보공개
https://etrs.gir.go.kr -> 공공데이터포털(data.go.kr) 파일데이터로 배포
(법인명, 대상년도, 온실가스배출량(tCO2eq) 등 포함)

기업명 매칭이 까다로운 이유: ETRS 데이터는 법인명을 "주식회사", "(주)" 등
표기가 제각각이고, SK/LG/GS/CJ 같은 그룹명을 "에스케이", "엘지" 등 한글 음차로
적기도 한다. DART corp_name과 정확히 일치하지 않는 경우가 많아 정규화가 필요하다.
"""
import os
import re
import xml.etree.ElementTree as ET

import pandas as pd

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_EMISSIONS_CSV = os.path.join(_DATA_DIR, "emissions_raw.csv")
_CORP_CODE_PATH = os.path.join(_DATA_DIR, "CORPCODE.xml")

# ETRS 법인명에 흔한 한글 음차 그룹명을 DART식 로마자 표기로 치환 (매칭률을 높이기 위함)
_PHONETIC_MAP = {
    "에스케이": "SK",
    "엘지": "LG",
    "지에스": "GS",
    "씨제이": "CJ",
    "케이티": "KT",
    "오씨아이": "OCI",
    "에이치디": "HD",
    "케이씨씨": "KCC",
}

_CORP_SUFFIX_PATTERN = re.compile(r"(주식회사|㈜|\(주\)|\(유\)|유한회사)")


def normalize_company_name(name: str) -> str:
    """법인명을 비교 가능한 형태로 정규화한다: 회사형태 표기 제거 + 공백 제거 + 음차 치환."""
    if not name:
        return ""
    cleaned = _CORP_SUFFIX_PATTERN.sub("", name)
    cleaned = cleaned.replace(" ", "").strip()
    for kor, latin in _PHONETIC_MAP.items():
        cleaned = cleaned.replace(kor, latin)
    return cleaned.upper()


def load_emissions_data() -> pd.DataFrame:
    """ETRS 배출량 원본 CSV를 로드해 정리한다."""
    df = pd.read_csv(_EMISSIONS_CSV, encoding="cp949")
    df["배출량_tCO2eq"] = pd.to_numeric(df["온실가스배출량"].astype(str).str.replace(",", ""), errors="coerce")
    df["에너지사용량_TJ"] = pd.to_numeric(df["에너지사용량"].astype(str).str.replace(",", ""), errors="coerce")
    df = df.dropna(subset=["배출량_tCO2eq"])  # 배출량 비공개(*****) 기업 제외
    df["정규화명"] = df["업체명"].apply(normalize_company_name)
    return df


def build_corp_name_index() -> dict[str, dict]:
    """DART CORPCODE.xml 전체(상장사+비상장 외부감사대상법인 포함)로 정규화명 -> 기업정보 인덱스를 만든다.

    주가를 더는 다루지 않으므로(이 프로젝트는 배출량 추정이 목적) 종목코드 유무로 거르지 않는다 —
    GS칼텍스처럼 비상장이지만 DART에 재무제표를 공시하는 대기업도 학습 데이터로 쓸 수 있어야 한다.
    """
    tree = ET.parse(_CORP_CODE_PATH)
    root = tree.getroot()

    index: dict[str, dict] = {}
    for item in root.findall("list"):
        corp_name = (item.findtext("corp_name") or "").strip()
        stock_code = (item.findtext("stock_code") or "").strip()
        corp_code = (item.findtext("corp_code") or "").strip()
        norm = normalize_company_name(corp_name)
        if norm:
            index[norm] = {"corp_code": corp_code, "corp_name": corp_name, "stock_code": stock_code}
    return index


def match_emissions_to_dart(emissions_df: pd.DataFrame, corp_index: dict[str, dict]) -> pd.DataFrame:
    """배출량 데이터에 DART corp_code/종목코드를 매칭해 붙인다. 매칭 안 되면 제외."""
    records = []
    for _, row in emissions_df.iterrows():
        match = corp_index.get(row["정규화명"])
        if match is None:
            continue
        records.append(
            {
                **match,
                "대상년도": row["대상년도"],
                "부문": row["부문"],
                "계획업종": row["계획업종"],
                "배출량_tCO2eq": row["배출량_tCO2eq"],
                "에너지사용량_TJ": row["에너지사용량_TJ"],
            }
        )
    return pd.DataFrame(records)
