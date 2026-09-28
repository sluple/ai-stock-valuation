"""배출량(ETRS) + 재무데이터(DART)를 결합해 학습용 데이터셋을 만든다.

실행: python build_emissions_dataset.py
결과: ../data/emissions_training_data.csv
"""
import sys
import time

import pandas as pd

from emissions_data import build_corp_name_index, load_emissions_data, match_emissions_to_dart
from fetch_dart import get_financial_statements
from normalize import build_financial_table

sys.stdout.reconfigure(encoding="utf-8")

OUTPUT_PATH = "../data/emissions_training_data.csv"


def main() -> None:
    print("배출량 데이터 로드 중...")
    emissions = load_emissions_data()
    corp_index = build_corp_name_index()
    matched = match_emissions_to_dart(emissions, corp_index)

    # 사업장/업체 이중 신고 등으로 같은 회사가 여러 행일 수 있으므로 합산
    grouped = (
        matched.groupby(["corp_code", "corp_name", "stock_code", "계획업종"], as_index=False)
        .agg({"배출량_tCO2eq": "sum", "에너지사용량_TJ": "sum"})
    )
    print(f"매칭된 고유 기업 수: {len(grouped)}")

    rows = []
    failed = []
    for i, row in grouped.iterrows():
        corp_code = row["corp_code"]
        try:
            # 별도(OFS)재무제표를 우선 사용한다: 배출권거래제 배출량은 특정 법인(사업장) 단위인데,
            # 연결(CFS) 매출액을 쓰면 지주회사·복합기업의 경우 무관한 계열사 매출까지 섞여
            # 매출 대비 배출집약도가 크게 왜곡된다 (세아베스틸지주는 연결매출이 별도매출의 22배,
            # 동국홀딩스는 50배 — 실제로 확인된 값). 별도재무제표가 없는 경우에만 연결로 대체한다.
            data = get_financial_statements(corp_code, 2024, fs_div="OFS")
            if data.get("status") != "000":
                data = get_financial_statements(corp_code, 2024, fs_div="CFS")
            if data.get("status") != "000":
                failed.append((row["corp_name"], data.get("status"), data.get("message")))
                continue
            fin = build_financial_table(data.get("list", []))
            if "매출액" not in fin or fin.get("매출액", 0) <= 0:
                failed.append((row["corp_name"], "no_revenue", ""))
                continue
            rows.append(
                {
                    "corp_code": corp_code,
                    "corp_name": row["corp_name"],
                    "stock_code": row["stock_code"],
                    "계획업종": row["계획업종"],
                    "배출량_tCO2eq": row["배출량_tCO2eq"],
                    "에너지사용량_TJ": row["에너지사용량_TJ"],
                    "매출액": fin.get("매출액"),
                    "매출원가": fin.get("매출원가"),
                    "영업이익": fin.get("영업이익"),
                    "자산총계": fin.get("자산총계"),
                }
            )
        except Exception as e:  # noqa: BLE001
            failed.append((row["corp_name"], "exception", str(e)))

        if (i + 1) % 50 == 0:
            print(f"  진행: {i + 1}/{len(grouped)} (성공 {len(rows)}, 실패 {len(failed)})")
        time.sleep(0.05)  # DART API 과호출 방지

    df = pd.DataFrame(rows)
    df.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    print(f"\n완료: {len(df)}개 기업 데이터 저장 -> {OUTPUT_PATH}")
    print(f"재무데이터 조회 실패: {len(failed)}개")
    if failed:
        print("실패 사례 일부:", failed[:10])


if __name__ == "__main__":
    main()
