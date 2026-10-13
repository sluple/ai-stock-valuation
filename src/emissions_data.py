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
_CORP_CODE_PATH = os.path.join(_DATA_DIR, "CORPCODE.xml")

# 여러 연도의 ETRS 원본을 모두 모아 학습한다(특정 연도에 고정되지 않도록, 표본도 늘리도록).
# 파일 추가 시 이 리스트에만 경로를 더하면 된다.
EMISSIONS_CSV_FILES = [
    os.path.join(_DATA_DIR, "emissions_raw_2024.csv"),
    os.path.join(_DATA_DIR, "emissions_raw_2025.csv"),
]

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


def _find_col(df: pd.DataFrame, prefix: str) -> str:
    """ETRS 파일은 연도마다 컬럼명에 단위가 붙거나 빠진다
    (예: "온실가스배출량" -> "온실가스배출량(tCO2-eq)"). prefix로 시작하는 컬럼을 찾는다."""
    for col in df.columns:
        if col.startswith(prefix):
            return col
    raise KeyError(f"'{prefix}'로 시작하는 컬럼을 찾을 수 없습니다. 실제 컬럼: {list(df.columns)}")


def _load_one_emissions_file(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="cp949")
    emission_col = _find_col(df, "온실가스배출량")
    energy_col = _find_col(df, "에너지사용량")
    df["배출량_tCO2eq"] = pd.to_numeric(df[emission_col].astype(str).str.replace(",", ""), errors="coerce")
    df["에너지사용량_TJ"] = pd.to_numeric(df[energy_col].astype(str).str.replace(",", ""), errors="coerce")
    df = df.dropna(subset=["배출량_tCO2eq"])  # 배출량 비공개(*****) 기업 제외
    df["정규화명"] = df["업체명"].apply(normalize_company_name)
    # 연도별로 "계획업종" 표기에 띄어쓰기가 있다 없다 하는 경우가 있어("기타식품제조업" vs
    # "기타 식품 제조업"), 공백을 지워 정규화한다 — 안 그러면 같은 업종이 다른 업종으로 쪼개진다.
    df["계획업종"] = df["계획업종"].str.replace(" ", "", regex=False)
    return df


def load_emissions_data(paths: list[str] | None = None) -> pd.DataFrame:
    """ETRS 배출량 원본 CSV(여러 연도)를 전부 로드해 하나로 합친다."""
    paths = paths if paths is not None else EMISSIONS_CSV_FILES
    frames = [_load_one_emissions_file(p) for p in paths]
    return pd.concat(frames, ignore_index=True)


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
