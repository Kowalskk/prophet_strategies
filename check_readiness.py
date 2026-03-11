import sqlite3
import pandas as pd

def check_readiness():
    db_path = "data/prophet.db"
    conn = sqlite3.connect(db_path)
    
    print("--- Markets Table Sample ---")
    df = pd.read_sql("SELECT * FROM markets LIMIT 5", conn)
    print(df.to_string())
    
    print("\n--- Filter Counts ---")
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM markets")
    print(f"Total: {c.fetchone()[0]}")
    
    c.execute("SELECT COUNT(*) FROM markets WHERE crypto IS NOT NULL")
    print(f"With Crypto: {c.fetchone()[0]}")
    
    c.execute("SELECT COUNT(*) FROM markets WHERE threshold IS NOT NULL")
    print(f"With Threshold: {c.fetchone()[0]}")
    
    c.execute("SELECT COUNT(*) FROM markets WHERE resolution_date IS NOT NULL")
    print(f"With Resolution Date: {c.fetchone()[0]}")
    
    c.execute("SELECT COUNT(*) FROM markets WHERE resolved_outcome IN ('YES', 'NO')")
    print(f"Resolved (YES/NO): {c.fetchone()[0]}")
    
    conn.close()

if __name__ == "__main__":
    check_readiness()
