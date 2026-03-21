import sqlite3
import pandas as pd

def check_payouts():
    db_path = "data/prophet.db"
    conn = sqlite3.connect(db_path)
    
    print("--- Distinct Payout Numerators ---")
    df = pd.read_sql("SELECT payout_numerators, COUNT(*) as count FROM market_resolutions GROUP BY payout_numerators LIMIT 50", conn)
    print(df.to_string())
    
    conn.close()

if __name__ == "__main__":
    check_payouts()
