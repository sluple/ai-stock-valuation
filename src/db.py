"""Supabase 연동: 배출량 분석 기록 저장/조회.

중요한 설계 원칙: Supabase 클라이언트는 모듈 전역 싱글턴으로 두지 않는다.
여러 사용자가 동시 접속하는 배포 환경을 고려해 `create_client()`로 세션(브라우저 탭)마다
새 클라이언트를 만들어 Streamlit의 `st.session_state`에 담아두고, 모든 함수는 그 클라이언트를
인자로 받는다 (의존성 주입).
"""
import os

from dotenv import load_dotenv

load_dotenv()


def is_enabled() -> bool:
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_KEY"))


def create_client():
    """세션(브라우저 탭)마다 하나씩 만들어 st.session_state에 보관할 클라이언트."""
    if not is_enabled():
        return None
    from supabase import create_client as _create_client  # 지연 임포트

    return _create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))


def save_estimate(client, result, report_text: str | None, context: dict) -> str | None:
    """배출량 추정 결과를 저장하고, 나중에 리포트를 붙여넣을 수 있도록 생성된 행의 id를 반환한다."""
    if client is None:
        return None
    row = {
        "corp_name": result.corp["corp_name"],
        "stock_code": result.corp["stock_code"],
        "industry": result.industry,
        "industry_source": result.industry_source,
        "revenue": result.financials.get("매출액"),
        "actual_scope12": result.actual_scope12,
        "model_estimate_scope12": result.model_estimate,
        "benchmark_estimate_scope12": result.benchmark_estimate,
        "scope3_estimate": result.scope3_estimate,
        "report_text": report_text,
        "raw_context": context,
    }
    resp = client.table("emissions_estimates").insert(row).execute()
    if resp.data:
        return resp.data[0].get("id")
    return None


def update_report(client, record_id: str | None, report_text: str) -> None:
    if client is None or not record_id:
        return
    client.table("emissions_estimates").update({"report_text": report_text}).eq("id", record_id).execute()


def fetch_recent_estimates(client, limit: int = 20) -> list[dict]:
    if client is None:
        return []
    resp = (
        client.table("emissions_estimates")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return resp.data
