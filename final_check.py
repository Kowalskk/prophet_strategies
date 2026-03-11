import sqlite3
import os

def final_count():
    db_path = "data/prophet.db"
    if not os.path.exists(db_path):
        print(f"DB not found at {db_path}")
        return
        
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    try:
        c.execute("SELECT COUNT(*) FROM market_trades")
        trades = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM markets")
        markets = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM market_resolutions")
        resolutions = c.fetchone()[0]
        
        c.execute("SELECT MAX(block_time) FROM market_trades")
        last_date = c.fetchone()[0]
        
        print(f"--- FINAL SYNC STATS ---")
        print(f"Total Trades: {trades:,}")
        print(f"Total Markets: {markets:,}")
        print(f"Total Resolutions: {resolutions:,}")
        print(f"Data Coverage Until: {last_date}")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    final_count()
