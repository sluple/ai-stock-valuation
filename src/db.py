"""Supabase 연동: 분석 기록 저장/조회 + 워치리스트 CRUD.

중요한 설계 원칙: Supabase 클라이언트는 모듈 전역 싱글턴으로 두지 않는다.
로그인 세션(JWT)이 클라이언트 객체에 붙기 때문에, 여러 사용자가 동시 접속하는
배포 환경에서 전역 싱글턴을 쓰면 사용자 A의 로그인 세션이 사용자 B의 요청에
섞이는 사고가 난다. 대신 `create_client()`로 세션(브라우저 탭)마다 새 클라이언트를
만들어 Streamlit의 `st.session_state`에 담아두고, 모든 함수는 그 클라이언트를
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


# ---------------------------------------------------------------------------
# 분석 기록 (analyses)
# ---------------------------------------------------------------------------

def save_analysis(
    client, result, peer_codes: list[str], report_text: str | None, context: dict, user_id: str | None = None
) -> str | None:
    """분석 결과를 저장하고, 나중에 리포트를 붙여넣을 수 있도록 생성된 행의 id를 반환한다."""
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
        "user_id": user_id,
    }
    resp = client.table("analyses").insert(row).execute()
    if resp.data:
        return resp.data[0].get("id")
    return None


def update_report(client, record_id: str | None, report_text: str) -> None:
    if client is None or not record_id:
        return
    client.table("analyses").update({"report_text": report_text}).eq("id", record_id).execute()


def fetch_recent_analyses(client, limit: int = 20) -> list[dict]:
    if client is None:
        return []
    resp = client.table("analyses").select("*").order("created_at", desc=True).limit(limit).execute()
    return resp.data


def fetch_analyses_for_stock(client, stock_code: str, limit: int = 60) -> list[dict]:
    """특정 종목의 과거 분석 기록을 시간순으로 가져온다 (괴리 추이 차트용)."""
    if client is None:
        return []
    resp = (
        client.table("analyses")
        .select("created_at,current_price,dcf_value_a,dcf_value_b")
        .eq("stock_code", stock_code)
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
    )
    return resp.data


# ---------------------------------------------------------------------------
# 워치리스트 (watchlist)
# ---------------------------------------------------------------------------

def add_watchlist_item(client, user_id: str, stock_code: str, corp_name: str, alert_threshold: float = -0.15) -> None:
    if client is None:
        return
    client.table("watchlist").upsert(
        {
            "user_id": user_id,
            "stock_code": stock_code,
            "corp_name": corp_name,
            "alert_threshold": alert_threshold,
        },
        on_conflict="user_id,stock_code",
    ).execute()


def remove_watchlist_item(client, item_id: str) -> None:
    if client is None:
        return
    client.table("watchlist").delete().eq("id", item_id).execute()


def list_watchlist(client, user_id: str) -> list[dict]:
    if client is None:
        return []
    resp = (
        client.table("watchlist")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return resp.data
