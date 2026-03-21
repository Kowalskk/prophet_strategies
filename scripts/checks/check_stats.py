import sqlite3

def check_db():
    conn = sqlite3.connect("data/prophet.db")
    c = conn.cursor()
    
    print("--- Database Stats ---")
    c.execute("SELECT COUNT(*) FROM market_trades")
    print(f"Trades: {c.fetchone()[0]:,}")
    
    c.execute("SELECT COUNT(*) FROM markets")
    print(f"Markets: {c.fetchone()[0]:,}")
    
    c.execute("SELECT COUNT(*) FROM market_resolutions")
    print(f"Resolutions: {c.fetchone()[0]:,}")
    
    print("\n--- Heaviest Markets (Top 5) ---")
    c.execute("SELECT question, COUNT(*) as c FROM market_trades GROUP BY question ORDER BY c DESC LIMIT 5")
    for row in c.fetchall():
        print(f"  {row[1]:,} trades: {row[0]}")
    
    conn.close()

if __name__ == "__main__":
    check_db()
