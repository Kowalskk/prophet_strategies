import sqlite3
from datetime import datetime

def analyze_dates():
    conn = sqlite3.connect("data/prophet.db")
    c = conn.cursor()
    
    c.execute("SELECT MIN(block_time), MAX(block_time) FROM market_trades")
    min_time, max_time = c.fetchone()
    
    c.execute("SELECT COUNT(*) FROM market_trades")
    total_trades = c.fetchone()[0]
    
    print(f"--- Database Time Coverage ---")
    print(f"Min Date: {min_time}")
    print(f"Max Date: {max_time}")
    print(f"Total Valid Trades: {total_trades:,}")
    
    # Estimate months covered
    if min_time and max_time:
        try:
            d1 = datetime.fromisoformat(min_time.split('.')[0])
            d2 = datetime.fromisoformat(max_time.split('.')[0])
            months = (d2.year - d1.year) * 12 + d2.month - d1.month
            print(f"Months covered: ~{months} months")
            
            # Prediction: If 7 months (June to Jan) = 1.6M trades
            # Then Feb 2025 to March 2026 (~14 months) might be around 3M-4M more trades
            # Total volume could be 5M-6M instead of 10M-12M since we filtered a lot
        except Exception as e:
            print(f"Error parsing dates: {e}")
            
    conn.close()

if __name__ == "__main__":
    analyze_dates()
