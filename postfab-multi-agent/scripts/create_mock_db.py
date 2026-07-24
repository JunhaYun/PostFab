"""
실제 데이터(post_data 엑셀)를 SQLite DB로 적재하는 스크립트.
"""
import sqlite3
import os
import pandas as pd

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "mock", "postfab.db")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "post_data")


def create_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    # 기존 DB 초기화
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)

    tables = {
        "tdlotinfo":       "tdlotinfo.xlsx",
        "tdtestresult":    "tdtestresult.xlsx",
        "tdrecipemapping": "tdrecipemapping.xlsx",
        "tdrecipemaster":  "tdrecipemaster.xlsx",
        "tdeqphistory":    "tdeqphistory.xlsx",
        "tdstripmap":      "tdstripmap.xlsx",
    }

    for table_name, filename in tables.items():
        path = os.path.join(DATA_DIR, filename)
        df = pd.read_excel(path)
        df.columns = [c.upper() for c in df.columns]

        # YIELD가 "96.2%" 같은 TEXT라 SQL 정렬/집계가 불가능하다.
        # 숫자 파생 컬럼(YIELD_NUM)을 추가해 "가장 수율 낮은 공정" 같은
        # 질의와 향후 Text-to-SQL이 바로 동작하도록 한다. 원본 YIELD는 보존.
        if table_name == "tdtestresult" and "YIELD" in df.columns:
            df["YIELD_NUM"] = (
                df["YIELD"].astype(str).str.replace("%", "", regex=False)
                .pipe(pd.to_numeric, errors="coerce")
            )

        df.to_sql(table_name, conn, if_exists="replace", index=False)
        print(f"[OK] {table_name}: {len(df)}행 적재")

    conn.commit()
    conn.close()
    print(f"\n[완료] DB 경로: {os.path.abspath(DB_PATH)}")


if __name__ == "__main__":
    create_db()
