import sqlite3
import pandas as pd

def check_resolutions():
    db_path = "data/prophet.db"
    conn = sqlite3.connect(db_path)
    
    print("--- market_resolutions outcomes ---")
    df = pd.read_sql("SELECT resolved_outcome, COUNT(*) as count FROM market_resolutions GROUP BY resolved_outcome", conn)
    print(df)
    
    conn.close()

if __name__ == "__main__":
    check_resolutions()
