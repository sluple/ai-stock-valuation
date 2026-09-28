"""Supabase에 분석 결과를 저장/조회한다.

SUPABASE_URL / SUPABASE_KEY가 없으면 조용히 비활성화되어 앱 전체가 죽지 않는다
(대시보드는 DB 없이도 동작해야 하는 부가 기능이므로).
"""
import os

from dotenv import load_dotenv

load_dotenv()

_client = None
_disabled_reason: str | None = None


def _get_client():
    global _client, _disabled_reason
    if _client is not None:
        return _client
    if _disabled_reason is not None:
        return None

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        _disabled_reason = "SUPABASE_URL / SUPABASE_KEY가 설정되지 않음"
        return None

    from supabase import create_client  # 지연 임포트: 미설치 환경에서도 나머지 기능은 동작하게

    _client = create_client(url, key)
    return _client


def is_enabled() -> bool:
    return _get_client() is not None


def save_analysis(result, peer_codes: list[str], report_text: str | None, context: dict) -> str | None:
    """분석 결과를 저장하고, 나중에 리포트를 붙여넣을 수 있도록 생성된 행의 id를 반환한다."""
    client = _get_client()
    if client is None:
        return None
    row = {
        "corp_name": result.corp["corp_name"],
        "stock_code": result.corp["stock_code"],
        "current_price": result.current_price,
        "wacc": result.wacc,
        "dcf_value_a": result.result_cagr["value_per_share"],
        "dcf_value_b": result.result_margin["value_per_share"],
        "per_implied_value": result.implied["value_per_share_per"] if result.implied else None,
        "pbr_implied_value": result.implied["value_per_share_pbr"] if result.implied else None,
        "ev_ebit_implied_value": result.implied["value_per_share_ev_ebit"] if result.implied else None,
        "peer_codes": ",".join(peer_codes),
        "report_text": report_text,
        "raw_context": context,
    }
    resp = client.table("analyses").insert(row).execute()
    if resp.data:
        return resp.data[0].get("id")
    return None


def update_report(record_id: str | None, report_text: str) -> None:
    client = _get_client()
    if client is None or not record_id:
        return
    client.table("analyses").update({"report_text": report_text}).eq("id", record_id).execute()


def fetch_recent_analyses(limit: int = 20) -> list[dict]:
    client = _get_client()
    if client is None:
        return []
    resp = client.table("analyses").select("*").order("created_at", desc=True).limit(limit).execute()
    return resp.data
