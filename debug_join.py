import sqlite3
import pandas as pd

def debug_join():
    db_path = "data/prophet.db"
    conn = sqlite3.connect(db_path)
    
    print("--- market_trades condition_id (Sample) ---")
    df_t = pd.read_sql("SELECT DISTINCT condition_id FROM market_trades LIMIT 5", conn)
    print(df_t)
    
    print("\n--- market_resolutions condition_id (Sample) ---")
    df_r = pd.read_sql("SELECT DISTINCT condition_id FROM market_resolutions LIMIT 5", conn)
    print(df_r)
    
    print("\n--- Intersection Count ---")
    c = conn.cursor()
    c.execute("""
        SELECT COUNT(*) FROM (
            SELECT DISTINCT condition_id FROM market_trades
            INTERSECT
            SELECT DISTINCT condition_id FROM market_resolutions
        )
    """)
    print(f"Intersecting IDs: {c.fetchone()[0]}")
    
    conn.close()

if __name__ == "__main__":
    debug_join()
