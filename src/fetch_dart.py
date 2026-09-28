"""OpenDART API 연동: 기업 고유번호 조회 + 전체 재무제표 조회"""
import io
import os
import zipfile
import xml.etree.ElementTree as ET

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("OPENDART_API_KEY")
BASE_URL = "https://opendart.fss.or.kr/api"

# 연결을 재사용하는 공유 세션. requests.get()을 매번 새로 호출하면 매 요청마다 새
# TCP/TLS 연결을 맺어 대량 조회(수백 개 기업) 시 소켓이 급격히 쌓이고 느려진다.
_session = requests.Session()
_adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10)
_session.mount("https://", _adapter)

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_CORP_CODE_PATH = os.path.join(_DATA_DIR, "CORPCODE.xml")


def _ensure_api_key():
    if not API_KEY:
        raise RuntimeError(
            "OPENDART_API_KEY가 설정되지 않았습니다. valuation-ai/.env 파일에 키를 넣어주세요."
        )


def download_corp_codes(force: bool = False) -> str:
    """DART에 등록된 전체 기업의 corp_code 매핑 파일을 내려받아 캐싱한다."""
    _ensure_api_key()
    os.makedirs(_DATA_DIR, exist_ok=True)
    if os.path.exists(_CORP_CODE_PATH) and not force:
        return _CORP_CODE_PATH

    resp = _session.get(f"{BASE_URL}/corpCode.xml", params={"crtfc_key": API_KEY}, timeout=30)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        z.extract("CORPCODE.xml", _DATA_DIR)
    return _CORP_CODE_PATH


_all_corps_cache: list[dict] | None = None


def _load_all_corps() -> list[dict]:
    """CORPCODE.xml(약 30MB)을 매 검색마다 다시 파싱하면 느리므로 최초 1회만 파싱해 캐싱한다."""
    global _all_corps_cache
    if _all_corps_cache is not None:
        return _all_corps_cache

    path = download_corp_codes()
    tree = ET.parse(path)
    root = tree.getroot()

    corps = []
    for item in root.findall("list"):
        corp_name = (item.findtext("corp_name") or "").strip()
        stock_code = (item.findtext("stock_code") or "").strip()
        corp_code = (item.findtext("corp_code") or "").strip()
        if corp_name:
            corps.append({"corp_code": corp_code, "corp_name": corp_name, "stock_code": stock_code})

    _all_corps_cache = corps
    return corps


def find_corp_code(name_or_stock_code: str) -> list[dict]:
    """기업명 또는 종목코드로 corp_code를 정확히 일치하는 것만 검색한다."""
    matches = []
    for corp in _load_all_corps():
        if not corp["stock_code"]:
            continue  # 비상장사는 제외
        if corp["stock_code"] == name_or_stock_code or corp["corp_name"] == name_or_stock_code:
            matches.append(corp)
    return matches


def search_corp_by_name(query: str, limit: int = 15) -> list[dict]:
    """회사 이름 일부로 검색한다 (예: "삼성" -> 삼성전자, 삼성SDI, 삼성물산 ...).

    상장사를 우선, 이름이 정확히 일치하거나 짧을수록(더 구체적일수록) 우선순위를 준다.
    """
    query = query.strip()
    if not query:
        return []

    matches = [c for c in _load_all_corps() if query in c["corp_name"]]

    def sort_key(c: dict) -> tuple:
        is_exact = c["corp_name"] != query
        is_unlisted = not bool(c["stock_code"])
        return (is_exact, is_unlisted, len(c["corp_name"]))

    matches.sort(key=sort_key)
    return matches[:limit]


def get_financial_statements(corp_code: str, year: int, reprt_code: str = "11011", fs_div: str = "CFS") -> dict:
    """전체 재무제표(단일회사 전체) 조회.

    reprt_code: 11011=사업보고서(연간), 11012=반기, 11013=1분기, 11014=3분기
    fs_div: CFS=연결재무제표, OFS=별도(개별)재무제표
    """
    _ensure_api_key()
    resp = _session.get(
        f"{BASE_URL}/fnlttSinglAcntAll.json",
        params={
            "crtfc_key": API_KEY,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": reprt_code,
            "fs_div": fs_div,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def get_common_shares_outstanding(corp_code: str, year: int, reprt_code: str = "11011") -> int | None:
    """보통주 유통주식수(발행주식총수 - 자기주식수)를 조회한다."""
    _ensure_api_key()
    resp = _session.get(
        f"{BASE_URL}/stockTotqySttus.json",
        params={
            "crtfc_key": API_KEY,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": reprt_code,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "000":
        return None
    for row in data.get("list", []):
        if row.get("se") == "보통주":
            distb = (row.get("distb_stock_co") or "").replace(",", "")
            return int(distb) if distb.isdigit() else None
    return None
