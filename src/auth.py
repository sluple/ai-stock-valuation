"""Supabase Auth 기반 로그인/회원가입.

db.py와 마찬가지로 client는 세션마다(st.session_state) 하나씩 주입받는다 —
로그인 세션이 여러 사용자 사이에 섞이지 않게 하기 위함.
"""


def sign_up(client, email: str, password: str):
    return client.auth.sign_up({"email": email, "password": password})


def sign_in(client, email: str, password: str):
    return client.auth.sign_in_with_password({"email": email, "password": password})


def sign_out(client) -> None:
    client.auth.sign_out()


def current_user(client):
    """현재 세션에 로그인된 사용자 정보(없으면 None)."""
    try:
        session = client.auth.get_session()
    except Exception:  # noqa: BLE001
        return None
    return session.user if session else None
