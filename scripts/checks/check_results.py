import sqlite3

def check_results():
    db_path = "data/prophet.db"
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM backtest_results")
    count = c.fetchone()[0]
    print(f"Total rows in backtest_results: {count:,}")
    
    if count > 0:
        c.execute("SELECT filled_trades, count(*) FROM backtest_results GROUP BY (filled_trades > 0)")
        stats = c.fetchall()
        print("Success stats (filled_trades > 0):", stats)
        
    conn.close()

if __name__ == "__main__":
    check_results()
