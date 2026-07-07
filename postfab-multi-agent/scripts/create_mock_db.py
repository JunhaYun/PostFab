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
        df.to_sql(table_name, conn, if_exists="replace", index=False)
        print(f"[OK] {table_name}: {len(df)}행 적재")

    conn.commit()
    conn.close()
    print(f"\n[완료] DB 경로: {os.path.abspath(DB_PATH)}")


if __name__ == "__main__":
    create_db()
