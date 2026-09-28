"""실시간 시세 조회 (Yahoo Finance 비공식 차트 API, 무료/키 불필요)."""
import requests


def get_krx_price(stock_code: str, market: str = "KS") -> float | None:
    """KRX 종목의 현재가(또는 최근 종가)를 조회한다.

    stock_code: 예) '005930' (삼성전자)
    market: 'KS'=코스피, 'KQ'=코스닥
    """
    ticker = f"{stock_code}.{market}"
    try:
        resp = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("chart", {}).get("result")
        if not result:
            return None
        return result[0]["meta"].get("regularMarketPrice")
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return None
